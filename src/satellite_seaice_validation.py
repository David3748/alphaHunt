#!/usr/bin/env python3
"""Fixed July satellite sea ice -> future September ice forecast evaluation.

Current-vintage chronological hindcast. This validates a physical forecast,
not shipping capacity, an original-release trading signal, or financial alpha.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/seaice"
URLS = {f"N_{month:02d}_extent_v4.0.csv":
        f"https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/data/N_{month:02d}_extent_v4.0.csv"
        for month in (7, 9)}
DOCS = {
    "archive": "https://nsidc.org/data/seaice_index/data-and-image-archive",
    "user_guide": "https://nsidc.org/sites/default/files/documents/user-guide/g02135-v004-userguide.pdf",
    "product": "https://nsidc.org/data/g02135/versions/4",
}
MODELS = ["satellite", "trend_prior", "trend", "persistence", "climatology"]


def fetch(out: Path) -> None:
    import requests
    inputs = out / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, url in URLS.items():
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        (inputs / name).write_bytes(response.content)
        manifest[name] = {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                          "sha256": hashlib.sha256(response.content).hexdigest(),
                          "http_last_modified": response.headers.get("Last-Modified"),
                          "bytes": len(response.content)}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def load_panel(inputs: Path) -> pd.DataFrame:
    monthly = {}
    for month in (7, 9):
        frame = pd.read_csv(inputs / f"N_{month:02d}_extent_v4.0.csv", skipinitialspace=True)
        if frame.year.duplicated().any() or not frame.mo.eq(month).all():
            raise ValueError("Duplicate year or wrong month in input")
        if not frame.region.str.strip().eq("N").all():
            raise ValueError("Expected Northern Hemisphere")
        if not frame.extent.between(0, 20).all():
            raise ValueError("Missing sentinel or invalid extent")
        monthly[month] = frame.set_index("year").sort_index()
    panel = pd.DataFrame({"july_extent": monthly[7].extent, "september_extent": monthly[9].extent,
                          "july_source": monthly[7].source_dataset.str.strip(),
                          "september_source": monthly[9].source_dataset.str.strip()})
    # Join by explicit year, never turn a missing year's label into persistence.
    panel["prior_september"] = panel.index.map(monthly[9].extent.rename(index=lambda year: year + 1))
    panel = panel.loc[(panel.index >= 1980) & (panel.index <= 2025)].copy()
    if panel.empty or panel.isna().any().any():
        raise ValueError("Missing aligned July, September or prior September")
    if not np.all(np.diff(panel.index) == 1):
        raise ValueError("Missing aligned year")
    panel.index.name = "year"
    panel = panel.reset_index()
    dates = panel.year.astype(str)
    panel["predictor_end"] = pd.to_datetime(dates + "-07-31")
    panel["assumed_predictor_available"] = pd.to_datetime(dates + "-08-14")
    panel["forecast_at"] = pd.to_datetime(dates + "-08-15")
    panel["target_start"] = pd.to_datetime(dates + "-09-01")
    panel["target_end"] = pd.to_datetime(dates + "-09-30")
    panel["assumed_label_available"] = pd.to_datetime(dates + "-10-15")
    return panel


def design(frame: pd.DataFrame, model: str) -> np.ndarray:
    # A fixed time origin improves conditioning, without using observed outcomes.
    columns = [np.ones(len(frame)), frame.year.to_numpy() - 1979]
    if model in ("trend_prior", "satellite"):
        columns.append(frame.prior_september.to_numpy())
    if model == "satellite":
        columns.append(frame.july_extent.to_numpy())
    if model not in ("trend", "trend_prior", "satellite"):
        raise ValueError("Unknown OLS model")
    return np.column_stack(columns)


def predict(panel: pd.DataFrame, initial_train: int = 20) -> pd.DataFrame:
    rows = []
    panel = panel.sort_values("year")
    for idx, row in panel.iterrows():
        if not row.predictor_end < row.assumed_predictor_available < row.forecast_at < row.target_start:
            raise ValueError("Current feature not available before forecast and target")
        train = panel.loc[(panel.year < row.year) & (panel.assumed_label_available < row.forecast_at)]
        if len(train) < initial_train:
            continue
        if int(train.year.max()) != row.year - 1:
            raise ValueError("Previous September unavailable")
        record = row.to_dict()
        record.update({"n_train": len(train), "training_last_year": int(train.year.max()),
                       "training_last_label_available": train.assumed_label_available.max(),
                       "persistence": float(row.prior_september),
                       "climatology": float(train.september_extent.mean())})
        for model in ("satellite", "trend_prior", "trend"):
            coefficients = np.linalg.lstsq(design(train, model), train.september_extent, rcond=None)[0]
            record[model] = float((design(panel.loc[[idx]], model) @ coefficients)[0])
            if model == "satellite":
                record["july_coefficient"] = float(coefficients[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame) -> dict:
    errors = predictions[MODELS].to_numpy() - predictions.september_extent.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {name: {"rmse_million_km2": float(np.sqrt(squared[:, i].mean())),
                      "mae_million_km2": float(np.abs(errors[:, i]).mean())}
               for i, name in enumerate(MODELS)}
    n = len(predictions)
    rng = np.random.default_rng(20260927)
    starts = rng.integers(0, n, size=(10000, (n + 1) // 2, 1))
    indices = ((starts + np.arange(2)) % n).reshape(10000, -1)[:, :n]
    bootstrap_rmse = np.sqrt(squared[indices].mean(axis=1))
    comparisons = {}
    for i, model in enumerate(MODELS[1:], 1):
        gain = metrics[model]["rmse_million_km2"] - metrics["satellite"]["rmse_million_km2"]
        distribution = bootstrap_rmse[:, i] - bootstrap_rmse[:, 0]
        comparisons[model] = {
            "rmse_reduction_fraction": gain / metrics[model]["rmse_million_km2"],
            "rmse_reduction_million_km2": gain,
            "rmse_reduction_ci95_million_km2": np.quantile(distribution, [.025, .975]).tolist(),
            "mae_reduction_fraction": 1 - metrics["satellite"]["mae_million_km2"] / metrics[model]["mae_million_km2"],
            "years_lower_squared_error": int((squared[:, 0] < squared[:, i]).sum()),
        }
    gate = all(c["rmse_reduction_fraction"] >= .05 and c["mae_reduction_fraction"] > 0
               for c in comparisons.values()) and comparisons["trend_prior"]["rmse_reduction_ci95_million_km2"][0] > 0
    return {"n_test_years": n, "first_test_year": int(predictions.year.min()),
            "last_test_year": int(predictions.year.max()), "metrics": metrics, "comparisons": comparisons,
            "predefined_forecast_gate_passed": bool(gate),
            "status": "predefined_gate_passed_current_vintage_physical_forecast" if gate else "forecast_gate_failed",
            "positive_ci_against_every_baseline": all(c["rmse_reduction_ci95_million_km2"][0] > 0 for c in comparisons.values()),
            "original_vintage_operational_verification": False, "prospective_verification": False,
            "shipping_capacity_verified": False, "trading_alpha_verified": False,
            "cross_candidate_multiple_testing_adjusted": False}


def diagnostics(panel: pd.DataFrame, predictions: pd.DataFrame, out: Path) -> dict:
    """Post-result review diagnostics; do not replace the frozen primary test."""
    periods = {}
    for start, stop in ((2000, 2009), (2010, 2019), (2020, 2025)):
        frame = predictions.loc[predictions.year.between(start, stop)]
        result = score(frame)
        periods[f"{start}-{stop}"] = {"n_years": len(frame), "metrics": result["metrics"],
                                     "comparisons": {name: {k: v for k, v in values.items() if "ci95" not in k}
                                                     for name, values in result["comparisons"].items()}}
    july = pd.read_csv(out / "inputs/N_07_extent_v4.0.csv", skipinitialspace=True).set_index("year").extent
    placebo = panel.copy()
    placebo["july_extent"] = placebo.year.map(july.rename(index=lambda year: year + 1))
    placebo_predictions = predict(placebo)
    placebo_predictions.to_csv(out / "prior_july_placebo_predictions.csv", index=False)
    placebo_score = score(placebo_predictions)
    result = {"status": "posthoc_review_sensitivity_not_primary_protocol",
              "periods": periods,
              "prior_july_placebo": {"description": "Same model and test rows, using previous-year July in place of current July",
                                     "metrics": placebo_score["metrics"],
                                     "comparisons": placebo_score["comparisons"]}}
    audit_path = out / "revision_audit/original_july_values.csv"
    if audit_path.exists():
        audit = pd.read_csv(audit_path)
        partial = panel.copy()
        for _, row in audit.iterrows():
            issue = pd.Timestamp(f"{int(row.year)}-08-15")
            if pd.Timestamp(row.publication_date) >= issue:
                raise ValueError("Vintage audit report was not before the fixed forecast issue")
            partial.loc[partial.year.eq(row.year), "july_extent"] = row.original_july_extent
        replacement_predictions = predict(partial)
        replacement_predictions.to_csv(out / "partial_original_july_predictions.csv", index=False)
        replacement = score(replacement_predictions)
        result["partial_original_july_substitution"] = {
            "status": "mixed_vintage_stress_test_not_original_release_backtest",
            "n_replaced_years": len(audit),
            "years": audit.year.astype(int).tolist(),
            "metrics": replacement["metrics"], "comparisons": replacement["comparisons"],
            "note": "Only audited July values replaced. Other predictor years, prior September and targets remain current archive. Pre-2017 reports used a different monthly aggregation definition."}
    (out / "sensitivity.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def run(out: Path = DEFAULT) -> dict:
    panel = load_panel(out / "inputs")
    predictions = predict(panel)
    summary = score(predictions)
    summary.update({"units": "million square kilometers of Arctic sea-ice extent",
                    "protocol": json.loads((out / "protocol.json").read_text()),
                    "source_manifest": json.loads((out / "source_manifest.json").read_text()),
                    "documentation_urls": DOCS,
                    "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "minimum_forecast_lead_days": int((predictions.target_start - predictions.forecast_at).dt.days.min()),
                    "availability_note": "August 14 monthly July availability is an assumption, not historical final-data publication proof. Current revised archive values replace historical near-real-time measurements.",
                    "interpretation": "Evidence of added physical forecast information beyond fitted trend and prior-year ice, conditional on current archive. Trend-only has a lower baseline RMSE and its improvement interval crosses zero. The target is future satellite-observed ice, not independent shipping/navigation activity. This candidate followed five failed or conditional candidates; no family-wise discovery, original-release or trading claim.",
                    "sensitivity_file": "sensitivity.json"})
    panel.to_csv(out / "panel.csv", index=False)
    predictions.to_csv(out / "predictions.csv", index=False)
    diagnostics(panel, predictions, out)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.out)
    print(json.dumps(run(args.out), indent=2))
