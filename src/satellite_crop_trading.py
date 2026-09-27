#!/usr/bin/env python3
"""Fixed-rule, small-sample market check of wheat heading satellite forecasts.

No outcome yield columns enter the signals. Available dates are upstream model
assumptions rather than certified asset-vintage timestamps. This is exploratory
market evidence and cannot upgrade the upstream data to point-in-time proof.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_WHEAT_STATES = ("CO", "ID", "KS", "MN", "MT", "ND", "NE", "OK", "OR", "SD", "TX", "WA")

RULE = {
    "crop": "wheat", "stage": "heading", "holding_weeks": 12,
    "entry": "first Friday strictly after common available_date; preceding return excluded",
    "direction": "negative sign of production-weighted predicted yield anomaly",
    "satellite_overlay_direction": "negative sign of satellite prediction minus weather prediction",
    "entry_cost_bps": 25, "exit_cost_bps": 25, "roll_cost_bps": 5,
    "notional": "1x collateral, rebalanced weekly; collateral cash interest excluded",
    "missing": "abstain if any required state forecast, return, or roll flag is missing",
    "required_states": list(REQUIRED_WHEAT_STATES),
    "state_universe_source": "positive wheat weights in results/satellite_validation/third_signal/input_config.json, frozen before market evaluation",
    "selection": "wheat heading selected during research; no rule or horizon optimization in this market check",
}
STRATEGIES = ("weather_only", "weather_plus_ndvi", "incremental_satellite", "always_long_control")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signals_from_predictions(predictions: pd.DataFrame, required_states=REQUIRED_WHEAT_STATES) -> pd.DataFrame:
    models = {"weather_only", "weather_plus_ndvi"}
    selected = predictions[(predictions["crop"] == "wheat") & (predictions["stage"] == "heading")
                           & predictions["model"].isin(models)].copy()
    if selected.empty:
        raise ValueError("No wheat heading forecasts")
    states = set(required_states)
    if not states:
        raise ValueError("A nonempty frozen state universe is required")
    rows = []
    for year, group in selected.groupby("year", sort=True):
        row = {"year": int(year), "status": "eligible", "reason": ""}
        forecasts = {}
        for model in sorted(models):
            part = group[group["model"] == model].set_index("state").sort_index()
            if part.index.duplicated().any():
                raise ValueError(f"Duplicate state forecast in {year}/{model}")
            fields = ["prediction_anomaly", "production_weight", "available_date"]
            if set(part.index) != states or part[fields].isna().any().any():
                row.update(status="abstain", reason="missing state forecast or availability")
                break
            w = pd.to_numeric(part["production_weight"], errors="coerce")
            y = pd.to_numeric(part["prediction_anomaly"], errors="coerce")
            if not np.isfinite(w).all() or not np.isfinite(y).all() or (w < 0).any() or w.sum() <= 0:
                row.update(status="abstain", reason="invalid forecast or production weight")
                break
            forecasts[model] = (float(np.average(y, weights=w)), w)
        if row["status"] == "eligible":
            if not forecasts["weather_only"][1].equals(forecasts["weather_plus_ndvi"][1]):
                raise ValueError(f"Model production weights differ in {year}")
            available = pd.to_datetime(group["available_date"], errors="coerce")
            if available.isna().any():
                row.update(status="abstain", reason="invalid availability date")
            else:
                weather, satellite = forecasts["weather_only"][0], forecasts["weather_plus_ndvi"][0]
                row.update(available_date=available.max().normalize(), states=len(states),
                           weather_anomaly=weather, satellite_anomaly=satellite,
                           incremental_anomaly=satellite - weather,
                           weather_only=int(-np.sign(weather)), weather_plus_ndvi=int(-np.sign(satellite)),
                           incremental_satellite=int(-np.sign(satellite - weather)), always_long_control=1)
        rows.append(row)
    return pd.DataFrame(rows)


def first_friday_after(available) -> pd.Timestamp:
    date = pd.Timestamp(available).normalize()
    days = (4 - date.weekday()) % 7
    return date + pd.Timedelta(days=days or 7)


def evaluate_trade(market: pd.DataFrame, available, side: int) -> dict:
    entry = first_friday_after(available)
    dates = pd.date_range(entry + pd.Timedelta(days=7), periods=RULE["holding_weeks"], freq="W-FRI")
    held = market.reindex(dates)
    result = {"entry_date": str(entry.date()), "exit_date": str(dates[-1].date()), "side": side,
              "status": "eligible", "reason": ""}
    if side == 0:
        return {**result, "status": "cash", "gross_return": 0., "net_return": 0., "rolls": 0,
                "total_cost_bps": 0., "weekly_observations": 0}
    if held[["wheat_return", "rolled_at_start"]].isna().any().any():
        return {**result, "status": "abstain", "reason": "missing required weekly return or roll flag"}
    returns = pd.to_numeric(held["wheat_return"], errors="coerce").to_numpy()
    if not np.isfinite(returns).all() or (returns <= -1).any():
        return {**result, "status": "abstain", "reason": "invalid weekly return"}
    flags = held["rolled_at_start"].astype(str).str.lower().map({"true": 1., "false": 0., "1": 1., "0": 0.})
    if flags.isna().any():
        return {**result, "status": "abstain", "reason": "invalid roll flag"}
    costs = flags.to_numpy() * RULE["roll_cost_bps"] / 10000
    costs[0] += RULE["entry_cost_bps"] / 10000
    costs[-1] += RULE["exit_cost_bps"] / 10000
    if ((1 + side * returns - costs) <= 0).any():
        return {**result, "status": "abstain", "reason": "non-positive collateral value; margin path unsupported"}
    return {**result, "gross_return": float(np.prod(1 + side * returns) - 1),
            "net_return": float(np.prod(1 + side * returns - costs) - 1),
            "rolls": int(flags.sum()), "total_cost_bps": float(costs.sum() * 10000),
            "weekly_observations": len(held)}


def evaluate(signals: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    if market.index.duplicated().any() or any(market.index.weekday != 4):
        raise ValueError("Market index must contain distinct Fridays")
    rows = []
    for signal in signals.to_dict("records"):
        for strategy in STRATEGIES:
            row = {"year": signal["year"], "strategy": strategy, "available_date": signal.get("available_date")}
            if signal["status"] != "eligible":
                rows.append({**row, "status": "abstain", "reason": signal["reason"]})
            else:
                rows.append({**row, **evaluate_trade(market, signal["available_date"], int(signal[strategy]))})
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame) -> dict:
    trades = trades.copy()
    for column in ("net_return", "entry_date", "exit_date"):
        if column not in trades:
            trades[column] = np.nan
    results = {}
    for strategy in STRATEGIES:
        selected = trades[(trades.strategy == strategy) & trades.status.isin(["eligible", "cash"])]
        returns = selected["net_return"].to_numpy() if len(selected) else np.array([])
        if len(selected) > 1:
            ordered = selected.sort_values("entry_date")
            if (pd.to_datetime(ordered.entry_date).iloc[1:].to_numpy() <=
                    pd.to_datetime(ordered.exit_date).iloc[:-1].to_numpy()).any():
                raise ValueError("Overlapping annual windows cannot compound as a single 1x strategy")
        results[strategy] = {
            "event_years": int(len(returns)), "positive_events": int((returns > 0).sum()),
            "mean_event_net_return": float(returns.mean()) if len(returns) else None,
            "compound_net_return_across_events": float(np.prod(1 + returns) - 1) if len(returns) else None,
            "median_event_net_return": float(np.median(returns)) if len(returns) else None,
            "worst_event_net_return": float(returns.min()) if len(returns) else None,
        }
    pair = trades[trades.status.isin(["eligible", "cash"])].pivot(index="year", columns="strategy", values="net_return")
    pair = pair.reindex(columns=["weather_only", "weather_plus_ndvi"]).dropna()
    differences = (pair.weather_plus_ndvi - pair.weather_only).to_numpy()
    pvalue = None
    if 0 < len(differences) <= 16:
        observed = abs(differences.mean())
        randomized = [abs(np.mean(differences * signs)) for signs in itertools.product([-1, 1], repeat=len(differences))]
        pvalue = float(np.mean(np.asarray(randomized) >= observed - 1e-15))
    return {"strategies": results, "paired_satellite_minus_weather": {
        "event_years": len(differences), "mean_net_return_difference": float(differences.mean()) if len(differences) else None,
        "two_sided_sign_flip_p_value": pvalue,
        "inference_limit": "descriptive, low-power test; exchangeable symmetric paired effects assumed; candidate was selected during research",
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=Path("results/satellite_validation/third_signal/predictions.csv"))
    parser.add_argument("--market-snapshot", type=Path)
    parser.add_argument("--raw-dir", type=Path, help="Optional explicit importer of ERS parquet caches; defaults to committed CSV snapshot")
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("results/satellite_validation/trading"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inputs = [{"path": str(args.predictions), "sha256": sha256(args.predictions)}]
    if args.raw_dir and args.market_snapshot:
        parser.error("Choose --raw-dir or --market-snapshot, not both")
    market_provenance = {}
    if args.raw_dir:
        rp, fp = args.raw_dir / "ers_contract_returns.parquet", args.raw_dir / "ers_contract_roll_flags.parquet"
        market = pd.DataFrame({"wheat_return": pd.read_parquet(rp)["wheat"],
                               "rolled_at_start": pd.read_parquet(fp)["wheat"]}).loc["2018":"2026"].copy()
        inputs.extend({"path": str(p), "sha256": sha256(p)} for p in (rp, fp))
        manifest = args.raw_dir.parent / "manifest.json"
        if manifest.exists():
            inputs.append({"path": str(manifest), "sha256": sha256(manifest), "metadata": json.loads(manifest.read_text())})
        market_provenance = {"source_inputs": inputs[1:]}
    else:
        snapshot = args.market_snapshot or args.output_dir / "weekly_wheat_inputs.csv"
        if not snapshot.exists():
            parser.error("Committed market snapshot absent; pass --market-snapshot or import explicitly with --raw-dir")
        market = pd.read_csv(snapshot, parse_dates=["date"], float_precision="round_trip").set_index("date")
        inputs.append({"path": str(snapshot), "sha256": sha256(snapshot)})
        provenance = snapshot.parent / "market_input_provenance.json"
        if provenance.exists():
            market_provenance = json.loads(provenance.read_text())
    market.index.name = "date"
    market.to_csv(args.output_dir / "weekly_wheat_inputs.csv")
    market_provenance["snapshot_sha256"] = sha256(args.output_dir / "weekly_wheat_inputs.csv")
    (args.output_dir / "market_input_provenance.json").write_text(json.dumps(market_provenance, indent=2) + "\n")
    signals = signals_from_predictions(pd.read_csv(args.predictions))
    signals.to_csv(args.output_dir / "signals.csv", index=False)
    trades = evaluate(signals, market)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    report = {"rule": RULE, "strict_point_in_time_certified": False, "tuned_on_market_returns": False,
              "sample": "corrected exploratory 2018-2024 research sample", "inputs": inputs,
              "market_provenance": market_provenance, "code_sha256": sha256(Path(__file__)), **summarize(trades)}
    if args.confirmation:
        extra_signals = signals_from_predictions(pd.read_csv(args.confirmation))
        extra = evaluate(extra_signals, market)
        extra_signals.to_csv(args.output_dir / "confirmation_signals.csv", index=False)
        extra.to_csv(args.output_dir / "confirmation_trades.csv", index=False)
        report["confirmation"] = {"label": "corrected 2025 exploratory confirmation; invalid-stage outcome already inspected, not pristine holdout",
                                  "input_sha256": sha256(args.confirmation), **summarize(extra)}
    report["limitations"] = [
        "Seven research event-years are not 84 independent weekly bets; no full-sample Sharpe is reported",
        "The signal's historical available_date is an upstream assumption, not original-vintage publication proof",
        "Current-vintage yield/weather/NDVI and retrospective crop/state weights limit tradeability claims",
        "Weekly contract-consistent returns avoid roll-price jumps but do not capture daily margin or exact execution slippage",
        "Friday labels are weekly observation boundaries, not a verified exchange holiday execution calendar",
        "Collateral earns zero here; commissions, market impact beyond fixed fees, taxes, and financing are excluded",
        "US production is only one determinant of global wheat prices; forecast accuracy does not establish price alpha",
    ]
    (args.output_dir / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"research": report["strategies"], "paired": report["paired_satellite_minus_weather"]}, indent=2))


if __name__ == "__main__":
    main()
