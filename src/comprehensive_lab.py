#!/usr/bin/env python3
"""High-volume, leakage-controlled Ox Alpha extraction and long research.

The cheap model is used as a redundant structured extractor first and a
forecaster second. Five specialist lenses run twice per historical snapshot;
three independent synthesis calls then consume only the structured analyses.
All stages are append-only, cached, resumable, and suitable for migration into
the bitemporal store.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import re
import statistics
import sys
import threading
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox
import subagents as sa
import temporal_store as ts


EXPERIMENT = "comprehensive_long_v1"
DEFAULT_RUN_DIR = Path("lab_runs/comprehensive_long")
ROLE_NAMES = ("liquidity", "operations", "catalysts", "accounting", "governance")
WRITE_LOCK = threading.Lock()

ROLE_TERMS = {
    "liquidity": ("cash", "liquidity", "debt", "covenant", "matur", "going concern",
                  "working capital", "financing", "dilution", "shares", "convertible"),
    "operations": ("revenue", "margin", "backlog", "orders", "customer", "utilization",
                   "inventory", "cost reduction", "profit", "loss", "cash flow"),
    "catalysts": ("expect", "milestone", "approval", "launch", "contract", "award",
                  "strategic", "restructur", "guidance", "pipeline", "trial"),
    "accounting": ("material weakness", "restatement", "impairment", "non-gaap",
                   "related party", "auditor", "internal control", "receivable",
                   "recognition", "contingenc"),
    "governance": ("director", "officer", "compensation", "beneficial owner", "insider",
                   "related party", "litigation", "investigation", "nasdaq", "nyse",
                   "delisting", "shareholder"),
}

CLAIM_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "semantic_key": {"type": "string"},
        "claim_type": {"type": "string", "enum": [
            "liquidity", "capital_structure", "operating_trend", "catalyst",
            "accounting_quality", "governance", "legal_regulatory", "risk", "other"
        ]},
        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "value_text": {"type": "string"},
        "effective_date": {"type": ["string", "null"]},
        "source": {"type": "string"},
        "quote": {"type": "string"},
        "materiality": {"type": "integer", "minimum": 1, "maximum": 5},
        "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["semantic_key", "claim_type", "direction", "value_text",
                 "effective_date", "source", "quote", "materiality", "confidence_pct"],
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "role": {"type": "string"},
        "overall_signal": {"type": "integer", "minimum": -2, "maximum": 2},
        "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "claims": {"type": "array", "items": CLAIM_ITEM},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["role", "overall_signal", "confidence_pct", "summary", "claims",
                 "missing_information", "contradictions"],
}

EVIDENCE_REF = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "role": {"type": "string"},
        "semantic_key": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["role", "semantic_key", "why"],
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "probability_positive_excess_90d_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "probability_plus20_excess_90d_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "expected_excess_return_90d_pct": {"type": "number", "minimum": -100, "maximum": 300},
        "downside_tail_probability_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "decision": {"type": "string", "enum": ["reject", "watch", "long_candidate"]},
        "thesis": {"type": "string"},
        "catalyst": {"type": "string"},
        "invalidation": {"type": "string"},
        "evidence_refs": {"type": "array", "items": EVIDENCE_REF},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["probability_positive_excess_90d_pct", "probability_plus20_excess_90d_pct",
                 "expected_excess_return_90d_pct", "downside_tail_probability_pct", "decision",
                 "thesis", "catalyst", "invalidation", "evidence_refs", "uncertainties"],
}

COMMON_SYSTEM = """You are a forensic extraction agent in a leakage-controlled historical
equity study. Use ONLY the supplied filing excerpts available at the stated cutoff. Do not
use outside knowledge, remembered issuer identity, prices, or later events. Extract concrete,
decision-relevant facts, including evidence against a long. Every claim must contain a short
EXACT quote copied from the supplied text. Do not manufacture dates or numbers. Prefer 4-10
material claims over generic prose. Return only the required structured tool result."""

ROLE_SYSTEMS = {
    "liquidity": COMMON_SYSTEM + "\nFocus on cash runway, free cash flow, debt, maturities, covenants, dilution capacity, and financing dependence.",
    "operations": COMMON_SYSTEM + "\nFocus on revenue/margin direction, backlog quality, demand, customers, unit economics, costs, and operating leverage.",
    "catalysts": COMMON_SYSTEM + "\nFocus on specific funded events likely within 90-180 days; distinguish dated catalysts from aspirations.",
    "accounting": COMMON_SYSTEM + "\nAudit earnings quality, working capital, impairments, controls, auditor issues, non-GAAP adjustments, and recognition risk.",
    "governance": COMMON_SYSTEM + "\nAudit incentives, ownership, capital allocation, related parties, litigation, listing risk, and management credibility.",
}

SYNTHESIS_SYSTEM = """You are a calibrated long-only portfolio researcher in a historical,
leakage-controlled experiment. You receive redundant specialist extractions based solely on
filings known at the cutoff. Forecast 90-calendar-day excess return versus SPY. Apply skeptical
base rates and discount unsupported or ungrounded claims. A long candidate needs both a credible
near-term rerating mechanism and tolerable downside; cheapness alone is insufficient. Replicates
are independent: do not imitate a presumed consensus. Return only the structured tool result."""


def compact_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def quote_is_grounded(quote: str, source_text: str) -> bool:
    needle = compact_space(quote)
    return len(needle) >= 12 and needle in compact_space(source_text)


def role_excerpt(text: str, role: str, max_chars: int = 60_000,
                 radius: int = 3_500) -> str:
    """Build deterministic role-specific coverage across very large filing packs."""
    if len(text) <= max_chars:
        return text
    priority_intervals = [(0, min(18_000, len(text)))]
    lower = text.lower()
    for term in ROLE_TERMS[role]:
        start = hits = 0
        while hits < 14:
            pos = lower.find(term, start)
            if pos < 0:
                break
            priority_intervals.append((max(0, pos - radius), min(len(text), pos + radius)))
            start = pos + len(term)
            hits += 1
    # Preserve broad coverage even when vocabulary misses an important section.
    coverage_intervals = []
    stride = max(1, len(text) // 8)
    for pos in range(stride, len(text), stride):
        coverage_intervals.append((pos, min(len(text), pos + 2_000)))
    # Allocate to the header and keyword hits before broad sampling. This is
    # intentionally not source-ordered: otherwise early boilerplate can crowd
    # a decisive late-file red flag out of a constrained model context.
    pieces, used, selected = [], 0, []
    for start, end in priority_intervals + coverage_intervals:
        if used >= max_chars:
            break
        if any(start >= prior_start and end <= prior_end for prior_start, prior_end in selected):
            continue
        piece = text[start:end][:max_chars - used]
        pieces.append(piece)
        selected.append((start, start + len(piece)))
        used += len(piece)
    return "\n\n[...section boundary...]\n\n".join(pieces)


def load_cases(run_dir: Path, source_dirs: list[Path]) -> list[dict]:
    path = run_dir / "cases.jsonl"
    existing = ox.load_jsonl(path)
    if existing:
        return existing
    rows = []
    for source_dir in source_dirs:
        for case in ox.load_jsonl(source_dir / "cases.jsonl"):
            source_case_id = case["case_id"]
            record = dict(case)
            record["source_case_id"] = source_case_id
            record["source_run_dir"] = str(source_dir)
            record["case_id"] = ts.digest(EXPERIMENT, str(source_dir), source_case_id)[:24]
            record["experiment"] = EXPERIMENT
            rows.append(record)
    rows.sort(key=lambda row: (row["cutoff"], row["ticker"], row["case_id"]))
    for row in rows:
        ox.append_jsonl(path, row)
    return rows


def extraction_done(path: Path) -> set[tuple[str, str, int]]:
    return {(row.get("case_id"), row.get("role"), int(row.get("replicate", 0)))
            for row in ox.load_jsonl(path) if isinstance(row.get("result"), dict)}


def synthesis_done(path: Path) -> set[tuple[str, int]]:
    return {(row.get("case_id"), int(row.get("replicate", 0)))
            for row in ox.load_jsonl(path) if isinstance(row.get("result"), dict)}


def run_extractions(cases: list[dict], run_dir: Path, client: sa.OpenRouter,
                    replicates: int, concurrency: int) -> list[dict]:
    path = run_dir / "extractions.jsonl"
    done = extraction_done(path)
    jobs = [(case, role, rep) for case in cases for role in ROLE_NAMES
            for rep in range(1, replicates + 1) if (case["case_id"], role, rep) not in done]
    cache = ox.LLMCache(run_dir / "cache" / "llm")

    def work(job):
        case, role, replicate = job
        result = None
        last_error = None
        excerpt = ""
        # A small minority of free-model calls repeatedly truncate JSON on the
        # normal 60k excerpt. Retry with distinct, shorter prompts so they do
        # not hit the same cache key and can still produce a valid extraction.
        for max_chars in (60_000, 30_000, 15_000):
            excerpt = role_excerpt(case["snapshot_text"], role, max_chars=max_chars)
            user = (f"ROLE: {role}\nINDEPENDENT REPLICATE: {replicate}\n"
                    f"CUTOFF: {case['cutoff']}\nANCHOR FORM: {case.get('anchor_form')}\n\n"
                    f"HISTORICAL FILING EXCERPTS:\n{excerpt}")
            try:
                result = cache.call_json(client, ROLE_SYSTEMS[role], user,
                                         f"comprehensive_{role}_v1", EXTRACTION_SCHEMA,
                                         effort="medium")
                break
            except Exception as exc:
                last_error = exc
        if result is None:
            raise last_error or RuntimeError("structured extraction failed")
        raw_claims = result.get("claims") if isinstance(result.get("claims"), list) else []
        # Free-model structured output occasionally preserves a malformed scalar
        # inside an otherwise valid claims array. Keep valid objects and record
        # the dropped count rather than failing the entire case indefinitely.
        claims = [claim for claim in raw_claims if isinstance(claim, dict)]
        result["claims"] = claims
        grounded = 0
        for claim in claims:
            claim["quote_grounded"] = quote_is_grounded(claim.get("quote", ""), excerpt)
            grounded += int(claim["quote_grounded"])
        return {"experiment": EXPERIMENT, "case_id": case["case_id"], "role": role,
                "replicate": replicate, "model": client.model,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "excerpt_chars": len(excerpt), "grounded_claims": grounded,
                "total_claims": len(claims),
                "malformed_claims_dropped": len(raw_claims) - len(claims),
                "result": result}

    print(f"extractions pending={len(jobs)} concurrency={concurrency}", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        completed = failed = 0
        for future in cf.as_completed(futures):
            case, role, replicate = futures[future]
            try:
                row = future.result()
                with WRITE_LOCK:
                    ox.append_jsonl(path, row)
                completed += 1
            except Exception as exc:
                failed += 1
                print(f"FAILED extraction {case['ticker']} {role} r{replicate}: {exc}", file=sys.stderr)
            if (completed + failed) % 25 == 0:
                print(f"extractions progress={completed + failed}/{len(jobs)} ok={completed} failed={failed}", file=sys.stderr)
    return ox.load_jsonl(path)


def run_syntheses(cases: list[dict], run_dir: Path, client: sa.OpenRouter,
                  replicates: int, concurrency: int) -> list[dict]:
    path = run_dir / "syntheses.jsonl"
    done = synthesis_done(path)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in ox.load_jsonl(run_dir / "extractions.jsonl"):
        if isinstance(row.get("result"), dict):
            grouped[row["case_id"]].append(row)
    jobs = [(case, rep) for case in cases for rep in range(1, replicates + 1)
            if (case["case_id"], rep) not in done and
            len({row["role"] for row in grouped[case["case_id"]]}) == len(ROLE_NAMES)]
    cache = ox.LLMCache(run_dir / "cache" / "llm")

    def work(job):
        case, replicate = job
        analyses = []
        for row in sorted(grouped[case["case_id"]], key=lambda x: (x["role"], x["replicate"])):
            result = dict(row["result"])
            result["claims"] = [claim for claim in result.get("claims", [])
                                if claim.get("quote_grounded")]
            analyses.append({"role": row["role"], "replicate": row["replicate"], "result": result})
        market = case.get("market_at_cutoff") or {}
        user = (f"INDEPENDENT SYNTHESIS REPLICATE: {replicate}\nCUTOFF: {case['cutoff']}\n"
                f"KNOWN MARKET STATE: {json.dumps(market, separators=(',', ':'))}\n\n"
                f"SPECIALIST EXTRACTIONS:\n{json.dumps(analyses, separators=(',', ':'))}")
        result = cache.call_json(client, SYNTHESIS_SYSTEM, user,
                                 "comprehensive_long_synthesis_v1", SYNTHESIS_SCHEMA, effort="high")
        required = set(SYNTHESIS_SCHEMA["required"])
        for repair_attempt in range(1, 4):
            if required.issubset(result):
                break
            missing = sorted(required - set(result))
            repair_user = (
                "STRICT SCHEMA REPAIR. Produce the complete forecast from the supplied specialist "
                f"evidence. The prior attempt omitted or misnamed required fields: {missing}. "
                f"This is repair attempt {repair_attempt}; every required property must be present. "
                "Do not reuse an unrelated schema.\n\n" + user)
            # Version the repair key so a structurally incomplete cached repair
            # cannot trap all later resumptions in a zero-call retry loop.
            result = cache.call_json(client, SYNTHESIS_SYSTEM, repair_user,
                                     f"sealed_safety_synthesis_repair_v{repair_attempt + 1}",
                                     SYNTHESIS_SCHEMA, effort="high")
        if not required.issubset(result):
            raise ValueError(f"incomplete synthesis fields: {sorted(required - set(result))}")
        return {"experiment": EXPERIMENT, "case_id": case["case_id"], "replicate": replicate,
                "model": client.model, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "result": result}

    print(f"syntheses pending={len(jobs)} concurrency={concurrency}", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        completed = failed = 0
        for future in cf.as_completed(futures):
            case, replicate = futures[future]
            try:
                row = future.result()
                with WRITE_LOCK:
                    ox.append_jsonl(path, row)
                completed += 1
            except Exception as exc:
                failed += 1
                print(f"FAILED synthesis {case['ticker']} r{replicate}: {exc}", file=sys.stderr)
            if (completed + failed) % 25 == 0:
                print(f"syntheses progress={completed + failed}/{len(jobs)} ok={completed} failed={failed}", file=sys.stderr)
    return ox.load_jsonl(path)


def auc(points: list[tuple[float, int]]) -> float | None:
    positives = [p for p, y in points if y == 1]
    negatives = [p for p, y in points if y == 0]
    if not positives or not negatives:
        return None
    return sum(1 if p > n else 0.5 if p == n else 0 for p in positives for n in negatives) / (len(positives) * len(negatives))


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    denom = math.sqrt(sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys))
    return sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / denom if denom else None


def group_metrics(rows: list[dict]) -> dict:
    usable = [row for row in rows if isinstance(row.get("relative_return_90d"), (int, float))]
    labeled = [row for row in usable if row.get("long_success") is not None]
    ranked = sorted(usable, key=lambda row: (-row["probability"], row["case_id"]))
    top = ranked[:max(1, math.ceil(len(ranked) * 0.10))] if ranked else []
    top_labels = [int(row["long_success"]) for row in top
                  if row.get("long_success") is not None]
    return {
        "n": len(usable),
        "auc_plus20": auc([(row["probability"], int(row["long_success"])) for row in labeled]),
        "probability_return_correlation": pearson(
            [row["probability"] for row in usable],
            [row["relative_return_90d"] for row in usable]),
        "expected_return_correlation": pearson(
            [row["expected_excess"] for row in usable],
            [row["relative_return_90d"] for row in usable]),
        "top_decile_n": len(top),
        "top_decile_mean_excess_return": statistics.mean(
            row["relative_return_90d"] for row in top) if top else None,
        "top_decile_success_fraction": statistics.mean(top_labels) if top_labels else None,
        "top_decile_tickers": [row["ticker"] for row in top],
    }


def score(run_dir: Path) -> dict:
    cases = {row["case_id"]: row for row in ox.load_jsonl(run_dir / "cases.jsonl")}
    syntheses: dict[str, list[dict]] = defaultdict(list)
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        if isinstance(row.get("result"), dict):
            syntheses[row["case_id"]].append(row["result"])
    rows = []
    for case_id, results in syntheses.items():
        case = cases.get(case_id)
        if not case or not results:
            continue
        probabilities = [r.get("probability_plus20_excess_90d_pct") for r in results]
        expected = [r.get("expected_excess_return_90d_pct") for r in results]
        if not all(isinstance(x, (int, float)) for x in probabilities + expected):
            continue
        outcome = case.get("outcome") or case.get("returns") or {}
        relative = outcome.get("relative_return_90d")
        success = outcome.get("long_success")
        if success is None and isinstance(relative, (int, float)):
            success = relative >= 0.20
        rows.append({"case_id": case_id, "ticker": case["ticker"], "split": case.get("split"),
                     "source_run_dir": case["source_run_dir"],
                     "probability": statistics.mean(probabilities) / 100,
                     "expected_excess": statistics.mean(expected) / 100,
                     "relative_return_90d": relative, "long_success": bool(success) if success is not None else None})
    usable = [r for r in rows if isinstance(r["relative_return_90d"], (int, float))]
    groups = {"all": group_metrics(usable)}
    for source in sorted({row["source_run_dir"] for row in usable}):
        groups[f"source:{source}"] = group_metrics(
            [row for row in usable if row["source_run_dir"] == source])
    for split in sorted({row["split"] for row in usable if row.get("split")}):
        groups[f"split:{split}"] = group_metrics(
            [row for row in usable if row.get("split") == split])
    metrics = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_scored": len(usable),
        "groups": groups,
        "rows": rows,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def claim_effective_time(value, fallback):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return ts.parse_time(value)
        except ValueError:
            pass
    return ts.parse_time(fallback, end_of_day=True)


def case_knowledge_time(case: dict):
    accepted = [row.get("acceptanceDateTime") for row in case.get("snapshot_sources", [])
                if row.get("acceptanceDateTime")]
    if accepted:
        return max(ts.parse_time(value) for value in accepted)
    return ts.case_time(case)


def ingest(store: ts.TemporalStore, run_dir: Path, system_time=None,
           experiment_name: str = "comprehensive_long",
           experiment_version: str = "v1") -> dict:
    """Transactionally materialize grounded claims and model runs in DuckDB."""
    proposed = ts.parse_time(system_time or dt.datetime.now(dt.timezone.utc)).isoformat()
    store.db.execute("BEGIN")
    try:
        metadata_key = re.sub(r"[^a-zA-Z0-9_.-]+", "_",
                              f"{experiment_name}_{experiment_version}_system_time")
        saved = store.set_metadata_once(metadata_key, proposed, proposed)
        recorded_at = ts.parse_time(saved)
        experiment_id = store.register_experiment(
            experiment_name, experiment_version, recorded_at,
            {"source_run_dir": str(run_dir), "roles": list(ROLE_NAMES),
             "role_replicates": len({int(row.get("replicate", 0)) for row in
                                      ox.load_jsonl(run_dir / "extractions.jsonl")}),
             "synthesis_replicates": len({int(row.get("replicate", 0)) for row in
                                           ox.load_jsonl(run_dir / "syntheses.jsonl")}),
             "task": "90-day excess-return long ranking"},
            ts.code_hash(Path(__file__)), "research")
        cases = ox.load_jsonl(run_dir / "cases.jsonl")
        extraction_rows: dict[str, list[dict]] = defaultdict(list)
        synthesis_rows: dict[str, list[dict]] = defaultdict(list)
        external_outcomes = {row.get("comprehensive_case_id"): row.get("outcome") or {}
                             for row in ox.load_jsonl(run_dir / "outcomes.jsonl")}
        for row in ox.load_jsonl(run_dir / "extractions.jsonl"):
            if isinstance(row.get("result"), dict):
                extraction_rows[row["case_id"]].append(row)
        for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
            if isinstance(row.get("result"), dict):
                synthesis_rows[row["case_id"]].append(row)

        claim_count = analysis_count = prediction_count = outcome_count = 0
        for case in cases:
            source = store.db.execute("""
                SELECT security_id, manifest_id, horizon_end, split
                FROM experiment_cases WHERE case_id=?
            """, [case["source_case_id"]]).fetchone()
            if not source:
                raise ValueError(f"source case not migrated: {case['source_case_id']}")
            security_id, source_manifest, horizon_end, source_split = source
            source_items = store.db.execute("""
                SELECT ordinal, item_type, logical_id, version_id, content_hash, role
                FROM manifest_items WHERE manifest_id=? ORDER BY ordinal
            """, [source_manifest]).fetchall()
            manifest_items = []
            document_available_times = []
            for row in source_items:
                ordinal, item_type, logical_id, version_id, content_hash, role = row
                if item_type == "document":
                    latest = store.db.execute("""
                        SELECT version_id, content_hash, available_at FROM document_history
                        WHERE document_id=? AND system_from <= ?
                          AND (system_to IS NULL OR ? < system_to)
                    """, [logical_id, recorded_at, recorded_at]).fetchone()
                    if latest:
                        version_id, content_hash, available_at = latest
                        document_available_times.append(available_at)
                manifest_items.append({"item_type": item_type, "logical_id": logical_id,
                                       "version_id": version_id, "content_hash": content_hash,
                                       "role": role})
            document_id = next((row["logical_id"] for row in manifest_items
                                if row["item_type"] == "document"), None)
            knowledge_at = max([case_knowledge_time(case), *document_available_times])
            analysis_manifest = store.create_manifest(
                knowledge_at, recorded_at, manifest_items,
                {"purpose": "comprehensive_claim_extraction", "case_id": case["case_id"],
                 "source_manifest": source_manifest}, recorded_at)
            issuer_id = str(case["cik"])
            for extraction in extraction_rows[case["case_id"]]:
                role, replicate = extraction["role"], int(extraction["replicate"])
                for index, claim in enumerate(extraction["result"].get("claims", [])):
                    if not claim.get("quote_grounded"):
                        continue
                    semantic = re.sub(r"[^a-z0-9_.-]+", "_", str(claim.get("semantic_key", "claim")).lower()).strip("_")
                    claim_key = f"{case['case_id']}:{role}:r{replicate}:{index}:{semantic[:80]}"
                    claim_id, version_id = store.append_claim(
                        issuer_id=issuer_id, claim_type=claim.get("claim_type") or role,
                        claim_key=claim_key,
                        value={"text": claim.get("value_text"), "direction": claim.get("direction"),
                               "materiality": claim.get("materiality"), "role": role,
                               "replicate": replicate, "semantic_key": claim.get("semantic_key")},
                        effective_from=claim_effective_time(claim.get("effective_date"), case["cutoff"]),
                        available_at=knowledge_at, system_from=recorded_at,
                        extractor_name="stealth/ox-alpha", extractor_version="comprehensive_claim_v1",
                        source_document_id=document_id,
                        evidence={"source": claim.get("source"), "quote": claim.get("quote"),
                                  "quote_grounded": True, "case_id": case["case_id"]},
                        confidence=(claim.get("confidence_pct") or 0) / 100,
                    )
                    manifest_items.append({"item_type": "claim", "logical_id": claim_id,
                                           "version_id": version_id, "role": f"{role}_claim"})
                    claim_count += 1
            manifest = store.create_manifest(
                knowledge_at, recorded_at, manifest_items,
                {"purpose": "comprehensive_long_prediction", "case_id": case["case_id"],
                 "source_manifest": source_manifest}, recorded_at)
            store.register_case(
                case["case_id"], experiment_id, security_id, knowledge_at, manifest,
                recorded_at, horizon_end or ts.horizon_time(case), case.get("split") or source_split,
                {"source_case_id": case["source_case_id"], "source_run_dir": case["source_run_dir"],
                 "ticker": case.get("ticker")})
            for extraction in extraction_rows[case["case_id"]]:
                store.insert_analysis(
                    experiment_id=experiment_id, case_id=case["case_id"],
                    role=f"extractor_{extraction['role']}", replicate=extraction["replicate"],
                    as_of_at=knowledge_at, system_as_of=recorded_at,
                    generated_at=extraction.get("generated_at"), recorded_at=recorded_at,
                    manifest_id=analysis_manifest, model=extraction.get("model") or sa.DEFAULT_MODEL,
                    prompt_version=f"comprehensive_{extraction['role']}_v1",
                    schema_version="comprehensive_claim_v1", code_version=ts.code_hash(Path(__file__)),
                    output=extraction["result"])
                analysis_count += 1
            for synthesis in synthesis_rows[case["case_id"]]:
                result = synthesis["result"]
                probability = result.get("probability_plus20_excess_90d_pct")
                store.insert_prediction(
                    experiment_id=experiment_id, case_id=case["case_id"],
                    task="long_plus20_excess_90d", replicate=synthesis["replicate"], horizon_days=90,
                    prediction_as_of=knowledge_at, system_as_of=recorded_at,
                    generated_at=synthesis.get("generated_at"), recorded_at=recorded_at,
                    manifest_id=manifest, model=synthesis.get("model") or sa.DEFAULT_MODEL,
                    prompt_version="comprehensive_long_synthesis_v1",
                    schema_version="comprehensive_long_synthesis_v1",
                    code_version=ts.code_hash(Path(__file__)),
                    probability=float(probability) / 100 if isinstance(probability, (int, float)) else None,
                    decision=result.get("decision"), output=result)
                prediction_count += 1
            outcome = dict(external_outcomes.get(case["case_id"]) or
                           case.get("outcome") or case.get("returns") or {})
            relative = outcome.get("relative_return_90d")
            if outcome.get("long_success") is None and isinstance(relative, (int, float)):
                outcome["long_success"] = relative >= 0.20
                outcome["drawdown_constraint_available"] = False
            store.append_outcome(
                case_id=case["case_id"], label_name="long_plus20_excess_90d", value=outcome,
                horizon_start=knowledge_at, horizon_end=horizon_end or ts.horizon_time(case),
                available_at=horizon_end or ts.horizon_time(case), system_from=recorded_at,
                source="legacy_adjusted_close")
            outcome_count += 1
        metrics_path = run_dir / "metrics.json"
        if metrics_path.exists():
            store.insert_metrics(experiment_id, "all", json.loads(metrics_path.read_text()), recorded_at)
        result = {"experiment_id": experiment_id, "cases": len(cases), "claims": claim_count,
                  "analyses": analysis_count, "predictions": prediction_count,
                  "outcomes": outcome_count, "summary": store.summary()}
        store.db.execute("COMMIT")
        return result
    except Exception:
        store.db.execute("ROLLBACK")
        raise


def add_model_args(parser):
    parser.add_argument("--model", default=sa.DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-cases", type=int, default=None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="comprehensive-lab", description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--source-dir", action="append", type=Path,
                        default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    extract = sub.add_parser("extract")
    add_model_args(extract)
    extract.add_argument("--replicates", type=int, default=2)
    synthesize = sub.add_parser("synthesize")
    add_model_args(synthesize)
    synthesize.add_argument("--replicates", type=int, default=3)
    sub.add_parser("score")
    sub.add_parser("status")
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--db", type=Path, default=ts.DEFAULT_DB)
    ingest_parser.add_argument("--blobs", type=Path, default=ts.DEFAULT_BLOBS)
    ingest_parser.add_argument("--system-time", default=None)
    ingest_parser.add_argument("--experiment-name", default="comprehensive_long")
    ingest_parser.add_argument("--experiment-version", default="v1")
    args = parser.parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    # argparse appends to a non-empty default; deduplicate paths predictably.
    source_dirs = list(dict.fromkeys(args.source_dir or
                       [Path("lab_runs/dilution90"), Path("lab_runs/long_dev")]))
    cases = load_cases(args.run_dir, source_dirs)
    if args.cmd in {"extract", "synthesize"}:
        if args.max_cases:
            cases = cases[:args.max_cases]
        client = sa.OpenRouter(sa.get_api_key(args.api_key), model=args.model,
                               timeout=args.timeout, max_retries=args.retries)
        if args.cmd == "extract":
            rows = run_extractions(cases, args.run_dir, client, args.replicates, args.concurrency)
        else:
            rows = run_syntheses(cases, args.run_dir, client, args.replicates, args.concurrency)
        usage = {"stage": args.cmd, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "rows": len(rows), "calls_this_process": client.calls,
                 "prompt_tokens_this_process": client.total_prompt_tokens,
                 "completion_tokens_this_process": client.total_completion_tokens}
        (args.run_dir / f"usage_{args.cmd}.json").write_text(
            json.dumps(usage, indent=2), encoding="utf-8")
        print(json.dumps(usage))
    elif args.cmd == "score":
        print(json.dumps({k: v for k, v in score(args.run_dir).items() if k != "rows"}, indent=2))
    elif args.cmd == "ingest":
        with ts.TemporalStore(args.db, args.blobs) as store:
            print(json.dumps(ingest(store, args.run_dir, args.system_time,
                                    args.experiment_name, args.experiment_version),
                             indent=2, default=str))
    else:
        extractions = ox.load_jsonl(args.run_dir / "extractions.jsonl")
        syntheses = ox.load_jsonl(args.run_dir / "syntheses.jsonl")
        grounded = sum(row.get("grounded_claims", 0) for row in extractions)
        claims = sum(row.get("total_claims", 0) for row in extractions)
        print(json.dumps({"cases": len(cases), "extractions": len(extractions),
                          "target_extractions": len(cases) * len(ROLE_NAMES) * 2,
                          "target_syntheses": len(cases) * 3,
                          "syntheses": len(syntheses), "grounded_claims": grounded,
                          "total_claims": claims}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
