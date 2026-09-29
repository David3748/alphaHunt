#!/usr/bin/env python3
"""Separately frozen eastern Pacific ACE forecast, using only June Niño3.4 SST."""
from __future__ import annotations
import argparse
import gzip
import json
from pathlib import Path
import numpy as np
import pandas as pd
import satellite_hurricane_validation as shared

DEFAULT = shared.ROOT / "results/satellite_validation/pacific_hurricane"
MODELS = shared.MODELS


def parse_pacific(path: Path) -> pd.DataFrame:
    lines = [line for line in gzip.decompress(path.read_bytes()).decode().splitlines() if line.strip()]
    rows, position = [], 0
    while position < len(lines):
        header = [part.strip() for part in lines[position].split(",")]
        storm, count = header[0], int(header[2])
        if len(storm) != 8 or storm[:2] not in ("EP", "CP"):
            raise ValueError("Unexpected Pacific storm header")
        if position + count >= len(lines):
            raise ValueError("Truncated Pacific storm")
        for line in lines[position + 1:position + count + 1]:
            fields = [part.strip() for part in line.split(",")]
            if len(fields) < 8:
                raise ValueError("Truncated Pacific observation")
            if storm.startswith("CP"):
                continue
            observed = pd.to_datetime(fields[0] + fields[1], format="%Y%m%d%H%M", errors="raise")
            if not 1981 <= observed.year <= 2025:
                continue
            status, wind = fields[3], int(fields[6])
            if status in ("TS", "HU", "SS") and wind < 0:
                raise ValueError("Missing Pacific storm wind")
            if fields[5][-1] not in ("E", "W"):
                raise ValueError("Unknown longitude hemisphere")
            # Unwrap western longitudes through the dateline:170E is190W,
            # not an eastern-Pacific point merely because170 >= -140.
            longitude = -float(fields[5][:-1]) if fields[5][-1] == "W" else float(fields[5][:-1]) - 360
            synoptic = fields[1] in ("0000", "0600", "1200", "1800")
            eligible = synoptic and status in ("TS", "HU", "SS") and wind >= 34 and longitude >= -140
            rows.append({"storm_id": storm, "observed_at": observed, "status": status, "wind_knots": wind,
                         "longitude": longitude, "synoptic": synoptic, "ace": wind ** 2 / 10000 if eligible else 0.0})
        position += count + 1
    result = pd.DataFrame(rows).sort_values(["observed_at", "storm_id"]).reset_index(drop=True)
    if result.empty or result.duplicated(["storm_id", "observed_at"]).any():
        raise ValueError("Empty or duplicate Pacific observations")
    return result


def build_panel(activity: pd.DataFrame, sst: pd.DataFrame) -> pd.DataFrame:
    # Reuse identical annual timing and lag lookup. A constant compatibility
    # column is removed immediately; no Atlantic SST values enter this study.
    return shared.build_panel(activity, sst.assign(mdr_sst_c=0.0)).drop(columns="mdr_sst_c")


def features(frame: pd.DataFrame, model: str) -> np.ndarray:
    columns = [frame.year.to_numpy(dtype=float), frame.prior_season_ace.to_numpy(dtype=float),
               frame.june_july_ace.to_numpy(dtype=float)]
    if model == "satellite":
        columns.append(frame.nino34_sst_c.to_numpy(dtype=float))
    elif model != "baseline":
        raise ValueError("Unknown model")
    return np.column_stack(columns)


def ridge_predict(train: pd.DataFrame, target: pd.DataFrame, model: str) -> tuple[float, np.ndarray]:
    x = features(train, model)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    x = (x - mean) / scale
    current = (features(target, model) - mean) / scale
    y = train.actual_ace.to_numpy(dtype=float)
    ymean = y.mean()
    beta = np.linalg.solve(x.T @ x + shared.ALPHA * np.eye(x.shape[1]), x.T @ (y - ymean))
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
                record[model], beta = ridge_predict(train, target, model)
                if model == "satellite":
                    record["nino34_standardized_coefficient"] = float(beta[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def run(out: Path = DEFAULT) -> dict:
    manifest = json.loads((out / "source_manifest.json").read_text())
    for source in manifest:
        if shared.digest(out / source["path"]) != source["sha256"]:
            raise ValueError("Source checksum mismatch")
    records = parse_pacific(out / "inputs/hurdat2_nepac_1949_2025.txt.gz")
    activity = shared.annual_activity(records)
    if set(activity.year) != set(range(1981, 2026)):
        raise ValueError("Incomplete Pacific annual coverage")
    panel = build_panel(activity, pd.read_csv(out / "inputs/june_nino34.csv"))
    predictions = predict(panel)
    summary = shared.score(predictions)
    summary.update({"protocol": json.loads((out / "protocol.json").read_text()),
                    "source_code_sha256": shared.digest(Path(__file__)),
                    "shared_code_sha256": shared.digest(Path(shared.__file__)),
                    "protocol_sha256": shared.digest(out / "protocol.json"),
                    "source_manifest_sha256": shared.digest(out / "source_manifest.json"),
                    "target": "EP-origin tropical/subtropicalstormenergy eastof140W duringAugust–November",
                    "interpretation": "Separately frozen easternPacific test selected mechanistically afterAtlanticfailure; sameformulaandcontrols except satelliteadds onlyJuneNiño3.4. Revisedarchive scientificforecast, not originalvintagecertification ormarketalpha."})
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
