import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import comprehensive_lab as cl
import ox_lab as ox
import temporal_store as ts


class ComprehensiveLabTest(unittest.TestCase):
    def test_quote_grounding_normalizes_whitespace(self):
        self.assertTrue(cl.quote_is_grounded("cash and cash equivalents", "Cash  and\n cash equivalents were $10."))
        self.assertFalse(cl.quote_is_grounded("invented assertion", "Cash was $10."))
        self.assertFalse(cl.quote_is_grounded("too short", "too short"))

    def test_role_excerpt_is_bounded_and_keeps_role_evidence(self):
        text = "HEADER\n" + ("ordinary filler " * 30_000) + "material weakness in internal control"
        excerpt = cl.role_excerpt(text, "accounting", max_chars=20_000, radius=1_000)
        self.assertLessEqual(len(excerpt.replace("\n\n[...section boundary...]\n\n", "")), 20_000)
        self.assertIn("material weakness", excerpt)
        self.assertTrue(excerpt.startswith("HEADER"))

    def test_case_build_is_stable_and_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            run = root / "run"
            source.mkdir()
            ox.append_jsonl(source / "cases.jsonl", {
                "case_id": "old", "ticker": "T", "cutoff": "2024-01-01",
                "snapshot_text": "filing", "anchor_form": "10-K",
            })
            first = cl.load_cases(run, [source])
            second = cl.load_cases(run, [source])
            self.assertEqual(first, second)
            self.assertNotEqual(first[0]["case_id"], "old")
            self.assertEqual(first[0]["source_case_id"], "old")

    def test_ingest_is_bitemporal_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            with ts.TemporalStore(root / "test.duckdb", root / "blobs") as store:
                source_experiment = store.register_experiment("source", "v1", "2024-01-03", {})
                security, _ = store.append_security("1", "T", "2024-01-03")
                content = store.put_content("The company had $10 million in cash.", recorded_at="2024-01-03")
                document, version = store.append_document(
                    "sec", "accession", content, "2024-01-01", "2024-01-02", "2024-01-03",
                    issuer_id="1")
                source_manifest = store.create_manifest(
                    "2024-01-02", "2024-01-03",
                    [{"item_type": "document", "logical_id": document, "version_id": version}])
                store.register_case("source-case", source_experiment, security, "2024-01-02",
                                    source_manifest, "2024-01-03", "2024-04-01")
                case_id = "comprehensive-case"
                ox.append_jsonl(run / "cases.jsonl", {
                    "case_id": case_id, "source_case_id": "source-case", "source_run_dir": "source",
                    "cik": "1", "ticker": "T", "cutoff": "2024-01-02",
                    "horizon_end": "2024-04-01", "snapshot_text": "The company had $10 million in cash.",
                    "returns": {"relative_return_90d": 0.25},
                })
                ox.append_jsonl(run / "extractions.jsonl", {
                    "case_id": case_id, "role": "liquidity", "replicate": 1,
                    "model": "test", "generated_at": "2024-01-03", "result": {"claims": [{
                        "semantic_key": "cash", "claim_type": "liquidity", "direction": "bullish",
                        "value_text": "$10 million cash", "effective_date": None, "source": "10-K",
                        "quote": "The company had $10 million in cash.", "materiality": 4,
                        "confidence_pct": 90, "quote_grounded": True,
                    }]},
                })
                ox.append_jsonl(run / "syntheses.jsonl", {
                    "case_id": case_id, "replicate": 1, "model": "test",
                    "generated_at": "2024-01-03", "result": {
                        "probability_plus20_excess_90d_pct": 60, "decision": "watch",
                    },
                })
                first = cl.ingest(store, run, "2024-05-01")
                second = cl.ingest(store, run, "2024-06-01")
                self.assertEqual(first["experiment_id"], second["experiment_id"])
                summary = store.summary()
                self.assertEqual(summary["claim_versions"], 1)
                self.assertEqual(summary["analysis_runs"], 1)
                self.assertEqual(summary["prediction_runs"], 1)
                self.assertEqual(store.validate_integrity(), [])


if __name__ == "__main__":
    unittest.main()
