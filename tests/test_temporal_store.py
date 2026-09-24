"""Tests for bitemporal invariants and point-in-time leakage guards."""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import temporal_store as ts


class TemporalStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ts.TemporalStore(root / "test.duckdb", root / "blobs")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_document(self, content, available, system, source_key="doc"):
        content_hash = self.store.put_content(content, recorded_at=system)
        return self.store.append_document(
            "test", source_key, content_hash, "2024-01-01", available, system,
            issuer_id="issuer-1", document_type="filing")

    def test_document_revision_preserves_old_system_view(self):
        _, v1 = self.add_document("original", "2024-01-02", "2024-01-03")
        _, v2 = self.add_document("corrected", "2024-01-02", "2024-01-05")
        old = self.store.documents_as_of("issuer-1", "2024-01-10", "2024-01-04")
        new = self.store.documents_as_of("issuer-1", "2024-01-10", "2024-01-06")
        self.assertEqual([row["version_id"] for row in old], [v1])
        self.assertEqual([row["version_id"] for row in new], [v2])

    def test_manifest_rejects_future_document(self):
        document_id, version_id = self.add_document(
            "future", "2024-02-01", "2024-01-02", "future-doc")
        with self.assertRaisesRegex(ValueError, "lookahead"):
            self.store.create_manifest(
                "2024-01-15", "2024-01-03",
                [{"item_type": "document", "logical_id": document_id,
                  "version_id": version_id}])

    def test_claim_query_honors_valid_available_and_system_time(self):
        claim_id, claim_version = self.store.append_claim(
            "issuer-1", "liquidity", "cash", {"usd": 10}, "2024-01-01",
            "2024-01-10", "2024-01-11", "extractor", "v1",
            effective_to="2024-04-01")
        before_public = self.store.claims_as_of(
            "issuer-1", "2024-02-01", "2024-01-09", "2024-02-01")
        visible = self.store.claims_as_of(
            "issuer-1", "2024-02-01", "2024-01-10", "2024-02-01")
        expired = self.store.claims_as_of(
            "issuer-1", "2024-05-01", "2024-05-01", "2024-05-01")
        self.assertEqual(before_public, [])
        self.assertEqual(len(visible), 1)
        self.assertEqual(expired, [])
        with self.assertRaisesRegex(ValueError, "lookahead claim"):
            self.store.create_manifest(
                "2024-01-09", "2024-02-01",
                [{"item_type": "claim", "logical_id": claim_id,
                  "version_id": claim_version}])

    def test_prediction_must_use_frozen_case_manifest(self):
        experiment = self.store.register_experiment(
            "test", "v1", "2024-01-03", {}, status="test")
        security, _ = self.store.append_security("issuer-1", "TEST", "2024-01-03")
        document_id, version_id = self.add_document("known", "2024-01-02", "2024-01-03")
        manifest = self.store.create_manifest(
            "2024-01-02", "2024-01-03",
            [{"item_type": "document", "logical_id": document_id,
              "version_id": version_id}], created_at="2024-01-03")
        self.store.register_case(
            "case-1", experiment, security, "2024-01-02", manifest,
            "2024-01-03", "2024-04-01")
        prediction = self.store.insert_prediction(
            experiment_id=experiment, case_id="case-1", task="test", horizon_days=90,
            prediction_as_of="2024-01-02", system_as_of="2024-01-03",
            recorded_at="2024-01-03", manifest_id=manifest, model="test",
            probability=0.4, output={"probability": 0.4})
        self.assertTrue(prediction)
        with self.assertRaisesRegex(ValueError, "as-of"):
            self.store.insert_prediction(
                experiment_id=experiment, case_id="case-1", task="test", horizon_days=90,
                prediction_as_of="2024-01-04", system_as_of="2024-01-03",
                recorded_at="2024-01-03", manifest_id=manifest, model="test",
                probability=0.4, output={"probability": 0.4})

    def test_integrity_validator_and_blob_hash(self):
        self.add_document("known", "2024-01-02", "2024-01-03")
        self.assertEqual(self.store.validate_integrity(), [])

    def test_backtest_inputs_obey_system_and_outcome_availability(self):
        experiment = self.store.register_experiment(
            "test", "v1", "2024-01-03", {}, status="test")
        security, _ = self.store.append_security("issuer-1", "TEST", "2024-01-03")
        document_id, version_id = self.add_document("known", "2024-01-02", "2024-01-03")
        manifest = self.store.create_manifest(
            "2024-01-02", "2024-01-03",
            [{"item_type": "document", "logical_id": document_id,
              "version_id": version_id}], created_at="2024-01-03")
        self.store.register_case(
            "case-1", experiment, security, "2024-01-02", manifest,
            "2024-01-03", "2024-04-01", split="validation")
        self.store.insert_prediction(
            experiment_id=experiment, case_id="case-1", task="test", horizon_days=90,
            prediction_as_of="2024-01-02", system_as_of="2024-01-03",
            recorded_at="2024-01-03", manifest_id=manifest, model="test",
            probability=0.8, output={"probability": 0.8})
        self.store.append_outcome(
            case_id="case-1", label_name="test", value={"label": 1},
            horizon_start="2024-01-02", horizon_end="2024-04-01",
            available_at="2024-04-02", system_from="2024-04-03", source="test")
        self.assertEqual(self.store.backtest_inputs(
            experiment, "2024-04-03", "2024-04-01", "validation"), [])
        rows = self.store.backtest_inputs(
            experiment, "2024-04-03", "2024-04-02", "validation")
        self.assertEqual(len(rows), 1)
        backtest_id = self.store.record_backtest(
            experiment, "2024-04-03", "2024-04-02", "point_in_time",
            {"entry": "next_close"}, {"count": 1}, "2024-04-03",
            split="validation")
        self.assertTrue(backtest_id)
        self.assertEqual(self.store.summary()["backtest_runs"], 1)


if __name__ == "__main__":
    unittest.main()
