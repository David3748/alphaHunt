#!/usr/bin/env python3
"""Discover obscure public evidence and rank potentially underreacted long catalysts."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

import ox_lab as ox
import subagents as sa

WRITE_LOCK = threading.Lock()
ROBOTS_LOCK = threading.Lock()
ROBOTS = {}
GDELT_LOCK = threading.Lock()
GDELT_LAST_REQUEST = 0.0

ANALYSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "fact": {"type": "string"}, "exact_quote": {"type": "string"},
        "source_credibility_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "novelty_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "estimated_materiality_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "direction": {"type": "string", "enum": ["bullish", "bearish", "mixed", "irrelevant"]},
        "mechanism": {"type": "string"}, "catalyst_window_days": {"type": ["integer", "null"]},
        "why_market_may_have_missed_it": {"type": "string"},
        "entity_link_confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "requires_verification": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["fact", "exact_quote", "source_credibility_pct", "novelty_pct",
                 "estimated_materiality_pct", "direction", "mechanism", "catalyst_window_days",
                 "why_market_may_have_missed_it", "entity_link_confidence_pct", "requires_verification"]
}

SKEPTIC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "independent_source_needed": {"type": "boolean"},
        "already_known_likelihood_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "priced_in_likelihood_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "strongest_alternative_explanation": {"type": "string"},
        "fatal_flaw": {"type": ["string", "null"]},
        "verdict": {"type": "string", "enum": ["reject", "verify", "candidate"]}
    },
    "required": ["independent_source_needed", "already_known_likelihood_pct",
                 "priced_in_likelihood_pct", "strongest_alternative_explanation", "fatal_flaw", "verdict"]
}

CONSENSUS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["reject", "watch", "long_candidate"]},
        "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "underreaction_probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "thesis": {"type": "string"}, "catalyst": {"type": "string"},
        "invalidation": {"type": "string"},
        "verification_steps": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["decision", "confidence_pct", "underreaction_probability_pct", "thesis",
                 "catalyst", "invalidation", "verification_steps"]
}

ANALYST_SYSTEM = """You analyze obscure public evidence for long-only equity research.
Use only the supplied timestamped document. Extract one concrete fact with an exact quote.
Do not claim something is unpriced; estimate why it might have been missed and demand verification.
Reject generic commentary, stock promotion, and facts with an uncertain issuer link."""

SKEPTIC_SYSTEM = """You are the adversarial second pass. Given a public document and a
first-pass equity interpretation, decide whether the item is stale, syndicated, promotional,
already obvious, weakly linked, immaterial, or plausibly worth independent verification.
Do not use later knowledge. Return the required structured result only."""

CONSENSUS_SYSTEM = """You are the final long-only research gate. Reconcile the source-grounded
analyst and adversarial skeptic. An obscure fact is not automatically unpriced. Reject weak entity
links, promotional sources, stale facts, and theses without a forcing catalyst. Return the required
structured result only and preserve explicit verification steps."""


def load_jsonl(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with WRITE_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def issuer_seeds(path: Path, limit: int) -> list[dict]:
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            ticker = row.get("ticker")
            if ticker and (ticker not in latest or row.get("cutoff", "") > latest[ticker].get("cutoff", "")):
                latest[ticker] = {k: row.get(k) for k in ("ticker", "company", "cik", "cutoff")}
    # Start with issuers that have the most recent eligible evidence. Alphabetic
    # truncation overweights obscure A-tickers and produced very sparse current-
    # web discovery in the first queued pass.
    return sorted(latest.values(), key=lambda r: (r.get("cutoff") or "", r["ticker"]),
                  reverse=True)[:limit]


def gdelt_query(company: str, terms: list[str], maximum: int) -> list[dict]:
    global GDELT_LAST_REQUEST
    company = re.sub(r"\b(inc|corp|corporation|company|ltd|plc)\.?\b", "", company,
                     flags=re.I).strip(" ,.")
    query = f'"{company}" ("' + '" OR "'.join(terms) + '")'
    params = {"query": query, "mode": "artlist", "maxrecords": str(maximum),
              "format": "json", "sort": "datedesc", "timespan": "3months"}
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "alphaHunt research contact research@example.com"})
    for attempt in range(4):
        try:
            with GDELT_LOCK:
                wait = 5.2 - (time.monotonic() - GDELT_LAST_REQUEST)
                if wait > 0: time.sleep(wait)
                GDELT_LAST_REQUEST = time.monotonic()
                with urllib.request.urlopen(req, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload.get("articles") or []
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(6 * (attempt + 1))
                continue
            return []
        except Exception:
            return []
    return []


def get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "alphaHunt/1.0 public research"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalized_company(value: str) -> str:
    return re.sub(r"\b(inc|corp|corporation|company|ltd|plc)\.?\b", "", value,
                  flags=re.I).strip(" ,.")


def api_discoveries(seed: dict, provider: str, maximum: int) -> list[dict]:
    company = normalized_company(seed.get("company") or seed["ticker"])
    encoded = urllib.parse.quote(company)
    rows = []
    try:
        if provider == "federal_register":
            url = ("https://www.federalregister.gov/api/v1/documents.json?per_page="
                   f"{maximum}&order=newest&conditions%5Bterm%5D={encoded}")
            for item in get_json(url).get("results") or []:
                rows.append({"source_family": "federal_register", "url": item.get("html_url"),
                             "title": item.get("title"), "published_at": item.get("publication_date"),
                             "inline_text": json.dumps(item, ensure_ascii=False)})
        elif provider == "clinical_trials":
            params = urllib.parse.urlencode({"query.term": company, "pageSize": maximum, "format": "json"})
            for item in get_json("https://clinicaltrials.gov/api/v2/studies?" + params).get("studies") or []:
                protocol = item.get("protocolSection") or {}
                identification = protocol.get("identificationModule") or {}
                status = protocol.get("statusModule") or {}
                nct = identification.get("nctId")
                rows.append({"source_family": "clinical_trials", "url": f"https://clinicaltrials.gov/study/{nct}",
                             "title": identification.get("briefTitle"),
                             "published_at": (status.get("studyFirstPostDateStruct") or {}).get("date"),
                             "inline_text": json.dumps(item, ensure_ascii=False)})
        elif provider in {"openfda_drug", "openfda_device"}:
            kind = "drug" if provider.endswith("drug") else "device"
            search = f'recalling_firm:"{company}"'
            params = urllib.parse.urlencode({"search": search, "limit": maximum})
            endpoint = f"https://api.fda.gov/{kind}/enforcement.json?{params}"
            for item in get_json(endpoint).get("results") or []:
                event = item.get("event_id") or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]
                rows.append({"source_family": provider, "url": endpoint + f"#event={event}",
                             "title": f"{kind} enforcement {event}: {item.get('reason_for_recall', '')[:120]}",
                             "published_at": item.get("report_date"),
                             "inline_text": json.dumps(item, ensure_ascii=False)})
        elif provider == "news_rss":
            query = urllib.parse.quote(f'"{company}" (contract OR permit OR trial OR recall OR award OR supplier)')
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            req = urllib.request.Request(url, headers={"User-Agent": "alphaHunt/1.0 public research"})
            with urllib.request.urlopen(req, timeout=30) as response:
                root = ET.fromstring(response.read())
            for item in root.findall("./channel/item")[:maximum]:
                title = item.findtext("title") or ""
                rows.append({"source_family": "news_rss", "url": item.findtext("link"),
                             "title": title, "published_at": item.findtext("pubDate"),
                             "inline_text": title + "\n" + (item.findtext("description") or "")})
    except Exception:
        return []
    return [row for row in rows if row.get("url")]


def discover_seed(seed: dict, config: dict) -> list[dict]:
    maximum = int(config["articles_per_query"])
    found = []
    # Run specialist APIs first; threads do useful work while other jobs wait
    # behind the provider-compliant serialized GDELT lane.
    for provider in config.get("discovery_providers", []):
        if provider != "gdelt":
            found.extend(api_discoveries(seed, provider, maximum))
    if "gdelt" in config.get("discovery_providers", []):
        terms = sorted({term for values in config["query_families"].values() for term in values})
        for article in gdelt_query(seed.get("company") or seed["ticker"], terms, maximum):
            found.append({"source_family": "gdelt_broad_web", "url": article.get("url"),
                          "domain": article.get("domain"), "title": article.get("title"),
                          "published_at": article.get("seendate")})
    return [row for row in found if row.get("url")]


def discover(config: dict, root: Path) -> dict:
    run_dir = root / config["run_dir"]
    output = run_dir / "discoveries.jsonl"
    seen = {row["url"] for row in load_jsonl(output)}
    seeds = issuer_seeds(root / config["source_cases"], int(config["max_issuers"]))
    jobs = seeds
    added = 0
    with cf.ThreadPoolExecutor(max_workers=int(config.get("discovery_concurrency", 8))) as pool:
        futures = {pool.submit(discover_seed, seed, config): seed for seed in jobs}
        for future in cf.as_completed(futures):
            seed = futures[future]
            for item in future.result():
                url = item["url"]
                if url in seen or not url.startswith(("http://", "https://")):
                    continue
                seen.add(url); added += 1
                append(output, {"ticker": seed["ticker"], "company": seed.get("company"),
                                "cik": seed.get("cik"), "discovered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                "discovery_provider": item.get("source_family"), **item})
    return {"issuers": len(seeds), "provider_queries": len(jobs) * len(config.get("discovery_providers", [])),
            "added": added, "total": len(seen)}


def fetch_one(row: dict) -> dict:
    inline = re.sub(r"\s+", " ", html.unescape(str(row.get("inline_text") or ""))).strip()
    if len(inline) >= 300:
        digest = hashlib.sha256(inline.encode()).hexdigest()
        return {**row, "content_hash": digest, "text": inline[:120_000],
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "fetch_error": None}
    parsed = urllib.parse.urlsplit(row["url"])
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    with ROBOTS_LOCK:
        robot = ROBOTS.get(parsed.netloc)
        if robot is None:
            robot = urllib.robotparser.RobotFileParser(robots_url)
            try: robot.read()
            except Exception: robot = None
            ROBOTS[parsed.netloc] = robot
    if robot is not None and not robot.can_fetch("alphaHunt/1.0", row["url"]):
        return {**row, "fetch_error": "robots_disallowed"}
    req = urllib.request.Request(row["url"], headers={"User-Agent": "alphaHunt/1.0 public research"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read(3_000_000)
            content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {**row, "fetch_error": f"unsupported content type: {content_type}"}
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()
        text = re.sub(r"\s+", " ", html.unescape(soup.get_text(" "))).strip()
        if len(text) < 500:
            return {**row, "fetch_error": "insufficient public text"}
        digest = hashlib.sha256(text.encode()).hexdigest()
        return {**row, "content_hash": digest, "text": text[:120_000],
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "fetch_error": None}
    except Exception as exc:
        return {**row, "fetch_error": f"{type(exc).__name__}: {exc}"}


def fetch(config: dict, root: Path) -> dict:
    run_dir = root / config["run_dir"]
    output = run_dir / "documents.jsonl"
    done = {row["url"] for row in load_jsonl(output)}
    pending = [row for row in load_jsonl(run_dir / "discoveries.jsonl") if row["url"] not in done]
    hashes = {row.get("content_hash") for row in load_jsonl(output) if row.get("content_hash")}
    ok = duplicate = failed = 0
    with cf.ThreadPoolExecutor(max_workers=int(config["fetch_concurrency"])) as pool:
        for row in pool.map(fetch_one, pending):
            digest = row.get("content_hash")
            if digest and digest in hashes:
                duplicate += 1
                append(output, {k: row.get(k) for k in row if k != "text"} | {"duplicate": True})
            else:
                if digest: hashes.add(digest)
                ok += int(bool(digest)); failed += int(not digest)
                append(output, row)
    return {"pending": len(pending), "ok": ok, "duplicates": duplicate, "failed": failed}


def analyze_one(client: sa.OpenRouter, cache: ox.LLMCache, row: dict) -> dict:
    evidence = (f"TICKER: {row['ticker']}\nCOMPANY: {row.get('company')}\n"
                f"PUBLISHED: {row.get('published_at')}\nSOURCE: {row['url']}\n\n"
                f"DOCUMENT:\n{row['text'][:50_000]}")
    first = cache.call_json(client, ANALYST_SYSTEM, evidence, "obscure_analyst_v1",
                            ANALYSIS_SCHEMA, effort="medium")
    skeptic = cache.call_json(client, SKEPTIC_SYSTEM,
                              evidence[:35_000] + "\n\nFIRST PASS:\n" + json.dumps(first),
                              "obscure_skeptic_v1", SKEPTIC_SCHEMA, effort="medium")
    consensus = cache.call_json(client, CONSENSUS_SYSTEM,
                                evidence[:20_000] + "\n\nANALYST:\n" + json.dumps(first) +
                                "\n\nSKEPTIC:\n" + json.dumps(skeptic),
                                "obscure_consensus_v1", CONSENSUS_SCHEMA, effort="medium")
    score = ((first["novelty_pct"] * first["estimated_materiality_pct"] *
              first["source_credibility_pct"] * first["entity_link_confidence_pct"]) ** .25
             * (1 - skeptic["already_known_likelihood_pct"] / 100)
             * (1 - skeptic["priced_in_likelihood_pct"] / 100))
    return {k: row.get(k) for k in row if k != "text"} | {
        "analyzed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "analysis": first, "skeptic": skeptic, "consensus": consensus,
        "underreaction_score": score}


def analyze(config: dict, root: Path) -> dict:
    run_dir = root / config["run_dir"]
    output = run_dir / "analyses.jsonl"
    done = {row["content_hash"] for row in load_jsonl(output)}
    documents = [row for row in load_jsonl(run_dir / "documents.jsonl")
                 if row.get("content_hash") and row["content_hash"] not in done]
    documents = documents[:int(config["documents_for_analysis"])]
    client = sa.OpenRouter(sa.get_api_key(), config["model"], timeout=120, max_retries=3)
    cache = ox.LLMCache(run_dir / "cache" / "llm")
    with cf.ThreadPoolExecutor(max_workers=int(config["llm_concurrency"])) as pool:
        futures = [pool.submit(analyze_one, client, cache, row) for row in documents]
        for future in cf.as_completed(futures):
            try: append(output, future.result())
            except Exception as exc: append(run_dir / "analysis_failures.jsonl", {"error": str(exc)})
    return {"documents": len(documents), "analyses": len(load_jsonl(output)), "calls": client.calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("discover", "fetch", "analyze", "play"))
    parser.add_argument("--config", type=Path, default=Path("config/obscure_miner.json"))
    args = parser.parse_args(); root = Path(__file__).resolve().parent.parent
    config = json.loads(args.config.read_text())
    result = {}
    stages = ("discover", "fetch", "analyze") if args.command == "play" else (args.command,)
    for stage in stages:
        result[stage] = globals()[stage](config, root)
        print(json.dumps({stage: result[stage]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
