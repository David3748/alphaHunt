"""Offline tests for the 8-K 5.02 stewardship-gap exclude MVP.

Covers: regex classifier (role / successor incl. negation / disagreement incl.
boilerplate negation / interim / immediate / departure-type splits), EFTS
items filter, multi-officer dedupe, security-master exclusions, CDX window
logic with fixture timestamps (PIT bound enforcement), live-path non-PIT
marking, exclude rule, and the -180d placebo helper. No network.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import stewardship_gap as sg

CEO_RESIGN_NOW = (
    "On June 1, 2024, John Smith resigned as Chief Executive Officer of the "
    "Company, effective immediately. No successor has been named and a search "
    "for a replacement is underway. There were no disagreements with management."
)
CFO_RETIRE_SUCCESSOR = (
    "On March 1, 2024, Jane Doe notified the Company of her retirement as "
    "Chief Financial Officer, effective April 15, 2024. The Board appointed "
    "Richard Roe as Chief Financial Officer, effective April 15, 2024."
)
CFO_QUIT_DISAGREE = (
    "On May 2, 2024, the Chief Financial Officer resigned following "
    "disagreements with management regarding accounting practices, "
    "effective immediately. An interim Chief Financial Officer will serve "
    "while the Company searches for a successor."
)


class ClassifierTest(unittest.TestCase):
    def test_ceo_immediate_no_successor_no_disagreement(self):
        c = sg.classify_departure(CEO_RESIGN_NOW, "2024-06-01")
        self.assertEqual(c["officer_role"], "CEO")
        self.assertEqual(c["departure_type"], "resignation")
        self.assertTrue(c["effective_immediate"])
        self.assertEqual(c["transition_period_days"], 0)
        self.assertFalse(c["successor_named"])  # negation wins
        self.assertFalse(c["has_disagreement_flag"])  # boilerplate negation wins
        self.assertTrue(c["is_unexpected"])  # immediate still triggers

    def test_cfo_retirement_with_successor(self):
        c = sg.classify_departure(CFO_RETIRE_SUCCESSOR, "2024-03-01")
        self.assertEqual(c["officer_role"], "CFO")
        self.assertEqual(c["departure_type"], "retirement")
        self.assertTrue(c["successor_named"])
        self.assertFalse(c["effective_immediate"])
        self.assertEqual(c["transition_period_days"], 45)
        self.assertFalse(c["is_unexpected"])

    def test_disagreement_split(self):
        c = sg.classify_departure(CFO_QUIT_DISAGREE, "2024-05-02")
        self.assertTrue(c["has_disagreement_flag"])
        self.assertTrue(c["is_unexpected"])
        self.assertTrue(c["interim_appointed"])
        # "searches for a successor" (future search) is not a named successor
        self.assertFalse(c["successor_named"])

    def test_departure_type_splits(self):
        self.assertEqual(sg.classify_departure("The CEO passed away on Friday.")["departure_type"], "death")
        self.assertEqual(sg.classify_departure("The CFO stepped down from her role.")["departure_type"], "stepping_down")
        self.assertEqual(sg.classify_departure("The Board terminated the CEO.")["departure_type"], "termination")
        self.assertEqual(sg.classify_departure("The CEO will retire next year.")["departure_type"], "retirement")
        self.assertEqual(sg.classify_departure("A director was appointed.")["departure_type"], "ambiguous")

    def test_disagreement_negation_variants(self):
        for text in ("His resignation was not due to any disagreement with the Company.",
                     "The departure was without any disagreements on operations.",
                     "There were no disagreements between the officer and management."):
            self.assertFalse(sg.classify_departure(text)["has_disagreement_flag"], text)

    def test_successor_positive_variants(self):
        for text in ("The Board named Alice Lin as interim Chief Executive Officer.",
                     "Bob Jones will assume the role of CFO on Monday.",
                     "The Company elected Carol Wu as Chief Financial Officer."):
            self.assertTrue(sg.classify_departure(text)["successor_named"], text)

    def test_appointment_only_has_no_departure_language(self):
        text = ("On June 24, 2026, the Board appointed Dr. Gilmer to serve on "
                "the Nominating and Corporate Governance Committee.")
        c = sg.classify_departure(text)
        self.assertFalse(c["has_departure_language"])
        # comp-agreement "terminate" far from any role is not a departure
        far = ("The Chief Executive Officer received a bonus. " + "filler " * 200 +
               "The old consulting agreement will terminate in June.")
        self.assertFalse(sg.has_departure_language(far))

    def test_departure_language_proximity(self):
        self.assertTrue(sg.has_departure_language(
            "Jay Kim resigned as Co-Chief Executive Officer of the Company."))
        self.assertTrue(sg.has_departure_language(
            "John Smith resigned. He had served as Chief Financial Officer."))
        self.assertFalse(sg.has_departure_language(
            "The Company appointed a new Chief Financial Officer."))


class ItemsFilterTest(unittest.TestCase):
    def test_keeps_only_502(self):
        rows = [{"items": "1.01,5.02,9.01"}, {"items": "4.02"},
                {"items": "5.02"}, {"items": ""}, {}, {"items": "15.02"}]
        self.assertEqual(len(sg.items_filter_502(rows)), 2)


class DedupeTest(unittest.TestCase):
    def _row(self, role, successor):
        return {"cik": "0000000001", "accession": "0000000001-24-000001",
                "ticker": "AAA", "company": "Acme", "filed_date": "2024-06-01",
                "form": "8-K", "officer_role": role, "departure_type": "resignation",
                "has_departure_language": True,
                "is_unexpected": True, "successor_named": successor,
                "has_disagreement_flag": False, "interim_appointed": False,
                "effective_immediate": True, "transition_period_days": 0,
                "stated_reason": "resigned"}

    def test_multi_officer_filing_is_one_event(self):
        events = sg.dedupe_events([self._row("CFO", False), self._row("CEO", True)])
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["primary_role"], "CEO")  # CEO outranks CFO
        self.assertEqual(sorted(ev["roles"]), ["CEO", "CFO"])
        # CEO named a successor, CFO did not -> CFO remains a trigger
        self.assertEqual(ev["trigger_roles_without_successor"], ["CFO"])
        self.assertEqual(len(ev["departures"]), 2)

    def test_distinct_filings_stay_distinct(self):
        a = self._row("CEO", False)
        b = dict(a, accession="0000000001-24-000002")
        self.assertEqual(len(sg.dedupe_events([a, b])), 2)

    def test_appointment_only_filing_has_no_triggers(self):
        rows = [{**self._row("CFO", True), "has_departure_language": False}]
        events = sg.dedupe_events(rows)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["has_departure_language"])
        self.assertEqual(events[0]["trigger_roles_without_successor"], [])
        dec = sg.apply_rule(events[0], {})
        self.assertEqual(dec["decision"], "no_exclude")
        self.assertEqual(dec["reason"], "appointment_or_comp_only_no_departure_language")


class SecurityMasterTest(unittest.TestCase):
    MASTER = {
        "AAA": {"exchange": "Nasdaq", "cik": "0000000001", "title": "Acme"},
        "BBB": {"exchange": "NYSE", "cik": "0000000002", "title": "Beta"},
        "OTCQ": {"exchange": "OTCQB", "cik": "0000000003", "title": "Otc Co"},
        "KITT": {"exchange": "Nasdaq", "cik": "0000000004", "title": "Nauticus"},
        "GOOG": {"exchange": "Nasdaq", "cik": "0000000005", "title": "Alphabet C"},
        "GOOGL": {"exchange": "Nasdaq", "cik": "0000000005", "title": "Alphabet A"},
    }

    def test_warrant_preferred_unit_suffixes_excluded(self):
        for t in ("XYZ-WT", "ABC-PA", "ABC-P", "SPAC-UN", "XYZ-RI"):
            excluded, reason = sg.is_excluded_security(t, self.MASTER)
            self.assertTrue(excluded, t)
            self.assertIn(reason, ("non_common_suffix_warrant_preferred_unit_right",))

    def test_class_b_dash_survives(self):
        excluded, _ = sg.is_excluded_security("BRK-B", None)
        self.assertFalse(excluded)

    def test_otc_excluded_listed_kept(self):
        self.assertTrue(sg.is_excluded_security("OTCQ", self.MASTER)[0])
        self.assertFalse(sg.is_excluded_security("AAA", self.MASTER)[0])
        self.assertFalse(sg.is_excluded_security("BBB", self.MASTER)[0])

    def test_unknown_ticker_fails_open(self):
        excluded, reason = sg.is_excluded_security("ZZZZ", self.MASTER)
        self.assertFalse(excluded)
        self.assertEqual(reason, "no_master_entry_needs_review")

    def test_fifth_char_warrant_by_root_collision(self):
        excluded, reason = sg.is_excluded_security("KITTW", self.MASTER)
        self.assertTrue(excluded)
        self.assertEqual(reason, "probable_warrant_unit_right_by_root")
        # 5-char commons are safe: GOOGL ends in L; unknown roots fail open
        self.assertFalse(sg.is_excluded_security("GOOGL", self.MASTER)[0])
        self.assertFalse(sg.is_excluded_security("ZZZZW", self.MASTER)[0])


CDX_ROWS = [
    ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest"],
    ["k1", "20240610120000", "https://example.com/careers", "text/html", "200", "d1"],
    ["k1", "20240715120000", "https://example.com/careers", "text/html", "200", "d2"],
]
HTML_NO_CFO = '<html><body><a href="/jobs/1">Software Engineer</a><h3>About us</h3></body></html>'
HTML_WITH_CFO = ('<html><body><a href="/jobs/9">Chief Financial Officer</a>'
                 '<a href="/jobs/1">Software Engineer</a></body></html>')


class AtsWindowTest(unittest.TestCase):
    CFG = {"vendor": "greenhouse", "board_token": "example",
           "careers_url": "https://example.com/careers"}

    def test_cdx_uses_latest_in_window_snapshot_only(self):
        seen_urls = []

        def cdx_get(url):
            seen_urls.append(url)
            return CDX_ROWS

        def snapshot_get(url):
            # PIT violation if the post-window 07-15 snapshot is ever fetched
            self.assertNotIn("20240715", url)
            self.assertIn("20240610", url)
            return HTML_NO_CFO

        res = sg.has_posting_in_window(self.CFG, "CFO", "2024-06-01",
                                       cdx_get=cdx_get, snapshot_get=snapshot_get)
        self.assertEqual(res["provenance"], "cdx_pit")
        self.assertFalse(res["found"])
        self.assertEqual(res["evidence"]["snapshot_ts"], "20240610120000")

    def test_post_window_posting_does_not_leak_into_decision(self):
        # Only snapshot available is AFTER event+30d -> no PIT evidence.
        rows = [CDX_ROWS[0], CDX_ROWS[2]]

        def snapshot_get(url):
            raise AssertionError(f"must not fetch out-of-window snapshot: {url}")

        res = sg.has_posting_in_window(self.CFG, "CFO", "2024-06-01",
                                       cdx_get=lambda u: rows, snapshot_get=snapshot_get)
        self.assertIsNone(res["found"])
        self.assertEqual(res["provenance"], "no_coverage")

    def test_cdx_hit_marks_pit(self):
        res = sg.has_posting_in_window(
            self.CFG, "CFO", "2024-06-01",
            cdx_get=lambda u: [CDX_ROWS[0], CDX_ROWS[1]],
            snapshot_get=lambda u: HTML_WITH_CFO)
        self.assertTrue(res["found"])
        self.assertEqual(res["provenance"], "cdx_pit")
        self.assertIn("Chief Financial Officer", res["evidence"]["matching_titles"])

    def test_live_path_is_flagged_non_pit(self):
        res = sg.has_posting_in_window(
            self.CFG, "CFO", "2024-06-01",
            live_json=lambda u: {"jobs": [{"title": "Chief Financial Officer"}]})
        self.assertTrue(res["found"])
        self.assertEqual(res["provenance"], "live_non_pit")
        self.assertIn("NOT point-in-time", res["evidence"]["warning"])

    def test_no_mapping_no_coverage(self):
        res = sg.has_posting_in_window(None, "CFO", "2024-06-01")
        self.assertIsNone(res["found"])
        self.assertEqual(res["provenance"], "no_coverage")

    def test_title_matching_is_tight(self):
        self.assertTrue(sg.title_matches_role("Chief Financial Officer", "CFO"))
        self.assertTrue(sg.title_matches_role("CFO — New York", "CFO"))
        self.assertFalse(sg.title_matches_role("VP Finance", "CFO"))
        self.assertFalse(sg.title_matches_role("Chief Executive Officer", "CFO"))


class RuleTest(unittest.TestCase):
    def _event(self, triggers):
        return {"event_id": "e1", "ticker": "AAA", "cik": "0000000001",
                "filed_date": "2024-06-01",
                "trigger_roles_without_successor": triggers}

    def test_exclude_on_gap_with_pit_provenance(self):
        dec = sg.apply_rule(self._event(["CFO"]),
                            {"CFO": {"found": False, "provenance": "cdx_pit"}})
        self.assertEqual(dec["decision"], "exclude")
        self.assertEqual(dec["horizon_td"], 90)
        self.assertEqual(dec["ats_provenance"], "cdx_pit")
        self.assertTrue(dec["exclude_end_cal_approx"] > dec["exclude_start"])

    def test_posting_in_window_blocks_exclude(self):
        dec = sg.apply_rule(self._event(["CFO"]),
                            {"CFO": {"found": True, "provenance": "cdx_pit"}})
        self.assertEqual(dec["decision"], "no_exclude")

    def test_live_only_evidence_never_excludes(self):
        dec = sg.apply_rule(self._event(["CFO"]),
                            {"CFO": {"found": False, "provenance": "live_non_pit"}})
        self.assertEqual(dec["decision"], "no_data")

    def test_missing_ats_never_excludes(self):
        dec = sg.apply_rule(self._event(["CEO"]), {})
        self.assertEqual(dec["decision"], "no_data")

    def test_successor_named_never_excludes(self):
        dec = sg.apply_rule(self._event([]), {})
        self.assertEqual(dec["decision"], "no_exclude")

    def test_placebo_shifts_minus_180d(self):
        dec = sg.apply_rule(self._event(["CFO"]),
                            {"CFO": {"found": False, "provenance": "cdx_pit"}})
        p = sg.make_placebo(dec)
        self.assertTrue(p["is_placebo"])
        self.assertEqual(p["placebo_shift_days"], -180)
        self.assertTrue(p["decision"].startswith("placebo_"))
        self.assertEqual(p["ticker"], "AAA")
        self.assertLess(p["exclude_start"], dec["exclude_start"])


class PipelineTest(unittest.TestCase):
    def test_end_to_end_offline(self):
        raw = [{"cik": "0000000001", "accession": "0000000001-24-000001",
                "ticker": "AAA", "company": "Acme", "filed_date": "2024-06-01",
                "form": "8-K", "filing_excerpt": CEO_RESIGN_NOW},
               {"cik": "0000000002", "accession": "0000000002-24-000001",
                "ticker": "XYZ-WT", "company": "Warrant Co",
                "filed_date": "2024-06-02", "form": "8-K",
                "filing_excerpt": CEO_RESIGN_NOW}]
        master = {"AAA": {"exchange": "Nasdaq", "cik": "0000000001", "title": "Acme"}}
        board_map = {"AAA": {"vendor": "greenhouse", "board_token": "acme",
                             "careers_url": "https://acme.example/careers"}}
        decisions, placebos, stats = sg.run_pipeline(
            raw, board_map=board_map, master=master,
            cdx_get=lambda u: [CDX_ROWS[0], CDX_ROWS[1]],
            snapshot_get=lambda u: HTML_NO_CFO)
        self.assertEqual(stats["excluded_security"], 1)  # XYZ-WT warrant
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "exclude")
        self.assertEqual(len(placebos), 1)
        self.assertTrue(placebos[0]["is_placebo"])


if __name__ == "__main__":
    unittest.main()
