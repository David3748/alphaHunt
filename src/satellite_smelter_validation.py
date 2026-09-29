#!/usr/bin/env python3
"""Audit satellite smelter signals against primary operating disclosures.

The chronological nowcast is deliberately small: one existing feature (fraction
of clear scenes containing heat), an intercept and expanding OLS. It is research
validation, not a preregistered or prospective experiment. No parameter search or
trading returns are involved. Missing or not-yet-created scenes are never zeros.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


TARGETS = ("refined_copper_kt", "concentrates_smelted_kt")


def load_heat(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["created"] = pd.to_datetime(frame["created"], utc=True, errors="coerce")
    if frame["date"].duplicated().any():
        raise ValueError("Heat input must contain one observation per acquisition day")
    if frame["hot_px20"].lt(0).any():
        raise ValueError("Negative heat counts are invalid")
    return frame.sort_values("date").reset_index(drop=True)


def asof_scenes(heat: pd.DataFrame, start, end, cutoff, max_cloud=0.2):
    """Conservative archive availability, not acquisition-date availability."""
    return heat.loc[
        heat["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        & heat["created"].notna()
        & heat["created"].le(pd.Timestamp(cutoff))
        & heat["created"].ge(heat["date"])
        & heat["cloud_frac"].between(0, max_cloud)
    ]


def quarter_features(heat: pd.DataFrame, quarters, max_cloud=0.2) -> pd.DataFrame:
    rows = []
    for quarter in quarters:
        period = pd.Period(quarter, freq="Q")
        start = period.start_time.tz_localize("UTC")
        cutoff = period.end_time.tz_localize("UTC")
        all_rows = heat.loc[heat["date"].between(start, cutoff)]
        seen = asof_scenes(heat, start, cutoff, cutoff, max_cloud)
        rows.append({
            "quarter": str(period), "forecast_at": cutoff,
            "acquired_scenes": len(all_rows), "available_scenes": len(seen),
            "late_or_missing_scenes": int((all_rows["created"].isna()
                                          | all_rows["created"].gt(cutoff)).sum()),
            "hot_fraction": float(seen["hot_px20"].gt(0).mean()) if len(seen) else np.nan,
            "mean_hot_pixels": float(seen["hot_px20"].mean()) if len(seen) else np.nan,
            "acquisition_span_days": int((seen["date"].max() - seen["date"].min()).days) if len(seen) else 0,
        })
    return pd.DataFrame(rows)


def expanding_nowcasts(features, labels, target, min_train=5, min_scenes=3):
    """Fit only previously published labels and original quarter-end features.

    Persistence and seasonality use labels only, including periods with no valid
    satellite data. That avoids handicapping those comparators with cloud gaps.
    """
    labels = labels.copy()
    labels["available_at"] = pd.to_datetime(labels["available_at"], utc=True)
    merged = features.merge(labels, on="quarter", validate="one_to_one").sort_values("quarter")
    rows = []
    for _, row in merged.iterrows():
        cutoff = row["forecast_at"]
        train = merged.loc[(merged["quarter"] < row["quarter"])
                           & merged["available_at"].le(cutoff)
                           & merged["available_scenes"].ge(min_scenes)
                           & merged["hot_fraction"].notna()]
        if len(train) < min_train or row["available_scenes"] < min_scenes:
            continue
        known = labels.loc[labels["available_at"].le(cutoff)].set_index("quarter")
        prior = str(pd.Period(row["quarter"], freq="Q") - 1)
        seasonal = str(pd.Period(row["quarter"], freq="Q") - 4)
        if prior not in known.index or seasonal not in known.index:
            continue
        if row["available_at"] <= cutoff:
            raise ValueError("Target is already public at the forecast time")
        design = np.column_stack([np.ones(len(train)), train["hot_fraction"]])
        intercept, slope = np.linalg.lstsq(design, train[target], rcond=None)[0]
        prediction = max(0.0, float(intercept + slope * row["hot_fraction"]))
        rows.append({
            "quarter": row["quarter"], "target": target, "forecast_at": cutoff,
            "label_available_at": row["available_at"],
            "report_lead_days": (row["available_at"] - cutoff).total_seconds() / 86400,
            "train_quarters": len(train), "available_scenes": row["available_scenes"],
            "hot_fraction": row["hot_fraction"], "actual": row[target],
            "satellite_ols": prediction, "persistence": known.loc[prior, target],
            "seasonal": known.loc[seasonal, target], "historical_mean": train[target].mean(),
            "slope": slope,
        })
    return pd.DataFrame(rows)


def paired_block_interval(differences, block_size=2, samples=10000, seed=20260926):
    """Circular moving-block bootstrap of paired errors (quarter units).

    Positive values mean satellite error is lower. With eight quarters this is
    only a stability diagnostic, not a reliable population confidence guarantee.
    """
    differences = np.asarray(differences, dtype=float)
    if len(differences) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(differences), size=(samples, int(np.ceil(len(differences) / block_size))))
    indices = (starts[:, :, None] + np.arange(block_size)) % len(differences)
    means = differences[indices.reshape(samples, -1)[:, :len(differences)]].mean(axis=1)
    return [float(x) for x in np.quantile(means, [0.025, 0.975])]


def score_forecasts(predictions):
    if predictions.empty:
        return {"n_quarters": 0, "verified_forecast_usefulness": False}
    actual = predictions["actual"]
    errors = {name: predictions[name] - actual for name in
              ("satellite_ols", "persistence", "seasonal", "historical_mean")}
    mae = {name: float(error.abs().mean()) for name, error in errors.items()}
    gains = {}
    for name in ("persistence", "seasonal", "historical_mean"):
        difference = errors[name].abs() - errors["satellite_ols"].abs()
        gains[name] = {"mae_reduction_pct": 100 * (1 - mae["satellite_ols"] / mae[name]),
                       "paired_mae_gain_kt": float(difference.mean()),
                       "paired_block_bootstrap_95pct_kt": paired_block_interval(difference),
                       "quarters_won": int(difference.gt(0).sum())}
    # No useful-forecast promotion from a point estimate alone; require beating
    # every comparator by >=10% with positive paired uncertainty interval.
    passes = all(g["mae_reduction_pct"] >= 10 and g["paired_block_bootstrap_95pct_kt"][0] > 0
                 for g in gains.values())
    return {"n_quarters": len(predictions), "start": predictions["quarter"].iloc[0],
            "end": predictions["quarter"].iloc[-1], "mae_kt": mae,
            "rmse_kt": {name: float(np.sqrt((error ** 2).mean())) for name, error in errors.items()},
            "comparisons": gains, "verified_forecast_usefulness": passes,
            "min_report_lead_days": float(predictions["report_lead_days"].min())}


def event_validation(heat):
    """Retrospective operating-state evidence; observations are not independent trials."""
    cutoff = pd.Timestamp("2024-12-31T23:59:59Z")
    before = asof_scenes(heat, "2022-01-01T00:00:00Z", "2023-05-30T23:59:59Z", cutoff)
    after = asof_scenes(heat, "2023-06-01T00:00:00Z", cutoff, cutoff)
    def counts(frame):
        return {"clear_scenes": len(frame), "hot_scenes": int(frame["hot_px20"].gt(0).sum()),
                "hot_fraction": float(frame["hot_px20"].gt(0).mean()) if len(frame) else None,
                "observed_months": int(frame["date"].dt.strftime("%Y-%m").nunique())}
    seasonal = []
    for month in range(1, 6):
        a = before.loc[before["date"].dt.year.eq(2023) & before["date"].dt.month.eq(month)]
        b = after.loc[after["date"].dt.year.eq(2024) & after["date"].dt.month.eq(month)]
        seasonal.append({"month": month, "before": counts(a), "after": counts(b)})
    return {"site": "codelco_ventanas", "closure_date_range": ["2023-05-30", "2023-05-31"],
            "before": counts(before), "after": counts(after), "matched_seasons": seasonal,
            "verified_scope": "Retrospective disappearance of smelter heat after publicly announced closure",
            "forecast_usefulness_verified": False, "trading_alpha_verified": False,
            "source": "https://www.codelco.com/tras-90-dias-concluyeron-trabajos-de-detencion-total-de-fundicion-ventanas",
            "critical_target_caveat": "Codelco states the electrolytic refinery continued operating; no-heat is not zero refined output.",
            "uncertainty": "One selected closure episode; temporally dependent scenes; no per-scene significance claim.",
            "availability_cutoff": str(cutoff)}


def manyar_diagnostics(heat):
    """Audit the tempting but invalid rule that any heat means copper output."""
    cutoff = pd.Timestamp("2025-03-31T23:59:59Z")
    during = asof_scenes(heat, "2024-10-14T00:00:00Z", cutoff, cutoff)
    winter = during.loc[during["date"].ge(pd.Timestamp("2025-01-01T00:00:00Z"))]
    return {"fire_date": "2024-10-14", "company_disclosure_date": "2024-10-22",
            "primary_source": "https://s22.q4cdn.com/529358580/files/doc_news/2024/FCX_241022_3Q_2024_Earnings_Release-docx.pdf",
            "repair_window_corroboration": [
                {"date": "2025-01-23", "page": 7, "url": "https://s22.q4cdn.com/529358580/files/doc_news/2025/FCX_250123_4Q_2024_Earnings_Release.pdf"},
                {"date": "2025-04-24", "page": 8, "url": "https://s22.q4cdn.com/529358580/files/doc_news/2025/FCX_250424_1Q_2025_Earnings_Release.pdf"}],
            "source_page": 7, "clear_scenes_during_repair_window": len(during),
            "furnace_zone_hot_scenes": int(during["furnace block"].gt(0).sum()),
            "aoi_hot_scenes": int(during["hot_px20"].gt(0).sum()),
            "mean_furnace_zone_pixels": float(during["furnace block"].mean()),
            "mean_elsewhere_pixels": float(during["elsewhere"].mean()),
            "2025Q1_clear_scenes": len(winter),
            "interpretation": "Heat persists in a documented suspension; other AOI areas dominate. No Q1 clear scenes is missing evidence, not inactivity.",
            "forecast_usefulness_verified": False}


def run(results_dir, heat_dir):
    results_dir = Path(results_dir)
    labels = pd.read_csv(results_dir / "production_labels.csv")
    kennecott = load_heat(Path(heat_dir) / "heat_rio_kennecott.csv")
    codelco = load_heat(Path(heat_dir) / "heat_codelco_ventanas.csv")
    manyar = load_heat(Path(heat_dir) / "heat_fcx_manyar.csv")
    features = quarter_features(kennecott, labels["quarter"])
    features.to_csv(results_dir / "quarter_features.csv", index=False)
    forecasts, scores = [], {}
    for target in TARGETS:
        predictions = expanding_nowcasts(features, labels, target)
        forecasts.append(predictions)
        scores[target] = score_forecasts(predictions)
    pd.concat(forecasts, ignore_index=True).to_csv(results_dir / "production_forecasts.csv", index=False)
    sensitivity = {}
    for threshold in (0.05, 0.1):
        alternative = quarter_features(kennecott, labels["quarter"], max_cloud=threshold)
        sensitivity[str(threshold)] = {
            target: score_forecasts(expanding_nowcasts(alternative, labels, target)) for target in TARGETS}
    audits = {}
    for name, data in (("rio_kennecott", kennecott), ("codelco_ventanas", codelco)):
        lag = (data["created"] - data["date"]).dt.total_seconds() / 86400
        audits[name] = {"scenes": len(data), "missing_created": int(lag.isna().sum()),
                        "created_over_7_days_after_acquisition": int(lag.gt(7).sum()),
                        "created_over_365_days_after_acquisition": int(lag.gt(365).sum()),
                        "max_lag_days": float(lag.max())}
    summary = {
        "schema_version": 1,
        "method": "Expanding OLS: intercept + hot-scene fraction, at quarter end, >=5 prior labeled quarters, >=3 clear scenes.",
        "evaluation_type": "Chronological retrospective evaluation; thresholds and AOIs inherited from exploratory work; not pristine holdout.",
        "forecast_gate": "At least 10% lower MAE than persistence, seasonal, and historical mean, with positive paired 2-quarter-block interval.",
        "production_validation": scores,
        "cloud_sensitivity": sensitivity,
        "event_validation": event_validation(codelco),
        "manyar_diagnostics": manyar_diagnostics(manyar),
        "availability_audit": audits,
        "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                         [Path(heat_dir) / "heat_rio_kennecott.csv", Path(heat_dir) / "heat_codelco_ventanas.csv",
                          Path(heat_dir) / "heat_fcx_manyar.csv",
                          results_dir / "production_labels.csv"]},
        "verdict": "Forecast usefulness not verified; smelter shutdown detection is a narrower, retrospective physical validation.",
        "limitations": [
            "Eight out-of-sample quarters are insufficient for stable inference; block intervals are exploratory.",
            "AOIs were selected retrospectively and partial cloud masks can conceal a furnace even when the full AOI is mostly clear.",
            "No local furnace cloud mask or slag-disposal control is available in the existing heat CSVs.",
            "Hot-scene absence is not equivalent to no activity: Kennecott 2025Q1 has 0/10 hot scenes and 42.3 kt refined copper.",
            "Kennecott refined copper excludes purchased/tolled concentrate; heat reflects processes and inventory with different timing.",
            "Created timestamps describe this archived asset; original acquisitions may have been obtainable from another provider earlier.",
            "Quarter-end predictions precede the formal output report, but no comparison to analyst consensus or public maintenance schedules is included.",
        ],
    }
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results/satellite_validation/smelters"))
    parser.add_argument("--heat", type=Path, default=Path("results/satellite_sites"))
    args = parser.parse_args()
    summary = run(args.results, args.heat)
    print(json.dumps(summary["production_validation"], indent=2))


if __name__ == "__main__":
    main()
