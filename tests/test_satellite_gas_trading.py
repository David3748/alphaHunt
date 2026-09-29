from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_gas_trading import event_return, make_events


def forecast(date="2020-01-01", issued="2020-02-15"):
    return {"date": date, "forecast_at": issued, "baseline": 20., "satellite": 21.,
            "ground_hdd": 22., "ground_hdd_satellite": 21.5}


def prices():
    dates = pd.bdate_range("2020-02-10", "2020-05-01")
    return pd.Series(np.arange(len(dates)) + 100., index=dates)


def test_strict_post_issue_entry_and_monthly_exit():
    events, skipped = make_events(pd.DataFrame([forecast()]), prices())
    assert skipped.empty
    assert events.entry.eq(pd.Timestamp("2020-02-17")).all()
    assert events.exit.eq(pd.Timestamp("2020-03-16")).all()
    actual = prices().loc["2020-03-16"] / prices().loc["2020-02-17"] - 1
    assert events.asset_return.iloc[0] == pytest.approx(actual)
    assert events.set_index("strategy").loc["satellite_overlay", "direction"] == -1


def test_entry_day_price_gain_is_not_earned():
    p = prices()
    p.loc[p.index >= "2020-02-17"] *= 2
    events, _ = make_events(pd.DataFrame([forecast()]), p)
    ordinary, _ = make_events(pd.DataFrame([forecast()]), prices())
    np.testing.assert_allclose(events.asset_return, ordinary.asset_return)


def test_costs_are_two_sided_with_borrow_only_for_shorts():
    long = event_return(1, .10, 30)
    short = event_return(-1, -.10, 30)
    assert long["net_return"] == pytest.approx(.095)
    assert short["net_return"] == pytest.approx(.095 - .03 * 30 / 365)
    assert event_return(0, .10, 30)["net_return"] == 0


def test_adjacent_events_do_not_double_count_boundaries():
    events, _ = make_events(pd.DataFrame([forecast(), forecast("2020-02-01", "2020-03-15")]), prices())
    group = events[events.strategy == "satellite"]
    assert group.exit.iloc[0] == group.entry.iloc[1]
    assert len(group) == 2


def test_overlapping_forecast_windows_raise():
    with pytest.raises(ValueError, match="Overlapping"):
        make_events(pd.DataFrame([forecast(), forecast("2020-02-01", "2020-03-10")]), prices())


def test_unfinished_price_window_abstains_on_same_sample():
    frame = pd.DataFrame([forecast(), forecast("2020-03-01", "2020-04-15")])
    events, skipped = make_events(frame, prices())
    assert len(skipped) == 1
    assert skipped.reason.iloc[0] == "incomplete_price_window"
    assert events.groupby("strategy").size().eq(1).all()


def test_monthly_inputs_must_be_complete_before_issue():
    with pytest.raises(ValueError, match="precedes"):
        make_events(pd.DataFrame([forecast(issued="2020-01-20")]), prices())
