#!/usr/bin/env python3
"""Slow-moving crop alpha: monthly, delayed, persistent, and ablation-first."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from .ers_futures_carry import carry_from_rows, contract_returns_from_rows, download, sha256
    from .nasa_crop_weather_backtest import metrics, normalize_gross, rv_signal, strategy_returns
except ImportError:
    from ers_futures_carry import carry_from_rows, contract_returns_from_rows, download, sha256
    from nasa_crop_weather_backtest import metrics, normalize_gross, rv_signal, strategy_returns


GRAINS = ["corn", "soy", "wheat"]


def monthly_positions(signal: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Observe each month-end signal and execute it on the following weekly bar."""
    signal = signal.reindex(index)
    decisions = signal.groupby(signal.index.to_period("M")).tail(1)
    held = decisions.reindex(index).ffill().shift(1)
    return held.fillna(0.0)


def slow_weather_signal(features: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    stress = pd.DataFrame({crop: features[(crop, "stress")] for crop in GRAINS})
    level_window = int(config["weather_level_weeks"])
    short_window = int(config["weather_trajectory_weeks"])
    level = stress.rolling(level_window, min_periods=max(4, level_window // 2)).mean()
    recent = stress.rolling(short_window, min_periods=short_window).mean()
    prior = recent.shift(short_window)
    # Persistent conditions dominate; trajectory only asks whether they are still worsening.
    outlook = (0.75 * level + 0.25 * (recent - prior)).shift(int(config["information_lag_weeks"]))
    relative = outlook.sub(outlook.mean(axis=1), axis=0).clip(-3, 3)
    return outlook, normalize_gross(relative)


def slow_rv_signal(prices: pd.DataFrame, config: dict) -> pd.DataFrame:
    rv = rv_signal(prices[GRAINS], int(config["rv_lookback_weeks"]), int(config["rv_min_weeks"]))
    smoothed = rv.rolling(int(config["rv_smoothing_weeks"]), min_periods=4).mean()
    smoothed = smoothed.sub(smoothed.mean(axis=1), axis=0)
    return normalize_gross(smoothed)


def slow_carry_signal(carry: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Vol-scale annualized backwardation, preserve its level, then compare across grains."""
    carry = carry.reindex(columns=GRAINS)
    volatility = carry.rolling(
        int(config["carry_vol_lookback_weeks"]),
        min_periods=int(config["carry_vol_min_weeks"]),
    ).std()
    scaled = (carry / volatility.replace(0, np.nan)).clip(-4, 4)
    smoothed = scaled.rolling(int(config["carry_smoothing_weeks"]), min_periods=4).mean()
    relative = smoothed.sub(smoothed.mean(axis=1), axis=0)
    return normalize_gross(relative)


def weather_conditioned_rv(rv: pd.DataFrame, outlook: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Haircut RV legs that fight persistent supply stress; never lever aligned legs."""
    scale = float(config["stress_scale"])
    severity = (outlook.abs() / scale).clip(0, 1).fillna(0)
    conflicts = (np.sign(rv) != np.sign(outlook)) & outlook.notna()
    multiplier = 1.0 - float(config["weather_conflict_haircut"]) * severity.where(conflicts, 0)
    adjusted = rv * multiplier
    adjusted = adjusted.sub(adjusted.mean(axis=1), axis=0)
    return normalize_gross(adjusted)


def yield_outlook_signal(vintages: pd.DataFrame, index: pd.DatetimeIndex,
                         max_age_weeks: int) -> pd.DataFrame:
    """Convert expanding-window yield forecasts to a slow, expiry-limited supply signal."""
    out = pd.DataFrame(np.nan, index=index, columns=GRAINS)
    for crop, rows in vintages.groupby("crop"):
        if crop not in GRAINS:
            continue
        rows = rows.sort_values("forecast_date")
        for row in rows.itertuples():
            observed = pd.Timestamp(row.forecast_date)
            # Higher expected yield is bearish; do not carry one vintage beyond six months.
            valid = (index >= observed) & (index <= observed + pd.Timedelta(weeks=max_age_weeks))
            out.loc[valid, crop] = -float(row.predicted_yield_anomaly)
    relative = out.sub(out.mean(axis=1), axis=0)
    return normalize_gross(relative)


def blend(*weighted_signals: tuple[float, pd.DataFrame]) -> pd.DataFrame:
    total = sum(weight * signal for weight, signal in weighted_signals)
    # Each sleeve is already dollar-neutral and gross-normalized. Do not re-lever the residual
    # when sleeves disagree: fixed capital weights should be allowed to cancel into cash.
    return total.sub(total.mean(axis=1), axis=0).fillna(0.0)


def run(config: dict, prices: pd.DataFrame, features: pd.DataFrame,
        vintages: pd.DataFrame, carry_panel: pd.DataFrame,
        roll_flags: pd.DataFrame | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prices = prices[GRAINS].copy()
    prices = prices[prices.index <= pd.Timestamp.today().normalize()]
    index = prices.index
    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)

    outlook, weather = slow_weather_signal(features.reindex(index), config)
    rv = slow_rv_signal(prices, config)
    carry = slow_carry_signal(carry_panel.reindex(index), config)
    conditioned = weather_conditioned_rv(rv, outlook, config)
    yield_signal = yield_outlook_signal(
        vintages, index, int(config["yield_max_age_weeks"]))

    w_weather = float(config["weather_overlay_weight"])
    w_yield = float(config["yield_overlay_weight"])
    w_carry = float(config["carry_overlay_weight"])
    weekly_targets = {
        "slow_weather_only": weather,
        "slow_rv": rv,
        "slow_carry": carry,
        "weather_conditioned_rv": conditioned,
        "rv_plus_weather": blend((1 - w_weather, rv), (w_weather, weather)),
        "rv_plus_carry": blend((1 - w_carry, rv), (w_carry, carry)),
        "slow_yield_only": yield_signal,
        "rv_plus_yield": blend((1 - w_yield, rv), (w_yield, yield_signal)),
        "rv_weather_yield": blend((1 - w_weather - w_yield, rv),
                                  (w_weather, weather), (w_yield, yield_signal)),
        "rv_carry_weather": blend((0.60, rv), (0.20, carry), (0.20, weather)),
        "rv_carry_yield": blend((0.60, rv), (0.20, carry), (0.20, yield_signal)),
        "rv_carry_weather_yield": blend((0.40, rv), (0.20, carry),
                                        (0.20, weather), (0.20, yield_signal)),
    }
    positions = {name: monthly_positions(target, index) for name, target in weekly_targets.items()}
    strategy_rets, stats = {}, {}
    start = pd.Timestamp(config["evaluation_start"])
    yield_start = pd.Timestamp(vintages["forecast_date"].min())
    holdout = pd.Timestamp(config["holdout_start"])
    for name, pos in positions.items():
        ret, turnover = strategy_returns(returns, pos, float(config["transaction_cost_bps_per_turnover"]))
        if roll_flags is not None:
            aligned_rolls = roll_flags.reindex(index=returns.index, columns=GRAINS).fillna(False)
            roll_turnover = (pos.reindex(returns.index).abs() * aligned_rolls.astype(float)).sum(axis=1)
            ret = ret - roll_turnover * float(config["contract_roll_cost_bps"]) / 10_000.0
            turnover = turnover + roll_turnover
        strategy_rets[name] = ret
        effective_start = max(start, yield_start) if "yield" in name else start
        slices = {"full": effective_start, "common_yield_window": yield_start, "holdout_2021": holdout}
        stats[name] = {}
        for label, slice_start in slices.items():
            result = metrics(ret.loc[slice_start:], turnover.loc[slice_start:])
            result["avg_gross_exposure"] = round(
                float(pos.reindex(returns.index).loc[slice_start:].abs().sum(axis=1).mean()), 3)
            stats[name][label] = result
    diagnostic = pd.concat(
        {"weather_outlook": outlook, **{f"target_{name}": value for name, value in weekly_targets.items()}},
        axis=1,
    )
    return stats, pd.DataFrame(strategy_rets), pd.concat(positions, axis=1), diagnostic


def table(stats: dict, slice_name: str) -> str:
    columns = ["annual_return_pct", "annual_vol_pct", "sharpe", "max_drawdown_pct", "avg_gross_exposure",
               "avg_weekly_turnover", "hac_mean_tstat", "final_growth_of_1"]
    lines = ["| Strategy | " + " | ".join(c.replace("_", " ") for c in columns) + " |",
             "|---" * (len(columns) + 1) + "|"]
    for name, slices in stats.items():
        row = slices[slice_name]
        lines.append("| " + name + " | " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(lines)


def sensitivity_table(sensitivity: dict) -> str:
    lines = [
        "| Specification | RV + carry common Sharpe | RV + carry 2021 Sharpe | "
        "All four common Sharpe | All four 2021 Sharpe |",
        "|---|---|---|---|---|",
    ]
    for label, stats in sensitivity.items():
        carry = stats["rv_plus_carry"]
        all_signal = stats["rv_carry_weather_yield"]
        lines.append(
            f"| {label} | {carry['common_yield_window']['sharpe']} | "
            f"{carry['holdout_2021']['sharpe']} | "
            f"{all_signal['common_yield_window']['sharpe']} | "
            f"{all_signal['holdout_2021']['sharpe']} |"
        )
    return "\n".join(lines)


def carry_sensitivity_table(sensitivity: dict) -> str:
    lines = [
        "| Carry specification | Carry common Sharpe | Carry 2021 Sharpe | "
        "RV + carry common Sharpe | RV + carry 2021 Sharpe | All four common Sharpe | All four 2021 Sharpe |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, stats in sensitivity.items():
        carry = stats["slow_carry"]
        rv_carry = stats["rv_plus_carry"]
        all_signal = stats["rv_carry_weather_yield"]
        lines.append(
            f"| {label} | {carry['common_yield_window']['sharpe']} | {carry['holdout_2021']['sharpe']} | "
            f"{rv_carry['common_yield_window']['sharpe']} | {rv_carry['holdout_2021']['sharpe']} | "
            f"{all_signal['common_yield_window']['sharpe']} | {all_signal['holdout_2021']['sharpe']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/slow_crop_alpha.json"))
    parser.add_argument("--weather-dir", type=Path, default=Path("research/weather_commodities"))
    parser.add_argument("--yield-dir", type=Path, default=Path("research/yield_model"))
    parser.add_argument("--output", type=Path, default=Path("research/slow_crop_alpha"))
    parser.add_argument("--refresh-carry", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    features = pd.read_parquet(args.weather_dir / "weekly_weather_features.parquet")
    vintages = pd.read_parquet(args.yield_dir / "national_yield_vintages.parquet")
    vintages = vintages[vintages["model"] == "weather_plus_optical"].copy()
    args.output.mkdir(parents=True, exist_ok=True)
    carry_raw = args.output / "raw/ers_inputdata.csv"
    download(config["carry_source_url"], carry_raw, args.refresh_carry)
    carry_rows = pd.read_csv(carry_raw, low_memory=False)
    carry_panel, carry_pairs = carry_from_rows(carry_rows, int(config["carry_min_days_to_expiry"]))
    contract_returns, roll_flags = contract_returns_from_rows(carry_rows, carry_pairs)
    # A cumulative contract-consistent index supports RV without the artificial jumps found in
    # generic unadjusted front-month series. Actual strategy P&L uses the same weekly returns.
    prices = 100.0 * (1.0 + contract_returns.fillna(0.0)).cumprod()
    carry_panel.to_parquet(args.output / "raw/ers_weekly_carry.parquet")
    carry_pairs.to_parquet(args.output / "raw/ers_carry_contract_pairs.parquet", index=False)
    contract_returns.to_parquet(args.output / "raw/ers_contract_returns.parquet")
    roll_flags.to_parquet(args.output / "raw/ers_contract_roll_flags.parquet")

    stats, returns, positions, diagnostic = run(
        config, prices, features, vintages, carry_panel, roll_flags)
    sensitivity_specs = {
        "2-week lag / 8-week level": {"information_lag_weeks": 2, "weather_level_weeks": 8},
        "base: 4-week lag / 12-week level": {},
        "8-week lag / 12-week level": {"information_lag_weeks": 8, "weather_level_weeks": 12},
        "4-week lag / 26-week level": {"information_lag_weeks": 4, "weather_level_weeks": 26},
    }
    sensitivity = {}
    for label, overrides in sensitivity_specs.items():
        variant = {**config, **overrides}
        sensitivity[label] = run(
            variant, prices, features, vintages, carry_panel, roll_flags)[0]
    carry_sensitivity_specs = {
        "21-day expiry buffer / 13-week smoothing": (21, 13),
        "base: 28-day buffer / 13-week smoothing": (28, 13),
        "42-day expiry buffer / 13-week smoothing": (42, 13),
        "28-day expiry buffer / 26-week smoothing": (28, 26),
    }
    carry_sensitivity = {}
    for label, (buffer_days, smoothing_weeks) in carry_sensitivity_specs.items():
        variant_carry, variant_pairs = carry_from_rows(carry_rows, buffer_days)
        variant_returns, variant_rolls = contract_returns_from_rows(carry_rows, variant_pairs)
        variant_prices = 100.0 * (1.0 + variant_returns.fillna(0.0)).cumprod()
        variant_config = {**config, "carry_smoothing_weeks": smoothing_weeks}
        carry_sensitivity[label] = run(
            variant_config, variant_prices, features, vintages, variant_carry, variant_rolls)[0]
    returns.to_parquet(args.output / "strategy_returns.parquet")
    positions.to_parquet(args.output / "positions.parquet")
    diagnostic.to_parquet(args.output / "signal_diagnostics.parquet")
    (args.output / "metrics.json").write_text(json.dumps(stats, indent=2))
    (args.output / "sensitivity.json").write_text(json.dumps(sensitivity, indent=2))
    (args.output / "carry_sensitivity.json").write_text(json.dumps(carry_sensitivity, indent=2))

    fig, ax = plt.subplots(figsize=(11, 6))
    for name in ["slow_rv", "rv_plus_carry", "rv_weather_yield", "rv_carry_weather_yield"]:
        growth = (1 + returns[name].loc[config["evaluation_start"]:]).cumprod()
        ax.plot(growth.index, growth, label=name.replace("_", " "))
    ax.set_yscale("log")
    ax.set_title("Slow crop alpha: monthly decisions, net of costs")
    ax.set_ylabel("Growth of $1 (log scale)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(args.output / "equity_curves.png", dpi=160)
    plt.close(fig)

    report = f"""# Slow-moving crop alpha backtest

## Design

- Monthly decisions, executed on the following weekly bar.
- NASA weather is delayed {config['information_lag_weeks']} weeks and smoothed over
  {config['weather_level_weeks']} weeks; the shorter trajectory is an explicitly subordinate feature.
- Grain relative value is smoothed over {config['rv_smoothing_weeks']} weeks.
- Yield vintages come from expanding prior-year models and expire after
  {config['yield_max_age_weeks']} weeks.
- Carry is annualized log front/deferred spread from USDA ERS weekly CBOT settlements. Contracts
  inside {config['carry_min_days_to_expiry']} days of approximate expiry are excluded, and the
  signal is smoothed over {config['carry_smoothing_weeks']} weeks.
- Returns hold the selected contract consistently between observations, eliminating generic-series
  roll jumps. Results include {config['transaction_cost_bps_per_turnover']} bps per signal turnover
  and {config['contract_roll_cost_bps']} bps per scheduled contract roll.
- Sleeve weights are fixed capital weights. Conflicting signals cancel into cash rather than being
  dynamically re-levered; average gross exposure is reported for every strategy.

## Finding

On the common 2011-present window, slow RV returned
{stats['slow_rv']['common_yield_window']['annual_return_pct']}% annualized with a
{stats['slow_rv']['common_yield_window']['sharpe']} Sharpe and
{stats['slow_rv']['common_yield_window']['max_drawdown_pct']}% maximum drawdown. Adding carry alone
changed those figures to {stats['rv_plus_carry']['common_yield_window']['annual_return_pct']}%,
{stats['rv_plus_carry']['common_yield_window']['sharpe']} and
{stats['rv_plus_carry']['common_yield_window']['max_drawdown_pct']}%. The fixed four-signal
RV/carry/weather/yield blend produced
{stats['rv_carry_weather_yield']['common_yield_window']['annual_return_pct']}%,
{stats['rv_carry_weather_yield']['common_yield_window']['sharpe']} and
{stats['rv_carry_weather_yield']['common_yield_window']['max_drawdown_pct']}%, respectively.

The 2021-present four-signal Sharpe is
{stats['rv_carry_weather_yield']['holdout_2021']['sharpe']} versus
{stats['slow_rv']['holdout_2021']['sharpe']} for RV. Its HAC mean t-statistics are
{stats['rv_carry_weather_yield']['common_yield_window']['hac_mean_tstat']} on the common sample and
{stats['rv_carry_weather_yield']['holdout_2021']['hac_mean_tstat']} since 2021. The latter period was
not an untouched holdout when this combined design was created, so the result remains a research
lead rather than a deployable alpha claim.

## Full available sample

{table(stats, 'full')}

Yield strategies start at the first genuine out-of-sample yield vintage; non-yield strategies use
the configured 2006 start after weather/RV warm-up.

## Common 2011-present comparison

{table(stats, 'common_yield_window')}

## Sealed-style 2021-present slice

{table(stats, 'holdout_2021')}

## Latency and persistence sensitivity

{sensitivity_table(sensitivity)}

These are fixed robustness cases, not a parameter search; the base specification remains the
declared model regardless of which row performs best.

## Carry construction sensitivity

{carry_sensitivity_table(carry_sensitivity)}

The expiry buffers and smoothing horizons above were declared as robustness cases and are not used
to select the base result.

## Interpretation rule

The test asks whether slow crop information improves the pre-existing RV baseline. A higher raw
return is insufficient: the overlay should also improve Sharpe or drawdown in the 2021 slice and
remain useful with the four-week information handicap. No weights or horizons were optimized.

## Known limits

- Reprocessed NASA/USDA archives are not original release-vintage files.
- USDA ERS settlements are weekly Thursday observations sourced by ERS from LSEG, not daily tick
  histories; execution and slippage remain simplified.
- US MODIS-derived yield vintages cover corn, soy and wheat; the global weather layer is broader
  only in the separate soft-commodity screen.
"""
    (args.output / "report.md").write_text(report)
    manifest = {
        "config": config,
        "inputs": {
            "contract_returns": str(args.output / "raw/ers_contract_returns.parquet"),
            "weather_features": str(args.weather_dir / "weekly_weather_features.parquet"),
            "yield_vintages": str(args.yield_dir / "national_yield_vintages.parquet"),
            "carry_contract_settlements": config["carry_source_url"],
        },
        "decision_frequency": "monthly; next weekly bar execution",
        "carry_definition": "annualized log(front/deferred); positive backwardation; USDA ERS CBOT weekly settlements",
        "carry_raw_sha256": sha256(carry_raw),
        "carry_coverage": {
            "start": str(carry_panel.index.min().date()),
            "end": str(carry_panel.index.max().date()),
            "contract_pair_rows": len(carry_pairs),
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
