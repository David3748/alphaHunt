import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import safety_backtest as sb


class SafetyBacktestTest(unittest.TestCase):
    def test_expanding_selector_never_uses_same_or_future_date(self):
        rows = [
            {"case_id": "a", "cutoff": "2020-01-01", "score": 0.1},
            {"case_id": "b", "cutoff": "2020-01-02", "score": 0.2},
            {"case_id": "c", "cutoff": "2020-01-02", "score": 1.0},
            {"case_id": "d", "cutoff": "2020-01-03", "score": 0.3},
        ]
        selected = sb.select_expanding(rows, fraction=0.5, warmup=1)
        self.assertEqual([row["case_id"] for row in selected], ["b", "c", "d"])
        self.assertEqual(selected[0]["prior_score_count"], 1)
        self.assertEqual(selected[1]["prior_score_count"], 1)

    def test_prepare_trades_enters_strictly_after_signal(self):
        selected = [{"case_id": "a", "ticker": "A", "cutoff": "2020-01-01"}]
        prices = {"A": [{"date": "2020-01-01", "close": 10},
                         {"date": "2020-01-02", "close": 11},
                         {"date": "2020-04-01", "close": 12}]}
        trade = sb.prepare_trades(selected, prices, holding_days=90)[0]
        self.assertEqual(trade["entry_date"], "2020-01-02")
        self.assertEqual(trade["exit_date"], "2020-04-01")

    def test_costs_reduce_flat_trade(self):
        trades = [{"case_id": "a", "ticker": "A", "entry_date": "2020-01-02",
                   "entry_price": 10, "exit_date": "2020-01-03", "exit_price": 10}]
        prices = {"A": [{"date": "2020-01-02", "close": 10},
                         {"date": "2020-01-03", "close": 10}],
                  "SPY": [{"date": "2020-01-02", "close": 100},
                          {"date": "2020-01-03", "close": 100}]}
        _, executed, _ = sb.simulate(trades, prices, max_positions=1, cost_bps_per_side=10)
        self.assertAlmostEqual(executed[0]["net_sleeve_return"], -0.001999, places=6)


if __name__ == "__main__":
    unittest.main()
