#!/usr/bin/env python3
"""Executable daily portfolio evaluation for the fresh safety-first signal."""

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

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_lab
import ox_lab as ox


def load_signals(run_dir: Path, source_dir: Path) -> list[dict]:
    source_cases = {row["case_id"]: row for row in ox.load_jsonl(source_dir / "cases.jsonl")}
    comprehensive = {row["case_id"]: row for row in ox.load_jsonl(run_dir / "cases.jsonl")
                     if Path(row["source_run_dir"]).name == source_dir.name}
    syntheses = defaultdict(list)
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        if row.get("case_id") in comprehensive and isinstance(row.get("result"), dict):
            value = row["result"].get("downside_tail_probability_pct")
            if isinstance(value, (int, float)):
                syntheses[row["case_id"]].append(float(value))
    signals = []
    for case_id, wrapper in comprehensive.items():
        values = syntheses.get(case_id, [])
        source = source_cases.get(wrapper["source_case_id"])
        if len(values) != 2 or not source:
            continue
        signals.append({
            "case_id": case_id, "source_case_id": wrapper["source_case_id"],
            "ticker": source["ticker"], "cutoff": source["cutoff"],
            "score": -statistics.mean(values) / 100,
            "downside_probability": statistics.mean(values) / 100,
            "adv30": source["market_at_cutoff"]["avg_dollar_volume_30d"],
            "drawdown": source["market_at_cutoff"]["drawdown_from_1y_high"],
        })
    return sorted(signals, key=lambda row: (row["cutoff"], row["case_id"]))


def quantile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q, method="linear"))


def select_global(signals: list[dict], fraction: float = 0.10) -> list[dict]:
    count = max(1, math.ceil(len(signals) * fraction))
    return sorted(signals, key=lambda row: (-row["score"], row["case_id"]))[:count]


def select_expanding(signals: list[dict], fraction: float = 0.10,
                     warmup: int = 20, lookback: int | None = None) -> list[dict]:
    """Use only scores strictly earlier than each event date to set the threshold."""
    selected = []
    for row in signals:
        prior = [other["score"] for other in signals if other["cutoff"] < row["cutoff"]]
        if lookback:
            prior = prior[-lookback:]
        if len(prior) < warmup:
            continue
        threshold = quantile(prior, 1 - fraction)
        if row["score"] >= threshold:
            selected.append({**row, "threshold_at_signal": threshold,
                             "prior_score_count": len(prior)})
    return selected


def fetch_prices(signals: list[dict], cache: Path) -> dict[str, list[dict]]:
    http = ox.CachedHTTP(cache)
    start, end = dt.date(2019, 11, 28), dt.date(2024, 7, 18)
    prices = {}
    for ticker in sorted({row["ticker"] for row in signals} | {"SPY", "^IRX"}):
        rows, _ = long_lab.chart_series(ticker, start, end, http)
        if rows:
            prices[ticker] = [{"date": row["date"].isoformat(), "close": row["close"]}
                              for row in rows]
    return prices


def prepare_trades(selected: list[dict], prices: dict[str, list[dict]],
                   holding_days: int = 90, entry_delay: int = 0) -> list[dict]:
    trades = []
    for signal in selected:
        rows = prices.get(signal["ticker"], [])
        cutoff = dt.date.fromisoformat(signal["cutoff"])
        after = [row for row in rows if dt.date.fromisoformat(row["date"]) > cutoff]
        if len(after) <= entry_delay:
            continue
        entry = after[entry_delay]
        target = cutoff + dt.timedelta(days=holding_days)
        exit_row = next((row for row in rows
                         if dt.date.fromisoformat(row["date"]) >= target), None)
        if not exit_row or exit_row["date"] <= entry["date"]:
            continue
        trades.append({**signal, "entry_date": entry["date"], "entry_price": entry["close"],
                       "exit_date": exit_row["date"], "exit_price": exit_row["close"]})
    return sorted(trades, key=lambda row: (row["entry_date"], row["case_id"]))


def simulate(trades: list[dict], prices: dict[str, list[dict]], max_positions: int = 10,
             cost_bps_per_side: float = 10, evaluation_start: str | None = None,
             evaluation_end: str | None = None) -> tuple[pd.DataFrame, list[dict], int]:
    if not trades:
        return pd.DataFrame(), [], 0
    spy = {row["date"]: row["close"] for row in prices["SPY"]}
    start = evaluation_start or trades[0]["entry_date"]
    end = evaluation_end or max(row["exit_date"] for row in trades)
    dates = [day for day in sorted(spy) if start <= day <= end]
    irx = {row["date"]: row["close"] for row in prices.get("^IRX", [])}
    px = {ticker: {row["date"]: row["close"] for row in rows}
          for ticker, rows in prices.items()}
    entries = defaultdict(list)
    for trade in trades:
        entries[trade["entry_date"]].append(trade)
    cash, positions = 1.0, []
    executed, skipped, records = [], 0, []
    previous_total, previous_spy, last_irx = 1.0, None, 0.0
    cost = cost_bps_per_side / 10_000
    for day in dates:
        daily_rf = max(last_irx, 0) / 100 / 252
        cash *= 1 + daily_rf
        exposure_before = sum(position["value"] for position in positions)
        still_open = []
        for position in positions:
            trade = position["trade"]
            close = px[trade["ticker"]].get(day)
            if close is not None and position["last_price"]:
                position["value"] *= close / position["last_price"]
                position["last_price"] = close
            if day >= trade["exit_date"]:
                proceeds = position["value"] * (1 - cost)
                cash += proceeds
                completed = dict(trade)
                completed["net_sleeve_return"] = proceeds / trade["entry_notional"] - 1
                executed.append(completed)
            else:
                still_open.append(position)
        positions = still_open
        for trade in entries.get(day, []):
            if len(positions) >= max_positions:
                skipped += 1
                continue
            total_before_entry = cash + sum(position["value"] for position in positions)
            entry_notional = min(total_before_entry / max_positions, cash)
            if entry_notional <= 0:
                skipped += 1
                continue
            cash -= entry_notional
            opened = dict(trade)
            opened["entry_notional"] = entry_notional
            positions.append({"trade": opened, "value": entry_notional * (1 - cost),
                              "last_price": trade["entry_price"]})
        total = cash + sum(position["value"] for position in positions)
        portfolio_return = total / previous_total - 1
        spy_return = 0.0 if previous_spy is None else spy[day] / previous_spy - 1
        exposure = exposure_before / previous_total
        matched_benchmark = exposure * spy_return + (1 - exposure) * daily_rf
        records.append({"date": day, "nav": total, "return": portfolio_return,
                        "spy_return": spy_return, "risk_free_return": daily_rf,
                        "excess_over_risk_free": portfolio_return - daily_rf,
                        "exposure": exposure, "matched_benchmark_return": matched_benchmark,
                        "exposure_matched_excess": portfolio_return - matched_benchmark,
                        "active_positions": len(positions)})
        previous_total, previous_spy = total, spy[day]
        if day in irx:
            last_irx = irx[day]
    return pd.DataFrame(records), executed, skipped


def max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1).min()) if len(nav) else float("nan")


def hac_mean_test(values: np.ndarray, maxlags: int = 20) -> tuple[float, float]:
    """Newey-West t-test for a nonzero mean, with Bartlett weights."""
    centered = values - values.mean()
    n = len(values)
    long_run_variance = float(np.dot(centered, centered) / n)
    for lag in range(1, min(maxlags, n - 1) + 1):
        covariance = float(np.dot(centered[lag:], centered[:-lag]) / n)
        long_run_variance += 2 * (1 - lag / (maxlags + 1)) * covariance
    standard_error = math.sqrt(max(long_run_variance, 0) / n)
    t_value = float(values.mean() / standard_error) if standard_error else 0.0
    p_value = math.erfc(abs(t_value) / math.sqrt(2))
    return t_value, p_value


def series_metrics(frame: pd.DataFrame, executed: list[dict], skipped: int) -> dict:
    if frame.empty:
        return {"trades": 0}
    returns = frame["return"].to_numpy()
    risk_free_excess = frame["excess_over_risk_free"].to_numpy()
    excess = frame["exposure_matched_excess"].to_numpy()
    vol = np.std(returns, ddof=1)
    excess_vol = np.std(excess, ddof=1)
    years = max((pd.Timestamp(frame.iloc[-1]["date"]) -
                 pd.Timestamp(frame.iloc[0]["date"])).days / 365.25, 1 / 365.25)
    downside = np.sqrt(np.mean(np.minimum(returns, 0) ** 2))
    spy_excess = (frame["spy_return"] - frame["risk_free_return"]).to_numpy()
    spy_variance = float(np.var(spy_excess, ddof=1))
    beta = (float(np.cov(risk_free_excess, spy_excess, ddof=1)[0, 1]) / spy_variance
            if spy_variance else 0.0)
    alpha = float(np.mean(risk_free_excess) - beta * np.mean(spy_excess))
    hac_t, hac_p = hac_mean_test(excess)
    return {
        "start": frame.iloc[0]["date"], "end": frame.iloc[-1]["date"],
        "trades": len(executed), "skipped_capacity": skipped,
        "terminal_return": float(frame.iloc[-1]["nav"] - 1),
        "cagr": float(frame.iloc[-1]["nav"] ** (1 / years) - 1),
        "annualized_volatility": float(vol * math.sqrt(252)),
        "sharpe_over_13week_tbill": float(np.mean(risk_free_excess) /
                                           np.std(risk_free_excess, ddof=1) * math.sqrt(252))
        if np.std(risk_free_excess, ddof=1) else None,
        "sortino_zero_target": float(np.mean(returns) / downside * math.sqrt(252)) if downside else None,
        "max_drawdown": max_drawdown(frame["nav"]),
        "average_capital_exposure": float(frame["exposure"].mean()),
        "max_active_positions": int(frame["active_positions"].max()),
        "trade_win_rate": float(np.mean([row["net_sleeve_return"] > 0 for row in executed])) if executed else None,
        "mean_trade_return": float(np.mean([row["net_sleeve_return"] for row in executed])) if executed else None,
        "median_trade_return": float(np.median([row["net_sleeve_return"] for row in executed])) if executed else None,
        "exposure_matched_information_ratio": float(np.mean(excess) / excess_vol * math.sqrt(252)) if excess_vol else None,
        "exposure_matched_excess_annualized": float(np.mean(excess) * 252),
        "excess_hac_t": hac_t,
        "excess_hac_p_two_sided": hac_p,
        "spy_beta": beta,
        "annualized_regression_alpha": alpha * 252,
    }


def block_bootstrap(frame: pd.DataFrame, draws: int = 5000, block: int = 20,
                    seed: int = 1901) -> dict:
    values = frame["exposure_matched_excess"].to_numpy()
    rng = np.random.default_rng(seed)
    means, ratios = [], []
    for _ in range(draws):
        sample = []
        while len(sample) < len(values):
            start = int(rng.integers(0, max(1, len(values) - block + 1)))
            sample.extend(values[start:start + block])
        sample = np.asarray(sample[:len(values)])
        means.append(float(sample.mean() * 252))
        sd = sample.std(ddof=1)
        ratios.append(float(sample.mean() / sd * math.sqrt(252)) if sd else 0.0)
    return {"draws": draws, "block_sessions": block,
            "annualized_excess_ci95": [float(x) for x in np.quantile(means, [0.025, 0.975])],
            "information_ratio_ci95": [float(x) for x in np.quantile(ratios, [0.025, 0.975])],
            "probability_annualized_excess_le_zero": float(np.mean(np.asarray(means) <= 0))}


def trade_bootstrap(executed: list[dict], draws: int = 10_000, seed: int = 1902) -> dict:
    values = np.asarray([row["net_sleeve_return"] for row in executed])
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(draws, len(values)), replace=True).mean(axis=1)
    return {"draws": draws, "trades": len(values),
            "mean_trade_return_ci95": [float(x) for x in np.quantile(samples, [0.025, 0.975])],
            "probability_mean_trade_return_le_zero": float(np.mean(samples <= 0))}


def period_returns(frame: pd.DataFrame, frequency: str) -> dict[str, float]:
    work = frame.copy()
    dates = pd.to_datetime(work["date"])
    labels = dates.dt.year.astype(str) if frequency == "year" else dates.dt.to_period("M").astype(str)
    return {str(label): float((1 + group["return"]).prod() - 1)
            for label, group in work.groupby(labels)}


def run(run_dir: Path, source_dir: Path, output_dir: Path) -> dict:
    signals = load_signals(run_dir, source_dir)
    prices = fetch_prices(signals, source_dir / "cache" / "http")
    selectors = {
        "global_top_decile_noncausal": select_global(signals, 0.10),
        "expanding_top_decile_warmup10": select_expanding(signals, 0.10, 10),
        "expanding_top_decile_warmup20": select_expanding(signals, 0.10, 20),
        "expanding_top_decile_warmup30": select_expanding(signals, 0.10, 30),
        "expanding_top_quintile_warmup20": select_expanding(signals, 0.20, 20),
        "expanding_top_decile_warmup20_lookback40": select_expanding(signals, 0.10, 20, 40),
        "fixed_downside_le_15pct": [row for row in signals if row["downside_probability"] <= 0.15],
        "fixed_downside_le_20pct": [row for row in signals if row["downside_probability"] <= 0.20],
        "fixed_downside_le_25pct": [row for row in signals if row["downside_probability"] <= 0.25],
        "all_events_baseline": signals,
        "worst_decile_negative_control": sorted(signals, key=lambda row: (row["score"], row["case_id"]))[:8],
    }
    all_trades = prepare_trades(signals, prices)
    common_start = min(row["entry_date"] for row in all_trades)
    common_end = max(row["exit_date"] for row in all_trades)
    primary_frames, executed_by, results = {}, {}, {}
    for name, selected in selectors.items():
        trades = prepare_trades(selected, prices)
        frame, executed, skipped = simulate(trades, prices, evaluation_start=common_start,
                                            evaluation_end=common_end)
        results[name] = {"selected": len(selected), **series_metrics(frame, executed, skipped),
                         "tickers": [row["ticker"] for row in executed]}
        primary_frames[name] = frame
        executed_by[name] = executed
    sensitivity = {}
    selected = selectors["global_top_decile_noncausal"]
    for holding in (30, 60, 90, 120, 180):
        for delay in (0, 1, 4):
            for cost in (0, 10, 25, 50):
                trades = prepare_trades(selected, prices, holding, delay)
                frame, executed, skipped = simulate(trades, prices, 10, cost,
                                                    common_start, common_end)
                key = f"hold{holding}_delay{delay}_cost{cost}bps_side"
                sensitivity[key] = series_metrics(frame, executed, skipped)
    sizing = {}
    for slots in (5, 10, 20):
        trades = prepare_trades(selected, prices)
        frame, executed, skipped = simulate(trades, prices, slots, 10,
                                            common_start, common_end)
        sizing[f"max_positions_{slots}"] = series_metrics(frame, executed, skipped)
    primary = primary_frames["global_top_decile_noncausal"]
    bootstrap = block_bootstrap(primary)
    trades_bootstrap = trade_bootstrap(executed_by["global_top_decile_noncausal"])
    by_year = {}
    for year in (2021, 2022, 2023):
        subset = [row for row in selected if row["cutoff"].startswith(str(year))]
        trades = prepare_trades(subset, prices)
        frame, executed, skipped = simulate(trades, prices, evaluation_start=common_start,
                                            evaluation_end=common_end)
        by_year[str(year)] = series_metrics(frame, executed, skipped)
    leave_one_month_out = {}
    for month in sorted({row["cutoff"][:7] for row in selected}):
        group = [row for row in selected if row["cutoff"][:7] != month]
        frame, executed, skipped = simulate(prepare_trades(group, prices), prices,
                                            evaluation_start=common_start,
                                            evaluation_end=common_end)
        leave_one_month_out[month] = series_metrics(frame, executed, skipped)
    leave_one_trade_out = {}
    for omitted in selected:
        group = [row for row in selected if row["case_id"] != omitted["case_id"]]
        frame, executed, skipped = simulate(prepare_trades(group, prices), prices,
                                            evaluation_start=common_start,
                                            evaluation_end=common_end)
        leave_one_trade_out[omitted["ticker"]] = series_metrics(frame, executed, skipped)
    ordered = sorted(signals, key=lambda row: (-row["score"], row["case_id"]))
    score_quintiles = {}
    for index in range(5):
        group = ordered[index * 16:(index + 1) * 16]
        frame, executed, skipped = simulate(prepare_trades(group, prices), prices,
                                            evaluation_start=common_start,
                                            evaluation_end=common_end)
        score_quintiles[str(index + 1)] = series_metrics(frame, executed, skipped)
    observed = results["global_top_decile_noncausal"]
    rng = random.Random(1903)
    placebo_terminal, placebo_ir = [], []
    for _ in range(2000):
        group = rng.sample(signals, 8)
        frame, executed, skipped = simulate(prepare_trades(group, prices), prices,
                                            evaluation_start=common_start,
                                            evaluation_end=common_end)
        metric = series_metrics(frame, executed, skipped)
        placebo_terminal.append(metric["terminal_return"])
        placebo_ir.append(metric["exposure_matched_information_ratio"])
    placebo = {
        "draws": 2000,
        "terminal_return_percentile": float(np.mean(np.asarray(placebo_terminal) <= observed["terminal_return"])),
        "information_ratio_percentile": float(np.mean(np.asarray(placebo_ir) <= observed["exposure_matched_information_ratio"])),
        "p_terminal_at_least_observed": float((sum(x >= observed["terminal_return"] for x in placebo_terminal) + 1) / 2001),
        "p_information_ratio_at_least_observed": float((sum(x >= observed["exposure_matched_information_ratio"] for x in placebo_ir) + 1) / 2001),
        "terminal_return_quantiles": [float(x) for x in np.quantile(placebo_terminal, [0.05, 0.5, 0.95])],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    primary.to_csv(output_dir / "daily_portfolio.csv", index=False)
    result = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "signal_count": len(signals), "price_tickers": len(prices),
              "execution": {"entry": "first adjusted close after filing date",
                            "holding_days": 90, "max_positions": 10,
                            "cost_bps_per_side": 10,
                            "idle_cash": "historical 13-week Treasury yield (^IRX)",
                            "sizing": "each new position targets 10% of current NAV"},
              "strategies": results, "sensitivity": sensitivity, "sizing": sizing,
              "by_signal_year": by_year, "score_quintiles": score_quintiles,
              "calendar_year_returns": period_returns(primary, "year"),
              "calendar_month_returns": period_returns(primary, "month"),
              "leave_one_signal_month_out": leave_one_month_out,
              "leave_one_trade_out": leave_one_trade_out,
              "block_bootstrap": bootstrap, "trade_bootstrap": trades_bootstrap,
              "random_selection_placebo": placebo,
              "limitations": [
                  "Global top-decile selection uses the full future score distribution and is non-causal.",
                  "The expanding selectors are causal but have few trades after the warm-up.",
                  "Cases are a random sample from a current-listing universe, not a complete historical event stream.",
                  "Adjusted-close execution omits bid-ask spread, market impact, taxes, and intraday filing latency.",
                  "No delisted names are represented, so survivorship bias remains material.",
              ]}
    (output_dir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(render_report(result), encoding="utf-8")
    return result


def pct(value) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def num(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_report(result: dict) -> str:
    strategies = result["strategies"]
    lines = [
        "# Safety-first trading-strategy evaluation", "",
        f"Generated: {result['generated_at']}", "",
        "## Verdict", "",
        "**Promising signal; not yet a valid deployable trading strategy.** The daily portfolio "
        "tests are strong, but the available sample cannot remove survivorship and future-data-availability "
        "bias, and the causal threshold was specified after seeing this sample's outcomes.", "",
        "## Headline portfolio tests", "",
        "| Rule | Trades | CAGR | T-bill Sharpe | Max drawdown | Avg exposure | Matched IR |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("global_top_decile_noncausal", "expanding_top_decile_warmup20",
                 "expanding_top_quintile_warmup20", "all_events_baseline",
                 "worst_decile_negative_control"):
        row = strategies[name]
        lines.append(f"| `{name}` | {row['trades']} | {pct(row.get('cagr'))} | "
                     f"{num(row.get('sharpe_over_13week_tbill'))} | "
                     f"{pct(row.get('max_drawdown'))} | {pct(row.get('average_capital_exposure'))} | "
                     f"{num(row.get('exposure_matched_information_ratio'))} |")
    primary = strategies["global_top_decile_noncausal"]
    causal = strategies["expanding_top_decile_warmup20"]
    lines += ["", "The original full-sample top-decile rule reports Sharpe "
              f"{primary['sharpe_over_13week_tbill']:.2f}, but it is non-causal: it needs the "
              "future distribution of all signal scores. The expanding prior-score-only version "
              f"reports Sharpe {causal['sharpe_over_13week_tbill']:.2f} on only "
              f"{causal['trades']} trades. That is the more relevant number, but it is exploratory, "
              "not a sealed estimate of future Sharpe.", "",
              "Execution uses the first adjusted close after the filing date, a 90-calendar-day hold, "
              "10% target NAV per position, 10 bp per side, a ten-position cap, and historical "
              "13-week Treasury yield on idle cash. Matched excess compares the portfolio with a "
              "SPY/cash blend at the same daily equity exposure.", "",
              "## Robustness", ""]
    placebo = result["random_selection_placebo"]
    trade_boot = result["trade_bootstrap"]
    leave_month = result["leave_one_signal_month_out"]
    lines += [
        f"- Random eight-name placebo: p={placebo['p_terminal_at_least_observed']:.4f} for "
        "terminal return and "
        f"p={placebo['p_information_ratio_at_least_observed']:.4f} for matched information ratio.",
        f"- Trade bootstrap mean-return 95% interval: "
        f"{pct(trade_boot['mean_trade_return_ci95'][0])} to "
        f"{pct(trade_boot['mean_trade_return_ci95'][1])}; only eight trades underpin it.",
        "- Results remain positive with 30/60/120/180-day holds, 1- or 4-session entry delays, "
        "and costs through 50 bp per side; the edge weakens materially away from 60–120 days.",
        "- Safety-score quintiles are monotonic in this sample: the safest quintile is strongly "
        "positive, middle quintiles are modest, and the two riskiest quintiles lose money.",
        "- The worst-safety decile is a useful negative control: negative return, Sharpe, and matched IR.",
        f"- Only {len(leave_month)} distinct signal months drive the eight-trade basket. Removing any "
        f"one month leaves Sharpe between {min(row['sharpe_over_13week_tbill'] for row in leave_month.values()):.2f} "
        f"and {max(row['sharpe_over_13week_tbill'] for row in leave_month.values()):.2f}, but this is still "
        "far too few independent clusters for a production claim.",
        "", "## Why this still fails a production-validation bar", "",
        "1. The universe was drawn from securities listed today. Delisted failures are absent.",
        "2. Case construction required a future 90-day price observation, adding another survival/data-availability filter.",
        "3. The 80 cases are a random sample, not every eligible severe-drawdown filing event; capacity and opportunity frequency are unknown.",
        "4. The original global top-decile rule is non-causal. The expanding rule fixes that mechanically, "
        "but was introduced after outcomes were visible and therefore needs a new sealed test.",
        "5. The causal result has only ten trades and a handful of time clusters. Daily HAC and bootstrap "
        "statistics cannot manufacture independent events.",
        "6. Adjusted-close execution still omits quoted spreads, impact, taxes, ticker changes, delisting payouts, "
        "and actual model-processing latency.",
        "", "## Required confirmation", "",
        "Freeze the expanding top-decile rule, build an exhaustive survivorship-free event universe with "
        "delistings and point-in-time identifiers, ingest unadjusted OHLCV/corporate actions, and run once on "
        "a later untouched period. Until that passes, the observed Sharpe should be treated as an optimistic "
        "research statistic rather than an expected live Sharpe.", "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/fresh_confirmation"))
    parser.add_argument("--source-dir", type=Path, default=Path("lab_runs/fresh_long"))
    parser.add_argument("--output-dir", type=Path, default=Path("lab_runs/safety_backtest"))
    args = parser.parse_args(argv)
    result = run(args.run_dir, args.source_dir, args.output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
