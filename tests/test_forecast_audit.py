"""Offline tests for the leakage/calibration audit, redaction audit, the Haiku probes and the ticker audit."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import forecast_audit as fa
import memorization_probe as mp
import identification_probe as ip
import recall_probe as rp
import redaction_audit as ra
import cutoff_probe as cp
import ticker_audit as ta


def synthetic(n_per_month=40, years=range(2010, 2015), seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for year in years:
        for month in range(1, 13):
            p = rng.uniform(0.05, 0.5, n_per_month)
            hit = rng.uniform(size=n_per_month) < p
            excess = np.where(hit, rng.uniform(0.2, 0.8, n_per_month), rng.uniform(-0.5, 0.19, n_per_month))
            for pi, ex in zip(p, excess):
                rows.append({"cutoff": f"{year}-{month:02d}-15", "cohort": "century", "p20": pi * 100,
                             "excess": ex})
    df = pd.DataFrame(rows)
    df["year"] = df.cutoff.str[:4].astype(int)
    df["month"] = df.cutoff.str[:7]
    df["hit"] = (df.excess >= fa.HIT).astype(int)
    df["p"] = df.p20 / 100
    return df


class TestCalibration(unittest.TestCase):
    def test_calibrated_forecasts_score_as_calibrated(self):
        df = synthetic()
        block = fa.calibration_block(df.p, df.hit, np.full(len(df), df.hit.mean()))
        self.assertGreater(block["brier_skill"], 0.05)
        self.assertLess(block["ece"], 0.03)
        self.assertAlmostEqual(block["calibration_slope"], 1.0, delta=0.15)

    def test_compressed_forecasts_have_slope_above_one(self):
        df = synthetic()
        squeezed = 0.15 + (df.p - 0.15) * 0.3  # same ranking, far too timid
        block = fa.calibration_block(squeezed, df.hit, np.full(len(df), df.hit.mean()))
        self.assertGreater(block["calibration_slope"], 2.0)

    def test_walk_forward_recalibration_ignores_current_and_future_years(self):
        df = synthetic()
        base = fa.walk_forward_recalibration(df).set_index(["cutoff", "p20"])
        future = df.copy()
        future.loc[future.year >= 2012, "hit"] = 1 - future.loc[future.year >= 2012, "hit"]
        moved = fa.walk_forward_recalibration(future).set_index(["cutoff", "p20"])
        mask = base.index.get_level_values(0).str[:4] == "2012"
        np.testing.assert_allclose(base.loc[mask, "p_recal"], moved.loc[mask, "p_recal"])
        self.assertTrue(set(base.year) <= set(range(2011, 2015)))  # first year has no history

    def test_reliability_bins_cover_all_cases(self):
        df = synthetic()
        rows = fa.reliability(df.p.values, df.hit.values)
        self.assertEqual(sum(r["n"] for r in rows), len(df))
        for r in rows:
            self.assertLessEqual(r["ci"][0], r["observed"])
            self.assertGreaterEqual(r["ci"][1], r["observed"])


class TestRanking(unittest.TestCase):
    def test_informative_signal_has_positive_monthly_ic(self):
        ics = fa.monthly_ics(synthetic())
        self.assertEqual(len(ics), 60)
        self.assertGreater(ics.mean(), 0.2)

    def test_small_months_are_skipped(self):
        self.assertEqual(len(fa.monthly_ics(synthetic(n_per_month=5))), 0)

    def test_auc_matches_definition(self):
        self.assertEqual(fa.auc(pd.Series([3, 2, 1, 0]), pd.Series([1, 1, 0, 0])), 1.0)
        self.assertEqual(fa.auc(pd.Series([1, 1, 1, 1]), pd.Series([1, 0, 1, 0])), 0.5)


class TestLiveVsBacktest(unittest.TestCase):
    def test_surprise_is_small_for_far_worse_live_trades(self):
        backtest = [0.2, 0.3, 0.1, 0.25, 0.4, -0.1] * 20
        self.assertLess(fa.surprise(backtest, [-0.3] * 10, draws=2000), 0.01)
        self.assertGreater(fa.surprise(backtest, [0.2] * 10, draws=2000), 0.2)

    def test_worst_run_uses_consecutive_trades(self):
        ledger = [{"entry_date": f"2020-01-{d:02d}", "ticker": "X", "excess_return": r}
                  for d, r in zip(range(1, 7), [0.1, -0.5, -0.4, 0.2, 0.3, 0.1])]
        run = fa.worst_run(ledger, 2)
        self.assertAlmostEqual(run["worst_mean"], -0.45)
        self.assertEqual((run["from"], run["to"]), ("2020-01-02", "2020-01-03"))


class TestRedactionAudit(unittest.TestCase):
    words = {"first", "solar", "all", "company", "drilling"}

    def test_short_name_survives_registrant_redaction(self):
        text = "[ISSUER] and its subsidiaries. Nabors Drilling expanded; Nabors' rigs idled."
        stats = ra.leak_stats(text, "NABORS INDUSTRIES LTD", "NBR", self.words)
        self.assertEqual(stats["distinctive_tokens"], "NABORS")
        self.assertEqual(stats["name_token_hits"], 2)

    def test_dictionary_words_and_tickers_are_not_counted(self):
        self.assertEqual(ra.distinctive_tokens("FIRST SOLAR INC", self.words), [])
        self.assertIsNone(ra.ticker_pattern("ALL", self.words))

    def test_strict_scrub_removes_name_tokens_and_ticker(self):
        out = ra.strict_scrub("Nabors Drilling (NBR) at www.nabors.com, EIN 98-0363970.",
                              "NABORS INDUSTRIES LTD", "NBR")
        self.assertNotIn("Nabors", out)
        self.assertNotIn("NBR", out)
        self.assertNotIn("98-0363970", out)


class TestMemorizationProbe(unittest.TestCase):
    def test_identification_by_ticker_or_name(self):
        truth = {"company": "ADVANCED MICRO DEVICES INC", "ticker": "AMD"}
        self.assertTrue(mp.identified({"ticker_guess": "amd"}, truth))
        self.assertTrue(mp.identified({"company_guess": "Advanced Micro Devices"}, truth))
        self.assertFalse(mp.identified({"company_guess": "Intel Corporation", "ticker_guess": "INTC"}, truth))

    def test_scoring_splits_cohorts(self):
        key = [{"item": "P1", "company": "Geron Corp", "ticker": "GERN", "cohort": "century", "excess": 0.6,
                "cutoff": "2014-03-01", "case_id": "a", "p20": 10.0},
               {"item": "P2", "company": "Knightscope", "ticker": "KSCP", "cohort": "live_2026", "excess": -0.5,
                "cutoff": "2026-03-01", "case_id": "b", "p20": 10.0}]
        answers = {"P1": {"company_guess": "Geron", "recall": "underperform", "p_beat20": 20},
                   "P2": {"company_guess": None, "recall": "no_memory", "p_beat20": 10}}
        result = mp.score(key, answers)
        self.assertEqual(result["century_2011_2024"]["identified"], 1)
        self.assertEqual(result["century_2011_2024"]["recall_direction_accuracy"], 0.0)
        self.assertEqual(result["live_2026_control"]["identified"], 0)



class TestRecallProbe(unittest.TestCase):
    def rows(self, n=20, perfect=True):
        out = []
        for i in range(n):
            winner = i % 2 == 0
            p = (80 if winner else 20) if perfect else 50
            out.append({"winner": winner, "p_outperform": p, "p_beat20": p,
                        "direction": "outperform" if (winner if perfect else True) else "underperform",
                        "memory": "specific" if perfect else "none", "recognize_company": perfect})
        return out

    def test_perfect_recall_scores_auc_one_and_significant(self):
        b = rp.block(self.rows())
        self.assertEqual(b["auc_p_outperform"], 1.0)
        self.assertEqual(b["direction_accuracy"], 1.0)
        self.assertLess(b["auc_p_value"], 0.01)
        self.assertEqual(b["by_memory"]["specific"]["n"], 20)

    def test_no_memory_scores_chance(self):
        b = rp.block(self.rows(perfect=False))
        self.assertEqual(b["auc_p_outperform"], 0.5)
        self.assertEqual(b["direction_accuracy"], 0.5)
        self.assertGreater(b["auc_p_value"], 0.3)

    def test_wilson_interval_brackets_rate(self):
        lo, hi = rp.wilson(30, 40)
        self.assertLess(lo, 0.75)
        self.assertGreater(hi, 0.75)


class TestIdentificationMatching(unittest.TestCase):
    words = {"american", "realty", "capital", "first", "solar", "energy", "fuels", "clean", "mining", "mines"}

    def test_same_company_under_common_variants(self):
        self.assertTrue(ip.name_match("Netflix", "NETFLIX INC", self.words))
        self.assertTrue(ip.name_match("Amarin Corporation", "AMARIN CORP PLC\\UK", self.words))
        self.assertTrue(ip.name_match("Coeur Mining", "COEUR D ALENE MINES CORP", self.words))
        self.assertTrue(ip.name_match("Clean Energy Fuels", "Clean Energy Fuels Corp.", self.words))

    def test_different_companies_sharing_generic_words_do_not_match(self):
        self.assertFalse(ip.name_match("American Realty Investors", "American Realty Capital Trust", self.words))
        self.assertFalse(ip.name_match("First Solar", "First Energy", self.words))
        self.assertFalse(ip.name_match(None, "NETFLIX INC", self.words))

    def test_ticker_match_ignores_share_class_suffix(self):
        self.assertTrue(ip.ticker_match("brk.b", "BRK"))
        self.assertFalse(ip.ticker_match("", "AMD"))


class TestCutoffProbe(unittest.TestCase):
    @staticmethod
    def arm(n, informative):
        """Rows with a Haiku score and a mechanical score; each either separates winners perfectly
        (AUC 1) or is constant (AUC 0.5)."""
        return [{"winner": i % 2 == 0,
                 "p_outperform": (60 if i % 2 == 0 else 40) if informative["haiku"] else 50,
                 "mech_model": (1.0 if i % 2 == 0 else -1.0) if informative["mech"] else 0.0} for i in range(n)]

    def test_a_drop_shared_with_the_mechanical_model_cancels(self):
        pre = self.arm(40, {"haiku": True, "mech": True})
        post = self.arm(40, {"haiku": False, "mech": False})
        self.assertAlmostEqual(cp.gap(pre, post, "p_outperform", draws=200)["point"], 0.5)
        d = cp.did(pre, post, "p_outperform", "mech_model", draws=200)
        self.assertAlmostEqual(d["point"], 0.0)
        self.assertEqual(d["ci"], [0.0, 0.0])

    def test_a_drop_only_the_llm_shows_is_attributed_to_it(self):
        pre = self.arm(40, {"haiku": True, "mech": False})
        post = self.arm(40, {"haiku": False, "mech": False})
        d = cp.did(pre, post, "p_outperform", "mech_model", draws=200)
        self.assertAlmostEqual(d["point"], 0.5)
        self.assertGreater(d["ci"][0], 0.0)


class TestTickerAudit(unittest.TestCase):
    def test_file_name_ticker_owned_by_another_filer_is_a_collision(self):
        rows = [{"cik": "1", "company": "GOGO INC.", "symbol": "GOGO", "symbol_source": "dei_fact",
                 "status": "cooldown_180d", "filed": "20260301"},
                {"cik": "2", "company": "GO GO BUYERS, INC.", "symbol": "GOGO", "symbol_source": "instance_stem",
                 "status": "eligible", "filed": "20260414"},
                {"cik": "3", "company": "TECOGEN INC.", "symbol": "TGEN", "symbol_source": "instance_stem",
                 "status": "eligible", "filed": "20260319"}]
        out = {r["company"]: r for r in ta.live_stem_collisions(rows)}
        self.assertTrue(out["GO GO BUYERS, INC."]["collision"])
        self.assertEqual(out["GO GO BUYERS, INC."]["priced_as"], ["GOGO INC."])
        self.assertFalse(out["TECOGEN INC."]["collision"])

    def test_owner_flags_tiers(self):
        cases = [{"case_id": "ford", "ticker": "F", "cutoff": "2022-05-10", "cik": "37996", "company": "FORD MOTOR CO"},
                 {"case_id": "shell", "ticker": "F", "cutoff": "2022-05-16", "cik": "999", "company": "NUTRIBAND"},
                 {"case_id": "old_alcoa", "ticker": "AA", "cutoff": "2016-02-19", "cik": "4281", "company": "ALCOA INC."},
                 {"case_id": "no_owner", "ticker": "ZZZZ", "cutoff": "2012-01-05", "cik": "5", "company": "DELISTED CO"}]
        owners = {"F": {"37996"}, "AA": {"1675149"}}
        flags = ta.owner_flags(cases, owners)
        self.assertNotIn("ford", flags)
        self.assertEqual(flags["shell"]["tier"], "probable")      # the owner filed the same year
        self.assertEqual(flags["old_alcoa"]["tier"], "possible")  # ticker reassigned later: not proof
        self.assertNotIn("no_owner", flags)


if __name__ == "__main__":
    unittest.main()
