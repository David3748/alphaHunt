#!/usr/bin/env python3
"""unstructured_proto.py — Multi-source unstructured data puller + LLM extractor.

Subcommands: pull | extract | run | export
Matches the century_pipeline / obscure_miner pattern: JSONL in/out,
ThreadPoolExecutor concurrency, resumable stages, gdrive export.

No alias table required for initial sources (NOAA = geo-join, 8-K = CIK,
USDA/Wikipedia = built-in joins, Steam = hardcoded map).
"""

import argparse
import concurrent.futures as cf
import csv
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import subagents as sa
import ox_lab

DEFAULT_MODEL = sa.DEFAULT_MODEL

# ── Write lock for JSONL output ─────────────────────────────────────────────
WRITE_LOCK = threading.Lock()

# ── Config ──────────────────────────────────────────────────────────────────


def load_config(path: Path) -> dict:
    with open(path) as fh:
        return json.load(fh)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: NOAA Storm Events — severity scoring (no alias table, geo-join)
# ═══════════════════════════════════════════════════════════════════════════════

NOAA_SCHEMA = {
    "type": "object",
    "properties": {
        "severity_score": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "1=minor/tree-down, 2=localized-property, 3=widespread-structures, 4=multi-county-severe, 5=catastrophic-fatalities",
        },
        "damage_category": {
            "type": "string",
            "enum": ["none", "vegetation_only", "residential", "commercial", "infrastructure", "mixed_severe"],
        },
        "is_insurable_event": {"type": "boolean"},
        "estimated_scope": {
            "type": "string",
            "enum": ["single_address", "neighborhood", "town", "multi_town", "county_wide", "multi_county"],
        },
        "key_hazards": {
            "type": "array",
            "items": {"type": "string"},
            "description": "e.g. flooding, wind, hail, tornado, fire, structural_collapse",
        },
        "narrative_summary": {"type": "string", "maxLength": 200},
    },
    "required": ["severity_score", "damage_category", "is_insurable_event", "estimated_scope", "key_hazards", "narrative_summary"],
    "additionalProperties": False,
}

NOAA_SYSTEM = (
    "You are a P&C insurance catastrophe analyst. Score the severity of a NOAA storm event "
    "based on its narrative description. Focus on insurable damage: structural, commercial, "
    "infrastructure, and residential property. Ignore crop-only or open-water events unless "
    "they hit structures. Be conservative — default to lower severity when ambiguous."
)

SEVERE_EVENT_TYPES = {"Tornado", "Hurricane", "Flood", "Flash Flood", "Wildfire",
                       "Hail", "Winter Storm", "Ice Storm", "Storm Surge", "Winter Weather",
                       "Blizzard", "Heavy Snow", "High Wind"}
HIGH_POP_STATES = {"TEXAS", "FLORIDA", "CALIFORNIA", "NEW YORK", "PENNSYLVANIA", "ILLINOIS",
                    "OHIO", "GEORGIA", "NORTH CAROLINA", "MICHIGAN", "NEW JERSEY", "VIRGINIA",
                    "WASHINGTON", "ARIZONA", "MASSACHUSETTS", "TENNESSEE", "INDIANA", "MISSOURI",
                    "MARYLAND", "WISCONSIN", "COLORADO", "MINNESOTA", "SOUTH CAROLINA", "ALABAMA",
                    "LOUISIANA", "KENTUCKY", "OREGON", "OKLAHOMA", "CONNECTICUT", "UTAH"}


def pull_noaa(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "noaa_storm_events.jsonl"
    if out_path.exists():
        print(f"  NOAA already pulled → {out_path}")
        return out_path
    sc = config["sources"]["noaa_storms"]
    years = sc["years"]
    sample_max = sc.get("sample_max", 5000)
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.3)

    # Scrape the directory index once to get the exact filename per year
    # (cut dates vary: historical years use c20260323, recent years differ)
    filename_by_year = {}
    try:
        index_html = http.get(
            "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/",
            timeout=60).decode("utf-8", errors="replace")
        for match in re.findall(r"StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d{8})\.csv\.gz", index_html):
            yr, cut = match
            filename_by_year.setdefault(yr, f"StormEvents_details-ftp_v1.0_d{yr}_c{cut}.csv.gz")
    except Exception as e:
        print(f"  NOAA: index scrape failed ({e}), falling back to guesses")

    all_rows = []
    for yr in years:
        yr_str = str(yr)
        url = None
        if yr_str in filename_by_year:
            url = f"https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/{filename_by_year[yr_str]}"
        else:
            for cut in [f"{yr}0801", "20260323", "20260819"]:
                candidate = f"https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/StormEvents_details-ftp_v1.0_d{yr_str}_c{cut}.csv.gz"
                try:
                    http.get(candidate, timeout=60)
                    url = candidate
                    break
                except Exception:
                    continue
        if url is None:
            print(f"  NOAA: could not resolve filename for year {yr}, skipping")
            continue
        try:
            raw = http.get(url, timeout=120)
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                text = gz.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  NOAA: download failed for {yr}: {e}")
            continue
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            et = row.get("EVENT_TYPE", "")
            st = row.get("STATE", "")
            narrative = row.get("EVENT_NARRATIVE", "").strip()
            if et in SEVERE_EVENT_TYPES and st in HIGH_POP_STATES and len(narrative) > 20:
                all_rows.append({
                    "event_id": row.get("EVENT_ID", ""),
                    "event_type": et,
                    "state": st,
                    "county_fips": f"{row.get('CZ_FIPS', '')}",
                    "begin_date": row.get("BEGIN_DATE_TIME", row.get("BEGIN_DATE", "")),
                    "damage_property": row.get("DAMAGE_PROPERTY", ""),
                    "narrative": narrative[:2000],
                })
    import random
    random.shuffle(all_rows)
    all_rows = all_rows[:sample_max]
    for r in all_rows:
        ox_lab.append_jsonl(out_path, r)
    print(f"  NOAA pulled {len(all_rows)} events → {out_path}")
    return out_path


def extract_noaa(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "noaa_storm_events.jsonl"
    if not in_path.exists():
        print("  NOAA: no input data, run pull first")
        return
    out_path = run_dir / "noaa_extracted.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("event_id", ""))
    rows = ox_lab.load_jsonl(in_path)
    todo = [r for r in rows if r.get("event_id") not in done]
    print(f"  NOAA extract: {len(todo)} events to classify ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "noaa_severity", "description": "Score storm event severity.",
        "parameters": NOAA_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = (f"Event type: {item['event_type']}\nState: {item['state']}\n"
                f"Property damage estimate: {item.get('damage_property','')}\n"
                f"Narrative: {item['narrative']}")
        try:
            raw = client.chat(
                [{"role": "system", "content": NOAA_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=1500,
                tools=[tool], tool_choice={"type": "function", "function": {"name": "noaa_severity"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"severity_score": 0, "error": "extraction_failed"}
        result["event_id"] = item["event_id"]
        result["event_type"] = item["event_type"]
        result["state"] = item["state"]
        result["begin_date"] = item.get("begin_date", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            result = fut.result()
            ox_lab.append_jsonl(out_path, result)
            if (i + 1) % 100 == 0:
                print(f"  NOAA extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: 8-K Item 5.02 — executive departure classification
# ═══════════════════════════════════════════════════════════════════════════════

DEPARTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "departure_type": {"type": "string", "enum": ["resignation", "retirement", "termination", "death", "stepping_down", "ambiguous"]},
        "officer_role": {"type": "string", "enum": ["CEO", "CFO", "CTO", "COO", "General_Counsel", "CAO", "other_C_suite", "VP", "director"]},
        "is_unexpected": {"type": "boolean", "description": "Abrupt, no transition plan, effective immediately, or stated disagreements"},
        "successor_named": {"type": "boolean"},
        "has_disagreement_flag": {"type": "boolean", "description": "8-K mentions disagreements with management/accounting/operations"},
        "transition_period_days": {"type": "integer", "description": "Days until departure effective date, 0 if immediate"},
        "stated_reason": {"type": "string", "maxLength": 150},
    },
    "required": ["departure_type", "officer_role", "is_unexpected", "successor_named", "has_disagreement_flag", "transition_period_days", "stated_reason"],
    "additionalProperties": False,
}

DEPARTURE_SYSTEM = (
    "You analyze SEC 8-K Item 5.02 filings for executive departures. "
    "Classify the departure type, role, and whether it appears unexpected/sudden. "
    "Key flags for 'unexpected': 'effective immediately', no named successor, "
    "'disagreements' mentioned, departure 'not due to' boilerplate, very short transition."
)


def pull_departures(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "departures.jsonl"
    if out_path.exists():
        print(f"  Departures already pulled → {out_path}")
        return out_path

    # Build CIK→ticker map from century cases (only source available on VM)
    century_source = ROOT / "lab_runs" / "century_safety_source"
    cases_path = century_source / "cases.jsonl"
    cik_to_info = {}
    if cases_path.exists():
        for row in ox_lab.load_jsonl(cases_path):
            cik = row.get("cik", "")
            ticker = row.get("ticker", "")
            if cik and ticker:
                key = str(cik).zfill(10)
                cik_to_info.setdefault(key, {"ticker": ticker, "company": row.get("company", "")})

    # Use SEC EDGAR full-text search to find 8-K Item 5.02 filings
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    sample_max = config["sources"].get("departures_8k", {}).get("sample_max", 300)
    samples = []

    # Search for Item 5.02 filings from last 2 years
    import random
    for year in ["2025", "2026"]:
        if len(samples) >= sample_max:
            break
        for month in ["01", "02", "03", "04", "05", "06", "07", "08"]:
            if len(samples) >= sample_max:
                break
            try:
                search_url = (f"https://efts.sec.gov/LATEST/search-index?q=%22Item%205.02%22"
                              f"&dateRange=custom&startdt={year}-{month}-01&enddt={year}-{month}-28&pageSize=100")
                data = http.json(search_url)
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    ciks = src.get("ciks") or [str(src.get("cik", ""))]
                    accession = src.get("adsh", "")
                    for raw_cik in ciks:
                        cik = str(raw_cik).zfill(10)
                        info = cik_to_info.get(cik)
                        if info and len(samples) < sample_max:
                            samples.append({
                                "cik": cik,
                                "ticker": info["ticker"],
                                "company": info["company"],
                                "accession": accession,
                                "filed_date": src.get("file_date", ""),
                                "form": src.get("form", "8-K"),
                            })
                            break
            except Exception:
                continue

    random.shuffle(samples)

    # Fetch filing text for each
    count = 0
    for item in samples:
        try:
            acc = item["accession"].replace("-", "")
            text_url = f"https://www.sec.gov/Archives/edgar/data/{item['cik']}/{acc}/{item['accession']}.txt"
            raw = http.get(text_url, timeout=30)
            text = raw.decode("utf-8", errors="replace")
            lower = text.lower()
            idx = lower.find("item 5.02")
            if idx < 0:
                idx = lower.find("departure of directors")
            if idx >= 0:
                excerpt = text[max(0, idx - 200):idx + 3000]
            else:
                excerpt = text[:4000]
            item["filing_excerpt"] = ox_lab.clean_document(excerpt.encode("utf-8", errors="replace"))[:4000]
            ox_lab.append_jsonl(out_path, item)
            count += 1
        except Exception:
            continue
    print(f"  Departures pulled {count} filings → {out_path}")
    return out_path


def extract_departures(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "departures.jsonl"
    if not in_path.exists():
        print("  Departures: no input data")
        return
    out_path = run_dir / "departures_extracted.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("accession", ""))
    rows = ox_lab.load_jsonl(in_path)
    todo = [r for r in rows if r.get("accession") not in done and r.get("filing_excerpt")]
    print(f"  Departures extract: {len(todo)} to classify ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "departure_classify", "parameters": DEPARTURE_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = f"Company: {item.get('company','')}  |  Filed: {item.get('filed_date','')}\n\nFiling excerpt:\n{item['filing_excerpt'][:3000]}"
        try:
            raw = client.chat(
                [{"role": "system", "content": DEPARTURE_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=1000,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "departure_classify"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"departure_type": "ambiguous", "is_unexpected": False}
        result["ticker"] = item.get("ticker", "")
        result["cik"] = item.get("cik", "")
        result["accession"] = item.get("accession", "")
        result["filed_date"] = item.get("filed_date", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            result = fut.result()
            ox_lab.append_jsonl(out_path, result)
            if (i + 1) % 50 == 0:
                print(f"  Departures extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: USDA commodity exposure from 10-K segment descriptions
# ═══════════════════════════════════════════════════════════════════════════════

COMMODITY_SCHEMA = {
    "type": "object",
    "properties": {
        "commodities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "commodity": {"type": "string", "enum": [
                        "corn", "soybeans", "wheat", "cotton", "cattle", "hogs",
                        "dairy", "poultry", "rice", "sugar", "coffee", "cocoa",
                        "ethanol", "fertilizer", "crude_oil", "natural_gas",
                        "copper", "lithium", "gold", "silver", "coal", "steel",
                        "lumber", "none_of_above"
                    ]},
                    "estimated_revenue_share_pct": {"type": "number", "minimum": 0, "maximum": 100},
                    "basis": {"type": "string", "enum": ["production", "processing", "trading", "transport", "equipment", "other"]},
                },
                "required": ["commodity", "estimated_revenue_share_pct"],
                "additionalProperties": False,
            },
        },
        "is_commodity_sensitive": {"type": "boolean"},
        "primary_commodity_risk": {"type": "string"},
    },
    "required": ["commodities", "is_commodity_sensitive", "primary_commodity_risk"],
    "additionalProperties": False,
}

COMMODITY_SYSTEM = (
    "You analyze company business segment descriptions to extract commodity revenue exposure. "
    "Identify which commodities the company produces, processes, trades, transports, or sells "
    "equipment for. Estimate the percentage of total revenue tied to each commodity. "
    "Be conservative — only flag commodities explicitly mentioned in the segment description."
)


def pull_usda(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "usda_segments.jsonl"
    if out_path.exists():
        print(f"  USDA segments already pulled → {out_path}")
        return out_path
    # Use existing century pipeline cases (has snapshot_text with segment descriptions)
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    if not cases_path.exists():
        print("  USDA: no cases.jsonl found, skipping")
        return out_path

    seen_tickers = set()
    sample_max = config["sources"].get("usda_segments", {}).get("sample_max", 500)
    for row in ox_lab.load_jsonl(cases_path):
        ticker = row.get("ticker", "")
        if ticker and ticker not in seen_tickers:
            seen_tickers.add(ticker)
            text = row.get("snapshot_text", row.get("filing_text", row.get("text", "")))
            ox_lab.append_jsonl(out_path, {
                "ticker": ticker,
                "cik": row.get("cik", ""),
                "company": row.get("company", ""),
                "filing_excerpt": text[:5000] if text else "",
                "filed_date": row.get("cutoff", row.get("filed_date", "")),
            })
            if len(seen_tickers) >= sample_max:
                break
    print(f"  USDA segments pulled {len(seen_tickers)} tickers → {out_path}")
    return out_path


def extract_usda(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "usda_segments.jsonl"
    if not in_path.exists():
        print("  USDA: no input data")
        return
    out_path = run_dir / "usda_extracted.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("ticker", ""))
    rows = ox_lab.load_jsonl(in_path)
    todo = [r for r in rows if r.get("ticker") not in done and r.get("filing_excerpt")]
    print(f"  USDA extract: {len(todo)} tickers ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "commodity_exposure", "parameters": COMMODITY_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = f"Company: {item.get('company','')} ({item.get('ticker','')})\n\nBusiness description excerpt:\n{item['filing_excerpt'][:4000]}"
        try:
            raw = client.chat(
                [{"role": "system", "content": COMMODITY_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=1500,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "commodity_exposure"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"commodities": [], "is_commodity_sensitive": False, "primary_commodity_risk": ""}
        result["ticker"] = item.get("ticker", "")
        result["company"] = item.get("company", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 100 == 0:
                print(f"  USDA extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 4: Wikipedia pageview anomalies
# ═══════════════════════════════════════════════════════════════════════════════

WIKI_SCHEMA = {
    "type": "object",
    "properties": {
        "has_significant_anomaly": {"type": "boolean"},
        "anomaly_date": {"type": "string"},
        "anomaly_type": {"type": "string", "enum": ["spike", "sustained_surge", "edit_war", "vandalism", "controversy_edit", "none"]},
        "likely_cause": {"type": "string", "maxLength": 200},
        "coincides_with_known_event": {"type": "boolean"},
        "pageview_z_score": {"type": "number"},
    },
    "required": ["has_significant_anomaly", "anomaly_type", "pageview_z_score"],
    "additionalProperties": False,
}

WIKI_SYSTEM = (
    "You analyze Wikipedia pageview and edit-history anomalies for a company's article. "
    "An anomalous spike is a daily pageview count >3 standard deviations above its 90-day trailing mean. "
    "If the article shows significant anomalies, determine if they coincide with known corporate events "
    "(earnings, M&A, scandals) or if they appear to be attention-driven before disclosure. "
    "Edit wars and vandalism spikes can signal brewing controversy."
)


def pull_wikipedia(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "wikipedia_anomalies.jsonl"
    if out_path.exists():
        print(f"  Wikipedia already pulled → {out_path}")
        return out_path

    # Get tickers from century cases
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    tickers = []
    company_map = {}
    if cases_path.exists():
        seen = set()
        for row in ox_lab.load_jsonl(cases_path):
            t = row.get("ticker", "")
            if t and t not in seen:
                seen.add(t)
                tickers.append(t)
                company_map[t] = row.get("company", "")
    if not tickers:
        print("  Wikipedia: no tickers from century data, skipping")
        return out_path
    sample_max = config["sources"].get("wikipedia", {}).get("sample_max", 500)
    tickers = tickers[:sample_max]

    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.2)

    results = []
    for ticker in tickers[:sample_max]:
        company = company_map.get(ticker, ticker)
        # Try common Wikipedia article title patterns
        title = company.replace(" Inc.", "").replace(" Corp.", "").replace(" Corporation", "").strip()
        title_encoded = urllib.parse.quote(title.replace(" ", "_"))
        try:
            # Fetch pageview history
            start = "20150801"  # Wikimedia API starts here
            end = "20260824"
            url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title_encoded}/daily/{start}/{end}"
            data = http.json(url)
            items = data.get("items", [])
            if items:
                views = [it["views"] for it in items]
                # Calculate basic stats
                if len(views) > 90:
                    recent = views[-90:]
                    mean = sum(recent) / len(recent)
                    std = (sum((v - mean) ** 2 for v in recent) / len(recent)) ** 0.5
                    max_z = max((v - mean) / std for v in views[-365:]) if std > 0 else 0
                else:
                    max_z = 0
                results.append({
                    "ticker": ticker,
                    "company": company,
                    "wiki_title": title,
                    "data_points": len(items),
                    "max_pageview_z_score_1yr": round(max_z, 2),
                    "has_anomaly_candidate": max_z > 3.0,
                })
        except Exception:
            continue
        if len(results) % 50 == 0:
            print(f"  Wikipedia: {len(results)}/{len(tickers)}")

    for r in results:
        ox_lab.append_jsonl(out_path, r)
    print(f"  Wikipedia pulled {len(results)} articles → {out_path}")
    return out_path


def extract_wikipedia(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "wikipedia_anomalies.jsonl"
    if not in_path.exists():
        print("  Wikipedia: no input data")
        return
    out_path = run_dir / "wikipedia_extracted.jsonl"
    # Only classify articles that have candidate anomalies
    rows = ox_lab.load_jsonl(in_path)
    candidates = [r for r in rows if r.get("has_anomaly_candidate")]
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("ticker", ""))
    todo = [r for r in candidates if r.get("ticker") not in done]
    print(f"  Wikipedia extract: {len(todo)} anomaly candidates ({len(done)} done)")

    # For those with anomalies, get edit history too
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.2)
    tool = {"type": "function", "function": {
        "name": "wiki_anomaly", "parameters": WIKI_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        title = item["wiki_title"]
        # Get recent edits
        edit_summary = "No edit data."
        try:
            edit_url = f"https://en.wikipedia.org/w/api.php?action=query&prop=revisions&titles={urllib.parse.quote(title)}&rvlimit=10&format=json"
            edit_data = http.json(edit_url)
            pages = edit_data.get("query", {}).get("pages", {})
            if pages:
                page = list(pages.values())[0]
                revs = page.get("revisions", [])
                edit_summary = "; ".join(r.get("comment", "")[:120] for r in revs[:5])
        except Exception:
            pass

        user = (f"Company: {item.get('company','')} ({item.get('ticker','')})\n"
                f"Wikipedia article: {title}\n"
                f"Max pageview z-score (1 year): {item.get('max_pageview_z_score_1yr',0)}\n"
                f"Most recent edit summaries: {edit_summary}")
        try:
            raw = client.chat(
                [{"role": "system", "content": WIKI_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=800,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "wiki_anomaly"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"has_significant_anomaly": False, "anomaly_type": "none", "pageview_z_score": 0}
        result["ticker"] = item.get("ticker", "")
        result["company"] = item.get("company", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 50 == 0:
                print(f"  Wikipedia extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 5: Steam review bombs (gaming small caps)
# ═══════════════════════════════════════════════════════════════════════════════

STEAM_SCHEMA = {
    "type": "object",
    "properties": {
        "is_review_bomb": {"type": "boolean"},
        "bomb_onset_date": {"type": "string"},
        "primary_theme": {"type": "string", "enum": [
            "monetization_greed", "game_breaking_bugs", "server_outage",
            "performance_issues", "content_cuts", "developer_controversy",
            "balance_changes", "cheating_epidemic", "other", "none"
        ]},
        "severity_tier": {"type": "integer", "minimum": 1, "maximum": 3, "description": "1=mild/transient, 2=moderate/persistent, 3=severe/cascading"},
        "negative_review_share_peak_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "estimated_duration_days": {"type": "integer"},
        "affects_lead_revenue_title": {"type": "boolean"},
    },
    "required": ["is_review_bomb", "severity_tier"],
    "additionalProperties": False,
}

STEAM_SYSTEM = (
    "You analyze Steam game review patterns for review-bomb events that can affect "
    "small-cap gaming publisher stock prices. A review bomb is a sudden surge in "
    "negative reviews, typically >60% negative share and >3x normal review volume "
    "over a 7-day window. Classify the underlying theme and severity. "
    "Consider whether the game is a lead revenue driver for its publisher."
)

# Hardcoded small-cap gaming publisher → appid mapping (verified)
GAMING_PUBLISHERS = {
    "SNAL": {"company": "Snail Games USA", "apps": [336840, 407720, 954850]},
    "MSGM": {"company": "Motorsport Games", "apps": [958310, 1054430, 1266710]},
    "GRVY": {"company": "Gravity Co", "apps": [215880, 237550, 296510]},
    "EMBRAC-B.ST": {"company": "Embracer Group", "apps": [1029690, 750130, 356190, 883710, 1069160, 1343400]},
    "DEVO.L": {"company": "Devolver Digital", "apps": [384980, 774171, 1054120, 1207650, 1345890]},
    "TTWO": {"company": "Take-Two", "apps": [271590, 970310, 1174180, 1222680, 1544020]},
    "EA": {"company": "Electronic Arts", "apps": [1237970, 1518760, 1029690, 1172470, 1222730]},
    "UBSFY": {"company": "Ubisoft", "apps": [22300, 812140, 1262540, 1356670]},
    "CCOEF": {"company": "Capcom", "apps": [582010, 601670, 1113000, 1446780]},
    "NCBDF": {"company": "Bandai Namco", "apps": [389730, 1030290, 1091500, 1304830]},
    "PLTK": {"company": "Playtika", "apps": []},  # mobile, no Steam
    "HUYA": {"company": "HUYA", "apps": []},
    "BILI": {"company": "Bilibili", "apps": []},
    "DDI": {"company": "Doubledown", "apps": [452280]},
    "SGAMY": {"company": "Sega Sammy", "apps": [582660, 1009270, 1363080]},
    "CCOEY": {"company": "Capcom", "apps": [582010, 601670, 1113000]},
}


def pull_steam(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "steam_reviews.jsonl"
    if out_path.exists():
        print(f"  Steam already pulled → {out_path}")
        return out_path

    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.3)
    sample_max = config["sources"].get("steam", {}).get("sample_max", 300)

    # For each publisher with Steam apps, pull recent review summaries
    # and detect candidate review-bomb windows
    results = []
    for ticker, info in GAMING_PUBLISHERS.items():
        for appid in info.get("apps", []):
            try:
                # Get review summary
                url = f"https://store.steampowered.com/appreviews/{appid}?json=1&language=all&purchase_type=all&num_per_page=1"
                data = http.json(url)
                if data.get("success") == 1:
                    query_summary = data.get("query_summary", {})
                    results.append({
                        "ticker": ticker,
                        "publisher": info["company"],
                        "appid": appid,
                        "total_reviews": query_summary.get("total_reviews", 0),
                        "total_positive": query_summary.get("total_positive", 0),
                        "total_negative": query_summary.get("total_negative", 0),
                        "review_score_desc": query_summary.get("review_score_desc", ""),
                        "positive_pct": round(query_summary.get("total_positive", 0) / max(query_summary.get("total_reviews", 1), 1) * 100, 1),
                    })
            except Exception:
                continue
            time.sleep(0.3)
    # Detect potential review bombs: negative share >50% with substantial volume
    for r in results:
        neg_share = 100 - r["positive_pct"]
        r["candidate_bomb"] = neg_share > 40 and r["total_reviews"] > 50
        ox_lab.append_jsonl(out_path, r)
    print(f"  Steam pulled {len(results)} app reviews → {out_path}")
    return out_path


def extract_steam(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "steam_reviews.jsonl"
    if not in_path.exists():
        print("  Steam: no input data")
        return
    out_path = run_dir / "steam_extracted.jsonl"
    rows = ox_lab.load_jsonl(in_path)
    candidates = [r for r in rows if r.get("candidate_bomb")]
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(f"{rec.get('ticker')}_{rec.get('appid')}")
    todo = [r for r in candidates if f"{r.get('ticker')}_{r.get('appid')}" not in done]
    print(f"  Steam extract: {len(todo)} bomb candidates ({len(done)} done)")

    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.3)
    tool = {"type": "function", "function": {
        "name": "steam_review_bomb", "parameters": STEAM_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        # Pull actual review text for this app (recent negative reviews)
        review_texts = []
        appid = item["appid"]
        try:
            url = f"https://store.steampowered.com/appreviews/{appid}?json=1&filter=recent&language=english&review_type=negative&purchase_type=all&num_per_page=20"
            data = http.json(url)
            for rev in data.get("reviews", []):
                review_texts.append(rev.get("review", "")[:300])
        except Exception:
            pass
        user = (f"Publisher: {item.get('publisher','')} ({item.get('ticker','')})\n"
                f"App ID: {appid}\n"
                f"Total reviews: {item.get('total_reviews',0)}\n"
                f"Positive: {item.get('total_positive',0)} ({item.get('positive_pct',0)}%)\n"
                f"Review score: {item.get('review_score_desc','')}\n"
                f"Sample negative reviews:\n" + "\n---\n".join(review_texts[:10]))
        try:
            raw = client.chat(
                [{"role": "system", "content": STEAM_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=1000,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "steam_review_bomb"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"is_review_bomb": False, "severity_tier": 1}
        result["ticker"] = item.get("ticker", "")
        result["appid"] = item.get("appid", "")
        result["publisher"] = item.get("publisher", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 50 == 0:
                print(f"  Steam extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 6: CPSC recalls (need alias table, but try direct API)
# ═══════════════════════════════════════════════════════════════════════════════

CPSC_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["fire_burn", "child_injury", "electrocution", "chemical", "fall_hazard", "mechanical", "labeling_only", "minor"]},
        "is_voluntary": {"type": "boolean"},
        "retailer_scope": {"type": "string", "enum": ["mass_market", "specialty_only", "direct_to_consumer", "unknown"]},
        "estimated_units_affected": {"type": "integer"},
        "remediation": {"type": "string", "enum": ["refund", "replace", "repair", "dispose", "unknown"]},
    },
    "required": ["severity", "is_voluntary"],
    "additionalProperties": False,
}


def pull_cpsc(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "cpsc_recalls.jsonl"
    if out_path.exists():
        print(f"  CPSC already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.5)
    sample_max = config["sources"].get("cpsc", {}).get("sample_max", 300)
    results = []
    skip = 0
    while len(results) < sample_max:
        try:
            url = f"https://www.saferproducts.gov/RestWebServices/Recall?format=json&page_size=100&offset={skip}"
            data = json.loads(http.get(url, timeout=30))
            if not data:
                break
            for rec in data:
                # manufacturer: prefer Manufacturers[] array, else parse "X Recalls" from Title
                mfrs = rec.get("Manufacturers", [])
                if isinstance(mfrs, list) and mfrs and isinstance(mfrs[0], dict):
                    mfr = mfrs[0].get("Name", "")
                else:
                    title = rec.get("Title", "")
                    mfr = title.split(" Recalls")[0].strip() if " Recalls" in title else ""
                hazards = rec.get("Hazards", [])
                haz = hazards[0].get("Name", "") if isinstance(hazards, list) and hazards else ""
                results.append({
                    "recall_id": rec.get("RecallID", rec.get("recallID", "")),
                    "title": rec.get("Title", rec.get("title", ""))[:500],
                    "description": rec.get("Description", rec.get("description", ""))[:2000],
                    "hazard": haz,
                    "recall_date": rec.get("RecallDate", rec.get("recallDate", "")),
                    "manufacturer": mfr,
                    "retailers": str(rec.get("Retailers", rec.get("retailers", [])))[:500],
                })
            skip += 100
            if len(data) < 100:
                break
        except Exception as e:
            print(f"  CPSC error at offset {skip}: {e}")
            break
    import random
    random.shuffle(results)
    for r in results[:sample_max]:
        ox_lab.append_jsonl(out_path, r)
    print(f"  CPSC pulled {min(len(results), sample_max)} recalls → {out_path}")
    return out_path


def extract_cpsc(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "cpsc_recalls.jsonl"
    if not in_path.exists():
        print("  CPSC: no input data")
        return
    out_path = run_dir / "cpsc_extracted.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("recall_id", ""))
    rows = ox_lab.load_jsonl(in_path)
    todo = [r for r in rows if r.get("recall_id") not in done]
    print(f"  CPSC extract: {len(todo)} recalls ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "cpsc_classify", "parameters": CPSC_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = (f"Title: {item.get('title','')}\nHazard: {item.get('hazard','')}\n"
                f"Manufacturer: {item.get('manufacturer','')}\n"
                f"Description: {item.get('description','')[:1500]}")
        try:
            raw = client.chat(
                [{"role": "system", "content": "You classify CPSC product recall severity and scope."},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=600,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "cpsc_classify"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"severity": "minor", "is_voluntary": True}
        result["recall_id"] = item.get("recall_id", "")
        result["manufacturer"] = item.get("manufacturer", "")
        result["recall_date"] = item.get("recall_date", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 100 == 0:
                print(f"  CPSC extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 7: openFDA drug/device enforcement (recalls + warning letters)
# ═══════════════════════════════════════════════════════════════════════════════

FDA_SCHEMA = {
    "type": "object",
    "properties": {
        "recall_class": {"type": "string", "enum": ["Class I", "Class II", "Class III", "voluntary_market_withdrawal", "unknown"]},
        "is_lead_product": {"type": "boolean", "description": "Likely involves the company's lead/star product"},
        "severity_rationale": {"type": "string", "maxLength": 200},
        "distribution_scope": {"type": "string", "enum": ["nationwide", "multi_state", "single_state", "international", "unknown"]},
        "contamination_type": {"type": "string", "enum": ["sterility", "microbial", "chemical", "particulate", "labeling_error", "potency", "packaging", "none"]},
        "is_firm_initiated": {"type": "boolean"},
    },
    "required": ["recall_class", "is_lead_product", "is_firm_initiated"],
    "additionalProperties": False,
}

FDA_SYSTEM = (
    "You analyze FDA drug/device enforcement records. Classify each recall's severity, "
    "whether it likely involves the firm's lead product, contamination type, and scope. "
    "Lead products are typically branded drugs or Class III devices that drive the majority "
    "of a small-cap pharma/medtech company's revenue. Be conservative."
)


def pull_fda(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "fda_enforcement.jsonl"
    if out_path.exists():
        print(f"  FDA already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.3)
    sample_max = config["sources"].get("fda_recalls", {}).get("sample_max", 500)
    results = []
    for endpoint in [
        "https://api.fda.gov/drug/enforcement.json?limit=100&skip=",
        "https://api.fda.gov/device/enforcement.json?limit=100&skip=",
    ]:
        skip = 0
        while sum(1 for r in results if r.get("category") == "drug") < sample_max // 2:
            try:
                data = http.json(endpoint + str(skip))
                for rec in data.get("results", []):
                    results.append({
                        "recall_number": rec.get("recall_number", ""),
                        "category": "drug" if "drug" in endpoint else "device",
                        "product_description": rec.get("product_description", "")[:1000],
                        "reason_for_recall": rec.get("reason_for_recall", "")[:1000],
                        "recalling_firm": rec.get("recalling_firm", ""),
                        "recall_initiation_date": rec.get("recall_initiation_date", ""),
                        "classification": rec.get("classification", ""),
                        "distribution_pattern": rec.get("distribution_pattern", "")[:500],
                        "voluntary_mandated": rec.get("voluntary_mandated", ""),
                    })
                skip += 100
                if len(data.get("results", [])) < 100:
                    break
            except Exception:
                break
    import random
    random.shuffle(results)
    for r in results[:sample_max]:
        ox_lab.append_jsonl(out_path, r)
    print(f"  FDA pulled {min(len(results), sample_max)} records → {out_path}")
    return out_path


def extract_fda(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "fda_enforcement.jsonl"
    if not in_path.exists():
        print("  FDA: no input data")
        return
    out_path = run_dir / "fda_extracted.jsonl"
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("recall_number", ""))
    rows = ox_lab.load_jsonl(in_path)
    todo = [r for r in rows if r.get("recall_number") not in done]
    print(f"  FDA extract: {len(todo)} records ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "fda_classify", "parameters": FDA_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = (f"Product: {item.get('product_description','')}\n"
                f"Reason: {item.get('reason_for_recall','')}\n"
                f"Firm: {item.get('recalling_firm','')}\n"
                f"Classification: {item.get('classification','')}\n"
                f"Distribution: {item.get('distribution_pattern','')}")
        try:
            raw = client.chat(
                [{"role": "system", "content": FDA_SYSTEM},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=800,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "fda_classify"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"recall_class": "unknown", "is_lead_product": False, "is_firm_initiated": False}
        result["recall_number"] = item.get("recall_number", "")
        result["recalling_firm"] = item.get("recalling_firm", "")
        result["recall_initiation_date"] = item.get("recall_initiation_date", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 100 == 0:
                print(f"  FDA extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 8: SBIR Phase transitions
# ═══════════════════════════════════════════════════════════════════════════════

SBIR_SCHEMA = {
    "type": "object",
    "properties": {
        "sector": {"type": "string", "enum": ["defense", "biotech", "materials", "energy", "software", "robotics", "aerospace", "other"]},
        "phase_transition_detected": {"type": "boolean", "description": "Phase II completion followed by non-SBIR obligation within 12 months"},
        "commercial_readiness_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "is_dual_use": {"type": "boolean"},
    },
    "required": ["sector", "phase_transition_detected"],
    "additionalProperties": False,
}


def pull_sbir(config: dict, run_dir: Path) -> Path:
    out_path = run_dir / "sbir_awards.jsonl"
    if out_path.exists():
        print(f"  SBIR already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.5)
    sample_max = config["sources"].get("sbir", {}).get("sample_max", 500)
    try:
        raw = http.get("https://data.www.sbir.gov/mod_awarddatapublic/award_data.csv", timeout=180)
        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        results = []
        for row in reader:
            phase = row.get("Phase", row.get("phase", ""))
            if "II" in phase:
                results.append({
                    "firm": row.get("Firm", row.get("firm", row.get("Company", ""))),
                    "phase": phase,
                    "agency": row.get("Agency", row.get("agency", "")),
                    "award_year": row.get("Award Year", row.get("award_year", row.get("Year", ""))),
                    "amount": row.get("Award Amount", row.get("award_amount", "0")),
                    "title": row.get("Title", row.get("title", ""))[:500],
                })
        import random
        random.shuffle(results)
        for r in results[:sample_max]:
            ox_lab.append_jsonl(out_path, r)
        print(f"  SBIR pulled {len(results)} Phase II awards → {out_path}")
    except Exception as e:
        print(f"  SBIR pull failed: {e}")
    return out_path


def extract_sbir(client: sa.OpenRouter, config: dict, run_dir: Path):
    in_path = run_dir / "sbir_awards.jsonl"
    if not in_path.exists():
        print("  SBIR: no input data")
        return
    out_path = run_dir / "sbir_extracted.jsonl"
    # Dedup by firm
    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(rec.get("firm", ""))
    rows = ox_lab.load_jsonl(in_path)
    # Group by firm, take one per firm
    by_firm = {}
    for r in rows:
        firm = r.get("firm", "")
        if firm and firm not in by_firm:
            by_firm[firm] = r
    todo = [v for k, v in by_firm.items() if k not in done]
    print(f"  SBIR extract: {len(todo)} firms ({len(done)} done)")

    tool = {"type": "function", "function": {
        "name": "sbir_classify", "parameters": SBIR_SCHEMA, "strict": True,
    }}

    def classify_one(item):
        user = (f"Firm: {item.get('firm','')}\nPhase: {item.get('phase','')}\n"
                f"Agency: {item.get('agency','')}\nTitle: {item.get('title','')[:300]}")
        try:
            raw = client.chat(
                [{"role": "system", "content": "Classify this SBIR award recipient by sector and phase transition potential."},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=500,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "sbir_classify"}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"sector": "other", "phase_transition_detected": False}
        result["firm"] = item.get("firm", "")
        result["phase"] = item.get("phase", "")
        result["agency"] = item.get("agency", "")
        return result

    concurrency = config.get("llm_concurrency", 128)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, item): item for item in todo}
        for i, fut in enumerate(cf.as_completed(futures)):
            ox_lab.append_jsonl(out_path, fut.result())
            if (i + 1) % 100 == 0:
                print(f"  SBIR extract: {i+1}/{len(todo)}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════

PULL_JOBS = [
    ("noaa_storms", pull_noaa),
    ("departures_8k", pull_departures),
    ("usda_segments", pull_usda),
    ("wikipedia", pull_wikipedia),
    ("steam", pull_steam),
    ("cpsc", pull_cpsc),
    ("fda_recalls", pull_fda),
    ("sbir", pull_sbir),
]

EXTRACT_JOBS = [
    ("noaa_storms", extract_noaa),
    ("departures_8k", extract_departures),
    ("usda_segments", extract_usda),
    ("wikipedia", extract_wikipedia),
    ("steam", extract_steam),
    ("cpsc", extract_cpsc),
    ("fda_recalls", extract_fda),
    ("sbir", extract_sbir),
]


def cmd_pull(config: dict, run_dir: Path):
    for name, fn in PULL_JOBS:
        src = config["sources"].get(name, {})
        if not src.get("enabled", True):
            print(f"SKIP {name} (disabled)")
            continue
        print(f"\n=== PULL {name} ===")
        t0 = time.monotonic()
        try:
            fn(config, run_dir)
            print(f"  {name}: {time.monotonic()-t0:.1f}s")
        except Exception as e:
            print(f"  {name} FAILED: {e}")


def cmd_extract(config: dict, run_dir: Path):
    api_key = sa.get_api_key()
    if not api_key:
        print("NO OPENROUTER_API_KEY SET - refusing to run extraction (would pollute outputs)")
        return
    client = sa.OpenRouter(api_key, model=config.get("model", DEFAULT_MODEL),
                            timeout=120, max_retries=3)
    for name, fn in EXTRACT_JOBS:
        src = config["sources"].get(name, {})
        if not src.get("enabled", True):
            print(f"SKIP {name} extract (disabled)")
            continue
        print(f"\n=== EXTRACT {name} ===")
        t0 = time.monotonic()
        try:
            fn(client, config, run_dir)
            print(f"  {name}: {time.monotonic()-t0:.1f}s  |  LLM calls: {client.call_count}")
        except Exception as e:
            print(f"  {name} EXTRACT FAILED: {e}")


def cmd_run(config: dict, run_dir: Path):
    cmd_pull(config, run_dir)
    cmd_extract(config, run_dir)
    print(f"\n=== RUN COMPLETE ===\nRun dir: {run_dir}")
    # Print summary
    for name, _ in EXTRACT_JOBS:
        out_path = run_dir / f"{name}_extracted.jsonl"
        if out_path.exists():
            count = sum(1 for _ in ox_lab.load_jsonl(out_path))
            print(f"  {name}: {count} records extracted")


def cmd_export(config: dict, run_dir: Path):
    """Compress and upload to Google Drive."""
    drive_remote = config.get("drive_remote", "gdrive:alphaHunt/unstructured_proto")
    import tempfile
    # Create a tar.zst archive
    archive = ROOT / "archives" / "unstructured_proto.tar.zst"
    archive.parent.mkdir(exist_ok=True)
    subprocess.run(["tar", "-cf", "-", "-C", str(run_dir), "."], stdout=subprocess.PIPE, check=False)
    result = subprocess.run(["tar", "-c", "-I", "zstd", "-f", str(archive), "-C", str(run_dir.parent), run_dir.name],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Archive failed: {result.stderr}")
        return
    # Upload
    subprocess.run(["rclone", "copy", str(archive), f"{drive_remote}/"], check=False)
    print(f"Exported to {drive_remote}/unstructured_proto.tar.zst")


def main():
    parser = argparse.ArgumentParser(description="Unstructured alpha prototype pipeline")
    parser.add_argument("command", choices=["pull", "extract", "run", "export"])
    parser.add_argument("--config", default="config/unstructured_proto.json")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    config_path = ROOT / args.config
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)
    config = load_config(config_path)

    if args.api_key:
        os.environ["OPENROUTER_API_KEY"] = args.api_key

    run_dir = ROOT / config["run_dir"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cache" / "http").mkdir(parents=True, exist_ok=True)

    if args.command == "pull":
        cmd_pull(config, run_dir)
    elif args.command == "extract":
        cmd_extract(config, run_dir)
    elif args.command == "run":
        cmd_run(config, run_dir)
    elif args.command == "export":
        cmd_export(config, run_dir)


if __name__ == "__main__":
    main()