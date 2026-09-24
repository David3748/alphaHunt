#!/usr/bin/env python3
"""Leakage-aware historical forecasting lab for Ox Alpha.

The first experiment asks a falsifiable question:

    Given only filings available at an historical cutoff, what is the
    probability that the issuer announces, prices, or completes a materially
    dilutive equity/equity-linked financing within the next 90 days?

Forecasts and outcome labels are separate model calls. Issuer identifiers are
redacted from model inputs to reduce memorized-future leakage. SEC and market
data are cached, runs are resumable, and all primary artifacts are JSONL.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import html as lxml_html

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alphahunt as ah
import subagents as sa


EXPERIMENT = "dilution_90d_v1"
DEFAULT_RUN_DIR = Path("lab_runs/dilution90")
ANCHOR_FORMS = {"10-K", "10-Q", "20-F", "40-F"}
CONTEXT_FORMS = ANCHOR_FORMS | {
    "8-K", "6-K", "S-1", "S-3", "F-1", "F-3", "424B1", "424B2",
    "424B3", "424B4", "424B5", "SC 13D", "SC 13D/A",
}
FUTURE_FORMS = CONTEXT_FORMS | {
    "S-1/A", "S-3/A", "F-1/A", "F-3/A", "POS AM", "EFFECT", "DEF 14A",
}
FINANCING_TERMS = (
    "liquidity", "capital resources", "going concern", "substantial doubt",
    "cash and cash equivalents", "cash runway", "working capital",
    "at-the-market", "at the market", "equity offering", "public offering",
    "registered direct", "private placement", "purchase agreement",
    "subscription agreement", "securities purchase", "common stock",
    "ordinary shares", "preferred stock", "convertible", "conversion price",
    "warrant", "pre-funded", "shelf registration", "prospectus supplement",
    "shares outstanding", "authorized shares", "reverse stock split",
    "equity line", "commitment shares", "issuance", "dilution", "covenant",
)


FORECAST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "risk_band": {"type": "string", "enum": ["low", "medium", "high"]},
        "predicted_route": {
            "type": "string",
            "enum": ["none", "atm", "public_offering", "registered_direct",
                     "pipe", "convertible", "equity_line", "rights_offering", "other"],
        },
        "expected_timing_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 90},
        "thesis": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                    "mechanism": {"type": "string"},
                },
                "required": ["source", "quote", "mechanism"],
            },
        },
        "disconfirming_evidence": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["probability_pct", "risk_band", "predicted_route",
                 "expected_timing_days", "thesis", "evidence",
                 "disconfirming_evidence", "missing_information"],
}

OUTCOME_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "enum": ["yes", "no", "uncertain"]},
        "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "event_date": {"type": ["string", "null"]},
        "financing_route": {
            "type": "string",
            "enum": ["none", "atm", "public_offering", "registered_direct",
                     "pipe", "convertible", "equity_line", "rights_offering", "other"],
        },
        "materiality_basis": {"type": "string"},
        "estimated_dilution_pct": {"type": ["number", "null"], "minimum": 0},
        "gross_proceeds_usd": {"type": ["number", "null"], "minimum": 0},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["source", "quote", "interpretation"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["label", "confidence_pct", "event_date", "financing_route",
                 "materiality_basis", "estimated_dilution_pct", "gross_proceeds_usd",
                 "evidence", "notes"],
}


class CachedHTTP:
    """Small persistent GET cache with a global request-rate governor."""

    def __init__(self, root: Path, min_interval: float = 0.13):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _path(self, url: str) -> Path:
        return self.root / (hashlib.sha256(url.encode()).hexdigest() + ".gz")

    def get(self, url: str, timeout: int = 45) -> bytes:
        path = self._path(url)
        if path.exists():
            return gzip.decompress(path.read_bytes())
        last_error = None
        for attempt in range(5):
            with self._lock:
                delay = self.min_interval - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                self._last_request = time.monotonic()
            req = urllib.request.Request(
                url,
                headers={"User-Agent": ah.SEC_UA, "Accept-Encoding": "identity"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(gzip.compress(data, compresslevel=5))
                os.replace(tmp, path)
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (403, 429, 500, 502, 503, 504):
                    break
                time.sleep(min(2 ** attempt, 12))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                time.sleep(min(1.5 * (attempt + 1), 8))
        raise RuntimeError(f"GET failed for {url}: {last_error}")

    def json(self, url: str) -> dict:
        return json.loads(self.get(url).decode("utf-8", errors="replace"))


def column_rows(columns: dict) -> list[dict]:
    keys = ("form", "filingDate", "accessionNumber", "primaryDocument")
    if not all(isinstance(columns.get(k), list) for k in keys):
        return []
    n = min(len(columns[k]) for k in keys)
    rows = []
    optional = ("acceptanceDateTime", "reportDate", "fileNumber", "items", "size",
                "primaryDocDescription")
    for i in range(n):
        row = {k: columns[k][i] for k in keys}
        for key in optional:
            values = columns.get(key)
            if isinstance(values, list) and i < len(values):
                row[key] = values[i]
        rows.append(row)
    return rows


def submission_rows(cik: str, http: CachedHTTP, include_archives: bool = False) -> tuple[str, list[dict]]:
    url = ah.SUBMISSIONS_URL.format(cik)
    data = http.json(url)
    rows = column_rows(data.get("filings", {}).get("recent", {}))
    if include_archives:
        for old in data.get("filings", {}).get("files", []):
            name = old.get("name")
            if not name:
                continue
            try:
                rows.extend(column_rows(http.json(f"https://data.sec.gov/submissions/{name}")))
            except Exception:
                continue
    seen, normalized = set(), []
    for row in rows:
        acc = row.get("accessionNumber") or ""
        if not acc or acc in seen:
            continue
        seen.add(acc)
        row = dict(row)
        row["cik"] = str(int(cik))
        row["date"] = row.get("filingDate", "")[:10]
        acc_clean = acc.replace("-", "")
        row["url"] = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_clean}/{row.get('primaryDocument', '')}"
        )
        normalized.append(row)
    normalized.sort(key=lambda x: (x["date"], x["accessionNumber"]))
    return data.get("name", ""), normalized


def is_future_form(form: str) -> bool:
    return form in FUTURE_FORMS or form.startswith("424B") or form in {"10-K/A", "10-Q/A", "20-F/A"}


def clean_document(raw: bytes) -> str:
    # SEC filings are often multi-megabyte documents with hundreds of thousands
    # of XBRL nodes. lxml keeps traversal in C and is dramatically faster than
    # walking every BeautifulSoup tag in Python.
    try:
        document = lxml_html.fromstring(raw)
        for element in document.xpath(
            "//script|//style|//noscript|//*[contains(translate(@style, ' ', ''), 'display:none')]"
        ):
            element.drop_tree()
        text = "\n".join(document.itertext())
    except Exception:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def relevant_excerpt(text: str, max_chars: int = 180_000, radius: int = 4_500) -> str:
    """Retain identity/context plus overlapping windows around financing language."""
    if len(text) <= max_chars:
        return text
    lower = text.lower()
    # Keep a modest identity/header slice without allowing it to crowd all
    # keyword evidence out of small excerpts.
    header_chars = min(12_000, max(500, max_chars // 4), len(text))
    intervals = [(0, header_chars)]
    for term in FINANCING_TERMS:
        start = 0
        hits = 0
        while hits < 10:
            pos = lower.find(term, start)
            if pos < 0:
                break
            intervals.append((max(0, pos - radius), min(len(text), pos + radius)))
            start = pos + len(term)
            hits += 1
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 250:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces, used = [], 0
    for start, end in merged:
        if used >= max_chars:
            break
        piece = text[start:end]
        piece = piece[:max_chars - used]
        pieces.append(piece)
        used += len(piece)
    return "\n\n[...section boundary...]\n\n".join(pieces)


def redact_issuer(text: str, names: list[str]) -> str:
    out = text
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        if name.isdigit():
            out = out.replace(name, "[ISSUER_ID]")
        elif len(name) <= 6 and re.fullmatch(r"[A-Za-z.]+", name):
            out = re.sub(rf"\b{re.escape(name)}\b", "[ISSUER]", out, flags=re.I)
        else:
            out = re.sub(re.escape(name), "[ISSUER]", out, flags=re.I)
            # SEC registrant names frequently abbreviate legal suffixes while
            # the filing spells them out (CO vs Company, CORP vs Corporation).
            # Redact the stable core too when it is distinctive enough.
            tokens = re.findall(r"[A-Za-z0-9]+", name)
            suffixes = {"inc", "incorporated", "corp", "corporation", "co", "company",
                        "ltd", "limited", "plc", "holdings", "holding", "group", "the"}
            core = [t for t in tokens if t.lower() not in suffixes]
            if len("".join(core)) >= 7:
                pattern = r"\b" + r"[\s,.'&-]+".join(re.escape(t) for t in core) + r"\b"
                out = re.sub(pattern, "[ISSUER]", out, flags=re.I)
    return out


def accession_files(row: dict, http: CachedHTTP, include_exhibits: bool, max_files: int = 5) -> list[tuple[str, str]]:
    primary = row.get("primaryDocument", "")
    base = (
        f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/"
        f"{row['accessionNumber'].replace('-', '')}/"
    )
    chosen = [("primary", primary)] if primary else []
    if not include_exhibits:
        return [(label, base + name) for label, name in chosen]
    try:
        items = http.json(base + "index.json").get("directory", {}).get("item", [])
    except Exception:
        items = []
    scored = []
    for item in items:
        name = item.get("name", "")
        low = name.lower()
        if not low.endswith((".htm", ".html", ".txt")) or name == primary:
            continue
        if low.startswith("filingsummary") or re.fullmatch(r"r\d+\.htm", low):
            continue
        score = 0
        if re.search(r"(^|[-_.])ex?(hibit)?[-_.]?(10|99)", low):
            score += 50
        if any(k in low for k in ("press", "agreement", "purchase", "subscription", "prospectus")):
            score += 30
        if low.endswith((".htm", ".html")):
            score += 5
        scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    for _, name in scored[:max(0, max_files - len(chosen))]:
        chosen.append(("exhibit", name))
    return [(label, base + name) for label, name in chosen]


def filing_text(row: dict, http: CachedHTTP, include_exhibits: bool,
                max_chars: int = 240_000) -> str:
    pieces, used = [], 0
    for index, (kind, url) in enumerate(accession_files(row, http, include_exhibits)):
        try:
            text = relevant_excerpt(clean_document(http.get(url)), max_chars=max_chars)
        except Exception:
            continue
        if not text:
            continue
        remain = max_chars - used
        if remain <= 0:
            break
        text = text[:remain]
        pieces.append(f"[{kind.upper()} DOCUMENT {index + 1}]\n{text}")
        used += len(text)
    return "\n\n".join(pieces)


def make_pack(rows: list[dict], http: CachedHTTP, names: list[str],
              include_exhibits: bool, per_filing_chars: int = 240_000,
              total_chars: int = 900_000) -> str:
    parts, used = [], 0
    for i, row in enumerate(rows, 1):
        text = filing_text(row, http, include_exhibits, max_chars=per_filing_chars)
        if not text:
            continue
        text = redact_issuer(text, names)
        remain = total_chars - used
        if remain <= 0:
            break
        text = text[:remain]
        parts.append(f"===== SOURCE {i}: {row['form']} FILED {row['date']} =====\n{text}")
        used += len(text)
    return "\n\n".join(parts)


def first_close_on_or_after(chart: dict, day: dt.date) -> float | None:
    result = chart.get("chart", {}).get("result") or []
    if not result:
        return None
    timestamps = result[0].get("timestamp") or []
    adj = ((result[0].get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or [])
    closes = ((result[0].get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
    values = adj if len(adj) == len(timestamps) else closes
    for ts, value in zip(timestamps, values):
        if value is None:
            continue
        if dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date() >= day:
            return float(value)
    return None


def chart_return(ticker: str, start: dt.date, end: dt.date, http: CachedHTTP) -> float | None:
    p1 = int(dt.datetime.combine(start - dt.timedelta(days=7), dt.time(), tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.combine(end + dt.timedelta(days=10), dt.time(), tzinfo=dt.timezone.utc).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
        f"?period1={p1}&period2={p2}&interval=1d&events=history"
    )
    try:
        chart = http.json(url)
        a = first_close_on_or_after(chart, start)
        b = first_close_on_or_after(chart, end)
    except Exception:
        return None
    if not a or not b:
        return None
    return b / a - 1.0


def case_returns(ticker: str, cutoff: dt.date, horizon_days: int, http: CachedHTTP) -> dict:
    end = cutoff + dt.timedelta(days=horizon_days)
    stock = chart_return(ticker, cutoff, end, http)
    spy = chart_return("SPY", cutoff, end, http)
    return {
        "stock_return_90d": stock,
        "spy_return_90d": spy,
        "relative_return_90d": stock - spy if stock is not None and spy is not None else None,
    }


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    # JSON strings may legally contain Unicode line/paragraph separators. Python's
    # splitlines() treats those characters as record boundaries even though JSONL
    # only uses an ASCII LF delimiter, silently manufacturing bogus records.
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def build_company_case(company: dict, http: CachedHTTP, seed: int, start: dt.date,
                       end: dt.date, horizon_days: int) -> dict | None:
    ticker, cik = company.get("ticker", ""), str(company.get("cik", ""))
    if not ticker or not cik.isdigit():
        return None
    try:
        sec_name, rows = submission_rows(cik, http, include_archives=start.year < 2023)
    except Exception:
        return None
    eligible = [r for r in rows if r["form"] in ANCHOR_FORMS
                and start <= dt.date.fromisoformat(r["date"]) <= end]
    if not eligible:
        return None
    # Stable per-issuer choice makes resuming and parallel collection reproducible.
    issuer_seed = int(hashlib.sha256(f"{seed}|{cik}".encode()).hexdigest()[:16], 16)
    anchor = random.Random(issuer_seed).choice(eligible)
    cutoff = dt.date.fromisoformat(anchor["date"])
    case_id = hashlib.sha256(f"{EXPERIMENT}|{cik}|{cutoff}".encode()).hexdigest()[:16]
    prior_start = cutoff - dt.timedelta(days=180)
    prior = [r for r in rows if prior_start <= dt.date.fromisoformat(r["date"]) <= cutoff
             and r["form"] in CONTEXT_FORMS and r["accessionNumber"] != anchor["accessionNumber"]]
    prior.sort(key=lambda r: r["date"], reverse=True)
    snapshot_rows = [anchor] + prior[:3]
    future_end = cutoff + dt.timedelta(days=horizon_days)
    future = [r for r in rows if cutoff < dt.date.fromisoformat(r["date"]) <= future_end
              and is_future_form(r["form"])]
    future.sort(key=lambda r: r["date"])
    names = [ticker, cik, company.get("name", ""), sec_name]
    snapshot = make_pack(snapshot_rows, http, names, include_exhibits=True,
                         per_filing_chars=260_000, total_chars=900_000)
    if len(snapshot) < 2_000:
        return None
    future_pack = make_pack(future[:16], http, names, include_exhibits=True,
                            per_filing_chars=180_000, total_chars=1_000_000)
    returns = case_returns(ticker, cutoff, horizon_days, http)
    return {
        "experiment": EXPERIMENT,
        "case_id": case_id,
        "ticker": ticker,
        "company": sec_name or company.get("name", ""),
        "cik": cik,
        "cutoff": cutoff.isoformat(),
        "horizon_end": future_end.isoformat(),
        "anchor_form": anchor["form"],
        "anchor_accession": anchor["accessionNumber"],
        "snapshot_sources": [{"form": r["form"], "date": r["date"],
                              "accession": r["accessionNumber"]} for r in snapshot_rows],
        "future_sources": [{"form": r["form"], "date": r["date"],
                            "accession": r["accessionNumber"]} for r in future[:16]],
        "snapshot_text": snapshot,
        "future_text": future_pack,
        "returns": returns,
        "selection_note": "Randomized from the current SEC ticker universe; survivorship bias remains.",
    }


def build_cases(universe_path: Path, run_dir: Path, count: int, seed: int,
                start: dt.date, end: dt.date, horizon_days: int = 90,
                candidate_limit: int = 1200, concurrency: int = 6) -> list[dict]:
    cases_path = run_dir / "cases.jsonl"
    existing = load_jsonl(cases_path)
    if len(existing) >= count:
        return existing[:count]
    existing_ids = {r.get("case_id") for r in existing}
    raw_universe = json.loads(universe_path.read_text())
    # company_tickers.json contains separate rows for common, preferred, unit,
    # debt, and warrant tickers. The file is ordered with the primary/common
    # security first; choosing the shortest ticker can incorrectly select an
    # exchange-traded note (for example JSM instead of NAVI).
    by_cik = defaultdict(list)
    for row in raw_universe:
        by_cik[str(row.get("cik", ""))].append(row)
    universe = []
    for issuer_rows in by_cik.values():
        row = issuer_rows[0]
        name = row.get("name", "")
        ticker = row.get("ticker", "")
        if (re.search(
                r"\bacquisition\b.*\bcorp(?:oration)?\b|\bblank check\b|\btrust\b|"
                r"\betf\b|\bexchange[- ]traded fund\b",
                name, re.I)
                or re.search(r"-(?:P[A-Z]?|WT|UN|RI)$", ticker, re.I)):
            continue
        universe.append(row)
    rng = random.Random(seed)
    rng.shuffle(universe)
    http = CachedHTTP(run_dir / "cache" / "http")
    made = len(existing)
    existing_ciks = {r.get("cik") for r in existing}
    candidates = [r for r in universe[:candidate_limit] if str(r.get("cik", "")) not in existing_ciks]
    index = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        while made < count and index < len(candidates):
            needed = count - made
            batch_size = min(len(candidates) - index,
                             max(concurrency * 2, min(needed * 2, concurrency * 4)))
            batch = candidates[index:index + batch_size]
            index += batch_size
            futures = {pool.submit(build_company_case, company, http, seed,
                                   start, end, horizon_days): company for company in batch}
            for future in cf.as_completed(futures):
                try:
                    record = future.result()
                except Exception:
                    record = None
                if not record or record["case_id"] in existing_ids or made >= count:
                    continue
                append_jsonl(cases_path, record)
                existing.append(record)
                existing_ids.add(record["case_id"])
                existing_ciks.add(record["cik"])
                made += 1
                print(f"built {made}/{count}: {record['ticker']} cutoff={record['cutoff']} "
                      f"snapshot={len(record['snapshot_text']):,} future={len(record['future_text']):,}",
                      file=sys.stderr)
    if len(existing) < count:
        print(f"warning: built only {len(existing)} of {count} requested cases", file=sys.stderr)
    return existing[:count]


def json_schema_format(name: str, schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


class LLMCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, payload: dict) -> Path:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return self.root / (hashlib.sha256(raw.encode()).hexdigest() + ".json")

    def call_json(self, client: sa.OpenRouter, system: str, user: str,
                  schema_name: str, schema: dict, effort: str = "high") -> dict:
        key = {"model": client.model, "system": system, "user": user,
               "schema_name": schema_name, "schema": schema, "effort": effort,
               "transport": "required_tool_v1"}
        path = self.path_for(key)
        if path.exists():
            return json.loads(path.read_text())["parsed"]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        tool = {"type": "function", "function": {
            "name": schema_name,
            "description": "Submit the completed structured result.",
            "parameters": schema,
            "strict": True,
        }}
        last_error = None
        parsed = raw = None
        # Strict tools are much more reliable than free-form JSON, but Ox can
        # still put an unescaped quotation mark inside function arguments. Retry
        # semantic parse failures as well as HTTP/empty-message failures.
        for semantic_attempt in range(3):
            try:
                attempt_messages = messages
                if semantic_attempt:
                    attempt_messages = messages + [{
                        "role": "system",
                        "content": (
                            "SERIALIZATION RETRY: The prior function arguments were invalid JSON. "
                            "Submit the same analysis as valid function arguments. In evidence quote "
                            "strings, choose exact excerpts that do not contain embedded quotation marks."
                        ),
                    }]
                raw = client.chat(
                    attempt_messages,
                    temperature=0.1,
                    reasoning_effort=effort,
                    # Ox performs mandatory hidden reasoning. Five thousand tokens
                    # can be exhausted before it emits the required tool call on a
                    # near-context-limit filing pack, yielding an HTTP-200/null body.
                    max_tokens=12_000,
                    tools=[tool],
                    tool_choice={"type": "function", "function": {"name": schema_name}},
                    extra_headers={"X-OpenRouter-Cache": "true", "X-OpenRouter-Cache-TTL": "604800"},
                )
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = sa.parse_llm_json(raw)
                if isinstance(parsed, dict):
                    break
                raise TypeError("structured result was not an object")
            except (sa.SubagentError, json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc
                parsed = None
        if parsed is None:
            try:
                raw = client.chat(messages, temperature=0.1,
                                  response_format={"type": "json_object"},
                                  reasoning_effort=effort, max_tokens=12_000)
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = sa.parse_llm_json(raw)
            except (sa.SubagentError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise sa.SubagentError(f"no valid structured result: {exc or last_error}") from exc
        if not isinstance(parsed, dict):
            raise sa.SubagentError(f"no valid structured result: {last_error}")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"parsed": parsed, "raw": raw}), encoding="utf-8")
        os.replace(tmp, path)
        return parsed


FORECAST_SYSTEM = """You are a calibrated forensic forecaster in a historical, leakage-controlled experiment.
You see only documents available at the stated cutoff. Do not use outside knowledge, remembered company events,
or facts after the cutoff. The issuer identity has been redacted deliberately.

Forecast whether, within the NEXT 90 CALENDAR DAYS, the issuer will announce, price, enter a binding agreement for,
or complete a MATERIAL DILUTIVE FINANCING. Count common/ordinary shares, equity-linked preferred or convertible
securities, pre-funded/common warrants, PIPEs, registered directs, equity lines, rights offerings, or actual ATM sales.
"Material" means reasonably at least 5% of pre-event basic shares or an economically comparable transfer. Do NOT count
a shelf registration, dormant ATM capacity, employee compensation plan, acquisition consideration, debt without an
equity conversion feature, or a reverse split by itself unless an actual qualifying issuance occurs in the window.

Use the base rate as well as issuer-specific evidence. Quote only supplied text. Explicitly recognize evidence against
the event. Return one JSON object with EXACTLY these top-level keys:
probability_pct, risk_band, predicted_route, expected_timing_days, thesis, evidence,
disconfirming_evidence, missing_information. Each evidence item must have source, quote, and mechanism.
Do not add an issuer field. Return JSON and nothing else."""

OUTCOME_SYSTEM = """You are labeling the realized outcome for a historical forecasting experiment using ONLY the
supplied filings. Determine whether the future-window documents establish that the issuer announced, priced, entered
a binding agreement for, or completed a MATERIAL DILUTIVE FINANCING during the stated 90-day window.

Material means reasonably at least 5% of pre-event basic shares or economically comparable. Count common/ordinary
shares, equity-linked preferred or convertible securities, pre-funded/common warrants, PIPEs, registered directs,
equity lines, rights offerings, and actual ATM sales. Exclude shelf capacity with no sale, employee plans, M&A
consideration, non-convertible debt, and reverse splits alone. "Subsequent event" disclosure counts only when it says
the qualifying financing occurred or became binding inside the window. If materiality cannot be established, label
uncertain rather than guessing. Quote only the supplied documents. Return one JSON object with EXACTLY these top-level
keys: label, confidence_pct, event_date, financing_route, materiality_basis, estimated_dilution_pct,
gross_proceeds_usd, evidence, notes. Each evidence item must have source, quote, and interpretation.
Do not add an issuer field. Return JSON and nothing else."""


def probability_value(result: dict) -> float | None:
    candidates = [
        result.get("probability_pct"), result.get("forecast_probability_pct"),
        result.get("final_probability"), result.get("probability"),
    ]
    nested = result.get("forecast")
    if isinstance(nested, dict):
        candidates.extend([nested.get("probability_material_dilutive_financing_90d"),
                           nested.get("probability_pct"), nested.get("probability")])
    for value in candidates:
        if isinstance(value, (int, float)):
            value = float(value)
            if 0 <= value <= 1:
                value *= 100
            if 0 <= value <= 100:
                return value
    return None


def evidence_items(value, interpretation_key: str) -> list[dict]:
    out = []
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, dict) and item.get("quote"):
            out.append({
                "source": str(item.get("source", "unspecified supplied filing")),
                "quote": str(item["quote"]),
                interpretation_key: str(item.get(interpretation_key) or item.get("mechanism") or ""),
            })
            continue
        if not isinstance(item, str):
            continue
        # Ox often embeds a supported quotation inside a prose evidence item.
        quoted = re.findall(r"['\u2018\u2019\u201c\u201d]([^'\u2018\u2019\u201c\u201d]{12,})['\u2018\u2019\u201c\u201d]", item)
        quote = max(quoted, key=len) if quoted else item
        out.append({"source": "unspecified supplied filing", "quote": quote,
                    interpretation_key: item})
    return out


def normalize_forecast_result(result: dict) -> dict:
    probability = probability_value(result)
    if probability is None:
        raise ValueError(f"forecast has no usable probability: {sorted(result)}")
    route = result.get("predicted_route")
    nested = result.get("forecast")
    if not route and isinstance(nested, dict):
        likelihoods = nested.get("event_type_likelihoods") or result.get("event_type_likelihoods") or {}
        if likelihoods:
            raw_route = max(likelihoods, key=likelihoods.get)
            route = {"atm_sales": "atm", "registered_direct_or_pipe": "registered_direct",
                     "convertible_or_equity_linked": "convertible",
                     "rights_offering_or_equity_line": "rights_offering"}.get(raw_route, "other")
    route = route if route in {"none", "atm", "public_offering", "registered_direct", "pipe",
                              "convertible", "equity_line", "rights_offering", "other"} else "none"
    thesis = result.get("thesis") or result.get("rationale") or result.get("reasoning_summary") or ""
    evidence = evidence_items(result.get("evidence") or result.get("evidence_for") or
                              result.get("key_evidence_for") or [], "mechanism")
    against = result.get("disconfirming_evidence") or result.get("evidence_against") or result.get("key_evidence_against") or []
    against = [str(x) for x in against] if isinstance(against, list) else [str(against)]
    return {
        "probability_pct": int(round(probability)),
        "risk_band": "high" if probability >= 65 else "medium" if probability >= 30 else "low",
        "predicted_route": route,
        "expected_timing_days": result.get("expected_timing_days"),
        "thesis": str(thesis),
        "evidence": evidence,
        "disconfirming_evidence": against,
        "missing_information": [str(x) for x in result.get("missing_information", result.get("key_uncertainties", []))],
        "_raw_shape": sorted(result.keys()),
    }


def normalize_outcome_result(result: dict) -> dict:
    label = str(result.get("label", result.get("outcome", result.get("material_dilutive_financing", "uncertain")))).lower()
    if label in ("true", "1", "occurred", "yes") or label.startswith("yes_"):
        label = "yes"
    elif label in ("false", "0", "did_not_occur", "no", "no_event") or label.startswith("no_"):
        label = "no"
    else:
        label = "uncertain"
    basis = str(result.get("materiality_basis", result.get("basis", "")))
    estimated_dilution = result.get("estimated_dilution_pct")
    if (label == "uncertain" and isinstance(estimated_dilution, (int, float))
            and estimated_dilution >= 5 and re.search(r"\b(actual|completed|issued|sold|raised)\b", basis, re.I)):
        label = "yes"
    route = result.get("financing_route") or result.get("route") or "none"
    route_text = str(route).lower()
    if "at-the-market" in route_text or re.search(r"\batm\b", route_text):
        route = "atm"
    elif "registered direct" in route_text:
        route = "registered_direct"
    elif "public offering" in route_text:
        route = "public_offering"
    elif "private" in route_text or "pipe" in route_text:
        route = "pipe"
    elif "convert" in route_text:
        route = "convertible"
    elif "equity line" in route_text:
        route = "equity_line"
    elif "rights" in route_text:
        route = "rights_offering"
    if (label == "uncertain" and route in (None, "none")
            and result.get("estimated_dilution_pct") in (0, 0.0)
            and re.match(r"^(no\b|none\b)", basis.strip(), re.I)):
        # Preserve Ox's semantics when it emitted a non-schema negative label
        # such as no_event/no_material_dilutive_financing before normalization.
        label = "no"
    if route not in {"none", "atm", "public_offering", "registered_direct", "pipe",
                     "convertible", "equity_line", "rights_offering", "other"}:
        route = "other" if label == "yes" else "none"
    return {
        "label": label,
        "confidence_pct": int(result.get("confidence_pct", result.get("confidence", 50)))
        if isinstance(result.get("confidence_pct", result.get("confidence", 50)), (int, float)) else 50,
        "event_date": result.get("event_date"),
        "financing_route": route,
        "materiality_basis": basis,
        "estimated_dilution_pct": estimated_dilution,
        "gross_proceeds_usd": result.get("gross_proceeds_usd"),
        "evidence": evidence_items(result.get("evidence") or result.get("supporting_evidence") or [], "interpretation"),
        "notes": str(result.get("notes", result.get("rationale", ""))),
        "_raw_shape": sorted(result.keys()),
    }


def forecast_user(case: dict, replicate: int) -> str:
    return (
        f"INDEPENDENT FORECAST REPLICATE: {replicate}\n"
        f"CUTOFF: {case['cutoff']}\nFORECAST WINDOW END: {case['horizon_end']}\n"
        f"ANCHOR FORM: {case['anchor_form']}\n\n"
        f"HISTORICAL SNAPSHOT (NO DOCUMENT FILED AFTER CUTOFF):\n{case['snapshot_text']}"
    )


def outcome_user(case: dict, replicate: int) -> str:
    future = case.get("future_text") or "[No relevant future-window filing text was collected.]"
    return (
        f"INDEPENDENT OUTCOME-LABEL REPLICATE: {replicate}\n"
        f"WINDOW: after {case['cutoff']} through {case['horizon_end']} inclusive\n\n"
        f"PRE-WINDOW CAPITAL-BASE CONTEXT:\n{case['snapshot_text'][:260_000]}\n\n"
        f"FUTURE-WINDOW FILINGS:\n{future}"
    )


def completed_keys(path: Path) -> set[tuple[str, int]]:
    return {(r.get("case_id"), int(r.get("replicate", 0))) for r in load_jsonl(path)
            if isinstance(r.get("result"), dict)}


def run_model_stage(cases: list[dict], run_dir: Path, client: sa.OpenRouter,
                    stage: str, replicates: int, concurrency: int) -> list[dict]:
    if stage not in ("forecast", "outcome"):
        raise ValueError(stage)
    out_path = run_dir / ("forecasts.jsonl" if stage == "forecast" else "outcomes.jsonl")
    done = completed_keys(out_path)
    cache = LLMCache(run_dir / "cache" / "llm")
    jobs = [(case, rep) for case in cases for rep in range(1, replicates + 1)
            if (case["case_id"], rep) not in done]

    def work(job):
        case, rep = job
        if stage == "forecast":
            result = cache.call_json(client, FORECAST_SYSTEM, forecast_user(case, rep),
                                     "dilution_forecast", FORECAST_SCHEMA, effort="high")
            result = normalize_forecast_result(result)
        else:
            result = cache.call_json(client, OUTCOME_SYSTEM, outcome_user(case, rep),
                                     "dilution_outcome", OUTCOME_SCHEMA, effort="high")
            result = normalize_outcome_result(result)
        return {"experiment": EXPERIMENT, "case_id": case["case_id"], "replicate": rep,
                "stage": stage, "model": client.model,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "result": result}

    if not jobs:
        return load_jsonl(out_path)
    print(f"{stage}: {len(jobs)} calls at concurrency {concurrency}", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        completed = 0
        for fut in cf.as_completed(futures):
            case, rep = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:
                print(f"{stage} failed {case['case_id']} r{rep}: {exc}", file=sys.stderr)
                continue
            append_jsonl(out_path, row)
            completed += 1
            print(f"{stage} {completed}/{len(jobs)}: {case['ticker']} r{rep}", file=sys.stderr)
    return load_jsonl(out_path)


def auc_score(points: list[tuple[float, int]]) -> float | None:
    pos = [p for p, y in points if y == 1]
    neg = [p for p, y in points if y == 0]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def mean_or_none(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def quote_match_rate(results: list[dict], source_text: str) -> float | None:
    """Check that model-provided evidence quotes occur in the supplied pack."""
    haystack = re.sub(r"\s+", " ", source_text).strip().lower()
    matches = []
    for result in results:
        for item in result.get("evidence", []):
            quote = re.sub(r"\s+", " ", str(item.get("quote", ""))).strip().lower()
            if len(quote) >= 12:
                matches.append(1.0 if quote in haystack else 0.0)
    return mean_or_none(matches)


def score_run(run_dir: Path) -> dict:
    cases = {r["case_id"]: r for r in load_jsonl(run_dir / "cases.jsonl")}
    forecasts, outcomes = defaultdict(list), defaultdict(list)
    for row in load_jsonl(run_dir / "forecasts.jsonl"):
        result = row.get("result") or {}
        try:
            result = normalize_forecast_result(result)
        except (TypeError, ValueError):
            continue
        if isinstance(result.get("probability_pct"), (int, float)):
            forecasts[row["case_id"]].append(result)
    for row in load_jsonl(run_dir / "outcomes.jsonl"):
        result = row.get("result") or {}
        result = normalize_outcome_result(result)
        if result.get("label") in ("yes", "no", "uncertain"):
            outcomes[row["case_id"]].append(result)
    scored = []
    for case_id, case in cases.items():
        fs, os_ = forecasts.get(case_id, []), outcomes.get(case_id, [])
        if not fs or not os_:
            continue
        labels = [o["label"] for o in os_ if o["label"] != "uncertain"]
        label = labels[0] if labels and len(set(labels)) == 1 else "uncertain"
        p = statistics.mean(float(f["probability_pct"]) for f in fs) / 100.0
        scored.append({
            "case_id": case_id, "ticker": case["ticker"], "company": case["company"],
            "cutoff": case["cutoff"], "probability": p, "label": label,
            "forecast_replicates": len(fs), "outcome_replicates": len(os_),
            "forecast_routes": [f.get("predicted_route") for f in fs],
            "outcome_routes": [o.get("financing_route") for o in os_],
            "forecast_quote_match_rate": quote_match_rate(fs, case.get("snapshot_text", "")),
            "outcome_quote_match_rate": quote_match_rate(os_, case.get("future_text", "")),
            **case.get("returns", {}),
        })
    resolved = [r for r in scored if r["label"] in ("yes", "no")]
    points = [(r["probability"], 1 if r["label"] == "yes" else 0) for r in resolved]
    brier = mean_or_none([(p - y) ** 2 for p, y in points])
    base_rate = mean_or_none([float(y) for _, y in points])
    base_brier = mean_or_none([(base_rate - y) ** 2 for _, y in points]) if base_rate is not None else None
    brier_skill = (1.0 - brier / base_brier) if brier is not None and base_brier else None
    calibration = []
    for low in (0.0, 0.2, 0.4, 0.6, 0.8):
        bucket = [(p, y) for p, y in points if low <= p < low + 0.2 or (low == 0.8 and p == 1.0)]
        if bucket:
            calibration.append({"range": [low, low + 0.2], "n": len(bucket),
                                "mean_probability": statistics.mean(p for p, _ in bucket),
                                "event_rate": statistics.mean(y for _, y in bucket)})
    high = [r for r in resolved if r["probability"] >= 0.7]
    low = [r for r in resolved if r["probability"] <= 0.3]
    baskets = []
    for threshold in (0.7, 0.5, 0.3, 0.2):
        basket = [r for r in scored if r["probability"] >= threshold
                  and r.get("relative_return_90d") is not None]
        if basket:
            returns = [r["relative_return_90d"] for r in basket]
            baskets.append({"threshold": threshold, "n": len(basket),
                            "tickers": [r["ticker"] for r in basket],
                            "mean_relative_return_90d": statistics.mean(returns),
                            "median_relative_return_90d": statistics.median(returns),
                            "negative_fraction": sum(v < 0 for v in returns) / len(returns)})
    metrics = {
        "experiment": EXPERIMENT,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cases_total": len(cases),
        "cases_scored": len(scored),
        "cases_resolved": len(resolved),
        "uncertain_or_disagreed": len(scored) - len(resolved),
        "event_rate": base_rate,
        "brier": brier,
        "base_rate_brier": base_brier,
        "brier_skill_score": brier_skill,
        "auc": auc_score(points),
        "high_probability_n": len(high),
        "high_probability_precision": mean_or_none([1.0 if r["label"] == "yes" else 0.0 for r in high]),
        "high_probability_mean_relative_return": mean_or_none([r.get("relative_return_90d") for r in high]),
        "low_probability_n": len(low),
        "low_probability_event_rate": mean_or_none([1.0 if r["label"] == "yes" else 0.0 for r in low]),
        "forecast_quote_match_rate": mean_or_none([r.get("forecast_quote_match_rate") for r in scored]),
        "outcome_quote_match_rate": mean_or_none([r.get("outcome_quote_match_rate") for r in scored]),
        "prediction_baskets": baskets,
        "calibration": calibration,
        "cases": sorted(scored, key=lambda r: r["probability"], reverse=True),
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    forecast_quote_s = (f"{metrics['forecast_quote_match_rate']:.0%}"
                        if metrics["forecast_quote_match_rate"] is not None else "n/a")
    outcome_quote_s = (f"{metrics['outcome_quote_match_rate']:.0%}"
                       if metrics["outcome_quote_match_rate"] is not None else "n/a")
    lines = ["# Ox Alpha historical dilution forecast", "",
             f"Experiment: `{EXPERIMENT}`", "",
             f"Cases: {len(cases)} total / {len(resolved)} resolved", "",
             f"Event rate: {base_rate:.1%}" if base_rate is not None else "Event rate: n/a",
             f"Brier score: {brier:.3f}" if brier is not None else "Brier score: n/a",
             f"Base-rate Brier: {base_brier:.3f}" if base_brier is not None else "Base-rate Brier: n/a",
             f"Brier skill vs. base rate: {brier_skill:.1%}" if brier_skill is not None else "Brier skill vs. base rate: n/a",
             f"AUC: {metrics['auc']:.3f}" if metrics["auc"] is not None else "AUC: n/a",
             f"Resolved precision at >=70%: {metrics['high_probability_precision']:.1%} ({metrics['high_probability_n']} names)"
             if metrics["high_probability_precision"] is not None else "Resolved precision at >=70%: n/a", ""]
    if baskets:
        lines.extend(["## Forecast baskets", "",
                      "| Minimum forecast | Names | Mean 90d relative return | Negative fraction |",
                      "|---:|---:|---:|---:|"])
        for basket in baskets:
            lines.append(f"| {basket['threshold']:.0%} | {basket['n']} | "
                         f"{basket['mean_relative_return_90d']:+.1%} | {basket['negative_fraction']:.0%} |")
        lines.append("")
    lines.extend(["## Cases", "",
                  "| Ticker | Cutoff | Forecast | Outcome | 90d relative return |",
                  "|---|---:|---:|---|---:|"])
    for row in metrics["cases"]:
        rr = row.get("relative_return_90d")
        rr_s = f"{rr:+.1%}" if rr is not None else "n/a"
        lines.append(f"| {row['ticker']} | {row['cutoff']} | {row['probability']:.0%} | {row['label']} | {rr_s} |")
    lines.extend(["", "## Limitations", "",
                  "- Issuers were sampled from the current ticker universe, so survivorship bias remains.",
                  "- Issuer redaction reduces but cannot eliminate model memorization of historical events.",
                  "- Outcome labels are model-extracted and require quoted-evidence audits before trading use.",
                  "- Exact forecast/outcome quote-match rates were only "
                  f"{forecast_quote_s}/{outcome_quote_s}; "
                  "table normalization and model paraphrase explain some misses, but manual verification remains mandatory.",
                  "- This pilot evaluates research signal, not executable returns after borrow, spread, and fees."])
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def make_client(args) -> sa.OpenRouter:
    return sa.OpenRouter(sa.get_api_key(args.api_key), model=args.model,
                         timeout=args.timeout, max_retries=args.retries)


def add_common(p):
    p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ox-lab", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build cached historical cases without model calls")
    add_common(b)
    b.add_argument("--universe", type=Path, default=Path("data/universe.json"))
    b.add_argument("--count", type=int, default=24)
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--start", type=parse_date, default=dt.date(2024, 1, 1))
    b.add_argument("--end", type=parse_date, default=dt.date.today() - dt.timedelta(days=120))
    b.add_argument("--build-concurrency", type=int, default=6)

    for name in ("forecast", "label"):
        c = sub.add_parser(name, help=f"run Ox {name} calls over built cases")
        add_common(c)
        c.add_argument("--max-cases", type=int, default=None)
        c.add_argument("--replicates", type=int, default=1)
        c.add_argument("--concurrency", type=int, default=8)
        c.add_argument("--model", default=sa.DEFAULT_MODEL)
        c.add_argument("--api-key", default=None)
        c.add_argument("--timeout", type=int, default=180)
        c.add_argument("--retries", type=int, default=3)

    s = sub.add_parser("score", help="score existing forecasts against outcome labels")
    add_common(s)

    r = sub.add_parser("run", help="build, forecast, label, and score")
    add_common(r)
    r.add_argument("--universe", type=Path, default=Path("data/universe.json"))
    r.add_argument("--count", type=int, default=24)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--start", type=parse_date, default=dt.date(2024, 1, 1))
    r.add_argument("--end", type=parse_date, default=dt.date.today() - dt.timedelta(days=120))
    r.add_argument("--build-concurrency", type=int, default=6)
    r.add_argument("--forecast-replicates", type=int, default=1)
    r.add_argument("--label-replicates", type=int, default=1)
    r.add_argument("--concurrency", type=int, default=8)
    r.add_argument("--model", default=sa.DEFAULT_MODEL)
    r.add_argument("--api-key", default=None)
    r.add_argument("--timeout", type=int, default=180)
    r.add_argument("--retries", type=int, default=3)

    args = p.parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "build":
        cases = build_cases(args.universe, args.run_dir, args.count, args.seed, args.start, args.end,
                            concurrency=args.build_concurrency)
        print(f"{len(cases)} cases -> {args.run_dir / 'cases.jsonl'}")
        return 0
    if args.cmd == "score":
        metrics = score_run(args.run_dir)
        print(json.dumps({k: v for k, v in metrics.items() if k != "cases"}, indent=2))
        return 0
    if args.cmd in ("forecast", "label"):
        cases = load_jsonl(args.run_dir / "cases.jsonl")
        if args.max_cases:
            cases = cases[:args.max_cases]
        client = make_client(args)
        run_model_stage(cases, args.run_dir, client,
                        "forecast" if args.cmd == "forecast" else "outcome",
                        args.replicates, args.concurrency)
        print(f"model calls={client.calls} prompt_tokens={client.total_prompt_tokens} completion_tokens={client.total_completion_tokens}")
        return 0
    cases = build_cases(args.universe, args.run_dir, args.count, args.seed, args.start, args.end,
                        concurrency=args.build_concurrency)
    client = make_client(args)
    run_model_stage(cases, args.run_dir, client, "forecast", args.forecast_replicates, args.concurrency)
    run_model_stage(cases, args.run_dir, client, "outcome", args.label_replicates, args.concurrency)
    metrics = score_run(args.run_dir)
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("cases", "calibration")}, indent=2))
    print(f"model calls={client.calls} prompt_tokens={client.total_prompt_tokens} completion_tokens={client.total_completion_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
