import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import obscure_miner as om


class ObscureMinerTest(unittest.TestCase):
    def test_latest_seed_per_ticker_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            rows = [
                {"ticker": "AAA", "company": "Old", "cik": "1", "cutoff": "2020-01-01"},
                {"ticker": "AAA", "company": "New", "cik": "1", "cutoff": "2022-01-01"},
                {"ticker": "BBB", "company": "Bee", "cik": "2", "cutoff": "2021-01-01"},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            seeds = om.issuer_seeds(path, 2)
            self.assertEqual([row["ticker"] for row in seeds], ["AAA", "BBB"])
            self.assertEqual(seeds[0]["company"], "New")

    def test_inline_api_record_is_content_addressed_without_web_fetch(self):
        text = "material public record " * 30
        row = om.fetch_one({"url": "https://example.test/record/1", "inline_text": text})
        self.assertIsNone(row["fetch_error"])
        self.assertEqual(row["text"], text.strip())
        self.assertEqual(len(row["content_hash"]), 64)


if __name__ == "__main__":
    unittest.main()
