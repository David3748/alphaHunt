"""Offline tests for the historical Ox forecasting lab."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ox_lab as lab


class TestEvidencePacking(unittest.TestCase):
    def test_llm_cache_retries_malformed_tool_arguments(self):
        class FakeClient:
            model = "test/model"

            def __init__(self):
                self.replies = iter(['{"quote":"an "unescaped" quote"}', '{"ok":true}'])
                self.calls = 0

            def chat(self, *args, **kwargs):
                self.calls += 1
                return next(self.replies)

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            result = lab.LLMCache(Path(tmp)).call_json(
                client, "system", "user", "submit", {"type": "object"})
            self.assertEqual(result, {"ok": True})
            self.assertEqual(client.calls, 2)

    def test_load_jsonl_does_not_split_unicode_line_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text('{"case_id":"a","text":"before\u2028after"}\n')
            rows = lab.load_jsonl(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["text"], "before\u2028after")

    def test_relevant_excerpt_keeps_financing_window(self):
        text = "HEADER\n" + ("ordinary operations " * 2000) + "substantial doubt about liquidity and an ATM offering" + (" tail" * 2000)
        excerpt = lab.relevant_excerpt(text, max_chars=3000, radius=500)
        self.assertIn("substantial doubt", excerpt)
        self.assertLessEqual(len(excerpt), 3000)

    def test_redaction_removes_identifiers(self):
        text = "Acme Holdings Inc. (ACME), CIK 123456, says ACME needs capital."
        out = lab.redact_issuer(text, ["Acme Holdings Inc.", "ACME", "123456"])
        self.assertNotIn("Acme", out)
        self.assertNotIn("ACME", out)
        self.assertNotIn("123456", out)
        self.assertIn("[ISSUER]", out)

    def test_redaction_handles_legal_name_variants(self):
        out = lab.redact_issuer("The Boston Beer Company, Inc. reported results.",
                                ["BOSTON BEER CO INC"])
        self.assertNotIn("Boston Beer", out)

    def test_normalizes_ox_forecast_shape(self):
        raw = {
            "forecast": {"probability_material_dilutive_financing_90d": 0.22},
            "rationale": "Cash burn but adequate liquidity.",
            "evidence_for": ["The filing says 'we may issue additional common stock' if needed."],
            "evidence_against": ["No debt maturities."],
        }
        out = lab.normalize_forecast_result(raw)
        self.assertEqual(out["probability_pct"], 22)
        self.assertEqual(out["risk_band"], "low")
        self.assertEqual(out["evidence"][0]["quote"], "we may issue additional common stock")

    def test_column_rows_rejects_misaligned_shape_safely(self):
        columns = {
            "form": ["10-Q", "8-K"],
            "filingDate": ["2024-01-01"],
            "accessionNumber": ["1", "2"],
            "primaryDocument": ["a.htm", "b.htm"],
        }
        rows = lab.column_rows(columns)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["form"], "10-Q")


class TestScoring(unittest.TestCase):
    def test_auc(self):
        self.assertEqual(lab.auc_score([(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]), 1.0)
        self.assertEqual(lab.auc_score([(0.5, 1), (0.5, 0)]), 0.5)

    def test_score_run_builds_metrics_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                {"case_id": "a", "ticker": "AAA", "company": "A", "cutoff": "2024-01-01",
                 "returns": {"relative_return_90d": -0.2}},
                {"case_id": "b", "ticker": "BBB", "company": "B", "cutoff": "2024-01-01",
                 "returns": {"relative_return_90d": 0.1}},
            ]
            forecasts = [
                {"case_id": "a", "replicate": 1, "result": {"probability_pct": 80, "predicted_route": "atm"}},
                {"case_id": "b", "replicate": 1, "result": {"probability_pct": 20, "predicted_route": "none"}},
            ]
            outcomes = [
                {"case_id": "a", "replicate": 1, "result": {"label": "yes", "financing_route": "atm"}},
                {"case_id": "b", "replicate": 1, "result": {"label": "no", "financing_route": "none"}},
            ]
            for name, rows in (("cases.jsonl", cases), ("forecasts.jsonl", forecasts), ("outcomes.jsonl", outcomes)):
                (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
            metrics = lab.score_run(root)
            self.assertEqual(metrics["cases_resolved"], 2)
            self.assertEqual(metrics["auc"], 1.0)
            self.assertAlmostEqual(metrics["brier"], 0.04)
            self.assertTrue((root / "report.md").exists())
            self.assertTrue((root / "metrics.json").exists())


if __name__ == "__main__":
    unittest.main()
