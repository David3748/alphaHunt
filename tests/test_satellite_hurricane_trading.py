from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from satellite_hurricane_trading import event_return, make_events


def fixture():
    forecasts=pd.DataFrame([dict(year=2006,eligible=True,forecast_at='2006-08-01',satellite=120.,baseline=80.,climatology=100.,actual_ace=999.)])
    dates=pd.bdate_range('2006-07-01','2006-12-31')
    prices=pd.DataFrame({'KIE':np.linspace(100,130,len(dates)),'SPY':np.linspace(100,110,len(dates))},index=dates)
    return forecasts,prices


def test_two_leg_sizing_and_short_cost_on_both_directions():
    a=event_return(1,.2,.1,100); b=event_return(-1,.2,.1,100)
    assert a['gross_return']==pytest.approx(.05)
    assert b['gross_return']==pytest.approx(-.05)
    assert a['execution_cost']==b['execution_cost']==.002
    assert a['borrow_cost']==b['borrow_cost']==pytest.approx(.015*100/365)
    assert event_return(0,.2,.1,100)['net_return']==0


def test_forecast_sign_and_actual_outcome_invariance():
    forecasts,prices=fixture();events=make_events(forecasts,prices).set_index('strategy')
    assert events.loc['satellite','direction']==-1
    assert events.loc['baseline','direction']==1
    assert events.loc['incremental_overlay','direction']==-1
    forecasts['actual_ace']=-10000
    pd.testing.assert_frame_equal(events,make_events(forecasts,prices).set_index('strategy'))


def test_prices_outside_fixed_event_window_do_not_change_returns():
    forecasts,prices=fixture(); expected=make_events(forecasts,prices)
    altered=prices.copy(); altered.loc[altered.index<'2006-08-01']*=10
    altered.loc[altered.index>'2006-11-30']*=.01
    pd.testing.assert_frame_equal(expected,make_events(forecasts,altered))
    assert expected.entry.min()==pd.Timestamp('2006-08-01')
    assert expected.exit.max()==pd.Timestamp('2006-11-30')


def test_reject_late_forecast_and_incomplete_window():
    forecasts,prices=fixture();forecasts['forecast_at']='2006-08-02'
    with pytest.raises(ValueError,match='issue'):make_events(forecasts,prices)
    forecasts['forecast_at']='2006-08-01'
    with pytest.raises(ValueError,match='Incomplete'):make_events(forecasts,prices.loc['2006-10-01':])


def test_doubled_execution_cost_does_not_double_borrow():
    single=event_return(1,.2,.1,100);double=event_return(1,.2,.1,100,2)
    assert double['borrow_cost']==single['borrow_cost']
    assert double['net_return']==pytest.approx(single['net_return']-.002)


def test_missing_asset_boundary_quote_fails_instead_of_shifting_entry():
    forecasts,prices=fixture(); prices.loc['2006-08-01','KIE']=np.nan
    with pytest.raises(ValueError,match='Missing quote'):make_events(forecasts,prices)
