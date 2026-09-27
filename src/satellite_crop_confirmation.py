#!/usr/bin/env python3
"""Execute the frozen, selected wheat-heading 2025 confirmation exactly once.

This is a disclosed selected-secondary confirmation, not a pristine blind holdout.
See confirmation_2025_protocol.json for the information available before this run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from . import satellite_crop_validation as validation
except ImportError:
    import satellite_crop_validation as validation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=validation.DEFAULT_OUTPUT)
    parser.add_argument("--source", type=Path, help="Existing yield_model cache for initial 2025 snapshot")
    args = parser.parse_args()
    protocol_path = args.output / "confirmation_2025_protocol.json"
    protocol = json.loads(protocol_path.read_text())
    expected = protocol["sha256_at_freeze"]["src/satellite_crop_validation.py"]
    if validation.sha256(Path(validation.__file__)) != expected:
        raise ValueError("Core validator differs from frozen confirmation protocol")
    if validation.MODEL != protocol["model_parameters"]:
        raise ValueError("Model parameters differ from frozen confirmation protocol")
    extra_path = args.output / "confirmation_input_2025.csv.gz"
    if args.source:
        source_path = args.source / "stage_feature_panel.parquet"
        panel = pd.read_parquet(source_path)
        extra = panel[panel.year.eq(2025) & panel.crop.eq("wheat") & panel.stage.eq("heading")].copy()
        extra = extra.drop(columns=[c for c in extra if "ndvi" in c] + ["trend_yield", "yield_anomaly"])
        extra.to_csv(extra_path, index=False, compression={"method": "gzip", "mtime": 0})
        (args.output / "confirmation_input_provenance.json").write_text(json.dumps({
            "original_source_sha256": validation.sha256(source_path),
            "snapshot_sha256": validation.sha256(extra_path),
            "protocol_sha256": validation.sha256(protocol_path),
            "source_path_at_ingest": str(source_path),
        }, indent=2, sort_keys=True))
    config = json.loads((args.output / "input_config.json").read_text())
    raw = pd.concat([pd.read_csv(args.output / "input_panel.csv.gz"), pd.read_csv(extra_path)], ignore_index=True)
    raw = raw[raw.crop.eq("wheat") & raw.stage.eq("heading")].copy()
    observations = pd.read_csv(args.output / "input_ndvi.csv")
    panel = validation.point_in_time_trends(validation.rebuild_optical(raw, observations, config))
    predictions = validation.predict(panel, first_year=2025, last_year=2025)
    predictions.to_csv(args.output / "confirmation_predictions_2025.csv", index=False)
    rows = []
    for model, group in predictions.groupby("model"):
        rows.append(dict(model=model, n=len(group), rmse_anomaly_pct=float(
            100 * np.sqrt(np.mean((group.prediction_anomaly - group.yield_anomaly) ** 2))),
                         mae_anomaly_pct=float(100 * np.abs(group.prediction_anomaly - group.yield_anomaly).mean()),
                         available_date=str(group.available_date.iloc[0].date())))
    values = pd.DataFrame(rows).set_index("model")
    current = float(values.loc["weather_plus_ndvi", "rmse_anomaly_pct"])
    weather = float(values.loc["weather_only", "rmse_anomaly_pct"])
    placebo = float(values.loc["weather_plus_prior_year_ndvi", "rmse_anomaly_pct"])
    gates = {"improves_weather_rmse": current < weather, "beats_prior_year_ndvi_rmse": current < placebo}
    result = dict(status="selected_secondary_confirmation_pass" if all(gates.values()) else "selected_secondary_confirmation_fail",
                  year=2025, crop="wheat", stage="heading", independent_years=1, gates=gates,
                  improvement_vs_weather_pct=100 * (1 - current / weather), metrics=rows,
                  protocol_sha256=hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                  limitations=protocol["untouched_status"],
                  inference="Descriptive single-year confirmation only. No pooled p-value or independent trade-alpha claim.")
    (args.output / "confirmation_2025.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
