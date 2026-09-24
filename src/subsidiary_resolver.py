#!/usr/bin/env python3
"""subsidiary_resolver.py — build the subsidiary -> parent(ticker,cik) map from
10-K Exhibit 21 ("Subsidiaries of the Registrant"). This is the entity-resolution
moat: recall/award/contract events name subsidiaries, not parents.

Flow per CIK in universe:
  1. submissions API -> latest 10-K accession
  2. filing index.json -> locate the EX-21 exhibit
  3. download + clean the exhibit text
  4. LLM extract subsidiary names (+ jurisdiction)
Writes subsidiary_map.jsonl (subsidiary_name -> parent ticker/cik).

Subcommands: build | match
"""

import argparse
import concurrent.futures as cf
import json
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import subagents as sa
import ox_lab

WRITE_LOCK = threading.Lock()

SUB_SCHEMA = {
    "type": "object",
    "properties": {
        "subsidiaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Subsidiary legal name, cleaned"},
                    "jurisdiction": {"type": "string", "description": "State/country of incorporation if listed"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "is_consolidated_list": {"type": "boolean", "description": "Exhibit states subsidiaries are omitted/consolidated"},
        "count_estimate": {"type": "integer"},
    },
    "required": ["subsidiaries", "is_consolidated_list"],
    "additionalProperties": False,
}

SUB_SYSTEM = (
    "You parse SEC Exhibit 21 (subsidiaries of the registrant). Extract the subsidiary "
    "names as listed. Remove trailing legal suffixes variation is fine, but keep the "
    "distinctive name. If the exhibit states subsidiaries are omitted or listed in a "
    "consolidated manner, set is_consolidated_list=true. Ignore the parent entity itself."
)


def clean_name(name):
    s = re.sub(r"\s+", " ", name).strip()
    return s


# ── Offline Exhibit-21 row ingestion (no API key, no network) ────────────────
# Shared subsidiary→CIK alias table v1 reuses this path: given already-fetched
# Exhibit-21 rows (subsidiary_map.jsonl records), build the normalized
# subsidiary-name -> [(parent_ticker, parent_cik)] index plus the forward
# parent -> children map. LLM extraction (extract_subsidiaries/build) stays
# optional/stubbed and is never called from here.

def ingest_exhibit21_record(rec):
    """Split one subsidiary_map.jsonl record into (sub_name, ticker, cik, jurisdiction) rows."""
    ticker = (rec.get("ticker") or "").strip().upper()
    raw_cik = str(rec.get("cik") or "").strip()
    cik = raw_cik.zfill(10) if raw_cik else ""
    if not ticker:
        return []
    rows = []
    subs = rec.get("subsidiaries", [])
    if isinstance(subs, dict):
        subs = [subs]
    for s in subs or []:
        if isinstance(s, str):
            name, jur = clean_name(s), ""
        elif isinstance(s, dict):
            name, jur = clean_name(s.get("name", "")), (s.get("jurisdiction") or "")
        else:
            continue
        if not name or len(name) < 3:
            continue
        rows.append({"name": name, "ticker": ticker, "cik": cik,
                     "jurisdiction": jur})
    return rows


def build_index_from_rows(records, norm_fn=None):
    """Build ({norm_name: [(ticker, cik)]}, {parent_key: [children]}) offline.

    records: iterable of subsidiary_map.jsonl-style dicts (or pre-split rows
      with name/ticker/cik keys — both accepted).
    norm_fn: callable(raw_name) -> normalized key; defaults to the
      alias_resolve.norm tokenizer so both modules share one normalizer.
    No network, no API key. Pure function of its inputs.
    """
    if norm_fn is None:
        from alias_resolve import norm as norm_fn
    idx = {}
    children_by_ticker = {}
    children_by_cik = {}
    flat = []
    for rec in records or []:
        if isinstance(rec, dict) and "name" in rec and "ticker" in rec and "subsidiaries" not in rec:
            rows = [{"name": clean_name(rec.get("name", "")),
                     "ticker": (rec.get("ticker") or "").strip().upper(),
                     "cik": str(rec.get("cik") or "").zfill(10) if rec.get("cik") else "",
                     "jurisdiction": rec.get("jurisdiction") or ""}]
        else:
            rows = ingest_exhibit21_record(rec if isinstance(rec, dict) else {})
        for r in rows:
            if not r["ticker"]:
                continue
            flat.append(r)
            key = norm_fn(r["name"])
            if not key or len(key) < 3:
                continue
            entry = (r["ticker"], r["cik"])
            existing = idx.setdefault(key, [])
            if entry not in existing:
                existing.append(entry)
            child = {"name": r["name"], "jurisdiction": r.get("jurisdiction", "")}
            tkey = r["ticker"].upper()
            if child not in children_by_ticker.setdefault(tkey, []):
                children_by_ticker[tkey].append(child)
            if r["cik"] and child not in children_by_cik.setdefault(r["cik"], []):
                children_by_cik[r["cik"]].append(child)
    return idx, {"by_ticker": children_by_ticker, "by_cik": children_by_cik,
                 "rows": flat}


def latest_10k_accession(http, cik):
    cik_pad = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_pad}.json"
    try:
        data = http.json(url)
    except Exception:
        return None
    recent = data.get("filings", {}).get("recent", [])
    for form, acc, date in zip(recent.get("form", []), recent.get("accessionNumber", []),
                               recent.get("filingDate", [])):
        if form in ("10-K", "10-K/A", "20-F", "40-F"):
            return acc
    return None


def find_ex21(http, cik, accession):
    cik_pad = str(cik).zfill(10)
    nodash = accession.replace("-", "")
    try:
        idx = http.json(f"https://www.sec.gov/Archives/edgar/data/{cik_pad}/{nodash}/index.json")
    except Exception:
        return None
    items = idx.get("directory", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]
    for it in items:
        n = it.get("name", "").upper()
        if re.search(r"EX-?21|EXHIBIT[-_]?21|EX-?21\.", n) and n.endswith((".HTM", ".HTML", ".TXT", ".XML")):
            return it["name"]
    return None


def extract_subsidiaries(client, cik, ticker, company, accession, exhibit_text):
    user = (f"Company: {company} ({ticker})\nExhibit 21 text:\n{exhibit_text[:5000]}")
    tool = {"type": "function", "function": {
        "name": "subsidiaries", "description": "Extract subsidiaries.",
        "parameters": SUB_SCHEMA, "strict": True,
    }}
    try:
        raw = client.chat(
            [{"role": "system", "content": SUB_SYSTEM}, {"role": "user", "content": user}],
            temperature=0.05, max_tokens=2000, tools=[tool],
            tool_choice={"type": "function", "function": {"name": "subsidiaries"}},
        )
        return json.loads(raw)
    except Exception:
        return {"subsidiaries": [], "is_consolidated_list": False}


def build(run_dir, concurrency, api_key):
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    cik_map = {}
    for row in ox_lab.load_jsonl(cases_path):
        cik = str(row.get("cik", "")).zfill(10)
        t = row.get("ticker", "")
        if cik and t and cik not in cik_map:
            cik_map[cik] = {"ticker": t, "company": row.get("company", "")}

    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    client = sa.OpenRouter(api_key, model=sa.DEFAULT_MODEL, timeout=120, max_retries=3)

    out_path = run_dir / "subsidiary_map.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("cik", ""))

    jobs = [cik for cik in cik_map if cik not in done]
    print(f"{len(jobs)} CIKs to process ({len(done)} done)", flush=True)

    def process(cik):
        info = cik_map[cik]
        acc = latest_10k_accession(http, cik)
        if not acc:
            return {"cik": cik, "ticker": info["ticker"], "subsidiaries": [], "status": "no_10k"}
        ex21 = find_ex21(http, cik, acc)
        if not ex21:
            return {"cik": cik, "ticker": info["ticker"], "subsidiaries": [], "status": "no_ex21", "accession": acc}
        nodash = acc.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{ex21}"
        try:
            raw = http.get(url, timeout=60)
            text = raw.decode("utf-8", errors="replace")
            text = ox_lab.clean_document(text.encode("utf-8", errors="replace"))[:6000]
        except Exception:
            return {"cik": cik, "ticker": info["ticker"], "subsidiaries": [], "status": "download_fail", "accession": acc}
        res = extract_subsidiaries(client, cik, info["ticker"], info["company"], acc, text)
        return {"cik": cik, "ticker": info["ticker"], "company": info["company"],
                "accession": acc, "status": "ok", **res}

    count = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(process, cik): cik for cik in jobs}
        for fut in cf.as_completed(futures):
            result = fut.result()
            ox_lab.append_jsonl(out_path, result)
            count += 1
            if count % 100 == 0:
                print(f"  {count}/{len(jobs)} done", flush=True)
    print(f"subsidiary_map done: {count}", flush=True)
    return out_path


def match(run_dir):
    """Load subsidiary_map and emit subsidiary_name -> parent lookup count."""
    map_path = run_dir / "subsidiary_map.jsonl"
    if not map_path.exists():
        print("no subsidiary_map, run build first")
        return
    names = 0
    for rec in ox_lab.load_jsonl(map_path):
        names += len(rec.get("subsidiaries", []))
    print(f"total subsidiary names: {names}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build", "match"])
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    ap.add_argument("--concurrency", type=int, default=96)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "build":
        key = sa.get_api_key(args.api_key)
        if not key:
            print("NO OPENROUTER_API_KEY")
            return
        build(run_dir, args.concurrency, key)
    else:
        match(run_dir)


if __name__ == "__main__":
    main()