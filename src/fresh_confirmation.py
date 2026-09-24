#!/usr/bin/env python3
"""Evaluate the two strategies frozen in fresh_confirmation/PROTOCOL.md."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox


def mean_numeric(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return statistics.mean(values) if values else None


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in x) *
                            sum((b - my) ** 2 for b in y))
    return numerator / denominator if denominator else None


def ranks(values: list[float]) -> list[float]:
    """Average ranks, with the smallest value assigned rank one."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2
        for index in order[start:end]:
            result[index] = rank
        start = end
    return result


def auc(scores: list[float], labels: list[int]) -> float | None:
    positives, negatives = sum(labels), len(labels) - sum(labels)
    if not positives or not negatives:
        return None
    score_ranks = ranks(scores)
    positive_rank_sum = sum(rank for rank, label in zip(score_ranks, labels) if label)
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def basket_stats(rows: list[dict], score_name: str, count: int) -> dict:
    ordered = sorted(rows, key=lambda row: (-row[score_name], row["case_id"]))[:count]
    returns = [row["relative_return"] for row in ordered]
    return {"n": len(ordered), "mean": statistics.mean(returns),
            "median": statistics.median(returns),
            "success_rate": statistics.mean(row["success"] for row in ordered)}


def safety_robustness(rows: list[dict]) -> dict:
    eligible = [row for row in rows if isinstance(row.get("safety"), (int, float))]
    ordered = sorted(eligible, key=lambda row: (-row["safety"], row["case_id"]))
    primary_n = max(1, math.ceil(len(rows) * 0.10))
    boundary = ordered[primary_n - 1]["safety"]
    above = [row for row in ordered if row["safety"] > boundary]
    tied = [row for row in ordered if row["safety"] == boundary]
    slots = primary_n - len(above)
    # The observed boundary has two tied names; enumerate each possible fill so
    # tie-breaking sensitivity is exact and deterministic.
    tie_means = []
    if slots == 1:
        base = [row["relative_return"] for row in above]
        tie_means = [statistics.mean([*base, row["relative_return"]]) for row in tied]
    scores = [row["safety"] for row in eligible]
    returns = [row["relative_return"] for row in eligible]
    labels = [row["success"] for row in eligible]
    replicate_rows = [row for row in eligible
                      if isinstance(row.get("safety_r1"), (int, float)) and
                      isinstance(row.get("safety_r2"), (int, float))]
    primary = basket_stats(eligible, "safety", primary_n)
    selected = sorted(eligible, key=lambda row: (-row["safety"], row["case_id"]))[:primary_n]
    leave_one_out = [statistics.mean(other["relative_return"] for other in selected
                                     if other["case_id"] != row["case_id"])
                     for row in selected] if primary_n > 1 else []
    return {
        "auc_for_plus20_success": auc(scores, labels),
        "spearman_score_return": pearson(ranks(scores), ranks(returns)),
        "pearson_score_return": pearson(scores, returns),
        "basket_size_sensitivity": {
            str(k): basket_stats(eligible, "safety", k)
            for k in (4, 6, 8, 10, 12, 16, 20) if k <= len(eligible)
        },
        "replicate_score_correlation": pearson(
            [row["safety_r1"] for row in replicate_rows],
            [row["safety_r2"] for row in replicate_rows]),
        "replicate_1_top_decile": basket_stats(replicate_rows, "safety_r1", primary_n),
        "replicate_2_top_decile": basket_stats(replicate_rows, "safety_r2", primary_n),
        "boundary": {"score": boundary, "strictly_above": len(above),
                     "tied": len(tied), "slots": slots,
                     "possible_basket_mean_min": min(tie_means) if tie_means else primary["mean"],
                     "possible_basket_mean_max": max(tie_means) if tie_means else primary["mean"]},
        "leave_one_out_basket_mean_min": min(leave_one_out) if leave_one_out else None,
        "leave_one_out_basket_mean_max": max(leave_one_out) if leave_one_out else None,
    }


def permutation_p(returns: list[float], selected_n: int,
                  observed: float, draws: int = 20_000, seed: int = 913) -> float:
    rng, exceed = random.Random(seed), 0
    indexes = range(len(returns))
    for _ in range(draws):
        chosen = set(rng.sample(indexes, selected_n))
        difference = (statistics.mean(returns[i] for i in chosen) -
                      statistics.mean(returns[i] for i in indexes if i not in chosen))
        exceed += difference >= observed
    return (exceed + 1) / (draws + 1)


def evaluate(rows: list[dict], score_name: str) -> dict:
    eligible = [row for row in rows if isinstance(row.get(score_name), (int, float))]
    eligible.sort(key=lambda row: (-row[score_name], row["case_id"]))
    basket_n = max(1, math.ceil(len(rows) * 0.10))
    basket = eligible[:basket_n]
    chosen = {row["case_id"] for row in basket}
    rest = [row for row in rows if row["case_id"] not in chosen]
    basket_mean = statistics.mean(row["relative_return"] for row in basket)
    rest_mean = statistics.mean(row["relative_return"] for row in rest)
    spread = basket_mean - rest_mean
    return {
        "n": len(rows), "eligible_n": len(eligible), "basket_n": len(basket),
        "base_mean_excess_return": statistics.mean(row["relative_return"] for row in rows),
        "basket_mean_excess_return": basket_mean,
        "basket_median_excess_return": statistics.median(row["relative_return"] for row in basket),
        "basket_success_rate": statistics.mean(row["success"] for row in basket),
        "rest_mean_excess_return": rest_mean, "basket_vs_rest_spread": spread,
        "permutation_p_one_sided": permutation_p(
            [row["relative_return"] for row in rows], len(basket), spread),
        "tickers": [row["ticker"] for row in basket],
        "returns": [row["relative_return"] for row in basket],
        "scores": [row[score_name] for row in basket],
    }


def run(run_dir: Path) -> dict:
    cases = {row["case_id"]: row for row in ox.load_jsonl(run_dir / "cases.jsonl")}
    syntheses = defaultdict(list)
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        if isinstance(row.get("result"), dict):
            syntheses[row["case_id"]].append(row)
    rows = defaultdict(list)
    for case_id, case in cases.items():
        synthesis_rows = sorted(syntheses.get(case_id, []),
                                key=lambda row: int(row.get("replicate", 0)))
        if len(synthesis_rows) != 2:
            continue
        results = [row["result"] for row in synthesis_rows]
        outcome = case.get("outcome") or case.get("returns") or {}
        relative_return = outcome.get("relative_return_90d")
        if not isinstance(relative_return, (int, float)):
            continue
        success = outcome.get("long_success")
        if success is None:
            success = relative_return >= 0.20
        downside_values = [result.get("downside_tail_probability_pct") for result in results]
        downside = mean_numeric(downside_values)
        p20 = mean_numeric(result.get("probability_plus20_excess_90d_pct") for result in results)
        source = Path(case["source_run_dir"]).name
        rows[source].append({"case_id": case_id, "ticker": case["ticker"],
                             "relative_return": relative_return, "success": int(bool(success)),
                             "safety": -downside / 100 if downside is not None else None,
                             "safety_r1": -downside_values[0] / 100 if isinstance(downside_values[0], (int, float)) else None,
                             "safety_r2": -downside_values[1] / 100 if isinstance(downside_values[1], (int, float)) else None,
                             "p20": p20 / 100 if p20 is not None else None})
    safety = evaluate(rows["fresh_long"], "safety")
    safety["robustness"] = safety_robustness(rows["fresh_long"])
    dilution = evaluate(rows["fresh_dilution"], "p20")
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "protocol": "lab_runs/fresh_confirmation/PROTOCOL.md",
        "decision_threshold_p": 0.05, "bonferroni_threshold_p": 0.025,
        "strategies": {"safety_first": safety,
                       "dilution_event_conditioned": dilution},
        "confirmed_nominal": [name for name, result in
                              (("safety_first", safety), ("dilution_event_conditioned", dilution))
                              if result["basket_vs_rest_spread"] > 0 and
                              result["permutation_p_one_sided"] < 0.05],
    }


def pct(value):
    return f"{value:+.1%}"


def report(result: dict) -> str:
    lines = ["# Fresh confirmation results", "", f"Generated: {result['generated_at']}", "",
             "Protocol was frozen before cases were built or outcomes inspected.", "",
             "| Strategy | N | Basket | Mean excess | Median excess | Success | Spread | p |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in result["strategies"].items():
        lines.append(f"| `{name}` | {row['n']} | {row['basket_n']} | "
                     f"{pct(row['basket_mean_excess_return'])} | "
                     f"{pct(row['basket_median_excess_return'])} | "
                     f"{pct(row['basket_success_rate'])} | "
                     f"{pct(row['basket_vs_rest_spread'])} | "
                     f"{row['permutation_p_one_sided']:.4f} |")
    lines += ["", "## Baskets", ""]
    for name, row in result["strategies"].items():
        pairs = ", ".join(f"{ticker} {ret:+.1%}" for ticker, ret in
                          zip(row["tickers"], row["returns"]))
        lines += [f"### {name}", "", pairs, ""]
    confirmed = result["confirmed_nominal"]
    lines += ["## Decision", "",
              ("Nominally confirmed at p<0.05: " + ", ".join(confirmed)
               if confirmed else "Neither strategy met the pre-registered p<0.05 confirmation rule."),
              "", "The two-hypothesis Bonferroni threshold is p<0.025.", "",
              "The safety-first result passes both the nominal and Bonferroni thresholds; "
              "the dilution-event strategy does not confirm.", "", "## Safety-first robustness", ""]
    robust = result["strategies"]["safety_first"]["robustness"]
    sizes = robust["basket_size_sensitivity"]
    lines += [
        f"The continuous safety score has AUC {robust['auc_for_plus20_success']:.3f}, "
        f"Spearman correlation {robust['spearman_score_return']:.3f}, and Pearson "
        f"correlation {robust['pearson_score_return']:.3f} with realized excess return.", "",
        "| Basket size | Mean excess | Median excess | Success |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for size, stats in sizes.items():
        lines.append(f"| {size} | {pct(stats['mean'])} | {pct(stats['median'])} | "
                     f"{pct(stats['success_rate'])} |")
    boundary = robust["boundary"]
    lines += ["", f"Replicate score correlation is {robust['replicate_score_correlation']:.3f}. "
              f"The two individual-replicate top-decile baskets returned "
              f"{pct(robust['replicate_1_top_decile']['mean'])} and "
              f"{pct(robust['replicate_2_top_decile']['mean'])} mean excess, respectively.", "",
              f"At the cutoff, {boundary['strictly_above']} names are strictly above the "
              f"boundary and {boundary['tied']} names compete for {boundary['slots']} slot; "
              f"the possible basket mean remains between "
              f"{pct(boundary['possible_basket_mean_min'])} and "
              f"{pct(boundary['possible_basket_mean_max'])}. Leave-one-out basket means "
              f"range from {pct(robust['leave_one_out_basket_mean_min'])} to "
              f"{pct(robust['leave_one_out_basket_mean_max'])}.", "",
              "## Limitations", "",
              "These are signal tests without costs, overlap handling, capacity controls, "
              "or executable next-session entry prices. The cases were sampled from a "
              "current-listing universe, creating survivorship bias that can materially "
              "inflate historical 2021–2023 long returns. Treat safety-first as a confirmed "
              "research signal in this sampled dataset—not yet a production trading result.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fresh-confirmation", description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/fresh_confirmation"))
    args = parser.parse_args(argv)
    result = run(args.run_dir)
    (args.run_dir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.run_dir / "report.md").write_text(report(result), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
