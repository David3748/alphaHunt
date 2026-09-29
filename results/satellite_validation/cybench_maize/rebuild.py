"""Portable offline reconstruction from checksum-verified compact source inputs."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))
from satellite_cybench_extract import rebuild  # noqa: E402

if __name__ == "__main__":
    data = rebuild(HERE)
    print(f"Rebuilt {len(data):,} county-year feature rows; no outcomes scored.")
