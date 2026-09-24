#!/usr/bin/env python3
"""How anonymous are the "issuer-redacted" evidence packs the forecaster saw?

`ox_lab.redact_issuer` replaces the SEC registrant name (and its distinctive core
phrase) with [ISSUER]. It does not touch shortened names ("Nabors" for "NABORS
INDUSTRIES LTD"), tickers, or cover-page identifiers such as the street address
and IRS employer number. This module measures, for every pack:

- surviving distinctive name tokens (non-dictionary words of the registrant name);
- surviving ticker mentions (whole word, case-sensitive, non-dictionary tickers);
- whether the cover page still carries an address / IRS employer ID.

It also builds strictly scrubbed MD&A excerpts for the LLM re-identification
probe (`--sample`), so the probe measures what content alone gives away.

    python3 src/redaction_audit.py --out results/redaction_audit
    python3 src/redaction_audit.py --out results/redaction_audit --sample 48
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CENTURY_PACKS = ROOT / "lab_runs/century_typesafe/packs.jsonl"
LIVE_PACKS = ROOT / "lab_runs/live_2026/cases.jsonl"
DATASET = ROOT / "lab_runs/llm_incremental/dataset.csv"
DICT_PATH = Path("/usr/share/dict/words")

SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
            "plc", "holdings", "holding", "group", "the", "and", "of", "lp", "llc", "sa", "nv",
            "ag", "se", "de", "trust", "fund", "partners", "international", "technologies",
            "technology", "industries", "systems", "pharmaceuticals", "therapeutics", "bancorp",
            "financial", "energy", "resources", "global", "new", "com", "adr"}
IRS_RE = re.compile(r"\b\d{2}-\d{7}\b")
ADDRESS_RE = re.compile(r"Address of (the )?principal executive offices", re.I)
MDA_RE = re.compile(r"management[’'`s]{0,2}\s+discussion\s+and\s+analysis", re.I)


def load_dictionary(path: Path = DICT_PATH) -> set[str]:
    try:
        return {w.strip().lower() for w in path.open(encoding="utf-8", errors="ignore")}
    except OSError:
        return set()


def name_tokens(company: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z][A-Za-z0-9&'-]*", company or "")
            if t.lower().strip("'") not in SUFFIXES and len(t) >= 3]


def distinctive_tokens(company: str, words: set[str]) -> list[str]:
    """Registrant-name tokens that are not ordinary English words (e.g. 'Nabors')."""
    return [t for t in name_tokens(company) if t.lower() not in words and len(t) >= 4]


def ticker_pattern(ticker: str, words: set[str]) -> re.Pattern | None:
    base = (ticker or "").split(".")[0].split("-")[0]
    if len(base) < 3 or base.lower() in words:
        return None  # 'ALL', 'ON', 'IT': cannot be told apart from prose
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(base)}(?![A-Za-z0-9])")


def leak_stats(text: str, company: str, ticker: str, words: set[str]) -> dict:
    tokens = distinctive_tokens(company, words)
    name_hits = sum(len(re.findall(rf"\b{re.escape(t)}\b", text, flags=re.I)) for t in tokens)
    pat = ticker_pattern(ticker, words)
    head = text[:20_000]
    return {
        "distinctive_tokens": " ".join(tokens),
        "name_token_hits": name_hits,
        "ticker_hits": len(pat.findall(text)) if pat else 0,
        "ticker_checkable": pat is not None,
        "cover_address": bool(ADDRESS_RE.search(head)),
        "cover_irs_id": bool(IRS_RE.search(head)),
        "issuer_placeholders": text.count("[ISSUER]"),
        "chars": len(text),
    }


def strict_scrub(text: str, company: str, ticker: str) -> str:
    """Remove every name token and the ticker, not just the full registrant phrase."""
    out = text
    for tok in sorted(set(name_tokens(company)), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(tok)}\w*", "[ISSUER]", out, flags=re.I)
    base = (ticker or "").split(".")[0]
    if len(base) >= 2:
        out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(base)}(?![A-Za-z0-9])", "[ISSUER]", out)
    out = re.sub(r"https?://\S+|www\.\S+", "[URL]", out)
    out = IRS_RE.sub("[ID]", out)
    return re.sub(r"(\[ISSUER\][\s,.'’-]*){2,}", "[ISSUER] ", out)


def mda_excerpt(text: str, chars: int = 4_500) -> str:
    """Business-describing prose: the MD&A body (skipping the table of contents)."""
    hits = [m.start() for m in MDA_RE.finditer(text)]
    start = hits[1] if len(hits) > 1 else (hits[0] if hits else len(text) // 20)
    chunk = text[start:start + chars * 3]
    # prefer prose lines over table fragments
    lines = [ln for ln in chunk.splitlines() if len(ln.split()) >= 8]
    return "\n".join(lines)[:chars]


def iter_packs():
    for path, cohort in ((CENTURY_PACKS, "century"), (LIVE_PACKS, "live_2026")):
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                cid = row.get("comprehensive_case_id") or row.get("case_id")
                yield cohort, cid, row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/redaction_audit")
    ap.add_argument("--sample", type=int, default=0, help="build N scrubbed probe excerpts")
    ap.add_argument("--probe-dir", type=Path, default=None,
                    help="where probe excerpts + answer key go (keep out of git)")
    ap.add_argument("--seed", type=int, default=20260923)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    words = load_dictionary()

    outcomes = {}
    if DATASET.exists():
        with DATASET.open() as fh:
            for r in csv.DictReader(fh):
                outcomes[r["case_id"]] = r

    rows, texts = [], {}
    want = set()
    if args.sample:
        rng = random.Random(args.seed)
        pre = [r for r in outcomes.values() if r["cohort"] == "century" and "2011" <= r["cutoff"][:4] <= "2024"]
        live = [r for r in outcomes.values() if r["cohort"] == "live_2026"]

        def pick(pool, lo, hi, n):
            cand = [r for r in pool if lo <= float(r["excess"]) < hi]
            by_year = {}
            for r in sorted(cand, key=lambda r: r["case_id"]):
                by_year.setdefault(r["cutoff"][:4], []).append(r)
            chosen, years = [], sorted(by_year)
            while len(chosen) < n and any(by_year.values()):
                for y in years:
                    if by_year[y] and len(chosen) < n:
                        chosen.append(by_year[y].pop(rng.randrange(len(by_year[y]))))
            return chosen

        n_live = max(4, args.sample // 4)
        n_pre = args.sample - n_live
        sample = (pick(pre, 0.30, 99, n_pre // 2) + pick(pre, -99, -0.30, n_pre - n_pre // 2)
                  + pick(live, 0.20, 99, n_live // 2) + pick(live, -99, -0.20, n_live - n_live // 2))
        want = {r["case_id"] for r in sample}

    for n, (cohort, cid, row) in enumerate(iter_packs(), 1):
        text = row.get("snapshot_text") or ""
        if not text:
            continue
        stats = leak_stats(text, row.get("company", ""), row.get("ticker", ""), words)
        rows.append({"case_id": cid, "cohort": cohort, "ticker": row.get("ticker", ""),
                     "cutoff": row.get("cutoff", ""), **stats})
        if cid in want:
            texts[cid] = (row.get("company", ""), row.get("ticker", ""), text)
        if n % 1000 == 0:
            print(f"  scanned {n}", file=sys.stderr)

    with (args.out / "pack_leaks.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def share(sub, key, cond=lambda v: v > 0):
        return round(sum(cond(r[key]) for r in sub) / len(sub), 4) if sub else None

    summary = {}
    for cohort in ("century", "live_2026", "all"):
        sub = rows if cohort == "all" else [r for r in rows if r["cohort"] == cohort]
        checkable = [r for r in sub if r["ticker_checkable"]]
        has_tokens = [r for r in sub if r["distinctive_tokens"]]
        summary[cohort] = {
            "packs": len(sub),
            "packs_with_distinctive_name_tokens": len(has_tokens),
            "share_name_token_survives": share(has_tokens, "name_token_hits"),
            "share_name_token_survives_5plus": share(has_tokens, "name_token_hits", lambda v: v >= 5),
            "median_name_token_hits": sorted(r["name_token_hits"] for r in has_tokens)[len(has_tokens) // 2] if has_tokens else None,
            "share_ticker_survives": share(checkable, "ticker_hits"),
            "share_cover_address": share(sub, "cover_address", bool),
            "share_cover_irs_id": share(sub, "cover_irs_id", bool),
            "share_any_identifier": round(sum((r["name_token_hits"] > 0) or (r["ticker_hits"] > 0)
                                              or r["cover_address"] for r in sub) / len(sub), 4) if sub else None,
        }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if args.sample:
        probe = args.probe_dir or args.out / "probe"
        probe.mkdir(parents=True, exist_ok=True)
        key, items = [], []
        for i, r in enumerate(sorted(sample, key=lambda r: r["case_id"]), 1):
            if r["case_id"] not in texts:
                continue
            company, ticker, text = texts[r["case_id"]]
            excerpt = strict_scrub(mda_excerpt(text), company, ticker)
            item_id = f"P{i:02d}"
            items.append({"item": item_id, "excerpt": excerpt})
            key.append({"item": item_id, "case_id": r["case_id"], "company": company, "ticker": ticker,
                        "cutoff": r["cutoff"], "cohort": r["cohort"], "excess": float(r["excess"]),
                        "p20": float(r["p20"])})
        (probe / "items.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
        (probe / "answer_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
        print(f"probe: {len(items)} excerpts -> {probe}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
