#!/usr/bin/env python3
"""fda_avoid.py — openFDA Class I recall avoid-list pipeline (researcher #5 spec).

Research artifact only. Produces a JSONL exclusion list of tickers under a
recent Class I drug/device recall overhang. No live trading.

Theme 1, idea #2 (UNSTRUCTURED_ALPHA_IDEAS.md): Class I recalls on a firm's
top products -> avoid/short 20d (drug) / 40d (device). Backfill to ~2004 via
the keyless openFDA enforcement endpoints (drug + device).

PIT DISCIPLINE — READ BEFORE TOUCHING:
  1. ANCHOR ON ``report_date``. NEVER on ``recall_initiation_date``. The
     initiation date is when the firm started the recall; the report date is
     when FDA published/processed the enforcement record, i.e. the first
     timestamp a market participant polling api.fda.gov could observe.
     Per the researcher #5 spec, initiation precedes report by a median of
     ~139 days. Anchoring (or backtesting) on initiation_date therefore
     injects ~4.5 months of lookahead: the signal appears tradable months
     before it was knowable. ``initiation_lag_days()`` exists so this leak
     can be measured, not used.
  2. ``recall_initiation_date`` ALSO carries garbage defaults (e.g. device
     records stamped 19301211 — decades before device regulation). Records
     with insane initiation dates are quarantined, not repaired: imputing
     would invent knowledge. See ``is_sane_fda_date()``.
  3. Snapshots are IMMUTABLE. Every pull writes a new timestamped file and
     ``write_snapshot()`` refuses to overwrite. The exclusion list is a
     deterministic derived artifact rebuildable from snapshots; snapshots
     themselves are never mutated.
  4. Liquidity gates (price >= $3, ADV >= $2M) take PIT inputs INJECTED by the
     caller (``get_quote``), never hardcoded spot values. Missing PIT data
     fails closed (gated out), never passes silently.
  5. Entity resolution goes through the shared alias-table interface
     (``src/alias_resolve.py``). Substring matching is FORBIDDEN: e.g. naive
     ``"endo" in name`` maps J&J's "Ethicon Endo-Surgery" device recalls onto
     Endo Pharmaceuticals — a false positive with real P&L consequences.
     Until the alias table is wired, ``resolve_ticker()`` returns
     unresolved (None) rather than guessing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Shared alias-table interface (join stub) ──────────────────────────────
try:
    # Works when src/ is on sys.path (repo convention; tests do this too).
    from alias_resolve import resolve as _alias_resolve_fn  # type: ignore

    _HAS_ALIAS_TABLE = True
except Exception:  # pragma: no cover — offline / missing table path
    _alias_resolve_fn = None
    _HAS_ALIAS_TABLE = False

# ── Endpoints & policy ────────────────────────────────────────────────────
DRUG_ENDPOINT = "https://api.fda.gov/drug/enforcement.json"
DEVICE_ENDPOINT = "https://api.fda.gov/device/enforcement.json"
ENDPOINTS = {"drug": DRUG_ENDPOINT, "device": DEVICE_ENDPOINT}

MIN_REPORT_DATE = dt.date(2004, 1, 1)  # openFDA enforcement backfill horizon
HOLD_DAYS = {"drug": 20, "device": 40}  # idea #2: short/avoid windows
DEFAULT_MIN_PRICE = 3.0
DEFAULT_MIN_ADV_USD = 2_000_000.0
PAGE_LIMIT = 100  # keyless-safe page size for api.fda.gov

# Dates older than this are data-entry garbage, not history. Device
# regulation starts in 1976 (Medical Device Amendments); values like
# 19301211 seen in the wild on recall_initiation_date are defaults/typos.
EARLIEST_SANE_DATE = dt.date(1970, 1, 1)
FUTURE_TOLERANCE_DAYS = 7  # allow small reporting-clock skew, nothing more

UTC = dt.timezone.utc


# ═══════════════════════════════════════════════════════════════════════════
# Dates
# ═══════════════════════════════════════════════════════════════════════════
def parse_fda_date(value: object) -> dt.date | None:
    """Parse an openFDA YYYYMMDD (or YYYY-MM-DD) field. None if unparseable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = text.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def is_sane_fda_date(day: dt.date | None, today: dt.date | None = None) -> bool:
    """Range sanity gate. Rejects garbage defaults (e.g. 1930-12-11) and
    future timestamps beyond reporting-clock tolerance."""
    if day is None:
        return False
    today = today or dt.datetime.now(UTC).date()
    return EARLIEST_SANE_DATE <= day <= today + dt.timedelta(days=FUTURE_TOLERANCE_DAYS)


def is_class_i(record: dict) -> bool:
    """Strict Class I check. The field is a controlled vocabulary
    ('Class I' / 'Class II' / 'Class III'); anything else fails closed."""
    return str(record.get("classification", "")).strip().lower() == "class i"


def anchor_date(record: dict) -> dt.date | None:
    """THE event timestamp: ``report_date`` — first observable via the API.

    WARNING: do not substitute ``recall_initiation_date``. Median
    initiation->report lag is ~139d (researcher #5 spec); using initiation
    as the anchor backdates knowledge by months = lookahead bias.
    """
    return parse_fda_date(record.get("report_date"))


def initiation_lag_days(record: dict) -> int | None:
    """Diagnostic only: report_date minus recall_initiation_date in days.

    Positive values quantify how early an initiation-anchored backtest would
    be cheating. Returns None when either date is missing/unparseable.
    This helper exists to MEASURE the leak, never to time entry."""
    report = parse_fda_date(record.get("report_date"))
    init = parse_fda_date(record.get("recall_initiation_date"))
    if report is None or init is None:
        return None
    return (report - init).days


# ═══════════════════════════════════════════════════════════════════════════
# Filter (Class I + report_date floor + sane dates)
# ═══════════════════════════════════════════════════════════════════════════
def classify_record(
    record: dict,
    today: dt.date | None = None,
    min_report_date: dt.date = MIN_REPORT_DATE,
) -> tuple[str, str]:
    """Return ('keep'|'drop', reason) for one raw enforcement record."""
    today = today or dt.datetime.now(UTC).date()
    if not is_class_i(record):
        return "drop", "not_class_i"
    report = parse_fda_date(record.get("report_date"))
    if report is None:
        return "drop", "missing_or_bad_report_date"
    if not is_sane_fda_date(report, today):
        if report < EARLIEST_SANE_DATE:
            return "drop", "insane_report_date"
        return "drop", "report_date_in_future"
    if report < min_report_date:
        return "drop", "report_date_before_2004"
    # Initiation date is NOT the anchor, but an insane value marks the
    # record as data-quality suspect -> quarantine (auditable), don't impute.
    # Missing initiation is fine (field is optional upstream).
    raw_init = record.get("recall_initiation_date")
    if raw_init not in (None, ""):
        init = parse_fda_date(raw_init)
        if init is None or not is_sane_fda_date(init, today):
            return "drop", "insane_initiation_date"
    return "keep", "ok"


def filter_records(
    records: list[dict],
    category: str,
    today: dt.date | None = None,
    min_report_date: dt.date = MIN_REPORT_DATE,
) -> tuple[list[dict], list[dict]]:
    """Split raw records into (kept, rejects). Rejects carry
    ``_reject_reason`` for audit; kept rows are untouched raw dicts."""
    kept, rejects = [], []
    for rec in records:
        verdict, reason = classify_record(rec, today, min_report_date)
        if verdict == "keep":
            kept.append(rec)
        else:
            rejects.append({**rec, "_reject_reason": reason, "_category": category})
    return kept, rejects


# ═══════════════════════════════════════════════════════════════════════════
# Exclusion windows (T+1 entry, 20d drug / 40d device, calendar days)
# ═══════════════════════════════════════════════════════════════════════════
def entry_date(report: dt.date) -> dt.date:
    """T+1 entry: first full session after the report date.

    Calendar-day convention. A trading-calendar roll (weekends/holidays)
    must be applied downstream with PIT price data; this artifact stays in
    calendar days so it is reproducible without a market calendar."""
    return report + dt.timedelta(days=1)


def window_end(report: dt.date, category: str) -> dt.date:
    """Inclusive last excluded day: entry + hold_days - 1."""
    return entry_date(report) + dt.timedelta(days=HOLD_DAYS[category] - 1)


def exclusion_window(report: dt.date, category: str) -> tuple[dt.date, dt.date]:
    """(entry_date, window_end) inclusive, both calendar days."""
    start = entry_date(report)
    return start, start + dt.timedelta(days=HOLD_DAYS[category] - 1)


def is_excluded(
    as_of: dt.date, start: dt.date, end: dt.date
) -> bool:
    """Inclusive membership test for an exclusion window."""
    return start <= as_of <= end


# ═══════════════════════════════════════════════════════════════════════════
# Join stub — shared alias-table interface
# ═══════════════════════════════════════════════════════════════════════════
def _norm_tokens(name: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", str(name or "").lower()))


def substring_match_would_false_positive(query: str, candidate: str) -> bool:
    """Demonstrate the forbidden naive match: True when ``query`` appears as
    a raw substring of ``candidate``. E.g. query 'Endo' vs candidate
    'Ethicon Endo-Surgery' -> True (substring hit) although these are
    different issuers (Endo Pharmaceuticals vs J&J's Ethicon division)."""
    q, c = str(query).lower(), str(candidate).lower()
    return bool(q) and q in c


def is_safe_alias_match(query: str, candidate: str) -> bool:
    """Conservative alias predicate: normalized full-string equality only.
    NEVER substring containment. 'Endo' != 'Ethicon Endo-Surgery'."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()

    q, c = norm(query), norm(candidate)
    return bool(q) and q == c


def resolve_ticker(
    recalling_firm: str, alias_index: dict | None = None
) -> tuple[str | None, str | None, str]:
    """Join hook: firm name -> (ticker, CIK, confidence).

    Delegates to the shared alias table (``src/alias_resolve.resolve``) when
    an index is supplied. Otherwise returns (None, None, reason) — a
    deliberate unresolved stub.

    TODO(researcher #5): wire the shared subsidiary->CIK alias table
    (EDGAR 10-K Exhibit 21 trees + company_tickers.json + OpenCorporates +
    human-reviewed golden set) and pass the built index here. Do NOT
    "resolve" with ``in``/substring heuristics in the meantime — see
    ``substring_match_would_false_positive`` / the Endo-vs-Ethicon trap.
    """
    if _HAS_ALIAS_TABLE and alias_index is not None and recalling_firm:
        try:
            ticker, cik, conf = _alias_resolve_fn(recalling_firm, alias_index)  # type: ignore
            if ticker:
                return ticker, cik, str(conf)
            return None, None, "none"
        except Exception as exc:  # fail closed, never guess
            return None, None, f"alias_error:{type(exc).__name__}"
    if not recalling_firm:
        return None, None, "empty_firm_name"
    if _HAS_ALIAS_TABLE:
        return None, None, "unresolved:alias-index-required"
    return None, None, "unresolved:alias-table-missing"


# ═══════════════════════════════════════════════════════════════════════════
# Liquidity gate (PIT inputs injected, never hardcoded spot data)
# ═══════════════════════════════════════════════════════════════════════════
def passes_liquidity(
    price: float | None,
    adv_usd: float | None,
    min_price: float = DEFAULT_MIN_PRICE,
    min_adv_usd: float = DEFAULT_MIN_ADV_USD,
) -> bool:
    """price >= min_price AND ADV >= min_adv_usd. None (missing PIT data)
    fails closed -> False. Thresholds are parameters, not constants."""
    if price is None or adv_usd is None:
        return False
    try:
        return float(price) >= float(min_price) and float(adv_usd) >= float(min_adv_usd)
    except (TypeError, ValueError):
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Puller — keyless, paginated, immutable snapshots
# ═══════════════════════════════════════════════════════════════════════════
def utc_compact_now() -> str:
    return dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def snapshot_filename(category: str, pull_ts: str) -> str:
    return f"{category}_enforcement_pull_{pull_ts}.json"


def write_snapshot(
    snapshot_dir: Path, category: str, payload: dict, pull_ts: str | None = None
) -> Path:
    """Persist a raw pull payload. NEVER overwrites: on filename collision a
    numeric suffix is appended, the existing file is left byte-identical."""
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pull_ts = pull_ts or utc_compact_now()
    path = snapshot_dir / snapshot_filename(category, pull_ts)
    if path.exists():
        stem = path.stem
        n = 1
        while (snapshot_dir / f"{stem}_{n:02d}.json").exists():
            n += 1
        path = snapshot_dir / f"{stem}_{n:02d}.json"
    # Exclusive create: belt-and-braces against TOCTOU overwrite.
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    return path


def load_snapshot(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_openfda_response(path: Path) -> list[dict]:
    """Offline path: read cached openFDA response fixtures
    (``{"results": [...]}`` or ``{"results": [...], "meta": ...}``)."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"unrecognized openFDA payload shape in {path}")


def _default_http_get(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "alphaHunt-research/1.0 (Class-I avoid-list; contact research@example.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def pull_openfda(
    category: str,
    snapshot_dir: Path,
    http_get=None,
    limit: int = PAGE_LIMIT,
    max_records: int | None = None,
    search: str | None = None,
    pull_ts: str | None = None,
    timeout: int = 60,
) -> tuple[Path, list[dict]]:
    """Paginated keyless pull of one enforcement endpoint.

    ``http_get`` is injectable (tests/fixtures/offline); defaults to a
    keyless urllib GET. The FULL raw ``results`` payload plus pull metadata
    is snapshotted immutably; returns (snapshot_path, records)."""
    if category not in ENDPOINTS:
        raise ValueError(f"unknown category: {category!r} (want drug|device)")
    http_get = http_get or (lambda url: _default_http_get(url, timeout))
    base = ENDPOINTS[category]
    pulled_at = dt.datetime.now(UTC).isoformat()
    pull_ts = pull_ts or utc_compact_now()

    records: list[dict] = []
    skip = 0
    while True:
        params = {"limit": limit, "skip": skip}
        if search:
            params["search"] = search
        url = base + "?" + urllib.parse.urlencode(params)
        payload = http_get(url)
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected response shape for {url}")
        if "error" in payload and not payload.get("results"):
            raise RuntimeError(f"openFDA error for {url}: {payload['error']}")
        batch = payload.get("results") or []
        records.extend(batch)
        if max_records is not None and len(records) >= max_records:
            records = records[:max_records]
            break
        total = ((payload.get("meta") or {}).get("results") or {}).get("total")
        if len(batch) < limit:
            break
        skip += limit
        if total is not None and skip >= total:
            break

    snapshot = {
        "pulled_at": pulled_at,
        "pull_ts": pull_ts,
        "category": category,
        "endpoint": base,
        "search": search,
        "page_limit": limit,
        "count": len(records),
        "results": records,  # raw, unmodified
    }
    path = write_snapshot(snapshot_dir, category, snapshot, pull_ts)
    return path, records


# ═══════════════════════════════════════════════════════════════════════════
# Exclusion-list build + weekly refresh
# ═══════════════════════════════════════════════════════════════════════════
def _recall_key(category: str, record: dict) -> str:
    num = str(record.get("recall_number") or "").strip()
    if num:
        return f"{category}:{num}"
    firm = str(record.get("recalling_firm") or "")[:80]
    prod = str(record.get("product_description") or "")[:80]
    return f"{category}:nokey:{hash((firm, prod, str(record.get('report_date')))) & 0xFFFFFFFF:08x}"


def build_exclusion_rows(
    kept: list[dict],
    category: str,
    pulled_at: str | None = None,
    alias_index: dict | None = None,
) -> list[dict]:
    """Map filtered raw records -> exclusion rows.

    Ticker/CIK are None until the alias table resolves them (join stub).
    Entry is T+1 after report_date; window_end per HOLD_DAYS."""
    rows = []
    for rec in kept:
        report = parse_fda_date(rec.get("report_date"))
        assert report is not None  # guaranteed by filter_records
        start, end = exclusion_window(report, category)
        ticker, cik, conf = resolve_ticker(rec.get("recalling_firm") or "", alias_index)
        reason_bits = str(rec.get("reason_for_recall") or rec.get("product_description") or "").strip()
        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "match_confidence": conf,
                "category": category,
                "classification": rec.get("classification", ""),
                "report_date": report.isoformat(),
                "entry_date": start.isoformat(),
                "window_end": end.isoformat(),
                "hold_days": HOLD_DAYS[category],
                "reason": f"Class I {category} recall {rec.get('recall_number', '')}: {reason_bits[:280]}".strip(),
                "recall_number": rec.get("recall_number", ""),
                "recalling_firm": rec.get("recalling_firm", ""),
                "liquidity_checked": False,
                "pulled_at": pulled_at,
            }
        )
    return rows


def apply_liquidity(
    rows: list[dict],
    get_quote=None,
    min_price: float = DEFAULT_MIN_PRICE,
    min_adv_usd: float = DEFAULT_MIN_ADV_USD,
) -> tuple[list[dict], list[dict]]:
    """Split exclusion rows into (passing, gated).

    ``get_quote(ticker, as_of_iso) -> (price, adv_usd) | None`` is INJECTED
    PIT market data. With ``get_quote=None`` (no PIT inputs available) every
    row is returned in ``passing`` with ``liquidity_checked=False`` so the
    gate is visibly skipped, never silently applied on spot data."""
    if get_quote is None:
        return rows, []
    passing, gated = [], []
    for row in rows:
        quote = None
        if row.get("ticker"):
            try:
                quote = get_quote(row["ticker"], row["entry_date"])
            except Exception:
                quote = None
        price, adv = (quote or (None, None))
        row = {**row, "liquidity_checked": True, "price": price, "adv_usd": adv}
        (passing if passes_liquidity(price, adv, min_price, min_adv_usd) else gated).append(row)
    return passing, gated


def write_exclusion_jsonl(rows: list[dict], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in sorted(rows, key=lambda r: (r.get("report_date", ""), r.get("category", ""), r.get("recall_number", ""))):
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return out_path


def rebuild_from_snapshots(
    snapshot_dir: Path,
    out_path: Path,
    today: dt.date | None = None,
    min_report_date: dt.date = MIN_REPORT_DATE,
    alias_index: dict | None = None,
) -> dict:
    """Deterministic rebuild of the exclusion list from ALL immutable
    snapshots in ``snapshot_dir``. Dedups by (category, recall_number),
    keeping the earliest pulled copy. Rewriting this derived artifact is
    safe BECAUSE snapshots are never mutated."""
    snapshot_dir = Path(snapshot_dir)
    seen: dict[str, dict] = {}
    snapshots_read, raw_total, reject_total = 0, 0, 0
    for path in sorted(snapshot_dir.glob("*_enforcement_pull_*.json")):
        try:
            payload = load_snapshot(path)
        except (OSError, ValueError):
            continue
        category = payload.get("category", "")
        if category not in ENDPOINTS:
            continue
        snapshots_read += 1
        results = payload.get("results") or []
        raw_total += len(results)
        kept, rejects = filter_records(results, category, today, min_report_date)
        reject_total += len(rejects)
        for rec in kept:
            key = _recall_key(category, rec)
            if key not in seen or str(payload.get("pulled_at", "")) < str(seen[key][1]):
                seen[key] = (rec, payload.get("pulled_at", ""), category)
    rows: list[dict] = []
    for rec, pulled_at, category in seen.values():
        rows.extend(build_exclusion_rows([rec], category, pulled_at, alias_index))
    write_exclusion_jsonl(rows, out_path)
    return {
        "snapshots_read": snapshots_read,
        "raw_records": raw_total,
        "rejects": reject_total,
        "exclusions": len(rows),
        "out_path": str(out_path),
    }


def weekly_refresh(
    snapshot_dir: Path,
    out_path: Path,
    http_get=None,
    search: str | None = None,
    max_records: int | None = None,
    offline: bool = False,
    today: dt.date | None = None,
    alias_index: dict | None = None,
) -> dict:
    """Weekly entrypoint: pull both endpoints (unless offline), then rebuild
    the exclusion list from the full snapshot history.

    Scratch network pulls belong in /tmp (pass a /tmp snapshot_dir); the
    committed-code path uses cached fixtures/snapshots with offline=True.
    Returns a summary dict; raises on network failure (never half-writes:
    rebuild runs only after successful pulls)."""
    snapshot_dir, out_path = Path(snapshot_dir), Path(out_path)
    pulled: dict[str, str] = {}
    if not offline:
        for category in ("drug", "device"):
            path, _ = pull_openfda(
                category, snapshot_dir, http_get=http_get,
                search=search, max_records=max_records,
            )
            pulled[category] = str(path)
    summary = rebuild_from_snapshots(snapshot_dir, out_path, today, alias_index=alias_index)
    summary["pulled"] = pulled
    summary["offline"] = offline
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("pull", "filter", "build", "refresh"))
    ap.add_argument("--snapshot-dir", type=Path, default=ROOT / "lab_runs" / "fda_avoid" / "snapshots")
    ap.add_argument("--out", type=Path, default=ROOT / "lab_runs" / "fda_avoid" / "fda_avoid_exclusion.jsonl")
    ap.add_argument("--category", choices=("drug", "device"), default=None)
    ap.add_argument("--search", default=None, help='openFDA search, e.g. classification:"Class I"')
    ap.add_argument("--max-records", type=int, default=None)
    ap.add_argument("--fixture", type=Path, default=None,
                    help="offline openFDA response JSON (results list); skips network")
    ap.add_argument("--offline", action="store_true", help="rebuild from snapshots only, no network")
    args = ap.parse_args(argv)

    if args.command == "pull":
        cats = [args.category] if args.category else ["drug", "device"]
        for cat in cats:
            if args.fixture:
                records = load_openfda_response(args.fixture)
                path = write_snapshot(
                    args.snapshot_dir, cat,
                    {"pulled_at": dt.datetime.now(UTC).isoformat(), "pull_ts": utc_compact_now(),
                     "category": cat, "endpoint": "fixture:" + str(args.fixture),
                     "search": None, "page_limit": len(records),
                     "count": len(records), "results": records},
                )
            else:
                path, records = pull_openfda(cat, args.snapshot_dir, search=args.search, max_records=args.max_records)
            print(f"{cat}: {len(records)} records -> {path}")
    elif args.command in ("build", "refresh"):
        summary = weekly_refresh(
            args.snapshot_dir, args.out, search=args.search,
            max_records=args.max_records, offline=args.offline or args.command == "build",
        )
        print(json.dumps(summary, indent=2))
    elif args.command == "filter":
        # Filter stdin JSONL (or fixture) -> stdout kept + stderr reject count.
        if args.fixture:
            records = load_openfda_response(args.fixture)
        else:
            records = [json.loads(line) for line in sys.stdin if line.strip()]
        cat = args.category or "drug"
        kept, rejects = filter_records(records, cat)
        for rec in kept:
            sys.stdout.write(json.dumps(rec, separators=(",", ":")) + "\n")
        print(f"kept={len(kept)} rejects={len(rejects)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
