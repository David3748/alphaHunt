#!/usr/bin/env python3
"""Frozen June satellite-enhanced OISST -> August-November Atlantic ACE forecast.

Reproducible current-vintage scientific forecast, not a claim of market alpha.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/hurricane"
MODELS = ("baseline", "satellite", "climatology", "recent_climatology", "persistence")
BASELINES = tuple(model for model in MODELS if model != "satellite")
ALPHA = 5.0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_hurdat(path: Path) -> pd.DataFrame:
    raw = gzip.decompress(path.read_bytes()).decode("utf-8") if path.suffix == ".gz" else path.read_text()
    lines = [line for line in raw.splitlines() if line.strip()]
    rows, position = [], 0
    while position < len(lines):
        header = [value.strip() for value in lines[position].split(",")]
        if len(header[0]) != 8 or not header[0].startswith("AL"):
            raise ValueError("Unexpected storm header")
        count = int(header[2])
        if position + count >= len(lines):
            raise ValueError("Truncated storm record")
        storm = header[0]
        for line in lines[position + 1:position + count + 1]:
            fields = [value.strip() for value in line.split(",")]
            if len(fields) < 8:
                raise ValueError("Truncated observation")
            observed = pd.to_datetime(fields[0] + fields[1], format="%Y%m%d%H%M", errors="raise")
            if not 1981 <= observed.year <= 2025:
                continue
            status, wind = fields[3], int(fields[6])
            if status in ("TS", "HU", "SS") and wind < 0:
                raise ValueError("Missing storm-strength wind cannot be treated as zeroACE")
            # Best-track tables contain extra landfall/intensity times; only the
            # four synoptic observations enter the defined six-hour ACE sum.
            synoptic = fields[1] in ("0000", "0600", "1200", "1800")
            eligible = synoptic and status in ("TS", "HU", "SS") and wind >= 34
            rows.append({"storm_id": storm, "observed_at": observed, "status": status,
                         "wind_knots": wind, "synoptic": synoptic,
                         "ace": wind ** 2 / 10000 if eligible else 0.0})
        position += count + 1
    frame = pd.DataFrame(rows).sort_values(["observed_at", "storm_id"]).reset_index(drop=True)
    if frame.empty or frame.duplicated(["storm_id", "observed_at"]).any():
        raise ValueError("Empty or duplicate storm observations")
    return frame


def annual_activity(records: pd.DataFrame) -> pd.DataFrame:
    years = records.observed_at.dt.year
    months = records.observed_at.dt.month
    rows = []
    for year in sorted(years.unique()):
        subset = records.loc[years == year]
        rows.append({"year": int(year),
                     "aug_nov_ace": float(records.loc[(years == year) & months.between(8, 11), "ace"].sum()),
                     "june_july_ace": float(records.loc[(years == year) & months.isin((6, 7)), "ace"].sum()),
                     "last_observation": subset.observed_at.max(), "n_records": len(subset)})
    return pd.DataFrame(rows)


def build_panel(activity: pd.DataFrame, sst: pd.DataFrame) -> pd.DataFrame:
    if activity.year.duplicated().any() or sst.year.duplicated().any():
        raise ValueError("Duplicate annual source")
    activity = activity.set_index("year")
    sst = sst.set_index("year")
    rows = []
    for year in range(1982, 2026):
        observed_month = pd.Timestamp(year, 6, 1)
        source_month = pd.Timestamp(sst.source_month.get(year)) if year in sst.index else pd.NaT
        if pd.notna(source_month) and source_month != observed_month:
            raise ValueError("Only current June SST is permitted")
        row = {"year": year, "forecast_at": pd.Timestamp(year, 8, 1),
               "target_start": pd.Timestamp(year, 8, 1), "target_end": pd.Timestamp(year, 11, 30, 23, 59),
               "assumed_label_available_at": pd.Timestamp(year + 1, 5, 1),
               "sst_source_month": source_month,
               "sst_assumed_available_at": pd.Timestamp(year, 6, 30) + pd.Timedelta(days=31),
               "storm_feature_cutoff": pd.Timestamp(year, 7, 31, 18),
               "prior_season_assumed_available_at": pd.Timestamp(year, 5, 1),
               "actual_ace": activity.aug_nov_ace.get(year, np.nan),
               "prior_season_ace": activity.aug_nov_ace.get(year - 1, np.nan),
               "june_july_ace": activity.june_july_ace.get(year, np.nan),
               "mdr_sst_c": sst.mdr_sst_c.get(year, np.nan), "nino34_sst_c": sst.nino34_sst_c.get(year, np.nan)}
        rows.append(row)
    frame = pd.DataFrame(rows)
    # A forecast must be issuable before its outcome exists. Historical labels
    # are required separately for training, and observed labels for scoring.
    columns = ["prior_season_ace", "june_july_ace", "mdr_sst_c", "nino34_sst_c"]
    frame["complete"] = np.isfinite(frame[columns]).all(axis=1)
    return frame


def raw_features(frame: pd.DataFrame, model: str) -> np.ndarray:
    columns = [frame.year.to_numpy(dtype=float), frame.prior_season_ace.to_numpy(dtype=float),
               frame.june_july_ace.to_numpy(dtype=float)]
    if model == "satellite":
        columns += [frame.mdr_sst_c.to_numpy(dtype=float), frame.nino34_sst_c.to_numpy(dtype=float)]
    elif model != "baseline":
        raise ValueError("Unknown ridge model")
    return np.column_stack(columns)


def ridge_predict(train: pd.DataFrame, target: pd.DataFrame, model: str) -> tuple[float, np.ndarray]:
    x = raw_features(train, model)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    x = (x - mean) / scale
    current = (raw_features(target, model) - mean) / scale
    y = train.actual_ace.to_numpy(dtype=float)
    ymean = y.mean()
    beta = np.linalg.solve(x.T @ x + ALPHA * np.eye(x.shape[1]), x.T @ (y - ymean))
    return max(0.0, float((current @ beta)[0] + ymean)), beta


def predict(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in panel.loc[panel.year.between(1999, 2025)].iterrows():
        if not (row.sst_assumed_available_at < row.forecast_at
                and row.storm_feature_cutoff < row.forecast_at
                and row.prior_season_assumed_available_at < row.forecast_at
                and row.forecast_at == row.target_start < row.target_end < row.assumed_label_available_at):
            raise ValueError("Source, issue, target or label timing violation")
        train = panel.loc[panel.complete & np.isfinite(panel.actual_ace) & (panel.year < row.year)
                          & (panel.assumed_label_available_at < row.forecast_at)].sort_values("year")
        record = row.to_dict()
        record.update({"eligible": False, "n_train": len(train), "abstain_reason": ""})
        if not row.complete or len(train) < 17:
            record["abstain_reason"] = "Incomplete target sources or fewer than17trainingyears"
        elif not (train.sst_assumed_available_at < row.forecast_at).all():
            record["abstain_reason"] = "Training SST unavailable"
        else:
            record.update({"eligible": True, "latest_training_label_available": train.assumed_label_available_at.max(),
                           "latest_training_year": int(train.year.max()), "climatology": float(train.actual_ace.mean()),
                           "recent_climatology": float(train.tail(10).actual_ace.mean()), "persistence": float(row.prior_season_ace)})
            target = row.to_frame().T
            for model in ("baseline", "satellite"):
                result, beta = ridge_predict(train, target, model)
                record[model] = result
                if model == "satellite":
                    record["mdr_standardized_coefficient"] = float(beta[-2])
                    record["nino34_standardized_coefficient"] = float(beta[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame, draws: int = 10000) -> dict:
    observed = np.isfinite(predictions.actual_ace)
    valid = predictions.loc[predictions.eligible & observed].sort_values("year")
    if valid.empty:
        return {"status": "no_observed_eligible_forecasts", "forecast_gate_passed": False, "n_test_years": 0,
                "n_pending_outcomes": int((predictions.eligible & ~observed).sum())}
    if not np.isfinite(valid[list(MODELS)]).all().all():
        raise ValueError("Eligible scored forecasts must be finite")
    errors = valid[list(MODELS)].to_numpy() - valid.actual_ace.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {model: {"rmse_ace": float(np.sqrt(squared[:, i].mean())), "mae_ace": float(np.abs(errors[:, i]).mean())}
               for i, model in enumerate(MODELS)}
    rng = np.random.default_rng(20260927)
    boot = []
    for _ in range(draws):
        starts = rng.integers(0, len(valid), size=(len(valid) + 4) // 5)
        indices = ((starts[:, None] + np.arange(5)) % len(valid)).ravel()[:len(valid)]
        boot.append(np.sqrt(squared[indices].mean(axis=0)))
    boot = np.asarray(boot)
    sat = MODELS.index("satellite")
    comparisons = {}
    for baseline in BASELINES:
        j = MODELS.index(baseline)
        comparisons[baseline] = {"rmse_reduction_fraction": 1 - metrics["satellite"]["rmse_ace"] / metrics[baseline]["rmse_ace"],
                                 "mae_reduction_fraction": 1 - metrics["satellite"]["mae_ace"] / metrics[baseline]["mae_ace"],
                                 "rmse_gain_ci95_ace": np.quantile(boot[:, j] - boot[:, sat], [.025, .975]).tolist(),
                                 "years_lower_squared_error": int((squared[:, sat] < squared[:, j]).sum())}
    best = min(BASELINES, key=lambda name: metrics[name]["rmse_ace"])
    gate = comparisons[best]["rmse_reduction_fraction"] >= .05 and all(
        item["mae_reduction_fraction"] > 0 and item["rmse_gain_ci95_ace"][0] > 0 for item in comparisons.values())
    return {"status": "scientific_forecast_gate_passed" if gate else "forecast_gate_failed",
            "n_test_years": len(valid), "n_abstentions": int((~predictions.eligible).sum()),
            "n_pending_outcomes": int((predictions.eligible & ~observed).sum()),
            "metrics": metrics, "comparisons": comparisons, "strongest_baseline": best,
            "forecast_gate_passed": bool(gate), "original_vintage_operational_verification": False,
            "satellite_only_incremental_attribution": False, "superiority_to_official_seasonal_forecasts_tested": False,
            "trading_alpha_verified": False, "prospective_verification": False,
            "cross_candidate_multiple_testing_adjusted": False}


def run(out: Path = DEFAULT) -> dict:
    manifest = json.loads((out / "source_manifest.json").read_text())
    for source in manifest:
        if digest(out / source["path"]) != source["sha256"]:
            raise ValueError(f"Source checksum mismatch: {source['path']}")
    records = parse_hurdat(out / "inputs/hurdat2_1851_2025.txt.gz")
    activity = annual_activity(records)
    if set(activity.year) != set(range(1981, 2026)):
        raise ValueError("Incomplete annual storm coverage")
    panel = build_panel(activity, pd.read_csv(out / "inputs/june_sst.csv"))
    predictions = predict(panel)
    summary = score(predictions)
    summary["protocol"] = json.loads((out / "protocol.json").read_text())
    summary["protocol_sha256"] = digest(out / "protocol.json")
    summary["source_code_sha256"] = digest(Path(__file__))
    summary["source_manifest_sha256"] = digest(out / "source_manifest.json")
    summary["units"] = "ACE: sum of six-hourly wind_knots squared /10000 for HU,TS,SS states withwinds>=34kt"
    summary["interpretation"] = "August1 forecast offutureAug–NovAtlanticstormenergy fromJune satellite-enhanced OISST, relative tosame JuneJulystormactivity/priorseason/trend controls. Current-version retrospective study; notisolatedsatelliteattribution, originalvintagecertification, or marketalpha."
    for name, frame in (("annual_activity.csv", activity), ("panel.csv", panel), ("predictions.csv", predictions)):
        frame.to_csv(out / name, index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = run(args.out)
    print(json.dumps({key: result[key] for key in ("status", "n_test_years", "metrics", "comparisons")}, indent=2))
