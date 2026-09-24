#!/usr/bin/env python3
"""Reproducible post-outcome evaluation of locked century hypotheses."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

import sealed_safety_eval as ev


FIELDS = {
    "p_plus20": "probability_plus20_excess_90d_pct",
    "expected": "expected_excess_return_90d_pct",
    "p_positive": "probability_positive_excess_90d_pct",
    "safety": "downside_tail_probability_pct",
}
RULES = ("safety", "p_plus20", "causal_blend", "upside_x_drawdown", "deep_drawdown")
COHORTS = {
    "historical_holdout_2009_2018": (2009, 2018),
    "discovery_2019_2020": (2019, 2020),
    "forward_holdout_2021_2025": (2021, 2025),
    "full_descriptive_2009_2025": (2009, 2025),
}


def mean_field(row: dict, field: str) -> float:
    values = [x.get(field) for x in row["syntheses"]]
    values = [float(x) for x in values if isinstance(x, (int, float))]
    if len(values) != 2:
        raise ValueError(f"missing synthesis field {field} for {row['case_id']}")
    return statistics.mean(values)


def add_locked_scores(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda r: (r.get("accepted") or r["cutoff"], r["case_id"]))
    prior = defaultdict(list)
    grouped = defaultdict(list)
    for row in ordered:
        grouped[row.get("accepted") or row["cutoff"]].append(row)
    output = []
    for timestamp in sorted(grouped):
        current = []
        for row in grouped[timestamp]:
            values = {name: mean_field(row, field) for name, field in FIELDS.items()}
            drawdown = abs(float((row.get("market_at_cutoff") or {}).get("drawdown_from_1y_high") or 0))
            z = []
            for name in ("p_plus20", "expected", "p_positive", "safety"):
                history = prior[name]
                sd = statistics.stdev(history) if len(history) >= 2 else 0
                direction = -1 if name == "safety" else 1
                z.append(direction * (values[name] - statistics.mean(history)) / sd if sd else 0.0)
            current.append({**row, "locked_values": values, "locked_scores": {
                "safety": -values["safety"],
                "p_plus20": values["p_plus20"],
                "causal_blend": statistics.mean(z),
                "upside_x_drawdown": values["p_plus20"] * drawdown,
                "deep_drawdown": drawdown,
            }})
        output.extend(current)
        for row in current:
            for name, value in row["locked_values"].items():
                prior[name].append(value)
    return output


def select(rows: list[dict], rule: str) -> list[dict]:
    return ev.causal_select([{**row, "score": row["locked_scores"][rule]} for row in rows])


def cohort_rows(rows: list[dict], start: int, end: int) -> list[dict]:
    return [row for row in rows if start <= int(row["cutoff"][:4]) <= end]


def evaluate(selected: list[dict], prices: dict, start_year: int, end_year: int) -> dict:
    group = cohort_rows(selected, start_year, end_year)
    if not group:
        return {"selected": 0, "trades": 0}
    start = f"{start_year}-01-01"
    end = (dt.date(end_year, 12, 31) + dt.timedelta(days=181)).isoformat()
    metrics, _, _ = ev.evaluate_selection(group, prices, "conservative", start=start, end=end)
    return metrics


def blocked_placebo(rows: list[dict], prices: dict, rule: str, observed: float,
                    draws: int, seed: int) -> dict:
    rng = random.Random(seed)
    blocks = defaultdict(list)
    for index, row in enumerate(rows):
        blocks[row["cutoff"][:7]].append(index)
    values = []
    base = [row["locked_scores"][rule] for row in rows]
    for _ in range(draws):
        shuffled = base[:]
        for indexes in blocks.values():
            block_values = [shuffled[i] for i in indexes]
            rng.shuffle(block_values)
            for i, value in zip(indexes, block_values):
                shuffled[i] = value
        permuted = [{**row, "score": score} for row, score in zip(rows, shuffled)]
        chosen = ev.causal_select(permuted)
        # Both clean cohorts are required; use their trade-count-weighted IR as
        # the family test statistic so the discovery years cannot help.
        metrics = [evaluate(chosen, prices, 2009, 2018), evaluate(chosen, prices, 2021, 2025)]
        usable = [(m.get("exposure_matched_information_ratio"), m.get("trades", 0)) for m in metrics]
        usable = [(v, n) for v, n in usable if v is not None and n]
        if usable:
            values.append(sum(v * n for v, n in usable) / sum(n for _, n in usable))
    p = (sum(value >= observed for value in values) + 1) / (len(values) + 1)
    return {"draws": len(values), "p_one_sided": p,
            "ir_quantiles": [float(x) for x in np.quantile(values, [.05, .5, .95])] if values else []}


def run(run_dir: Path, source_dir: Path, draws: int) -> dict:
    raw = ev.load_scored_cases(run_dir, source_dir)
    rows = add_locked_scores(raw)
    prices = ev.fetch_prices(rows, run_dir, concurrency=24)
    results = {}
    for index, rule in enumerate(RULES):
        chosen = select(rows, rule)
        cohorts = {name: evaluate(chosen, prices, *years) for name, years in COHORTS.items()}
        clean = [cohorts["historical_holdout_2009_2018"], cohorts["forward_holdout_2021_2025"]]
        usable = [(m.get("exposure_matched_information_ratio"), m.get("trades", 0)) for m in clean]
        usable = [(v, n) for v, n in usable if v is not None and n]
        statistic = sum(v * n for v, n in usable) / sum(n for _, n in usable) if usable else -math.inf
        placebo = blocked_placebo(rows, prices, rule, statistic, draws, 82026 + index)
        positive_both = all((m.get("exposure_matched_excess_annualized") or -math.inf) > 0 for m in clean)
        results[rule] = {"selected_all": len(chosen), "cohorts": cohorts,
                         "clean_weighted_ir": statistic, "blocked_placebo": placebo,
                         "passes_secondary_gate": (rule in {"p_plus20", "causal_blend", "upside_x_drawdown"}
                                                    and positive_both and placebo["p_one_sided"] < .0167)}
    output = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "protocol_amendment": "CENTURY_HYPOTHESES.md", "cases": len(rows),
              "placebo_draws_requested": draws, "rules": results}
    (run_dir / "century_hypotheses.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    (run_dir / "century_hypotheses.md").write_text(report(output), encoding="utf-8")
    return output


def report(result: dict) -> str:
    lines = ["# Century strategy hypotheses", "", f"Generated: {result['generated_at']}", "",
             "2019–2020 is discovery-only. Gates use 2009–2018 and 2021–2025.", "",
             "| Rule | Historical IR | Forward IR | Full IR | Blocked p | Secondary gate |",
             "| --- | ---: | ---: | ---: | ---: | --- |"]
    for name, row in result["rules"].items():
        c = row["cohorts"]
        def ir(key):
            value = c[key].get("exposure_matched_information_ratio")
            return "n/a" if value is None else f"{value:.2f}"
        lines.append(f"| `{name}` | {ir('historical_holdout_2009_2018')} | "
                     f"{ir('forward_holdout_2021_2025')} | {ir('full_descriptive_2009_2025')} | "
                     f"{row['blocked_placebo']['p_one_sided']:.4f} | "
                     f"{'PASS' if row['passes_secondary_gate'] else '—'} |")
    lines += ["", "The original safety-first verdict remains governed by the frozen primary protocol.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--placebo-draws", type=int, default=5000)
    args = parser.parse_args()
    result = run(args.run_dir, args.source_dir, args.placebo_draws)
    print(json.dumps({"cases": result["cases"], "rules": {k: v["passes_secondary_gate"] for k, v in result["rules"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
