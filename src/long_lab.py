#!/usr/bin/env python3
"""Historical false-distress long forecasting lab for Ox Alpha.

The experiment starts with liquid listed common stocks trading at least 40%
below their trailing one-year high. Using only filings available at an
historical cutoff, three independent analysts assess survivability, operating
inflection, and value-trap risk. A fourth call synthesizes a calibrated long
probability. Market outcomes are calculated deterministically.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox
import subagents as sa


EXPERIMENT = "false_distress_long_v1"
DEFAULT_RUN_DIR = Path("lab_runs/long_dev")
LISTED_EXCHANGES = {"NMS", "NYQ", "NCM", "NGM", "ASE", "NASDAQ", "NYSE"}
ROLE_NAMES = ("survival", "inflection", "skeptic")


EVIDENCE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source": {"type": "string"},
        "quote": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["source", "quote", "why"],
}

SURVIVAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "survival_probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "dilution_probability_90d_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "cash_runway_months": {"type": ["number", "null"], "minimum": 0},
        "liquidity_assessment": {"type": "string"},
        "balance_sheet_risks": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": EVIDENCE_ITEM},
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["survival_probability_pct", "dilution_probability_90d_pct",
                 "cash_runway_months", "liquidity_assessment", "balance_sheet_risks",
                 "evidence", "missing_information"],
}

INFLECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "inflection_probability_90d_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "fundamental_direction": {"type": "string", "enum": ["improving", "stable", "deteriorating"]},
        "primary_catalyst": {"type": "string"},
        "catalyst_timing_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 180},
        "operating_drivers": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": EVIDENCE_ITEM},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["inflection_probability_90d_pct", "fundamental_direction",
                 "primary_catalyst", "catalyst_timing_days", "operating_drivers",
                 "evidence", "risks"],
}

SKEPTIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value_trap_probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["reject", "caution", "survives"]},
        "strongest_kill_factor": {"type": "string"},
        "hidden_risks": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": EVIDENCE_ITEM},
        "what_would_change_view": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["value_trap_probability_pct", "verdict", "strongest_kill_factor",
                 "hidden_risks", "evidence", "what_would_change_view"],
}

SYNTH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "long_success_probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "decision": {"type": "string", "enum": ["reject", "watch", "candidate"]},
        "primary_catalyst": {"type": "string"},
        "expected_horizon_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 180},
        "invalidation": {"type": "string"},
        "rationale": {"type": "string"},
        "key_evidence": {"type": "array", "items": EVIDENCE_ITEM},
        "analyst_disagreements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["long_success_probability_pct", "decision", "primary_catalyst",
                 "expected_horizon_days", "invalidation", "rationale", "key_evidence",
                 "analyst_disagreements"],
}

ROLE_SCHEMAS = {
    "survival": SURVIVAL_SCHEMA,
    "inflection": INFLECTION_SCHEMA,
    "skeptic": SKEPTIC_SCHEMA,
}

COMMON_SYSTEM = """You are working in a leakage-controlled historical equity experiment.
Use ONLY the supplied filing text and market state available at the cutoff. Do not use remembered company identity,
prices, events, or filings after the cutoff. The issuer identity is deliberately redacted. Quote only supplied text.
The candidate is already at least 40% below its trailing one-year high. A cheap-looking stock is not automatically a
long: distinguish recoverable operational distress from terminal impairment. Return only the required tool result."""

ROLE_SYSTEMS = {
    "survival": COMMON_SYSTEM + """

Act as the balance-sheet and capital-structure analyst. Determine whether the issuer can survive the next 12 months
without insolvency or a destructive financing. Estimate cash runway only when supported. Treat near-term dilution,
debt maturities, covenants, going-concern language, negative working capital, and dependence on capital markets as
major risks. A high survival score requires evidence, not merely the absence of an explicit warning.""",
    "inflection": COMMON_SYSTEM + """

Act as the operating-inflection and catalyst analyst. Look for concrete evidence that conditions are improving:
sequential margins, backlog conversion, cost reductions, utilization, customer wins, product/regulatory milestones,
refinancing, or management actions likely to become visible within 90-180 days. Penalize vague hopes, distant TAM,
and catalysts without dates or funding. Judge whether the evidence can plausibly rerate the equity within 90 days.""",
    "skeptic": COMMON_SYSTEM + """

Act as an adversarial long-idea killer. Assume the drawdown may be deserved. Find dilution, insolvency, covenant,
customer-concentration, accounting, delisting, litigation, competitive, and management-credibility risks. Identify the
single strongest reason the apparent recovery will fail. Only use evidence in the supplied documents.""",
}

SYNTH_SYSTEM = """You are the final calibrated portfolio researcher in a historical experiment. You receive three
independent analyses of a stock already down at least 40% from its one-year high. Estimate the probability that, over
the next 90 calendar days, the stock will outperform SPY by at least 20 percentage points while never closing 25% or
more below the post-filing entry price. Use base rates: dramatic rebounds are uncommon. Reject issuers with high
dilution/value-trap risk unless a specific, funded, near-term catalyst dominates. Do not use outside knowledge or infer
issuer identity. Return only the required tool result."""


def primary_universe(path: Path) -> list[dict]:
    raw = json.loads(path.read_text())
    seen, rows = set(), []
    for row in raw:
        cik = str(row.get("cik", ""))
        if not cik or cik in seen:
            continue
        seen.add(cik)
        name, ticker = row.get("name", ""), row.get("ticker", "")
        if not ticker or re.search(
                r"\bacquisition\b.*\bcorp(?:oration)?\b|\bblank check\b|\btrust\b|"
                r"\betf\b|\bexchange[- ]traded fund\b", name, re.I):
            continue
        if re.search(r"-(?:P[A-Z]?|WT|UN|RI)$", ticker, re.I):
            continue
        rows.append(row)
    return rows


def chart_series(ticker: str, start: dt.date, end: dt.date,
                 http: ox.CachedHTTP) -> tuple[list[dict], dict]:
    p1 = int(dt.datetime.combine(start, dt.time(), tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ox.urllib.parse.quote(ticker)}"
           f"?period1={p1}&period2={p2}&interval=1d&events=history")
    data = http.json(url)
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return [], {}
    ts = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    closes = adj if len(adj) == len(ts) else quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = []
    for i, stamp in enumerate(ts):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        rows.append({
            "date": dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).date(),
            "close": float(close),
            "volume": float(volumes[i] or 0) if i < len(volumes) else 0.0,
        })
    return rows, result.get("meta") or {}


def first_after(rows: list[dict], day: dt.date) -> dict | None:
    return next((row for row in rows if row["date"] > day), None)


def first_on_or_after(rows: list[dict], day: dt.date) -> dict | None:
    return next((row for row in rows if row["date"] >= day), None)


def market_case(stock: list[dict], spy: list[dict], cutoff: dt.date) -> dict | None:
    history = [r for r in stock if cutoff - dt.timedelta(days=370) <= r["date"] <= cutoff]
    entry = first_after(stock, cutoff)
    spy_entry = first_after(spy, cutoff)
    if len(history) < 120 or not entry or not spy_entry:
        return None
    peak = max(r["close"] for r in history)
    pre_close = history[-1]["close"]
    recent = history[-30:]
    avg_dollar_volume = statistics.mean(r["close"] * r["volume"] for r in recent)
    exchange = ""
    result = {
        "pre_cutoff_close": pre_close,
        "entry_date": entry["date"].isoformat(),
        "entry_close": entry["close"],
        "drawdown_from_1y_high": pre_close / peak - 1.0 if peak else None,
        "avg_dollar_volume_30d": avg_dollar_volume,
    }
    bases = (entry["close"], spy_entry["close"])
    future_stock = [r for r in stock if entry["date"] <= r["date"] <= cutoff + dt.timedelta(days=190)]
    future_spy = [r for r in spy if spy_entry["date"] <= r["date"] <= cutoff + dt.timedelta(days=190)]
    for horizon in (30, 90, 180):
        end = cutoff + dt.timedelta(days=horizon)
        s = first_on_or_after(future_stock, end)
        b = first_on_or_after(future_spy, end)
        if s and b:
            stock_return = s["close"] / bases[0] - 1.0
            spy_return = b["close"] / bases[1] - 1.0
            result[f"stock_return_{horizon}d"] = stock_return
            result[f"spy_return_{horizon}d"] = spy_return
            result[f"relative_return_{horizon}d"] = (1 + stock_return) / (1 + spy_return) - 1.0
        else:
            result[f"stock_return_{horizon}d"] = None
            result[f"spy_return_{horizon}d"] = None
            result[f"relative_return_{horizon}d"] = None
    path90 = [r for r in future_stock if r["date"] <= cutoff + dt.timedelta(days=90)]
    spy_by_date = {r["date"]: r["close"] for r in future_spy}
    minimum_return, hit_up, hit_down = 0.0, None, None
    for row in path90:
        stock_return = row["close"] / bases[0] - 1.0
        minimum_return = min(minimum_return, stock_return)
        spy_close = spy_by_date.get(row["date"])
        if spy_close:
            relative = (row["close"] / bases[0]) / (spy_close / bases[1]) - 1.0
            if relative >= 0.20 and hit_up is None:
                hit_up = row["date"]
        if stock_return <= -0.25 and hit_down is None:
            hit_down = row["date"]
    result["max_drawdown_from_entry_90d"] = minimum_return
    result["hit_plus20_before_minus25"] = bool(hit_up and (not hit_down or hit_up < hit_down))
    rel90 = result.get("relative_return_90d")
    result["long_success"] = bool(rel90 is not None and rel90 >= 0.20 and minimum_return > -0.25)
    return result


def build_company_case(company: dict, http: ox.CachedHTTP, spy: list[dict], seed: int,
                       start: dt.date, end: dt.date, min_drawdown: float,
                       min_dollar_volume: float, min_price: float) -> dict | None:
    ticker, cik = company.get("ticker", ""), str(company.get("cik", ""))
    try:
        sec_name, filings = ox.submission_rows(cik, http, include_archives=False)
        stock, meta = chart_series(ticker, start - dt.timedelta(days=400),
                                   end + dt.timedelta(days=200), http)
    except Exception:
        return None
    exchange = str(meta.get("exchangeName") or meta.get("fullExchangeName") or "").upper()
    if exchange not in LISTED_EXCHANGES:
        return None
    anchors = [r for r in filings if r["form"] in ox.ANCHOR_FORMS
               and start <= dt.date.fromisoformat(r["date"]) <= end]
    issuer_seed = int(hashlib.sha256(f"{seed}|{cik}".encode()).hexdigest()[:16], 16)
    random.Random(issuer_seed).shuffle(anchors)
    chosen = None
    for anchor in anchors:
        cutoff = dt.date.fromisoformat(anchor["date"])
        market = market_case(stock, spy, cutoff)
        if not market:
            continue
        if (market["drawdown_from_1y_high"] <= min_drawdown
                and market["avg_dollar_volume_30d"] >= min_dollar_volume
                and market["pre_cutoff_close"] >= min_price
                and market.get("relative_return_90d") is not None):
            chosen = (anchor, cutoff, market)
            break
    if not chosen:
        return None
    anchor, cutoff, market = chosen
    prior_start = cutoff - dt.timedelta(days=180)
    prior = [r for r in filings if prior_start <= dt.date.fromisoformat(r["date"]) <= cutoff
             and r["form"] in ox.CONTEXT_FORMS and r["accessionNumber"] != anchor["accessionNumber"]]
    prior.sort(key=lambda r: r["date"], reverse=True)
    snapshot_rows = [anchor] + prior[:4]
    names = [ticker, cik, company.get("name", ""), sec_name]
    snapshot = ox.make_pack(snapshot_rows, http, names, include_exhibits=True,
                            per_filing_chars=220_000, total_chars=700_000)
    if len(snapshot) < 2_000:
        return None
    case_id = hashlib.sha256(f"{EXPERIMENT}|{cik}|{cutoff}".encode()).hexdigest()[:16]
    split_bucket = int(hashlib.sha256(f"split|{case_id}".encode()).hexdigest()[:8], 16) % 10
    return {
        "experiment": EXPERIMENT,
        "case_id": case_id,
        "ticker": ticker,
        "company": sec_name or company.get("name", ""),
        "cik": cik,
        "cutoff": cutoff.isoformat(),
        "split": "development" if split_bucket < 6 else "validation",
        "anchor_form": anchor["form"],
        "anchor_accession": anchor["accessionNumber"],
        "snapshot_sources": [{"form": r["form"], "date": r["date"],
                              "accession": r["accessionNumber"]} for r in snapshot_rows],
        "snapshot_text": snapshot,
        "market_at_cutoff": {k: market[k] for k in (
            "pre_cutoff_close", "entry_date", "entry_close", "drawdown_from_1y_high",
            "avg_dollar_volume_30d")},
        "outcome": {k: v for k, v in market.items() if k not in {
            "pre_cutoff_close", "entry_date", "entry_close", "drawdown_from_1y_high",
            "avg_dollar_volume_30d"}},
        "selection_note": "Current listed ticker universe; historical survivorship bias remains.",
    }


def build_cases(universe_path: Path, run_dir: Path, count: int, seed: int,
                start: dt.date, end: dt.date, concurrency: int = 8,
                candidate_limit: int = 5000, min_drawdown: float = -0.40,
                min_dollar_volume: float = 1_000_000, min_price: float = 1.0) -> list[dict]:
    path = run_dir / "cases.jsonl"
    existing = ox.load_jsonl(path)
    if len(existing) >= count:
        return existing[:count]
    existing_ids = {r["case_id"] for r in existing}
    existing_ciks = {r["cik"] for r in existing}
    universe = primary_universe(universe_path)
    random.Random(seed).shuffle(universe)
    candidates = [r for r in universe[:candidate_limit] if str(r.get("cik", "")) not in existing_ciks]
    http = ox.CachedHTTP(run_dir / "cache" / "http")
    spy, _ = chart_series("SPY", start - dt.timedelta(days=400),
                          end + dt.timedelta(days=200), http)
    made, index = len(existing), 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        while made < count and index < len(candidates):
            needed = count - made
            batch_size = min(len(candidates) - index, max(concurrency * 3, min(needed * 5, concurrency * 8)))
            batch = candidates[index:index + batch_size]
            index += batch_size
            futures = {pool.submit(build_company_case, row, http, spy, seed, start, end,
                                   min_drawdown, min_dollar_volume, min_price): row for row in batch}
            for future in cf.as_completed(futures):
                try:
                    record = future.result()
                except Exception as exc:
                    print(f"candidate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue
                if not record or record["case_id"] in existing_ids or made >= count:
                    continue
                ox.append_jsonl(path, record)
                existing.append(record)
                existing_ids.add(record["case_id"])
                made += 1
                m = record["market_at_cutoff"]
                print(f"built {made}/{count}: {record['ticker']} cutoff={record['cutoff']} "
                      f"drawdown={m['drawdown_from_1y_high']:.0%} adv=${m['avg_dollar_volume_30d']:,.0f}",
                      file=sys.stderr)
    if len(existing) < count:
        print(f"warning: built only {len(existing)}/{count} cases", file=sys.stderr)
    return existing[:count]


def role_user(case: dict, role: str) -> str:
    market = case["market_at_cutoff"]
    return (
        f"ROLE: {role}\nCUTOFF: {case['cutoff']}\nANCHOR FORM: {case['anchor_form']}\n"
        f"MARKET STATE KNOWN AT CUTOFF: price={market['pre_cutoff_close']:.4g}; "
        f"drawdown from trailing one-year high={market['drawdown_from_1y_high']:.1%}; "
        f"30-day average dollar volume=${market['avg_dollar_volume_30d']:,.0f}.\n\n"
        f"HISTORICAL FILINGS:\n{case['snapshot_text']}"
    )


def synthesis_user(case: dict, analyses: dict) -> str:
    market = case["market_at_cutoff"]
    return (
        f"CUTOFF: {case['cutoff']}\nKNOWN MARKET STATE: "
        f"drawdown={market['drawdown_from_1y_high']:.1%}, price={market['pre_cutoff_close']:.4g}, "
        f"average dollar volume=${market['avg_dollar_volume_30d']:,.0f}.\n\n"
        f"INDEPENDENT ANALYSES:\n{json.dumps(analyses, separators=(',', ':'))}"
    )


def completed_role_keys(path: Path) -> set[tuple[str, str]]:
    return {(r.get("case_id"), r.get("role")) for r in ox.load_jsonl(path)
            if isinstance(r.get("result"), dict)}


def run_roles(cases: list[dict], run_dir: Path, client: sa.OpenRouter,
              concurrency: int) -> list[dict]:
    path = run_dir / "analyses.jsonl"
    done = completed_role_keys(path)
    cache = ox.LLMCache(run_dir / "cache" / "llm")
    jobs = [(case, role) for case in cases for role in ROLE_NAMES
            if (case["case_id"], role) not in done]

    def work(job):
        case, role = job
        result = cache.call_json(client, ROLE_SYSTEMS[role], role_user(case, role),
                                 f"false_distress_{role}", ROLE_SCHEMAS[role], effort="high")
        return {"experiment": EXPERIMENT, "case_id": case["case_id"], "role": role,
                "model": client.model, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "result": result}

    if not jobs:
        return ox.load_jsonl(path)
    print(f"analyses: {len(jobs)} calls at concurrency {concurrency}", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        completed = 0
        for future in cf.as_completed(futures):
            case, role = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                print(f"analysis failed {case['ticker']} {role}: {exc}", file=sys.stderr)
                continue
            ox.append_jsonl(path, row)
            completed += 1
            print(f"analysis {completed}/{len(jobs)}: {case['ticker']} {role}", file=sys.stderr)
    return ox.load_jsonl(path)


def run_synthesis(cases: list[dict], run_dir: Path, client: sa.OpenRouter,
                  concurrency: int) -> list[dict]:
    path = run_dir / "syntheses.jsonl"
    done = {r.get("case_id") for r in ox.load_jsonl(path) if isinstance(r.get("result"), dict)}
    by_case = defaultdict(dict)
    for row in ox.load_jsonl(run_dir / "analyses.jsonl"):
        if row.get("role") in ROLE_NAMES and isinstance(row.get("result"), dict):
            by_case[row["case_id"]][row["role"]] = row["result"]
    jobs = [case for case in cases if case["case_id"] not in done
            and all(role in by_case[case["case_id"]] for role in ROLE_NAMES)]
    cache = ox.LLMCache(run_dir / "cache" / "llm")

    def work(case):
        result = cache.call_json(client, SYNTH_SYSTEM,
                                 synthesis_user(case, by_case[case["case_id"]]),
                                 "false_distress_synthesis", SYNTH_SCHEMA, effort="high")
        return {"experiment": EXPERIMENT, "case_id": case["case_id"],
                "model": client.model, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "result": result}

    if not jobs:
        return ox.load_jsonl(path)
    print(f"synthesis: {len(jobs)} calls at concurrency {concurrency}", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(work, case): case for case in jobs}
        completed = 0
        for future in cf.as_completed(futures):
            case = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                print(f"synthesis failed {case['ticker']}: {exc}", file=sys.stderr)
                continue
            ox.append_jsonl(path, row)
            completed += 1
            print(f"synthesis {completed}/{len(jobs)}: {case['ticker']}", file=sys.stderr)
    return ox.load_jsonl(path)


def mean(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(vals) if vals else None


def basket_metrics(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "tickers": [r["ticker"] for r in rows],
        "mean_relative_return_90d": mean(r["relative_return_90d"] for r in rows),
        "median_relative_return_90d": statistics.median(r["relative_return_90d"] for r in rows) if rows else None,
        "positive_fraction": mean(1.0 if r["relative_return_90d"] > 0 else 0.0 for r in rows),
        "long_success_rate": mean(1.0 if r["long_success"] else 0.0 for r in rows),
        "mean_max_drawdown_90d": mean(r["max_drawdown_from_entry_90d"] for r in rows),
    }


def score_run(run_dir: Path, split: str = "all") -> dict:
    cases = {r["case_id"]: r for r in ox.load_jsonl(run_dir / "cases.jsonl")
             if split == "all" or r.get("split") == split}
    synth = {r["case_id"]: r["result"] for r in ox.load_jsonl(run_dir / "syntheses.jsonl")
             if isinstance(r.get("result"), dict)}
    analyses = defaultdict(dict)
    for row in ox.load_jsonl(run_dir / "analyses.jsonl"):
        if row.get("case_id") in cases and isinstance(row.get("result"), dict):
            analyses[row["case_id"]][row.get("role")] = row["result"]
    scored = []
    for case_id, case in cases.items():
        result = synth.get(case_id)
        probability = result.get("long_success_probability_pct") if result else None
        outcome = case.get("outcome") or {}
        if not isinstance(probability, (int, float)) or outcome.get("relative_return_90d") is None:
            continue
        role_data = analyses.get(case_id, {})
        scored.append({
            "case_id": case_id, "ticker": case["ticker"], "company": case["company"],
            "cutoff": case["cutoff"], "split": case.get("split"),
            "probability": probability / 100.0, "decision": result.get("decision"),
            "primary_catalyst": result.get("primary_catalyst"),
            "invalidation": result.get("invalidation"),
            "survival_probability": (role_data.get("survival") or {}).get("survival_probability_pct"),
            "dilution_probability": (role_data.get("survival") or {}).get("dilution_probability_90d_pct"),
            "inflection_probability": (role_data.get("inflection") or {}).get("inflection_probability_90d_pct"),
            "value_trap_probability": (role_data.get("skeptic") or {}).get("value_trap_probability_pct"),
            **case["market_at_cutoff"], **outcome,
        })
    scored.sort(key=lambda r: r["probability"], reverse=True)
    points = [(r["probability"], 1 if r["long_success"] else 0) for r in scored]
    base_rate = mean(y for _, y in points)
    brier = mean((p - y) ** 2 for p, y in points)
    base_brier = mean((base_rate - y) ** 2 for _, y in points) if base_rate is not None else None
    top_n = max(1, math.ceil(len(scored) * 0.10)) if scored else 0
    top_decile = scored[:top_n]
    baseline = basket_metrics(scored)
    top = basket_metrics(top_decile)
    threshold_baskets = []
    for threshold in (0.7, 0.6, 0.5, 0.4):
        rows = [r for r in scored if r["probability"] >= threshold]
        if rows:
            threshold_baskets.append({"threshold": threshold, **basket_metrics(rows)})
    metrics = {
        "experiment": EXPERIMENT, "split": split,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cases_available": len(cases), "cases_scored": len(scored),
        "base_success_rate": base_rate, "brier": brier, "base_rate_brier": base_brier,
        "brier_skill_score": (1 - brier / base_brier) if brier is not None and base_brier else None,
        "auc": ox.auc_score(points), "baseline": baseline, "top_decile": top,
        "threshold_baskets": threshold_baskets, "cases": scored,
    }
    suffix = "" if split == "all" else f"_{split}"
    (run_dir / f"metrics{suffix}.json").write_text(json.dumps(metrics, indent=2))
    lines = ["# Ox Alpha false-distress long experiment", "",
             f"Split: `{split}`", "", f"Cases scored: {len(scored)}", "",
             f"Base success rate: {base_rate:.1%}" if base_rate is not None else "Base success rate: n/a",
             f"Brier: {brier:.3f}" if brier is not None else "Brier: n/a",
             f"Base-rate Brier: {base_brier:.3f}" if base_brier is not None else "Base-rate Brier: n/a",
             f"AUC: {metrics['auc']:.3f}" if metrics["auc"] is not None else "AUC: n/a", "",
             "## Basket comparison", "",
             "| Basket | N | Mean excess return | Positive | Long success | Mean drawdown |",
             "|---|---:|---:|---:|---:|---:|",
             f"| All distressed | {baseline['n']} | {baseline['mean_relative_return_90d']:+.1%} | "
             f"{baseline['positive_fraction']:.0%} | {baseline['long_success_rate']:.0%} | "
             f"{baseline['mean_max_drawdown_90d']:+.1%} |",
             f"| Ox top decile | {top['n']} | {top['mean_relative_return_90d']:+.1%} | "
             f"{top['positive_fraction']:.0%} | {top['long_success_rate']:.0%} | "
             f"{top['mean_max_drawdown_90d']:+.1%} |", "", "## Ranked cases", "",
             "| Ticker | Cutoff | Forecast | Outcome | Excess return | Max drawdown |", "|---|---:|---:|---|---:|---:|"]
    for row in scored:
        lines.append(f"| {row['ticker']} | {row['cutoff']} | {row['probability']:.0%} | "
                     f"{'yes' if row['long_success'] else 'no'} | {row['relative_return_90d']:+.1%} | "
                     f"{row['max_drawdown_from_entry_90d']:+.1%} |")
    lines.extend(["", "## Limitations", "",
                  "- The current ticker universe creates historical survivorship bias.",
                  "- Issuer redaction cannot eliminate recognition from products or distinctive facts.",
                  "- Yahoo adjusted-close data does not model spreads, borrow, taxes, or execution.",
                  "- The experiment is exploratory until a sealed sample and forward paper test pass."])
    (run_dir / f"report{suffix}.md").write_text("\n".join(lines) + "\n")
    return metrics


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def make_client(args) -> sa.OpenRouter:
    return sa.OpenRouter(sa.get_api_key(args.api_key), model=args.model,
                         timeout=args.timeout, max_retries=args.retries)


def add_common(parser):
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)


def add_model(parser):
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--model", default=sa.DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=3)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="long-lab", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    add_common(build)
    build.add_argument("--universe", type=Path, default=Path("data/universe.json"))
    build.add_argument("--count", type=int, default=100)
    build.add_argument("--seed", type=int, default=117)
    build.add_argument("--start", type=parse_date, default=dt.date(2024, 1, 1))
    build.add_argument("--end", type=parse_date, default=dt.date.today() - dt.timedelta(days=200))
    build.add_argument("--build-concurrency", type=int, default=8)
    build.add_argument("--candidate-limit", type=int, default=5000)
    for name in ("analyze", "synthesize"):
        stage = sub.add_parser(name)
        add_common(stage)
        add_model(stage)
        stage.add_argument("--max-cases", type=int, default=None)
        stage.add_argument("--split", choices=("all", "development", "validation"), default="all")
    score = sub.add_parser("score")
    add_common(score)
    score.add_argument("--split", choices=("all", "development", "validation"), default="all")
    args = parser.parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "build":
        cases = build_cases(args.universe, args.run_dir, args.count, args.seed,
                            args.start, args.end, concurrency=args.build_concurrency,
                            candidate_limit=args.candidate_limit)
        print(f"{len(cases)} cases -> {args.run_dir / 'cases.jsonl'}")
    elif args.cmd == "score":
        metrics = score_run(args.run_dir, args.split)
        print(json.dumps({k: v for k, v in metrics.items() if k != "cases"}, indent=2))
    else:
        cases = ox.load_jsonl(args.run_dir / "cases.jsonl")
        if args.split != "all":
            cases = [case for case in cases if case.get("split") == args.split]
        if args.max_cases:
            cases = cases[:args.max_cases]
        client = make_client(args)
        if args.cmd == "analyze":
            run_roles(cases, args.run_dir, client, args.concurrency)
        else:
            run_synthesis(cases, args.run_dir, client, args.concurrency)
        print(f"model calls={client.calls} prompt_tokens={client.total_prompt_tokens} "
              f"completion_tokens={client.total_completion_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
