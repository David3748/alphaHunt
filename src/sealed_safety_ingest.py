#!/usr/bin/env python3
"""Ingest sealed safety source cases, analyses, predictions, outcomes, and backtest."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comprehensive_lab as cl
import ox_lab as ox
import temporal_store as ts


def source_knowledge_time(case: dict):
    values = [row.get("acceptanceDateTime") for row in case.get("snapshot_sources", [])
              if row.get("acceptanceDateTime")]
    return max(ts.parse_time(value) for value in values) if values else ts.case_time(case)


def ingest_sources(store: ts.TemporalStore, source_dir: Path, model_dir: Path,
                   system_time=None) -> dict:
    proposed = ts.parse_time(system_time or dt.datetime.now(dt.timezone.utc)).isoformat()
    saved = store.set_metadata_once("sealed_safety_source_v1_system_time", proposed, proposed)
    recorded_at = ts.parse_time(saved)
    cases = ox.load_jsonl(source_dir / "cases.jsonl")
    outcomes = {row["case_id"]: row.get("outcome") or {}
                for row in ox.load_jsonl(model_dir / "outcomes.jsonl")}
    experiment_id = store.register_experiment(
        "sealed_safety_source", "v1", recorded_at,
        {"source_dir": str(source_dir), "population": "exhaustive structured SEC severe drawdown",
         "future_price_eligibility_filter": False, "availability_precision": "exact_acceptance"},
        ts.code_hash(Path(__file__).with_name("sealed_safety.py")), "validated_research")
    store.db.execute("BEGIN")
    try:
        for case in cases:
            knowledge_at = source_knowledge_time(case)
            horizon_end = knowledge_at + dt.timedelta(days=90)
            market = case.get("market_at_cutoff") or {}
            security_id, _ = store.append_security(
                str(case["cik"]), case["ticker"], recorded_at,
                valid_from=knowledge_at, exchange=market.get("vendor_exchange"),
                attributes={"company": case.get("company"),
                            "historical_symbol_source": market.get("historical_symbol_source"),
                            "survivorship_reduced": True})
            content_hash = store.put_content(
                case.get("snapshot_text") or "", "text/plain",
                {"case_id": case["case_id"], "role": "prediction_sources",
                 "anchor_accession": case.get("anchor_accession")}, recorded_at)
            document_id, version_id = store.append_document(
                "sec_edgar", case["anchor_accession"], content_hash,
                case["cutoff"], knowledge_at, recorded_at, issuer_id=str(case["cik"]),
                document_type=case.get("anchor_form"),
                metadata={"sources": case.get("snapshot_sources") or [],
                          "availability_precision": "exact_acceptance",
                          "needs_acceptance_time_enrichment": False})
            manifest_id = store.create_manifest(
                knowledge_at, recorded_at,
                [{"item_type": "document", "logical_id": document_id,
                  "version_id": version_id, "content_hash": content_hash,
                  "role": "prediction_sources"}],
                {"case_id": case["case_id"], "purpose": "sealed_safety_source"}, recorded_at)
            store.register_case(
                case["case_id"], experiment_id, security_id, knowledge_at, manifest_id,
                recorded_at, horizon_end, "sealed",
                {"anchor_form": case.get("anchor_form"),
                 "anchor_accession": case.get("anchor_accession"),
                 "market_at_cutoff": market, "selection_note": case.get("selection_note")})
            store.append_outcome(
                case_id=case["case_id"], label_name="sealed_safety_return_90d",
                value=outcomes.get(case["case_id"], {"status": "not_materialized"}),
                horizon_start=knowledge_at, horizon_end=horizon_end, available_at=horizon_end,
                system_from=recorded_at, source="yahoo_chart_conservative_delisting_bound")
        store.db.execute("COMMIT")
    except Exception:
        store.db.execute("ROLLBACK")
        raise
    return {"experiment_id": experiment_id, "cases": len(cases),
            "outcomes": sum(case["case_id"] in outcomes for case in cases),
            "recorded_at": recorded_at.isoformat()}


def run(source_dir: Path, model_dir: Path, db: Path, blobs: Path,
        system_time=None) -> dict:
    fixed_time = system_time or dt.datetime.now(dt.timezone.utc).isoformat()
    with ts.TemporalStore(db, blobs) as store:
        source = ingest_sources(store, source_dir, model_dir, fixed_time)
        model = cl.ingest(store, model_dir, fixed_time,
                          "sealed_safety_validation", "v1")
        results = json.loads((model_dir / "results.json").read_text())
        recorded_at = ts.parse_time(model.get("recorded_at") or source["recorded_at"])
        store.insert_metrics(model["experiment_id"], "sealed", results, recorded_at,
                             results.get("generated_at"))
        backtest_id = store.record_backtest(
            model["experiment_id"], recorded_at,
            dt.datetime(2021, 7, 1, tzinfo=dt.timezone.utc),
            "strictly-prior expanding 90th-percentile downside score",
            {"entry": "next eligible close", "holding_days": 90, "cost_bps_side": 25,
             "position_fraction": .10, "max_positions": 10,
             "terminal_missing": "zero conservative / carry optimistic"},
            results, recorded_at, split=None,
            universe_version="SEC detailed filings 2019-2020 historical-symbol v1")
        return {"source": source, "model": model, "backtest_id": backtest_id,
                "integrity_issues": store.validate_integrity(), "summary": store.summary()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("lab_runs/sealed_safety_source"))
    parser.add_argument("--model-dir", type=Path, default=Path("lab_runs/sealed_safety"))
    parser.add_argument("--db", type=Path, default=ts.DEFAULT_DB)
    parser.add_argument("--blobs", type=Path, default=ts.DEFAULT_BLOBS)
    parser.add_argument("--system-time", default=None)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.source_dir, args.model_dir, args.db, args.blobs,
                         args.system_time), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
