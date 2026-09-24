import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import strategy_experiments as se


class StrategyExperimentsTest(unittest.TestCase):
    def test_dilution_split_is_chronological(self):
        frame = pd.DataFrame([
            {"case_id": str(i), "cohort": "dilution", "cutoff": f"2024-01-{i + 1:02d}",
             "legacy_split": None} for i in range(10)
        ])
        result = se.assign_splits(frame)
        self.assertEqual((result.evaluation_split == "development").sum(), 7)
        self.assertEqual((result.evaluation_split == "validation").sum(), 3)
        self.assertEqual(result.iloc[6].evaluation_split, "development")
        self.assertEqual(result.iloc[7].evaluation_split, "validation")

    def test_top_decile_uses_highest_score(self):
        frame = pd.DataFrame([
            {"case_id": str(i), "ticker": str(i), "score": i,
             "relative_return": i / 10, "success": int(i >= 8)} for i in range(10)
        ])
        result = se.evaluate_partition(frame, "score")
        self.assertEqual(result["basket_n"], 1)
        self.assertEqual(result["tickers"], ["9"])
        self.assertAlmostEqual(result["basket_mean_excess_return"], 0.9)

    def test_missing_scores_are_ineligible_not_ranked_last(self):
        frame = pd.DataFrame([
            {"case_id": "a", "ticker": "A", "score": None,
             "relative_return": 1.0, "success": 1},
            {"case_id": "b", "ticker": "B", "score": 0.2,
             "relative_return": 0.1, "success": 0},
        ])
        result = se.evaluate_partition(frame, "score")
        self.assertEqual(result["eligible_n"], 1)
        self.assertEqual(result["tickers"], ["B"])


if __name__ == "__main__":
    unittest.main()
