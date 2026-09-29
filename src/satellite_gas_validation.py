#!/usr/bin/env python3
"""Fixed satellite-temperature -> delayed-release residential-gas nowcast.

Default execution uses committed CSV snapshots without network or Excel engines.
This is a current-vintage chronological study, not a tradable vintage backtest.
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
DEFAULT = ROOT / "results/satellite_validation/gas"
WINTER_MONTHS = (1, 2, 3, 10, 11, 12)
MODELS = ("baseline", "satellite", "ground_hdd", "ground_hdd_satellite", "seasonal_persistence")
URLS = {
    "uah_tlt_v61.txt": "https://www.nsstc.uah.edu/data/msu/v6.1/tlt/uahncdc_lt_6.1.txt",
    "eia_residential_gas.xls": "https://www.eia.gov/dnav/ng/hist_xls/N3010US2m.xls",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_uah(path: Path) -> pd.DataFrame:
    lines = path.read_text().splitlines()
    header = next(line.split() for line in lines if "USA48" in line and "Year" in line)
    column = header.index("USA48")
    rows = []
    for line in lines:
        fields = line.split()
        if len(fields) != len(header) or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        year, month = int(fields[0]), int(fields[1])
        if 1900 <= year <= 2100 and 1 <= month <= 12:
            value = float(fields[column])
            if not np.isfinite(value) or not -20 < value < 20:
                raise ValueError("Invalid UAH temperature anomaly")
            rows.append({"date": pd.Timestamp(year, month, 1), "uah_usa48": value})
    frame = pd.DataFrame(rows)
    if frame.empty or frame.date.duplicated().any():
        raise ValueError("Empty or duplicate UAH monthly values")
    return frame.sort_values("date")


def parse_gas_xls(path: Path) -> pd.DataFrame:
    # xlrd is needed only for an explicit raw-data refresh; offline replay uses CSV.
    frame = pd.read_excel(path, sheet_name="Data 1", header=2)
    if len(frame.columns) != 2 or "Residential Consumption (MMcf)" not in frame.columns[1]:
        raise ValueError("Unexpected EIA workbook series or unit")
    dates = pd.to_datetime(frame.iloc[:, 0], errors="raise").dt.to_period("M").dt.to_timestamp()
    gas = pd.to_numeric(frame.iloc[:, 1], errors="raise")
    result = pd.DataFrame({"date": dates, "gas_mmcf": gas})
    if result.date.duplicated().any() or not result.gas_mmcf.gt(0).all():
        raise ValueError("Duplicate or invalid EIA monthly gas values")
    return result.sort_values("date")


def import_raw(out: Path) -> None:
    inputs = out / "inputs"
    for name, frame in (("uah_monthly.csv", parse_uah(inputs / "uah_tlt_v61.txt")),
                        ("gas_monthly.csv", parse_gas_xls(inputs / "eia_residential_gas.xls"))):
        frame.to_csv(inputs / name, index=False)


def fetch(out: Path) -> None:
    import requests
    inputs = out / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, url in URLS.items():
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        path = inputs / name
        path.write_bytes(response.content)
        manifest[name] = {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                          "http_last_modified": response.headers.get("Last-Modified"),
                          "sha256": sha256(path), "bytes": len(response.content)}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    import_raw(out)


def read_monthly(path: Path, value: str) -> pd.Series:
    frame = pd.read_csv(path, parse_dates=["date"])
    if not {"date", value}.issubset(frame.columns):
        raise ValueError(f"Expected date and {value} in {path.name}")
    if frame.date.duplicated().any() or not frame.date.dt.day.eq(1).all():
        raise ValueError("Duplicate date or non-month-start date")
    series = pd.to_numeric(frame.set_index("date")[value], errors="raise").sort_index()
    if np.isinf(series).any():
        raise ValueError("Infinite monthly value")
    return series


def load_panel(out: Path = DEFAULT) -> pd.DataFrame:
    gas = read_monthly(out / "inputs/gas_monthly.csv", "gas_mmcf")
    uah = read_monthly(out / "inputs/uah_monthly.csv", "uah_usa48")
    hdd = read_monthly(out / "ground_hdd/monthly_hdd.csv", "hdd")
    if gas.dropna().le(0).any() or hdd.dropna().lt(0).any():
        raise ValueError("Nonpositive gas or negative HDD")
    dates = pd.date_range(max(gas.index.min(), uah.index.min()), "2025-12-01", freq="MS")
    panel = pd.DataFrame(index=dates)
    panel.index.name = "date"
    panel["gas_mmcf"] = gas.reindex(dates)
    panel["uah_usa48"] = uah.reindex(dates)
    panel["hdd"] = hdd.reindex(dates)
    panel["year"] = dates.year
    panel["month"] = dates.month
    panel["days"] = dates.days_in_month
    panel["gas_bcf_day"] = panel.gas_mmcf / 1000 / panel.days
    # Look up the actual prior calendar year; never shift across a missing row.
    prior_dates = dates - pd.DateOffset(years=1)
    panel["prior_gas_bcf_day"] = gas.reindex(prior_dates).to_numpy() / 1000 / prior_dates.days_in_month
    panel["hdd_day"] = panel.hdd / panel.days
    panel["target_end"] = dates + pd.offsets.MonthEnd(0)
    panel["forecast_at"] = panel.target_end + pd.Timedelta(days=15)
    panel["assumed_feature_available"] = panel.target_end + pd.Timedelta(days=14)
    panel["assumed_label_available"] = panel.target_end + pd.offsets.MonthEnd(2)
    panel["winter_year"] = panel.year + panel.month.ge(10).astype(int)
    return panel.reset_index()


def design(frame: pd.DataFrame, model: str, target_year: int) -> np.ndarray:
    cols = [np.ones(len(frame)), frame.year.to_numpy() - target_year,
            frame.prior_gas_bcf_day.to_numpy()]
    if model in ("ground_hdd", "ground_hdd_satellite"):
        cols.append(frame.hdd_day.to_numpy())
    if model in ("satellite", "ground_hdd_satellite"):
        cols.append(frame.uah_usa48.to_numpy())
    if model not in MODELS[:-1]:
        raise ValueError("Unknown OLS model")
    return np.column_stack(cols)


def predict(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    required = ["gas_bcf_day", "prior_gas_bcf_day", "uah_usa48", "hdd_day"]
    for _, row in panel.loc[panel.year.between(2000, 2025) & panel.month.isin(WINTER_MONTHS)].iterrows():
        if not row.target_end < row.assumed_feature_available < row.forecast_at < row.assumed_label_available:
            raise ValueError("Feature/label availability does not bracket issue date")
        expected_years = set(range(int(row.year) - 10, int(row.year)))
        train = panel.loc[(panel.month == row.month) & panel.year.isin(expected_years)
                          & (panel.assumed_label_available < row.forecast_at)].sort_values("year")
        record = row.to_dict()
        record.update({"eligible": False, "n_train": len(train), "abstain_reason": ""})
        if set(train.year) != expected_years or len(train) != 10:
            record["abstain_reason"] = "Missing one of previous ten same-month training years"
        elif train[required].isna().any().any() or pd.isna(row[required]).any():
            record["abstain_reason"] = "Missing required source, training label, or evaluation target"
        elif (train.assumed_feature_available >= row.forecast_at).any():
            record["abstain_reason"] = "Training source unavailable at issue"
        else:
            record.update({"eligible": True, "training_first_year": int(train.year.min()),
                           "training_last_year": int(train.year.max()),
                           "training_last_label_available": train.assumed_label_available.max(),
                           "seasonal_persistence": float(row.prior_gas_bcf_day)})
            current = row.to_frame().T
            for model in MODELS[:-1]:
                x = design(train, model, int(row.year))
                beta = np.linalg.lstsq(x, train.gas_bcf_day.to_numpy(), rcond=None)[0]
                record[model] = float((design(current, model, int(row.year)) @ beta)[0])
                if model == "ground_hdd_satellite":
                    record["satellite_coefficient_conditional_hdd"] = float(beta[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame, draws: int = 10000) -> dict:
    valid = predictions.loc[predictions.eligible].sort_values("date").copy()
    if valid.empty:
        return {"n_test_months": 0, "incremental_usefulness_gate_passed": False,
                "status": "no_complete_evaluation_months"}
    error = valid[list(MODELS)].to_numpy() - valid.gas_bcf_day.to_numpy()[:, None]
    squared = error ** 2
    metrics = {model: {"rmse_bcf_day": float(np.sqrt(squared[:, i].mean())),
                       "mae_bcf_day": float(np.abs(error[:, i]).mean())}
               for i, model in enumerate(MODELS)}
    winters = valid.winter_year.unique()
    groups = [np.flatnonzero(valid.winter_year.to_numpy() == w) for w in winters]
    rng = np.random.default_rng(20260927)
    bootstrap = []
    for _ in range(draws):
        starts = rng.integers(0, len(groups), size=(len(groups) + 1) // 2)
        winter_indices = ((starts[:, None] + np.arange(2)) % len(groups)).ravel()[:len(groups)]
        month_indices = np.concatenate([groups[i] for i in winter_indices])
        bootstrap.append(np.sqrt(squared[month_indices].mean(axis=0)))
    bootstrap = np.asarray(bootstrap)
    comparisons = {}
    for candidate, baseline in (("satellite", "baseline"), ("satellite", "ground_hdd"),
                                ("ground_hdd_satellite", "ground_hdd"),
                                ("satellite", "seasonal_persistence")):
        i, j = MODELS.index(candidate), MODELS.index(baseline)
        gain = metrics[baseline]["rmse_bcf_day"] - metrics[candidate]["rmse_bcf_day"]
        key = f"{candidate}_vs_{baseline}"
        ci = np.quantile(bootstrap[:, j] - bootstrap[:, i], [.025, .975]).tolist()
        comparisons[key] = {"rmse_reduction_bcf_day": gain,
                            "rmse_reduction_fraction": gain / metrics[baseline]["rmse_bcf_day"],
                            "rmse_reduction_ci95_bcf_day": ci,
                            "mae_reduction_fraction": 1 - metrics[candidate]["mae_bcf_day"] / metrics[baseline]["mae_bcf_day"],
                            "months_lower_squared_error": int((squared[:, i] < squared[:, j]).sum())}
    gate = all(comparisons[key]["rmse_reduction_fraction"] >= .05
               and comparisons[key]["mae_reduction_fraction"] > 0
               and comparisons[key]["rmse_reduction_ci95_bcf_day"][0] > 0
               for key in ("satellite_vs_baseline", "ground_hdd_satellite_vs_ground_hdd"))
    return {"n_test_months": len(valid), "n_winter_clusters": len(winters),
            "first_test_month": str(valid.date.min().date()), "last_test_month": str(valid.date.max().date()),
            "n_abstentions": int((~predictions.eligible).sum()), "metrics": metrics,
            "comparisons": comparisons, "incremental_usefulness_gate_passed": bool(gate),
            "status": "current_vintage_incremental_nowcast_gate_passed" if gate else "incremental_nowcast_gate_failed",
            "original_vintage_operational_verification": False, "trading_alpha_verified": False,
            "prospective_verification": False, "cross_candidate_multiple_testing_adjusted": False}


def run(out: Path = DEFAULT) -> dict:
    panel = load_panel(out)
    predictions = predict(panel)
    summary = score(predictions)
    summary["post_method_change_training_targets_sensitivity_2021_2025"] = score(predictions.loc[predictions.year >= 2021])
    summary["method_change_sensitivity_note"] = "2021-2025 has ten post-August-2010 training targets per month. January-March 2011 training rows still have prior-year gas features from the earlier methodology; this is not a claim that every training input is post-change. The predeclared sample is unchanged."
    summary["protocol"] = json.loads((out / "protocol.json").read_text())
    summary["source_manifest"] = json.loads((out / "source_manifest.json").read_text())
    summary["offline_input_sha256"] = {str(path.relative_to(out)): sha256(path)
                                       for path in (out / "inputs/uah_monthly.csv", out / "inputs/gas_monthly.csv",
                                                    out / "ground_hdd/monthly_hdd.csv")}
    summary["source_code_sha256"] = sha256(Path(__file__))
    summary["units"] = "Billion cubic feet per day (EIA MMcf divided by 1000 and calendar days)"
    summary["interpretation"] = "Contemporaneous winter gas-demand nowcast ahead of assumed EIA publication; all predictors and labels are revised current archives. Ground HDD is a required comparator. No market alpha or actual historical-release reconstruction."
    panel.to_csv(out / "panel.csv", index=False)
    predictions.to_csv(out / "predictions.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--fetch", action="store_true", help="Refresh UAH/EIA raw inputs; requires requests and xlrd")
    parser.add_argument("--import-raw", action="store_true", help="Parse stored UAH/EIA raw files; requires xlrd")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.out)
    elif args.import_raw:
        import_raw(args.out)
    print(json.dumps(run(args.out), indent=2))
