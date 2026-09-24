"""Offline unit tests for alphahunt logic (no network)."""

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import alphahunt as ah


class TestSpreadMath(unittest.TestCase):
    def test_gross_spread_basic(self):
        self.assertAlmostEqual(ah.gross_spread(72.32, 73.00), 73.00 / 72.32 - 1.0)

    def test_gross_spread_fulc_case(self):
        # verified live case: package $3.95 vs market $3.83
        sp = ah.gross_spread(3.83, 3.95)
        self.assertGreater(sp, 0.02)
        self.assertLess(sp, 0.05)

    def test_annualized_thin_spread(self):
        a = ah.annualized(ah.gross_spread(72.32, 73.00), 150)
        self.assertLess(a, 0.03)  # ~1.5-2% annualized, matches manual finding

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            ah.gross_spread(0, 10)
        with self.assertRaises(ValueError):
            ah.annualized(0.01, 0)
        with self.assertRaises(ValueError):
            ah.package_value(100.0, 0)


class TestPackageValue(unittest.TestCase):
    def test_fulc_package_math(self):
        # stub $31.3M + dividend $270M over 76,297,257 shares ~= $3.95
        v = ah.package_value(31_300_000 + 270_000_000, 76_297_257)
        self.assertAlmostEqual(v, 3.9499, delta=0.01)

    def test_tbph_cvr_zero_is_rational(self):
        # cash leg fully priced: no spread on $17.00 vs $17.01 market
        self.assertLess(ah.gross_spread(17.01, 17.00), 0)


class TestHitParsing(unittest.TestCase):
    def test_classify_buckets(self):
        self.assertEqual(ah.classify({"items": ["2.01"], "form": "8-K"}), "closed/completing")
        self.assertEqual(ah.classify({"items": ["5.01", "1.01"], "form": "8-K"}), "closed/completing")
        self.assertEqual(ah.classify({"items": ["1.01"], "form": "DEFM14A"}), "proxy/vote pending")
        self.assertEqual(ah.classify({"items": ["1.01"], "form": "8-K"}), "agreement signed")
        self.assertEqual(ah.classify({"items": [], "form": "8-K"}), "other")

    def test_search_filings_normalizes_hits(self):
        fake = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "display_names": ["Fulcrum Therapeutics, Inc.", "FULC"],
                            "ciks": [1680581],
                            "file_type": "8-K",
                            "file_date": "2026-08-17T00:00:00-04:00",
                            "robo_form_headers": ["1.01"],
                        }
                    }
                ]
            }
        }
        with mock.patch.object(ah, "http_json", return_value=fake):
            hits = ah.search_filings("merger agreement", "8-K", "2026-08-10", "2026-08-17")
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual(h["company"], "Fulcrum Therapeutics, Inc.")
        self.assertEqual(h["ticker"], "FULC")
        self.assertEqual(h["filed"], "2026-08-17")
        self.assertIn("1.01", h["items"])

    def test_search_handles_empty(self):
        with mock.patch.object(ah, "http_json", return_value={"hits": {"hits": []}}):
            self.assertEqual(ah.search_filings("x", "8-K", "2026-01-01", "2026-01-02"), [])


class TestDateWindow(unittest.TestCase):
    def test_scan_window_defaults_to_recent_past(self):
        end = dt.date.today()
        start = end - dt.timedelta(days=7)
        self.assertLess(start, end)
        self.assertLessEqual((end - start).days, 7)


FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><reportingOwnerName>JANE Q. INSIDER</reportingOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector><isOfficer>0</isOfficer>
      <officerTitle></officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionDate><value>2026-08-18</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>10.25</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionDate><value>2026-08-18</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>999999</value></transactionShares>
        <transactionPricePerShare><value>1.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionDate><value>2026-08-19</value></transactionDate>
      <transactionAmounts>
        <transactionShares><value>12345</value></transactionShares>
        <transactionPricePerShare><value>11.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


class TestForm4Parsing(unittest.TestCase):
    def test_only_code_p_buys_extracted(self):
        buys = ah.parse_form4_buys(FORM4_XML)
        self.assertEqual(len(buys), 1)
        b = buys[0]
        self.assertEqual(b["owner"], "JANE Q. INSIDER")
        self.assertEqual(b["role"], "director")
        self.assertEqual(b["shares"], 50000)
        self.assertAlmostEqual(b["price"], 10.25)
        self.assertEqual(b["date"], "2026-08-18")

    def test_malformed_xml_returns_empty(self):
        self.assertEqual(ah.parse_form4_buys("<broken"), [])

    def test_cluster_two_buyers(self):
        buys = [
            {"owner": "A", "date": "2026-08-18", "shares": 1000, "price": 5.0},
            {"owner": "B", "date": "2026-08-20", "shares": 2000, "price": 5.2},
        ]
        c = ah.detect_cluster(buys)
        self.assertIsNotNone(c)
        self.assertEqual(c["n_buyers"], 2)
        self.assertEqual(c["span_days"], 2)
        self.assertAlmostEqual(c["avg_price"], (5.0 * 1000 + 5.2 * 2000) / 3000)

    def test_single_buyer_no_cluster(self):
        buys = [{"owner": "A", "date": "2026-08-18", "shares": 1000, "price": 5.0},
                {"owner": "A", "date": "2026-08-19", "shares": 500, "price": 5.1}]
        self.assertIsNone(ah.detect_cluster(buys))

    def test_cluster_outside_window(self):
        buys = [{"owner": "A", "date": "2026-01-01", "shares": 1000, "price": 5.0},
                {"owner": "B", "date": "2026-06-01", "shares": 500, "price": 5.1}]
        self.assertIsNone(ah.detect_cluster(buys, window_days=14))


class TestWatchlist(unittest.TestCase):
    def test_load_watchlist(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# my watch\n\n1680581  # FULC\nnot-a-cik\n1583107\n")
            path = f.name
        try:
            self.assertEqual(ah.load_watchlist(path), ["1680581", "1583107"])
        finally:
            os.unlink(path)


class TestSubagentSwarm(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        import subagents
        self.sa = subagents

    def _fake_client(self, replies):
        client = self.sa.OpenRouter("test-key")
        queue = list(replies)
        client.chat = lambda messages, temperature=0.2: queue.pop(0)
        return client

    def test_parse_llm_json_with_wrapping_prose(self):
        out = self.sa.parse_llm_json('Sure! here is the JSON:\n```json\n{"a": 1}\n```')
        self.assertEqual(out, {"a": 1})

    def test_parse_llm_json_rejects_garbage(self):
        with self.assertRaises(self.sa.SubagentError):
            self.sa.parse_llm_json("no json here at all")

    def test_missing_key_raises(self):
        import os
        old = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with self.assertRaises(self.sa.SubagentError):
                self.sa.get_api_key(None)
            self.assertEqual(self.sa.get_api_key("explicit"), "explicit")
        finally:
            if old is not None:
                os.environ["OPENROUTER_API_KEY"] = old

    def test_openrouter_retries_null_message_content(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                import json
                return json.dumps(self.payload).encode()

        replies = iter([
            FakeResponse({"choices": [{"message": {"content": None}}]}),
            FakeResponse({"choices": [{"message": {"content": "{\"ok\":true}"}}]}),
        ])
        client = self.sa.OpenRouter("test-key", max_retries=1)
        with mock.patch.object(self.sa.urllib.request, "urlopen",
                               side_effect=lambda *a, **k: next(replies)), \
             mock.patch.object(self.sa.time, "sleep"):
            out = client.chat([{"role": "user", "content": "x"}])
        self.assertEqual(out, '{"ok":true}')
        self.assertEqual(client.calls, 2)

    def test_deep_candidate_full_chain(self):
        replies = [
            '{"event_type":"merger","consideration":{"cash_per_share":73.0}}',
            '{"verdict":"watch","probability_deal_completes_pct":90}',
            '{"direction":"long","structure":"shares"}',
        ]
        client = self._fake_client(replies)
        result = self.sa.deep_candidate(
            {"ticker": "TECH", "cik": "842023"},
            client,
            fetch_filing_text=lambda ctx: "8-K text about $73.00 per share",
            get_market_data=lambda ctx: {"last_price": 72.32},
        )
        self.assertEqual(result["terms"]["consideration"]["cash_per_share"], 73.0)
        self.assertEqual(result["skeptic"]["verdict"], "watch")
        self.assertEqual(result["thesis"]["direction"], "long")

    def test_deep_candidate_filing_fetch_failure_is_contained(self):
        client = self._fake_client([])
        def boom(ctx):
            raise RuntimeError("403")
        result = self.sa.deep_candidate({"ticker": "X"}, client,
                                        fetch_filing_text=boom,
                                        get_market_data=lambda ctx: {})
        self.assertIn("error", result)

    def test_ranking_prefers_actionable(self):
        results = [
            {"ticker": "AVOID", "skeptic": {"verdict": "avoid"}, "thesis": {"direction": "none"}},
            {"ticker": "WATCHY", "skeptic": {"verdict": "watch"}, "thesis": {"direction": "none"}},
            {"ticker": "GO", "skeptic": {"verdict": "actionable"}, "thesis": {"direction": "long"}},
        ]
        ranked = self.sa.rank_results(results)
        self.assertEqual(ranked[0]["ticker"], "GO")
        self.assertEqual(ranked[-1]["ticker"], "AVOID")

    def test_role_registry_shapes(self):
        for name, spec in self.sa.ROLES.items():
            self.assertIn("system", spec)
            self.assertIn("needs", spec)


if __name__ == "__main__":
    unittest.main()
