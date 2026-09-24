#!/usr/bin/env python3
"""Run five deterministic long-signal experiments on the comprehensive corpus."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comprehensive_lab as cl
import ox_lab as ox
import temporal_store as ts


EXPERIMENT_SPECS = (
    {"name": "baseline_probability", "cohort": "long", "score": "p20",
     "description": "Rank false-distress cases by mean +20% excess-return probability."},
    {"name": "safety_first", "cohort": "long", "score": "safety",
     "description": "Rank false-distress cases by lowest forecast downside-tail probability."},
    {"name": "catalyst_with_vetoes", "cohort": "long", "score": "catalyst_veto",
     "description": "Rank catalyst score only when liquidity, accounting, and governance are each >= -1."},
    {"name": "consensus_adjusted", "cohort": "long", "score": "consensus",
     "description": "Mean +20% probability minus half the range across three syntheses."},
    {"name": "dilution_event_conditioned", "cohort": "dilution", "score": "p20",
     "description": "Rank comprehensive probability only inside the financing-risk event cohort."},
)


def mean_numeric(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return statistics.mean(values) if values else None


def feature_frame(run_dir: Path) -> pd.DataFrame:
    cases = {row["case_id"]: row for row in ox.load_jsonl(run_dir / "cases.jsonl")}
    extractions, syntheses = defaultdict(list), defaultdict(list)
    for row in ox.load_jsonl(run_dir / "extractions.jsonl"):
        extractions[row["case_id"]].append(row)
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        syntheses[row["case_id"]].append(row)
    rows = []
    for case_id, case in cases.items():
        outcome = case.get("outcome") or case.get("returns") or {}
        relative_return = outcome.get("relative_return_90d")
        if not isinstance(relative_return, (int, float)):
            continue
        success = outcome.get("long_success")
        if success is None:
            success = relative_return >= 0.20
        role_scores = {}
        for role in cl.ROLE_NAMES:
            role_scores[role] = mean_numeric(
                row["result"].get("overall_signal")
                for row in extractions[case_id] if row.get("role") == role)
        results = [row["result"] for row in syntheses[case_id]]
        p20_values = [row.get("probability_plus20_excess_90d_pct") for row in results]
        downside_values = [row.get("downside_tail_probability_pct") for row in results]
        p20 = mean_numeric(p20_values)
        downside = mean_numeric(downside_values)
        if p20 is None:
            continue
        veto_pass = all(role_scores.get(role) is not None and role_scores[role] >= -1
                        for role in ("liquidity", "accounting", "governance"))
        rows.append({
            "case_id": case_id, "ticker": case["ticker"], "cutoff": case["cutoff"],
            "cohort": "dilution" if Path(case["source_run_dir"]).name == "dilution90" else "long",
            "legacy_split": case.get("split"), "relative_return": relative_return,
            "success": int(bool(success)), "p20": p20 / 100,
            "safety": -downside / 100 if downside is not None else None,
            "consensus": p20 / 100 - 0.5 * ((max(p20_values) - min(p20_values)) / 100),
            "catalyst_veto": role_scores["catalysts"] if veto_pass else None,
            "veto_pass": veto_pass, **role_scores,
        })
    return pd.DataFrame(rows)


def assign_splits(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["evaluation_split"] = None
    long_mask = frame["cohort"] == "long"
    frame.loc[long_mask, "evaluation_split"] = frame.loc[long_mask, "legacy_split"]
    indexes = frame[frame["cohort"] == "dilution"].sort_values(
        ["cutoff", "case_id"]).index.tolist()
    boundary = math.floor(len(indexes) * 0.70)
    frame.loc[indexes[:boundary], "evaluation_split"] = "development"
    frame.loc[indexes[boundary:], "evaluation_split"] = "validation"
    return frame


def permutation_p_value(returns: list[float], selected_indexes: set[int],
                        seed: int = 117, draws: int = 20_000) -> float | None:
    n, chosen_n = len(returns), len(selected_indexes)
    if chosen_n == 0 or chosen_n == n:
        return None
    selected = [returns[index] for index in selected_indexes]
    rest = [returns[index] for index in range(n) if index not in selected_indexes]
    observed = statistics.mean(selected) - statistics.mean(rest)
    rng, exceed = random.Random(seed), 0
    all_indexes = range(n)
    for _ in range(draws):
        chosen = set(rng.sample(all_indexes, chosen_n))
        difference = (statistics.mean(returns[index] for index in chosen) -
                      statistics.mean(returns[index] for index in all_indexes if index not in chosen))
        exceed += difference >= observed
    return (exceed + 1) / (draws + 1)


def evaluate_partition(frame: pd.DataFrame, score_name: str) -> dict:
    eligible = frame.dropna(subset=[score_name]).sort_values(
        [score_name, "case_id"], ascending=[False, True])
    target_n = max(1, math.ceil(len(frame) * 0.10)) if len(frame) else 0
    basket = eligible.head(min(target_n, len(eligible)))
    if basket.empty:
        return {"n": len(frame), "eligible_n": len(eligible), "basket_n": 0}
    rest = frame.drop(basket.index)
    ordered = frame.reset_index(drop=True)
    selected = set(ordered.index[ordered["case_id"].isin(basket["case_id"])])
    p_value = permutation_p_value(ordered["relative_return"].tolist(), selected)
    return {
        "n": len(frame), "eligible_n": len(eligible), "basket_n": len(basket),
        "base_mean_excess_return": frame["relative_return"].mean(),
        "base_success_rate": frame["success"].mean(),
        "basket_mean_excess_return": basket["relative_return"].mean(),
        "basket_median_excess_return": basket["relative_return"].median(),
        "basket_success_rate": basket["success"].mean(),
        "rest_mean_excess_return": rest["relative_return"].mean() if len(rest) else None,
        "basket_vs_rest_spread": (basket["relative_return"].mean() -
                                  rest["relative_return"].mean()) if len(rest) else None,
        "permutation_p_one_sided": p_value,
        "tickers": basket["ticker"].tolist(),
        "returns": basket["relative_return"].tolist(),
        "scores": basket[score_name].tolist(),
    }


def run_experiments(run_dir: Path) -> dict:
    frame = assign_splits(feature_frame(run_dir))
    results = []
    for spec in EXPERIMENT_SPECS:
        cohort = frame[frame["cohort"] == spec["cohort"]]
        partitions = {
            split: evaluate_partition(cohort[cohort["evaluation_split"] == split], spec["score"])
            for split in ("development", "validation")
        }
        results.append({**spec, "partitions": partitions})
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": {"basket": "top decile", "permutation_draws": 20_000,
                   "long_split": "legacy 62/38", "dilution_split": "chronological 70/30",
                   "costs_included": False},
        "experiments": results,
    }


def fmt_pct(value):
    return "n/a" if value is None else f"{value:+.1%}"


def render_report(result: dict) -> str:
    lines = ["# Five long-signal experiments", "", f"Generated: {result['generated_at']}", "",
             "These are signal tests using legacy 90-day excess-return outcomes, not costed portfolio backtests.", "",
             "## Results", "",
             "| Experiment | Cohort | Development top basket | Validation top basket | Validation spread | Permutation p |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for experiment in result["experiments"]:
        dev, val = experiment["partitions"]["development"], experiment["partitions"]["validation"]
        p_value = val.get("permutation_p_one_sided")
        lines.append(f"| `{experiment['name']}` | {experiment['cohort']} | "
                     f"{fmt_pct(dev.get('basket_mean_excess_return'))} ({dev.get('basket_n', 0)}) | "
                     f"{fmt_pct(val.get('basket_mean_excess_return'))} ({val.get('basket_n', 0)}) | "
                     f"{fmt_pct(val.get('basket_vs_rest_spread'))} | "
                     f"{'n/a' if p_value is None else f'{p_value:.3f}'} |")
    lines += ["", "## Definitions", ""]
    for experiment in result["experiments"]:
        lines.append(f"- `{experiment['name']}`: {experiment['description']}")
    lines += ["", "## Validation baskets", ""]
    for experiment in result["experiments"]:
        val = experiment["partitions"]["validation"]
        pairs = ", ".join(f"{ticker} {ret:+.1%}" for ticker, ret in
                          zip(val.get("tickers", []), val.get("returns", []))) or "none"
        lines += [f"### {experiment['name']}", "", pairs, "",
                  f"Mean {fmt_pct(val.get('basket_mean_excess_return'))}; median "
                  f"{fmt_pct(val.get('basket_median_excess_return'))}; success rate "
                  f"{fmt_pct(val.get('basket_success_rate'))}.", ""]
    lines += ["## Interpretation constraints", "",
              "- The legacy long validation split has already been inspected in prior analysis and is no longer pristine.",
              "- The dilution chronological split is newly imposed but the strategy family was suggested by full-cohort results, so it is confirmatory only in a weak sense.",
              "- No transaction costs, next-session execution, overlapping-position rules, or capacity constraints are included.",
              "- Five experiments create multiple-testing risk; isolated low p-values would require fresh data confirmation.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="strategy-experiments", description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/comprehensive_long"))
    parser.add_argument("--output", type=Path, default=Path("lab_runs/strategy_experiments"))
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    result = run_experiments(args.run_dir)
    (args.output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.output / "report.md").write_text(render_report(result), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
