#!/usr/bin/env python3
"""Audit the incremental forecast value of actual MODIS vegetation observations.

The checked-in compact inputs permit offline reruns. They are current-archive
research inputs, not original release vintages. A forecast improvement is not
proof of trading alpha. No parameter is selected using evaluation years.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image
from sklearn.ensemble import RandomForestRegressor

DEFAULT_OUTPUT = Path("results/satellite_validation/third_signal")
MODEL = dict(n_estimators=400, max_depth=5, min_samples_leaf=5,
             max_features=0.75, random_state=17, n_jobs=-1)
MODEL_NAMES = ("trend", "ndvi_only", "weather_only", "weather_plus_ndvi",
               "weather_plus_prior_year_ndvi")
FIRST_TEST_YEAR, LAST_TEST_YEAR = 2018, 2024
MIN_TRAINING_YEARS = 5
RELEASE_LAG_DAYS = 14
PRIMARY = ("corn", "flowering")
API = "https://nassgeo.csiss.gmu.edu/VegService/GetFile"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def weekly_composite(date: pd.Timestamp) -> tuple[str, pd.Timestamp]:
    start = date - pd.Timedelta(days=date.weekday())
    end = start + pd.Timedelta(days=6)
    return f"weekly_ndvi_{int(start.isocalendar().week)}_{start:%Y.%m.%d}_{end:%Y.%m.%d}", end


def rebuild_optical(panel: pd.DataFrame, observations: pd.DataFrame, config: dict,
                    release_lag: int = RELEASE_LAG_DAYS) -> pd.DataFrame:
    """Carry earlier stage observations forward; never fill future stages."""
    if release_lag < 0:
        raise ValueError("Release lag cannot be negative")
    lookup = observations.set_index("key")["mean_ndvi"].to_dict()
    result = panel.drop(columns=[c for c in panel if "ndvi" in c], errors="ignore").copy()
    for i in range(1, 4):
        result[f"s{i}_ndvi"] = np.nan
        result[f"s{i}_prior_year_ndvi"] = np.nan
    result["forecast_date"] = pd.to_datetime(result["forecast_date"])
    for idx, row in result.iterrows():
        fips = config["states"][row["state"]]["ndvi_county_fips"]
        ends = []
        # JSON serializers may sort stage *names*, which are not chronological.
        # Stage numbers in the inherited panel follow calendar endpoint order.
        stages = sorted(config["crops"][row["crop"]]["stages"].values(), key=lambda dates: dates[1])
        for i, dates in enumerate(stages, 1):
            if i > int(row["stage_number"]):
                break
            date_id, end = weekly_composite(pd.Timestamp(f"{row['year']}-{dates[1]}"))
            prior_id, _ = weekly_composite(pd.Timestamp(f"{row['year'] - 1}-{dates[1]}"))
            result.at[idx, f"s{i}_ndvi"] = lookup.get(fips + ":" + date_id, np.nan)
            result.at[idx, f"s{i}_prior_year_ndvi"] = lookup.get(fips + ":" + prior_id, np.nan)
            ends.append(end)
        result.at[idx, "composite_end"] = max(ends)
        result.at[idx, "available_date"] = max(ends) + pd.Timedelta(days=release_lag)
    return result


def point_in_time_trends(panel: pd.DataFrame, minimum: int = 5) -> pd.DataFrame:
    """Every trend excludes the evaluated year's yield and all later yields."""
    result = panel.copy()
    result["trend_yield"] = np.nan
    for _, idx in result.groupby(["crop", "state"]).groups.items():
        annual = result.loc[idx, ["year", "yield"]].drop_duplicates("year").sort_values("year")
        for year in annual.year:
            train = annual[annual.year < year]
            if len(train) < minimum:
                continue
            # Center years to avoid poorly conditioned calendar-year coefficients.
            center = train.year.mean()
            slope, intercept = np.polyfit(train.year - center, train["yield"], 1)
            selected = result.index.isin(idx) & result.year.eq(year)
            result.loc[selected, "trend_yield"] = intercept + slope * (year - center)
    result["yield_anomaly"] = result["yield"] / result["trend_yield"] - 1
    return result


def feature_columns(panel: pd.DataFrame, model: str, stage_number: int) -> list[str]:
    weather = [c for c in panel if re.match(r"s[123]_", c)
               and "ndvi" not in c and int(c[1]) <= stage_number]
    current = [f"s{i}_ndvi" for i in range(1, stage_number + 1)]
    prior = [f"s{i}_prior_year_ndvi" for i in range(1, stage_number + 1)]
    return {"ndvi_only": current, "weather_only": weather,
            "weather_plus_ndvi": weather + current,
            "weather_plus_prior_year_ndvi": weather + prior}[model]


def predict(panel: pd.DataFrame, first_year: int = FIRST_TEST_YEAR,
            last_year: int = LAST_TEST_YEAR, model_kwargs: dict | None = None) -> pd.DataFrame:
    outputs = []
    for (crop, stage), group in panel.groupby(["crop", "stage"]):
        stage_number = int(group.stage_number.iloc[0])
        for year in range(first_year, last_year + 1):
            train = group[(group.year < year) & group.yield_anomaly.notna()]
            test = group[group.year.eq(year) & group.yield_anomaly.notna()]
            if train.year.nunique() < MIN_TRAINING_YEARS or test.empty:
                continue
            # Columns and imputation are fitted only to the training sample.
            states = sorted(train.state.unique())
            for model in MODEL_NAMES:
                prediction = np.zeros(len(test))
                if model != "trend":
                    cols = feature_columns(group, model, stage_number)
                    a, b = train[cols], test[cols]
                    medians = a.median()
                    a, b = a.fillna(medians).fillna(0), b.fillna(medians).fillna(0)
                    a = pd.concat([a, pd.get_dummies(train.state).reindex(columns=states, fill_value=0)], axis=1)
                    b = pd.concat([b, pd.get_dummies(test.state).reindex(columns=states, fill_value=0)], axis=1)
                    estimator = RandomForestRegressor(**(model_kwargs or MODEL))
                    estimator.fit(a, train.yield_anomaly)
                    prediction = estimator.predict(b)
                frame = test[["crop", "state", "year", "stage", "stage_number", "forecast_date",
                              "composite_end", "available_date", "production_weight", "yield",
                              "trend_yield", "yield_anomaly"]].copy()
                frame["model"] = model
                frame["prediction_anomaly"] = prediction
                frame["prediction_yield"] = frame.trend_yield * (1 + prediction)
                frame["training_last_year"] = int(train.year.max())
                frame["training_n_years"] = int(train.year.nunique())
                outputs.append(frame)
    if not outputs:
        raise ValueError("Insufficient data for chronological evaluation")
    return pd.concat(outputs, ignore_index=True)


def confidence_interval_by_year(year_improvements: np.ndarray,
                                repeats: int = 10000) -> list[float]:
    """Resample whole years: correlated state observations are not independent."""
    values = np.asarray(year_improvements, dtype=float)
    rng = np.random.default_rng(17)
    draws = rng.choice(values, size=(repeats, len(values)), replace=True).mean(axis=1)
    return [float(x) for x in np.quantile(draws, [0.025, 0.975])]


def metrics(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries, annual_rows = [], []
    for (crop, stage), group in predictions.groupby(["crop", "stage"]):
        base = group[group.model.eq("trend")].set_index(["year", "state"])
        weather = group[group.model.eq("weather_only")].set_index(["year", "state"])
        for model, part in group.groupby("model"):
            x = part.set_index(["year", "state"]).sort_index()
            error = (x.prediction_anomaly - x.yield_anomaly) ** 2
            trend_error = (base.loc[x.index].yield_anomaly) ** 2
            weather_error = (weather.loc[x.index].prediction_anomaly - x.yield_anomaly) ** 2
            yearly = pd.DataFrame({"mse": error, "trend_mse": trend_error, "weather_mse": weather_error}).groupby("year").mean()
            for year, row in yearly.iterrows():
                annual_rows.append(dict(crop=crop, stage=stage, model=model, year=int(year), **row.to_dict()))
            rmse, trend_rmse, weather_rmse = (float(np.sqrt(s.mean())) for s in [error, trend_error, weather_error])
            ci = confidence_interval_by_year((yearly.weather_mse - yearly.mse).to_numpy())
            summaries.append(dict(crop=crop, stage=stage, model=model, n=len(x), n_years=len(yearly),
                                  rmse_anomaly_pct=100 * rmse, trend_rmse_pct=100 * trend_rmse,
                                  weather_rmse_pct=100 * weather_rmse,
                                  improvement_vs_trend_pct=100 * (1 - rmse / trend_rmse),
                                  improvement_vs_weather_pct=100 * (1 - rmse / weather_rmse),
                                  year_cluster_mse_improvement_ci_low=ci[0],
                                  year_cluster_mse_improvement_ci_high=ci[1]))
    return pd.DataFrame(summaries), pd.DataFrame(annual_rows)


def verify_tiff_samples(observations: pd.DataFrame, output: Path) -> list[dict]:
    """Fetch a fixed small set of original raster files and verify cache extraction."""
    lookup = observations.set_index("key").mean_ndvi
    records = []
    output.mkdir(parents=True, exist_ok=True)
    for fips in ["19169", "17113", "20191"]:
        for year in [2012, 2018, 2024]:
            date_id, _ = weekly_composite(pd.Timestamp(f"{year}-07-31"))
            key = fips + ":" + date_id
            target = output / f"{date_id}_{fips}.tif"
            response = requests.get(API, params={"fips": fips, "date": date_id}, timeout=60)
            response.raise_for_status()
            match = re.search(r"url:'([^']+)'", response.text)
            if not match:
                raise ValueError(f"No TIFF returned for {key}")
            image = requests.get(match.group(1), timeout=60)
            image.raise_for_status()
            target.write_bytes(image.content)
            pixels = np.asarray(Image.open(BytesIO(image.content)), dtype=float)
            valid = pixels[(pixels > 0) & (pixels <= 250)]
            if not len(valid):
                raise ValueError(f"No valid NDVI pixels for {key}")
            value = float((valid.mean() - 125.0) / 125.0)
            cached = float(lookup.get(key, np.nan))
            records.append(dict(key=key, api_url=response.url, raster_url=match.group(1),
                                sha256=sha256(target), path=str(target), valid_pixels=len(valid),
                                raster_mean_ndvi=value, cached_mean_ndvi=cached,
                                absolute_difference=abs(value - cached),
                                fetched_at=datetime.now(timezone.utc).isoformat()))
    return records


def snapshot_inputs(source: Path, output: Path, config_path: Path) -> None:
    """Adopt existing real-data caches, preserving source-file hashes and lineage."""
    output.mkdir(parents=True, exist_ok=True)
    panel_path = source / "stage_feature_panel.parquet"
    optical_path = source / "raw/vegscape_ndvi_means.json"
    label_path = source / "raw/nass_state_yields.tsv"
    panel = pd.read_parquet(panel_path)
    panel = panel[panel.year <= LAST_TEST_YEAR].copy()
    # Recompute optical features and trends; inherited derived columns are not trusted.
    panel = panel.drop(columns=[c for c in panel if "ndvi" in c] + ["trend_yield", "yield_anomaly"])
    panel.to_csv(output / "input_panel.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    raw = json.loads(optical_path.read_text())
    pd.DataFrame([dict(key=k, mean_ndvi=v) for k, v in sorted(raw.items())]).to_csv(output / "input_ndvi.csv", index=False)
    config = json.loads(config_path.read_text())
    (output / "input_config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
    lineage = {
        "adopted_at": datetime.now(timezone.utc).isoformat(),
        "source_file_sha256": {p.name: sha256(p) for p in [panel_path, optical_path, label_path, config_path]},
        "source_paths_at_ingest": [str(p) for p in [panel_path, optical_path, label_path]],
        "satellite_source": API,
        "metadata": "https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_VegScape.htm",
        "api_documentation": "https://nassgeo.csiss.gmu.edu/VegScape/devhelp/vegservice.html",
        "yield_source": config["nass_bulk_url"],
        "reconstruction": "Use nass_yield_ingest.py and crop_yield_model.py to rebuild inherited caches; --source imports them. NDVI is extracted from county GeoTIFFs using ((mean of pixel codes 1..250)-125)/125. Weather features are inherited NASA POWER stage aggregates, not counted as direct satellite observations.",
        "archive_limit": "Input caches contain current reprocessed archives and revised outcomes, not historical publication files. Original archive-fetch timestamps are unavailable; copied cache extraction is independently spot-checked against live TIFFs.",
        "crop_mask": "No crop mask: whole representative-county pixels. Static state production weights from existing config. Neither is optimized here.",
    }
    (output / "input_provenance.json").write_text(json.dumps(lineage, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source", type=Path, help="Existing research/yield_model cache; omit for offline rerun")
    parser.add_argument("--config", type=Path, default=Path("config/crop_yield_model.json"))
    parser.add_argument("--verify-remote", action="store_true", help="Refresh fixed live TIFF spot checks")
    args = parser.parse_args()
    if args.source:
        snapshot_inputs(args.source, args.output, args.config)
    config = json.loads((args.output / "input_config.json").read_text())
    raw = pd.read_csv(args.output / "input_panel.csv.gz")
    observations = pd.read_csv(args.output / "input_ndvi.csv")
    panel = point_in_time_trends(rebuild_optical(raw, observations, config))
    panel.to_csv(args.output / "feature_panel.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    predictions = predict(panel)
    measured, annual = metrics(predictions)
    predictions.to_csv(args.output / "predictions.csv", index=False)
    measured.to_csv(args.output / "metrics.csv", index=False)
    annual.to_csv(args.output / "yearly_errors.csv", index=False)
    if args.verify_remote:
        checks = verify_tiff_samples(observations, args.output / "tiff_samples")
        (args.output / "raster_verification.json").write_text(json.dumps(checks, indent=2, sort_keys=True))
    primary = measured[measured.crop.eq(PRIMARY[0]) & measured.stage.eq(PRIMARY[1])]
    row = primary[primary.model.eq("weather_plus_ndvi")].iloc[0]
    placebo = primary[primary.model.eq("weather_plus_prior_year_ndvi")].iloc[0]
    gate = dict(minimum_5pct_vs_trend=bool(row.improvement_vs_trend_pct >= 5),
                minimum_5pct_vs_weather=bool(row.improvement_vs_weather_pct >= 5),
                year_cluster_ci_above_zero=bool(row.year_cluster_mse_improvement_ci_low > 0),
                beats_prior_year_ndvi_placebo=bool(row.rmse_anomaly_pct < placebo.rmse_anomaly_pct))
    coverage = []
    for (crop, stage), group in panel.groupby(["crop", "stage"]):
        number = int(group.stage_number.iloc[0])
        coverage.append(dict(crop=crop, stage=stage, rows=len(group),
                             current_ndvi_present=int(group[f"s{number}_ndvi"].notna().sum())))
    summary = {
        "signal": "MODIS crop vegetation / USDA VegScape NDVI",
        "status": "forecast_utility_pass" if all(gate.values()) else "forecast_utility_not_verified",
        "trading_alpha_status": "not_established; no trading result inferred from yield RMSE",
        "primary_test": {"crop": PRIMARY[0], "stage": PRIMARY[1], "model": "weather_plus_ndvi"},
        "gate": gate, "primary_metrics": row.to_dict(), "primary_placebo_metrics": placebo.to_dict(),
        "all_metrics": measured.to_dict("records"), "coverage": coverage,
        "evaluation": {"first_year": FIRST_TEST_YEAR, "last_year": LAST_TEST_YEAR,
                       "training": "expanding prior crop years only", "minimum_training_years": MIN_TRAINING_YEARS,
                       "model_parameters": MODEL, "release_lag_days_after_composite_end": RELEASE_LAG_DAYS,
                       "temporal_holdout_not_blind": "Model family/parameters inherited; prior repository analyses had already inspected these years. This is an audited chronological ablation, not a newly untouched test.",
                       "uncertainty": "95% percentile bootstrap of per-year MSE differences; 10,000 draws, seed 17. State rows clustered by year. Only seven independent evaluation years; interval is diagnostic."},
        "limits": ["Reprocessed imagery and revised USDA yield labels are not point-in-time vintages.",
                   "14-day release lag is an explicit conservative assumption, not a verified historical SLA.",
                   "Representative whole-county NDVI is not crop-mask-weighted state coverage.",
                   "Training uses prior crop years under a next-season availability assumption; publication revisions remain unresolved.",
                   "All nine crop/stage results, including failures, are retained; secondary successes cannot independently pass the primary gate."],
        "input_sha256": {name: sha256(args.output / name) for name in ["input_panel.csv.gz", "input_ndvi.csv", "input_config.json"]},
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps({k: summary[k] for k in ["status", "primary_metrics", "gate"]}, indent=2))


if __name__ == "__main__":
    main()
