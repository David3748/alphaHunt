import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.slow_softs_alpha_backtest import (
    SOFTS, blend, build_cot_panel, positioning_signal, strategy_returns,
)


CONFIG = json.loads(Path("config/slow_softs_alpha.json").read_text())


def test_fixed_weight_blend_can_cancel_to_cash():
    index = pd.date_range("2020-01-31", periods=2, freq="ME")
    first = pd.DataFrame([[0.5, -0.5, 0, 0]] * 2, index=index, columns=SOFTS)
    second = -first
    assert blend((0.5, first), (0.5, second)).abs().sum().sum() == 0


def test_positions_execute_one_month_later_and_cost_turnover():
    index = pd.date_range("2020-01-31", periods=3, freq="ME")
    returns = pd.DataFrame(0.0, index=index, columns=SOFTS)
    targets = pd.DataFrame(0.0, index=index, columns=SOFTS)
    targets.loc[index[0], ["coffee", "sugar"]] = [0.5, -0.5]
    net, turnover, held = strategy_returns(returns, targets, 10)
    assert held.loc[index[0]].abs().sum() == 0
    assert held.loc[index[1], "coffee"] == 0.5
    assert np.isclose(net.loc[index[1]], -0.001)
    assert turnover.loc[index[1]] == 1.0


def test_cot_parser_uses_configured_contract_and_commercial_share():
    rows = pd.DataFrame({
        "Market and Exchange Names": ["COFFEE C - ICE FUTURES U.S.", "RANDOM - EXCHANGE"],
        "As of Date in Form YYYY-MM-DD": ["2024-01-02", "2024-01-02"],
        "Open Interest (All)": [100, 100],
        "Commercial Positions-Long (All)": [30, 10],
        "Commercial Positions-Short (All)": [50, 10],
    })
    panel = build_cot_panel(rows, CONFIG)
    assert np.isclose(panel.loc[pd.Timestamp("2024-01-02"), "coffee"], -0.2)
    assert panel["sugar"].isna().all()


def test_positioning_observes_publication_lag():
    dates = pd.date_range("2020-01-07", periods=180, freq="W-TUE")
    cot = pd.DataFrame({soft: np.linspace(-1, 1, len(dates)) for soft in SOFTS}, index=dates)
    monthly = pd.date_range("2020-01-31", "2023-12-31", freq="ME")
    signal = positioning_signal(cot, monthly, CONFIG)
    assert signal.index.equals(monthly)
    assert np.allclose(signal.sum(axis=1), 0, atol=1e-12)


def test_weather_reversion_can_cancel_positioning_without_relevering():
    index = pd.date_range("2020-01-31", periods=2, freq="ME")
    positioning = pd.DataFrame([[.5, -.5, 0, 0]] * 2, index=index, columns=SOFTS)
    weather_reversion = -positioning
    combined = blend((.75, positioning), (.25, weather_reversion))
    assert np.allclose(combined.abs().sum(axis=1), .5)
