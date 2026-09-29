#!/usr/bin/env python3
"""Frozen Sobradinho satellite altimetry -> future monthly hydro forecast.

Offline current-archive hindcast; assumed release lags do not certify original vintages.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/hydro"
MODELS = ("baseline", "satellite", "ground_storage", "ground_storage_satellite", "persistence")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_altimetry(path: Path) -> pd.DataFrame:
    rows = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 16 or not fields[2].isdigit() or fields[2] == "99999999":
            continue
        if fields[0] in ("TOPEX", "POSDN"):
            continue
        observed = pd.to_datetime(fields[2], format="%Y%m%d", errors="coerce")
        if pd.isna(observed) or observed < pd.Timestamp("2008-07-01"):
            continue
        hour, minute = int(fields[3]), int(fields[4])
        height, error = float(fields[5]), float(fields[6])
        if hour > 23 or minute > 59 or not (np.isfinite(height) and abs(height) < 100 and 0 <= error <= 1):
            continue
        if int(fields[13]) != 0:
            continue
        observed += pd.Timedelta(hours=hour, minutes=minute)
        rows.append({"observed_at": observed, "assumed_available_at": observed + pd.Timedelta(days=90),
                     "height_m": height, "estimated_error_m": error, "mission": fields[0],
                     "gdr_igdr_flag": int(fields[15])})
    frame = pd.DataFrame(rows).sort_values("observed_at").reset_index(drop=True)
    if frame.empty or frame.duplicated(["observed_at", "mission"]).any():
        raise ValueError("Empty or duplicate within-mission satellite observations")
    # Tandem Jason3/Sentinel6 calibration orbits can share a timestamp. Combine
    # simultaneous valid measurements without choosing the one that fits outcomes.
    frame = frame.groupby("observed_at", as_index=False).agg(
        assumed_available_at=("assumed_available_at", "max"), height_m=("height_m", "mean"),
        estimated_error_m=("estimated_error_m", "mean"), mission=("mission", lambda x: "+".join(sorted(x))),
        gdr_igdr_flag=("gdr_igdr_flag", lambda x: "+".join(map(str, sorted(x)))),
        n_simultaneous_measurements=("height_m", "size"))
    return frame.sort_values("observed_at").reset_index(drop=True)


def aggregate_ons(out: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted((out / "inputs").glob("generation_*.parquet"))]
    generation = pd.concat(frames, ignore_index=True)
    if not generation.nom_usina.str.upper().isin(("SOBRADINHO", "UHE SOBRADINHO")).all() or not generation.id_ons.eq("BAUSB").all():
        raise ValueError("Unexpected generation plant identity")
    generation["val_geracao"] = pd.to_numeric(generation.val_geracao, errors="raise")
    generation["date"] = pd.to_datetime(generation.din_instante)
    if generation.date.duplicated().any():
        raise ValueError("Duplicate generation timestamps")
    generation["month"] = generation.date.dt.to_period("M").dt.to_timestamp()
    monthly = generation.groupby("month").agg(generation_mw=("val_geracao", "mean"),
                                               valid_hours=("val_geracao", "count"))
    monthly["hour_coverage"] = monthly.valid_hours / (monthly.index.days_in_month * 24)
    monthly.loc[monthly.hour_coverage < .95, "generation_mw"] = np.nan
    frames = [pd.read_parquet(path) for path in sorted((out / "inputs").glob("hydrology_*.parquet"))]
    hydro = pd.concat(frames, ignore_index=True)
    if not hydro.nom_reservatorio.str.upper().eq("SOBRADINHO").all() or hydro.id_reservatorio.nunique() != 1:
        raise ValueError("Unexpected hydrology reservoir identity")
    for field in ("val_nivelmontante", "val_vazaoafluente"):
        hydro[field] = pd.to_numeric(hydro[field], errors="raise")
    hydro["date"] = pd.to_datetime(hydro.din_instante)
    if hydro.date.duplicated().any():
        raise ValueError("Duplicate hydrology timestamps")
    hydro["month"] = hydro.date.dt.to_period("M").dt.to_timestamp()
    hm = hydro.groupby("month").agg(ground_level_m=("val_nivelmontante", "mean"),
                                   inflow_m3_s=("val_vazaoafluente", "mean"),
                                   valid_level_days=("val_nivelmontante", "count"),
                                   valid_inflow_days=("val_vazaoafluente", "count"))
    for value, count in (("ground_level_m", "valid_level_days"), ("inflow_m3_s", "valid_inflow_days")):
        hm.loc[hm[count] / hm.index.days_in_month < .95, value] = np.nan
    return monthly.join(hm, how="outer").rename_axis("date").reset_index()


def build_panel(monthly: pd.DataFrame, satellite: pd.DataFrame) -> pd.DataFrame:
    source = monthly.set_index("date")
    if source.index.duplicated().any():
        raise ValueError("Duplicate source months")
    rows = []
    for issue in pd.date_range("2010-01-01", "2025-12-01", freq="MS"):
        lag2 = issue - pd.DateOffset(months=2)
        lag12 = issue - pd.DateOffset(years=1)
        target_end = issue + pd.offsets.MonthEnd(0)
        available_ground = lag2 + pd.offsets.MonthEnd(0) + pd.Timedelta(days=30)
        row = {"date": issue, "year": issue.year, "month": issue.month, "forecast_at": issue,
               "target_end": target_end, "target_available_at": target_end + pd.Timedelta(days=30),
               "ground_source_month": lag2, "ground_available_at": available_ground,
               "generation_mw": source.generation_mw.get(issue, np.nan),
               "generation_lag2": source.generation_mw.get(lag2, np.nan),
               "generation_lag12": source.generation_mw.get(lag12, np.nan),
               "inflow_lag2": source.inflow_m3_s.get(lag2, np.nan),
               "ground_level_lag2": source.ground_level_m.get(lag2, np.nan),
               "satellite_height": np.nan, "satellite_height_change": np.nan,
               "satellite_observed_at": pd.NaT, "satellite_available_at": pd.NaT,
               "satellite_previous_observed_at": pd.NaT}
        past = satellite.loc[(satellite.assumed_available_at < issue)
                             & (satellite.observed_at >= issue - pd.Timedelta(days=130))]
        if len(past):
            latest = past.iloc[-1]
            earlier = satellite.loc[(satellite.observed_at <= latest.observed_at - pd.Timedelta(days=30))
                                    & (satellite.observed_at >= issue - pd.Timedelta(days=180))
                                    & (satellite.assumed_available_at < issue)]
            if len(earlier):
                prior = earlier.iloc[-1]
                row.update({"satellite_height": latest.height_m,
                            "satellite_height_change": latest.height_m - prior.height_m,
                            "satellite_observed_at": latest.observed_at,
                            "satellite_available_at": latest.assumed_available_at,
                            "satellite_previous_observed_at": prior.observed_at})
        rows.append(row)
    panel = pd.DataFrame(rows)
    required = ["generation_lag2", "generation_lag12", "inflow_lag2",
                "ground_level_lag2", "satellite_height", "satellite_height_change"]
    panel["complete"] = np.isfinite(panel[required]).all(axis=1) & (panel.ground_available_at < panel.forecast_at)
    return panel


def design(frame: pd.DataFrame, model: str, center_year: int) -> np.ndarray:
    phase = 2 * np.pi * frame.month.to_numpy(dtype=float) / 12
    columns = [np.ones(len(frame)), np.sin(phase), np.cos(phase), np.sin(2 * phase), np.cos(2 * phase),
               frame.year.to_numpy(dtype=float) - center_year,
               frame.generation_lag2.to_numpy(dtype=float), frame.generation_lag12.to_numpy(dtype=float),
               frame.inflow_lag2.to_numpy(dtype=float)]
    if model in ("ground_storage", "ground_storage_satellite"):
        columns.append(frame.ground_level_lag2.to_numpy(dtype=float))
    if model in ("satellite", "ground_storage_satellite"):
        columns.extend([frame.satellite_height.to_numpy(dtype=float), frame.satellite_height_change.to_numpy(dtype=float)])
    if model not in MODELS[:-1]:
        raise ValueError("Unknown model")
    return np.column_stack(columns)


def predict(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in panel.loc[panel.year >= 2018].iterrows():
        train = panel.loc[panel.complete & np.isfinite(panel.generation_mw) & (panel.target_available_at < row.forecast_at)].copy()
        record = row.to_dict()
        record.update({"n_train": len(train), "eligible": False, "abstain_reason": ""})
        if not row.complete:
            record["abstain_reason"] = "Missing/late source under fixed coverage/availability rules"
        elif len(train) < 72:
            record["abstain_reason"] = "Fewer than72complete prior training months"
        else:
            record.update({"eligible": True, "latest_training_target_available": train.target_available_at.max(),
                           "persistence": row.generation_lag2})
            current = row.to_frame().T
            for model in MODELS[:-1]:
                coefficients = np.linalg.lstsq(design(train, model, int(row.year)), train.generation_mw.to_numpy(), rcond=None)[0]
                record[model] = float((design(current, model, int(row.year)) @ coefficients)[0])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame, draws: int = 10000) -> dict:
    valid = predictions.loc[predictions.eligible & np.isfinite(predictions.generation_mw)].sort_values("date").copy()
    if valid.empty:
        return {"status": "no_eligible_forecasts", "n_test_months": 0, "incremental_gate_passed": False,
                "original_vintage_operational_verification": False, "trading_alpha_verified": False}
    errors = valid[list(MODELS)].to_numpy() - valid.generation_mw.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {model: {"rmse_mw": float(np.sqrt(squared[:, i].mean())), "mae_mw": float(np.abs(errors[:, i]).mean())}
               for i, model in enumerate(MODELS)}
    # Calendar blocks retain abstentions rather than compressing missing months into adjacent observations.
    full = predictions.sort_values("date").reset_index(drop=True)
    full_sq = np.full((len(full), len(MODELS)), np.nan)
    full_sq[(full.eligible & np.isfinite(full.generation_mw)).to_numpy()] = squared
    rng = np.random.default_rng(20260927)
    boot = []
    for _ in range(draws):
        starts = rng.integers(0, len(full), size=(len(full) + 11) // 12)
        idx = ((starts[:, None] + np.arange(12)) % len(full)).ravel()[:len(full)]
        boot.append(np.sqrt(np.nanmean(full_sq[idx], axis=0)))
    boot = np.asarray(boot)
    comparisons = {}
    for candidate, baseline in (("satellite", "baseline"), ("ground_storage_satellite", "ground_storage"),
                                ("satellite", "ground_storage"), ("satellite", "persistence")):
        i, j = MODELS.index(candidate), MODELS.index(baseline)
        gain = metrics[baseline]["rmse_mw"] - metrics[candidate]["rmse_mw"]
        comparisons[f"{candidate}_vs_{baseline}"] = {"rmse_reduction_fraction": gain / metrics[baseline]["rmse_mw"],
            "rmse_reduction_mw": gain, "mae_reduction_fraction": 1 - metrics[candidate]["mae_mw"] / metrics[baseline]["mae_mw"],
            "rmse_reduction_ci95_mw": np.quantile(boot[:, j] - boot[:, i], [.025, .975]).tolist()}
    gate = all(comparisons[name]["rmse_reduction_fraction"] >= .05
               and comparisons[name]["mae_reduction_fraction"] > 0
               and comparisons[name]["rmse_reduction_ci95_mw"][0] > 0
               for name in ("satellite_vs_baseline", "ground_storage_satellite_vs_ground_storage"))
    annual = []
    for year, group in valid.groupby("year"):
        annual.append({"year": int(year), "n_months": len(group),
                       **{f"{model}_rmse_mw": float(np.sqrt(np.mean((group[model] - group.generation_mw)**2))) for model in MODELS}})
    return {"status": "current_archive_incremental_gate_passed" if gate else "incremental_gate_failed",
            "n_test_months": len(valid), "n_planned_months": len(predictions), "n_abstentions": int((~predictions.eligible).sum()),
            "metrics": metrics, "comparisons": comparisons, "annual_metrics": annual,
            "incremental_gate_passed": bool(gate), "original_vintage_operational_verification": False,
            "prospective_verification": False, "trading_alpha_verified": False,
            "cross_candidate_multiple_testing_adjusted": False}


def run(out: Path = DEFAULT) -> dict:
    manifest = json.loads((out / "source_manifest.json").read_text())
    for item in manifest:
        if digest(out / item["path"]) != item["selected_snapshot_sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {item['path']}")
    satellite = parse_altimetry(out / "inputs/lake000345.10d.2.txt")
    monthly = aggregate_ons(out)
    panel = build_panel(monthly, satellite)
    predictions = predict(panel)
    summary = score(predictions)
    summary["protocol"] = json.loads((out / "protocol.json").read_text())
    summary["protocol_sha256"] = digest(out / "protocol.json")
    summary["source_code_sha256"] = digest(Path(__file__))
    summary["source_manifest_sha256"] = digest(out / "source_manifest.json")
    summary["satellite_valid_observations"] = len(satellite)
    summary["satellite_date_start"] = str(satellite.observed_at.min())
    summary["satellite_date_end"] = str(satellite.observed_at.max())
    summary["interpretation"] = "Future-month Sobradinho generation forecast using90day-old satellite level and public ONS controls. Current revised archives and assumed publication lags; not full point-in-time operational validation."
    for name, frame in (("satellite.csv", satellite), ("monthly_sources.csv", monthly), ("panel.csv", panel), ("predictions.csv", predictions)):
        frame.to_csv(out / name, index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(run(args.out), indent=2))
