import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from satellite_reit_backtest import blend, normalize_gross, strategy_returns


def test_normalize_gross_is_neutral_and_unit_gross():
    frame = pd.DataFrame([[1.0, 2.0, 4.0], [5.0, 5.0, 5.0]])
    result = normalize_gross(frame)
    assert np.isclose(result.iloc[0].sum(), 0.0)
    assert np.isclose(result.iloc[0].abs().sum(), 1.0)
    assert np.isclose(result.iloc[1].abs().sum(), 0.0)


def test_positions_are_lagged_and_costed():
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    returns = pd.DataFrame({"a": [0.10, 0.10, 0.10], "b": [0.0, 0.0, 0.0]}, index=dates)
    target = pd.DataFrame({"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 1.0]}, index=dates)
    net, turnover, positions = strategy_returns(returns, target, 10)
    assert positions.loc[dates[0]].abs().sum() == 0
    assert positions.loc[dates[1], "a"] == 1
    assert np.isclose(net.loc[dates[1]], 0.10 - 0.001)
    assert turnover.loc[dates[2]] == 2.0


def test_blend_preserves_cancellation_as_cash():
    frame = pd.DataFrame([[0.5, -0.5]])
    result = blend((0.5, frame), (0.5, -frame))
    assert result.abs().sum().sum() == 0.0


def test_core_universe_uses_diversified_funds_only():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config" / "satellite_reit_alpha.json").read_text())
    kinds = [config["instruments"][ticker]["kind"] for ticker in config["core_universe"]]
    assert all("ETF" in kind or "fund" in kind for kind in kinds)
