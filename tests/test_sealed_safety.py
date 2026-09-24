import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import sealed_safety as ss
import sealed_safety_eval as se
import sealed_safety_ingest as si


class SealedSafetyTest(unittest.TestCase):
    def test_plain_xbrl_symbol(self):
        raw = b'<dei:TradingSymbol contextRef="c">OLD</dei:TradingSymbol>'
        self.assertEqual(ss.facts(raw, "TradingSymbol"), ["OLD"])

    def test_inline_xbrl_symbol_with_nested_tag(self):
        raw = (b'<ix:nonNumeric name="dei:TradingSymbol" contextRef="c">'
               b'<span>ABC</span></ix:nonNumeric>')
        self.assertEqual(ss.facts(raw, "TradingSymbol"), ["ABC"])

    def test_fact_values_are_deduplicated(self):
        raw = (b'<dei:TradingSymbol contextRef="a">ABC</dei:TradingSymbol>'
               b'<dei:TradingSymbol contextRef="b">ABC</dei:TradingSymbol>')
        self.assertEqual(ss.facts(raw, "TradingSymbol"), ["ABC"])

    def test_instance_hint_detects_symbol_stem(self):
        self.assertEqual(ss.instance_hint("amd-20181229.xml"), "amd")
        self.assertEqual(ss.instance_hint("old_10q_2019.xml"), "old")

    def test_causal_selector_excludes_same_timestamp_scores(self):
        rows = [
            {"case_id": "a", "accepted": "1", "score": 0.1},
            {"case_id": "b", "accepted": "2", "score": 0.2},
            {"case_id": "c", "accepted": "2", "score": 1.0},
            {"case_id": "d", "accepted": "3", "score": 0.3},
        ]
        chosen = se.causal_select(rows, warmup=1, fraction=.5)
        self.assertEqual([row["case_id"] for row in chosen], ["b", "c", "d"])
        self.assertEqual(chosen[0]["prior_score_count"], 1)

    def test_source_knowledge_time_prefers_exact_acceptance(self):
        case = {"cutoff": "2020-01-02", "snapshot_sources": [
            {"acceptanceDateTime": "2020-01-02T15:30:00Z"}]}
        self.assertEqual(si.source_knowledge_time(case).isoformat(),
                         "2020-01-02T15:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
