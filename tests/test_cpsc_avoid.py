"""tests/test_cpsc_avoid.py — offline tests for the CPSC fire/burn avoid-list.

Covers (per spec): hazard string-matcher (no LLM), listed-firm gate +
fallback resolution chain, units>=10k parsing, PIT RecallDate+1 anchoring,
40d windows, auto-sector cap, full-dump puller snapshot immutability, and
an end-to-end offline build from the committed fixture (no network —
scratch network pulls belong in /tmp, never in the repo).
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FIX = TESTS / "fixtures" / "cpsc_avoid" / "cpsc_recall_sample.json"

sys.path.insert(0, str(TESTS.parent / "src"))
import cpsc_avoid as ca


def rec(**kw):
    base = {
        "RecallID": 1,
        "RecallNumber": "26001",
        "RecallDate": "2026-08-20T00:00:00",
        "Title": "Acme Recalls Widgets Due to Fire Hazard",
        "Description": "test",
        "Hazards": [{"Name": "Fire hazard."}],
        "Products": [{"NumberOfUnits": "About 20,000"}],
        "Manufacturers": [{"Name": "Acme Corp., of Springfield, Illinois"}],
        "Importers": [],
        "Retailers": [],
    }
    base.update(kw)
    return base


TODAY = dt.date(2026, 9, 1)


class HazardMatcherTest(unittest.TestCase):
    def test_fire_matches(self):
        ok, text = ca.hazard_matches([{"Name": "Fire & Fire-Related Burn"}])
        self.assertTrue(ok)
        self.assertIn("Fire", text)

    def test_burn_matches(self):
        ok, _ = ca.hazard_matches([{"Name": "Burn - Not Fire-Related"}])
        self.assertTrue(ok)

    def test_electrocution_matches(self):
        ok, _ = ca.hazard_matches([{"Name": "Electrocution/Electric Shock"}])
        self.assertTrue(ok)

    def test_electric_shock_without_electrocution_word_matches(self):
        ok, _ = ca.hazard_matches(
            [{"Name": "The wires can become damaged, posing a risk of electric shock."}]
        )
        self.assertTrue(ok)

    def test_shock_hazard_phrase_matches(self):
        ok, _ = ca.hazard_matches(
            [{"Name": "The interface can fail, posing a shock hazard to consumers."}]
        )
        self.assertTrue(ok)

    def test_non_fire_hazards_do_not_match(self):
        for name in ("Choking", "Laceration", "Fall", "Lead", "Strangulation"):
            ok, _ = ca.hazard_matches([{"Name": name}])
            self.assertFalse(ok, name)

    def test_bare_shock_absorber_mechanical_text_does_not_match(self):
        # Bare "shock" alone must NOT match: mechanical shock absorbers are
        # not electrocution events.
        ok, _ = ca.hazard_matches(
            [{"Name": "The shock absorber rod assembly can unthread, posing crash hazards."}]
        )
        self.assertFalse(ok)

    def test_chemical_burn_ingestion_matches_by_design(self):
        # DOCUMENTED false positive: button-cell "internal chemical burns"
        # ingestion text matches "burn" under the spec's plain string rule.
        # Kept deliberately (no-LLM rule); splitting burn subtypes needs a
        # classifier. This test locks the behavior so any future fix is
        # explicit, not accidental.
        ok, _ = ca.hazard_matches(
            [{"Name": "When swallowed, batteries can cause serious internal chemical burns."}]
        )
        self.assertTrue(ok)

    def test_missing_or_malformed_hazards_fail_closed(self):
        self.assertEqual(ca.hazard_matches([]), (False, ""))
        self.assertEqual(ca.hazard_matches(None), (False, ""))
        self.assertEqual(ca.hazard_matches("fire"), (False, ""))
        self.assertEqual(ca.hazard_matches([{"Nope": 1}]), (False, ""))


class UnitsParsingTest(unittest.TestCase):
    def test_about_with_comma(self):
        self.assertEqual(ca.parse_units(rec()), 20000)

    def test_plain_number(self):
        self.assertEqual(
            ca.parse_units(rec(Products=[{"NumberOfUnits": "1,694"}])), 1694
        )

    def test_canada_parenthetical_takes_max_us_figure(self):
        self.assertEqual(
            ca.parse_units(
                rec(Products=[{"NumberOfUnits": "About 21,040 (In addition, about 4,140 were sold in Canada)"}])
            ),
            21040,
        )

    def test_max_across_products(self):
        self.assertEqual(
            ca.parse_units(rec(Products=[{"NumberOfUnits": "About 5,000"}, {"NumberOfUnits": "About 30,000"}])),
            30000,
        )

    def test_unparseable_fails_closed(self):
        self.assertIsNone(ca.parse_units(rec(Products=[{"NumberOfUnits": "Millions"}])))
        self.assertIsNone(ca.parse_units(rec(Products=[{"NumberOfUnits": ""}])))
        self.assertIsNone(ca.parse_units(rec(Products=[])))
        r = rec()
        del r["Products"]
        self.assertIsNone(ca.parse_units(r))


class ListedFirmAndFallbackTest(unittest.TestCase):
    def test_manufacturer_preferred(self):
        name, method = ca.resolve_firm_name(rec())
        self.assertEqual((name, method), ("Acme Corp.", "manufacturer"))

    def test_importer_fallback_when_manufacturer_empty(self):
        r = rec(Manufacturers=[], Importers=[{"Name": "Lisse USA LLC, of Brooklyn, New York"}])
        name, method = ca.resolve_firm_name(r)
        self.assertEqual((name, method), ("Lisse USA LLC", "importer"))
        self.assertTrue(ca.has_listed_firm(r))

    def test_title_fallback_when_both_empty(self):
        r = rec(Manufacturers=[], Importers=[])
        name, method = ca.resolve_firm_name(r)
        self.assertEqual((name, method), ("Acme", "title"))
        # ... but title alone does NOT satisfy the rule gate.
        self.assertFalse(ca.has_listed_firm(r))

    def test_title_parse_strips_cpsc_prefix(self):
        r = rec(
            Manufacturers=[],
            Importers=[],
            Title="CPSC, Nautilus Inc. Announce Recall to Repair Exercise Benches",
        )
        name, method = ca.resolve_firm_name(r)
        self.assertEqual(method, "title")
        self.assertEqual(name, "Nautilus Inc.")

    def test_location_suffix_cleaned_bank_of_america_untouched(self):
        self.assertEqual(
            ca.clean_firm_name("CCM Hockey U.S., Inc., of Maple Grove, Illinois"),
            "CCM Hockey U.S., Inc.",
        )
        self.assertEqual(ca.clean_firm_name("Bank of America"), "Bank of America")

    def test_garbage_title_resolves_none(self):
        name, method = ca.resolve_firm_name(rec(Manufacturers=[], Importers=[], Title=""))
        self.assertEqual((name, method), ("", "none"))


class ResolveTickerTest(unittest.TestCase):
    def test_fails_closed_without_index(self):
        ticker, cik, conf = ca.resolve_ticker("Acme Corp.")
        self.assertIsNone(ticker)
        self.assertIsNone(cik)
        self.assertIn("unresolved", conf)

    def test_empty_name(self):
        _, _, conf = ca.resolve_ticker("", alias_index={})
        self.assertEqual(conf, "empty_firm_name")

    def test_shared_alias_hook_wiring(self):
        sys.path.insert(0, str(TESTS.parent / "src"))
        import alias_resolve as ar

        idx = {ar.norm("Acme Corporation"): [("ACME", "0000001234")]}
        ticker, cik, conf = ca.resolve_ticker("Acme Corp.", idx)
        self.assertEqual(ticker, "ACME")
        self.assertEqual(cik, "0000001234")
        self.assertTrue(conf)


class FilterRuleTest(unittest.TestCase):
    def test_keeps_qualifying_recall(self):
        kept, rejects = ca.filter_records([rec()], TODAY)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejects, [])

    def test_drops_hazard_mismatch(self):
        kept, rejects = ca.filter_records(
            [rec(Hazards=[{"Name": "Choking"}])], TODAY
        )
        self.assertEqual(kept, [])
        self.assertEqual(rejects[0]["_reject_reason"], "hazard_mismatch")

    def test_drops_unlisted_firm_even_with_title(self):
        kept, rejects = ca.filter_records(
            [rec(Manufacturers=[], Importers=[])], TODAY
        )
        self.assertEqual(kept, [])
        self.assertEqual(rejects[0]["_reject_reason"], "no_listed_firm")

    def test_drops_small_and_unknown_units(self):
        kept, rejects = ca.filter_records(
            [
                rec(RecallID=11, Products=[{"NumberOfUnits": "About 251"}]),
                rec(RecallID=12, Products=[{"NumberOfUnits": "Millions"}]),
            ],
            TODAY,
        )
        self.assertEqual(kept, [])
        self.assertEqual(
            {r["_reject_reason"] for r in rejects},
            {"units_below_threshold", "units_unknown"},
        )

    def test_drops_bad_and_future_dates(self):
        kept, rejects = ca.filter_records(
            [
                rec(RecallID=21, RecallDate=""),
                rec(RecallID=22, RecallDate="2099-01-01T00:00:00"),
            ],
            TODAY,
        )
        self.assertEqual(kept, [])
        self.assertEqual(
            {r["_reject_reason"] for r in rejects},
            {"missing_or_bad_recall_date", "recall_date_in_future"},
        )


class PitWindowTest(unittest.TestCase):
    def test_entry_is_recall_plus_1_hold_40d(self):
        rows = ca.build_exclusion_rows([rec(RecallDate="2026-08-20T00:00:00")])
        self.assertEqual(rows[0]["recall_date"], "2026-08-20")
        self.assertEqual(rows[0]["entry_date"], "2026-08-21")
        self.assertEqual(rows[0]["window_end"], "2026-09-29")
        start = dt.date.fromisoformat(rows[0]["entry_date"])
        end = dt.date.fromisoformat(rows[0]["window_end"])
        self.assertEqual((end - start).days + 1, 40)

    def test_is_excluded_inclusive_bounds(self):
        start, end = dt.date(2026, 8, 21), dt.date(2026, 9, 29)
        self.assertTrue(ca.is_excluded(start, start, end))
        self.assertTrue(ca.is_excluded(end, start, end))
        self.assertFalse(ca.is_excluded(start - dt.timedelta(days=1), start, end))
        self.assertFalse(ca.is_excluded(end + dt.timedelta(days=1), start, end))


class SectorCapTest(unittest.TestCase):
    def _row(self, rid, industry, units):
        return {
            "recall_id": rid,
            "recall_date": "2026-01-01",
            "entry_date": "2026-01-02",
            "units": units,
            "ticker": f"T{rid}",
            "industry": industry,
        }

    def test_unknown_industry_exempt(self):
        rows = [self._row(1, None, 50000), self._row(2, "", 60000)]
        capped, dropped = ca.apply_sector_cap(rows, 0.05)
        self.assertEqual(len(capped), 2)
        self.assertEqual(dropped, [])

    def test_single_industry_capped_at_5pct_keeps_largest_units(self):
        rows = [self._row(i, "Consumer Discretionary", 10000 + i) for i in range(20)]
        rows.append(self._row(99, "Industrials", 15000))
        capped, dropped = ca.apply_sector_cap(rows, 0.05)
        # N_pre=21 -> max_allowed=max(1, floor(5%*21))=1 per industry.
        disc = [r for r in capped if r["industry"] == "Consumer Discretionary"]
        self.assertEqual(len(disc), 1)
        self.assertEqual(disc[0]["recall_id"], 19)  # largest units kept
        self.assertEqual(len(dropped), 19)
        self.assertTrue(all(r["_drop_reason"] == "sector_cap" for r in dropped))

    def test_param_respected(self):
        rows = [self._row(i, "Same", 10000 + i) for i in range(10)]
        capped, _ = ca.apply_sector_cap(rows, 0.5)
        self.assertEqual(len(capped), 5)  # floor(50% * 10)

    def test_invalid_pct_raises(self):
        with self.assertRaises(ValueError):
            ca.apply_sector_cap([self._row(1, "A", 10000)], 0)
        with self.assertRaises(ValueError):
            ca.apply_sector_cap([self._row(1, "A", 10000)], 1.5)


class SnapshotImmutabilityTest(unittest.TestCase):
    def test_same_pull_ts_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p1 = ca.write_snapshot(d, {"results": [1]}, pull_ts="20240101T000000Z")
            before = p1.read_bytes()
            p2 = ca.write_snapshot(d, {"results": [2]}, pull_ts="20240101T000000Z")
            self.assertNotEqual(p1, p2, "collision must create a new file, not overwrite")
            self.assertEqual(p1.read_bytes(), before, "original snapshot must be byte-identical")


class FullDumpPullerTest(unittest.TestCase):
    def test_single_get_full_dump_snapshot(self):
        records = ca.load_dump_response(FIX)
        payload_bytes = json.dumps(records).encode()
        seen_urls = []

        def fake_get(url):
            seen_urls.append(url)
            return payload_bytes

        with tempfile.TemporaryDirectory() as tmp:
            path, pulled = ca.pull_cpsc(Path(tmp), http_get=fake_get, pull_ts="20240101T000000Z")
            self.assertEqual(len(pulled), len(records))
            # Full-dump endpoint: one GET, pagination params ignored/absent.
            self.assertEqual(len(seen_urls), 1)
            self.assertNotIn("page_size", seen_urls[0])
            self.assertNotIn("offset", seen_urls[0])
            snap = ca.load_snapshot(path)
            self.assertIn("pulled_at", snap)
            self.assertEqual(snap["count"], len(records))
            self.assertEqual(len(snap["results"]), len(records))

    def test_load_dump_accepts_raw_list_shape(self):
        records = ca.load_dump_response(FIX)
        self.assertIsInstance(records, list)
        self.assertGreater(len(records), 0)
        self.assertIn("RecallID", records[0])


class OfflineFixtureBuildTest(unittest.TestCase):
    def test_end_to_end_from_committed_fixture(self):
        records = ca.load_dump_response(FIX)
        kept, rejects = ca.filter_records(records, TODAY)
        self.assertEqual(
            {r["RecallID"] for r in kept}, {90001, 90002, 90003}
        )
        self.assertEqual(
            {r["_reject_reason"] for r in rejects},
            {
                "units_below_threshold",  # 90004 fire but 251 units
                "hazard_mismatch",  # 90005 choking
                "no_listed_firm",  # 90006 title-only
                "units_unknown",  # 90007 "Millions"
                "recall_date_in_future",  # 90008
                "missing_or_bad_recall_date",  # 90009
            },
        )
        rows = ca.build_exclusion_rows(kept, pulled_at="fixture")
        by_id = {r["recall_id"]: r for r in rows}
        self.assertEqual(by_id[90001]["entry_date"], "2026-08-21")
        self.assertEqual(by_id[90001]["window_end"], "2026-09-29")
        self.assertEqual(by_id[90001]["resolution_method"], "importer")
        self.assertEqual(by_id[90001]["resolved_name"], "Goal Zero")
        # Join stub: unresolved until the alias table is wired.
        self.assertTrue(all(r["ticker"] is None for r in rows))

    def test_offline_refresh_rebuilds_exclusion_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snaps"
            out = Path(tmp) / "excl.jsonl"
            records = ca.load_dump_response(FIX)
            ca.write_snapshot(
                snap,
                {
                    "pulled_at": "2024-01-01T00:00:00+00:00",
                    "pull_ts": "t",
                    "endpoint": "fixture",
                    "note": "test",
                    "count": len(records),
                    "results": records,
                },
                pull_ts="20240101T000000Z",
            )
            summary = ca.refresh_cpsc_avoid(
                snap, out, offline=True, today=TODAY
            )
            self.assertEqual(summary["exclusions"], 3)
            lines = out.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
            first = json.loads(lines[0])
            for key in (
                "ticker", "cik", "recall_id", "recall_date", "reason",
                "window_end", "entry_date", "units", "resolution_method",
            ):
                self.assertIn(key, first)


if __name__ == "__main__":
    unittest.main()
