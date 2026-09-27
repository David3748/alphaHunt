import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_crop_trading import REQUIRED_WHEAT_STATES, evaluate, evaluate_trade, first_friday_after, signals_from_predictions, summarize


def market():
    return pd.DataFrame({"wheat_return": [0.01] * 20, "rolled_at_start": [False] * 20},
                        index=pd.date_range("2020-01-03", periods=20, freq="W-FRI"))


def test_friday_signal_waits_until_following_friday():
    assert first_friday_after("2020-01-03") == pd.Timestamp("2020-01-10")
    assert first_friday_after("2020-01-02") == pd.Timestamp("2020-01-03")


def test_pre_entry_return_excluded_and_exact_holding_horizon():
    m = market()
    m.loc["2020-01-03", "wheat_return"] = 8.0
    result = evaluate_trade(m, "2020-01-02", 1)
    assert result["gross_return"] == pytest.approx(1.01 ** 12 - 1)
    assert result["exit_date"] == "2020-03-27"
    assert result["weekly_observations"] == 12


def test_entry_exit_and_roll_costs_deducted_for_short():
    m = market()
    m.loc["2020-01-17", "rolled_at_start"] = True
    result = evaluate_trade(m, "2020-01-02", -1)
    assert result["total_cost_bps"] == pytest.approx(55.)
    assert result["net_return"] == pytest.approx((.99 - .0025) ** 2 * (.99 - .0005) * .99 ** 9 - 1)


@pytest.mark.parametrize("column", ["wheat_return", "rolled_at_start"])
def test_missing_held_input_abstains_without_gap_filling(column):
    m = market().astype({"rolled_at_start": object})
    m.loc["2020-01-17", column] = np.nan
    assert evaluate_trade(m, "2020-01-02", 1)["status"] == "abstain"


def predictions():
    return pd.DataFrame([
        {"crop": "wheat", "stage": "heading", "year": 2020, "state": state, "model": model,
         "prediction_anomaly": value, "production_weight": weight, "available_date": "2020-01-02",
         "yield_anomaly": 999}
        for model, values in [("weather_only", [.2, -.1]), ("weather_plus_ndvi", [-.1, -.3])]
        for state, value, weight in zip(["KS", "ND"], values, [.7, .3])
    ])


def test_fixed_directions_weighting_and_no_outcome_dependency():
    p = predictions()
    a = signals_from_predictions(p, required_states=("KS", "ND"))
    p["yield_anomaly"] = -999
    pd.testing.assert_frame_equal(a, signals_from_predictions(p, required_states=("KS", "ND")))
    assert a.iloc[0].weather_anomaly == pytest.approx(.11)
    assert a.iloc[0].satellite_anomaly == pytest.approx(-.16)
    assert a.iloc[0].weather_only == -1
    assert a.iloc[0].weather_plus_ndvi == 1
    assert a.iloc[0].incremental_satellite == 1


def test_missing_state_model_does_not_renormalize_away():
    p = predictions().iloc[:-1]
    assert signals_from_predictions(p).iloc[0].status == "abstain"


def test_all_abstentions_return_zero_events_not_a_backtest():
    p = predictions().iloc[:-1]
    result = summarize(evaluate(signals_from_predictions(p), market()))
    assert result["strategies"]["weather_only"]["event_years"] == 0
    assert result["strategies"]["weather_only"]["mean_event_net_return"] is None


def test_state_missing_from_both_models_uses_frozen_universe():
    p = pd.DataFrame([
        {"crop": "wheat", "stage": "heading", "year": 2025, "state": state, "model": model,
         "prediction_anomaly": .1, "production_weight": 1 / 12, "available_date": "2025-06-29"}
        for model in ("weather_only", "weather_plus_ndvi") for state in REQUIRED_WHEAT_STATES
    ])
    assert signals_from_predictions(p).iloc[0].status == "eligible"
    without_kansas = p[p.state != "KS"]
    assert signals_from_predictions(without_kansas).iloc[0].status == "abstain"
