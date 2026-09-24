import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import followon_cdx as cdx
import followon_rivals as rivals
import followon_tariff as tariff


# ── A: 10-K sourcing-country extractor on local fixtures (precision path) ─────

SNIPPET_SINGLE = (
    "We rely on contract manufacturers located in China. Substantially all of "
    "our finished products are manufactured in China, and we depend on a "
    "limited number of suppliers there."
)
SNIPPET_MULTI = (
    "We source raw materials from suppliers in China, Vietnam and Mexico. "
    "Our facilities in Texas assemble finished goods."
)
SNIPPET_NONE = (
    "We sell our products through distributors in the United States and "
    "Europe. Our headquarters are in Ohio."
)
SNIPPET_ALIAS = (
    "A supplier is located in the PRC, from which we import "
    "components for final assembly."
)


class TariffExtractorTest(unittest.TestCase):
    def test_single_country_concentration(self):
        self.assertEqual(
            tariff.extract_10k_countries(SNIPPET_SINGLE),
            {"countries": ["China"], "single_country_sourced": True, "cue_hits": 2},
        )

    def test_multi_country_no_heuristic(self):
        result = tariff.extract_10k_countries(SNIPPET_MULTI)
        self.assertEqual(result["countries"], ["China", "Mexico", "Vietnam"])
        self.assertFalse(result["single_country_sourced"])

    def test_no_sourcing_language(self):
        self.assertEqual(
            tariff.extract_10k_countries(SNIPPET_NONE),
            {"countries": [], "single_country_sourced": False, "cue_hits": 0},
        )

    def test_alias_resolves_to_canonical(self):
        result = tariff.extract_10k_countries(SNIPPET_ALIAS)
        self.assertEqual(result["countries"], ["China"])
        self.assertFalse(result["single_country_sourced"])

    def test_exposure_record_carries_filing_date_pit(self):
        expo = tariff.extract_exposure("AAA", "123", "2025-03-01", SNIPPET_SINGLE)
        self.assertEqual(expo["filing_date"], "2025-03-01")
        self.assertEqual(expo["countries"], ["China"])
        self.assertTrue(expo["single_country_sourced"])

    def test_bad_filing_date_rejected(self):
        with self.assertRaises(ValueError):
            tariff.extract_exposure("AAA", "123", "Q1 2025", SNIPPET_SINGLE)

    def test_lexicon_and_cue_counts(self):
        self.assertEqual(len(tariff.COUNTRY_LEXICON), 45)
        self.assertEqual(len(tariff.SOURCING_CUES), 12)

    def test_fr_client_reuses_injected_getter(self):
        def fake_getter(url):
            return {"results": [
                {"document_number": "2025-002", "title": "CVD initiation re widgets",
                 "publication_date": "2025-02-01", "html_url": "https://fr.test/2"},
                {"document_number": "2025-001", "title": "AD initiation re widgets",
                 "publication_date": "2025-01-10", "html_url": "https://fr.test/1"},
                {"document_number": "2025-001", "title": "dup",
                 "publication_date": "2025-01-10", "html_url": "https://fr.test/1"},
            ]}

        rows = tariff.fetch_fr_initiations(terms=["widgets"], getter=fake_getter)
        self.assertEqual([r["document_number"] for r in rows], ["2025-002", "2025-001"])
        self.assertEqual(rows[0]["publication_date"], "2025-02-01")  # event timestamp

    def test_join_is_pit_gated(self):
        expo = tariff.extract_exposure("AAA", "123", "2025-03-01", SNIPPET_SINGLE)
        early = {"countries": ["China"], "publication_date": "2025-01-15",
                 "document_number": "e", "html_url": "u", "hts_scope": []}
        late = {"countries": ["China"], "publication_date": "2025-04-01",
                "document_number": "l", "html_url": "u", "hts_scope": ["8471.30"]}
        rows = tariff.join_exposures([expo], [early, late])
        self.assertEqual(len(rows), 1)  # pre-filing event dropped, never backfilled
        self.assertEqual(rows[0]["event_date"], "2025-04-01")
        self.assertEqual(rows[0]["country"], "China")

    def test_hts_scope_stub_needs_context(self):
        scope = tariff.extract_hts_scope(
            "merchandise classifiable under HTSUS subheadings 8471.30.01 and 8517.12")
        self.assertEqual(scope, ["8471.30.01", "8517.12"])
        self.assertEqual(tariff.extract_hts_scope("net sales were 1234.56 million"), [])


# ── B: rival map lookup ───────────────────────────────────────────────────────

class RivalMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = rivals.load_rival_map()

    def test_fixture_size_and_verified_minimum(self):
        self.assertEqual(len(self.rows), 30)
        self.assertGreaterEqual(sum(1 for r in self.rows if r["verified"]), 5)

    def test_tickers_unique_and_gics6(self):
        tickers = [r["ticker"] for r in self.rows]
        self.assertEqual(len(set(tickers)), 30)
        for row in self.rows:
            self.assertRegex(str(row["gics_industry"]), r"^\d{6}$")

    def test_class_i_event_returns_mapped_rivals(self):
        self.assertEqual(
            rivals.rival_long_candidates(
                {"firm_ticker": "MDT", "classification": "Class I"},
                rival_map=self.rows),
            ["ABT", "BSX", "EW"],
        )

    def test_non_class_i_returns_nothing(self):
        for cls_ in ("Class II", "Class III", "", None):
            self.assertEqual(
                rivals.rival_long_candidates(
                    {"firm_ticker": "MDT", "classification": cls_},
                    rival_map=self.rows),
                [],
            )

    def test_unknown_firm_returns_nothing(self):
        self.assertEqual(
            rivals.rival_long_candidates(
                {"firm_ticker": "ZZZZ", "classification": "Class I"},
                rival_map=self.rows),
            [],
        )

    def test_name_fallback_resolves(self):
        self.assertEqual(
            rivals.rival_long_candidates(
                {"firm_name": "Medtronic", "classification": "Class I"},
                rival_map=self.rows),
            ["ABT", "BSX", "EW"],
        )

    def test_unverified_placeholder_yields_no_candidates(self):
        self.assertEqual(
            rivals.rival_long_candidates(
                {"firm_ticker": "ZBH", "classification": "Class I"},
                rival_map=self.rows),
            [],
        )


# ── C: CDX interval semantics + digest collapse ───────────────────────────────

CAPTURES = [
    {"timestamp": "20240101000000", "date": "2024-01-01", "original": "u",
     "digest": "aaa", "statuscode": "200"},
    {"timestamp": "20240201000000", "date": "2024-02-01", "original": "u",
     "digest": "aaa", "statuscode": "200"},  # repost, not a change
    {"timestamp": "20240301000000", "date": "2024-03-01", "original": "u",
     "digest": "bbb", "statuscode": "200"},  # genuine change
    {"timestamp": "20240401000000", "date": "2024-04-01", "original": "u",
     "digest": "aaa", "statuscode": "200"},  # revert: same digest, kept
]

PREV_TEXT = ("Leadership: Jane Smith, Chief Executive Officer. "
             "John Doe, Chief Financial Officer. Contact us.")
CURR_TEXT = "Leadership: Jane Smith, Chief Executive Officer. Contact us."
GOV_URL = "https://example.com/about/leadership"
PRODUCT_URL = "https://example.com/products/widget"


class CdxTest(unittest.TestCase):
    def test_digest_collapse_consecutive_only(self):
        collapsed = cdx.collapse_digests(CAPTURES)
        self.assertEqual(
            [c["timestamp"] for c in collapsed],
            ["20240101000000", "20240301000000", "20240401000000"],
        )

    def test_interval_half_open_semantics(self):
        interval = cdx.change_interval(CAPTURES[0], CAPTURES[2])
        self.assertEqual(interval["semantics"], "(prev_capture, this_capture]")
        self.assertFalse(cdx.in_interval("2024-01-01", interval["start"], interval["end"]))
        self.assertTrue(cdx.in_interval("2024-02-15", interval["start"], interval["end"]))
        self.assertTrue(cdx.in_interval("2024-03-01", interval["start"], interval["end"]))

    def test_person_removal_flagged_on_governance_url(self):
        result = cdx.flag_person_removals(PREV_TEXT, CURR_TEXT, GOV_URL)
        self.assertTrue(result["flag"])
        self.assertEqual(result["removed_names"], ["John Doe"])

    def test_no_removal_no_flag(self):
        result = cdx.flag_person_removals(PREV_TEXT, PREV_TEXT, GOV_URL)
        self.assertFalse(result["flag"])
        self.assertEqual(result["removed_names"], [])

    def test_non_governance_url_never_flags(self):
        result = cdx.flag_person_removals(PREV_TEXT, CURR_TEXT, PRODUCT_URL)
        self.assertFalse(result["flag"])

    def test_crosscheck_window(self):
        interval = {"start": "2024-01-01", "end": "2024-03-01",
                    "semantics": "(prev_capture, this_capture]"}
        hit = cdx.crosscheck_802(["2024-02-27", "2024-06-01"], interval)
        self.assertTrue(hit["corroborated"])
        self.assertEqual(hit["matched_filings"], ["2024-02-27"])
        miss = cdx.crosscheck_802(["2024-06-01"], interval)
        self.assertFalse(miss["corroborated"])

    def test_cdx_client_parses_with_fake_opener(self):
        payload = [["timestamp", "original", "digest", "statuscode"],
                   ["20240101000000", "http://x.test/about", "aaa", "200"],
                   ["20240301000000", "http://x.test/about", "bbb", "200"]]

        def fake_opener(req):
            return json.dumps(payload).encode()

        caps = cdx.cdx_captures("http://x.test/about", opener=fake_opener,
                                min_interval=0)
        self.assertEqual([c["digest"] for c in caps], ["aaa", "bbb"])
        self.assertEqual(caps[0]["date"], "2024-01-01")


if __name__ == "__main__":
    unittest.main()
