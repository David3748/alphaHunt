#!/usr/bin/env python3
"""Frozen September OISST -> following Texas winter rainfall forecast test.

This is a current-vintage chronological hindcast, not an original-release
backtest or an estimate of incremental satellite value over in-situ ENSO data.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/enso"
URLS = {
    "sstoi.indices": "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices",
    "texas_winter.csv": "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/41/pcp/3/2/1982-2026.csv",
}
DOCUMENTATION = {
    "cpc_index_definition": "https://www.cpc.ncep.noaa.gov/data/indices/",
    "cpc_revision_history": "https://www.cpc.ncep.noaa.gov/data/indices/Readme.index.shtml",
    "oisst_satellite_and_in_situ_inputs": "https://www.ncei.noaa.gov/products/optimum-interpolation-sst",
    "oisst_final_two_week_latency": "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01606",
    "physical_mechanism": "https://www.climate.gov/news-features/blogs/enso/la-ninas-delayed-effect-sizzling-texas-summers",
}


def fetch(inputs: Path) -> None:
    import requests
    inputs.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        (inputs / name).write_bytes(response.content)


def load_panel(inputs: Path) -> pd.DataFrame:
    sst = pd.read_csv(inputs / "sstoi.indices", sep=r"\s+")
    rain_path = inputs / "texas_winter.csv"
    header = rain_path.read_text().splitlines()[:2]
    if "Texas December-February Precipitation" not in header[0] or "Inches" not in header[1]:
        raise ValueError("Expected Texas December-February precipitation in inches")
    rain = pd.read_csv(rain_path, comment="#")
    if sst.duplicated(["YR", "MON"]).any() or rain.Date.duplicated().any():
        raise ValueError("Duplicate source month")
    if not (rain.Date % 100).eq(2).all():
        raise ValueError("Winter precipitation must be indexed by February")
    sst = sst.loc[sst.MON.eq(9), ["YR", "NINO3.4"]].copy()
    # Absolute September SST avoids use of future years in the anomaly normal.
    # A fixed September anomaly gives identical predictions with an intercept.
    sst["winter_year"] = sst.YR + 1
    rain["winter_year"] = rain.Date // 100
    panel = sst.merge(rain, on="winter_year", validate="one_to_one")
    panel = panel.rename(columns={"NINO3.4": "september_sst_c", "Value": "rain_inches"})
    panel = panel.sort_values("winter_year").reset_index(drop=True)
    if panel.empty or panel[["september_sst_c", "rain_inches"]].isna().any().any():
        raise ValueError("Missing aligned observations")
    if not panel.september_sst_c.between(15, 35).all() or (panel.rain_inches < 0).any():
        raise ValueError("Invalid physical measurement or missing sentinel")
    if not panel.winter_year.diff().dropna().eq(1).all():
        raise ValueError("Missing aligned winter")
    panel["predictor_end"] = pd.to_datetime(panel.YR.astype(str) + "-09-30")
    panel["assumed_predictor_available"] = panel.predictor_end + pd.Timedelta(days=14)
    panel["forecast_at"] = pd.to_datetime(panel.YR.astype(str) + "-10-15")
    panel["target_start"] = pd.to_datetime(panel.YR.astype(str) + "-12-01")
    panel["target_end"] = pd.to_datetime([
        f"{y}-02-{calendar.monthrange(int(y), 2)[1]}" for y in panel.winter_year
    ])
    # Conservative training-label embargo; actual historical issue dates absent.
    panel["assumed_label_available"] = pd.to_datetime(panel.winter_year.astype(str) + "-04-01")
    return panel.drop(columns=["Date", "YR"])


def predict(panel: pd.DataFrame, initial_train: int = 15) -> pd.DataFrame:
    """Expanding OLS, no tuning, following the on-disk frozen protocol."""
    rows = []
    panel = panel.sort_values("winter_year")
    for _, row in panel.iterrows():
        if not row.assumed_predictor_available < row.forecast_at < row.target_start:
            raise ValueError("Feature availability must precede forecast and target")
        train = panel.loc[(panel.winter_year < row.winter_year)
                          & (panel.assumed_label_available < row.forecast_at)]
        if len(train) < initial_train:
            continue
        if int(train.winter_year.iloc[-1]) != int(row.winter_year) - 1:
            raise ValueError("Prior-winter comparator unavailable")
        x = np.column_stack([np.ones(len(train)), train.september_sst_c])
        coefficients = np.linalg.lstsq(x, train.rain_inches, rcond=None)[0]
        record = row.to_dict()
        record.update({"n_train": len(train), "training_last_winter": int(train.winter_year.iloc[-1]),
                       "satellite": float(coefficients @ [1, row.september_sst_c]),
                       "climatology": float(train.rain_inches.mean()),
                       "persistence": float(train.rain_inches.iloc[-1]),
                       "sst_coefficient_inches_per_c": float(coefficients[1])})
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame) -> dict:
    models = ["satellite", "climatology", "persistence"]
    errors = predictions[models].to_numpy() - predictions.rain_inches.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {model: {"rmse_inches": float(np.sqrt(squared[:, i].mean())),
                       "mae_inches": float(np.abs(errors[:, i]).mean())}
               for i, model in enumerate(models)}
    # Resample paired winters, preserving adjacent two-winter dependence.
    rng = np.random.default_rng(20260926)
    n = len(predictions)
    starts = rng.integers(0, n, size=(10000, (n + 1) // 2, 1))
    indices = ((starts + np.arange(2)) % n).reshape(10000, -1)[:, :n]
    boot_rmse = np.sqrt(squared[indices].mean(axis=1))
    comparisons = {}
    for i, baseline in enumerate(models[1:], 1):
        gain = metrics[baseline]["rmse_inches"] - metrics["satellite"]["rmse_inches"]
        comparisons[baseline] = {
            "rmse_reduction_fraction": gain / metrics[baseline]["rmse_inches"],
            "rmse_reduction_inches": gain,
            "rmse_reduction_ci95_inches": np.quantile(boot_rmse[:, i] - boot_rmse[:, 0], [.025, .975]).tolist(),
            "mae_reduction_fraction": 1 - metrics["satellite"]["mae_inches"] / metrics[baseline]["mae_inches"],
            "winters_lower_squared_error": int((squared[:, 0] < squared[:, i]).sum()),
        }
    gate = all(c["rmse_reduction_fraction"] >= .05 and c["mae_reduction_fraction"] > 0
               for c in comparisons.values()) and comparisons["climatology"]["rmse_reduction_ci95_inches"][0] > 0
    return {"n_test_winters": n, "first_test_winter": int(predictions.winter_year.min()),
            "last_test_winter": int(predictions.winter_year.max()), "metrics": metrics,
            "comparisons": comparisons, "predefined_forecast_gate_passed": bool(gate),
            "status": "forecast_gate_passed_current_vintage" if gate else "provisional_point_improvement_uncertainty_gate_failed",
            "original_vintage_operational_verification": False, "incremental_satellite_vs_in_situ_verified": False,
            "trading_alpha_verified": False}


def run(out: Path = DEFAULT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    panel = load_panel(out / "inputs")
    predictions = predict(panel)
    summary = score(predictions)
    summary.update({"protocol": json.loads((out / "protocol.json").read_text()),
                    "source_urls": URLS, "documentation_urls": DOCUMENTATION,
                    "input_sha256": {name: hashlib.sha256((out / "inputs" / name).read_bytes()).hexdigest()
                                     for name in URLS},
                    "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "forecast_minimum_lead_days": int((predictions.target_start - predictions.forecast_at).dt.days.min()),
                    "availability": {"predictor": "Assumed September end +14 days, from OISST final latency; CPC monthly original release timestamps not preserved",
                                     "labels": "Assumed April 1 after target winter; current revised NCEI archive",
                                     "vintage": "Current OISST index includes historical reprocessing and in-situ inputs; chronological hindcast only"}})
    panel.to_csv(out / "panel.csv", index=False)
    predictions.to_csv(out / "predictions.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.out / "inputs")
    print(json.dumps(run(args.out), indent=2))
