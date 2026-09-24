import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pipeline_monitor as pm


class PipelineMonitorTest(unittest.TestCase):
    def test_snapshot_counts_and_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / "run"; source = root / "source"
            run.mkdir(); source.mkdir()
            (source / "cases.jsonl").write_text("{}\n{}\n")
            (run / "extractions.jsonl").write_text("{}\n" * 4)
            (run / "pipeline_state.json").write_text(json.dumps(
                {"completed": {"cases": "now"}, "attempts": [{"stage": "extract"}]}))
            value = pm.snapshot(run, source)
            self.assertEqual(value["targets"]["extractions"], 10)
            self.assertEqual(value["artifacts"]["extractions"]["rows"], 4)
            self.assertEqual(value["active_stage"], "extract")


if __name__ == "__main__":
    unittest.main()
