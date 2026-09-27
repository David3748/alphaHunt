import numpy as np
import pandas as pd
import pytest

from src import satellite_cybench_trading as m


def inputs():
    stats = pd.DataFrame([{"adm_id": c, "harvest_year": y, "yield": 5., "harvest_area": a}
                          for c, a in [("a", 80.), ("b", 20.)] for y in range(2000, 2024)])
    pred = pd.DataFrame([{"adm_id": "a", "year": 2013, "eligible": True,
                          "forecast_at": "2013-08-15 12:00:00", "weather": 4., "satellite": 6., "trend": 5.},
                         {"adm_id": "b", "year": 2013, "eligible": True,
                          "forecast_at": "2013-08-15 12:00:00", "weather": 9., "satellite": 1., "trend": 5.}])
    return pred, stats


def test_aggregation_uses_prior_area_same_support_and_not_current_targets():
    pred, stats = inputs()
    a, weights = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    assert a.iloc[0].weather == pytest.approx(5.)
    assert a.iloc[0].satellite == pytest.approx(5.)
    assert a.iloc[0].prior_area_coverage == 1.
    assert weights.weight_harvest_year.eq(2012).all()
    stats.loc[stats.harvest_year.ge(2013), ["yield", "harvest_area"]] = [1e9, 1e9]
    pred["actual"] = np.nan
    b, _ = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    pd.testing.assert_frame_equal(a, b)


def test_missing_county_forecast_stays_in_denominator_and_threshold_is_inclusive():
    pred, stats = inputs()
    pred = pred[pred.adm_id.eq("a")]
    a, _ = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    assert a.iloc[0].eligible
    assert a.iloc[0].prior_area_coverage == .8
    stats.loc[stats.adm_id.eq("a") & stats.harvest_year.eq(2012), "harvest_area"] = 79
    b, _ = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    assert not b.iloc[0].eligible
    assert b.iloc[0].weather != b.iloc[0].weather


def test_missing_prior_area_is_not_filled_from_future_or_older_area():
    pred, stats = inputs()
    stats.loc[stats.adm_id.eq("b") & stats.harvest_year.eq(2012), "harvest_area"] = np.nan
    a, weights = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    assert a.iloc[0].missing_prior_area_counties == 1
    assert a.iloc[0].total_reported_prior_area_ha == 80
    assert weights.adm_id.tolist() == ["a"]


def test_insufficient_prior_history_cannot_expand_universe_using_future_labels():
    pred, stats = inputs()
    stats = stats[~(stats.adm_id.eq("b") & stats.harvest_year.lt(2006))]
    a, weights = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013])
    assert a.iloc[0].history_counties == 1
    assert weights.adm_id.tolist() == ["a"]


def market_inputs():
    f = pd.DataFrame([dict(year=2013, forecast_at=pd.Timestamp("2013-08-15 12:00"), eligible=True,
                          reason="", weather=4., satellite=6., trend=5., prior_area_coverage=.9)])
    sessions = pd.bdate_range("2013-08-01", "2013-10-31")
    prices = pd.Series(100 + np.arange(len(sessions)), index=sessions)
    return f, prices, sessions


def test_entry_strictly_after_august15_and_cost_borrow_semantics():
    f, prices, sessions = market_inputs()
    events, skipped = m.make_events(f, prices, sessions)
    assert skipped.empty
    assert events.entry.eq(pd.Timestamp("2013-08-16")).all()
    assert events.exit.eq(pd.Timestamp("2013-10-31")).all()
    sat = events.set_index("strategy").loc["satellite"]
    weather = events.set_index("strategy").loc["weather"]
    cash = events.set_index("strategy").loc["cash"]
    assert sat.direction == -1 and weather.direction == 1
    assert sat.borrow_cost == pytest.approx(.03 * 76 / 365)
    assert sat.execution_cost == .005
    assert weather.borrow_cost == 0 and cash.net_return == 0


def test_exact_zero_signal_cash_and_doubled_execution_cost_only():
    f, prices, sessions = market_inputs()
    f["weather"] = 5.
    a, _ = m.make_events(f, prices, sessions)
    b, _ = m.make_events(f, prices, sessions, 2)
    a, b = a.set_index("strategy"), b.set_index("strategy")
    assert a.loc["weather", "direction"] == 0 and a.loc["weather", "net_return"] == 0
    assert b.loc["satellite", "net_return"] - a.loc["satellite", "net_return"] == pytest.approx(-.005)
    assert b.loc["satellite", "borrow_cost"] == a.loc["satellite", "borrow_cost"]


def test_missing_boundary_or_interior_quote_abstains():
    f, prices, sessions = market_inputs()
    for date in ["2013-08-16", "2013-09-11", "2013-10-31"]:
        events, skipped = m.make_events(f, prices.drop(pd.Timestamp(date)), sessions)
        assert events.empty and len(skipped) == 1


def test_shared_calendar_blocks_preserve_strategy_dependence_and_gaps():
    wide = pd.DataFrame({"x": [.1, .2, .3], "y": [.15, .25, .35]}, index=[2013, 2015, 2023])
    sample = m.block_samples(wide, draws=100)
    valid = np.isfinite(sample).all(axis=1)
    assert np.allclose(sample[valid, 1] - sample[valid, 0], .05)


def test_no_events_is_explicit_failure_and_not_fake_profit():
    result = m.summarize(pd.DataFrame())
    assert result["n_events"] == 0 and result["strategies"] == {}
    assert not result["economic_diagnostic_gate_passed"]
    assert not result["trading_alpha_verified"]


def test_future_prices_do_not_change_existing_event():
    f, prices, sessions = market_inputs()
    a, _ = m.make_events(f, prices, sessions)
    prices.loc[pd.Timestamp("2013-11-01")] = 1e9
    b, _ = m.make_events(f, prices, sessions)
    pd.testing.assert_frame_equal(a, b)


def test_declared_secondary_horizon_uses_september15_and_same_exit():
    f, prices, sessions = market_inputs()
    f["forecast_at"] = pd.Timestamp("2013-09-15 12:00")
    events, skipped = m.make_events(f, prices, sessions, issue_month=9)
    assert skipped.empty
    assert events.entry.eq(pd.Timestamp("2013-09-16")).all()
    assert events.exit.eq(pd.Timestamp("2013-10-31")).all()
    with pytest.raises(ValueError, match="two frozen horizons"):
        m.make_events(f, prices, sessions, issue_month=10)


def test_secondary_aggregation_preserves_prior_year_area_rule():
    pred, stats = inputs()
    pred["forecast_at"] = "2013-09-15 12:00:00"
    a, weights = m.aggregate_forecasts(pred, stats, ["a", "b"], [2013], issue_month=9)
    assert a.iloc[0].forecast_at == pd.Timestamp("2013-09-15 12:00")
    assert weights.weight_harvest_year.eq(2012).all()


def test_secondary_cannot_overwrite_primary_output_directory(tmp_path):
    (tmp_path / "protocol.json").write_text("{}")
    with pytest.raises(ValueError, match="protocol and requested horizon differ"):
        m.run(tmp_path, issue_month=9)
