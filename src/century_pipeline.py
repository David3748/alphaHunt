#!/usr/bin/env python3
"""Resumable one-command orchestration for the century-scale safety dataset."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "century.json"
STAGES = ("enumerate", "resolve", "preprice", "cases", "extract", "synthesize",
          "evaluate", "archive")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"structured_start_year", "end_year", "run_dir", "source_dir",
                "archive_dir", "model"}
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"missing config keys: {missing}")
    if int(config["structured_start_year"]) > int(config["end_year"]):
        raise ValueError("structured_start_year must not exceed end_year")
    return config


def absolute(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def state_path(run_dir: Path) -> Path:
    return run_dir / "pipeline_state.json"


def load_state(run_dir: Path) -> dict:
    path = state_path(run_dir)
    return json.loads(path.read_text()) if path.exists() else {"completed": {}, "attempts": []}


def save_state(run_dir: Path, state: dict) -> None:
    path = state_path(run_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_command(command: list[str], env: dict | None = None) -> None:
    printable = " ".join(command)
    print(f"\n>>> {printable}", file=sys.stderr, flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_commands(config: dict) -> dict[str, list[str]]:
    python = sys.executable
    run_dir = absolute(config["run_dir"])
    source_dir = absolute(config["source_dir"])
    start, end = str(config["structured_start_year"]), str(config["end_year"])
    common = [python, "src/sealed_safety.py", "--run-dir", str(run_dir),
              "--start-year", start, "--end-year", end]
    model_common = [python, "src/comprehensive_lab.py", "--run-dir", str(run_dir),
                    "--source-dir", str(source_dir)]
    return {
        "enumerate": common + ["enumerate"],
        "resolve": common + ["resolve-symbols", "--concurrency",
                              str(config.get("resolve_concurrency", 12))],
        "preprice": common + ["preprice", "--concurrency",
                               str(config.get("price_concurrency", 24))],
        "cases": common + ["build-cases", "--source-dir", str(source_dir),
                            "--concurrency", str(config.get("filing_concurrency", 8))],
        "extract": model_common + ["extract", "--model", config["model"],
                                    "--concurrency", str(config.get("llm_concurrency", 256)),
                                    "--replicates", str(config.get("extraction_replicates", 1))],
        "synthesize": model_common + ["synthesize", "--model", config["model"],
                                       "--concurrency", str(config.get("llm_concurrency", 256)),
                                       "--replicates", str(config.get("synthesis_replicates", 2))],
        "evaluate": [python, "src/sealed_safety_eval.py", "--run-dir", str(run_dir),
                     "--source-dir", str(source_dir), "--concurrency",
                     str(config.get("outcome_concurrency", 24)), "--placebo-draws",
                     str(config.get("placebo_draws", 250))],
    }


def verify_stage(stage: str, config: dict) -> dict:
    run_dir, source_dir = absolute(config["run_dir"]), absolute(config["source_dir"])
    def lines(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    counts = {
        "filings": lines(run_dir / "filings.jsonl"),
        "symbols": lines(run_dir / "symbols.jsonl"),
        "preprice": lines(run_dir / "preprice.jsonl"),
        "cases": lines(source_dir / "cases.jsonl"),
        "extractions": lines(run_dir / "extractions.jsonl"),
        "syntheses": lines(run_dir / "syntheses.jsonl"),
    }
    if stage == "enumerate" and not counts["filings"]:
        raise RuntimeError("enumeration produced no filings")
    if stage == "cases" and not (source_dir / "case_build_status.json").exists() \
            and not (run_dir / "case_build_status.json").exists():
        raise RuntimeError("case build status is missing")
    if stage == "extract" and counts["extractions"] != counts["cases"] * 5 * int(config.get("extraction_replicates", 1)):
        raise RuntimeError(f"incomplete extraction count: {counts}")
    if stage == "synthesize" and counts["syntheses"] != counts["cases"] * int(config.get("synthesis_replicates", 2)):
        raise RuntimeError(f"incomplete synthesis count: {counts}")
    if stage == "evaluate":
        result_path = run_dir / "results.json"
        if not result_path.exists():
            raise RuntimeError("evaluation result is missing")
        counts["validated"] = json.loads(result_path.read_text()).get("validated")
    return counts


def archive(config: dict) -> dict:
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd is required for archival")
    run_dir, source_dir = absolute(config["run_dir"]), absolute(config["source_dir"])
    archive_dir = absolute(config["archive_dir"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        run_dir / "filings.jsonl", run_dir / "symbols.jsonl", run_dir / "preprice.jsonl",
        source_dir / "cases.jsonl", run_dir / "cases.jsonl", run_dir / "extractions.jsonl",
        run_dir / "syntheses.jsonl", run_dir / "results.json",
        run_dir / "sealed_daily_portfolio.csv", run_dir / "report.md",
        run_dir / "PROTOCOL.md", state_path(run_dir),
    ]
    manifest = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "files": []}
    level = str(config.get("zstd_level", 6))
    for source in candidates:
        if not source.exists():
            continue
        relative = source.relative_to(ROOT)
        target = archive_dir / (str(relative).replace("/", "__") + ".zst")
        run_command(["zstd", "-q", "-T0", f"-{level}", "-f", str(source), "-o", str(target)])
        manifest["files"].append({"source": str(relative), "archive": target.name,
                                  "source_bytes": source.stat().st_size,
                                  "archive_bytes": target.stat().st_size,
                                  "sha256": sha256(target)})
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    remote = str(config.get("drive_remote") or "").strip()
    if remote:
        if shutil.which("rclone") is None:
            raise RuntimeError("drive_remote is set but rclone is not installed")
        run_command(["rclone", "copy", str(archive_dir), remote, "--checksum",
                     "--transfers", "8", "--checkers", "16"])
    return {"files": len(manifest["files"]),
            "source_bytes": sum(row["source_bytes"] for row in manifest["files"]),
            "archive_bytes": sum(row["archive_bytes"] for row in manifest["files"]),
            "remote": remote or None}


def preflight(config: dict, require_key: bool = True) -> dict:
    required_commands = ["zstd"]
    if config.get("drive_remote"):
        required_commands.append("rclone")
    missing = [name for name in required_commands if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing commands: {missing}")
    if require_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is not set; supply it through the environment or cloud secret store")
    free = shutil.disk_usage(ROOT).free
    return {"python": sys.version.split()[0], "free_disk_gb": round(free / 2**30, 2),
            "drive_enabled": bool(config.get("drive_remote")),
            "structured_period": [config["structured_start_year"], config["end_year"]],
            "requested_period": [config.get("requested_start_year"), config["end_year"]]}


def freeze_protocol(config: dict) -> Path:
    run_dir = absolute(config["run_dir"])
    path = run_dir / "PROTOCOL.md"
    if path.exists():
        return path
    start, end = config["structured_start_year"], config["end_year"]
    text = f"""# Frozen century safety protocol

Frozen before model scoring and post-signal outcomes are opened.

- Structured SEC population: every detailed 10-K, 10-Q, 20-F, and 40-F from {start}–{end}.
- Eligibility uses only filing-native identifiers and pre-signal data: at least 120 closes,
  price >= $1, 30-session ADV >= $1M, drawdown from the prior 370-day high <= -40%,
  and a 180-day issuer cooldown. Future price availability is not an eligibility condition.
- Evidence is the complete anchor primary filing available at its SEC acceptance time.
- Five quote-grounded reusable extraction lenses, one replicate each; two independent
  safety syntheses. Ranking score is negative mean downside-tail probability.
- Causal selection: first 50 scores are warmup, then accept at or above the expanding
  90th percentile of strictly prior scores at each timestamp.
- Execution: next eligible close, 10% NAV per position, maximum 10 positions, 90-calendar-day
  hold, 25 bp per side, idle cash at 13-week Treasury yield, SPY exposure-matched benchmark.
- Missing terminal histories: conservative mark to zero and optimistic carry-forward bounds.
- Locked conservative checks: at least 30 executed trades, T-bill Sharpe > 1, matched IR > 0.5,
  max drawdown better than -25%, positive matched excess in at least 60% of signal years
  (all years when the run has two or fewer), one-sided random-score placebo p < 0.05,
  and no signal month contributing more than half of total excess profit.
- Report all annual cohorts, 30/60/90/120/180-day holds, 0/1/4-session delays,
  10/25/50 bp costs, block/trade bootstraps, and {config.get('placebo_draws', 250)}
  random-score placebos. Market-relative performance and bootstrap/HAC skill probability
  are primary; the random-score placebo is a secondary ranking sanity check.

The 2001–2008 pre-XBRL period is a separately identified coverage gap until a historical
security master can link issuers to delisted symbols without using future information.
"""
    path.write_text(text, encoding="utf-8")
    return path


def play(config: dict, from_stage: str | None = None, through_stage: str | None = None) -> dict:
    run_dir = absolute(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    absolute(config["source_dir"]).mkdir(parents=True, exist_ok=True)
    freeze_protocol(config)
    state = load_state(run_dir)
    commands = stage_commands(config)
    start_index = STAGES.index(from_stage) if from_stage else 0
    end_index = STAGES.index(through_stage) if through_stage else len(STAGES) - 1
    env = dict(os.environ)
    for stage in STAGES[start_index:end_index + 1]:
        if state["completed"].get(stage):
            print(f">>> skip completed stage: {stage}", file=sys.stderr)
            continue
        attempt = {"stage": stage, "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        state["attempts"].append(attempt)
        save_state(run_dir, state)
        try:
            result = archive(config) if stage == "archive" else (
                run_command(commands[stage], env=env) or verify_stage(stage, config))
            attempt["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            attempt["result"] = result
            state["completed"][stage] = attempt["finished_at"]
            save_state(run_dir, state)
        except Exception as exc:
            attempt["failed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            save_state(run_dir, state)
            raise
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    play_parser = sub.add_parser("play")
    play_parser.add_argument("--from-stage", choices=STAGES)
    play_parser.add_argument("--through-stage", choices=STAGES)
    sub.add_parser("status")
    sub.add_parser("archive")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "preflight":
        result = preflight(config)
    elif args.command == "play":
        preflight(config)
        result = play(config, args.from_stage, args.through_stage)
    elif args.command == "archive":
        result = archive(config)
    else:
        result = {"preflight": preflight(config, require_key=False),
                  "state": load_state(absolute(config["run_dir"])),
                  "counts": verify_stage("status", config)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
