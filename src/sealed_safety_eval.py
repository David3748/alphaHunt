#!/usr/bin/env python3
"""Open outcomes only after sealed model completion and evaluate the locked strategy."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_lab
import ox_lab as ox
import safety_backtest as sb


def load_scored_cases(run_dir: Path, source_dir: Path) -> list[dict]:
    sources = {row["case_id"]: row for row in ox.load_jsonl(source_dir / "cases.jsonl")}
    wrappers = {row["case_id"]: row for row in ox.load_jsonl(run_dir / "cases.jsonl")}
    syntheses = defaultdict(list)
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        value = (row.get("result") or {}).get("downside_tail_probability_pct")
        if isinstance(value, (int, float)):
            syntheses[row["case_id"]].append(row)
    scored = []
    for case_id, wrapper in wrappers.items():
        rows = sorted(syntheses.get(case_id, []), key=lambda row: int(row.get("replicate", 0)))
        source = sources.get(wrapper.get("source_case_id"))
        if not source or len(rows) != 2:
            continue
        downside = statistics.mean(row["result"]["downside_tail_probability_pct"] for row in rows) / 100
        scored.append({**source, "comprehensive_case_id": case_id, "score": -downside,
                       "downside_probability": downside,
                       "syntheses": [row["result"] for row in rows]})
    return sorted(scored, key=lambda row: (row.get("accepted") or row["cutoff"], row["case_id"]))


def causal_select(rows: list[dict], warmup: int = 50, fraction: float = 0.10) -> list[dict]:
    selected, prior = [], []
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("accepted") or row["cutoff"]].append(row)
    for timestamp in sorted(grouped):
        threshold = float(np.quantile(prior, 1 - fraction, method="linear")) if len(prior) >= warmup else None
        for row in sorted(grouped[timestamp], key=lambda item: item["case_id"]):
            if threshold is not None and row["score"] >= threshold:
                selected.append({**row, "threshold_at_signal": threshold,
                                 "prior_score_count": len(prior)})
        prior.extend(row["score"] for row in grouped[timestamp])
    return selected


def fetch_prices(rows: list[dict], run_dir: Path, concurrency: int = 12) -> dict:
    symbols = sorted({row["ticker"] for row in rows} | {"SPY", "^IRX"})
    http = ox.CachedHTTP(run_dir / "cache" / "outcome_price", min_interval=0.08)
    signal_days = [dt.date.fromisoformat(row["cutoff"]) for row in rows]
    start = min(signal_days) - dt.timedelta(days=400)
    end = max(signal_days) + dt.timedelta(days=200)
    prices = {}

    def work(symbol):
        try:
            series, _ = long_lab.chart_series(symbol, start, end, http)
            return symbol, [{"date": row["date"].isoformat(), "close": row["close"]} for row in series]
        except Exception:
            return symbol, []

    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, symbol): symbol for symbol in symbols}
        completed = 0
        for future in cf.as_completed(futures):
            symbol, series = future.result()
            prices[symbol] = series
            completed += 1
            if completed % 100 == 0:
                print(f"outcome prices {completed}/{len(symbols)}", file=sys.stderr)
    return prices


def acceptance_entry_index(case: dict, series: list[dict], delay: int = 0) -> int | None:
    accepted = case.get("accepted") or ""
    day = dt.date.fromisoformat(case["cutoff"])
    same_day_allowed = False
    if accepted:
        try:
            parsed = dt.datetime.strptime(accepted.split(".")[0], "%Y-%m-%d %H:%M:%S")
            day, same_day_allowed = parsed.date(), parsed.time() < dt.time(16, 0)
        except ValueError:
            pass
    candidates = [index for index, row in enumerate(series)
                  if dt.date.fromisoformat(row["date"]) >= day
                  and (same_day_allowed or dt.date.fromisoformat(row["date"]) > day)]
    return candidates[delay] if len(candidates) > delay else None


def make_trade(case: dict, prices: dict, bound: str = "conservative",
               holding_days: int = 90, delay: int = 0) -> tuple[dict | None, str]:
    series = prices.get(case["ticker"], [])
    index = acceptance_entry_index(case, series, delay)
    if index is None:
        return None, "no_executable_entry"
    entry = series[index]
    target = dt.date.fromisoformat(entry["date"]) + dt.timedelta(days=holding_days)
    exit_row = next((row for row in series[index + 1:]
                     if dt.date.fromisoformat(row["date"]) >= target), None)
    terminal_gap = exit_row is None
    if terminal_gap:
        last = series[-1] if len(series) > index else entry
        spy_exit = next((row for row in prices["SPY"]
                         if dt.date.fromisoformat(row["date"]) >= target), None)
        if not spy_exit:
            return None, "benchmark_exit_missing"
        exit_row = {"date": spy_exit["date"],
                    "close": 0.0 if bound == "conservative" else last["close"]}
    return {**case, "entry_date": entry["date"], "entry_price": entry["close"],
            "exit_date": exit_row["date"], "exit_price": exit_row["close"],
            "terminal_history_gap": terminal_gap, "terminal_bound": bound,
            "stock_return": exit_row["close"] / entry["close"] - 1}, "ok"


def simulation_prices(base: dict, trades: list[dict]) -> dict:
    needed = {"SPY", "^IRX", *(trade["ticker"] for trade in trades)}
    result = {symbol: base.get(symbol, []) for symbol in needed}
    copied = set()
    for trade in trades:
        ticker = trade["ticker"]
        series = result.get(ticker, [])
        if not any(row["date"] == trade["exit_date"] for row in series):
            if ticker not in copied:
                series = [dict(row) for row in series]
                result[ticker] = series
                copied.add(ticker)
            series.append({"date": trade["exit_date"], "close": trade["exit_price"]})
            series.sort(key=lambda row: row["date"])
    return result


def evaluate_selection(selected: list[dict], prices: dict, bound: str,
                       holding_days: int = 90, delay: int = 0, cost: float = 25,
                       start: str | None = None, end: str | None = None) -> tuple[dict, list[dict], object]:
    trades, failures = [], defaultdict(int)
    for case in selected:
        trade, status = make_trade(case, prices, bound, holding_days, delay)
        if trade:
            trades.append(trade)
        else:
            failures[status] += 1
    frame, executed, skipped = sb.simulate(
        trades, simulation_prices(prices, trades), 10, cost, start, end)
    metrics = sb.series_metrics(frame, executed, skipped)
    metrics.update({"selected": len(selected), "prepared_trades": len(trades),
                    "pretrade_failures": dict(failures),
                    "terminal_history_gaps": sum(row["terminal_history_gap"] for row in trades)})
    return metrics, executed, frame


def serialize_trade(row: dict) -> dict:
    synthesis = row["syntheses"][0]
    return {key: row.get(key) for key in ("case_id", "comprehensive_case_id", "ticker", "company",
                                           "cik", "cutoff", "accepted", "score",
                                           "downside_probability", "threshold_at_signal",
                                           "prior_score_count", "entry_date", "entry_price",
                                           "exit_date", "exit_price", "stock_return",
                                           "terminal_history_gap", "terminal_bound")} | {
        "thesis": synthesis.get("thesis"), "catalyst": synthesis.get("catalyst"),
        "invalidation": synthesis.get("invalidation"),
        "evidence_refs": synthesis.get("evidence_refs") or [],
        "net_trade_return": row.get("net_sleeve_return"),
    }


def signal_month_attribution(executed: list[dict], prices: dict) -> dict:
    """Attribute trade-level excess P&L to the month in which each signal arrived."""
    spy = {row["date"]: row["close"] for row in prices.get("SPY", [])}
    by_month = defaultdict(float)
    unmatched = 0
    for row in executed:
        entry_spy = spy.get(row["entry_date"])
        exit_spy = spy.get(row["exit_date"])
        if not entry_spy or exit_spy is None:
            unmatched += 1
            continue
        spy_return = exit_spy / entry_spy - 1
        excess_profit = row["entry_notional"] * (row["net_sleeve_return"] - spy_return)
        by_month[row["cutoff"][:7]] += excess_profit
    total = float(sum(by_month.values()))
    largest_month, largest = (max(by_month.items(), key=lambda item: item[1])
                              if by_month else (None, 0.0))
    share = float(largest / total) if total > 0 else None
    return {"method": "entry-notional-weighted trade return less same-window SPY return",
            "total_excess_profit": total, "largest_month": largest_month,
            "largest_month_excess_profit": float(largest),
            "largest_month_share": share, "unmatched_trades": unmatched,
            "by_month": dict(sorted(by_month.items()))}


def materialize_outcomes(scored: list[dict], prices: dict, run_dir: Path) -> list[dict]:
    spy = {row["date"]: row["close"] for row in prices.get("SPY", [])}
    rows = []
    for case in scored:
        conservative, status = make_trade(case, prices, "conservative")
        optimistic, _ = make_trade(case, prices, "optimistic")
        value = {"status": status, "holding_days": 90}
        if conservative:
            entry_spy, exit_spy = spy.get(conservative["entry_date"]), spy.get(conservative["exit_date"])
            benchmark = exit_spy / entry_spy - 1 if entry_spy and exit_spy is not None else None
            value.update({
                "entry_date": conservative["entry_date"], "entry_price": conservative["entry_price"],
                "exit_date": conservative["exit_date"], "benchmark_return_90d": benchmark,
                "stock_return_90d_conservative": conservative["stock_return"],
                "stock_return_90d_optimistic": optimistic["stock_return"] if optimistic else None,
                "relative_return_90d": (conservative["stock_return"] - benchmark
                                        if benchmark is not None else None),
                "relative_return_90d_optimistic": (optimistic["stock_return"] - benchmark
                                                   if optimistic and benchmark is not None else None),
                "terminal_history_gap": conservative["terminal_history_gap"],
            })
        rows.append({"case_id": case["case_id"],
                     "comprehensive_case_id": case["comprehensive_case_id"],
                     "ticker": case["ticker"], "cutoff": case["cutoff"], "outcome": value})
    output = run_dir / "outcomes.jsonl"
    tmp = output.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    os.replace(tmp, output)
    return rows


def run(run_dir: Path, source_dir: Path, concurrency: int = 12,
        placebo_draws: int = 1000) -> dict:
    scored = load_scored_cases(run_dir, source_dir)
    source_count = len(ox.load_jsonl(source_dir / "cases.jsonl"))
    if len(scored) != source_count:
        raise RuntimeError(f"sealed model run incomplete: scored={len(scored)} cases={source_count}")
    prices = fetch_prices(scored, run_dir, concurrency)
    outcomes = materialize_outcomes(scored, prices, run_dir)
    selected = causal_select(scored)
    common_start = min(row["cutoff"] for row in scored)
    common_end = (max(dt.date.fromisoformat(row["cutoff"]) for row in scored)
                  + dt.timedelta(days=181)).isoformat()
    signal_years = sorted({int(row["cutoff"][:4]) for row in scored})
    if signal_years == [2019, 2020]:
        common_end = "2021-06-30"
    bounds, executed_by, frames = {}, {}, {}
    for bound in ("conservative", "optimistic"):
        metrics, executed, frame = evaluate_selection(
            selected, prices, bound, start=common_start, end=common_end)
        bounds[bound], executed_by[bound], frames[bound] = metrics, executed, frame
    sensitivity = {}
    for holding in (30, 60, 90, 120, 180):
        for delay in (0, 1, 4):
            for cost in (10, 25, 50):
                metrics, _, _ = evaluate_selection(selected, prices, "conservative",
                                                    holding, delay, cost, common_start, common_end)
                sensitivity[f"hold{holding}_delay{delay}_cost{cost}"] = metrics
    by_year = {}
    for year in signal_years:
        group = [row for row in selected if row["cutoff"].startswith(str(year))]
        by_year[str(year)], _, _ = evaluate_selection(group, prices, "conservative",
                                                       start=common_start, end=common_end)
    ordered = sorted(scored, key=lambda row: (-row["score"], row["case_id"]))
    buckets = {}
    size = math.ceil(len(ordered) / 5)
    for index in range(5):
        group = ordered[index * size:(index + 1) * size]
        buckets[str(index + 1)], _, _ = evaluate_selection(group, prices, "conservative",
                                                            start=common_start, end=common_end)
    rng = random.Random(2401)
    observed = bounds["conservative"].get("exposure_matched_information_ratio") or -math.inf
    placebo_values = []
    original_scores = [row["score"] for row in scored]
    for _ in range(placebo_draws):
        shuffled = original_scores[:]
        rng.shuffle(shuffled)
        permuted = [{**row, "score": score} for row, score in zip(scored, shuffled)]
        group = causal_select(permuted)
        metric, _, _ = evaluate_selection(group, prices, "conservative",
                                           start=common_start, end=common_end)
        value = metric.get("exposure_matched_information_ratio")
        if value is not None:
            placebo_values.append(value)
    placebo_p = ((sum(value >= observed for value in placebo_values) + 1) /
                 (len(placebo_values) + 1))
    conservative = bounds["conservative"]
    month_attribution = signal_month_attribution(executed_by["conservative"], prices)
    positive_years = sum((by_year[str(year)].get("exposure_matched_excess_annualized") or 0) > 0
                         for year in signal_years)
    required_positive_years = len(signal_years) if len(signal_years) <= 2 else math.ceil(.60 * len(signal_years))
    year_positive = positive_years >= required_positive_years
    passed = {
        "at_least_30_trades": conservative.get("trades", 0) >= 30,
        "sharpe_gt_1": (conservative.get("sharpe_over_13week_tbill") or -math.inf) > 1,
        "matched_ir_gt_0_5": (conservative.get("exposure_matched_information_ratio") or -math.inf) > 0.5,
        "max_drawdown_better_than_minus25": (conservative.get("max_drawdown") or -1) > -0.25,
        "positive_year_coverage": year_positive,
        "placebo_p_lt_0_05": placebo_p < 0.05,
        "no_signal_month_over_half_excess_profit": (
            month_attribution["largest_month_share"] is not None
            and month_attribution["largest_month_share"] <= 0.5),
    }
    result = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "protocol": "lab_runs/sealed_safety/PROTOCOL.md", "scored_cases": len(scored),
        "causal_selected": len(selected), "bounds": bounds, "by_signal_year": by_year,
        "year_coverage": {"signal_years": signal_years, "positive_years": positive_years,
                          "required_positive_years": required_positive_years},
        "score_quintiles": buckets, "sensitivity": sensitivity,
        "random_score_placebo": {"draws": len(placebo_values), "p_one_sided": placebo_p,
                                  "ir_quantiles": [float(x) for x in np.quantile(placebo_values, [.05, .5, .95])]},
        "bootstrap": {
            "daily_block": sb.block_bootstrap(frames["conservative"]),
            "trades": sb.trade_bootstrap(executed_by["conservative"]),
        },
        "signal_month_attribution": month_attribution,
        "decision_checks": passed, "validated": all(passed.values()),
        "trades": [serialize_trade(row) for row in executed_by["conservative"]],
        "coverage": {"source_cases": source_count,
                     "symbols_with_prices": sum(bool(prices.get(row["ticker"])) for row in scored),
                     "materialized_outcomes": len(outcomes)},
    }
    frames["conservative"].to_csv(run_dir / "sealed_daily_portfolio.csv", index=False)
    (run_dir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(report(result), encoding="utf-8")
    return result


def pct(value):
    return "n/a" if value is None else f"{value:+.1%}"


def report(result: dict) -> str:
    lines = ["# Sealed survivorship-reduced safety validation", "",
             f"Generated: {result['generated_at']}", "", "## Verdict", "",
             ("**VALIDATED under the frozen rule.**" if result["validated"] else
              "**NOT VALIDATED under the frozen rule.**"), "",
             "| Bound | Trades | CAGR | T-bill Sharpe | Max DD | Matched IR | Terminal gaps |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in result["bounds"].items():
        lines.append(f"| {name} | {row.get('trades', 0)} | {pct(row.get('cagr'))} | "
                     f"{row.get('sharpe_over_13week_tbill', float('nan')):.2f} | "
                     f"{pct(row.get('max_drawdown'))} | "
                     f"{row.get('exposure_matched_information_ratio', float('nan')):.2f} | "
                     f"{row.get('terminal_history_gaps', 0)} |")
    lines += ["", "## Locked checks", ""]
    for key, passed in result["decision_checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{key}`")
    lines += ["", f"Random-score placebo p={result['random_score_placebo']['p_one_sided']:.4f}.", "",
              "The conservative bound marks terminal price histories to zero; the optimistic bound carries "
              "the last close. Coverage and unresolved identifier counts must be read with the universe audit.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/sealed_safety"))
    parser.add_argument("--source-dir", type=Path, default=Path("lab_runs/sealed_safety_source"))
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--placebo-draws", type=int, default=1000)
    args = parser.parse_args(argv)
    result = run(args.run_dir, args.source_dir, args.concurrency, args.placebo_draws)
    print(json.dumps({"validated": result["validated"], "scored_cases": result["scored_cases"],
                      "causal_selected": result["causal_selected"],
                      "bounds": result["bounds"], "checks": result["decision_checks"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
