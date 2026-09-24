"""Offline tests for the false-distress long lab."""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import long_lab as lab


class TestMarketOutcome(unittest.TestCase):
    def rows(self, start, closes):
        return [{"date": start + dt.timedelta(days=i), "close": close, "volume": 1_000_000}
                for i, close in enumerate(closes)]

    def test_success_requires_excess_return_and_drawdown_control(self):
        start = dt.date(2023, 1, 1)
        stock = self.rows(start, [100.0] * 370 + [50.0] + [50.0 + i * 0.25 for i in range(1, 192)])
        spy = self.rows(start, [100.0] * len(stock))
        result = lab.market_case(stock, spy, start + dt.timedelta(days=370))
        self.assertLessEqual(result["drawdown_from_1y_high"], -0.4)
        self.assertTrue(result["long_success"])

    def test_deep_drawdown_fails_even_if_recovered(self):
        start = dt.date(2023, 1, 1)
        # The filing-day close is 50, entry is the following 50 close, then the
        # stock drops to 30 before recovering.
        future = [50.0, 50.0, 30.0] + [30.0 + i * 0.5 for i in range(1, 190)]
        stock = self.rows(start, [100.0] * 370 + future)
        spy = self.rows(start, [100.0] * len(stock))
        result = lab.market_case(stock, spy, start + dt.timedelta(days=370))
        self.assertLessEqual(result["max_drawdown_from_entry_90d"], -0.25)
        self.assertFalse(result["long_success"])


class TestUniverse(unittest.TestCase):
    def test_primary_universe_excludes_funds_and_secondary_securities(self):
        rows = [
            {"ticker": "NAVI", "name": "NAVIENT CORP", "cik": "1"},
            {"ticker": "JSM", "name": "NAVIENT CORP", "cik": "1"},
            {"ticker": "COINX", "name": "Coin Example ETF", "cik": "2"},
            {"ticker": "ABC-P", "name": "ABC Inc", "cik": "3"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.json"
            path.write_text(json.dumps(rows))
            result = lab.primary_universe(path)
        self.assertEqual([r["ticker"] for r in result], ["NAVI"])


if __name__ == "__main__":
    unittest.main()
