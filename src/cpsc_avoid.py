#!/usr/bin/env python3
"""cpsc_avoid.py — CPSC fire/burn avoid-list pipeline (researcher #5 spec).

Research artifact only. Produces a JSONL exclusion list of recalls that
trigger a 40-day avoid window. No live trading.

Theme 1, idea #3 (UNSTRUCTURED_ALPHA_IDEAS.md): CPSC recalls
(fire/burn/child-injury -> short, labeling-only -> long). This module
implements the fire/burn half as a deterministic, LLM-free exclusion rule.

RULE (all three must hold):
  1. Hazard string-match (NO LLM): any entry in ``Hazards[].Name`` matches
     ``HAZARD_RE`` (fire | burn | electrocut* | electric shock |
     shock hazard), case-insensitive.
  2. Listed firm: ``Manufacturers[]`` OR ``Importers[]`` contains a
     non-empty ``Name``. Title text alone does NOT satisfy this gate.
  3. Units >= 10,000 (``MIN_UNITS``): max numeric value parsed from
     ``Products[].NumberOfUnits`` (e.g. "About 12,957",
     "About 21,040 (In addition, about 4,140 were sold in Canada)").
     Unparseable units (e.g. "Millions") fail closed -> dropped.

Qualifying recalls are excluded for 40 calendar days (``HOLD_DAYS``).

PIT DISCIPLINE — READ BEFORE TOUCHING:
  1. ANCHOR ON ``RecallDate`` + 1 day entry. ``RecallDate`` is the CPSC
     publication timestamp, i.e. the first moment a market participant
     polling the RestWebServices endpoint could observe the recall.
     ``LastPublishDate`` is a revision stamp, NEVER an anchor: using it
     backdates knowledge of the original publication = lookahead bias.
  2. FULL-DUMP API. The endpoint ignores pagination parameters
     (``page_size``/``offset``/``skip``): every request returns the full
     dump (~27 MB / ~9,966 records as of 2026-08). ``pull_cpsc()`` therefore
     issues ONE keyless GET and snapshots the raw list immutably. Do NOT
     "paginate" this endpoint — offset loops just re-download the dump.
  3. Snapshots are IMMUTABLE (same convention as ``fda_avoid.py``).
     ``write_snapshot()`` refuses to overwrite; the exclusion list is a
     deterministic derived artifact rebuildable from snapshots.
  4. Entity resolution goes through the shared alias-table interface
     (``src/alias_resolve.py``). Substring matching is FORBIDDEN (see
     ``fda_avoid.substring_match_would_false_positive`` / the
     Endo-vs-Ethicon trap). Unresolved names stay unresolved (None),
     never guessed.
  5. Sector cap (``max_single_industry_pct``, default 5%) takes a PIT
     industry map INJECTED by the caller, never hardcoded spot values.
     Rows without a mapped industry are exempt from the cap (never
     dropped for it) so missing mappings fail open visibly, not silently.

JOIN COVERAGE (measured 2026-09 on the live full dump, n=9,966):
  * ``Manufacturers[]`` non-empty:                    ~54% of records
  * ``Manufacturers[]`` OR ``Importers[]`` non-empty:  ~88% of records
    (both empty: ~12%; these fail the listed-firm gate and are dropped)
  * Ticker-resolvable via a universe-names-only index: ~1.7% of records.
    The researcher spec's "~7% listed" figure reflects the FULL shared
    alias table (SEC company_tickers.json + 10-K Exhibit 21 subsidiary
    trees + golden set), which resolves subsidiaries and private-label
    importers the universe-only index misses. Use
    ``measure_join_coverage()`` to re-measure with any alias index.
    The gap between "88% have a named firm" and "single-digit % map to a
    ticker" is EXPECTED: most recalled products are private-label /
    foreign-manufacturer goods with no US-listed parent.

KNOWN STRING-MATCH LIMITATIONS (no-LLM rule, by design):
  * False positives: "chemical burn" ingestion hazards (e.g. button-cell
    "internal chemical burns", ~93 records) match "burn" although they are
    not fire/burn events. Kept deliberately: the spec mandates plain
    string matching, and splitting burn subtypes needs an LLM/classifier.
  * False negatives: atypical phrasings outside the regex (e.g. "shock
    absorber" mechanical text correctly excluded; rare electrical
    phrasings without fire/burn/electrocut/shock-hazard tokens missed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Shared alias-table interface (join hook) ─────────────────────────────
try:
    from alias_resolve import resolve as _alias_resolve_fn  # type: ignore

    _HAS_ALIAS_TABLE = True
except Exception:  # pragma: no cover — offline / missing table path
    _alias_resolve_fn = None
    _HAS_ALIAS_TABLE = False

# ── Endpoint & policy ────────────────────────────────────────────────────
CPSC_ENDPOINT = "https://www.saferproducts.gov/RestWebServices/Recall"

HOLD_DAYS = 40  # idea #3: fire/burn avoid window (calendar days)
MIN_UNITS = 10_000  # materiality floor on Products[].NumberOfUnits
DEFAULT_MAX_SINGLE_INDUSTRY_PCT = 0.05  # auto-sector cap (param)

# String-match hazard rule. No LLM per spec. Covers:
#   fire / burn (incl. "Fire & Fire-Related Burn", "Burn - Not Fire-Related",
#   "chemical burns" — documented false positive, kept by design),
#   electrocut* (electrocution/electrocuted/electrocute),
#   "electric shock" and "shock hazard" phrasings that never contain the
#   word "electrocution" (~172 live records, e.g. "posing a shock hazard").
# Bare "shock" alone is NOT matched: it fires on mechanical text such as
# "shock absorber rod assembly" (false positive). "shock hazard" as a
# phrase is electrical in CPSC usage.
HAZARD_RE = re.compile(
    r"fire|burn|electrocut|electric\s*shock|shock\s*hazard", re.IGNORECASE
)

EARLIEST_SANE_DATE = dt.date(1973, 1, 1)  # min RecallDate seen in the dump
FUTURE_TOLERANCE_DAYS = 7  # allow small reporting-clock skew, nothing more

UTC = dt.timezone.utc


# ═══════════════════════════════════════════════════════════════════════════
# Hazard matcher (string match, no LLM)
# ═══════════════════════════════════════════════════════════════════════════
def hazard_matches(hazards: object) -> tuple[bool, str]:
    """Return (matched, matched_text) for a raw ``Hazards`` list.

    ``hazards`` is the raw ``Hazards[]`` value (list of ``{"Name": ...}``).
    Non-list / missing input -> (False, ""). Match is a case-insensitive
    ``HAZARD_RE`` search over each entry's ``Name``. Returns the first
    matching hazard text for audit.
    """
    if not isinstance(hazards, list):
        return False, ""
    for entry in hazards:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or "")
        if name and HAZARD_RE.search(name):
            return True, name
    return False, ""


# ═══════════════════════════════════════════════════════════════════════════
# Units parsing
# ═══════════════════════════════════════════════════════════════════════════
def parse_units(record: dict) -> int | None:
    """Max numeric unit count across ``Products[].NumberOfUnits``.

    Handles "About 12,957", "1,694", "About 21,040 (In addition, about
    4,140 were sold in Canada)" (max wins, which is the US figure in
    practice — the Canada parenthetical is smaller). Returns None when no
    numeric token is found (e.g. "Millions", "") so the caller fails
    closed. Never raises on malformed input.
    """
    try:
        products = record.get("Products") or []
    except AttributeError:
        return None
    if not isinstance(products, list):
        return None
    best: int | None = None
    for product in products:
        if not isinstance(product, dict):
            continue
        text = str(product.get("NumberOfUnits") or "")
        for match in re.finditer(r"[\d,]+", text):
            try:
                value = int(match.group(0).replace(",", ""))
            except ValueError:
                continue
            if best is None or value > best:
                best = value
    return best


# ═══════════════════════════════════════════════════════════════════════════
# Firm listing gate + fallback resolution chain
# ═══════════════════════════════════════════════════════════════════════════
def _nonempty_names(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = str(entry.get("Name") or "").strip()
            if name:
                names.append(name)
    return names


def has_listed_firm(record: dict) -> bool:
    """Rule gate: ``Manufacturers[]`` OR ``Importers[]`` has a non-empty Name.

    Title text alone does NOT satisfy this gate — the fallback Title-parse
    exists only for entity *resolution*, not for qualification.
    """
    return bool(
        _nonempty_names(record.get("Manufacturers"))
        or _nonempty_names(record.get("Importers"))
    )


def clean_firm_name(name: str) -> str:
    """Strip CPSC ", of <city>" location suffixes: "CCM Hockey U.S., Inc.,
    of Maple Grove, Illinois" -> "CCM Hockey U.S., Inc.". Splits on comma
    + "of" so firms like "Bank of America" (no comma) are untouched."""
    cleaned = re.split(r",\s*of\s+", str(name or "").strip(), maxsplit=1)[0]
    return cleaned.strip(" ,;")


def parse_title_firm(title: str) -> str:
    """Fallback firm parse from the recall Title.

    CPSC titles lead with the firm: "<Firm> Recalls ...", "<Firm> Recalled
    ...", "CPSC, <Firm> Announce ...", "<Firm> Expands Recall ...".
    Strategy: strip a leading "CPSC[,/and ...]" prefix, then cut at the
    first recall verb (Recalls/Recalled/Announces/Expands/Warns). Returns ""
    when nothing usable remains.
    """
    text = str(title or "").strip()
    # Strip leading agency prefix: "CPSC, ", "CPSC and ", "CPSC Warns ..." is
    # itself a title pattern — only strip when followed by a firm + verb.
    text = re.sub(
        r"^cpsc\s*(,|\band\b)?\s*", "", text, flags=re.IGNORECASE
    ).strip()
    parts = re.split(
        r"\sRecalls?\b|\sRecalled\b|\sAnnounces?\b|\sExpands?\b|\sWarns\b",
        text,
        maxsplit=1,
    )
    candidate = parts[0].strip() if parts else ""
    # Guard against verb-first titles with no firm ("Recalled Due to ...").
    if not candidate or len(candidate) < 2:
        return ""
    return candidate


def resolve_firm_name(record: dict) -> tuple[str, str]:
    """Fallback chain -> (firm_name, method).

    Order: Manufacturers[0] ("manufacturer") -> Importers[0] ("importer")
    -> Title-parse ("title"). Manufacturer/Importer names are location-
    cleaned; Title-parse is raw. Returns ("", "none") when all fail.
    """
    mfrs = _nonempty_names(record.get("Manufacturers"))
    if mfrs:
        return clean_firm_name(mfrs[0]), "manufacturer"
    importers = _nonempty_names(record.get("Importers"))
    if importers:
        return clean_firm_name(importers[0]), "importer"
    title_firm = parse_title_firm(record.get("Title") or "")
    if title_firm:
        return title_firm, "title"
    return "", "none"


def resolve_ticker(
    firm_name: str, alias_index: dict | None = None
) -> tuple[str | None, str | None, str]:
    """Join hook: firm name -> (ticker, CIK, confidence).

    Delegates to the shared alias table (``src/alias_resolve.resolve``)
    when an index is supplied. Otherwise returns (None, None, reason) —
    a deliberate unresolved stub. Never substring-guesses.
    """
    if _HAS_ALIAS_TABLE and alias_index is not None and firm_name:
        try:
            ticker, cik, conf = _alias_resolve_fn(firm_name, alias_index)  # type: ignore
            if ticker:
                return ticker, cik, str(conf)
            return None, None, "none"
        except Exception as exc:  # fail closed, never guess
            return None, None, f"alias_error:{type(exc).__name__}"
    if not firm_name:
        return None, None, "empty_firm_name"
    if _HAS_ALIAS_TABLE:
        return None, None, "unresolved:alias-index-required"
    return None, None, "unresolved:alias-table-missing"


def measure_join_coverage(
    records: list[dict], alias_index: dict | None = None
) -> dict:
    """Document join coverage over raw dump records.

    Returns counts for: manufacturers present, either firm present,
    fallback method histogram, and (when ``alias_index`` is given)
    ticker-resolved + rate. With ``alias_index=None`` the resolved count
    is 0 — the named-firm counts are still reported.
    """
    total = len(records)
    with_mfr = sum(1 for r in records if _nonempty_names(r.get("Manufacturers")))
    with_either = sum(1 for r in records if has_listed_firm(r))
    methods: dict[str, int] = {"manufacturer": 0, "importer": 0, "title": 0, "none": 0}
    resolved = 0
    for rec in records:
        name, method = resolve_firm_name(rec if isinstance(rec, dict) else {})
        methods[method] = methods.get(method, 0) + 1
        if alias_index is not None and name:
            ticker, _, _ = resolve_ticker(name, alias_index)
            resolved += int(bool(ticker))
    return {
        "total": total,
        "with_manufacturer": with_mfr,
        "with_manufacturer_or_importer": with_either,
        "both_empty": total - with_either,
        "method_histogram": methods,
        "ticker_resolved": resolved,
        "ticker_resolve_pct": round(100 * resolved / total, 2) if total else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Dates (PIT: RecallDate + 1 entry, 40d window, calendar days)
# ═══════════════════════════════════════════════════════════════════════════
def parse_recall_date(value: object) -> dt.date | None:
    """Parse a CPSC ``RecallDate`` ("2026-08-20T00:00:00" / "2026-08-20").

    None if missing/unparseable. Time component is discarded (date-level
    PIT granularity).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    day_text = text[:10]
    try:
        return dt.date(
            int(day_text[0:4]), int(day_text[5:7]), int(day_text[8:10])
        )
    except (ValueError, IndexError):
        return None


def exclusion_window(recall: dt.date) -> tuple[dt.date, dt.date]:
    """(entry_date, window_end) inclusive: entry = RecallDate + 1 (PIT),
    window_end = entry + HOLD_DAYS - 1. Calendar-day convention; a
    trading-calendar roll must be applied downstream with PIT prices."""
    start = recall + dt.timedelta(days=1)
    return start, start + dt.timedelta(days=HOLD_DAYS - 1)


def is_excluded(as_of: dt.date, start: dt.date, end: dt.date) -> bool:
    """Inclusive membership test for an exclusion window."""
    return start <= as_of <= end


# ═══════════════════════════════════════════════════════════════════════════
# Filter (hazard + listed firm + units + sane dates)
# ═══════════════════════════════════════════════════════════════════════════
def classify_record(
    record: dict, today: dt.date | None = None
) -> tuple[str, str]:
    """Return ('keep'|'drop', reason) for one raw recall record."""
    today = today or dt.datetime.now(UTC).date()
    if not isinstance(record, dict):
        return "drop", "bad_record_shape"
    recall = parse_recall_date(record.get("RecallDate"))
    if recall is None:
        return "drop", "missing_or_bad_recall_date"
    if recall < EARLIEST_SANE_DATE:
        return "drop", "insane_recall_date"
    if recall > today + dt.timedelta(days=FUTURE_TOLERANCE_DAYS):
        return "drop", "recall_date_in_future"
    matched, _ = hazard_matches(record.get("Hazards"))
    if not matched:
        return "drop", "hazard_mismatch"
    if not has_listed_firm(record):
        return "drop", "no_listed_firm"
    units = parse_units(record)
    if units is None:
        return "drop", "units_unknown"
    if units < MIN_UNITS:
        return "drop", "units_below_threshold"
    return "keep", "ok"


def filter_records(
    records: list[dict], today: dt.date | None = None
) -> tuple[list[dict], list[dict]]:
    """Split raw records into (kept, rejects). Rejects carry
    ``_reject_reason`` for audit; kept rows are untouched raw dicts."""
    kept, rejects = [], []
    for rec in records:
        verdict, reason = classify_record(rec, today)
        if verdict == "keep":
            kept.append(rec)
        else:
            base = rec if isinstance(rec, dict) else {"_raw": rec}
            rejects.append({**base, "_reject_reason": reason})
    return kept, rejects


# ═══════════════════════════════════════════════════════════════════════════
# Exclusion-list build
# ═══════════════════════════════════════════════════════════════════════════
def _recall_key(record: dict) -> str:
    rid = record.get("RecallID", "")
    if rid not in (None, ""):
        return f"cpsc:{rid}"
    num = str(record.get("RecallNumber") or "").strip()
    if num:
        return f"cpsc:num:{num}"
    return (
        f"cpsc:nokey:{hash((str(record.get('Title')), str(record.get('RecallDate')))) & 0xFFFFFFFF:08x}"
    )


def build_exclusion_rows(
    kept: list[dict],
    pulled_at: str | None = None,
    alias_index: dict | None = None,
    industry_map: dict | None = None,
) -> list[dict]:
    """Map filtered raw records -> exclusion rows.

    Ticker/CIK resolve via the shared alias hook when ``alias_index`` is
    given, else None with an "unresolved:..." confidence. ``industry_map``
    (ticker -> GICS industry) is attached as ``industry`` when available;
    missing mappings leave ``industry`` None (exempt from the sector cap).
    """
    rows = []
    for rec in kept:
        recall = parse_recall_date(rec.get("RecallDate"))
        assert recall is not None  # guaranteed by filter_records
        start, end = exclusion_window(recall)
        matched, hazard_text = hazard_matches(rec.get("Hazards"))
        units = parse_units(rec)
        firm_name, method = resolve_firm_name(rec)
        mfrs = _nonempty_names(rec.get("Manufacturers"))
        importers = _nonempty_names(rec.get("Importers"))
        ticker, cik, conf = resolve_ticker(firm_name, alias_index)
        industry = None
        if industry_map and ticker:
            industry = industry_map.get(ticker)
        hazard_names = [
            str(h.get("Name") or "")[:300]
            for h in (rec.get("Hazards") or [])
            if isinstance(h, dict)
        ]
        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "match_confidence": conf,
                "industry": industry,
                "recall_id": rec.get("RecallID", ""),
                "recall_number": str(rec.get("RecallNumber") or ""),
                "recall_date": recall.isoformat(),
                "entry_date": start.isoformat(),
                "window_end": end.isoformat(),
                "hold_days": HOLD_DAYS,
                "hazard_match": hazard_text[:300],
                "hazard_names": hazard_names,
                "units": units,
                "manufacturer_raw": mfrs[0] if mfrs else "",
                "importer_raw": importers[0] if importers else "",
                "resolved_name": firm_name,
                "resolution_method": method,
                "title": str(rec.get("Title") or "")[:500],
                "reason": (
                    f"CPSC fire/burn/electrocution recall "
                    f"{rec.get('RecallID', '')}: {hazard_text[:200]}"
                ).strip(),
                "pulled_at": pulled_at,
            }
        )
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Auto-sector cap (max % of the avoid-list in a single GICS industry)
# ═══════════════════════════════════════════════════════════════════════════
def apply_sector_cap(
    rows: list[dict],
    max_single_industry_pct: float = DEFAULT_MAX_SINGLE_INDUSTRY_PCT,
    industry_map: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Enforce: no single industry holds more than ``max_single_industry_pct``
    of the avoid-list. Returns (capped, dropped); dropped rows carry
    ``_drop_reason="sector_cap"``.

    Mechanics: ``max_allowed = max(1, floor(max_pct * N_pre))`` per known
    industry, keeping the largest-``units`` recalls (ties: earliest
    ``recall_date``, then smallest ``recall_id``). Rows with unknown
    industry (no mapping) are EXEMPT — never dropped here — so missing
    mappings fail open visibly instead of silently nuking the list.
    ``industry_map`` (ticker -> industry) supplements rows whose
    ``industry`` field is unset.
    """
    if not 0 < max_single_industry_pct <= 1:
        raise ValueError("max_single_industry_pct must be in (0, 1]")
    n_pre = len(rows)
    if n_pre == 0:
        return [], []
    max_allowed = max(1, int(max_single_industry_pct * n_pre // 1))

    by_industry: dict[str, list[dict]] = {}
    exempt: list[dict] = []
    for row in rows:
        industry = row.get("industry") or (
            industry_map or {}
        ).get(row.get("ticker") or "")
        if not industry:
            exempt.append(row)
            continue
        by_industry.setdefault(str(industry), []).append(row)

    capped: list[dict] = list(exempt)
    dropped: list[dict] = []
    for industry in sorted(by_industry):
        members = sorted(
            by_industry[industry],
            key=lambda r: (
                -(r.get("units") if isinstance(r.get("units"), int) else -1),
                str(r.get("recall_date", "")),
                str(r.get("recall_id", "")),
            ),
        )
        capped.extend(members[:max_allowed])
        for row in members[max_allowed:]:
            dropped.append({**row, "_drop_reason": "sector_cap"})
    # Deterministic output order: entry_date, then recall_id.
    capped.sort(key=lambda r: (str(r.get("entry_date", "")), str(r.get("recall_id", ""))))
    dropped.sort(key=lambda r: (str(r.get("entry_date", "")), str(r.get("recall_id", ""))))
    return capped, dropped


# ═══════════════════════════════════════════════════════════════════════════
# Puller — keyless full-dump, immutable snapshots
# ═══════════════════════════════════════════════════════════════════════════
def utc_compact_now() -> str:
    return dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def snapshot_filename(pull_ts: str) -> str:
    return f"cpsc_recall_pull_{pull_ts}.json"


def write_snapshot(
    snapshot_dir: Path, payload: dict, pull_ts: str | None = None
) -> Path:
    """Persist a raw pull payload. NEVER overwrites: on filename collision
    a numeric suffix is appended, the existing file is left byte-identical."""
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pull_ts = pull_ts or utc_compact_now()
    path = snapshot_dir / snapshot_filename(pull_ts)
    if path.exists():
        stem = path.stem
        n = 1
        while (snapshot_dir / f"{stem}_{n:02d}.json").exists():
            n += 1
        path = snapshot_dir / f"{stem}_{n:02d}.json"
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    return path


def load_snapshot(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_dump_response(path: Path) -> list[dict]:
    """Offline path: read a cached CPSC dump.

    Accepts the live API shape (a raw JSON list of recall dicts) as well
    as ``{"results": [...]}`` / ``{"recalls": [...]}`` wrappers used by
    small committed fixtures.
    """
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "recalls", "Recalls"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"unrecognized CPSC payload shape in {path}")


def _default_http_get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "alphaHunt-research/1.0 (CPSC fire-burn avoid-list; contact research@example.com)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def pull_cpsc(
    snapshot_dir: Path,
    http_get=None,
    pull_ts: str | None = None,
    timeout: int = 120,
) -> tuple[Path, list[dict]]:
    """Keyless full-dump pull of the CPSC recall endpoint.

    ONE GET to ``CPSC_ENDPOINT?format=json`` — the API ignores pagination
    params, so no page_size/offset loop. ``http_get`` is injectable
    (tests/fixtures/offline); defaults to keyless urllib. The FULL raw
    record list plus pull metadata is snapshotted immutably; returns
    (snapshot_path, records).

    NOTE: scratch network pulls belong in /tmp (pass a /tmp snapshot_dir).
    The ~27 MB snapshot is a cache artifact, not a committed fixture.
    """
    http_get = http_get or (lambda url: _default_http_get(url, timeout))
    pulled_at = dt.datetime.now(UTC).isoformat()
    pull_ts = pull_ts or utc_compact_now()
    url = CPSC_ENDPOINT + "?" + urllib.parse.urlencode({"format": "json"})
    raw = http_get(url)
    if isinstance(raw, (bytes, bytearray)):
        records = json.loads(bytes(raw).decode("utf-8", errors="replace"))
    elif isinstance(raw, str):
        records = json.loads(raw)
    else:
        records = raw
    if isinstance(records, dict):
        # Tolerate wrapped shapes; the live API returns a bare list.
        for key in ("results", "recalls", "Recalls"):
            if isinstance(records.get(key), list):
                records = records[key]
                break
    if not isinstance(records, list):
        raise RuntimeError(f"unexpected CPSC response shape for {url}")
    snapshot = {
        "pulled_at": pulled_at,
        "pull_ts": pull_ts,
        "endpoint": CPSC_ENDPOINT,
        "note": "full-dump endpoint ignores pagination params; single GET",
        "count": len(records),
        "results": records,  # raw, unmodified
    }
    path = write_snapshot(snapshot_dir, snapshot, pull_ts)
    return path, records


# ═══════════════════════════════════════════════════════════════════════════
# Exclusion-list build + refresh entrypoint
# ═══════════════════════════════════════════════════════════════════════════
def write_exclusion_jsonl(rows: list[dict], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in sorted(
            rows,
            key=lambda r: (str(r.get("entry_date", "")), str(r.get("recall_id", ""))),
        ):
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return out_path


def rebuild_from_snapshots(
    snapshot_dir: Path,
    out_path: Path,
    today: dt.date | None = None,
    alias_index: dict | None = None,
    industry_map: dict | None = None,
    max_single_industry_pct: float = DEFAULT_MAX_SINGLE_INDUSTRY_PCT,
) -> dict:
    """Deterministic rebuild of the exclusion list from ALL immutable
    snapshots in ``snapshot_dir``. Dedups by RecallID, keeping the
    earliest pulled copy. Applies the sector cap. Rewriting this derived
    artifact is safe BECAUSE snapshots are never mutated."""
    snapshot_dir = Path(snapshot_dir)
    seen: dict[str, tuple[dict, str]] = {}
    snapshots_read, raw_total, reject_total = 0, 0, 0
    for path in sorted(snapshot_dir.glob("cpsc_recall_pull_*.json")):
        try:
            payload = load_snapshot(path)
        except (OSError, ValueError):
            continue
        results = payload.get("results") or []
        if not isinstance(results, list):
            continue
        snapshots_read += 1
        raw_total += len(results)
        kept, rejects = filter_records(results, today)
        reject_total += len(rejects)
        for rec in kept:
            key = _recall_key(rec)
            if key not in seen or str(payload.get("pulled_at", "")) < str(
                seen[key][1]
            ):
                seen[key] = (rec, payload.get("pulled_at", ""))
    rows = build_exclusion_rows(
        [rec for rec, _ in seen.values()],
        pulled_at=None,
        alias_index=alias_index,
        industry_map=industry_map,
    )
    pre_cap = len(rows)
    rows, sector_dropped = apply_sector_cap(
        rows, max_single_industry_pct, industry_map
    )
    write_exclusion_jsonl(rows, out_path)
    return {
        "snapshots_read": snapshots_read,
        "raw_records": raw_total,
        "rejects": reject_total,
        "pre_cap_exclusions": pre_cap,
        "sector_dropped": len(sector_dropped),
        "exclusions": len(rows),
        "out_path": str(out_path),
    }


def refresh_cpsc_avoid(
    snapshot_dir: Path,
    out_path: Path,
    http_get=None,
    offline: bool = False,
    today: dt.date | None = None,
    alias_index: dict | None = None,
    industry_map: dict | None = None,
    max_single_industry_pct: float = DEFAULT_MAX_SINGLE_INDUSTRY_PCT,
) -> dict:
    """Refresh entrypoint: full-dump pull (unless offline), then rebuild
    the exclusion list from the full snapshot history.

    Scratch network pulls belong in /tmp (pass a /tmp snapshot_dir); the
    committed-code path uses cached fixtures/snapshots with offline=True.
    Returns a summary dict; raises on network failure (never half-writes:
    rebuild runs only after a successful pull).
    """
    snapshot_dir, out_path = Path(snapshot_dir), Path(out_path)
    pulled: dict[str, str] = {}
    if not offline:
        path, _ = pull_cpsc(snapshot_dir, http_get=http_get)
        pulled["cpsc"] = str(path)
    summary = rebuild_from_snapshots(
        snapshot_dir,
        out_path,
        today,
        alias_index=alias_index,
        industry_map=industry_map,
        max_single_industry_pct=max_single_industry_pct,
    )
    summary["pulled"] = pulled
    summary["offline"] = offline
    return summary


# ── CLI ──────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("pull", "filter", "build", "refresh"))
    ap.add_argument(
        "--snapshot-dir",
        type=Path,
        default=ROOT / "data" / "cpsc_avoid" / "snapshots",
        help="snapshot cache dir (use a /tmp path for scratch network pulls)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "cpsc_avoid" / "cpsc_avoid_exclusion.jsonl",
    )
    ap.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="offline CPSC dump JSON (raw list); skips network",
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="rebuild from snapshots only, no network",
    )
    ap.add_argument(
        "--max-single-industry-pct",
        type=float,
        default=DEFAULT_MAX_SINGLE_INDUSTRY_PCT,
    )
    args = ap.parse_args(argv)

    if args.command == "pull":
        if args.fixture:
            records = load_dump_response(args.fixture)
            path = write_snapshot(
                args.snapshot_dir,
                {
                    "pulled_at": dt.datetime.now(UTC).isoformat(),
                    "pull_ts": utc_compact_now(),
                    "endpoint": "fixture:" + str(args.fixture),
                    "note": "offline fixture ingest",
                    "count": len(records),
                    "results": records,
                },
            )
        else:
            path, records = pull_cpsc(args.snapshot_dir)
        print(f"cpsc: {len(records)} records -> {path}")
    elif args.command in ("build", "refresh"):
        summary = refresh_cpsc_avoid(
            args.snapshot_dir,
            args.out,
            offline=args.offline or args.command == "build",
            max_single_industry_pct=args.max_single_industry_pct,
        )
        print(json.dumps(summary, indent=2))
    elif args.command == "filter":
        if args.fixture:
            records = load_dump_response(args.fixture)
        else:
            records = [json.loads(line) for line in sys.stdin if line.strip()]
            if len(records) == 1 and isinstance(records[0], list):
                records = records[0]
        kept, rejects = filter_records(records)
        for rec in kept:
            sys.stdout.write(json.dumps(rec, separators=(",", ":")) + "\n")
        print(f"kept={len(kept)} rejects={len(rejects)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
