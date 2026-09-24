import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import century_hypotheses as ch


class CenturyHypothesesTest(unittest.TestCase):
    def row(self, index, year=2010):
        result = {
            "probability_plus20_excess_90d_pct": 10 + index,
            "expected_excess_return_90d_pct": index,
            "probability_positive_excess_90d_pct": 40 + index,
            "downside_tail_probability_pct": 30 - index,
        }
        return {"case_id": f"c{index}", "cutoff": f"{year}-01-{index + 1:02d}",
                "accepted": f"{year}-01-{index + 1:02d} 12:00:00.0",
                "syntheses": [result, result],
                "market_at_cutoff": {"drawdown_from_1y_high": -.4 - index / 100}}

    def test_locked_scores_have_expected_directions(self):
        rows = ch.add_locked_scores([self.row(i) for i in range(10)])
        self.assertLess(rows[0]["locked_scores"]["p_plus20"], rows[-1]["locked_scores"]["p_plus20"])
        self.assertLess(rows[0]["locked_scores"]["safety"], rows[-1]["locked_scores"]["safety"])
        self.assertGreater(rows[-1]["locked_scores"]["upside_x_drawdown"], 0)
        self.assertGreater(rows[-1]["locked_scores"]["causal_blend"], 0)

    def test_discovery_period_is_separate_from_holdouts(self):
        rows = [self.row(0, year) for year in (2010, 2019, 2022)]
        self.assertEqual(len(ch.cohort_rows(rows, 2009, 2018)), 1)
        self.assertEqual(len(ch.cohort_rows(rows, 2019, 2020)), 1)
        self.assertEqual(len(ch.cohort_rows(rows, 2021, 2025)), 1)


if __name__ == "__main__":
    unittest.main()
