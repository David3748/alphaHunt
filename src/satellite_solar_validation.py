#!/usr/bin/env python3
"""Satellite irradiance ablation against independent EIA solar generation.

This verifies retrospective physical estimation value, not historical availability:
POWER's archived SYN1DEG is a revised product with 3--4 month latency. A seven-day
FLASHFlux implementation needs a separately preserved first-release archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/solar"
EIA_URL = "https://api.eia.gov/v2/electricity/facility-fuel/data/"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


def fetch(out: Path) -> None:
    import requests
    out.mkdir(parents=True, exist_ok=True)
    jobs = {
        "eia.json": (EIA_URL, {"api_key": "DEMO_KEY", "frequency": "monthly",
            "data[0]": "generation", "facets[plantCode][]": "57695",
            "start": "2015-01", "end": "2025-12", "length": 5000}),
        "power.json": (POWER_URL, {"parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
            "longitude": -120.067, "latitude": 35.383, "start": 2015, "end": 2025, "format": "JSON"}),
    }
    for name, (url, params) in jobs.items():
        response = requests.get(url, params=params, timeout=90)
        response.raise_for_status()
        response.json()
        (out / name).write_bytes(response.content)


def load_panel(inputs: Path) -> pd.DataFrame:
    data = json.loads((inputs / "eia.json").read_text())["response"]
    raw = pd.DataFrame(data["data"])
    if int(data["total"]) != len(raw):
        raise ValueError("Truncated EIA response; retrieve remaining pages")
    # EIA repeats each measurement as plant total, fuel total and prime mover.
    # Use exactly one fully specified fuel/prime-mover series, never sum totals.
    raw = raw[(raw.plantCode == "57695") & (raw.fuel2002 == "SUN") & (raw.primeMover == "PV")].copy()
    if raw.period.duplicated().any():
        raise ValueError("Duplicate plant/month labels")
    raw["date"] = pd.to_datetime(raw.period) + pd.offsets.MonthEnd(0)
    raw["generation_mwh"] = pd.to_numeric(raw.generation, errors="raise")
    raw = raw.set_index("date").sort_index()
    power = json.loads((inputs / "power.json").read_text())
    source = power["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    # POWER includes a synthetic month 13 for annual averages.
    ghi = pd.Series({pd.Timestamp(k[:4] + "-" + k[4:] + "-01") + pd.offsets.MonthEnd(0): v
                     for k, v in source.items() if 1 <= int(k[4:]) <= 12}, name="ghi_kwh_m2_day")
    panel = raw[["generation_mwh"]].join(ghi)
    if panel.index.duplicated().any() or panel.isna().any().any() or (panel <= 0).any().any():
        raise ValueError("Incomplete or nonpositive generation/irradiance")
    if not panel.index.equals(pd.date_range(panel.index.min(), panel.index.max(), freq="ME")):
        raise ValueError("Monthly observations have gaps")
    panel["daily_mwh"] = panel.generation_mwh / panel.index.days_in_month
    panel["month"] = panel.index.month
    panel["year"] = panel.index.year
    panel["time"] = np.arange(len(panel)) / 12
    panel["prior_year_ghi"] = panel.ghi_kwh_m2_day.shift(12)
    return panel


def design(frame: pd.DataFrame, model: str) -> np.ndarray:
    x = np.column_stack([np.ones(len(frame)), frame.time,
                         *[(frame.month == m).astype(float) for m in range(2, 13)]])
    if model != "season_trend":
        column = "ghi_kwh_m2_day" if model == "satellite" else "prior_year_ghi"
        x = np.column_stack([x, np.log(frame[column])])
    return x


def predict(panel: pd.DataFrame, start: str = "2020-01-31", end: str = "2025-12-31") -> pd.DataFrame:
    """Same rolling OLS for every model; no tuning on evaluation observations.

    48 most recent eligible months, monthly effects + time trend, then one
    additional log-irradiance term for the satellite model. Labels are embargoed
    two complete months to represent EIA reporting delay, though their precise
    original publication/revision times are not in this archive.
    """
    rows = []
    for date, row in panel.loc[start:end].iterrows():
        cutoff = date - pd.offsets.MonthEnd(2)
        # Same training rows for all three models, including the placebo.
        train = panel.loc[panel.index <= cutoff].dropna(subset=["prior_year_ghi"]).tail(48)
        if len(train) < 36:
            continue
        target = panel.loc[[date]]
        output = {"date": date, "year": date.year, "actual_mwh": row.generation_mwh,
                  "training_last_month": train.index.max(), "n_train": len(train)}
        y = np.log(train.daily_mwh.to_numpy())
        for model in ("season_trend", "satellite", "prior_year_placebo"):
            coeff = np.linalg.lstsq(design(train, model), y, rcond=None)[0]
            output[model] = float(np.exp((design(target, model) @ coeff)[0]) * date.days_in_month)
        output["seasonal_persistence"] = float(panel.loc[date - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0), "daily_mwh"] * date.days_in_month)
        rows.append(output)
    return pd.DataFrame(rows)


def metrics(pred: pd.DataFrame) -> dict:
    actual = pred.actual_mwh.to_numpy()
    scores = {}
    for model in ("season_trend", "satellite", "prior_year_placebo", "seasonal_persistence"):
        error = pred[model].to_numpy() - actual
        scores[model] = {"mae_mwh": float(np.abs(error).mean()), "rmse_mwh": float(np.sqrt(np.mean(error ** 2)))}
    baseline = scores["season_trend"]
    scores["satellite"]["rmse_improvement_pct"] = 100 * (1 - scores["satellite"]["rmse_mwh"] / baseline["rmse_mwh"])
    scores["satellite"]["mae_improvement_pct"] = 100 * (1 - scores["satellite"]["mae_mwh"] / baseline["mae_mwh"])
    groups = [v for _, v in pred.groupby("year")]
    rng = np.random.default_rng(74913)
    draws = []
    for _ in range(5000):
        sample = pd.concat([groups[i] for i in rng.integers(len(groups), size=len(groups))])
        b = np.mean((sample.season_trend - sample.actual_mwh) ** 2)
        s = np.mean((sample.satellite - sample.actual_mwh) ** 2)
        draws.append(100 * (1 - np.sqrt(s / b)))
    return {"n_months": len(pred), "n_year_blocks": len(groups), "models": scores,
            "satellite_rmse_improvement_95pct_year_block_ci": np.quantile(draws, [0.025, 0.975]).tolist()}


def evaluate(inputs: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    panel = load_panel(inputs)
    pred = predict(panel)
    score = metrics(pred)
    by_year = {str(year): metrics_simple(group) for year, group in pred.groupby("year")}
    # This diagnostic tests independent outcome prediction; it deliberately does
    # not equate a positive archive ablation with a historically available signal.
    models = score["models"]
    retrospective_pass = (models["satellite"]["rmse_improvement_pct"] >= 5
        and score["satellite_rmse_improvement_95pct_year_block_ci"][0] > 0
        and models["satellite"]["rmse_mwh"] < models["prior_year_placebo"]["rmse_mwh"]
        and models["satellite"]["rmse_mwh"] < models["seasonal_persistence"]["rmse_mwh"])
    summary = {
        "candidate": "CERES solar irradiance -> Topaz monthly EIA electricity generation",
        "plant_code": "57695", "coordinates": [35.383, -120.067],
        "model_specification": "Rolling 48 eligible months; OLS log daily MWh on calendar month + linear time, plus log GHI; two-month outcome embargo; same sample for all ablations",
        "evaluation_period": ["2020-01", "2025-12"],
        "metrics": score, "by_year": by_year,
        "retrospective_estimation_improvement_pass": bool(retrospective_pass),
        "forecast_usefulness_verified": False,
        "original_vintage_operational_verification": False,
        "trading_alpha_verified": False,
        "availability": {
            "downloaded_product": "NASA POWER historical SYN1DEG, current vintage",
            "historical_latency": "Approximately 3-4 months; cannot be backdated to month-end +7d",
            "potential_live_product": "FLASHFlux has 5-7 day latency but is a different product; original releases not used here",
            "meaning": "Independent measurement-to-outcome validation only. A pre-release nowcast backtest remains uncertified."
        },
        "limitations": ["One plant selected for size and stable operating history, not a representative asset sample", "No contemporaneous analyst expectations or market-return test", "Current-vintage EIA generation can include revisions", "Year-block bootstrap has only six blocks", "Added after negative results in other mechanisms; exploratory selection must be disclosed"],
        "sources": [EIA_URL, POWER_URL, "https://power.larc.nasa.gov/docs/methodology/data/sources/", "https://power.larc.nasa.gov/docs/services/api/temporal/monthly/", "https://www.eia.gov/electricity/data/browser/#/plant/57695"],
        "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (inputs / "eia.json", inputs / "power.json")}
    }
    panel.to_csv(out / "monthly_inputs.csv", index_label="date", float_format="%.10g")
    pred.to_csv(out / "predictions.csv", index=False, float_format="%.10g")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def metrics_simple(group: pd.DataFrame) -> dict:
    b = np.sqrt(np.mean((group.season_trend - group.actual_mwh) ** 2))
    s = np.sqrt(np.mean((group.satellite - group.actual_mwh) ** 2))
    return {"n": len(group), "baseline_rmse_mwh": float(b), "satellite_rmse_mwh": float(s), "improvement_pct": float(100 * (1 - s / b))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DEFAULT / "inputs")
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.inputs)
    print(json.dumps(evaluate(args.inputs, args.out)["metrics"], indent=2))
