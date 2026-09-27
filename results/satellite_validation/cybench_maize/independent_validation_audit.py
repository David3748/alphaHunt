#!/usr/bin/env python3
"""Recompute the frozen county forecast report without importing its model code."""
from pathlib import Path
import hashlib
import json
import argparse
import ast
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
MODELS = ["weather", "satellite", "trend", "persistence", "recent_mean"]
BASELINES = [name for name in MODELS if name != "satellite"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close(actual, expected, label):
    if not np.allclose(actual, expected, rtol=1e-9, atol=1e-10, equal_nan=True):
        raise AssertionError(label)


def verify_forecast_implementation(root, summary):
    current = root.parents[2] / "src/satellite_cybench_validation.py"
    expected = summary.get("forecast_code_sha256", summary["code_sha256"])
    candidates = [current, root / "first_fit_source.py.txt", ROOT / "forecast_implementation_snapshot.py"]
    matched = next((path for path in candidates if path.exists() and digest(path) == expected), None)
    assert matched is not None, "Missing exact fitted implementation snapshot"
    if digest(current) != expected:
        def forecast_ast(path):
            tree = ast.parse(path.read_text())
            tree.body = [node for node in tree.body if not (isinstance(node, ast.FunctionDef) and node.name == "score")]
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "summary"
                        and node.func.attr == "update"):
                    node.keywords = [item for item in node.keywords
                                     if item.arg not in ["forecast_code_sha256", "reporting_code_sha256"]]
            return ast.dump(tree, include_attributes=False)
        assert forecast_ast(current) == forecast_ast(matched), "Forecast implementation changed beyond reporting score and source-hash metadata"
    if "reporting_code_sha256" in summary:
        assert digest(current) == summary["reporting_code_sha256"]
    return dict(forecast_code_sha256=expected, current_code_sha256=digest(current),
                exact_fitted_snapshot_verified=True, non_score_code_unchanged=True)


def audit_source_aggregation(root, features, issue_month=8):
    ndvi = pd.read_csv(root / "ndvi_observations.csv.gz")
    weather = pd.read_csv(root / "monthly_weather_statistics.csv.gz")
    assert not ndvi.duplicated(["adm_id", "date"]).any()
    assert not weather.duplicated(["adm_id", "year", "month"]).any()
    date = pd.to_datetime(ndvi.date, utc=True)
    end = pd.to_datetime(ndvi.window_end, utc=True)
    available = pd.to_datetime(ndvi.assumed_available_date, utc=True)
    assert end.eq(date + pd.Timedelta(days=8) - pd.Timedelta(seconds=1)).all()
    assert available.eq(end + pd.Timedelta(days=14)).all()
    assert (available < pd.to_datetime(ndvi.year.astype(str) + f"-{issue_month:02d}-15T12:00:00Z")).all()
    assert date.dt.month.eq(ndvi.month).all() and date.dt.year.eq(ndvi.year).all()
    assert (((date.dt.dayofyear - 1) % 8) == 0).all()
    assert ndvi.ndvi.dropna().between(-1, 1).all()
    expected_index = features.set_index(["adm_id", "year"]).index
    for month, tag in [(4, "apr"), (5, "may"), (6, "jun"), (7, "jul"), (8, "aug")][:issue_month - 4]:
        observations = ndvi[ndvi.month.eq(month)].groupby(["adm_id", "year"]).ndvi
        counts = observations.count().reindex(expected_index, fill_value=0)
        means = observations.mean().reindex(expected_index)
        close(counts.to_numpy(), features[f"ndvi_count_{tag}"], "NDVI observation counts differ")
        close(means.to_numpy(), features[f"ndvi_{tag}"], "NDVI means differ")
        days = 30 if month in [4, 6] else 31
        sufficient = weather[weather.month.eq(month)].set_index(["adm_id", "year"]).reindex(expected_index)
        assert sufficient.rows.eq(days).all()
        assert sufficient.day_bits.eq(2 ** days - 1).all()
        for variable in ("tmin", "tmax", "tavg", "vpd", "prec", "rad", "et0", "cwb"):
            count = sufficient[variable + "_count"]
            value = sufficient[variable + "_sum"]
            if variable in ("tmin", "tmax", "tavg", "vpd"):
                value = value / days
            value = value.where(count.eq(days))
            close(count.to_numpy(), features[f"{variable}_count_{tag}"], "Weather counts differ")
            close(value.to_numpy(), features[f"{variable}_{tag}"], "Weather aggregation differs")
    return dict(ndvi_observations=len(ndvi), monthly_weather_statistics=len(weather),
                all_composites_complete_before_issue=True, aggregation_recomputed=True)


def run(root=ROOT):
    summary = json.loads((root / "summary.json").read_text())
    issue_month = int(summary.get("forecast_issue_month", 8))
    assert issue_month in [8, 9]
    sources = json.loads((root / "source_manifest.json").read_text())
    extraction = json.loads((root / "extraction_summary.json").read_text())
    for name, expected in sources["compact_input_sha256"].items():
        assert digest(root / name) == expected, name
    assert digest(root / "monthly_features.csv.gz") == extraction["feature_sha256"]
    for name, expected in summary["input_sha256"].items():
        assert digest(root / name) == expected, name
    assert digest(root / "protocol.json") == summary["protocol_sha256"]
    implementation = verify_forecast_implementation(root, summary)
    predictions = pd.read_csv(root / "predictions.csv.gz", low_memory=False)
    labels = pd.read_csv(root / "yields.csv.gz").rename(columns={"yield": "source_actual"})
    features = pd.read_csv(root / "monthly_features.csv.gz")
    source_aggregation = audit_source_aggregation(root, features, issue_month)
    assert not predictions.duplicated(["adm_id", "year"]).any()
    assert not labels.duplicated(["adm_id", "year"]).any()
    joined = predictions.merge(labels, on=["adm_id", "year"], how="left", validate="one_to_one")
    close(joined.actual, joined.source_actual, "Prediction labels differ from independent source")
    forecast_at = pd.to_datetime(predictions.forecast_at, utc=True)
    assert forecast_at.eq(pd.to_datetime(predictions.year.astype(str) + f"-{issue_month:02d}-15T12:00:00Z")).all()
    eligible = predictions[predictions.eligible].copy()
    assert np.isfinite(eligible[MODELS]).all().all()
    assert predictions.loc[~predictions.eligible, "abstention_reason"].notna().all()
    held = eligible[np.isfinite(eligible.actual)].copy()
    assert len(held) == summary["n_county_years"]
    for month in ("apr", "may", "jun", "jul", "aug")[:issue_month - 4]:
        end = pd.to_datetime(eligible[f"ndvi_latest_window_end_{month}"], utc=True)
        assert ((end + pd.Timedelta(days=14)) < pd.to_datetime(eligible.forecast_at, utc=True)).all()
        n, expected = eligible[f"ndvi_count_{month}"], eligible[f"ndvi_expected_{month}"]
        assert (n.ge(2) & n.le(expected) & (n / expected).ge(.5)).all()
        assert eligible[f"ndvi_{month}"].between(-1, 1).all()
        days = 30 if month in ("apr", "jun") else 31
        for variable in ("tmin", "tmax", "tavg", "vpd", "prec", "rad", "et0", "cwb"):
            assert eligible[f"{variable}_count_{month}"].eq(days).all()
            assert eligible[f"{variable}_expected_{month}"].eq(days).all()
            assert np.isfinite(eligible[f"{variable}_{month}"]).all()

    # Reconstruct all three non-ML comparators and the current fold's lag feature.
    histories = {county: group.sort_values("year") for county, group in labels.groupby("adm_id")}
    max_comparator_difference = 0.
    for row in eligible.itertuples():
        history = histories[row.adm_id]
        history = history[(history.year < row.year) & np.isfinite(history.source_actual)]
        assert len(history) >= 8
        years = history.year.to_numpy()
        values = history.source_actual.to_numpy()
        x = years - years.mean()
        slope = float(x @ (values - values.mean()) / (x @ x))
        trend = float(values.mean() + slope * (row.year - years.mean()))
        prior_trend = float(values.mean() + slope * (years[-1] - years.mean()))
        actual = np.array([row.trend, row.persistence, row.recent_mean, row.prior_residual])
        expected = np.array([trend, values[-1], values[-5:].mean(), values[-1] - prior_trend])
        close(actual, expected, "Non-ML comparator or lag does not match released history")
        max_comparator_difference = max(max_comparator_difference, float(abs(actual - expected).max()))
        assert row.latest_training_year == years[-1]
    annual = []
    coverage = []
    for year, group in predictions.groupby("year"):
        selected = group[group.eligible & np.isfinite(group.actual)]
        losses = np.column_stack([selected[name] - selected.actual for name in MODELS])
        record = {"year": int(year), "n_counties": len(selected)}
        record.update({name + "_mse": float(np.mean(losses[:, j] ** 2)) for j, name in enumerate(MODELS)})
        record.update({name + "_mae": float(np.mean(abs(losses[:, j]))) for j, name in enumerate(MODELS)})
        annual.append(record)
        coverage.append(dict(year=int(year), rows=len(group), forecasts=int(group.eligible.sum()),
                             unknown_outcome_forecasts=int((group.eligible & group.actual.isna()).sum()),
                             scored=len(selected), abstentions=int((~group.eligible).sum())))
        reported = next(item for item in summary["coverage"] if item["year"] == year)
        assert reported["feature_rows"] == len(group)
        assert reported["forecasts"] == int(group.eligible.sum())
        assert reported["observed_scored"] == len(selected)
        assert len(group) == int(features.year.eq(year).sum())
    annual = pd.DataFrame(annual).sort_values("year")
    reported_annual = pd.read_csv(root / "annual_losses.csv").sort_values("year")
    close(annual[reported_annual.columns], reported_annual, "Annual metrics differ")
    metrics = {name: {"rmse_t_ha": float(np.sqrt(annual[name + "_mse"].mean())),
                      "mae_t_ha": float(annual[name + "_mae"].mean())} for name in MODELS}
    for name in MODELS:
        for metric in metrics[name]:
            close(metrics[name][metric], summary["metrics"][name][metric], "Overall metric differs")

    # Independent loop implementation: each draw samples consecutive calendar
    # years, using the same draw for every county/model. Missing years stay gaps.
    calendar = np.arange(int(annual.year.min()), int(annual.year.max()) + 1)
    lookup = annual.set_index("year").to_dict("index")
    starts = np.random.default_rng(20260927).integers(0, len(calendar), (10000, (len(calendar) + 4) // 5))
    draws = {name: [] for name in BASELINES}
    for draw in starts:
        years = [int(calendar[(int(start) + offset) % len(calendar)]) for start in draw for offset in range(5)][:len(calendar)]
        rows = [lookup[year] for year in years if year in lookup]
        sat = np.mean([row["satellite_mse"] for row in rows])
        for name in BASELINES:
            base = np.mean([row[name + "_mse"] for row in rows])
            draws[name].append(1 - np.sqrt(sat / base))
    comparisons = {}
    for name in BASELINES:
        comparisons[name] = dict(
            rmse_reduction=1 - metrics["satellite"]["rmse_t_ha"] / metrics[name]["rmse_t_ha"],
            mae_reduction=1 - metrics["satellite"]["mae_t_ha"] / metrics[name]["mae_t_ha"],
            rmse_reduction_ci95=list(map(float, np.quantile(draws[name], [.025, .975]))),
            years_lower_mse=int((annual.satellite_mse < annual[name + "_mse"]).sum()))
        for metric, value in comparisons[name].items():
            close(value, summary["comparisons"][name][metric], "Paired comparison differs")
    strongest = min(BASELINES, key=lambda name: metrics[name]["rmse_t_ha"])
    assert strongest == summary["strongest_baseline"]
    post = annual[annual.year.isin([2022, 2023])]
    post_signs = bool(len(post) == 2 and (post.satellite_mse < post.weather_mse).all() and (post.satellite_mse < post.trend_mse).all())
    count_gate = bool(len(annual) >= 8 and annual.n_counties.ge(500).all())
    gate = bool(count_gate and post_signs and comparisons[strongest]["rmse_reduction"] >= .05
                and all(item["mae_reduction"] > 0 and item["rmse_reduction_ci95"][0] > 0 for item in comparisons.values()))
    assert gate == summary["forecast_gate_passed"]
    assert post_signs == summary["post_reference_year_sign_check_passed"]
    assert count_gate == summary["data_count_gate_passed"]
    statistical = bool(count_gate and post_signs and all(item["mae_reduction"] > 0 and item["rmse_reduction_ci95"][0] > 0 for item in comparisons.values()))
    if "statistical_skill_components_passed" in summary:
        assert statistical == summary["statistical_skill_components_passed"]
        assert bool(comparisons[strongest]["rmse_reduction"] >= .05) == summary["materiality_component_passed"]
    result = dict(audit_passed=True, forecast_gate_passed=gate, metrics=metrics,
                  statistical_skill_components_passed=statistical,
                  forecast_issue_month=issue_month,
                  comparisons=comparisons, strongest_baseline=strongest,
                  post_reference_year_signs=post.to_dict("records"), coverage=coverage,
                  maximum_comparator_difference=max_comparator_difference,
                  source_aggregation=source_aggregation,
                  implementation=implementation,
                  source_hashes_verified=True, predictions_sha256=digest(root / "predictions.csv.gz"),
                  summary_sha256=digest(root / "summary.json"), audit_code_sha256=digest(__file__),
                  original_vintage_operational_verification=False,
                  scope="Independent arithmetic, chronology, source coverage and comparator reconstruction; no estimator retuning or refit.")
    (root / "independent_validation_audit.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    run(parser.parse_args().output_dir)
