import unittest

import numpy as np
import pandas as pd

from src.ers_futures_carry import carry_from_rows, contract_returns_from_rows


class ErsFuturesCarryTests(unittest.TestCase):
    def test_uses_first_two_contracts_outside_expiry_buffer(self):
        rows = pd.DataFrame({
            "commodity": ["Corn"] * 3,
            "item": ["Futures price daily"] * 3,
            "futures_exchange": ["CBOT"] * 3,
            "data_source_date": ["2020-06-25"] * 3,
            "futures_contract": ["2020-07", "2020-09", "2020-12"],
            "value": [4.00, 3.80, 3.70],
        })
        carry, detail = carry_from_rows(rows, min_days_to_expiry=28)
        # July is too close; September and December form the spread.
        self.assertEqual(detail.iloc[0]["front_contract"], "2020-09")
        self.assertEqual(detail.iloc[0]["deferred_contract"], "2020-12")
        expected = np.log(3.80 / 3.70) * 365.25 / 91
        self.assertAlmostEqual(detail.iloc[0]["annualized_carry"], expected)
        self.assertAlmostEqual(carry["corn"].dropna().iloc[0], expected)

    def test_backwardation_is_positive(self):
        rows = pd.DataFrame({
            "commodity": ["Soybeans", "Soybeans"],
            "item": ["Futures price daily"] * 2,
            "futures_exchange": ["CBOT"] * 2,
            "data_source_date": ["2020-01-02"] * 2,
            "futures_contract": ["2020-03", "2020-05"],
            "value": [10.0, 9.5],
        })
        carry, _ = carry_from_rows(rows)
        self.assertGreater(carry["soy"].dropna().iloc[0], 0)

    def test_return_holds_same_contract_between_observations(self):
        rows = pd.DataFrame({
            "commodity": ["Corn"] * 4,
            "item": ["Futures price daily"] * 4,
            "futures_exchange": ["CBOT"] * 4,
            "data_source_date": ["2020-01-02", "2020-01-02", "2020-01-09", "2020-01-09"],
            "futures_contract": ["2020-03", "2020-05", "2020-03", "2020-05"],
            "value": [4.00, 4.30, 4.04, 4.50],
        })
        _, pairs = carry_from_rows(rows)
        returns, _ = contract_returns_from_rows(rows, pairs)
        # P&L is March-to-March, not the apparent jump to the May price.
        self.assertAlmostEqual(returns["corn"].dropna().iloc[0], 4.04 / 4.00 - 1)


if __name__ == "__main__":
    unittest.main()
