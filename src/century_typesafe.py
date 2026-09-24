#!/usr/bin/env python3
"""Re-run the century P(+20%) backtest with TypeSafe (Jev) evaluating the filings.

The original century run used Ox for five extraction lenses and two syntheses.
Here two TypeSafe (Jev) passes replace it: an anonymizer judges which
code-proposed names/phrases would identify the issuer (code redacts them), then an
analyzer reads five role-focused excerpts of the redacted filing, each asked the
same outcome judgments plus role-specific judgments. Code averages the answers, applies the locked causal top-decile rule,
and reuses the frozen century evaluator (execution, cohorts, blocked placebos).

Stages (all resumable): recover -> packs -> anonymize -> score -> evaluate.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures as cf
import os
import re
import datetime as dt
import hashlib
import json
import math
import statistics
import sys
import threading
import time
import http.client as http_client
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox
import sealed_safety as ss
import temporal_store as ts
from comprehensive_lab import ROLE_NAMES, role_excerpt


ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "lab_runs" / "century_typesafe"
CENTURY_DIR = ROOT / "lab_runs" / "century_safety"
SOURCE_EXPERIMENT = "sealed_safety_2009_2025_v1"
VM_SOURCE_DIR = "/opt/alphahunt/lab_runs/century_safety_source"
FULL_INDEX = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
PROMPT_VERSION = "typesafe_century_v2"
KEY_FILE = Path.home() / ".config" / "typesafe" / "api_key"
REDACT_THRESHOLD = 0.5
DICTIONARY_WORD_THRESHOLD = 0.7
PROPAGATE_THRESHOLD = 0.3
CANDIDATES_PER_REQUEST = 60
MAX_CANDIDATES = 240
WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------- recover ---

def comprehensive_id(cik: str, accession: str) -> tuple[str, str]:
    source_id = hashlib.sha256(f"{SOURCE_EXPERIMENT}|{cik}|{accession}".encode()).hexdigest()[:20]
    return source_id, ts.digest("comprehensive_long_v1", VM_SOURCE_DIR, source_id)[:24]


def fetch_index(http: ox.CachedHTTP, url: str, attempts: int = 6) -> bytes:
    # CachedHTTP retries HTTP/URL errors but not truncated chunked bodies.
    for attempt in range(attempts):
        try:
            return http.get(url, timeout=180)
        except http_client.HTTPException:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def recover(run_dir: Path) -> dict:
    """Map every archived century case id back to its SEC (cik, accession)."""
    output = run_dir / "recovered.jsonl"
    wrappers = json.loads((CENTURY_DIR / "wrapper_index.json").read_text())
    done = {row["comprehensive_case_id"] for row in ox.load_jsonl(output)}
    by_day = defaultdict(set)
    for case_id, row in wrappers.items():
        if case_id not in done:
            by_day[row["cutoff"]].add(case_id)
    http = ox.CachedHTTP(run_dir / "cache" / "sec_index", min_interval=0.13)
    quarters = sorted({(int(day[:4]), (int(day[5:7]) - 1) // 3 + 1) for day in by_day})
    for year, quarter in quarters:
        raw = fetch_index(http, FULL_INDEX.format(year=year, quarter=quarter))
        for line in raw.decode("latin1").splitlines():
            form = line[:12].strip()
            if form not in ss.FORMS or not line.rstrip().endswith(".txt"):
                continue
            parts = line.split()
            filename, day = parts[-1], parts[-2]
            wanted = by_day.get(day)
            if not wanted:
                continue
            cik = str(int(parts[-3]))
            accession = filename.rsplit("/", 1)[-1][:-4]
            source_id, case_id = comprehensive_id(cik, accession)
            if case_id in wanted:
                wanted.discard(case_id)
                wrapper = wrappers[case_id]
                ox.append_jsonl(output, {
                    "comprehensive_case_id": case_id, "case_id": source_id, "cik": cik,
                    "accession": accession, "form": form,
                    "company": line[12:74].strip(), "ticker": wrapper["ticker"],
                    "cutoff": wrapper["cutoff"], "accepted": wrapper["accepted"],
                    "drawdown": wrapper["dd"]})
        print(f"recovered through {year}Q{quarter}", file=sys.stderr)
    missing = sorted(case for cases in by_day.values() for case in cases)
    result = {"cases": len(wrappers), "recovered": len(ox.load_jsonl(output)),
              "missing": len(missing), "missing_sample": missing[:10]}
    (run_dir / "recover_status.json").write_text(json.dumps(result, indent=2))
    return result


# ------------------------------------------------------------------ packs ---

def build_pack(row: dict, http: ox.CachedHTTP) -> dict:
    """Rebuild the anchor-filing evidence pack exactly as the century run did."""
    try:
        sec_name, filings = ox.submission_rows(row["cik"], http, include_archives=True)
        anchor = next((f for f in filings if f["accessionNumber"] == row["accession"]), None)
        if not anchor:
            return {**row, "error": "accession missing from submissions history"}
        pack = ox.make_pack([anchor], http, [row["ticker"], row["cik"], row["company"], sec_name],
                            include_exhibits=False, per_filing_chars=220_000, total_chars=700_000)
        # The pack is the durable artifact; drop the multi-megabyte raw filing cache.
        http._path(anchor["url"]).unlink(missing_ok=True)
        if len(pack) < 2_000:
            return {**row, "error": f"evidence pack too short: {len(pack)}"}
        return {**row, "anchor_form": anchor["form"], "snapshot_text": pack, "error": None}
    except Exception as exc:
        return {**row, "error": f"{type(exc).__name__}: {exc}"}


def build_packs(run_dir: Path, concurrency: int, limit: int | None) -> dict:
    output, failures = run_dir / "packs.jsonl", run_dir / "pack_failures.jsonl"
    done = {r["comprehensive_case_id"] for r in ox.load_jsonl(output)}
    done |= {r["comprehensive_case_id"] for r in ox.load_jsonl(failures)}
    pending = [r for r in ox.load_jsonl(run_dir / "recovered.jsonl")
               if r["comprehensive_case_id"] not in done]
    pending.sort(key=lambda r: (r["cutoff"], r["comprehensive_case_id"]))
    if limit:
        pending = pending[:limit]
    http = ox.CachedHTTP(run_dir / "cache" / "case_sec", min_interval=0.13)
    ok = bad = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for result in pool.map(lambda r: build_pack(r, http), pending):
            with WRITE_LOCK:
                ox.append_jsonl(failures if result["error"] else output, result)
            ok += not result["error"]
            bad += bool(result["error"])
            if (ok + bad) % 100 == 0:
                print(f"packs {ok + bad}/{len(pending)} ok={ok} failed={bad}", file=sys.stderr)
    return {"built_this_run": ok, "failed_this_run": bad,
            "packs": sum(1 for _ in open(output)) if output.exists() else 0}



def iter_jsonl(path: Path):
    """Stream large JSONL (packs are GBs) without loading the whole file."""
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace", newline="\n") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY") or (
        KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    if not key:
        raise RuntimeError(f"set TYPESAFE_API_KEY or write the key to {KEY_FILE}")
    return key


# -------------------------------------------------------------- anonymize ---

PHRASE = re.compile(r"\b[A-Z][A-Za-z0-9'&.\-]*(?:[ \t]+(?:of|and|&|de|the|for)?[ \t]*[A-Z][A-Za-z0-9'&.\-]*){0,5}")
LEADING = re.compile(r"^(?:(?:On|In|At|As|of|During|Effective|The|Our|Since|From|By|For|Through|Between)\s+)+")
MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"}
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9&]{1,6}\b")
DOMAIN = re.compile(r"\b(?:https?://)?(?:www\.)?[a-z0-9\-]+\.(?:com|net|org|io|co|us)\b[^\s]*", re.I)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PHONE = re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b")
BOILERPLATE = {
    "SEC", "GAAP", "US", "USD", "LLC", "INC", "CORP", "LP", "LTD", "PLC", "FORM", "ITEM", "PART",
    "NYSE", "NASDAQ", "FASB", "ASC", "ASU", "IFRS", "EBITDA", "EPS", "CEO", "CFO", "COO", "IPO",
    "Q1", "Q2", "Q3", "Q4", "FY", "N/A", "OTC", "IRS", "EU", "UK", "LIBOR", "SOFR", "PCAOB",
    "ISSUER", "ISSUER_ID", "REDACTED", "I", "II", "III", "IV", "A", "THE",
}


def candidate_phrases(text: str) -> list[dict]:
    """Code proposes identifier candidates; Jev decides which ones identify the issuer.

    A capitalised phrase is a candidate only if some word in it never appears in
    lowercase anywhere in the filing (a proper-noun signal), which filters generic
    headings like "Total Revenue" without a hand-maintained vocabulary.
    """
    lowercase_words = set(re.findall(r"\b[a-z][a-z0-9'\-]+\b", text))
    counts: dict[str, int] = defaultdict(int)
    for match in PHRASE.finditer(text):
        phrase = LEADING.sub("", match.group(0).strip(" .-&")).strip(" .-&")
        if phrase.endswith("'s"):
            phrase = phrase[:-2]
        if phrase in MONTHS:
            continue
        words = [w for w in re.findall(r"[A-Za-z0-9'\-]+", phrase) if w.lower() not in {"of", "and", "de", "the", "for"}]
        if not words or len(phrase) < 3 or phrase.upper() in BOILERPLATE:
            continue
        if len(words) == 1 and re.fullmatch(r"[A-Z][a-z]+", phrase) and not re.search(
                rf"[a-z,;(] {re.escape(phrase)}\b", text):
            continue  # capitalised only where a sentence or heading starts
        if phrase.isupper() and len(words) > 1:
            continue  # multi-word ALL-CAPS text is table/section headings
        if any(w.lower() not in lowercase_words and not w.isdigit() for w in words):
            counts[phrase] += 1
    for match in ACRONYM.finditer(text):
        token = match.group(0)
        # All-caps headings (TOTAL, CASH) are generic when the word also appears in lowercase.
        if token not in BOILERPLATE and not token.isdigit() and token.lower() not in lowercase_words:
            counts[token] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:MAX_CANDIDATES]
    out = []
    for phrase, count in ranked:
        position = text.find(phrase)
        start = max(0, position - 160)
        context = text[start:position + len(phrase) + 160].replace("\n", " ")
        out.append({"phrase": phrase, "occurrences": count, "context": context})
    return out


ANON_INSTRUCTIONS = (
    "The filing is from a real public company whose own name has been replaced by [ISSUER]. "
    "What kind of term is `candidates[{i}].phrase`, as used in `candidates[{i}].context`?")
ANON_CRITERIA = {
    "issuer_specific_name": ("A name particular to the filing company: its own or former name, "
                             "ticker, brand, product, drug, subsidiary, segment brand, proprietary "
                             "facility, vessel, rig, mine, field, or project."),
    "person_name": "The name of a specific person, such as an executive, director, or founder.",
    "named_counterparty": ("A specific other company named as this company's customer, supplier, "
                           "partner, acquirer, target, joint venture, or litigant."),
    "distinctive_location": ("A small or distinctive place tied to the company's operations, e.g. "
                             "a town, county, street, or site, not a country, state, or major city."),
    "generic_term": ("Generic filing language any company could use: headings, accounting or "
                     "legal terms, standard security or plan names, exhibit labels, or ordinary words."),
    "public_institution": ("A regulator, law, standard, exchange, index, rating agency, major bank "
                           "acting as lender or trustee, auditor, or government body."),
    "broad_geography": "A country, region, state, province, or major city.",
}
IDENTIFYING = ("issuer_specific_name", "person_name", "named_counterparty", "distinctive_location")


def identify_probability(answer: dict) -> float:
    return sum(answer["probabilities"].get(k, 0.0) for k in IDENTIFYING)


DICTIONARY_PATH = Path("/usr/share/dict/words")
_DICTIONARY: set[str] | None = None


def dictionary() -> set[str]:
    global _DICTIONARY
    if _DICTIONARY is None:
        _DICTIONARY = ({w.strip().lower() for w in DICTIONARY_PATH.read_text().split()}
                       if DICTIONARY_PATH.exists() else set())
    return _DICTIONARY


def is_dictionary_word(phrase: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]+", phrase)) and phrase.lower() in dictionary()


def redaction_set(decisions: list[dict]) -> set[str]:
    """Locked redaction policy, applied in code to Jev's stored probabilities.

    - P(identifying) >= 0.5 redacts, except a lone English dictionary word needs >= 0.7
      (global replacement of words like "Data" or "Directors" would damage the text).
    - A non-dictionary word inside a redacted phrase is also redacted at >= 0.3, so
      "Genworth" is removed whenever "Genworth Canada" is.
    """
    def p(d):
        return d.get("noul_raw", d["noul"])
    chosen = {d["phrase"] for d in decisions
              if p(d) >= (DICTIONARY_WORD_THRESHOLD if is_dictionary_word(d["phrase"]) else REDACT_THRESHOLD)}
    components = {w for phrase in chosen for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", phrase)}
    chosen |= {d["phrase"] for d in decisions
               if d["phrase"] in components and not is_dictionary_word(d["phrase"])
               and p(d) >= PROPAGATE_THRESHOLD}
    return chosen


def mechanical_redactions(text: str) -> str:
    text = EMAIL.sub("[REDACTED_CONTACT]", text)
    text = DOMAIN.sub("[REDACTED_URL]", text)
    return PHONE.sub("[REDACTED_CONTACT]", text)


def apply_redactions(text: str, decisions: list[dict]) -> str:
    text = mechanical_redactions(text)
    phrases = sorted(redaction_set(decisions),
                     key=len, reverse=True)
    for phrase in phrases:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", "[REDACTED]", text)
    return text


async def anonymize_cases(run_dir: Path, concurrency: int, limit: int | None, model: str) -> dict:
    """Jev pass 1: judge which candidate phrases identify the issuer."""
    import msgspec
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeError

    output = run_dir / "redactions.jsonl"
    done = {r["comprehensive_case_id"] for r in iter_jsonl(output)}
    semaphore = asyncio.Semaphore(concurrency)
    stats = {"ok": 0, "failed": 0, "input_tokens": 0, "redacted_phrases": 0}

    async with AsyncTypeSafeClient(api_key=api_key(), model=model, timeout=120.0,
                                   retry=RetryPolicy(max_retries=6)) as client:
        async def work(case: dict) -> None:
            text = mechanical_redactions(case["snapshot_text"])
            candidates = candidate_phrases(text)
            decisions = []
            async with semaphore:
                for offset in range(0, len(candidates), CANDIDATES_PER_REQUEST):
                    batch = candidates[offset:offset + CANDIDATES_PER_REQUEST]
                    state = {"filing_opening": text[:3000], "candidates": batch}
                    questions = {f"c{i}": {"type": "choice", "criteria": ANON_CRITERIA,
                                           "instructions": ANON_INSTRUCTIONS.format(i=i)}
                                 for i in range(len(batch))}
                    try:
                        response = await client.system_one(state=state, questions=questions)
                    except TypeSafeError as exc:
                        stats["failed"] += 1
                        print(f"FAILED anonymize {case['ticker']}: {exc}", file=sys.stderr)
                        return
                    stats["input_tokens"] += response.usage.input_tokens
                    for i, item in enumerate(batch):
                        answer = msgspec.to_builtins(response.answers[f"c{i}"])
                        decisions.append({"phrase": item["phrase"], "occurrences": item["occurrences"],
                                          "kind": answer["choice"],
                                          "noul": identify_probability(answer)})
            flagged = len(redaction_set(decisions))
            with WRITE_LOCK:
                ox.append_jsonl(output, {
                    "comprehensive_case_id": case["comprehensive_case_id"],
                    "prompt_version": PROMPT_VERSION, "threshold": REDACT_THRESHOLD,
                    "dictionary_word_threshold": DICTIONARY_WORD_THRESHOLD,
                    "propagate_threshold": PROPAGATE_THRESHOLD,
                    "candidates": len(decisions), "redacted": flagged, "decisions": decisions,
                    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
            stats["ok"] += 1
            stats["redacted_phrases"] += flagged
            if stats["ok"] % 100 == 0:
                print(f"anonymized {stats['ok']} failed={stats['failed']} "
                      f"in_tokens={stats['input_tokens']}", file=sys.stderr)

        # Bounded in-flight window so multi-GB packs are never all held in memory.
        pending: set[asyncio.Task] = set()
        count = 0
        for case in iter_jsonl(run_dir / "packs.jsonl"):
            if case["comprehensive_case_id"] in done:
                continue
            if limit and count >= limit:
                break
            count += 1
            pending.add(asyncio.create_task(work(case)))
            if len(pending) >= concurrency * 2:
                _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if pending:
            await asyncio.wait(pending)
    return stats


# ------------------------------------------------------------------ score ---

HORIZON = ("the 90 calendar days after `cutoff`, measured against SPY (excess return = "
           "stock total return minus SPY total return over the same window)")

OUTCOME_QUESTIONS = {
    "plus20": {"type": "noul", "instructions": (
        f"Judging only from `filing_excerpt` and `market_at_cutoff`, will this stock beat SPY "
        f"by at least 20 percentage points over {HORIZON}? Base rates for deeply drawn-down "
        "stocks are low; cheapness alone is not enough without a credible rerating mechanism.")},
    "positive_excess": {"type": "noul", "instructions": (
        f"Judging only from `filing_excerpt` and `market_at_cutoff`, will this stock have a "
        f"positive excess return over {HORIZON}?")},
    "downside_tail": {"type": "noul", "instructions": (
        f"Judging only from `filing_excerpt` and `market_at_cutoff`, will this stock trail SPY "
        f"by 25 percentage points or more over {HORIZON}, e.g. from insolvency, dilution, "
        "delisting, or a further collapse in the business?")},
}

ROLE_QUESTIONS = {
    "liquidity": {
        "liquidity_runway": {"type": "score", "instructions": (
            "How secure is the company's liquidity and financing position as disclosed in "
            "`filing_excerpt`?"), "criteria": [
            "Acute distress: going-concern doubt, covenant default, or cash runs out within a year",
            "Strained: heavy cash burn or near-term maturities that depend on new financing",
            "Adequate: can fund operations and maturities for the next year with some pressure",
            "Comfortable: ample cash or credit and manageable debt",
            "Fortress: large net cash or strong free cash flow with no meaningful maturities"]},
        "dilution_risk": {"type": "noul", "instructions": (
            "Does `filing_excerpt` indicate the company will likely need a dilutive equity or "
            "convertible raise within the next six months?")},
    },
    "operations": {
        "operating_trajectory": {"type": "score", "instructions": (
            "What is the direction of the core business in `filing_excerpt` "
            "(revenue, margins, orders, backlog, cash flow)?"), "criteria": [
            "Sharp, accelerating deterioration",
            "Continued decline without signs of stabilization",
            "Mixed or stabilizing",
            "Early, evidenced improvement",
            "Clear inflection with improving revenue, margins, and cash flow"]},
        "drawdown_nature": {"type": "choice", "instructions": (
            "Given `filing_excerpt` and the drawdown in `market_at_cutoff`, what best explains "
            "the stock's decline?"), "criteria": {
            "temporary_dislocation": "A one-off or transient problem while the franchise is intact",
            "cyclical_trough": "Industry or macro cycle weakness likely to reverse",
            "structural_decline": "Lasting loss of demand, pricing power, or competitive position",
            "financial_distress": "Balance-sheet or solvency problems dominate",
            "unclear": "The filing does not reveal the cause"}},
    },
    "catalysts": {
        "rerating_catalyst": {"type": "score", "instructions": (
            "How strong is the near-term rerating catalyst disclosed in `filing_excerpt` for the "
            "next 90 to 180 days?"), "criteria": [
            "No identifiable catalyst",
            "Only vague aspirations or generic strategy statements",
            "Plausible but undated or unfunded events",
            "Specific, dated event likely within 180 days",
            "Specific, funded, dated event likely within 90 days with material upside"]},
    },
    "accounting": {
        "accounting_red_flag": {"type": "noul", "instructions": (
            "Does `filing_excerpt` disclose a serious accounting red flag, such as a material "
            "weakness, restatement, auditor going-concern doubt, auditor change, large unexplained "
            "impairments, or aggressive revenue recognition?")},
    },
    "governance": {
        "governance_legal_risk": {"type": "noul", "instructions": (
            "Does `filing_excerpt` disclose a serious governance, legal, regulatory, or listing "
            "risk, such as an investigation, material litigation, delisting notice, or "
            "significant related-party dealings?")},
    },
}


def market_state(drawdown: float) -> dict:
    return {"drawdown_from_1y_high_pct": round(100 * float(drawdown), 1),
            "note": "Stock is at least 40% below its prior 1-year high at the cutoff."}


def case_state(case: dict, role: str, max_chars: int) -> dict:
    return {
        "study": ("Leakage-controlled historical equity study. Use only the supplied filing text "
                  "known at the cutoff. Identifying names are replaced by [ISSUER] or [REDACTED]; "
                  "do not try to recall the company or any later events."),
        "cutoff": case["cutoff"],
        "anchor_form": case.get("anchor_form") or case.get("form"),
        "market_at_cutoff": market_state(case["drawdown"]),
        "excerpt_focus": role,
        "filing_excerpt": role_excerpt(case["redacted_text"], role, max_chars=max_chars),
    }


def request_questions(role: str) -> dict:
    return {**OUTCOME_QUESTIONS, **ROLE_QUESTIONS[role]}


async def score_cases(run_dir: Path, concurrency: int, max_chars: int, limit: int | None,
                      model: str) -> dict:
    import msgspec
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeError

    output = run_dir / "judgments.jsonl"
    done = {(r["comprehensive_case_id"], r["role"]) for r in iter_jsonl(output) if r.get("answers")}
    redactions = {r["comprehensive_case_id"]: r["decisions"] for r in iter_jsonl(run_dir / "redactions.jsonl")}
    semaphore = asyncio.Semaphore(concurrency)
    stats = {"ok": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0}

    async with AsyncTypeSafeClient(api_key=api_key(), model=model, timeout=120.0,
                                   retry=RetryPolicy(max_retries=6)) as client:
        async def work(case: dict, role: str) -> None:
            async with semaphore:
                size = max_chars
                while True:
                    try:
                        response = await client.system_one(
                            state=case_state(case, role, size), questions=request_questions(role))
                        break
                    except TypeSafeError as exc:
                        # Oversized states shrink deterministically; anything else is logged.
                        if size > 8_000 and ("413" in str(exc) or "too long" in str(exc).lower()
                                             or "token" in str(exc).lower()):
                            size //= 2
                            continue
                        stats["failed"] += 1
                        print(f"FAILED {case['ticker']} {role}: {exc}", file=sys.stderr)
                        return
            row = {"comprehensive_case_id": case["comprehensive_case_id"], "role": role,
                   "prompt_version": PROMPT_VERSION, "model": response.model,
                   "excerpt_chars": size, "usage": msgspec.to_builtins(response.usage),
                   "answers": msgspec.to_builtins(response.answers),
                   "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            with WRITE_LOCK:
                ox.append_jsonl(output, row)
            stats["ok"] += 1
            stats["input_tokens"] += row["usage"].get("input_tokens", 0)
            stats["output_tokens"] += row["usage"].get("output_tokens", 0)
            if stats["ok"] % 250 == 0:
                print(f"typesafe ok={stats['ok']} failed={stats['failed']} "
                      f"in_tokens={stats['input_tokens']}", file=sys.stderr)

        async def run_case(case: dict) -> None:
            await asyncio.gather(*(work(case, role) for role in ROLE_NAMES
                                   if (case["comprehensive_case_id"], role) not in done))

        # Only anonymized cases are analyzed; raw filing text never reaches the analyzer.
        pending: set[asyncio.Task] = set()
        count = 0
        for case in iter_jsonl(run_dir / "packs.jsonl"):
            decisions = redactions.get(case["comprehensive_case_id"])
            if decisions is None or all((case["comprehensive_case_id"], r) in done for r in ROLE_NAMES):
                continue
            if limit and count >= limit:
                break
            count += 1
            case["redacted_text"] = apply_redactions(case.pop("snapshot_text"), decisions)
            pending.add(asyncio.create_task(run_case(case)))
            if len(pending) >= max(2, concurrency // 2):
                _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if pending:
            await asyncio.wait(pending)
    return stats


# --------------------------------------------------------------- evaluate ---

def value(answer: dict) -> float:
    return float(answer.get("noul", answer.get("score")))


def aggregate(run_dir: Path) -> list[dict]:
    """Combine five role views per case into locked TypeSafe signals."""
    judgments = defaultdict(dict)
    for row in iter_jsonl(run_dir / "judgments.jsonl"):
        if row.get("answers"):
            judgments[row["comprehensive_case_id"]][row["role"]] = row["answers"]
    cases = []
    for case in iter_jsonl(run_dir / "packs.jsonl"):
        views = judgments.get(case["comprehensive_case_id"], {})
        if len(views) != len(ROLE_NAMES):
            continue
        mean = lambda key: statistics.mean(value(v[key]) for v in views.values())
        role = lambda r, key: value(views[r][key])
        signals = {
            "plus20": mean("plus20"),
            "positive_excess": mean("positive_excess"),
            "downside_tail": mean("downside_tail"),
            "liquidity_runway": role("liquidity", "liquidity_runway") / 4,
            "dilution_risk": role("liquidity", "dilution_risk"),
            "operating_trajectory": role("operations", "operating_trajectory") / 4,
            "rerating_catalyst": role("catalysts", "rerating_catalyst") / 4,
            "accounting_red_flag": role("accounting", "accounting_red_flag"),
            "governance_legal_risk": role("governance", "governance_legal_risk"),
            "distress_probability": views["operations"]["drawdown_nature"]["probabilities"]["financial_distress"],
        }
        cases.append({**{k: v for k, v in case.items() if k != "snapshot_text"},
                      "market_at_cutoff": {"drawdown_from_1y_high": case["drawdown"]},
                      "typesafe": signals,
                      "syntheses": [{"thesis": None, "catalyst": None, "invalidation": None}]})
    return sorted(cases, key=lambda r: (r.get("accepted") or r["cutoff"], r["case_id"]))


def add_scores(rows: list[dict], ox_scores: dict) -> list[dict]:
    """Locked rules. Composite uses expanding z-scores of strictly earlier cases only."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("accepted") or row["cutoff"]].append(row)
    composite_parts = {"plus20": 1, "rerating_catalyst": 1, "operating_trajectory": 1,
                       "liquidity_runway": 1, "downside_tail": -1, "dilution_risk": -1,
                       "accounting_red_flag": -1, "distress_probability": -1}
    prior = defaultdict(list)
    for timestamp in sorted(grouped):
        for row in grouped[timestamp]:
            s = row["typesafe"]
            z = []
            for name, sign in composite_parts.items():
                history = prior[name]
                sd = statistics.stdev(history) if len(history) >= 2 else 0
                z.append(sign * (s[name] - statistics.mean(history)) / sd if sd else 0.0)
            drawdown = abs(row["drawdown"])
            row["locked_scores"] = {
                "ts_plus20": s["plus20"],
                "ts_composite": statistics.mean(z),
                "ts_upside_x_drawdown": s["plus20"] * drawdown,
                "ts_safety": -s["downside_tail"],
                "ox_p_plus20": ox_scores.get(row["comprehensive_case_id"], math.nan),
            }
        for row in grouped[timestamp]:
            for name in composite_parts:
                prior[name].append(row["typesafe"][name])
    return rows


def ox_p_plus20() -> dict:
    values = defaultdict(list)
    for row in ox.load_jsonl(CENTURY_DIR / "syntheses.jsonl"):
        v = (row.get("result") or {}).get("probability_plus20_excess_90d_pct")
        if isinstance(v, (int, float)):
            values[row["case_id"]].append(float(v))
    return {k: statistics.mean(v) for k, v in values.items() if len(v) == 2}


def evaluate(run_dir: Path, draws: int) -> dict:
    import century_hypotheses as ch
    import sealed_safety_eval as ev
    rows = add_scores(aggregate(run_dir), ox_p_plus20())
    rows = [r for r in rows if not math.isnan(r["locked_scores"]["ox_p_plus20"])]
    if not rows:
        raise RuntimeError("no fully scored cases")
    prices = ev.fetch_prices(rows, CENTURY_DIR, concurrency=24)
    rules = ("ts_plus20", "ts_composite", "ts_upside_x_drawdown", "ts_safety", "ox_p_plus20")
    years = sorted({int(r["cutoff"][:4]) for r in rows})
    results = {}
    for index, rule in enumerate(rules):
        chosen = ch.select(rows, rule)
        cohorts = {name: ch.evaluate(chosen, prices, *span) for name, span in ch.COHORTS.items()}
        cohorts["scored_span"] = ch.evaluate(chosen, prices, years[0], years[-1])
        clean = [cohorts["historical_holdout_2009_2018"], cohorts["forward_holdout_2021_2025"]]
        usable = [(m.get("exposure_matched_information_ratio"), m.get("trades", 0)) for m in clean]
        usable = [(v, n) for v, n in usable if v is not None and n]
        statistic = sum(v * n for v, n in usable) / sum(n for _, n in usable) if usable else -math.inf
        placebo = (ch.blocked_placebo(rows, prices, rule, statistic, draws, 92026 + index)
                   if draws and usable else {"draws": 0, "p_one_sided": None})
        _, executed, frame = ev.evaluate_selection(
            chosen, prices, "conservative", start=f"{years[0]}-01-01",
            end=(dt.date(years[-1], 12, 31) + dt.timedelta(days=181)).isoformat())
        results[rule] = {"selected_all": len(chosen), "cohorts": cohorts,
                         "clean_weighted_ir": statistic, "blocked_placebo": placebo,
                         "trades": [ev.serialize_trade(t) for t in executed]}
        if rule in ("ts_plus20", "ox_p_plus20"):
            frame.to_csv(run_dir / f"daily_{rule}.csv")
    calibration = calibration_table(rows, prices)
    output = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "prompt_version": PROMPT_VERSION, "cases": len(rows), "years": [years[0], years[-1]],
              "placebo_draws": draws, "rules": results, "calibration": calibration}
    (run_dir / "results.json").write_text(json.dumps(output, indent=2, default=str))
    (run_dir / "report.md").write_text(report(output))
    return output


def calibration_table(rows: list[dict], prices: dict) -> dict:
    """Does the TypeSafe P(+20% excess) match realised 90-day excess hit rates?"""
    import sealed_safety_eval as ev
    spy = {r["date"]: r["close"] for r in prices.get("SPY", [])}
    buckets = defaultdict(lambda: {"n": 0, "hits": 0, "p_sum": 0.0})
    for row in rows:
        trade, status = ev.make_trade(row, prices, "optimistic")
        if not trade or trade["entry_date"] not in spy or trade["exit_date"] not in spy:
            continue
        excess = trade["stock_return"] - (spy[trade["exit_date"]] / spy[trade["entry_date"]] - 1)
        p = row["typesafe"]["plus20"]
        bucket = buckets[min(int(p * 10), 9)]
        bucket["n"] += 1
        bucket["hits"] += excess >= 0.20
        bucket["p_sum"] += p
    return {f"{k / 10:.1f}-{(k + 1) / 10:.1f}": {"n": b["n"], "mean_p": b["p_sum"] / b["n"],
                                                 "hit_rate": b["hits"] / b["n"]}
            for k, b in sorted(buckets.items())}


def report(result: dict) -> str:
    lines = ["# Century P(+20%) backtest: TypeSafe (Jev) as document evaluator", "",
             f"Generated {result['generated_at']} · {result['cases']} cases "
             f"{result['years'][0]}–{result['years'][1]} · placebo draws {result['placebo_draws']}", "",
             "| Rule | Trades | CAGR | Sharpe | Max DD | Hist IR | Fwd IR | Span IR | Blocked p |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    fmt = lambda v, p=2: "n/a" if v is None else f"{v:.{p}f}"
    for name, r in result["rules"].items():
        c = r["cohorts"]
        span = c["scored_span"]
        ir = lambda k: fmt(c[k].get("exposure_matched_information_ratio"))
        pct = lambda v: "n/a" if v is None else f"{100 * v:.1f}%"
        lines.append(f"| `{name}` | {span.get('trades', 0)} | {pct(span.get('cagr'))} | "
                     f"{fmt(span.get('sharpe_over_13week_tbill'))} | {pct(span.get('max_drawdown'))} | "
                     f"{ir('historical_holdout_2009_2018')} | {ir('forward_holdout_2021_2025')} | "
                     f"{ir('scored_span')} | {fmt(r['blocked_placebo'].get('p_one_sided'), 4)} |")
    lines += ["", "## Calibration of TypeSafe P(+20% excess)", "",
              "| Bucket | n | Mean P | Realised hit rate |", "| --- | ---: | ---: | ---: |"]
    for bucket, b in result["calibration"].items():
        lines.append(f"| {bucket} | {b['n']} | {b['mean_p']:.3f} | {b['hit_rate']:.3f} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- main ---

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recover")
    packs = sub.add_parser("packs")
    packs.add_argument("--concurrency", type=int, default=6)
    packs.add_argument("--limit", type=int)
    anon = sub.add_parser("anonymize")
    anon.add_argument("--concurrency", type=int, default=32)
    anon.add_argument("--limit", type=int)
    anon.add_argument("--model", default="jev-latest")
    score = sub.add_parser("score")
    score.add_argument("--concurrency", type=int, default=32)
    score.add_argument("--max-chars", type=int, default=40_000)
    score.add_argument("--limit", type=int)
    score.add_argument("--model", default="jev-latest")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--placebo-draws", type=int, default=500)
    args = parser.parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "recover":
        result = recover(args.run_dir)
    elif args.command == "packs":
        result = build_packs(args.run_dir, args.concurrency, args.limit)
    elif args.command == "anonymize":
        result = asyncio.run(anonymize_cases(args.run_dir, args.concurrency, args.limit, args.model))
    elif args.command == "score":
        result = asyncio.run(score_cases(args.run_dir, args.concurrency, args.max_chars,
                                         args.limit, args.model))
    else:
        result = {k: v for k, v in evaluate(args.run_dir, args.placebo_draws).items()
                  if k != "rules"}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
