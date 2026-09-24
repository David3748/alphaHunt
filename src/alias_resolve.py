#!/usr/bin/env python3
"""alias_resolve.py — build a company-name -> (ticker, cik) index and resolve
the name-matched event sources (CPSC recalls, FDA recalls, SBIR awards) so they
can be joined to prices and backtested.

Index sources:
  1. SEC company_tickers.json  (~11k active issuers)
  2. century cases.jsonl        (3,026 companies already in the universe)

Matching: exact normalized name first, then token-Jaccard fuzzy within a
first-token candidate bucket. Writes <source>_resolved.jsonl with ticker added
and a match-rate summary.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab

LEGAL = ("inc", "corp", "corporation", "incorporated", "ltd", "limited", "llc",
         "llp", "lp", "co", "company", "plc", "the", "group", "holdings",
         "international", "industries", "technologies", "technology", "systems",
         "solutions", "laboratories", "labs", "pharmaceuticals", "pharma",
         "biotech", "therapeutics", "sciences", "research", "management", "capital")

# Shared alias-table v1 matching policy (researcher #12 cross-cutting gate).
FUZZY_THRESHOLD_DEFAULT = 0.6

# Corporate-suffix tokens: raw names carrying one of these count as full legal
# names and bypass the bare-stem collision guard.
SUFFIX_TOKENS = frozenset(
    ("inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
     "limited", "llc", "llp", "lp", "plc", "holdings", "holding", "group",
     "intl", "sa", "ag", "nv", "se", "ab", "asa"))

# Fund/defendant markers: never force-map these unless exact-normalized hit.
FUND_RE = re.compile(
    r"\b(fund|etf|mutual|trust\s+(co|company)?\s*(fund|etf)?|"
    r"advisors?\s+(fund|trust)|asset\s+management|capital\s+(partners|fund|"
    r"growth|opportunities)|growth\s+fund|bond\s+fund|index\s+fund|"
    r"pension|endowment|foundation|s sicav|sicav)\b", re.I)

# Known ambiguous bare stems: single-token queries equal to one of these always
# require a full-name match (return null). The generic multi-CIK containment
# rule below covers unlisted stems automatically.
AMBIGUOUS_STEMS = frozenset(
    ("compass", "endo", "american", "first", "global", "national", "united",
     "premier", "summit", "apex", "vector", "meridian", "beacon", "harbor",
     "crest", "liberty", "patriot", "eagle", "atlas", "titan", "apex",
     "alliance", "century", "heritage", "pinnacle", "cornerstone"))


def has_legal_suffix(raw):
    toks = re.findall(r"[a-z0-9]+", str(raw or "").lower())
    return any(t in SUFFIX_TOKENS for t in toks)


def _cik_sort_key(entry):
    t, c = entry
    try:
        return (int(c), t)
    except (TypeError, ValueError):
        return (10 ** 30, t)


def _pick_cik_priority(cands):
    """Deterministic tiebreak: smallest numeric CIK first (oldest registrant,
    usually the operating parent), then ticker. Never returns None."""
    return sorted(cands, key=_cik_sort_key)[0]


def norm(name):
    if not name:
        return ""
    s = str(name).lower()
    s = s.split("/")[0]           # strip state-of-incorporation "/DE/"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if t not in LEGAL]
    return " ".join(tokens)


def load_company_tickers(http, cache_dir):
    """Return dict normalized_name -> list of (ticker, cik)."""
    url = "https://www.sec.gov/files/company_tickers.json"
    data = http.json(url)
    idx = {}
    for _, rec in data.items():
        cik = str(rec.get("cik_str", "")).zfill(10)
        ticker = rec.get("ticker", "")
        title = rec.get("title", "")
        if ticker and title:
            key = norm(title)
            if key:
                idx.setdefault(key, []).append((ticker, cik))
    return idx


def build_index(run_dir):
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.0)
    idx = {}
    print("loading company_tickers.json...", flush=True)
    try:
        idx = load_company_tickers(http, run_dir)
        print(f"  {len(idx)} canonical names from company_tickers.json", flush=True)
    except Exception as e:
        print(f"  company_tickers.json failed: {e}", flush=True)
    # add century cases
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    added = 0
    if cases_path.exists():
        for row in ox_lab.load_jsonl(cases_path):
            t = row.get("ticker", "")
            c = row.get("cik", "")
            comp = row.get("company", "")
            if t and comp:
                key = norm(comp)
                if key and key not in idx:
                    idx[key] = [(t, str(c).zfill(10))]
                    added += 1
    print(f"  +{added} names from century cases -> {len(idx)} total", flush=True)

    # add subsidiary names -> parent ticker/cik from the Exhibit 21 map
    sub_map = run_dir / "subsidiary_map.jsonl"
    if sub_map.exists():
        sub_added = 0
        for rec in ox_lab.load_jsonl(sub_map):
            parent_t = rec.get("ticker", "")
            parent_c = str(rec.get("cik", "")).zfill(10) if rec.get("cik") else ""
            if not parent_t:
                continue
            for s in rec.get("subsidiaries", []):
                key = norm(s.get("name", ""))
                if not key or len(key) < 3:
                    continue
                entry = (parent_t, parent_c)
                existing = idx.setdefault(key, [])
                if entry not in existing:
                    existing.append(entry)
                    sub_added += 1
        print(f"  +{sub_added} subsidiary names from Exhibit 21 -> {len(idx)} total", flush=True)
    return idx


def token_jaccard(a, b):
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def resolve(name, idx, fuzzy_threshold=FUZZY_THRESHOLD_DEFAULT):
    """Return (ticker, cik, confidence) or (None, None, None).

    Policy (alias-table v1):
      exact (CIK-priority tiebreak) -> token-containment fallback (distinctive
      single-stem joins like coinbase->Coinbase Global) -> Jaccard fuzzy at
      `fuzzy_threshold`. Guards: bare colliding stems (Compass/Endo class),
      officer surnames / person names, and fund defendants return null instead
      of a force-mapped wrong CIK.
    """
    key = norm(name)
    if not key:
        return None, None, None
    if key in idx and idx[key]:
        # Full-name-match requirement: a bare single-token query (no legal
        # suffix in the raw string) that collides across >=2 CIKs via token
        # containment must not resolve through the exact shortcut either.
        # E.g. bare "Compass" must not -> COMP while "Compass Diversified
        # Holdings" -> CODI and "Compass, Inc." -> COMP remain legal.
        qtokens = set(key.split())
        if len(qtokens) == 1 and not has_legal_suffix(name):
            stem = key
            if stem in AMBIGUOUS_STEMS:
                return None, None, None
            hit_ciks = set()
            for k, v in idx.items():
                if stem in set(k.split()):
                    hit_ciks.update(c for _, c in v)
            if len(hit_ciks) >= 2:
                return None, None, None
        t, c = _pick_cik_priority(idx[key])
        return t, c, "exact"
    qtokens = key.split()
    # Fund / person defendants: no force-map without an exact hit.
    if FUND_RE.search(str(name or "")):
        return None, None, None
    if len(qtokens) <= 1 and not has_legal_suffix(name):
        # Bare surname / single-token defendant (e.g. "Smith", "Compass",
        # "Endo"): only an exact hit may resolve; containment/fuzzy below
        # would be a force-map. The one exception is the distinctive-stem
        # containment check which requires a single owning CIK.
        stem = key
        owners = set()
        for k, v in idx.items():
            if stem in set(k.split()):
                owners.update(c for _, c in v)
        if len(owners) != 1:
            return None, None, None
        # exactly one owning CIK -> fall through to containment resolution
    if len(qtokens) == 2 and not has_legal_suffix(name):
        # Likely officer/person defendant ("John Smith"): allow only an exact
        # or containment hit against a real canonical, never fuzzy.
        qset = set(qtokens)
        contained = [(k, v) for k, v in idx.items()
                     if qset <= set(k.split()) or set(k.split()) <= qset]
        if not contained:
            return None, None, None
    # token-containment fallback: query tokens subset of candidate or vice
    # versa. Fixes coinbase (norm "coinbase") vs "coinbase global" Jaccard
    # 0.50 < 0.60 -> containment_0.50. Requires a single owning CIK when the
    # query is a bare stem, else returns null (collision guard).
    qset = set(qtokens)
    contained = [(k, v) for k, v in idx.items()
                 if qset <= set(k.split()) or set(k.split()) <= qset]
    if contained:
        owners = set(c for _, v in contained for _, c in v)
        if len(owners) == 1 or len(qset) > 1:
            # highest Jaccard first, CIK-priority tiebreak — deterministic
            scored = sorted(
                contained,
                key=lambda kv: (-token_jaccard(key, kv[0]),
                                _cik_sort_key(_pick_cik_priority(kv[1]))))
            best_k, best_v = scored[0]
            t, c = _pick_cik_priority(best_v)
            return t, c, f"containment_{token_jaccard(key, best_k):.2f}"
        return None, None, None
    # fuzzy within first-token bucket
    first = key.split()[0]
    bucket = [(k, v) for k, v in idx.items() if k.split()[0] == first]
    if not bucket:
        # No shared first token and no containment: a fuzzy scan of the whole
        # 8k-name index would be a force-map engine for surnames — refuse for
        # short person-like queries, allow for longer corporate names.
        if len(qtokens) <= 2 and not has_legal_suffix(name):
            return None, None, None
        bucket = list(idx.items())
    best = None
    best_score = 0.0
    best_cands = None
    for k, v in bucket:
        s = token_jaccard(key, k)
        if s > best_score:
            best_score = s
            best = k
            best_cands = v
    if best and best_score >= fuzzy_threshold:
        t, c = _pick_cik_priority(best_cands)
        return t, c, f"fuzzy_{best_score:.2f}"
    return None, None, None


def resolve_source(idx, in_name, out_path, name_field):
    if not in_name.exists():
        return 0, 0
    total = 0
    matched = 0
    for row in ox_lab.load_jsonl(in_name):
        total += 1
        t, c, conf = resolve(row.get(name_field, ""), idx)
        if t:
            row["ticker"] = t
            row["cik"] = c
            row["match_confidence"] = conf
            matched += 1
        else:
            row["ticker"] = ""
            row["match_confidence"] = "none"
        ox_lab.append_jsonl(out_path, row)
    return total, matched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    args = ap.parse_args()
    run_dir = Path(args.run_dir)

    idx = build_index(run_dir)

    # sbir: preload award_year by firm (extracted rows lack the date)
    year_by_firm = {}
    sbir_awards = run_dir / "sbir_awards.jsonl"
    if sbir_awards.exists():
        for row in ox_lab.load_jsonl(sbir_awards):
            f = row.get("firm", "")
            y = row.get("award_year", "")
            if f and y and f not in year_by_firm:
                year_by_firm[f] = y

    # resolve against EXTRACTED files (they carry name + signal + date)
    sources = [
        ("cpsc", "cpsc_extracted.jsonl", "manufacturer", "recall_date"),
        ("fda", "fda_extracted.jsonl", "recalling_firm", "recall_initiation_date"),
        ("sbir", "sbir_extracted.jsonl", "firm", None),
    ]
    summary = []
    for key, in_file, name_field, date_field in sources:
        in_path = run_dir / in_file
        out_path = run_dir / f"{key}_resolved.jsonl"
        if not in_path.exists():
            print(f"{key}: no extracted file, skip")
            continue
        total = 0
        matched = 0
        for row in ox_lab.load_jsonl(in_path):
            total += 1
            t, c, conf = resolve(row.get(name_field, ""), idx)
            row["ticker"] = t or ""
            row["match_confidence"] = conf or "none"
            if key == "sbir" and not date_field:
                row["award_year"] = year_by_firm.get(row.get("firm", ""), "")
            if t:
                matched += 1
            ox_lab.append_jsonl(out_path, row)
        pct = round(100 * matched / total, 1) if total else 0
        summary.append({"source": key, "total": total, "matched": matched, "match_pct": pct})
        print(f"{key}: {matched}/{total} matched ({pct}%)", flush=True)

    (run_dir / "alias_resolve_summary.json").write_text(json.dumps(summary, indent=2))
    print("done")


if __name__ == "__main__":
    main()