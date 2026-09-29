#!/usr/bin/env python3
"""Test delayed satellite irradiance before an annual plant-production release.

An annual respondent's monthly detail is not publicly available each month.
Training uses a November 1 following-year release proxy, including every model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/annual_solar"
GOES = ROOT / "results/satellite_validation/goes_solar"
BASELINES = ["season_trend", "weather", "persistence", "seasonal_mean"]
MODELS = [*BASELINES, "satellite", "irradiance_only", "prior_year_irradiance_placebo"]


def load_panel(out: Path = DEFAULT) -> pd.DataFrame:
    response = json.loads((GOES / "inputs/eia_57439.json").read_text())["response"]
    data = pd.DataFrame(response["data"])
    if int(response["total"]) != len(data):
        raise ValueError("Incomplete EIA response")
    data = data.loc[(data.plantCode == "57439") & (data.fuel2002 == "SUN") & (data.primeMover == "PV")].copy()
    if data.period.duplicated().any():
        raise ValueError("Duplicate EIA monthly outcome")
    data["date"] = pd.to_datetime(data.period) + pd.offsets.MonthEnd(0)
    data["actual_mwh"] = pd.to_numeric(data.generation, errors="raise")
    power = json.loads((out / "inputs/power_cvsr.json").read_text())["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    irradiance = pd.Series({pd.Timestamp(year=int(k[:4]), month=int(k[4:]), day=1) + pd.offsets.MonthEnd(0): v
                           for k, v in power.items() if 1 <= int(k[4:]) <= 12}, name="ghi")
    weather = pd.read_csv(GOES / "ground_weather/monthly_weather.csv")
    weather["date"] = pd.to_datetime(weather.date) + pd.offsets.MonthEnd(0)
    weather = weather.set_index("date")
    if weather.index.duplicated().any():
        raise ValueError("Duplicate weather month")
    weather.loc[~weather.complete, ["tmean_c", "dtr_c", "prcp_mm"]] = np.nan
    panel = data.set_index("date")[["actual_mwh"]].join(irradiance).join(weather[["tmean_c", "dtr_c", "prcp_mm"]]).sort_index()
    panel["year"] = panel.index.year
    panel["month"] = panel.index.month
    panel["time"] = (panel.year - 2015) + (panel.month - 1) / 12
    panel["daily_mwh"] = panel.actual_mwh / panel.index.days_in_month
    panel["label_available_at"] = pd.to_datetime((panel.year + 1).astype(str) + "-11-01")
    panel["irradiance_available_at"] = pd.Series(panel.index + pd.DateOffset(months=4) + pd.Timedelta(days=14), index=panel.index)
    panel["previous_year_ghi"] = panel.index.map(irradiance.rename(index=lambda d: d + pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)))
    if (panel.actual_mwh <= 0).any() or (panel.ghi <= 0).any():
        raise ValueError("Nonpositive production or irradiance")
    return panel


def design(frame: pd.DataFrame, model: str) -> np.ndarray:
    columns = [np.ones(len(frame)), frame.time.to_numpy(),
               *[frame.month.eq(month).astype(float).to_numpy() for month in range(2, 13)]]
    if model in ("weather", "satellite"):
        columns += [frame.tmean_c.to_numpy(), frame.dtr_c.to_numpy(), np.log1p(frame.prcp_mm.to_numpy())]
    if model in ("satellite", "irradiance_only"):
        columns.append(np.log(frame.ghi.to_numpy()))
    return np.column_stack(columns)


def predict(panel: pd.DataFrame, lag_months: int = 4) -> pd.DataFrame:
    rows = []
    for date, row in panel.loc["2019-01-01":"2024-12-31"].iterrows():
        issue = date + pd.DateOffset(months=lag_months) + pd.Timedelta(days=14)
        record = {"date": date, "year": date.year, "forecast_at": issue,
                  "target_label_available_at": row.label_available_at, "actual_mwh": row.actual_mwh}
        if issue >= row.label_available_at:
            raise ValueError("Forecast is not earlier than target-label publication proxy")
        train = panel.loc[(panel.index < date) & (panel.label_available_at < issue)
                          & (panel.irradiance_available_at <= issue)]
        train = train.dropna(subset=["actual_mwh", "ghi", "tmean_c", "dtr_c", "prcp_mm"]).tail(48)
        target = panel.loc[[date]]
        complete = target[["ghi", "tmean_c", "dtr_c", "prcp_mm", "previous_year_ghi"]].notna().all().all()
        same_month = train.loc[train.month == date.month].tail(2)
        if len(train) < 36 or len(same_month) < 2 or not complete or row.irradiance_available_at > issue:
            record.update({"eligible": False, "n_train": len(train)})
            rows.append(record)
            continue
        record.update({"eligible": True, "n_train": len(train), "training_last_month": train.index.max(),
                       "training_latest_label_release": train.label_available_at.max(),
                       "source_available_at": row.irradiance_available_at,
                       "persistence": same_month.daily_mwh.iloc[-1] * date.days_in_month,
                       "seasonal_mean": same_month.daily_mwh.mean() * date.days_in_month})
        y = np.log(train.daily_mwh.to_numpy())
        for model in ("season_trend", "weather", "satellite", "irradiance_only"):
            coef = np.linalg.lstsq(design(train, model), y, rcond=None)[0]
            record[model] = float(np.exp((design(target, model) @ coef)[0]) * date.days_in_month)
            if model == "satellite":
                placebo = target.copy()
                placebo["ghi"] = placebo.previous_year_ghi
                record["prior_year_irradiance_placebo"] = float(np.exp((design(placebo, model) @ coef)[0]) * date.days_in_month)
                record["irradiance_coefficient"] = float(coef[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame) -> dict:
    valid = predictions.loc[predictions.eligible].copy()
    if valid.empty:
        raise ValueError("No eligible predictions")
    values = valid[MODELS].to_numpy()
    errors = values - valid.actual_mwh.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {name: {"rmse_mwh": float(np.sqrt(squared[:, i].mean())), "mae_mwh": float(np.abs(errors[:, i]).mean())}
               for i, name in enumerate(MODELS)}
    groups = [np.flatnonzero(valid.year.to_numpy() == year) for year in sorted(valid.year.unique())]
    rng = np.random.default_rng(20260927)
    samples = np.array([np.sqrt(squared[np.concatenate([groups[i] for i in rng.integers(len(groups), size=len(groups))])].mean(axis=0))
                        for _ in range(10000)])
    sat_idx = MODELS.index("satellite")
    comparisons = {}
    for name in BASELINES:
        i = MODELS.index(name)
        comparisons[name] = {"rmse_reduction_pct": 100 * (1 - metrics["satellite"]["rmse_mwh"] / metrics[name]["rmse_mwh"]),
                             "mae_reduction_pct": 100 * (1 - metrics["satellite"]["mae_mwh"] / metrics[name]["mae_mwh"]),
                             "rmse_gain_year_bootstrap_ci95_mwh": np.quantile(samples[:, i] - samples[:, sat_idx], [.025, .975]).tolist()}
    best = min(BASELINES, key=lambda name: metrics[name]["rmse_mwh"])
    gate = (comparisons[best]["rmse_reduction_pct"] >= 5
            and all(v["mae_reduction_pct"] > 0 and v["rmse_gain_year_bootstrap_ci95_mwh"][0] > 0 for v in comparisons.values())
            and metrics["satellite"]["rmse_mwh"] < metrics["prior_year_irradiance_placebo"]["rmse_mwh"])
    by_year = {}
    for year, part in valid.groupby("year"):
        rmse = {model: float(np.sqrt(np.mean((part[model] - part.actual_mwh) ** 2))) for model in MODELS}
        by_year[str(year)] = {"n_months": len(part), "rmse_mwh": rmse,
                              "improvement_vs_weather_pct": 100 * (1 - rmse["satellite"] / rmse["weather"])}
    return {"n_test_months": len(valid), "n_year_clusters": len(groups), "abstentions": int((~predictions.eligible).sum()),
            "metrics": metrics, "comparisons": comparisons, "strongest_nonsatellite_baseline": best,
            "by_year": by_year, "forecast_gate_passed": bool(gate), "original_vintage_certified": False,
            "trading_alpha_verified": False, "target": "Plant production before annual EIA disclosure, not before physical generation"}


def run(out: Path = DEFAULT) -> dict:
    panel = load_panel(out)
    predictions = predict(panel)
    summary = score(predictions)
    sensitivity = predict(panel, lag_months=5)
    summary["five_month_delay_sensitivity"] = score(sensitivity)
    summary["protocol"] = json.loads((out / "protocol.json").read_text())
    summary["reporting_frequency_audit_required"] = False
    summary["monthly_labels_independently_reported"] = False
    summary["reporting_audit"] = "CVSR pre2023 annual respondent monthly fields were allocated; this monthly test is a discarded diagnostic, not independent production validation"
    summary["forecast_usefulness_verified"] = False  # Upgrade only after the independent reporting-frequency audit.
    summary["interpretation"] = "Current-vintage delayed-disclosure forecast evaluation; annual label timing must be independently confirmed before accepting usefulness"
    panel.to_csv(out / "panel.csv", index_label="date", float_format="%.12g")
    predictions.to_csv(out / "predictions.csv", index=False, float_format="%.12g")
    sensitivity.to_csv(out / "predictions_five_month_lag.csv", index=False, float_format="%.12g")
    summary["input_hashes"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [out / "inputs/power_cvsr.json", GOES / "inputs/eia_57439.json",
                                         GOES / "ground_weather/monthly_weather.csv"]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = run(args.out)
    print(json.dumps({k: result[k] for k in ("n_test_months", "metrics", "comparisons", "forecast_gate_passed")}, indent=2))
