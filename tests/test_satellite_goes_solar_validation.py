"""Independent semantic tests for the frozen GOES production forecast."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location(
    "goes_solar_validation", Path(__file__).resolve().parents[1] / "src/satellite_goes_solar_validation.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def synthetic_panel():
    dates = pd.date_range("2015-01-31", "2025-12-31", freq="ME")
    panel = pd.DataFrame(index=dates)
    panel["actual_mwh"] = dates.days_in_month * 100.
    panel["daily_mwh"] = 100.
    panel["year"], panel["month"] = dates.year, dates.month
    panel["time"] = (dates.year - 2015) + (dates.month - 1) / 12
    panel["label_available_at"] = dates + pd.offsets.MonthEnd(2)
    panel["ghi"], panel["eligible"], panel["daylight_coverage"] = 2., True, 1.
    panel["last_source_available_at"] = dates + pd.Timedelta(days=1)
    panel["tmean_c"], panel["dtr_c"], panel["prcp_mm"] = 15., 8., 10.
    return panel


def first_forecast(panel, date="2022-01-31"):
    return mod.predict(panel, date, date).iloc[0]


def test_fixed_physical_ratio_and_matched_calibration():
    panel = synthetic_panel()
    panel.loc["2022-01-31", "ghi"] = 4.
    forecast = first_forecast(panel)
    assert forecast.eligible
    assert forecast.satellite == 6200.
    assert forecast.seasonal_mean == forecast.persistence == 3100.
    assert forecast.prior_year_irradiance_placebo == 3100.
    assert forecast.forecast_at == pd.Timestamp("2022-02-14")
    assert forecast.calibration_ghi == 2.


def test_target_and_future_labels_do_not_affect_current_forecast():
    panel = synthetic_panel()
    expected = first_forecast(panel)
    panel.loc[panel.index >= "2022-01-31", ["actual_mwh", "daily_mwh"]] *= 9876.
    actual = first_forecast(panel)
    for model in mod.MODELS:
        assert actual[model] == pytest.approx(expected[model])
    assert actual.actual_mwh != expected.actual_mwh


def test_unreleased_training_label_is_excluded_and_common_window_is_capped():
    panel = synthetic_panel()
    expected = first_forecast(panel)
    # December's two-month embargo ends after the February 14 issue.
    panel.loc["2021-12-31", ["actual_mwh", "daily_mwh"]] *= 1e9
    actual = first_forecast(panel)
    assert actual.n_train == 48
    assert actual.training_last_month == pd.Timestamp("2021-11-30")
    assert actual.training_latest_label_release < actual.forecast_at
    for model in mod.MODELS:
        assert actual[model] == pytest.approx(expected[model])


def test_missing_weather_is_not_imputed_and_training_requires_36_months():
    panel = synthetic_panel()
    panel.loc["2022-01-31", "prcp_mm"] = np.nan
    assert not first_forecast(panel).eligible
    assert not first_forecast(synthetic_panel(), "2017-01-31").eligible


@pytest.mark.parametrize("column,value", [
    ("eligible", False), ("daylight_coverage", .899), ("ghi", np.nan),
    ("ghi", -1.), ("ghi", np.inf), ("last_source_available_at", pd.NaT),
    ("last_source_available_at", pd.Timestamp("2022-02-15")),
])
def test_missing_late_or_bad_current_satellite_abstains(column, value):
    panel = synthetic_panel()
    panel.loc["2022-01-31", column] = value
    assert not first_forecast(panel).eligible


def test_historical_satellite_and_labels_must_also_be_available():
    panel = synthetic_panel()
    panel.loc["2020-01-31", "last_source_available_at"] = pd.Timestamp("2022-02-15")
    assert not first_forecast(panel).eligible
    panel = synthetic_panel()
    panel.loc["2021-01-31", "label_available_at"] = pd.Timestamp("2022-02-15")
    assert not first_forecast(panel).eligible


def test_leap_month_uses_daily_output_not_raw_monthly_persistence():
    forecast = first_forecast(synthetic_panel(), "2024-02-29")
    assert forecast.seasonal_mean == forecast.persistence == forecast.satellite == 2900.


def annual_inputs(tmp_path):
    dates = pd.date_range("2020-01-31", "2025-12-31", freq="ME")
    features = pd.DataFrame({"plant_code": 57439, "date": dates,
                             "ghi_kwh_m2_day": 2., "eligible": True,
                             "daylight_coverage": 1.,
                             "last_source_available_at": dates + pd.Timedelta(days=1)})
    features.to_csv(tmp_path / "monthly_irradiance.csv", index=False)
    labels = pd.DataFrame([{"year": year, "plant_id": 57439,
                            "annual_generation_mwh": 100 * (366 if pd.Timestamp(year=year, month=12, day=31).is_leap_year else 365),
                            "monthly_sum_mwh": 999999999.}
                           for year in range(2020, 2026)])
    (tmp_path / "reporting_frequency").mkdir()
    labels.to_csv(tmp_path / "reporting_frequency/annual_generation.csv", index=False)
    return features, labels


def test_annual_uses_authoritative_totals_daily_normalization_and_prior_labels(tmp_path):
    features, labels = annual_inputs(tmp_path)
    features.loc[features.date.dt.year.eq(2024), "ghi_kwh_m2_day"] = 3.
    features.to_csv(tmp_path / "monthly_irradiance.csv", index=False)
    forecasts, _ = mod.annual_confirmation(tmp_path)
    row = forecasts.set_index("year").loc[2024]
    assert row.seasonal_mean == row.persistence == 36600.
    assert row.satellite == 54900.
    assert row.actual_mwh == 36600.
    assert row.latest_training_label_release < row.forecast_at
    labels.loc[labels.year.eq(2024), "annual_generation_mwh"] = 1e9
    labels.to_csv(tmp_path / "reporting_frequency/annual_generation.csv", index=False)
    changed, _ = mod.annual_confirmation(tmp_path)
    assert changed.set_index("year").loc[2024, "satellite"] == row.satellite


@pytest.mark.parametrize("column,value", [
    ("last_source_available_at", pd.NaT),
    ("last_source_available_at", pd.Timestamp("2023-01-15")),
    ("ghi_kwh_m2_day", np.nan), ("ghi_kwh_m2_day", -1.),
    ("eligible", False), ("daylight_coverage", .899), ("daylight_coverage", np.nan),
])
def test_annual_missing_or_late_satellite_cannot_pass(tmp_path, column, value):
    features, _ = annual_inputs(tmp_path)
    features.loc[features.date.eq("2022-12-31"), column] = value
    features.to_csv(tmp_path / "monthly_irradiance.csv", index=False)
    forecasts, summary = mod.annual_confirmation(tmp_path)
    assert not forecasts.set_index("year").loc[2022, "eligible"]
    assert not summary["point_confirmation_passed"]


def test_annual_incomplete_year_abstains_instead_of_summing_available_months(tmp_path):
    features, _ = annual_inputs(tmp_path)
    features = features.loc[~features.date.eq("2024-09-30")]
    features.to_csv(tmp_path / "monthly_irradiance.csv", index=False)
    forecasts, summary = mod.annual_confirmation(tmp_path)
    assert summary["n_test_years"] == 2
    assert not forecasts.set_index("year").loc[2024, "eligible"]
    assert not summary["point_confirmation_passed"]


def scored_predictions():
    dates = pd.date_range("2022-01-31", "2025-12-31", freq="ME")
    frame = pd.DataFrame({"date": dates, "year": dates.year, "eligible": True, "actual_mwh": 100.})
    for name in mod.BASELINES:
        frame[name] = 110.
    frame["satellite"], frame["prior_year_irradiance_placebo"] = 102., 105.
    return frame


def test_primary_gate_requires_every_baseline_and_placebo():
    frame = scored_predictions()
    assert mod.score(frame)["primary_gate_passed"]
    frame["weather"] = 101.
    assert not mod.score(frame)["primary_gate_passed"]
    frame = scored_predictions()
    frame["prior_year_irradiance_placebo"] = 101.
    assert not mod.score(frame)["primary_gate_passed"]


def test_score_retains_abstentions_and_rejects_unknown_outcomes():
    frame = scored_predictions()
    frame.loc[0, "eligible"] = False
    summary = mod.score(frame)
    assert summary["n_test_months"] == 47
    assert summary["abstentions"] == 1
    frame.loc[1, "actual_mwh"] = np.nan
    with pytest.raises(ValueError, match="Missing prediction/outcome"):
        mod.score(frame)
