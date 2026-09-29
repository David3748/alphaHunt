import json
import shutil

import numpy as np
import pandas as pd
import pytest

from src import corn_market_data as m


def sample_market(end='2024-12-31T23:00:00Z'):
    dates = pd.bdate_range('2024-11-01', '2024-12-31')
    dates = dates.difference(pd.DatetimeIndex(['2024-11-28', '2024-12-25']))
    schedule = pd.DataFrame(index=dates)
    schedule['open_at'] = (dates + pd.Timedelta(hours=9, minutes=30)).tz_localize(m.NY).tz_convert('UTC')
    schedule['close_at'] = (dates + pd.Timedelta(hours=16)).tz_localize(m.NY).tz_convert('UTC')
    schedule.loc['2024-11-29', 'close_at'] = pd.Timestamp('2024-11-29T18:00:00Z')
    prices = pd.Series(100. + np.arange(len(dates)), index=dates)
    prices = prices.loc[schedule.close_at.le(pd.Timestamp(end)).to_numpy()]
    return m.MarketData(prices, schedule, pd.Timestamp(end), {'symbol': 'CORN'})


def test_strict_next_new_york_date_even_preopen_with_twenty_subsequent_sessions():
    data = sample_market()
    for issue in ['2024-11-05T01:00:00Z', '2024-11-04T12:00:00Z', '2024-11-04T22:00:00Z']:
        trade = m.paper_trade(data, issue, 1)
        assert trade['status'] == 'executed'
        assert trade['entry'] == '2024-11-05'
        assert trade['exit'] == '2024-12-04'
        assert trade['holding_sessions'] == 20
        assert pd.Timestamp(trade['entry_at']) > pd.Timestamp(issue)


def test_holiday_weekend_and_early_close_follow_calendar():
    trade = m.paper_trade(sample_market(), '2024-11-27T12:00:00Z', 1, exit_on='2024-12-01')
    assert trade['status'] == 'unavailable'  # Fri entry is also last session before Sunday.
    trade = m.paper_trade(sample_market(), '2024-11-26T12:00:00Z', 1, exit_on='2024-12-01')
    assert trade['entry'] == '2024-11-27'
    assert trade['exit'] == '2024-11-29'
    assert trade['exit_at'] == '2024-11-29T18:00:00+00:00'


def test_costs_are_initial_notional_round_trip_and_calendar_day_borrow():
    data = sample_market()
    buy = m.paper_trade(data, '2024-11-04T12:00:00Z', 1)
    short = m.paper_trade(data, '2024-11-04T12:00:00Z', -1)
    assert buy['execution_cost'] == .005 and buy['borrow_cost'] == 0
    assert buy['net_return'] == pytest.approx(buy['asset_return'] - .005)
    assert short['borrow_cost'] == pytest.approx(.03 * 29 / 365)
    assert short['net_return'] == pytest.approx(-buy['asset_return'] - .005 - .03 * 29 / 365)
    double = m.paper_trade(data, '2024-11-04T12:00:00Z', -1, entry_cost_bps=50, exit_cost_bps=50)
    assert double['net_return'] == pytest.approx(short['net_return'] - .005)
    assert double['borrow_cost'] == short['borrow_cost']


def test_cash_has_no_costs_and_shares_common_valid_window():
    data = sample_market()
    cash = m.paper_trade(data, '2024-11-04T12:00:00Z', 0)
    assert cash['status'] == 'cash' and cash['net_return'] == 0
    assert cash['execution_cost'] == cash['borrow_cost'] == cash['notional_fraction'] == 0
    data.prices = data.prices.drop(pd.Timestamp('2024-11-20'))
    assert m.paper_trade(data, '2024-11-04T12:00:00Z', 0)['status'] == 'unavailable'


@pytest.mark.parametrize('missing', ['2024-11-05', '2024-11-20', '2024-12-04'])
def test_missing_entry_interior_or_exit_never_shifts_execution(missing):
    data = sample_market()
    data.prices = data.prices.drop(pd.Timestamp(missing))
    trade = m.paper_trade(data, '2024-11-04T12:00:00Z', 1)
    assert trade['status'] == 'unavailable'
    assert trade['entry'] == '2024-11-05' and trade['exit'] == '2024-12-04'
    assert trade['net_return'] is None


def test_unfinished_window_does_not_use_last_known_quote():
    data = sample_market('2024-11-20T21:01:00Z')
    trade = m.paper_trade(data, '2024-11-04T12:00:00Z', 1)
    assert trade['status'] == 'unavailable' and trade['net_return'] is None
    assert 'not completed' in trade['reason']


def test_later_prices_cannot_change_earlier_event():
    data = sample_market()
    before = m.paper_trade(data, '2024-11-04T12:00:00Z', -1, holding_sessions=5)
    data.prices.loc['2024-12-01':] *= 1000
    after = m.paper_trade(data, '2024-11-04T12:00:00Z', -1, holding_sessions=5)
    assert before == after


def test_bad_inputs_and_naive_issue_are_rejected():
    data = sample_market()
    for args in [('2024-11-04', 1), ('2024-11-04T12:00Z', 2), ('2024-11-04T12:00Z', True)]:
        with pytest.raises(ValueError):
            m.paper_trade(data, *args)
    for kwargs in [dict(holding_sessions=0), dict(holding_sessions=2.5), dict(entry_cost_bps=-1),
                   dict(short_borrow_rate=np.nan), dict(exit_on='2024-12-04T15:00:00')]:
        with pytest.raises(ValueError):
            m.paper_trade(data, '2024-11-04T12:00Z', 1, **kwargs)


def test_calendar_coverage_bounds_cannot_guess_next_session():
    data = sample_market()
    for issue in ['2024-10-31T12:00Z', '2024-12-31T12:00Z']:
        assert m.paper_trade(data, issue, 1)['status'] == 'unavailable'
    assert m.paper_trade(data, '2024-12-30T12:00Z', 1)['status'] == 'unavailable'
    assert m.paper_trade(data, '2024-11-04T12:00Z', 1, exit_on='2025-01-01')['status'] == 'unavailable'


def test_invalid_price_calendar_or_future_completed_price_rejected():
    data = sample_market()
    for values in [data.prices * np.nan, data.prices * -1, pd.concat([data.prices, data.prices.iloc[:1]])]:
        with pytest.raises(ValueError):
            m.MarketData(values, data.schedule, data.as_of, {})
    with pytest.raises(ValueError, match='before its session'):
        m.MarketData(data.prices, data.schedule, pd.Timestamp('2024-11-05T19:00Z'), {})
    bad = data.schedule.copy()
    bad.loc['2024-11-05', 'close_at'] = pd.Timestamp('2024-11-06T21:00Z')
    with pytest.raises(ValueError, match='dates disagree'):
        m.MarketData(data.prices, bad, data.as_of, {})


def test_parser_discards_in_progress_bar_and_validates_instrument():
    data = sample_market()
    dates = data.sessions[:3]
    payload = {'chart': {'result': [{'meta': {'symbol': 'CORN', 'currency': 'USD'},
        'timestamp': [int(data.schedule.loc[d, 'open_at'].timestamp()) for d in dates],
        'indicators': {'adjclose': [{'adjclose': [100., 101., 102.]}]}}], 'error': None}}
    prices = m.parse_yahoo(payload, data.schedule, '2024-11-05T18:00Z')
    assert prices.index.tolist() == list(dates[:2])
    payload['chart']['result'][0]['meta']['symbol'] = 'ZC=F'
    with pytest.raises(ValueError, match='instrument'):
        m.parse_yahoo(payload, data.schedule, data.as_of)


def test_bankruptcy_is_explicit_not_clipped_or_compoundable():
    data = sample_market()
    data.prices.loc['2024-12-04'] = 1000
    trade = m.paper_trade(data, '2024-11-04T12:00Z', -1)
    assert trade['status'] == 'bankrupt' and trade['bankrupt']
    assert trade['net_return'] < -1


def test_offline_snapshot_raw_reconstruction_and_hash_tamper(tmp_path):
    data = m.load_market()
    assert m.market_status(data)['first_price_date'] == '2010-06-09'
    assert m.paper_trade(data, '2013-08-15T12:00Z', 1)['status'] == 'executed'
    for path in m.DEFAULT_MARKET_DIR.iterdir():
        if path.is_file():
            shutil.copyfile(path, tmp_path / path.name)
    target = tmp_path / 'adjusted_prices.csv'
    target.write_text(target.read_text() + '\n')
    with pytest.raises(ValueError, match='checksum'):
        m.load_market(tmp_path)
