import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import century_pipeline as cp


class CenturyPipelineTest(unittest.TestCase):
    def test_load_config_rejects_reversed_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"structured_start_year": 2025, "end_year": 2009,
                                        "run_dir": "r", "source_dir": "s",
                                        "archive_dir": "a", "model": "m"}))
            with self.assertRaises(ValueError):
                cp.load_config(path)

    def test_stage_commands_preserve_replication_and_concurrency(self):
        config = cp.load_config(cp.DEFAULT_CONFIG)
        commands = cp.stage_commands(config)
        self.assertIn("256", commands["extract"])
        self.assertEqual(commands["extract"][-1], "1")
        self.assertEqual(commands["synthesize"][-1], "2")
        self.assertIn("2009", commands["enumerate"])
        self.assertIn("2025", commands["enumerate"])

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = {"completed": {"enumerate": "now"}, "attempts": []}
            cp.save_state(root, value)
            self.assertEqual(cp.load_state(root), value)


if __name__ == "__main__":
    unittest.main()
