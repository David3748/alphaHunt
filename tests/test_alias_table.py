"""Tests for the shared subsidiary->CIK alias table v1 (researcher #12 gate).

Offline only: no OPENROUTER_API_KEY, no network. Covers the Coinbase
containment fix, the Compass/CODI collision guard, surname/fund nulls,
Exhibit-21 absorption, and the human golden-set fixture.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import alias_table as at
from alias_resolve import norm, resolve


def company_only_table():
    uni = json.loads((ROOT / "data" / "universe.json").read_text(encoding="utf-8"))
    return at.AliasTable.from_parts(company_records=uni)


class TestNorm(unittest.TestCase):
    def test_norm_strips_suffix_and_state(self):
        self.assertEqual(norm("Coinbase, Inc."), "coinbase")
        self.assertEqual(norm("Compass Diversified Holdings"), "compass diversified")
        self.assertEqual(norm("BANK OF AMERICA CORP /DE/"), "bank of america")


class TestContainmentFix(unittest.TestCase):
    def test_coinbase_resolves_via_containment_on_company_index(self):
        t = company_only_table()
        ticker, cik, conf = t.resolve_ticker("Coinbase, Inc.")
        self.assertEqual(ticker, "COIN")
        self.assertEqual(cik, "0001679788")
        self.assertTrue(conf.startswith("containment_"),
                        f"expected containment fallback, got {conf}")

    def test_exact_canonical_still_exact(self):
        t = company_only_table()
        self.assertEqual(t.resolve_ticker("Apple Inc.")[:2], ("AAPL", "0000320193"))


class TestCollisionGuard(unittest.TestCase):
    def test_bare_compass_is_null(self):
        t = company_only_table()
        self.assertEqual(t.resolve_ticker("Compass"), (None, None, None))

    def test_full_names_resolve_to_each_side(self):
        tbl = at.get_default_table()
        self.assertEqual(tbl.resolve_ticker("Compass, Inc.")[:2],
                         ("COMP", "0001563190"))
        self.assertEqual(tbl.resolve_ticker("Compass Diversified Holdings")[:2],
                         ("CODI", "0001345126"))

    def test_endo_pair(self):
        tbl = at.get_default_table()
        self.assertEqual(tbl.resolve_ticker("Endo"), (None, None, None))
        self.assertEqual(tbl.resolve_ticker("Endo International plc")[:2],
                         ("ENDP", "0001593034"))
        # J&J subsidiary must map to JNJ, never to ENDP
        self.assertEqual(tbl.resolve_ticker("Ethicon Endo-Surgery")[:2],
                         ("JNJ", "0000200406"))


class TestNullGuards(unittest.TestCase):
    def test_officer_surnames_return_null(self):
        tbl = at.get_default_table()
        self.assertEqual(tbl.resolve_ticker("Smith"), (None, None, None))
        self.assertEqual(tbl.resolve_ticker("John Smith"), (None, None, None))
        self.assertEqual(tbl.resolve_ticker("Michael Johnson"), (None, None, None))

    def test_fund_defendants_return_null_but_issuer_exact_works(self):
        tbl = at.get_default_table()
        self.assertEqual(tbl.resolve_ticker("BlackRock Fund Advisors"),
                         (None, None, None))
        self.assertEqual(tbl.resolve_ticker("Compass Growth Fund"),
                         (None, None, None))
        self.assertEqual(tbl.resolve_ticker("BlackRock, Inc.")[:2],
                         ("BLK", "0002012383"))


class TestMatchPolicy(unittest.TestCase):
    def test_fuzzy_threshold_param(self):
        idx = {"first national continental bank": [("FNCB", "0000001234")]}
        t, c, conf = resolve("First National Continental Banl", idx)
        self.assertEqual((t, c), ("FNCB", "0000001234"))
        self.assertTrue(conf.startswith("fuzzy_"))
        strict = resolve("First National Continental Banl", idx,
                         fuzzy_threshold=0.95)
        self.assertEqual(strict, (None, None, None))

    def test_cik_priority_tiebreak(self):
        idx = {"acme": [("ACME", "0000000002"), ("ACMX", "0000000001")]}
        self.assertEqual(resolve("Acme Corp", idx)[:2], ("ACMX", "0000000001"))


class TestExhibit21Absorption(unittest.TestCase):
    def test_subsidiary_rows_absorbed_offline_no_api_key(self):
        uni = json.loads((ROOT / "data" / "universe.json").read_text(encoding="utf-8"))
        rows = [
            {"ticker": "JNJ", "cik": "200406",
             "subsidiaries": [{"name": "Ethicon Endo-Surgery", "jurisdiction": "NJ"},
                              {"name": "Janssen-Cilag Ltd", "jurisdiction": "UK"}]},
            {"ticker": "TEST", "cik": "999999",
             "subsidiaries": [{"name": "Test Subsidiary LLC"}]},
        ]
        tbl = at.AliasTable.from_parts(company_records=uni, exhibit21_records=rows)
        self.assertGreaterEqual(tbl.stats["exhibit21_aliases"], 3)
        self.assertEqual(tbl.resolve_ticker("Janssen-Cilag Ltd")[:2],
                         ("JNJ", "0000200406"))
        kids = tbl.resolve_subsidiary("JNJ")
        self.assertTrue(any(k["name"] == "Ethicon Endo-Surgery" for k in kids))
        self.assertTrue(any(k["name"] == "Test Subsidiary LLC"
                            for k in tbl.resolve_subsidiary("999999")))
        # reverse lookup still resolves the parent itself
        self.assertEqual(tbl.resolve_ticker("Test Subsidiary LLC")[:2],
                         ("TEST", "0000999999"))


class TestGoldenSet(unittest.TestCase):
    def test_fixture_loads_and_covers_required_cases(self):
        rows = at.load_golden_set()
        self.assertGreaterEqual(len(rows), 50,
                                f"golden set needs ~50-100 entries, got {len(rows)}")
        names = {r["name"] for r in rows}
        for required in ["Compass", "Compass Diversified Holdings",
                         "Coinbase, Inc.", "Endo", "Ethicon Endo-Surgery",
                         "StoneCo Ltd.", "Red Cat Holdings, Inc.",
                         "Organon & Co.", "DoubleVerify Holdings, Inc.",
                         "ASTRAZENECA PLC", "Elevance Health, Inc.",
                         "UroGen Pharma Ltd."]:
            self.assertIn(required, names, f"golden set missing {required}")

    def test_golden_spot_checks_pass_through_default_table(self):
        tbl = at.get_default_table()
        rows = {r["name"]: r for r in at.load_golden_set()}
        checked = 0
        for name in ["Coinbase, Inc.", "Compass Diversified Holdings",
                     "Compass, Inc.", "Ethicon Endo-Surgery", "StoneCo Ltd.",
                     "Organon & Co.", "Johnson & Johnson"]:
            want = rows[name]
            got = tbl.resolve_ticker(name)
            self.assertEqual(got[0], want["expected_ticker"], name)
            self.assertEqual(got[1], want["expected_cik"], name)
            checked += 1
        for name in ["Compass", "Endo", "Smith", "John Smith",
                     "BlackRock Fund Advisors", "Purdue Pharma LP"]:
            self.assertEqual(tbl.resolve_ticker(name), (None, None, None), name)
            checked += 1
        self.assertGreaterEqual(checked, 10)


class TestHookEntryPoints(unittest.TestCase):
    def test_hooks_share_resolve_ticker_and_resolve_subsidiary(self):
        # FDA recall firm / CPSC manufacturer / §337 respondent / SBIR firm /
        # LDA client names all flow through one entry point.
        self.assertEqual(at.resolve_ticker("Ethicon Endo-Surgery")[:2],
                         ("JNJ", "0000200406"))
        self.assertEqual(at.resolve_ticker("Coinbase, Inc.")[0], "COIN")
        self.assertEqual(at.resolve_ticker("StoneCo Ltd.")[0], "STNE")
        self.assertEqual(at.resolve_ticker("John Smith"), (None, None, None))
        kids = at.resolve_subsidiary("JNJ")
        self.assertTrue(any("Ethicon" in k["name"] for k in kids))
        diag = at.get_default_table().match_rate(
            ["Ethicon Endo-Surgery", "Coinbase, Inc.", "John Smith"])
        self.assertEqual((diag["total"], diag["matched"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
