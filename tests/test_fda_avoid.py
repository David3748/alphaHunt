"""tests/test_fda_avoid.py — offline tests for the Class I avoid-list pipeline.

Covers (per spec): date filter, report-vs-initiation anchoring, snapshot
immutability, exclusion windows. Plus substring-guard, liquidity gate, and
an end-to-end offline build from committed fixtures (no network).
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FIX = TESTS / "fixtures" / "fda_avoid"

sys.path.insert(0, str(TESTS.parent / "src"))
import fda_avoid as fa


def rec(**kw):
    base = {
        "classification": "Class I",
        "report_date": "20210315",
        "recall_initiation_date": "20210210",
        "recall_number": "D-T-1",
        "recalling_firm": "Test Pharma Inc",
        "reason_for_recall": "test",
        "product_description": "test drug",
    }
    base.update(kw)
    return base


class DateFilterTest(unittest.TestCase):
    def test_keeps_class_i_on_or_after_2004(self):
        kept, rejects = fa.filter_records(
            [rec(), rec(report_date="20040101", recall_number="D-T-2",
                        recall_initiation_date="20031220")],
            "drug", today=dt.date(2024, 1, 1),
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(rejects, [])

    def test_drops_non_class_i(self):
        kept, rejects = fa.filter_records(
            [rec(classification="Class II"), rec(classification="Class III"),
             rec(classification="")],
            "drug", today=dt.date(2024, 1, 1),
        )
        self.assertEqual(kept, [])
        self.assertTrue(all(r["_reject_reason"] == "not_class_i" for r in rejects))

    def test_drops_pre_2004_report(self):
        kept, rejects = fa.filter_records(
            [rec(report_date="20031231", recall_initiation_date="20031101")],
            "drug", today=dt.date(2024, 1, 1),
        )
        self.assertEqual(kept, [])
        self.assertEqual(rejects[0]["_reject_reason"], "report_date_before_2004")

    def test_drops_bad_and_missing_report_dates(self):
        rows = [rec(report_date="99999999"), rec(report_date=""),
                rec(report_date="20211301")]
        del rows[1]["report_date"]
        kept, rejects = fa.filter_records(rows, "device", today=dt.date(2024, 1, 1))
        self.assertEqual(kept, [])
        self.assertTrue(all(r["_reject_reason"] == "missing_or_bad_report_date" for r in rejects))

    def test_drops_future_report_date(self):
        kept, rejects = fa.filter_records(
            [rec(report_date="20990101", recall_initiation_date="20981201")],
            "device", today=dt.date(2024, 1, 1),
        )
        self.assertEqual(kept, [])
        self.assertEqual(rejects[0]["_reject_reason"], "report_date_in_future")

    def test_drops_insane_initiation_date_19301211(self):
        kept, rejects = fa.filter_records(
            [rec(report_date="20210501", recall_initiation_date="19301211")],
            "device", today=dt.date(2024, 1, 1),
        )
        self.assertEqual(kept, [])
        self.assertEqual(rejects[0]["_reject_reason"], "insane_initiation_date")

    def test_missing_initiation_date_is_ok(self):
        r = rec()
        del r["recall_initiation_date"]
        kept, rejects = fa.filter_records([r], "drug", today=dt.date(2024, 1, 1))
        self.assertEqual(len(kept), 1)


class AnchoringTest(unittest.TestCase):
    def test_anchor_is_report_date_not_initiation(self):
        r = rec(report_date="20200519", recall_initiation_date="20200101")
        self.assertEqual(fa.anchor_date(r), dt.date(2020, 5, 19))
        self.assertNotEqual(fa.anchor_date(r), fa.parse_fda_date("20200101"))

    def test_initiation_lag_quantifies_lookahead(self):
        # 2020-01-01 -> 2020-05-19 == 139 days: the median leak from the spec.
        r = rec(report_date="20200519", recall_initiation_date="20200101")
        self.assertEqual(fa.initiation_lag_days(r), 139)

    def test_entry_is_t_plus_1_of_report(self):
        rows = fa.build_exclusion_rows([rec(report_date="20200519")], "drug")
        self.assertEqual(rows[0]["entry_date"], "2020-05-20")
        self.assertEqual(rows[0]["report_date"], "2020-05-19")


class SnapshotImmutabilityTest(unittest.TestCase):
    def test_same_pull_ts_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p1 = fa.write_snapshot(d, "drug", {"results": [1]}, pull_ts="20240101T000000Z")
            before = p1.read_bytes()
            p2 = fa.write_snapshot(d, "drug", {"results": [2]}, pull_ts="20240101T000000Z")
            self.assertNotEqual(p1, p2, "collision must create a new file, not overwrite")
            self.assertEqual(p1.read_bytes(), before, "original snapshot must be byte-identical")
            self.assertEqual(json.loads(p1.read_text())["results"], [1])
            self.assertEqual(json.loads(p2.read_text())["results"], [2])

    def test_paginated_pull_snapshots_raw_results(self):
        pages = {
            0: {"meta": {"results": {"skip": 0, "limit": 2, "total": 3}},
                "results": [rec(recall_number="D-1"), rec(recall_number="D-2")]},
            2: {"meta": {"results": {"skip": 2, "limit": 2, "total": 3}},
                "results": [rec(recall_number="D-3")]},
        }

        def fake_get(url):
            from urllib.parse import parse_qs, urlparse

            skip = int(parse_qs(urlparse(url).query)["skip"][0])
            return pages[skip]

        with tempfile.TemporaryDirectory() as tmp:
            path, records = fa.pull_openfda("drug", Path(tmp), http_get=fake_get,
                                            limit=2, pull_ts="20240101T000000Z")
            self.assertEqual(len(records), 3)
            snap = fa.load_snapshot(path)
            self.assertIn("pulled_at", snap)
            self.assertEqual(snap["count"], 3)
            self.assertEqual(len(snap["results"]), 3)


class ExclusionWindowTest(unittest.TestCase):
    def test_drug_hold_20d_device_hold_40d(self):
        report = dt.date(2021, 3, 15)
        d_entry, d_end = fa.exclusion_window(report, "drug")
        v_entry, v_end = fa.exclusion_window(report, "device")
        self.assertEqual((d_entry, d_end), (dt.date(2021, 3, 16), dt.date(2021, 4, 4)))
        self.assertEqual((v_entry, v_end), (dt.date(2021, 3, 16), dt.date(2021, 4, 24)))
        self.assertEqual((d_end - d_entry).days + 1, 20)
        self.assertEqual((v_end - v_entry).days + 1, 40)

    def test_is_excluded_inclusive_bounds(self):
        start, end = dt.date(2021, 3, 16), dt.date(2021, 4, 4)
        self.assertTrue(fa.is_excluded(start, start, end))
        self.assertTrue(fa.is_excluded(end, start, end))
        self.assertFalse(fa.is_excluded(start - dt.timedelta(days=1), start, end))
        self.assertFalse(fa.is_excluded(end + dt.timedelta(days=1), start, end))


class AliasGuardTest(unittest.TestCase):
    def test_substring_trap_documented(self):
        # Naive `"endo" in "ethicon endo-surgery"` FIRES — that is the bug.
        self.assertTrue(fa.substring_match_would_false_positive("Endo", "Ethicon Endo-Surgery Inc"))
        # The safe predicate never fires on it.
        self.assertFalse(fa.is_safe_alias_match("Endo", "Ethicon Endo-Surgery Inc"))
        self.assertTrue(fa.is_safe_alias_match("Medtronic Inc.", "medtronic  inc"))

    def test_resolve_ticker_fails_closed_without_index(self):
        ticker, cik, conf = fa.resolve_ticker("Ethicon Endo-Surgery Inc")
        self.assertIsNone(ticker)
        self.assertIsNone(cik)
        self.assertIn("unresolved", conf)


class LiquidityTest(unittest.TestCase):
    def test_thresholds_and_missing_data(self):
        self.assertTrue(fa.passes_liquidity(3.0, 2_000_000.0))
        self.assertFalse(fa.passes_liquidity(2.99, 5_000_000.0))
        self.assertFalse(fa.passes_liquidity(10.0, 1_999_999.0))
        self.assertFalse(fa.passes_liquidity(None, 5_000_000.0))
        self.assertFalse(fa.passes_liquidity(10.0, None))
        self.assertTrue(fa.passes_liquidity(10.0, 5_000_000.0, min_price=5.0, min_adv_usd=1e6))

    def test_apply_liquidity_without_pit_inputs_skips_visibly(self):
        rows = fa.build_exclusion_rows([rec()], "drug")
        passing, gated = fa.apply_liquidity(rows, get_quote=None)
        self.assertEqual(len(passing), 1)
        self.assertEqual(gated, [])
        self.assertFalse(passing[0]["liquidity_checked"])


class OfflineFixtureBuildTest(unittest.TestCase):
    def test_end_to_end_from_committed_fixtures(self):
        drug = fa.load_openfda_response(FIX / "drug_enforcement_sample.json")
        device = fa.load_openfda_response(FIX / "device_enforcement_sample.json")
        kept_d, rej_d = fa.filter_records(drug, "drug", today=dt.date(2024, 1, 1))
        kept_v, rej_v = fa.filter_records(device, "device", today=dt.date(2024, 1, 1))
        self.assertEqual({r["recall_number"] for r in kept_d}, {"D-0001-2021", "D-0005-2004"})
        self.assertEqual({r["recall_number"] for r in kept_v}, {"Z-0001-2020", "Z-0004-2020"})
        self.assertEqual(
            {r["_reject_reason"] for r in rej_d},
            {"not_class_i", "report_date_before_2004", "missing_or_bad_report_date",
             "missing_or_bad_report_date"},
        )
        self.assertIn("insane_initiation_date", {r["_reject_reason"] for r in rej_v})
        rows = fa.build_exclusion_rows(kept_d, "drug", pulled_at="fixture") + \
            fa.build_exclusion_rows(kept_v, "device", pulled_at="fixture")
        by_num = {r["recall_number"]: r for r in rows}
        self.assertEqual(by_num["D-0001-2021"]["entry_date"], "2021-03-16")
        self.assertEqual(by_num["D-0001-2021"]["window_end"], "2021-04-04")
        self.assertEqual(by_num["Z-0001-2020"]["window_end"], "2020-12-30")
        # Join stub: unresolved until the alias table is wired.
        self.assertTrue(all(r["ticker"] is None for r in rows))

    def test_offline_refresh_rebuilds_exclusion_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snaps"
            out = Path(tmp) / "excl.jsonl"
            for cat, name in (("drug", "drug_enforcement_sample.json"),
                              ("device", "device_enforcement_sample.json")):
                records = fa.load_openfda_response(FIX / name)
                fa.write_snapshot(
                    snap, cat,
                    {"pulled_at": "2024-01-01T00:00:00+00:00", "pull_ts": "t",
                     "category": cat, "endpoint": "fixture", "search": None,
                     "page_limit": len(records), "count": len(records), "results": records},
                    pull_ts="20240101T000000Z",
                )
            summary = fa.weekly_refresh(snap, out, offline=True, today=dt.date(2024, 1, 1))
            self.assertEqual(summary["exclusions"], 4)
            lines = out.read_text().strip().split("\n")
            self.assertEqual(len(lines), 4)
            first = json.loads(lines[0])
            for key in ("ticker", "cik", "report_date", "reason", "window_end",
                        "entry_date", "category", "recall_number"):
                self.assertIn(key, first)


if __name__ == "__main__":
    unittest.main()
