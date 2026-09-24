"""tests/test_fidelity_exclusions.py — integrator tests for the merged list.

Covers lane-shaped inputs (fda_avoid rows, stewardship decisions, fr337
events, CPSC config gate), overlap merging, PIT filtering, hooks, the paper
helper, and JSON/CSV round-trip. No network.
"""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fidelity_exclusions as fe


def fda_row(**kw):
    base = {"ticker": None, "cik": None, "match_confidence": "unresolved",
            "category": "drug", "classification": "Class I",
            "report_date": "2021-03-15", "entry_date": "2021-03-16",
            "window_end": "2021-04-04", "hold_days": 20,
            "reason": "Class I drug recall D-1: test",
            "recall_number": "D-1", "recalling_firm": "Test Pharma Inc",
            "liquidity_checked": False, "pulled_at": "fixture"}
    base.update(kw)
    return base


def stew_decision(**kw):
    base = {"event_id": "e1", "ticker": "GOVC", "cik": "0000000001",
            "filed_date": "2024-06-01", "exclude_start": "2024-06-01",
            "horizon_td": 90, "decision": "exclude",
            "reason": "trigger_departure_no_successor_no_cdx_posting_30d",
            "trigger_roles": ["CEO"]}
    base.update(kw)
    return base


def fr_event(**kw):
    base = {"document_number": "2026-1", "title": "Institution of Investigation",
            "event_type": "institution", "ladder_stage": "institution",
            "investigation_no": "337-TA-1520",
            "respondents_resolved": [{"raw": "Acme Corp", "ticker": "ACME"}],
            "pit_timestamp": "2026-05-01", "publication_date": "2026-05-02"}
    base.update(kw)
    return base


class ParseTest(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(fe.parse_date("20250115"), dt.date(2025, 1, 15))
        self.assertEqual(fe.parse_date("2025-03-04"), dt.date(2025, 3, 4))
        self.assertEqual(fe.parse_date("2026-05-01T00:00:00Z"), dt.date(2026, 5, 1))
        self.assertEqual(fe.parse_date("2024"), dt.date(2024, 1, 1))
        for bad in ("", "not-a-date", None, "99999999"):
            self.assertIsNone(fe.parse_date(bad), bad)


class FdaLaneTest(unittest.TestCase):
    def test_lane_rows_keep_entry_window(self):
        out = fe.from_fda_avoid_rows(
            [fda_row(), fda_row(recall_number="D-2", category="device",
                                entry_date="2021-03-16", window_end="2021-04-24",
                                hold_days=40)])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["event_date"], "2021-03-16")  # T+1, not initiation
        self.assertEqual(out[0]["window_end"], "2021-04-04")   # 20d drug hold
        self.assertIsNone(out[0]["ticker"])                   # unresolved kept...
        self.assertEqual(out[0]["firm"], "Test Pharma Inc")   # ...with firm key

    def test_raw_fallback_is_report_anchored(self):
        recs = [{"classification": "Class I", "report_date": "20200519",
                 "recall_initiation_date": "20200101", "ticker": "AAA",
                 "reason_for_recall": "x"}]
        out = fe.load_fda_raw(recs, category="drug")
        self.assertEqual(len(out), 1)
        # report 2020-05-19 -> entry T+1, 20d drug hold; initiation ignored
        self.assertEqual(out[0]["event_date"], "2020-05-20")
        self.assertEqual(out[0]["window_end"], "2020-06-08")

    def test_raw_fallback_drops_non_class_i_and_missing_report(self):
        recs = [{"classification": "Class II", "report_date": "20200519", "ticker": "A"},
                {"classification": "Class I", "ticker": "B"}]
        self.assertEqual(fe.load_fda_raw(recs), [])


class CpscLaneTest(unittest.TestCase):
    def test_fire_pattern_qualifies_40d(self):
        out = fe.load_cpsc_rows([{"ticker": "TOYK", "recall_date": "2026-04-10",
                                  "hazard": "Fire hazard", "number_of_units": 50000,
                                  "title": "overheating tablets"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["event_date"], "2026-04-11")  # T+1
        self.assertEqual(out[0]["window_end"], "2026-05-20")  # 40d

    def test_non_fire_hazard_dropped(self):
        self.assertEqual(fe.load_cpsc_rows(
            [{"ticker": "A", "recall_date": "2026-01-01", "hazard": "labeling_only"}]), [])

    def test_small_recall_dropped(self):
        self.assertEqual(fe.load_cpsc_rows(
            [{"ticker": "A", "recall_date": "2026-01-01",
              "hazard": "burn hazard", "number_of_units": 500}]), [])

    def test_unknown_units_kept_flagged(self):
        out = fe.load_cpsc_rows([{"ticker": "A", "recall_date": "2026-01-01",
                                  "hazard": "burn hazard"}])
        self.assertEqual(len(out), 1)
        self.assertIn("units unverified", out[0]["reason"])


class StewardshipLaneTest(unittest.TestCase):
    def test_exclude_decision_merges(self):
        out = fe.from_stewardship_decisions(
            [stew_decision(exclude_end_cal_approx="2024-10-09")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ticker"], "GOVC")
        self.assertEqual(out[0]["source"], "5.02-gap")
        self.assertEqual((out[0]["event_date"], out[0]["window_end"]),
                         ("2024-06-01", "2024-10-09"))

    def test_placebo_and_nodata_never_merge(self):
        rows = [stew_decision(is_placebo=True, exclude_end_cal_approx="2024-10-09"),
                stew_decision(event_id="e2", decision="no_data",
                              exclude_end_cal_approx="2024-10-09"),
                stew_decision(event_id="e3", decision="no_exclude",
                              exclude_end_cal_approx="2024-10-09")]
        self.assertEqual(fe.from_stewardship_decisions(rows), [])

    def test_raw_fallback_gap_only(self):
        rows = [{"ticker": "E", "filed_date": "2025-01-05", "is_unexpected": True,
                 "successor_named": False, "officer_role": "CEO"},
                {"ticker": "F", "filed_date": "2025-01-05", "is_unexpected": True,
                 "successor_named": True},
                {"ticker": "G", "filed_date": "2025-01-05", "is_unexpected": False,
                 "successor_named": False}]
        out = fe.load_gap502_rows(rows)
        self.assertEqual([r["ticker"] for r in out], ["E"])


class Fr337LaneTest(unittest.TestCase):
    def test_institution_merges_receipt_and_misc_do_not(self):
        rows = [fr_event(),
                fr_event(document_number="2", ladder_stage="complaint",
                         event_type="receipt"),
                fr_event(document_number="3", ladder_stage="misc", event_type="misc")]
        out = fe.from_fr337_events(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ticker"], "ACME")
        # PIT = publication_date 2026-05-02 (lane prefers filed_at, else
        # publication_date) -> event T+1, 180d hold
        self.assertEqual(out[0]["event_date"], "2026-05-03")  # PIT+1
        self.assertEqual(out[0]["window_end"], "2026-10-29")  # 180d

    def test_unresolved_respondent_kept_with_firm(self):
        ev = fr_event(respondents_resolved=[{"raw": "Unknown Corp", "ticker": None}])
        out = fe.from_fr337_events([ev])
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["ticker"])
        self.assertIn("Unknown Corp", out[0].get("firm", ""))


class MergeTest(unittest.TestCase):
    def test_overlap_extends_no_reentry(self):
        recs = [
            {"ticker": "AAA", "source": "FDA", "event_date": "2025-01-01",
             "window_end": "2025-06-30", "reason": "first"},
            {"ticker": "AAA", "source": "CPSC", "event_date": "2025-03-01",
             "window_end": "2025-06-29", "reason": "second"},
            {"ticker": "AAA", "source": "337", "event_date": "2026-01-01",
             "window_end": "2026-06-30", "reason": "later"}]
        merged = fe.merge_exclusions(recs)
        aaa = [r for r in merged if r["ticker"] == "AAA"]
        self.assertEqual(len(aaa), 2)
        self.assertEqual(aaa[0]["source"], "CPSC+FDA")
        self.assertEqual((aaa[0]["event_date"], aaa[0]["window_end"]),
                         ("2025-01-01", "2025-06-30"))

    def test_unresolved_never_merge_across_firms(self):
        recs = [
            {"ticker": None, "firm": "Firm A", "source": "FDA",
             "event_date": "2025-01-01", "window_end": "2025-06-30", "reason": "a"},
            {"ticker": None, "firm": "Firm B", "source": "FDA",
             "event_date": "2025-01-01", "window_end": "2025-06-30", "reason": "b"}]
        self.assertEqual(len(fe.merge_exclusions(recs)), 2)


class PitFilterTest(unittest.TestCase):
    EXCL = [{"ticker": "AAA", "source": "FDA", "event_date": "2025-01-10",
             "window_end": "2025-02-08", "reason": "x"}]

    def test_bounds(self):
        self.assertIsNone(fe.is_excluded("AAA", "2025-01-09", self.EXCL))
        self.assertIsNotNone(fe.is_excluded("AAA", "2025-01-10", self.EXCL))
        self.assertIsNotNone(fe.is_excluded("aaa", "2025-02-08", self.EXCL))
        self.assertIsNone(fe.is_excluded("AAA", "2025-02-09", self.EXCL))
        self.assertIsNone(fe.is_excluded("ZZZ", "2025-02-01", self.EXCL))

    def test_default_off_and_entry_skip(self):
        sigs = [{"ticker": "AAA", "date": "2025-02-01"}]
        kept, skipped = fe.filter_signals(sigs, None)
        self.assertEqual((kept, skipped), (sigs, []))
        sigs = [{"ticker": "AAA", "date": "2025-02-01"},
                {"ticker": "AAA", "date": "2025-08-01"},
                {"ticker": "BBB", "date": "2025-02-01"}]
        kept, skipped = fe.filter_signals(sigs, self.EXCL)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(skipped), 1)
        self.assertIn("_excluded_by", skipped[0])


class HooksTest(unittest.TestCase):
    def test_sector_cap(self):
        cands = [{"ticker": t, "date": "2025-01-01"} for t in ("A", "B", "C", "D")]
        sector = {"A": "Tech", "B": "Tech", "C": "Tech", "D": "Health"}
        kept, dropped = fe.enforce_sector_caps(cands, sector, cap=0.5)
        self.assertTrue(len(kept) < 4 and len(dropped) >= 1)
        kept2, _ = fe.enforce_sector_caps([{"ticker": "Z"}], {}, cap=0.01)
        self.assertEqual(len(kept2), 1)

    def test_liquidity_defaults_and_fail_closed(self):
        self.assertTrue(fe.passes_liquidity(3.0, 2_000_000.0))
        self.assertFalse(fe.passes_liquidity(2.99, 5_000_000.0))
        self.assertFalse(fe.passes_liquidity(10.0, None))
        sigs = [{"ticker": "A", "date": "2025-01-01"}, {"ticker": "B", "date": "2025-01-01"}]
        kept, dropped = fe.filter_liquidity(
            sigs, price_at=lambda t, d: {"A": 10.0, "B": 1.0}[t],
            adv_at=lambda t, d: 5_000_000)
        self.assertEqual([s["ticker"] for s in kept], ["A"])
        self.assertEqual([s["ticker"] for s in dropped], ["B"])

    def test_liquidity_no_getters_skips_visibly(self):
        kept, dropped = fe.filter_liquidity([{"ticker": "A"}], None, None)
        self.assertEqual(len(kept), 1)
        self.assertFalse(kept[0]["_liquidity_checked"])
        self.assertEqual(dropped, [])


class PaperTest(unittest.TestCase):
    def test_excluded_and_capacity(self):
        excl = [{"ticker": "AAA", "source": "FDA", "event_date": "2025-01-10",
                 "window_end": "2025-07-09", "reason": "x"}]
        sigs = [{"ticker": "AAA", "date": "2025-02-01"},
                {"ticker": "BBB", "date": "2025-02-01"},
                {"ticker": "CCC", "date": "2025-02-02"}]
        rep = fe.paper_long_only(sigs, excl, hold_days=90, max_positions=1)
        self.assertEqual(rep["n_skipped_excluded"], 1)
        self.assertEqual(rep["n_entered"], 1)
        self.assertEqual(rep["n_skipped_capacity"], 1)
        self.assertEqual(rep["holdings"][0]["ticker"], "BBB")


class RoundtripTest(unittest.TestCase):
    def test_json_csv(self):
        merged = [{"ticker": "AAA", "source": "FDA", "event_date": "2025-01-10",
                   "window_end": "2025-02-08", "reason": "x"}]
        with tempfile.TemporaryDirectory() as td:
            jp, cp = Path(td) / "e.json", Path(td) / "e.csv"
            fe.save_exclusions_json(merged, jp, meta={"note": "t"})
            fe.save_exclusions_csv(merged, cp)
            self.assertEqual(fe.load_exclusions_json(jp), merged)
            text = cp.read_text()
            self.assertIn("ticker,source,event_date,window_end,reason,firm", text)
            self.assertIn("AAA", text)


if __name__ == "__main__":
    unittest.main()
