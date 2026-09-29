from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_annual_solar_validation import MODELS, predict


def synthetic_panel():
    dates = pd.date_range("2015-01-31", "2024-12-31", freq="ME")
    frame = pd.DataFrame(index=dates)
    frame["year"] = dates.year
    frame["month"] = dates.month
    frame["time"] = (dates.year - 2015) + (dates.month - 1) / 12
    frame["daily_mwh"] = 1000 + 4 * frame.time + 100 * np.sin(2 * np.pi * frame.month / 12)
    frame["actual_mwh"] = frame.daily_mwh * dates.days_in_month
    frame["ghi"] = 5 + .1 * np.cos(2 * np.pi * frame.month / 12)
    frame["previous_year_ghi"] = frame.ghi - .1
    frame["tmean_c"] = 20 + np.sin(2 * np.pi * frame.month / 12)
    frame["dtr_c"] = 10 + np.cos(2 * np.pi * frame.month / 12)
    frame["prcp_mm"] = 5.0
    frame["label_available_at"] = pd.to_datetime((frame.year + 1).astype(str) + "-11-01")
    frame["irradiance_available_at"] = dates + pd.DateOffset(months=4) + pd.Timedelta(days=14)
    return frame


def test_training_annual_releases_and_target_publication_bracket_issue():
    predictions = predict(synthetic_panel())
    valid = predictions.loc[predictions.eligible]
    assert len(valid) == 72
    assert (valid.training_latest_label_release < valid.forecast_at).all()
    assert (valid.forecast_at < valid.target_label_available_at).all()
    assert (valid.source_available_at <= valid.forecast_at).all()
    assert valid.n_train.between(36, 48).all()


def test_persistence_uses_latest_released_same_calendar_month_and_leap_adjustment():
    panel = synthetic_panel()
    predictions = predict(panel).set_index("date")
    # At2020June issue,2019annual labels are not released until2020November.
    row = predictions.loc[pd.Timestamp("2020-02-29")]
    assert row.persistence == pytest.approx(panel.loc["2018-02-28", "daily_mwh"] * 29)
    expected = panel.loc[["2017-02-28", "2018-02-28"], "daily_mwh"].mean() * 29
    assert row.seasonal_mean == pytest.approx(expected)


def test_unreleased_past_labels_cannot_affect_issued_forecast():
    panel = synthetic_panel()
    original = predict(panel).set_index("date")
    target = pd.Timestamp("2020-02-29")
    issue = original.loc[target, "forecast_at"]
    modified = panel.copy()
    mask = modified.label_available_at >= issue
    modified.loc[mask, ["actual_mwh", "daily_mwh"]] *= 100
    changed = predict(modified).set_index("date")
    np.testing.assert_array_equal(original.loc[target, MODELS], changed.loc[target, MODELS])


def test_future_weather_and_irradiance_cannot_affect_earlier_issued_forecast():
    panel = synthetic_panel()
    original = predict(panel).set_index("date")
    target = pd.Timestamp("2020-02-29")
    modified = panel.copy()
    modified.loc[modified.index > target, ["ghi", "previous_year_ghi", "tmean_c", "dtr_c", "prcp_mm"]] += 50
    changed = predict(modified).set_index("date")
    np.testing.assert_array_equal(original.loc[target, MODELS], changed.loc[target, MODELS])


def test_late_target_irradiance_must_abstain():
    panel = synthetic_panel()
    panel.loc["2020-02-29", "irradiance_available_at"] = pd.Timestamp("2025-01-01")
    predictions = predict(panel).set_index("date")
    assert not predictions.loc[pd.Timestamp("2020-02-29"), "eligible"]


def test_delayed_training_irradiance_is_excluded():
    panel = synthetic_panel()
    target = pd.Timestamp("2020-02-29")
    predictions = predict(panel).set_index("date")
    panel.loc["2018-06-30", "irradiance_available_at"] = pd.Timestamp("2025-01-01")
    changed = predict(panel).set_index("date")
    assert changed.loc[target, "n_train"] == predictions.loc[target, "n_train"] - 1


def test_forecast_after_target_annual_release_is_rejected():
    with pytest.raises(ValueError, match="publication"):
        predict(synthetic_panel(), lag_months=22)
