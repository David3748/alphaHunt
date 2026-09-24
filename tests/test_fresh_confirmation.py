import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fresh_confirmation as fc


class FreshConfirmationTest(unittest.TestCase):
    def test_evaluate_selects_locked_top_decile(self):
        rows = [{"case_id": str(i), "ticker": str(i), "score": i,
                 "relative_return": i / 10, "success": int(i >= 8)}
                for i in range(10)]
        result = fc.evaluate(rows, "score")
        self.assertEqual(result["basket_n"], 1)
        self.assertEqual(result["tickers"], ["9"])
        self.assertAlmostEqual(result["basket_mean_excess_return"], 0.9)

    def test_missing_scores_are_not_selected(self):
        rows = [
            {"case_id": "a", "ticker": "A", "score": None,
             "relative_return": 1.0, "success": 1},
            {"case_id": "b", "ticker": "B", "score": 0.2,
             "relative_return": 0.1, "success": 0},
        ]
        result = fc.evaluate(rows, "score")
        self.assertEqual(result["eligible_n"], 1)
        self.assertEqual(result["tickers"], ["B"])


if __name__ == "__main__":
    unittest.main()
