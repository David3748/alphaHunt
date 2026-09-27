"""Reporting and public-input failure cases beyond the independent timing audit."""
import json

import numpy as np
import pandas as pd
import pytest

from src import corn_model as model


def test_forecast_metrics_weight_years_not_number_of_horizons():
    # Two errors of 1 in year1, one error of 3 in year2 => sqrt((1+9)/2).
    p = pd.DataFrame({"year": [2020, 2020, 2021], "status": ["ready"]*3,
                      "target_yield_bu_acre": [100., 100., 100.]})
    for name in model.MODELS:
        p[f"{name}_yield_bu_acre"] = [101., 101., 103.]
    result = model.score_forecasts(p)
    assert result["n_years"] == 2 and result["n_events"] == 3
    assert result["models"]["satellite"]["rmse_bu_acre"] == pytest.approx(np.sqrt(5))
    assert result["models"]["satellite"]["mae_bu_acre"] == 2
    assert result["satellite_comparisons"]["weather"]["conditional_ci95"] == [0., 0.]


def test_no_forecast_outcomes_has_no_invented_metric():
    p = pd.DataFrame({"status": ["ready", "abstain"], "target_yield_bu_acre": [np.nan, 100.]})
    result = model.score_forecasts(p)
    assert result["n_events"] == 0 and result["models"] == {}
    assert result["trading_alpha_verified"] is False


def market_events():
    rows = []
    for year in [2020, 2021]:
        for strategy, ret in [("usda", 0.), ("weather", .01), ("satellite", .02)]:
            rows.append({"year": year, "forecast_at": f"{year}-08-15T12:00:00Z", "strategy": strategy,
                         "entry": f"{year}-08-17", "exit": f"{year}-09-15", "direction": 0 if strategy == "usda" else 1,
                         "net_return": ret, "double_cost_net_return": ret-.005 if ret else 0.})
    return pd.DataFrame(rows)


def test_costed_return_compounds_and_paired_intervals_keep_shared_years():
    result = model.market_summary(market_events())
    sat = result["strategies"]["satellite"]
    assert sat["compound_net_event_return"] == pytest.approx(1.02**2-1)
    assert sat["double_cost_compound_return"] == pytest.approx(1.015**2-1)
    assert sat["paired_mean_advantage_vs_weather_ci95"] == pytest.approx([.01, .01])
    assert result["strategies"]["usda"]["n_positions"] == 0


def test_market_comparisons_drop_unmatched_support_for_every_strategy():
    events = market_events()
    events.loc[(events.year == 2020) & (events.strategy == "weather"), "net_return"] = np.nan
    result = model.market_summary(events)
    assert result["matched_events"] == 1
    assert all(x["n_events"] == 1 for x in result["strategies"].values())


def test_bankruptcy_cannot_turn_into_positive_compounded_capital():
    events = market_events()
    events.loc[events.strategy == "satellite", ["net_return", "double_cost_net_return"]] = -1.2
    result = model.market_summary(events)["strategies"]["satellite"]
    assert result["bankruptcy_encountered"] is True
    assert result["compound_net_event_return"] is None
    assert result["double_cost_compound_return"] is None
    assert result["mean_net_event_return"] == -1.2


def test_overlapping_full_notional_windows_are_not_compounded():
    events = market_events()
    events.loc[events.year == 2020, "exit"] = "2021-09-01"
    result = model.market_summary(events)
    assert all(x["overlapping_windows"] for x in result["strategies"].values())
    assert all(x["compound_net_event_return"] is None for x in result["strategies"].values())


def test_duplicate_trade_cannot_double_event_count():
    events = market_events()
    with pytest.raises(ValueError, match="Duplicate"):
        model.market_summary(pd.concat([events, events.iloc[[0]]]))


def test_strict_json_serializes_missing_outcomes_as_null():
    value = model.clean({"when": pd.NaT, "error": np.nan, "years": np.array([2020, 2021]),
                         "stamp": pd.Timestamp("2023-08-15T12:00:00Z")})
    text = json.dumps(value, allow_nan=False)
    assert json.loads(text)["error"] is None
    assert value["years"] == [2020, 2021]


def test_unknown_or_invalid_issue_abstains_without_stale_carryforward():
    panel = pd.DataFrame({"forecast_at": pd.to_datetime(["2023-09-15T12:00:00Z"])})
    result = model.forecast_as_of(panel, "2026-09-15")
    assert result["status"] == "abstain" and result["models"] == {}
    with pytest.raises(ValueError, match="valid forecast"):
        model.forecast_as_of(panel, pd.NaT)


def test_satellite_snapshot_tampering_fails_before_forecasting(tmp_path):
    (tmp_path / "august.csv").write_text("year,forecast_at\n2023,2023-08-15\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"files": [{"path": "august.csv", "sha256": "incorrect"}]}))
    with pytest.raises(ValueError, match="hash mismatch"):
        model.load_proxies(tmp_path)
