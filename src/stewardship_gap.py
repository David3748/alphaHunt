#!/usr/bin/env python3
"""stewardship_gap.py — 8-K Item 5.02 stewardship-gap exclude MVP (researcher #8).

Thesis: a CEO/CFO departure with no successor named in the filing AND no
same-role job posting within 30d of the event = stewardship gap -> exclude
the name for 90 trading days.

PIT discipline (load-bearing — read before backtesting):
  * Filing leg: EFTS full-text search for "Item 5.02" (forms=8-K,8-K/A) plus a
    submissions-API `items`-field filter ("5.02" in row["items"]). Filing date
    is the event date. PIT-safe.
  * Classifier leg: deterministic regex (role / successor / interim /
    disagreement / immediate). No LLM dependency. The DEPARTURE_SCHEMA /
    DEPARTURE_SYSTEM objects from unstructured_proto remain available as an
    OPTIONAL overlay (lazy import inside classify_with_llm_overlay()).
  * ATS leg has TWO paths with different PIT status:
      - CDX path (PIT): Common-Crawl-style reconstruction via
        web.archive.org CDX snapshots of the company's CAREERS-PAGE HTML.
        Only snapshots with timestamp <= event_date + 30d are admitted.
        Career-HTML only. We do NOT claim Greenhouse/Ashby JSON-API backfill:
        boards-api / posting-api are current-state-only endpoints.
      - Live path (NON-PIT): current Greenhouse boards-api / Ashby posting-api
        state. Forward-only monitoring; every record it touches is tagged
        provenance="live_non_pit" and MUST NOT enter backtest decisions.
  * Rule leg: CEO/CFO departure AND no successor in filing AND no same-role
    posting in the 30d CDX window -> exclude 90TD. Missing ATS coverage
    (no board mapping / no in-window snapshot) yields decision="no_data"
    (no exclude) — we never exclude on absent evidence.

Security master leg: company_tickers_exchange.json hook excludes warrants /
preferred / units / rights (ticker-suffix filter, same convention as
ox_lab.build_cases) and OTC / non-listed exchange tiers.

Outputs (new run dir only; never overwrites lab_runs/* or research/*):
  <run_dir>/stewardship_gap_events.jsonl   one row per deduped filing event
  <run_dir>/stewardship_gap_placebo.jsonl  same-ticker -180d placebo rows

Usage:
  python3 src/stewardship_gap.py pull   --run-dir lab_runs/stewardship_gap_mvp
  python3 src/stewardship_gap.py run    --run-dir lab_runs/stewardship_gap_mvp
"""

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab  # noqa: E402  (CachedHTTP, submission_rows, clean_document, load_jsonl, append_jsonl)

RUN_DEFAULT = ROOT / "lab_runs" / "stewardship_gap_mvp"

EXCLUDE_HORIZON_TD = 90
PLACEBO_SHIFT_DAYS = 180
ATS_WINDOW_DAYS = 30
# 90 trading days ~= 90 * 365.25/252 calendar days; stored as an APPROXIMATE
# calendar end for display — consumers holding exactly 90 sessions should
# count trading days off exclude_start instead.
CAL_DAYS_PER_TD = 365.25 / 252.0

EFTS_SEARCH = "https://efts.sec.gov/LATEST/search-index"
UA = {"User-Agent": "alphaHunt research contact@example.com"}

# ── Security master ──────────────────────────────────────────────────────────
# Same non-common suffix convention as ox_lab.build_cases (so BRK-B survives).
NON_COMMON_SUFFIX_RE = re.compile(r"-(?:P[A-Z]?|WT|UN|RI)$", re.I)
# company_tickers_exchange.json `exchange` values to reject (case-insensitive,
# prefix match covers OTCQB/OTCQX/OTCBB/Pink variants as published).
OTC_PREFIXES = ("otc", "pink", "grey", "gray", "expert market")
ALLOWED_EXCHANGES = {"nasdaq", "nyse", "nyse american", "amex", "cboe", "bats", "iex"}
SECURITY_MASTER_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


def load_security_master(http=None, path=None):
    """Return {TICKER: {"exchange": str, "cik": str, "title": str}}.

    Prefers a local JSON copy when `path` is given (offline/test use);
    otherwise fetches company_tickers_exchange.json (cached when an
    ox_lab.CachedHTTP is passed).
    """
    if path is not None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    elif http is not None:
        data = http.json(SECURITY_MASTER_URL)
    else:
        import urllib.request
        req = urllib.request.Request(SECURITY_MASTER_URL, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    master = {}
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        rows = data["data"]  # company_tickers_exchange.json envelope
    else:
        rows = data.values() if isinstance(data, dict) else data
    for rec in rows:
        if isinstance(rec, (list, tuple)):
            # company_tickers_exchange.json: [cik, title, ticker, exchange]
            cik, title, t, exch = (list(rec) + ["", "", "", ""])[:4]
            t = (t or "").upper()
            if not t:
                continue
            master[t] = {"exchange": exch or "", "cik": str(cik).zfill(10),
                         "title": title or ""}
            continue
        t = (rec.get("ticker") or "").upper()
        if not t:
            continue
        master[t] = {
            "exchange": rec.get("exchange") or "",
            "cik": str(rec.get("cik_str") or rec.get("cik") or "").zfill(10),
            "title": rec.get("title") or rec.get("name") or "",
        }
    return master


def is_excluded_security(ticker, master=None):
    """Return (excluded: bool, reason: str). Unknown tickers are NOT excluded
    (fail-open with a review flag — an exclude list must not silently drop
    names the master never covered)."""
    t = (ticker or "").upper()
    if NON_COMMON_SUFFIX_RE.search(t):
        return True, "non_common_suffix_warrant_preferred_unit_right"
    if master is not None:
        # NASDAQ 5th-character convention: a 5-char symbol ending W/U/R whose
        # 4-char root is itself listed (KITTW/KITT, BENFW/BENF) is the root's
        # warrant/unit/right. Requires the root in-master to avoid hitting
        # 5-char commons (GOOGL ends in L and is untouched regardless).
        if len(t) == 5 and t[4] in ("W", "U", "R") and t[:4] in master:
            return True, "probable_warrant_unit_right_by_root"
        info = master.get(t)
        if info is None:
            return False, "no_master_entry_needs_review"
        exch = (info.get("exchange") or "").strip().lower()
        if not exch:
            return False, "blank_exchange_needs_review"
        if exch.startswith(OTC_PREFIXES):
            return True, f"otc_tier:{info.get('exchange')}"
        if exch not in ALLOWED_EXCHANGES:
            return True, f"non_listed_exchange:{info.get('exchange')}"
        return False, "listed_common"
    return False, "no_master_checked"


# ── Regex classifier (no LLM) ────────────────────────────────────────────────
ROLE_PATTERNS = [
    ("CEO", re.compile(r"\bchief\s+executive\s+officer\b|\bCEO\b|\bprincipal\s+executive\s+officer\b", re.I)),
    ("CFO", re.compile(r"\bchief\s+financial\s+officer\b|\bCFO\b|\bprincipal\s+financial\s+officer\b", re.I)),
    ("COO", re.compile(r"\bchief\s+operating\s+officer\b|\bCOO\b", re.I)),
    ("CTO", re.compile(r"\bchief\s+technolog\w*\s+officer\b|\bCTO\b", re.I)),
    ("GC", re.compile(r"\bgeneral\s+counsel\b|\bchief\s+legal\s+officer\b", re.I)),
    ("CAO", re.compile(r"\bchief\s+accounting\s+officer\b|\bprincipal\s+accounting\s+officer\b|\bcontroller\b", re.I)),
]

# Strong positives name/place a specific person in the role. A bare "successor"
# alone is weak evidence (often "search for a successor" = nobody named yet).
SUCCESSOR_STRONG_RES = [
    re.compile(r"\bsucceed(?:s|ed|ing)?\b.{0,60}?\b(?:as|him|her|them)\b", re.I),
    re.compile(r"\bappoint(?:s|ed|ment|ing)?\b.{0,100}?\bas\b", re.I),
    re.compile(r"\bnamed\b.{0,100}?\bas\b", re.I),
    re.compile(r"\belect(?:s|ed|ing|ion)?\b.{0,100}?\bas\b", re.I),
    re.compile(r"\bwill\s+(?:assume|serve\s+as|act\s+as|succeed)\b", re.I),
]
SUCCESSOR_BARE_RE = re.compile(r"\bsuccessor\b", re.I)
# Negated-successor language must win over positive patterns above.
SUCCESSOR_NEGATIVE_RES = [
    re.compile(r"\bno\s+successor\b", re.I),
    re.compile(r"\bsuccessor\s+has\s+not\s+been\b", re.I),
    re.compile(r"\bhas\s+not\s+(?:yet\s+)?named\s+(?:a\s+)?successor\b", re.I),
    re.compile(r"\bwithout\s+(?:naming\s+)?a\s+successor\b", re.I),
    re.compile(r"\bno\s+replacement\s+(?:has\s+been\s+)?(?:named|appointed)\b", re.I),
    re.compile(r"\bsearch(?:es|ing)?\s+for\s+(?:a\s+)?(?:successor|replacement)\b", re.I),
]

INTERIM_RE = re.compile(r"\binterim\b|\bacting\b.{0,30}?\b(?:chief|officer|ceo|cfo|president)\b", re.I)
DISAGREEMENT_RE = re.compile(r"\bdisagreements?\b|\bdisagreed?\b", re.I)
# Boilerplate negation ("there were no disagreements ...") must win.
DISAGREEMENT_NEGATIVE_RES = [
    re.compile(r"\bno\s+disagreements?\b", re.I),
    re.compile(r"\bwithout\s+(?:any\s+)?disagreements?\b", re.I),
    re.compile(r"\bnot\s+(?:due\s+to|the\s+result\s+of|related\s+to|because\s+of)\s+(?:any\s+)?disagreements?\b", re.I),
]
IMMEDIATE_RES = [
    re.compile(r"effective\s+immediately", re.I),
    re.compile(r"with\s+immediate\s+effect", re.I),
    re.compile(r"effective\s+as\s+of\s+(?:the\s+date\s+hereof|today)", re.I),
]
EFFECTIVE_DATE_RE = re.compile(
    r"effective\s+(?:as\s+of\s+)?(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})", re.I)
DEPARTURE_TYPE_RES = [
    ("death", re.compile(r"\bpassed\s+away\b|\bdeceased\b|\bdeath\s+of\b", re.I)),
    ("stepping_down", re.compile(r"\bstep(?:ped|ping)?\s+down\b", re.I)),
    ("retirement", re.compile(r"\bretir(?:e|ed|ement|ing|es)\b", re.I)),
    ("termination", re.compile(r"\bterminat(?:ed|ion|es)\b|\bremoved\b.{0,30}?\b(?:as|from)\b|\bfired\b", re.I)),
    ("resignation", re.compile(r"\bresign(?:ed|ation|ing|s)?\b", re.I)),
]

DEPARTURE_VERB_RES = [
    re.compile(r"\bresign\w*\b", re.I),
    re.compile(r"\bretir\w*\b", re.I),
    re.compile(r"\bterminat\w*\b", re.I),
    re.compile(r"\bstep(?:ped|ping)?\s+down\b", re.I),
    re.compile(r"\bpass(?:ed)?\s+away\b|\bdeceased\b|\bdeath\s+of\b", re.I),
    re.compile(r"\bdepart(?:ure|ed|s|ing)?\b", re.I),
    re.compile(r"\bseparat(?:ion|ed|es|ing)?\b", re.I),
    re.compile(r"\bremov(?:ed|al)?\b", re.I),
    re.compile(r"\bdismiss\w*\b|\bfired\b", re.I),
    re.compile(r"\bnot\s+stand\s+for\s+re-?election\b|\bwill\s+not\s+continue\s+as\b", re.I),
]
# Max chars between an officer-role mention and a departure verb for the
# filing to count as a departure event (kills comp-agreement "terminate" FPs
# while keeping "Smith resigned. He was CFO." adjacent-sentence cases).
DEPARTURE_PROXIMITY_CHARS = 300


def has_departure_language(text):
    """True iff a departure verb occurs near an officer-role mention."""
    text = text or ""
    role_spans = [m.span() for _, rx in ROLE_PATTERNS for m in rx.finditer(text)]
    if not role_spans:
        return False
    verb_spans = [m.span() for rx in DEPARTURE_VERB_RES for m in rx.finditer(text)]
    for vs, ve in verb_spans:
        for rs, re_ in role_spans:
            if min(abs(vs - re_), abs(rs - ve)) <= DEPARTURE_PROXIMITY_CHARS:
                return True
    return False


TRIGGER_ROLES = ("CEO", "CFO")
ROLE_PRIORITY = {"CEO": 0, "CFO": 1, "COO": 2, "CTO": 3, "GC": 4, "CAO": 5}


def _first_sentence_with(text, pattern, max_len=150):
    for m in pattern.finditer(text):
        start = text.rfind(".", 0, m.start()) + 1
        end = text.find(".", m.end())
        end = len(text) if end < 0 else end
        sent = re.sub(r"\s+", " ", text[start:end]).strip()
        if sent:
            return sent[:max_len]
    return ""


def classify_departure(text, filed_date=None):
    """Deterministic regex classification of an Item 5.02 excerpt.

    Returns a dict aligned to DEPARTURE_SCHEMA field names (officer_role,
    departure_type, is_unexpected, successor_named, has_disagreement_flag,
    transition_period_days, stated_reason) plus MVP extras (interim,
    all_roles, effective_immediate). filed_date "YYYY-MM-DD" enables
    transition_period_days from an explicit effective date.
    """
    text = text or ""
    roles = [role for role, rx in ROLE_PATTERNS if rx.search(text)]
    role = roles[0] if roles else "unknown"

    dtype = "ambiguous"
    for name, rx in DEPARTURE_TYPE_RES:
        if rx.search(text):
            dtype = name
            break

    has_disagreement = bool(DISAGREEMENT_RE.search(text))
    if any(rx.search(text) for rx in DISAGREEMENT_NEGATIVE_RES):
        has_disagreement = False
    interim = bool(INTERIM_RE.search(text))
    immediate = any(rx.search(text) for rx in IMMEDIATE_RES)

    negated = any(rx.search(text) for rx in SUCCESSOR_NEGATIVE_RES)
    successor_named = (any(rx.search(text) for rx in SUCCESSOR_STRONG_RES)
                       or (bool(SUCCESSOR_BARE_RE.search(text)) and not negated))

    transition_days = None
    if immediate:
        transition_days = 0
    else:
        m = EFFECTIVE_DATE_RE.search(text)
        if m and filed_date:
            try:
                eff = dt.date(int(m.group(3)),
                              _month_num(m.group(1)), int(m.group(2)))
                filed = dt.date.fromisoformat(str(filed_date)[:10])
                transition_days = max(0, (eff - filed).days)
            except (ValueError, TypeError):
                transition_days = None

    is_unexpected = bool(immediate or has_disagreement)

    reason = ""
    for _, rx in DEPARTURE_TYPE_RES:
        reason = _first_sentence_with(text, rx)
        if reason:
            break

    return {
        "officer_role": role,
        "all_roles": roles,
        "departure_type": dtype,
        "has_departure_language": has_departure_language(text),
        "is_unexpected": is_unexpected,
        "successor_named": successor_named,
        "has_disagreement_flag": has_disagreement,
        "interim_appointed": interim,
        "effective_immediate": immediate,
        "transition_period_days": transition_days,
        "stated_reason": reason,
    }


def _month_num(name):
    return {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
            "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
            "november": 11, "december": 12}[name.lower()]


def classify_with_llm_overlay(text, filed_date=None, client=None):
    """OPTIONAL LLM overlay: returns (regex_result, llm_result_or_None).

    Keeps DEPARTURE_SCHEMA as an optional second opinion; the MVP rule path
    uses only the regex result. Requires an OpenRouter client (see
    subagents.OpenRouter) and network — never called by default.
    """
    from unstructured_proto import (  # lazy: no hard LLM dependency for MVP
        DEPARTURE_SCHEMA, DEPARTURE_SYSTEM,
    )
    regex_result = classify_departure(text, filed_date)
    if client is None:
        return regex_result, None
    tool = {"type": "function", "function": {
        "name": "departure_classify", "parameters": DEPARTURE_SCHEMA, "strict": True,
    }}
    raw = client.chat(
        [{"role": "system", "content": DEPARTURE_SYSTEM},
         {"role": "user", "content": (text or "")[:3000]}],
        temperature=0.05, max_tokens=1000,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "departure_classify"}},
    )
    return regex_result, json.loads(raw)


# ── EFTS Item 5.02 pull ──────────────────────────────────────────────────────
def efts_search_502(startdt, enddt, http_get, page_size=100):
    """EFTS full-text search for 8-K Item 5.02 filings in [startdt, enddt].

    http_get(url) -> parsed JSON (injectable for tests). Uses the `forms`
    filter (8-K,8-K/A) alongside the quoted-phrase query. Single page of
    `page_size` hits; callers page by month (same pattern as
    unstructured_proto.pull_departures).
    """
    params = urllib.parse.urlencode({
        "q": '"Item 5.02"',
        "forms": "8-K,8-K/A",
        "dateRange": "custom",
        "startdt": startdt,
        "enddt": enddt,
        "pageSize": page_size,
    })
    data = http_get(f"{EFTS_SEARCH}?{params}")
    return data.get("hits", {}).get("hits", [])


def items_filter_502(rows):
    """Submissions-API items filter: keep rows whose `items` field names 5.02.

    `items` arrives as e.g. "1.01,5.02,9.01" on 8-K rows (see
    ox_lab.column_rows optional fields). Pure function — unit-testable.
    """
    out = []
    for r in rows:
        items = r.get("items") or ""
        if re.search(r"(?:^|[,;\s])5\.02(?:[,;\s]|$)", str(items)):
            out.append(r)
    return out


def filing_text_502(row, http, max_chars=6000):
    """Fetch a filing's primary document and return the Item 5.02 excerpt."""
    from ox_lab import accession_files  # local import: keeps module import light
    primary = row.get("primaryDocument", "")
    if not primary:
        # EFTS hits carry no primary-document name; resolve via submissions.
        try:
            _, sub_rows = ox_lab.submission_rows(str(row["cik"]).zfill(10), http)
            want = str(row["accession"]).replace("-", "")
            for sr in sub_rows:
                if str(sr.get("accessionNumber", "")).replace("-", "") == want:
                    primary = sr.get("primaryDocument", "")
                    break
        except Exception:
            primary = ""
    urls = accession_files(
        {"cik": str(int(str(row["cik"]))), "accessionNumber": row["accession"],
         "primaryDocument": primary},
        http, include_exhibits=False)
    if not urls:
        return ""
    raw = http.get(urls[0][1], timeout=30)
    text = ox_lab.clean_document(raw)
    lower = text.lower()
    idx = lower.find("item 5.02")
    if idx < 0:
        idx = lower.find("departure of directors")
    if idx < 0:
        return text[:max_chars]
    return text[max(0, idx - 200):idx + max_chars]


def pull_502_events(universe_ciks, start_months, run_dir, http=None,
                    efts_get=None, max_filings=300):
    """Pull candidate 5.02 events. Writes departures_502_raw.jsonl in run_dir.

    universe_ciks: {cik10: {"ticker":, "company":}} restrict-to-universe map
      (same shape as form4_history.load_universe output).
    start_months: ["2025-01", ...] month buckets paged through EFTS.
    """
    run_dir = Path(run_dir)
    http = http or ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    efts_get = efts_get or http.json
    out_path = run_dir / "departures_502_raw.jsonl"
    seen = set()
    if out_path.exists():
        for r in ox_lab.load_jsonl(out_path):
            seen.add(r.get("accession", ""))

    collected = 0
    for ym in start_months:
        if collected >= max_filings:
            break
        year, mon = ym.split("-")
        last_day = "28"  # EFTS custom range; safe for every month incl. Feb
        try:
            hits = efts_search_502(f"{ym}-01", f"{year}-{mon}-{last_day}", efts_get)
        except Exception:
            continue
        for hit in hits:
            if collected >= max_filings:
                break
            src = hit.get("_source", {})
            accession = src.get("adsh", "")
            if not accession or accession in seen:
                continue
            for raw_cik in (src.get("ciks") or [str(src.get("cik", ""))]):
                cik = str(raw_cik).zfill(10)
                info = universe_ciks.get(cik)
                if not info:
                    continue
                rec = {"cik": cik, "ticker": info["ticker"],
                       "company": info.get("company", ""),
                       "accession": accession,
                       "filed_date": src.get("file_date", ""),
                       "form": src.get("form", "8-K")}
                try:
                    rec["filing_excerpt"] = filing_text_502(
                        {"cik": cik, "accession": accession,
                         "primaryDocument": ""}, http)
                except Exception:
                    continue
                if not rec["filing_excerpt"]:
                    continue
                ox_lab.append_jsonl(out_path, rec)
                seen.add(accession)
                collected += 1
                break
    return out_path


# ── Dedupe: multi-officer filings → one event ────────────────────────────────
def dedupe_events(classified_rows):
    """Collapse per-officer rows from one filing into a single event.

    Input rows: {cik, accession, ticker, company, filed_date, form,
                 filing_excerpt, + classify_departure() fields}.
    One event per (cik, accession): roles merged, primary_role = highest
    priority CEO > CFO > rest, successor_named = any-officer True ONLY when
    every trigger-role departure names a successor (per-role flags kept in
    `departures` for the rule leg).
    """
    grouped = {}
    for r in classified_rows:
        key = (r.get("cik", ""), r.get("accession", ""))
        grouped.setdefault(key, []).append(r)
    events = []
    for (cik, accession), rows in grouped.items():
        rows = sorted(rows, key=lambda r: ROLE_PRIORITY.get(r.get("officer_role"), 99))
        # Rule triggers need departure language: appointment/comp-only filings
        # (no verb near a role mention) can never trigger, even when the
        # appointment regexes incidentally fire "successor" patterns.
        departed = [r for r in rows if r.get("has_departure_language")]
        trigger = [r for r in departed if r.get("officer_role") in TRIGGER_ROLES]
        if trigger:
            no_successor_roles = [r["officer_role"] for r in trigger
                                  if not r.get("successor_named")]
            successor_ok = not no_successor_roles
        else:
            no_successor_roles = []
            successor_ok = True
        base = rows[0]
        events.append({
            "event_id": f"{cik}_{accession}".replace("-", ""),
            "cik": cik, "accession": accession,
            "ticker": base.get("ticker", ""), "company": base.get("company", ""),
            "filed_date": base.get("filed_date", ""), "form": base.get("form", "8-K"),
            "primary_role": rows[0].get("officer_role", "unknown"),
            "roles": sorted({r.get("officer_role", "unknown") for r in rows}),
            "has_departure_language": bool(departed),
            "successor_named_all_triggers": successor_ok,
            "trigger_roles_without_successor": no_successor_roles,
            "has_disagreement_flag": any(r.get("has_disagreement_flag") for r in rows),
            "is_unexpected": any(r.get("is_unexpected") for r in rows),
            "departures": [{k: r.get(k) for k in (
                "officer_role", "departure_type", "has_departure_language",
                "is_unexpected",
                "successor_named", "has_disagreement_flag",
                "interim_appointed", "effective_immediate",
                "transition_period_days", "stated_reason")} for r in rows],
        })
    return events


# ── ATS hook ─────────────────────────────────────────────────────────────────
# Same-role keyword map for posting-title matching (tight on purpose: a
# "VP Finance" post is NOT a CFO succession signal).
ROLE_POSTING_KEYWORDS = {
    "CEO": [r"\bchief\s+executive\s+officer\b", r"\bchief\s+executive\b",
            r"(?<![a-z])ceo(?![a-z])"],
    "CFO": [r"\bchief\s+financial\s+officer\b", r"\bchief\s+finance\s+officer\b",
            r"\bfinance\s+chief\b", r"(?<![a-z])cfo(?![a-z])"],
}
ATS_COMPILED = {role: [re.compile(p, re.I) for p in pats]
                for role, pats in ROLE_POSTING_KEYWORDS.items()}

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{token}"
CDX_API = "https://web.archive.org/cdx/search/cdx"


def load_board_map(path):
    """Load ticker → ATS board config. See config/stewardship_gap_boards.example.json.

    {TICKER: {"vendor": "greenhouse"|"ashby", "board_token": str,
              "careers_url": str}} — careers_url is the human careers page
    archived by the Wayback Machine (the CDX leg reads career-HTML only).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(k).upper(): v for k, v in data.items()}


def fetch_live_postings(vendor, board_token, http_json):
    """Current-state ATS pull (FORWARD-ONLY, NON-PIT). Returns [titles].

    Greenhouse boards-api and Ashby posting-api expose only LIVE postings —
    there is no historical endpoint, so this MUST NOT backfill.
    """
    vendor = (vendor or "").lower()
    if vendor == "greenhouse":
        url = GREENHOUSE_API.format(token=board_token)
    elif vendor == "ashby":
        url = ASHBY_API.format(token=board_token)
    else:
        raise ValueError(f"unknown ATS vendor: {vendor!r}")
    data = http_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [str(j.get("title") or j.get("job_title") or "") for j in jobs
            if isinstance(j, dict)]


def extract_titles_from_careers_html(html_text):
    """Candidate job titles from archived careers-page HTML: anchor + heading texts."""
    texts = re.findall(r"<a\b[^>]*>(.*?)</a>", html_text or "", re.S | re.I)
    texts += re.findall(r"<h[1-4]\b[^>]*>(.*?)</h[1-4]>", html_text or "", re.S | re.I)
    out = []
    for t in texts:
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if 3 <= len(t) <= 120:
            out.append(t)
    return out


def title_matches_role(title, role):
    return any(rx.search(title or "") for rx in ATS_COMPILED.get(role, []))


def cdx_careers_snapshots(careers_url, window_end, http_get):
    """CDX lookup of archived careers-HTML snapshots with ts <= window_end.

    window_end: dt.date/datetime (event_date + 30d). Returns [(ts_str, url)]
    sorted ascending. Career-HTML only (mimetype + statuscode filters);
    never the boards JSON API.
    """
    to = window_end.strftime("%Y%m%d%H%M%S") if hasattr(window_end, "strftime") else str(window_end)
    params = urllib.parse.urlencode({
        "url": careers_url, "output": "json",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "digest", "to": to,
    })
    data = http_get(f"{CDX_API}?{params}")
    rows = data if isinstance(data, list) else []
    snaps = []
    for r in rows[1:] if rows and rows[0][0] == "urlkey" else rows:
        try:
            ts = str(r[1])
        except (IndexError, TypeError):
            continue
        if ts > to:
            continue  # client-side PIT bound: never trust param handling upstream
        try:
            snaps.append((ts, r[2]))  # (timestamp, original)
        except (IndexError, TypeError):
            continue
    return sorted(snaps)


def has_posting_in_window(board_cfg, role, event_date, window_days=ATS_WINDOW_DAYS,
                          cdx_get=None, snapshot_get=None, live_json=None):
    """Same-role posting check in (event_date, event_date + window_days].

    PIT path (preferred): latest CDX careers-HTML snapshot with
      timestamp <= event_date + window_days -> provenance "cdx_pit".
    Live path (fallback/monitor only): current boards-api state ->
      provenance "live_non_pit" (forward-only; NOT point-in-time).
    Returns {"found": bool|None, "provenance": str, "evidence": {...}}.
    found=None when no in-window snapshot / no board mapping exists.
    """
    event_date = (dt.date.fromisoformat(str(event_date)[:10])
                  if not isinstance(event_date, dt.date) else event_date)
    window_end = event_date + dt.timedelta(days=window_days)
    evidence = {"window_end": window_end.isoformat(), "titles_checked": []}

    if board_cfg and board_cfg.get("careers_url") and cdx_get and snapshot_get:
        try:
            snaps = cdx_careers_snapshots(board_cfg["careers_url"], window_end, cdx_get)
        except Exception as exc:
            return {"found": None, "provenance": "cdx_pit",
                    "evidence": {**evidence, "error": f"cdx_lookup_failed:{exc}"}}
        if snaps:
            ts, original = snaps[-1]  # latest snapshot inside the PIT window
            snap_url = f"https://web.archive.org/web/{ts}id_/{original}"
            try:
                html_text = snapshot_get(snap_url)
                if isinstance(html_text, bytes):
                    html_text = html_text.decode("utf-8", errors="replace")
            except Exception as exc:
                return {"found": None, "provenance": "cdx_pit",
                        "evidence": {**evidence, "snapshot_ts": ts,
                                     "error": f"snapshot_fetch_failed:{exc}"}}
            titles = extract_titles_from_careers_html(html_text)
            hits = [t for t in titles if title_matches_role(t, role)]
            return {"found": bool(hits), "provenance": "cdx_pit",
                    "evidence": {**evidence, "snapshot_ts": ts,
                                 "snapshot_url": snap_url,
                                 "titles_checked": titles[:50],
                                 "matching_titles": hits[:10]}}
        evidence["cdx_snapshots_in_window"] = 0

    if board_cfg and board_cfg.get("board_token") and live_json:
        try:
            titles = fetch_live_postings(board_cfg.get("vendor", ""),
                                         board_cfg["board_token"], live_json)
        except Exception as exc:
            return {"found": None, "provenance": "live_non_pit",
                    "evidence": {**evidence, "error": f"live_fetch_failed:{exc}"}}
        hits = [t for t in titles if title_matches_role(t, role)]
        return {"found": bool(hits), "provenance": "live_non_pit",
                "evidence": {**evidence, "titles_checked": titles[:50],
                             "matching_titles": hits[:10],
                             "warning": "forward-only current state; NOT point-in-time"}}

    return {"found": None, "provenance": "no_coverage",
            "evidence": {**evidence, "reason": "no_board_mapping_or_no_snapshot"}}


# ── Rule + outputs ───────────────────────────────────────────────────────────
def apply_rule(event, ats_by_role):
    """Exclude rule. ats_by_role: {role: has_posting_in_window() result}.

    Exclude 90TD iff for some trigger role: departure present AND no
    successor in filing AND CDX-PIT says no same-role posting in 30d.
    Live-only (non-PIT) or missing ATS evidence -> decision "no_data",
    never an exclude.
    """
    filed = event.get("filed_date", "")[:10]
    try:
        start = dt.date.fromisoformat(filed) if filed else None
    except ValueError:
        start = None
    cal_days = round(EXCLUDE_HORIZON_TD * CAL_DAYS_PER_TD)
    end_cal = (start + dt.timedelta(days=cal_days)).isoformat() if start else None

    base = {"event_id": event.get("event_id", ""), "ticker": event.get("ticker", ""),
            "cik": event.get("cik", ""), "filed_date": filed,
            "exclude_start": filed, "horizon_td": EXCLUDE_HORIZON_TD,
            "exclude_end_cal_approx": end_cal,
            "cal_days_note": f"{cal_days} calendar days ~= 90 trading days"}

    triggers = event.get("trigger_roles_without_successor", [])
    if not event.get("has_departure_language", True):
        return {**base, "decision": "no_exclude",
                "reason": "appointment_or_comp_only_no_departure_language"}
    if not triggers:
        return {**base, "decision": "no_exclude",
                "reason": "no_trigger_role_without_successor"}
    cdx_roles_no_posting = [r for r in triggers
                            if (ats_by_role.get(r) or {}).get("found") is False
                            and (ats_by_role.get(r) or {}).get("provenance") == "cdx_pit"]
    if cdx_roles_no_posting:
        return {**base, "decision": "exclude",
                "reason": "trigger_departure_no_successor_no_cdx_posting_30d",
                "trigger_roles": cdx_roles_no_posting,
                "ats_provenance": "cdx_pit"}
    live_hits = {r: (ats_by_role.get(r) or {}).get("provenance")
                 for r in triggers if ats_by_role.get(r)}
    if any((ats_by_role.get(r) or {}).get("found") for r in triggers):
        return {**base, "decision": "no_exclude",
                "reason": "same_role_posted_in_window",
                "ats_provenance": (ats_by_role.get(triggers[0]) or {}).get("provenance")}
    return {**base, "decision": "no_data",
            "reason": "insufficient_pit_ats_coverage",
            "trigger_roles": triggers, "ats_provenance_detail": live_hits}


def make_placebo(decision_row, shift_days=PLACEBO_SHIFT_DAYS):
    """Same-ticker placebo: identical row shifted `shift_days` earlier.

    Used to test whether the exclude window itself (vs the event) drives any
    measured effect. Placebo rows are marked and MUST NOT be traded.
    """
    row = dict(decision_row)
    try:
        start = dt.date.fromisoformat(row["exclude_start"])
        cal_days = round(row.get("horizon_td", EXCLUDE_HORIZON_TD) * CAL_DAYS_PER_TD)
        p_start = start - dt.timedelta(days=shift_days)
        row["exclude_start"] = p_start.isoformat()
        row["exclude_end_cal_approx"] = (p_start + dt.timedelta(days=cal_days)).isoformat()
    except (ValueError, TypeError, KeyError):
        pass
    row["is_placebo"] = True
    row["placebo_shift_days"] = -shift_days
    row["decision"] = "placebo_" + str(row.get("decision", ""))
    return row


def run_pipeline(raw_rows, board_map=None, master=None,
                 cdx_get=None, snapshot_get=None, live_json=None):
    """Pure-ish pipeline: classify -> dedupe -> security-master -> ATS -> rule.

    raw_rows: [{cik, accession, ticker, company, filed_date, form,
                filing_excerpt}]. fetchers injectable (None = skip that leg,
    yielding no_data ATS decisions).
    Returns (decisions, placebo_rows, stats).
    """
    stats = {"raw": len(raw_rows), "classified": 0, "events": 0,
             "excluded_security": 0, "exclude": 0, "no_exclude": 0, "no_data": 0}
    per_officer = []
    for r in raw_rows:
        c = classify_departure(r.get("filing_excerpt", ""), r.get("filed_date"))
        # One row per detected role (multi-officer filings expand here and
        # collapse in dedupe_events). Unknown role -> single "unknown" row.
        for role in (c["all_roles"] or ["unknown"]):
            row = {**r, **c, "officer_role": role}
            per_officer.append(row)
    stats["classified"] = len(per_officer)
    events = dedupe_events(per_officer)
    stats["events"] = len(events)

    decisions, placebos = [], []
    for ev in events:
        excl, reason = is_excluded_security(ev.get("ticker", ""), master)
        if excl:
            stats["excluded_security"] += 1
            continue
        ats_by_role = {}
        for role in ev.get("trigger_roles_without_successor", []):
            ats_by_role[role] = has_posting_in_window(
                (board_map or {}).get((ev.get("ticker") or "").upper()),
                role, ev.get("filed_date") or "1970-01-01",
                cdx_get=cdx_get, snapshot_get=snapshot_get, live_json=live_json)
        dec = apply_rule(ev, ats_by_role)
        dec["ats"] = ats_by_role
        dec["security_master"] = reason
        decisions.append(dec)
        stats[dec["decision"]] = stats.get(dec["decision"], 0) + 1
        placebos.append(make_placebo(dec))
    return decisions, placebos, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["pull", "run"])
    ap.add_argument("--run-dir", type=Path, default=RUN_DEFAULT)
    ap.add_argument("--boards", type=Path, default=None,
                    help="ticker->board-token map (see config/stewardship_gap_boards.example.json)")
    ap.add_argument("--master", type=Path, default=None,
                    help="local company_tickers_exchange.json copy (else fetched+cached)")
    ap.add_argument("--months", nargs="*", default=[],
                    help='EFTS month buckets, e.g. --months 2025-01 2025-02 (default: none, classify local raw file)')
    ap.add_argument("--max-filings", type=int, default=300)
    ap.add_argument("--no-ats-live", action="store_true",
                    help="disable the non-PIT live ATS path (CDX-PIT only)")
    args = ap.parse_args(argv)

    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)

    if args.stage in ("pull", "run"):
        raw_path = run_dir / "departures_502_raw.jsonl"
        if args.months:
            from form4_history import load_universe
            universe = load_universe()
            pull_502_events(universe, args.months, run_dir, http=http,
                            max_filings=args.max_filings)
        elif not raw_path.exists():
            print("no --months given and no departures_502_raw.jsonl; nothing to do")
            return 1

    if args.stage == "run":
        raw_rows = ox_lab.load_jsonl(run_dir / "departures_502_raw.jsonl")
        board_map = load_board_map(args.boards) if args.boards else {}
        if args.master:
            master = load_security_master(path=args.master)
        else:
            try:
                master = load_security_master(http=http)
            except Exception as exc:
                print(f"security master fetch failed ({exc}); continuing without master")
                master = None
        live_json = None if args.no_ats_live else http.json
        decisions, placebos, stats = run_pipeline(
            raw_rows, board_map=board_map, master=master,
            cdx_get=http.json, snapshot_get=http.get, live_json=live_json)
        out = run_dir / "stewardship_gap_events.jsonl"
        if out.exists():
            out.unlink()
        for d in decisions:
            ox_lab.append_jsonl(out, d)
        pout = run_dir / "stewardship_gap_placebo.jsonl"
        if pout.exists():
            pout.unlink()
        for p in placebos:
            ox_lab.append_jsonl(pout, p)
        print(json.dumps(stats, indent=2))
        print(f"events -> {out}\nplacebo -> {pout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
