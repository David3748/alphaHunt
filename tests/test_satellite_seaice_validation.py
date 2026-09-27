from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_seaice_validation import DEFAULT, MODELS, load_panel, predict, score


def test_season_and_previous_year_alignment():
    panel = load_panel(DEFAULT / "inputs")
    assert panel.year.iloc[0] == 1980
    np.testing.assert_allclose(panel.prior_september.iloc[1:], panel.september_extent.iloc[:-1])
    assert panel.predictor_end.dt.month.eq(7).all()
    assert panel.target_start.dt.month.eq(9).all()
    assert (panel.forecast_at < panel.target_start).all()
    predictions = predict(panel)
    assert predictions.year.iloc[0] == 2000
    assert predictions.n_train.iloc[0] == 20
    assert (predictions.training_last_label_available < predictions.forecast_at).all()


def test_future_targets_do_not_change_earlier_predictions():
    panel = load_panel(DEFAULT / "inputs")
    original = predict(panel)
    panel.loc[panel.year >= 2010, "september_extent"] += 5
    panel.loc[panel.year > 2010, "prior_september"] += 5
    changed = predict(panel)
    np.testing.assert_array_equal(original.loc[original.year <= 2010, MODELS],
                                  changed.loc[changed.year <= 2010, MODELS])


def test_future_features_do_not_change_earlier_predictions():
    panel = load_panel(DEFAULT / "inputs")
    original = predict(panel)
    panel.loc[panel.year > 2010, "july_extent"] += 5
    changed = predict(panel)
    np.testing.assert_array_equal(original.loc[original.year <= 2010, MODELS],
                                  changed.loc[changed.year <= 2010, MODELS])


def test_unavailable_current_measurement_rejected():
    panel = load_panel(DEFAULT / "inputs")
    panel.loc[25, "assumed_predictor_available"] = pd.Timestamp("2099-01-01")
    with pytest.raises(ValueError, match="available"):
        predict(panel)


def test_wrong_month_is_rejected(tmp_path):
    for path in (DEFAULT / "inputs").glob("*.csv"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    path = tmp_path / "N_07_extent_v4.0.csv"
    frame = pd.read_csv(path, skipinitialspace=True)
    frame.loc[0, "mo"] = 8
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="month"):
        load_panel(tmp_path)


def test_strong_baseline_gain_and_claim_limits():
    result = score(predict(load_panel(DEFAULT / "inputs")))
    assert result["n_test_years"] == 26
    assert result["predefined_forecast_gate_passed"]
    assert result["metrics"]["satellite"]["rmse_million_km2"] == pytest.approx(.473795, abs=1e-6)
    assert result["comparisons"]["trend_prior"]["rmse_reduction_ci95_million_km2"][0] > 0
    assert result["comparisons"]["trend"]["rmse_reduction_ci95_million_km2"][0] < 0
    assert not result["positive_ci_against_every_baseline"]
    assert not result["original_vintage_operational_verification"]
    assert not result["shipping_capacity_verified"]
    assert not result["trading_alpha_verified"]


def test_original_monthly_reports_precede_fixed_issue():
    audit = pd.read_csv(DEFAULT / "revision_audit/original_july_values.csv")
    assert len(audit) == 18
    assert not audit.year.duplicated().any()
    assert (pd.to_datetime(audit.publication_date) < pd.to_datetime(audit.year.astype(str) + "-08-15")).all()
    assert (audit.original_july_extent > 0).all()
    assert audit.loc[audit.year.eq(2012), "current_minus_original"].iloc[0] == pytest.approx(-.27)
