#!/usr/bin/env python3
"""Expanding-window, crop-stage yield surprise model and trading test."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
from io import BytesIO
import hashlib
import json
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
import requests
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score

try:
    from .nasa_crop_weather_backtest import (
        fetch_power_point, metrics, normalize_gross, rv_signal, strategy_returns)
except ImportError:  # direct script execution
    from nasa_crop_weather_backtest import (
        fetch_power_point, metrics, normalize_gross, rv_signal, strategy_returns)


def load_nass(path: Path, config: dict) -> pd.DataFrame:
    raw = pd.read_csv(path, sep="\t", low_memory=False, dtype=str).fillna("")
    raw["VALUE_NUM"] = pd.to_numeric(raw["VALUE"].str.replace(",", "", regex=False), errors="coerce")
    raw["YEAR_NUM"] = pd.to_numeric(raw["YEAR"], errors="coerce")
    pieces = []
    for crop, meta in config["crops"].items():
        part = raw[
            raw["COMMODITY_DESC"].eq(meta["nass_commodity"])
            & raw["SHORT_DESC"].str.contains(meta["short_desc_contains"], regex=False)
            & raw["CLASS_DESC"].eq("ALL CLASSES")
            & raw["PRODN_PRACTICE_DESC"].eq("ALL PRODUCTION PRACTICES")
            & raw["REFERENCE_PERIOD_DESC"].eq("YEAR")
        ].copy()
        part["crop"] = crop
        pieces.append(part)
    labels = pd.concat(pieces, ignore_index=True)
    labels = labels[["crop", "STATE_ALPHA", "YEAR_NUM", "VALUE_NUM", "LOAD_TIME", "SHORT_DESC"]]
    labels.columns = ["crop", "state", "year", "yield", "load_time", "short_desc"]
    labels = labels.dropna(subset=["year", "yield"])
    labels["year"] = labels["year"].astype(int)
    labels = labels.sort_values("load_time").drop_duplicates(["crop", "state", "year"], keep="last")
    return labels.reset_index(drop=True)


def saturation_vapor_pressure(temp_c: pd.Series) -> pd.Series:
    return 0.6108 * np.exp(17.27 * temp_c / (temp_c + 237.3))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def stage_features(daily: pd.DataFrame, config: dict, end_year: int) -> pd.DataFrame:
    rows = []
    for crop, crop_meta in config["crops"].items():
        crop_states = {s: m for s, m in config["states"].items() if float(m.get(crop, 0)) > 0}
        for state in crop_states:
            state_daily = daily[daily["region"].eq(state)].copy()
            state_daily["vpd"] = (
                saturation_vapor_pressure(state_daily["T2M_MAX"])
                - saturation_vapor_pressure(state_daily["T2MDEW"])
            ).clip(lower=0)
            state_daily["gdd"] = (
                ((state_daily["T2M_MAX"].clip(upper=30) + state_daily["T2M_MIN"].clip(lower=10)) / 2) - 10
            ).clip(lower=0)
            state_daily["kdd"] = (state_daily["T2M_MAX"] - 29).clip(lower=0)
            state_daily["heat_days"] = (state_daily["T2M_MAX"] > 35).astype(float)
            for year in range(config["start_year"], end_year + 1):
                accumulated: dict[str, float] = {}
                for stage_number, (stage, dates) in enumerate(crop_meta["stages"].items(), start=1):
                    lo, hi = pd.Timestamp(f"{year}-{dates[0]}"), pd.Timestamp(f"{year}-{dates[1]}")
                    x = state_daily.loc[lo:hi]
                    if len(x) < 20:
                        continue
                    prefix = f"s{stage_number}_"
                    accumulated.update({
                        prefix + "precip": float(x["PRECTOTCORR"].sum()),
                        prefix + "gdd": float(x["gdd"].sum()),
                        prefix + "kdd": float(x["kdd"].sum()),
                        prefix + "vpd": float(x["vpd"].mean()),
                        prefix + "rootwet": float(x["GWETROOT"].mean()),
                        prefix + "et": float(x["EVPTRNS"].sum()),
                        prefix + "radiation": float(x["ALLSKY_SFC_SW_DWN"].sum()),
                        prefix + "heat_days": float(x["heat_days"].sum()),
                    })
                    rows.append({
                        "crop": crop, "state": state, "year": year, "stage": stage,
                        "stage_number": stage_number, "forecast_date": hi,
                        "production_weight": float(crop_states[state][crop]), **accumulated,
                    })
    return pd.DataFrame(rows)


def vegscape_date_id(forecast_date: pd.Timestamp) -> str:
    start = forecast_date - pd.Timedelta(days=forecast_date.weekday())
    end = start + pd.Timedelta(days=6)
    week = int(start.isocalendar().week)
    return f"weekly_ndvi_{week}_{start:%Y.%m.%d}_{end:%Y.%m.%d}"


def fetch_ndvi_mean(fips: str, date_id: str, session: requests.Session) -> float:
    endpoint = "https://nassgeodata.gmu.edu/VegService/GetFile"
    response = session.get(endpoint, params={"fips": fips, "date": date_id}, timeout=90)
    response.raise_for_status()
    match = re.search(r"url:'([^']+)'", response.text)
    if not match:
        return np.nan
    image_response = session.get(match.group(1), timeout=90)
    image_response.raise_for_status()
    pixels = np.asarray(Image.open(BytesIO(image_response.content)), dtype=float)
    valid = pixels[(pixels > 0) & (pixels <= 250)]
    return float((valid.mean() - 125.0) / 125.0) if len(valid) else np.nan


def add_vegscape_ndvi(features: pd.DataFrame, config: dict, cache_path: Path,
                      refresh: bool = False) -> pd.DataFrame:
    cached: dict[str, float] = {}
    if cache_path.exists() and not refresh:
        cached = json.loads(cache_path.read_text())
    jobs: dict[str, tuple[str, str]] = {}
    for _, row in features.iterrows():
        fips = config["states"][row["state"]]["ndvi_county_fips"]
        date_id = vegscape_date_id(pd.Timestamp(row["forecast_date"]))
        key = f"{fips}:{date_id}"
        if key not in cached:
            jobs[key] = (fips, date_id)

    def work(item: tuple[str, tuple[str, str]]) -> tuple[str, float]:
        key, (fips, date_id) = item
        with requests.Session() as session:
            try:
                return key, fetch_ndvi_mean(fips, date_id, session)
            except Exception:
                return key, np.nan

    if jobs:
        with cf.ThreadPoolExecutor(max_workers=12) as pool:
            for i, (key, value) in enumerate(pool.map(work, jobs.items()), start=1):
                cached[key] = value
                if i % 100 == 0:
                    cache_path.write_text(json.dumps(cached, sort_keys=True))
        cache_path.write_text(json.dumps(cached, sort_keys=True))

    enriched = features.copy()
    enriched["current_ndvi"] = [
        cached.get(
            f"{config['states'][row.state]['ndvi_county_fips']}:{vegscape_date_id(pd.Timestamp(row.forecast_date))}",
            np.nan)
        for row in enriched.itertuples()
    ]
    for (_, _, _), idx in enriched.groupby(["crop", "state", "year"]).groups.items():
        ordered = enriched.loc[idx].sort_values("stage_number")
        prior = np.nan
        for row_idx in ordered.index:
            stage_number = int(enriched.at[row_idx, "stage_number"])
            current = enriched.at[row_idx, "current_ndvi"]
            enriched.at[row_idx, f"s{stage_number}_ndvi"] = current
            enriched.at[row_idx, f"s{stage_number}_ndvi_delta"] = current - prior if pd.notna(prior) else np.nan
            prior = current
    return enriched.drop(columns="current_ndvi")


def add_point_in_time_trend(panel: pd.DataFrame, minimum: int) -> pd.DataFrame:
    panel = panel.copy()
    panel["trend_yield"] = np.nan
    for (_, _), idx in panel.groupby(["crop", "state"]).groups.items():
        ordered = panel.loc[idx].sort_values("year")
        # one target observation per state/crop/year, repeated over stages
        annual = ordered.drop_duplicates("year")[["year", "yield"]].dropna()
        for row_idx in ordered.index:
            year = int(panel.at[row_idx, "year"])
            prior = annual[annual["year"] < year]
            if len(prior) >= minimum:
                coef = np.polyfit(prior["year"], prior["yield"], 1)
                panel.at[row_idx, "trend_yield"] = np.polyval(coef, year)
    panel["yield_anomaly"] = panel["yield"] / panel["trend_yield"] - 1
    return panel


def expanding_predictions(panel: pd.DataFrame, minimum_years: int,
                          include_optical: bool = True) -> pd.DataFrame:
    outputs = []
    excluded = {"crop", "state", "year", "stage", "stage_number", "forecast_date",
                "production_weight", "yield", "load_time", "short_desc", "trend_yield",
                "yield_anomaly"}
    for crop in sorted(panel["crop"].unique()):
        for stage_number in sorted(panel["stage_number"].unique()):
            subset = panel[(panel["crop"] == crop) & (panel["stage_number"] == stage_number)].copy()
            numeric = [c for c in subset.columns if c not in excluded and c.startswith("s")]
            if not include_optical:
                numeric = [c for c in numeric if "ndvi" not in c]
            for year in sorted(subset["year"].unique()):
                train = subset[(subset["year"] < year) & subset["yield_anomaly"].notna()]
                test = subset[(subset["year"] == year) & subset["yield_anomaly"].notna()]
                if train["year"].nunique() < minimum_years or test.empty:
                    continue
                states = sorted(set(train["state"]) | set(test["state"]))
                x_train = pd.concat([train[numeric], pd.get_dummies(train["state"]).reindex(columns=states, fill_value=0)], axis=1)
                x_test = pd.concat([test[numeric], pd.get_dummies(test["state"]).reindex(columns=states, fill_value=0)], axis=1)
                medians = x_train.median()
                x_train, x_test = x_train.fillna(medians).fillna(0), x_test.fillna(medians).fillna(0)
                model = RandomForestRegressor(
                    n_estimators=400, max_depth=5, min_samples_leaf=5,
                    max_features=0.75, random_state=17, n_jobs=-1)
                model.fit(x_train, train["yield_anomaly"])
                pred = test[["crop", "state", "year", "stage", "stage_number", "forecast_date",
                             "production_weight", "yield_anomaly", "trend_yield", "yield"]].copy()
                pred["predicted_yield_anomaly"] = model.predict(x_test)
                pred["predicted_yield"] = pred["trend_yield"] * (1 + pred["predicted_yield_anomaly"])
                pred["model"] = "weather_plus_optical" if include_optical else "weather_only"
                outputs.append(pred)
    if not outputs:
        raise RuntimeError("No expanding predictions; inspect yield-label filters and coverage")
    return pd.concat(outputs, ignore_index=True)


def prediction_metrics(pred: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    state_rows = []
    for (model, crop, stage), x in pred.groupby(["model", "crop", "stage"]):
        state_rows.append({
            "model": model, "crop": crop, "stage": stage, "n": len(x),
            "r2": r2_score(x["yield_anomaly"], x["predicted_yield_anomaly"]),
            "rmse_anomaly_pct": math.sqrt(mean_squared_error(x["yield_anomaly"], x["predicted_yield_anomaly"])) * 100,
            "baseline_rmse_pct": math.sqrt(mean_squared_error(x["yield_anomaly"], np.zeros(len(x)))) * 100,
            "direction_accuracy_pct": (np.sign(x["yield_anomaly"]) == np.sign(x["predicted_yield_anomaly"])).mean() * 100,
        })
    def aggregate(x: pd.DataFrame) -> pd.Series:
        weights = x["production_weight"] / x["production_weight"].sum()
        return pd.Series({
            "actual_yield_anomaly": np.average(x["yield_anomaly"], weights=weights),
            "predicted_yield_anomaly": np.average(x["predicted_yield_anomaly"], weights=weights),
        })
    national = pred.groupby(["model", "crop", "year", "stage", "stage_number", "forecast_date"]).apply(
        aggregate, include_groups=False).reset_index()
    return pd.DataFrame(state_rows), national


def national_metrics(national: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (model, crop, stage), x in national.groupby(["model", "crop", "stage"]):
        rows.append({
            "model": model, "crop": crop, "stage": stage, "n_years": len(x),
            "r2": r2_score(x["actual_yield_anomaly"], x["predicted_yield_anomaly"]),
            "rmse_anomaly_pct": math.sqrt(mean_squared_error(
                x["actual_yield_anomaly"], x["predicted_yield_anomaly"])) * 100,
            "correlation": x["actual_yield_anomaly"].corr(x["predicted_yield_anomaly"]),
            "direction_accuracy_pct": (
                np.sign(x["actual_yield_anomaly"]) == np.sign(x["predicted_yield_anomaly"])
            ).mean() * 100,
        })
    return pd.DataFrame(rows)


def backtest(national: pd.DataFrame, weekly_prices: pd.DataFrame, cost_bps: float) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    price_returns = weekly_prices[["corn", "soy", "wheat"]].pct_change().fillna(0)
    yield_positions = pd.DataFrame(0.0, index=weekly_prices.index, columns=price_returns.columns)
    revision_positions = yield_positions.copy()
    for crop, x in national.groupby("crop"):
        x = x.sort_values("forecast_date")
        for _, season in x.groupby("year"):
            season = season.sort_values("forecast_date")
            previous_forecast = 0.0
            for _, row in season.iterrows():
                starts = weekly_prices.index[weekly_prices.index > row["forecast_date"]]
                if len(starts) == 0:
                    continue
                start = starts[0]
                later = season[season["forecast_date"] > row["forecast_date"]]
                if not later.empty:
                    stop = weekly_prices.index[weekly_prices.index > later["forecast_date"].min()][0]
                else:
                    stop = start + pd.Timedelta(weeks=6)
                mask = (yield_positions.index >= start) & (yield_positions.index < stop)
                forecast = float(row["predicted_yield_anomaly"])
                yield_positions.loc[mask, crop] = np.clip(-forecast / 0.10, -1, 1)
                revision_positions.loc[mask, crop] = np.clip(-(forecast - previous_forecast) / 0.05, -1, 1)
                previous_forecast = forecast
    yield_positions = normalize_gross(yield_positions)
    revision_positions = normalize_gross(revision_positions)
    rv = rv_signal(weekly_prices[["corn", "soy", "wheat"]], 156, 78).shift(1).fillna(0)
    combined = normalize_gross(0.5 * yield_positions + 0.5 * rv)
    revision_combined = normalize_gross(0.5 * revision_positions + 0.5 * rv)
    positions = {
        "yield_surprise": yield_positions,
        "yield_forecast_revision": revision_positions,
        "grain_relative_value": rv,
        "yield_plus_relative_value": combined,
        "yield_revision_plus_relative_value": revision_combined,
    }
    returns, result = {}, {}
    # Use one common, genuinely out-of-sample window.  Earlier price history is
    # retained for RV lookbacks, but must not dilute the yield strategies with
    # zero returns before the first expanding-window forecast existed.
    evaluation_start = pd.Timestamp(national["forecast_date"].min())
    for name, pos in positions.items():
        ret, turnover = strategy_returns(price_returns, pos, cost_bps)
        returns[name] = ret
        result[name] = metrics(ret.loc[evaluation_start:], turnover.loc[evaluation_start:])
    return result, pd.DataFrame(returns), pd.concat(positions, axis=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/crop_yield_model.json"))
    parser.add_argument("--output", type=Path, default=Path("research/yield_model"))
    parser.add_argument("--refresh-weather", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    label_path = args.output / "raw/nass_state_yields.tsv"
    if not label_path.exists() or label_path.stat().st_size < 1000:
        raise RuntimeError("Filtered NASS bulk file is missing; run the documented ingest first")
    labels = load_nass(label_path, config)
    last_label_year = min(int(labels["year"].max()), int(config["max_completed_year"]))
    labels = labels[labels["year"] <= last_label_year].copy()
    weather_parts = []
    parameter_string = ",".join(config["power_parameters"])
    # fetch_power_point uses this module-level setting in its source module.
    try:
        from . import nasa_crop_weather_backtest as weather_module
    except ImportError:
        import nasa_crop_weather_backtest as weather_module
    old_parameters = weather_module.PARAMETERS
    weather_module.PARAMETERS = parameter_string
    try:
        for state, meta in config["states"].items():
            weather_parts.append(fetch_power_point(
                state, meta["lat"], meta["lon"], f"{config['start_year']}0101", f"{last_label_year}1231",
                args.output / "raw/power_yield", args.refresh_weather))
    finally:
        weather_module.PARAMETERS = old_parameters
    daily = pd.concat(weather_parts).sort_index()
    features = stage_features(daily, config, last_label_year)
    features = add_vegscape_ndvi(features, config, args.output / "raw/vegscape_ndvi_means.json")
    panel = features.merge(labels, on=["crop", "state", "year"], how="inner")
    panel = add_point_in_time_trend(panel, config["minimum_training_years"])
    predictions = pd.concat([
        expanding_predictions(panel, config["minimum_training_years"], include_optical=False),
        expanding_predictions(panel, config["minimum_training_years"], include_optical=True),
    ], ignore_index=True)
    state_metrics, national = prediction_metrics(predictions)
    national_metric_frame = national_metrics(national)
    weekly_prices = pd.read_parquet("research/weather_commodities/weekly_prices.parquet")
    # W-FRI resampling labels an incomplete current week with the coming Friday.
    # Exclude it so the artifact never appears to contain a future observation.
    weekly_prices = weekly_prices[weekly_prices.index <= pd.Timestamp.today().normalize()]
    trading, returns, positions = backtest(
        national[national["model"] == "weather_plus_optical"], weekly_prices,
        config["transaction_cost_bps_per_turnover"])

    panel.to_parquet(args.output / "stage_feature_panel.parquet", index=False)
    predictions.to_parquet(args.output / "state_yield_predictions.parquet", index=False)
    national.to_parquet(args.output / "national_yield_vintages.parquet", index=False)
    state_metrics.to_csv(args.output / "prediction_metrics.csv", index=False)
    national_metric_frame.to_csv(args.output / "national_prediction_metrics.csv", index=False)
    returns.to_parquet(args.output / "strategy_returns.parquet")
    positions.to_parquet(args.output / "positions.parquet")
    (args.output / "metrics.json").write_text(json.dumps({"trading": trading}, indent=2))
    manifest = {
        "model_policy": "expanding_year; test crop year trained only on earlier years",
        "sources": {
            "yield_labels": config["nass_bulk_url"],
            "weather": "NASA POWER daily API (MERRA-2/POWER)",
            "optical": "USDA VegScape weekly NDVI derived from NASA MODIS",
            "prices": "Yahoo Finance generic front-month futures cache",
        },
        "coverage": {"start_year": config["start_year"], "last_completed_yield_year": last_label_year},
        "counts": {"labels": len(labels), "panel_rows": len(panel), "predictions": len(predictions)},
        "input_sha256": {
            "nass_state_yields.tsv": file_sha256(label_path),
            "vegscape_ndvi_means.json": file_sha256(args.output / "raw/vegscape_ndvi_means.json"),
        },
        "known_limit": "Current reprocessed archives are not original release-vintage reconstructions.",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    for ax, crop in zip(axes, ["corn", "soy", "wheat"]):
        x = national[(national["crop"] == crop) & (national["model"] == "weather_plus_optical")]
        for stage, y in x.groupby("stage"):
            ax.plot(y["year"], y["predicted_yield_anomaly"] * 100, marker=".", label=f"forecast: {stage}")
        actual = x.sort_values("stage_number").drop_duplicates("year", keep="last")
        ax.plot(actual["year"], actual["actual_yield_anomaly"] * 100, color="black", lw=2, label="actual")
        ax.axhline(0, color="gray", lw=0.7)
        ax.set_title(crop.title())
        ax.set_ylabel("Yield anomaly %")
        ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(args.output / "yield_forecasts.png", dpi=160)
    plt.close(fig)

    report = f"""# Stage-aware crop yield and trading experiment

## Outcome

This expanding-window model predicts detrended state yield from stage-aligned NASA POWER
weather, evapotranspiration, root-zone wetness and VPD features. The multimodal version adds
USDA VegScape weekly NDVI derived from NASA MODIS for representative high-production counties.
Every test year is fit only on earlier crop years.

The optical ablation is in `prediction_metrics.csv` and `national_prediction_metrics.csv`.
The trading results below use the multimodal model and 10 bps per unit of turnover:

```json
{json.dumps(trading, indent=2)}
```

## Interpretation

The yield model has modest genuine out-of-year skill, especially after flowering. Trading the
forecast level fails, while trading only the stage-to-stage forecast revision is positive but
statistically weak and has a severe drawdown. The revision signal does not improve the
relative-value strategy on a risk-adjusted basis. This is consistent with readily observable
weather and vegetation being incorporated into futures before our stage endpoints.

## Reproduce

```bash
python3 src/nass_yield_ingest.py \\
  --url {config['nass_bulk_url']} \\
  --output research/yield_model/raw/nass_state_yields.tsv
python3 src/crop_yield_model.py
python3 -m unittest tests.test_crop_yield_model tests.test_nasa_crop_weather_backtest -v
```

## Important limits

- Final revised USDA yields are outcome labels; they are never used before their crop year in fitting.
- VegScape is a current historical archive, not a preserved file-by-file release-vintage database.
- Representative-county NDVI is an economical prototype, not a full crop-mask-weighted state composite.
- SAR is not silently backfilled before Sentinel-1. A separate 2015+ SAR extension still requires
  authenticated or cloud-native Sentinel-1 processing and crop masks.
- Yahoo generic futures are research proxies and lack institutional roll construction.
"""
    (args.output / "report.md").write_text(report)
    print(json.dumps({"labels": len(labels), "panel": len(panel), "predictions": len(predictions),
                      "prediction_metrics": state_metrics.to_dict("records"),
                      "national_prediction_metrics": national_metric_frame.to_dict("records"),
                      "trading": trading}, indent=2))


if __name__ == "__main__":
    main()
