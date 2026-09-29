from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_gas_validation import MODELS, load_panel, parse_uah, predict, score


def synthetic_sources(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "ground_hdd").mkdir()
    dates = pd.date_range("1980-01-01", "2025-12-01", freq="MS")
    rng = np.random.default_rng(42)
    cold = rng.normal(size=len(dates))
    gas = 400000 + 1000 * (dates.year - 1980) + 10000 * cold
    pd.DataFrame({"date": dates, "gas_mmcf": gas}).to_csv(tmp_path / "inputs/gas_monthly.csv", index=False)
    pd.DataFrame({"date": dates, "uah_usa48": -cold}).to_csv(tmp_path / "inputs/uah_monthly.csv", index=False)
    pd.DataFrame({"date": dates, "hdd": 600 + 50 * cold}).to_csv(tmp_path / "ground_hdd/monthly_hdd.csv", index=False)
    return tmp_path


def test_uah_uses_named_usa48_column_not_neighbor(tmp_path):
    path = tmp_path / "uah.txt"
    path.write_text("Year Mo Globe USA48 USA49\n2001 2 1.2 -2.3 4.5\ninvalid footer\n")
    parsed = parse_uah(path)
    assert parsed.iloc[0].date == pd.Timestamp("2001-02-01")
    assert parsed.iloc[0].uah_usa48 == -2.3


def test_exact_prior_year_and_leap_month_denominator(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path)).set_index("date")
    assert panel.loc["2020-02-01", "prior_gas_bcf_day"] == pytest.approx(panel.loc["2019-02-01", "gas_bcf_day"])
    assert panel.loc["2020-02-01", "days"] == 29
    assert panel.loc["2020-02-01", "forecast_at"] == pd.Timestamp("2020-03-15")
    assert panel.loc["2020-02-01", "assumed_label_available"] == pd.Timestamp("2020-04-30")


def test_target_and_future_outcomes_cannot_affect_issued_prediction(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path))
    original = predict(panel)
    changed = panel.copy()
    changed.loc[changed.date >= "2015-01-01", "gas_bcf_day"] += 100
    changed.loc[changed.date >= "2016-01-01", "prior_gas_bcf_day"] += 100
    altered = predict(changed)
    mask = original.date <= "2015-01-01"
    np.testing.assert_array_equal(original.loc[mask, MODELS], altered.loc[mask, MODELS])


def test_future_weather_cannot_affect_issued_prediction(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path))
    original = predict(panel)
    panel.loc[panel.date > "2015-01-01", ["uah_usa48", "hdd_day"]] += 100
    altered = predict(panel)
    mask = original.date <= "2015-01-01"
    np.testing.assert_array_equal(original.loc[mask, MODELS], altered.loc[mask, MODELS])


def test_ten_exact_calendar_years_and_common_months(tmp_path):
    pred = predict(load_panel(synthetic_sources(tmp_path)))
    assert len(pred) == 156
    assert pred.eligible.all()
    assert set(pred.month) == {1, 2, 3, 10, 11, 12}
    assert (pred.training_first_year == pred.year - 10).all()
    assert (pred.training_last_year == pred.year - 1).all()
    assert pred.n_train.eq(10).all()
    assert (pred.training_last_label_available < pred.forecast_at).all()


def test_missing_ground_feature_abstains_instead_of_ffill(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path))
    panel.loc[panel.date == "2003-01-01", "hdd_day"] = np.nan
    predictions = predict(panel)
    affected = predictions.loc[(predictions.month == 1) & predictions.year.between(2003, 2013)]
    assert (~affected.eligible).all()
    assert affected.ground_hdd.isna().all()
    assert predictions.loc[predictions.date == "2003-02-01", "eligible"].all()


def test_unavailable_feature_is_rejected(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path))
    panel.loc[panel.date == "2000-01-01", "assumed_feature_available"] = pd.Timestamp("2099-01-01")
    with pytest.raises(ValueError, match="availability"):
        predict(panel)


def test_missing_year_is_not_shifted_into_training(tmp_path):
    panel = load_panel(synthetic_sources(tmp_path))
    panel = panel.loc[panel.date != "1993-01-01"]
    pred = predict(panel)
    assert not pred.loc[pred.date == "2000-01-01", "eligible"].iloc[0]
    assert pred.loc[pred.date == "2000-02-01", "eligible"].iloc[0]


def test_score_preserves_claim_limits_and_all_abstention(tmp_path):
    pred = predict(load_panel(synthetic_sources(tmp_path)))
    result = score(pred, draws=100)
    assert result["n_winter_clusters"] == 27
    assert not result["original_vintage_operational_verification"]
    assert not result["trading_alpha_verified"]
    pred["eligible"] = False
    result = score(pred, draws=100)
    assert result["n_test_months"] == 0
    assert not result["incremental_usefulness_gate_passed"]
