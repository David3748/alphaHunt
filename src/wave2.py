#!/usr/bin/env python3
"""wave2.py — second-wave alpha sources: SEC comment letters, Form 4 insider
footnotes, Form D raises, FDA orphan designations, NHTSA recalls, and crt.sh
certificate-transparency subdomain diffing.

Subcommands: pull | extract | run
"""

import argparse
import concurrent.futures as cf
import csv
import io
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import subagents as sa
import ox_lab

WRITE_LOCK = threading.Lock()

SEC_UA = {"User-Agent": "alphaHunt research contact@example.com"}


def fetch_filing_text(http, cik, adsh):
    """Fetch the primary document text for an EDGAR filing (cik 10-padded, adsh with dashes)."""
    cik = str(cik).zfill(10)
    nodash = adsh.replace("-", "")
    try:
        idx = http.json(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/index.json")
    except Exception:
        return None
    items = idx.get("directory", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]
    # Prefer primary doc: filename containing the accession, or .htm/.txt/.xml non-graphic
    candidates = [it for it in items if it.get("name", "").lower().endswith((".htm", ".txt", ".xml", ".html"))]
    if not candidates:
        return None
    # sort: prefer raw XML, then .htm/.html letter, deprioritize .txt (old-format index) and xsl
    def score(it):
        n = it.get("name", "")
        s = 0
        nl = n.lower()
        if nl.endswith(".xml") and "xsl" not in nl:
            s += 4
        if nl.endswith((".htm", ".html")):
            s += 3
        if nl.endswith(".txt"):
            s += 1
        if adsh.split("-")[-1] in n:
            s += 2
        if "index" in nl or "header" in nl or "xsl" in nl or nl.endswith(".hdr.sgml"):
            s -= 6
        return s
    candidates.sort(key=score, reverse=True)
    doc = candidates[0]["name"]
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{doc}"
    try:
        raw = http.get(url, timeout=60)
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def clean_text(raw):
    """Clean a fetched EDGAR document. Handles full-submission .txt files that
    wrap the letter in an SGML envelope (<SEC-HEADER> ... <DOCUMENT><TEXT>)."""
    if "<SEC-HEADER>" in raw:
        m = re.search(r"</SEC-HEADER>(.*)", raw, re.S)
        if m:
            raw = m.group(1)
    texts = re.findall(r"<TEXT>(.*?)</TEXT>", raw, re.S | re.I)
    if texts:
        raw = "\n".join(texts)
    try:
        return ox_lab.clean_document(raw.encode("utf-8", errors="replace"))
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)[:8000]


# ═══════════════════════════════════════════════════════════════════════════════
# B — SEC COMMENT LETTERS
# ═══════════════════════════════════════════════════════════════════════════════

COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["sec_comment_letter", "company_response", "other"]},
        "concern_topics": {"type": "array", "items": {"type": "string", "enum": [
            "revenue_recognition", "non_gaap_metrics", "md_and_a", "segment_reporting",
            "related_party", "goodwill_impairment", "internal_controls", "liquidity_going_concern",
            "exec_compensation", "restatement_risk", "segment_geography", "other", "none"]}},
        "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                     "description": "1=routine, 3=substantive accounting question, 5=serious/restatement-risk"},
        "response_evasive": {"type": "boolean", "description": "If company response, is it evasive or incomplete?"},
        "key_issue": {"type": "string", "maxLength": 300},
    },
    "required": ["doc_type", "concern_topics", "severity"],
    "additionalProperties": False,
}

COMMENT_SYSTEM = (
    "You analyze SEC comment-letter correspondence. Identify whether the document is the SEC "
    "staff's comment letter or the company's response, list the accounting/disclosure topics, "
    "and rate severity. For company responses, flag evasiveness or incomplete answers — "
    "evasive responses to serious questions signal restatement/control risk."
)


def pull_comment_letters(config, run_dir, cik_to_info):
    out_path = run_dir / "comment_letters.jsonl"
    if out_path.exists():
        print(f"  comment_letters already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    samples = []
    seen = set()
    for year in ["2024", "2025", "2026"]:
        for month in ["01", "04", "07", "10"]:
            if len(samples) >= 800:
                break
            try:
                url = (f"https://efts.sec.gov/LATEST/search-index?q=%22comment+letter%22"
                       f"&dateRange=custom&startdt={year}-{month}-01&enddt={year}-{month}-28&pageSize=100")
                data = http.json(url)
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    adsh = src.get("adsh", "")
                    form = src.get("form", "")
                    ciks = src.get("ciks") or []
                    if not ciks or not adsh or adsh in seen:
                        continue
                    if form != "CORRESP":
                        continue
                    seen.add(adsh)
                    matched = None
                    for raw_cik in ciks:
                        c = str(raw_cik).zfill(10)
                        if c in cik_to_info:
                            matched = c
                            break
                    if not matched:
                        continue
                    info = cik_to_info[matched]
                    samples.append({
                        "adsh": adsh, "cik": matched,
                        "ticker": info.get("ticker", ""),
                        "company": info.get("company", ""),
                        "form": form,
                        "file_date": src.get("file_date", ""),
                    })
            except Exception:
                continue
    count = 0
    for item in samples:
        text = fetch_filing_text(http, item["cik"], item["adsh"])
        if text:
            item["text"] = clean_text(text)[:6000]
            ox_lab.append_jsonl(out_path, item)
            count += 1
    print(f"  comment_letters pulled {count} → {out_path}")
    return out_path


def extract_comment_letters(client, config, run_dir):
    in_path = run_dir / "comment_letters.jsonl"
    if not in_path.exists():
        print("  comment_letters: no input")
        return
    out_path = run_dir / "comment_letters_extracted.jsonl"
    done = {r.get("adsh", "") for r in _read(out_path)}
    todo = [r for r in _read(in_path) if r.get("adsh") not in done and r.get("text")]
    print(f"  comment_letters extract: {len(todo)} ({len(done)} done)")
    tool = {"type": "function", "function": {"name": "comment_classify", "parameters": COMMENT_SCHEMA, "strict": True}}

    def one(item):
        user = (f"Company: {item.get('company','')} ({item.get('ticker','')})\n"
                f"Form type: {item.get('form','')}\nFiled: {item.get('file_date','')}\n\n{item['text'][:5000]}")
        try:
            raw = client.chat([{"role": "system", "content": COMMENT_SYSTEM},
                               {"role": "user", "content": user}],
                              temperature=0.05, max_tokens=1000, tools=[tool],
                              tool_choice={"type": "function", "function": {"name": "comment_classify"}})
            res = json.loads(raw)
        except Exception:
            res = {"doc_type": "other", "concern_topics": [], "severity": 0}
        res["adsh"] = item.get("adsh"); res["ticker"] = item.get("ticker")
        res["file_date"] = item.get("file_date")
        return res

    _run_pool(one, todo, out_path, config.get("llm_concurrency", 128), "comment_letters")


# ═══════════════════════════════════════════════════════════════════════════════
# C — FORM 4 FOOTNOTES (10b5-1 vs discretionary)
# ═══════════════════════════════════════════════════════════════════════════════

FORM4_SCHEMA = {
    "type": "object",
    "properties": {
        "has_transactions": {"type": "boolean"},
        "dominant_direction": {"type": "string", "enum": ["open_market_buy", "open_market_sell", "grant_award", "option_exercise", "mixed", "none"]},
        "is_10b5_1_scheduled": {"type": "boolean", "description": "Sales are under a pre-arranged 10b5-1 plan"},
        "is_discretionary": {"type": "boolean", "description": "Sales are discretionary market sales, NOT 10b5-1"},
        "aggregate_value": {"type": "number", "description": "Approx total $ if determinable, else 0"},
        "officer_role": {"type": "string", "enum": ["CEO", "CFO", "director", "other_officer", "unknown"]},
        "key_evidence": {"type": "string", "maxLength": 300},
    },
    "required": ["has_transactions", "dominant_direction", "is_10b5_1_scheduled", "is_discretionary"],
    "additionalProperties": False,
}

FORM4_SYSTEM = (
    "You analyze SEC Form 4 insider-transaction filings. Determine the dominant direction "
    "(open-market buy/sell, grant, exercise), and crucially whether dispositions are under a "
    "Rule 10b5-1 pre-arranged plan (footnotes say '10b5-1', 'pre-arranged', 'scheduled') or are "
    "discretionary market sales. Discretionary insider selling is a stronger bearish signal than "
    "scheduled 10b5-1 sales."
)


def parse_form4(text):
    footnotes = re.findall(r"<footnote\b[^>]*>(.*?)</footnote>", text, re.S | re.I)
    footnotes = [re.sub(r"<[^>]+>", " ", f).strip()[:800] for f in footnotes]
    codes = re.findall(r"<transactionCode>(.*?)</transactionCode>", text, re.S | re.I)
    shares = re.findall(r"<transactionShares>\s*<value>([\d.]+)</value>", text, re.S | re.I)
    prices = re.findall(r"<transactionPricePerShare>\s*<value>([\d.]+)</value>", text, re.S | re.I)
    return footnotes, codes, shares, prices


def pull_form4(config, run_dir, cik_to_info):
    out_path = run_dir / "form4.jsonl"
    if out_path.exists():
        print(f"  form4 already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    samples = []
    seen = set()
    for year in ["2025", "2026"]:
        for month in ["01", "02", "03", "04", "05", "06", "07", "08"]:
            if len(samples) >= 900:
                break
            try:
                url = (f"https://efts.sec.gov/LATEST/search-index?q=form%3A4"
                       f"&dateRange=custom&startdt={year}-{month}-01&enddt={year}-{month}-28&pageSize=100")
                data = http.json(url)
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    adsh = src.get("adsh", "")
                    ciks = src.get("ciks") or []
                    if not ciks or not adsh or adsh in seen:
                        continue
                    seen.add(adsh)
                    matched = None
                    for raw_cik in ciks:
                        c = str(raw_cik).zfill(10)
                        if c in cik_to_info:
                            matched = c
                            break
                    if not matched:
                        continue
                    info = cik_to_info[matched]
                    samples.append({"adsh": adsh, "cik": matched, "ticker": info.get("ticker", ""),
                                    "company": info.get("company", ""), "file_date": src.get("file_date", "")})
            except Exception:
                continue
    count = 0
    for item in samples:
        text = fetch_filing_text(http, item["cik"], item["adsh"])
        if text:
            fn, codes, shares, prices = parse_form4(text)
            if fn or codes:
                item["footnotes"] = fn
                item["codes"] = codes
                item["shares"] = shares
                item["prices"] = prices
                ox_lab.append_jsonl(out_path, item)
                count += 1
    print(f"  form4 pulled {count} → {out_path}")
    return out_path


def extract_form4(client, config, run_dir):
    in_path = run_dir / "form4.jsonl"
    if not in_path.exists():
        print("  form4: no input")
        return
    out_path = run_dir / "form4_extracted.jsonl"
    done = {r.get("adsh", "") for r in _read(out_path)}
    todo = [r for r in _read(in_path) if r.get("adsh") not in done]
    print(f"  form4 extract: {len(todo)} ({len(done)} done)")
    tool = {"type": "function", "function": {"name": "form4_classify", "parameters": FORM4_SCHEMA, "strict": True}}

    def one(item):
        user = (f"Company: {item.get('company','')} ({item.get('ticker','')})\n"
                f"Filed: {item.get('file_date','')}\n"
                f"Transaction codes: {item.get('codes','')}\nShares: {item.get('shares','')}\nPrices: {item.get('prices','')}\n"
                f"Footnotes:\n" + "\n".join(item.get("footnotes", [])[:10]))
        try:
            raw = client.chat([{"role": "system", "content": FORM4_SYSTEM},
                               {"role": "user", "content": user}],
                              temperature=0.05, max_tokens=800, tools=[tool],
                              tool_choice={"type": "function", "function": {"name": "form4_classify"}})
            res = json.loads(raw)
        except Exception:
            res = {"has_transactions": False, "dominant_direction": "none", "is_10b5_1_scheduled": False, "is_discretionary": False}
        res["adsh"] = item.get("adsh"); res["ticker"] = item.get("ticker"); res["file_date"] = item.get("file_date")
        return res

    _run_pool(one, todo, out_path, config.get("llm_concurrency", 128), "form4")


# ═══════════════════════════════════════════════════════════════════════════════
# C — FORM D (private placements)
# ═══════════════════════════════════════════════════════════════════════════════

FORMD_SCHEMA = {
    "type": "object",
    "properties": {
        "offering_type": {"type": "string", "enum": ["equity", "debt", "convertible", "option_warrant", "other_security", "unknown"]},
        "is_public_company": {"type": "boolean"},
        "offering_amount": {"type": "number"},
        "dilution_signal": {"type": "string", "enum": ["high_dilution", "moderate", "neutral", "distress_financing", "unknown"]},
        "key_evidence": {"type": "string", "maxLength": 300},
    },
    "required": ["offering_type", "dilution_signal"],
    "additionalProperties": False,
}

FORMD_SYSTEM = (
    "You analyze SEC Form D notices of exempt securities offerings. Classify the offering type "
    "and whether it signals dilution or distress for a public company (convertible/equity at "
    "deep discount = distress; equity with premium investors = confidence)."
)


def pull_formd(config, run_dir, cik_to_info):
    out_path = run_dir / "formd.jsonl"
    if out_path.exists():
        print(f"  formd already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    samples = []
    seen = set()
    for year in ["2025", "2026"]:
        if len(samples) >= 500:
            break
        for month in ["01", "04", "07", "10"]:
            if len(samples) >= 500:
                break
            try:
                url = (f"https://efts.sec.gov/LATEST/search-index?q=%22form+D%22"
                       f"&dateRange=custom&startdt={year}-{month}-01&enddt={year}-{month}-28&pageSize=100")
                data = http.json(url)
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    adsh = src.get("adsh", "")
                    form = src.get("form", "")
                    ciks = src.get("ciks") or []
                    if not ciks or not adsh or adsh in seen:
                        continue
                    if form not in ("D", "D/A"):
                        continue
                    seen.add(adsh)
                    matched = None
                    for raw_cik in ciks:
                        c = str(raw_cik).zfill(10)
                        if c in cik_to_info:
                            matched = c
                            break
                    if not matched:
                        continue
                    info = cik_to_info[matched]
                    samples.append({"adsh": adsh, "cik": matched, "ticker": info.get("ticker", ""),
                                    "company": info.get("company", ""), "file_date": src.get("file_date", "")})
            except Exception:
                continue
    count = 0
    for item in samples:
        text = fetch_filing_text(http, item["cik"], item["adsh"])
        if text:
            item["text"] = clean_text(text)[:4000]
            ox_lab.append_jsonl(out_path, item)
            count += 1
    print(f"  formd pulled {count} → {out_path}")
    return out_path


def extract_formd(client, config, run_dir):
    in_path = run_dir / "formd.jsonl"
    if not in_path.exists():
        print("  formd: no input")
        return
    out_path = run_dir / "formd_extracted.jsonl"
    done = {r.get("adsh", "") for r in _read(out_path)}
    todo = [r for r in _read(in_path) if r.get("adsh") not in done and r.get("text")]
    print(f"  formd extract: {len(todo)} ({len(done)} done)")
    tool = {"type": "function", "function": {"name": "formd_classify", "parameters": FORMD_SCHEMA, "strict": True}}

    def one(item):
        user = f"Company: {item.get('company','')} ({item.get('ticker','')})\nFiled: {item.get('file_date','')}\n\n{item['text'][:3500]}"
        try:
            raw = client.chat([{"role": "system", "content": FORMD_SYSTEM},
                               {"role": "user", "content": user}],
                              temperature=0.05, max_tokens=600, tools=[tool],
                              tool_choice={"type": "function", "function": {"name": "formd_classify"}})
            res = json.loads(raw)
        except Exception:
            res = {"offering_type": "unknown", "dilution_signal": "unknown"}
        res["adsh"] = item.get("adsh"); res["ticker"] = item.get("ticker"); res["file_date"] = item.get("file_date")
        return res

    _run_pool(one, todo, out_path, config.get("llm_concurrency", 128), "formd")


# ═══════════════════════════════════════════════════════════════════════════════
# B — FDA ORPHAN DRUG DESIGNATIONS
# ═══════════════════════════════════════════════════════════════════════════════

FDAORPHAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sponsor_is_public": {"type": "boolean"},
        "designation": {"type": "string", "enum": ["orphan_drug", "breakthrough", "fast_track", "priority_review", "other"]},
        "therapeutic_area": {"type": "string", "enum": ["oncology", "rare_disease", "neurology", "infectious_disease", "cardiovascular", "other"]},
        "significance": {"type": "integer", "minimum": 1, "maximum": 5,
                         "description": "1=minor, 5=major milestone for the sponsor"},
    },
    "required": ["designation", "significance"],
    "additionalProperties": False,
}


def pull_fda_orphan(config, run_dir):
    out_path = run_dir / "fda_orphan.jsonl"
    if out_path.exists():
        print(f"  fda_orphan already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.4)
    results = []
    skip = 0
    while skip < 2000 and len(results) < 600:
        try:
            data = http.json(f"https://api.fda.gov/drug/drugsfda.json?limit=100&skip={skip}")
            for rec in data.get("results", []):
                products = rec.get("products", [])
                subs = rec.get("submissions", [])
                brand = ""
                generic = ""
                if products:
                    brand = products[0].get("brand_name", "")
                    generic = products[0].get("generic_name", "")
                latest_status = ""
                priority = ""
                if subs:
                    latest_status = subs[-1].get("submission_status", "")
                    priority = subs[-1].get("review_priority", "")
                results.append({
                    "sponsor": rec.get("openfda", {}).get("manufacturer_name", [""])[0] if rec.get("openfda", {}).get("manufacturer_name") else "",
                    "brand_name": brand,
                    "generic_name": generic,
                    "status": latest_status,
                    "priority": priority,
                    "application_number": rec.get("application_number", ""),
                })
            skip += 100
            if len(data.get("results", [])) < 100:
                break
        except Exception as e:
            print(f"  fda_orphan error at skip {skip}: {e}")
            break
    for r in results:
        ox_lab.append_jsonl(out_path, r)
    print(f"  fda_orphan pulled {len(results)} → {out_path}")
    return out_path


def extract_fda_orphan(client, config, run_dir):
    in_path = run_dir / "fda_orphan.jsonl"
    if not in_path.exists():
        print("  fda_orphan: no input")
        return
    out_path = run_dir / "fda_orphan_extracted.jsonl"
    rows = _read(in_path)
    done = set()
    for r in _read(out_path):
        done.add(f"{r.get('sponsor')}_{r.get('generic_name')}")
    todo = [r for r in rows if f"{r.get('sponsor')}_{r.get('generic_name')}" not in done]
    print(f"  fda_orphan extract: {len(todo)} ({len(done)} done)")
    tool = {"type": "function", "function": {"name": "fda_orphan_classify", "parameters": FDAORPHAN_SCHEMA, "strict": True}}

    def one(item):
        user = (f"Sponsor: {item.get('sponsor','')}\nBrand: {item.get('brand_name','')}\n"
                f"Generic: {item.get('generic_name','')}\nStatus: {item.get('status','')}\n"
                f"Review priority: {item.get('priority','')}")
        try:
            raw = client.chat([{"role": "system", "content": "Classify this FDA drug product approval by therapeutic area and significance to the sponsor."},
                               {"role": "user", "content": user}],
                              temperature=0.05, max_tokens=500, tools=[tool],
                              tool_choice={"type": "function", "function": {"name": "fda_orphan_classify"}})
            res = json.loads(raw)
        except Exception:
            res = {"designation": "other", "significance": 1}
        res["sponsor"] = item.get("sponsor"); res["generic_name"] = item.get("generic_name")
        return res

    _run_pool(one, todo, out_path, config.get("llm_concurrency", 128), "fda_orphan")


# ═══════════════════════════════════════════════════════════════════════════════
# crt — CERTIFICATE TRANSPARENCY SUBDOMAIN DIFFING
# ═══════════════════════════════════════════════════════════════════════════════

CRT_SCHEMA = {
    "type": "object",
    "properties": {
        "has_launch_signal": {"type": "boolean", "description": "New subdomains suggest new product/brand/initiative"},
        "has_mna_signal": {"type": "boolean", "description": "Subdomains referencing another company/entity suggest M&A"},
        "subdomain_themes": {"type": "array", "items": {"type": "string", "enum": [
            "api", "shop_store", "app", "staging_dev", "corp_site", "careers_hr", "customer_portal",
            "acquisition_related", "new_brand", "infrastructure", "other"]}},
        "recent_activity": {"type": "integer", "minimum": 1, "maximum": 5,
                            "description": "1=dormant, 5=very active new cert issuance"},
        "key_evidence": {"type": "string", "maxLength": 300},
    },
    "required": ["has_launch_signal", "has_mna_signal", "recent_activity"],
    "additionalProperties": False,
}

CRT_SYSTEM = (
    "You analyze a company's TLS certificate transparency data (recently issued subdomains). "
    "New subdomains reveal upcoming product/brand launches, acquisitions, or infrastructure "
    "changes before press releases. Classify the semantic themes of the subdomains."
)


def _guess_domains(company):
    c = company.lower()
    c = c.split("/")[0]  # strip state-of-incorporation suffix like "/DE/"
    for suffix in [" inc", " corp", " corporation", " ltd", " llc", " co", " group", " holdings",
                   " technologies", " technology", " international", " plc", " limited", "the "]:
        c = c.replace(suffix, "")
    c = re.sub(r"[^a-z0-9]", "", c)
    if len(c) < 3:
        return []
    return [f"{c}.com", f"{c}.io", f"{c}corp.com", f"{c}inc.com"]


def pull_crt(config, run_dir, companies):
    out_path = run_dir / "crt_subdomains.jsonl"
    if out_path.exists():
        print(f"  crt already pulled → {out_path}")
        return out_path
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=1.0)
    results = []
    seen_domains = set()
    for ticker, company in companies.items():
        for domain in _guess_domains(company):
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            try:
                url = f"https://crt.sh/?q=%25.{domain}&output=json"
                data = http.json(url)
                if not data:
                    continue
                names = set()
                recent = 0
                for cert in data:
                    nv = cert.get("name_value", "")
                    for n in nv.split("\n"):
                        n = n.strip().lower()
                        if n and n.endswith(domain):
                            names.add(n)
                    nb = cert.get("not_before", "")
                    if nb and nb >= "2024-01-01":
                        recent += 1
                if names:
                    results.append({"ticker": ticker, "company": company, "domain": domain,
                                    "subdomains": sorted(names)[:60], "recent_cert_count": recent})
            except Exception:
                continue
        if len(results) % 25 == 0:
            print(f"  crt: {len(results)} domains resolved")
    for r in results:
        ox_lab.append_jsonl(out_path, r)
    print(f"  crt pulled {len(results)} domains → {out_path}")
    return out_path


def extract_crt(client, config, run_dir):
    in_path = run_dir / "crt_subdomains.jsonl"
    if not in_path.exists():
        print("  crt: no input")
        return
    out_path = run_dir / "crt_extracted.jsonl"
    done = {r.get("domain", "") for r in _read(out_path)}
    todo = [r for r in _read(in_path) if r.get("domain") not in done]
    print(f"  crt extract: {len(todo)} ({len(done)} done)")
    tool = {"type": "function", "function": {"name": "crt_classify", "parameters": CRT_SCHEMA, "strict": True}}

    def one(item):
        subs = item.get("subdomains", [])
        sub_text = "\n".join(subs[:50])
        user = (f"Company: {item.get('company','')} ({item.get('ticker','')})\nDomain: {item.get('domain','')}\n"
                f"Recent certs (2024+): {item.get('recent_cert_count',0)}\nSubdomains:\n{sub_text}")
        try:
            raw = client.chat([{"role": "system", "content": CRT_SYSTEM},
                               {"role": "user", "content": user}],
                              temperature=0.05, max_tokens=800, tools=[tool],
                              tool_choice={"type": "function", "function": {"name": "crt_classify"}})
            res = json.loads(raw)
        except Exception:
            res = {"has_launch_signal": False, "has_mna_signal": False, "recent_activity": 1}
        res["ticker"] = item.get("ticker"); res["domain"] = item.get("domain")
        return res

    _run_pool(one, todo, out_path, config.get("llm_concurrency", 128), "crt")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS + MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def _read(path):
    if not path.exists():
        return []
    return ox_lab.load_jsonl(path)


def _run_pool(fn, items, out_path, concurrency, label):
    count = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(fn, it): it for it in items}
        for fut in cf.as_completed(futures):
            ox_lab.append_jsonl(out_path, fut.result())
            count += 1
            if count % 100 == 0:
                print(f"  {label} extract: {count}/{len(items)}")


def build_cik_map():
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    cik_to_info = {}
    if cases_path.exists():
        for row in ox_lab.load_jsonl(cases_path):
            cik = str(row.get("cik", "")).zfill(10)
            ticker = row.get("ticker", "")
            if cik and ticker:
                cik_to_info.setdefault(cik, {"ticker": ticker, "company": row.get("company", "")})
    return cik_to_info


def build_company_map():
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    companies = {}
    if cases_path.exists():
        for row in ox_lab.load_jsonl(cases_path):
            t = row.get("ticker", "")
            c = row.get("company", "")
            if t and c and t not in companies:
                companies[t] = c
    return companies


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["pull", "extract", "run"])
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    ap.add_argument("--concurrency", type=int, default=128)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    config = {"llm_concurrency": args.concurrency}
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cache" / "http").mkdir(parents=True, exist_ok=True)

    cik_to_info = build_cik_map()
    companies = build_company_map()
    print(f"cik map: {len(cik_to_info)}, companies: {len(companies)}")

    pull_jobs = [
        ("comment_letters", lambda: pull_comment_letters(config, run_dir, cik_to_info)),
        ("form4", lambda: pull_form4(config, run_dir, cik_to_info)),
        ("formd", lambda: pull_formd(config, run_dir, cik_to_info)),
        ("fda_orphan", lambda: pull_fda_orphan(config, run_dir)),
    ]
    extract_jobs = [
        ("comment_letters", lambda c: extract_comment_letters(c, config, run_dir)),
        ("form4", lambda c: extract_form4(c, config, run_dir)),
        ("formd", lambda c: extract_formd(c, config, run_dir)),
        ("fda_orphan", lambda c: extract_fda_orphan(c, config, run_dir)),
    ]

    if args.command in ("pull", "run"):
        for name, fn in pull_jobs:
            print(f"\n=== PULL {name} ===")
            t0 = time.monotonic()
            try:
                fn()
                print(f"  {name}: {time.monotonic()-t0:.1f}s")
            except Exception as e:
                print(f"  {name} FAILED: {e}")

    if args.command in ("extract", "run"):
        api_key = sa.get_api_key(args.api_key)
        if not api_key:
            print("NO OPENROUTER_API_KEY - refusing extract")
            return
        client = sa.OpenRouter(api_key, model=args.model or sa.DEFAULT_MODEL,
                               timeout=120, max_retries=3)
        for name, fn in extract_jobs:
            print(f"\n=== EXTRACT {name} ===")
            t0 = time.monotonic()
            try:
                fn(client)
                print(f"  {name}: {time.monotonic()-t0:.1f}s  |  calls {client.calls}")
            except Exception as e:
                print(f"  {name} EXTRACT FAILED: {e}")


if __name__ == "__main__":
    main()