#!/usr/bin/env python3
"""Fixed, costed UNG diagnostic, separate from satellite gas-demand accuracy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/gas_trading"
STRATEGIES = {
    "satellite": ("satellite", "baseline"),
    "weather": ("ground_hdd", "baseline"),
    "satellite_overlay": ("ground_hdd_satellite", "ground_hdd"),
}


def load_prices(path: Path) -> pd.Series:
    payload = json.loads(path.read_text())["chart"]
    if payload.get("error") or len(payload["result"]) != 1:
        raise ValueError("Invalid market data response")
    data = payload["result"][0]
    if data["meta"]["symbol"] != "UNG" or data["meta"]["currency"] != "USD":
        raise ValueError("Expected UNG in USD")
    dates = pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    prices = pd.Series(data["indicators"]["adjclose"][0]["adjclose"], index=dates, name="adjusted_close")
    if prices.index.duplicated().any() or not prices.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered price dates")
    if prices.isna().any() or not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Missing or invalid adjusted close")
    return prices


def event_return(direction: int, asset_return: float, days: int) -> dict:
    if direction not in (-1, 0, 1) or days <= 0:
        raise ValueError("Invalid position or holding period")
    transaction_cost = .005 * abs(direction)
    borrow_cost = .03 * days / 365 if direction < 0 else 0.0
    gross = direction * asset_return
    net = gross - transaction_cost - borrow_cost
    if net <= -1:
        raise ValueError("Position bankrupt; simple event compounding invalid")
    return {"direction": direction, "gross_return": gross, "transaction_cost": transaction_cost,
            "borrow_cost": borrow_cost, "net_return": net}


def make_events(forecasts: pd.DataFrame, prices: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = forecasts.rename(columns={"date": "target_month"}).copy()
    frame["target_month"] = pd.to_datetime(frame.target_month)
    frame["forecast_at"] = pd.to_datetime(frame.forecast_at)
    frame = frame.loc[frame.target_month.dt.year.between(2008, 2025)
                      & frame.target_month.dt.month.isin([1, 2, 3, 10, 11, 12])].sort_values("forecast_at")
    if frame.target_month.duplicated().any():
        raise ValueError("Duplicate target month")
    required = sorted({column for pair in STRATEGIES.values() for column in pair})
    rows, skipped = [], []
    last_exit = None
    for row in frame.to_dict("records"):
        month, issue = row["target_month"], row["forecast_at"]
        if issue <= month + pd.offsets.MonthEnd(0):
            raise ValueError("Economic nowcast precedes complete monthly input")
        if not all(pd.notna(row[name]) and np.isfinite(row[name]) for name in required):
            skipped.append({"target_month": month, "reason": "missing_complete_forecasts"})
            continue
        entry_idx = prices.index.searchsorted(issue, side="right")
        exit_idx = prices.index.searchsorted(issue + pd.DateOffset(months=1), side="right")
        if entry_idx >= len(prices) or exit_idx >= len(prices):
            skipped.append({"target_month": month, "reason": "incomplete_price_window"})
            continue
        entry, finish = prices.index[entry_idx], prices.index[exit_idx]
        if last_exit is not None and entry < last_exit:
            raise ValueError("Overlapping event windows")
        last_exit = finish
        days = (finish - entry).days
        asset_return = float(prices.iloc[exit_idx] / prices.iloc[entry_idx] - 1)
        directions = {name: int(np.sign(row[left] - row[right])) for name, (left, right) in STRATEGIES.items()}
        directions.update({"always_long": 1, "always_short": -1})
        for strategy, direction in directions.items():
            rows.append({"target_month": month, "forecast_at": issue,
                         "winter_year": month.year + int(month.month >= 10),
                         "entry": entry, "exit": finish, "holding_calendar_days": days,
                         "entry_adjusted_close": float(prices.iloc[entry_idx]),
                         "exit_adjusted_close": float(prices.iloc[exit_idx]),
                         "asset_return": asset_return, "strategy": strategy,
                         **event_return(direction, asset_return, days)})
    if not rows:
        raise ValueError("No complete trading events")
    return pd.DataFrame(rows), pd.DataFrame(skipped, columns=["target_month", "reason"])


def summarize(events: pd.DataFrame) -> dict:
    wide = events.pivot(index="target_month", columns="strategy", values="net_return").sort_index()
    if wide.isna().any().any():
        raise ValueError("Strategies must share event windows")
    winter = events.drop_duplicates("target_month").set_index("target_month").winter_year.reindex(wide.index)
    groups = [np.flatnonzero(winter.to_numpy() == year) for year in sorted(winter.unique())]
    rng = np.random.default_rng(20260927)
    boot = {name: [] for name in wide.columns}
    diff = {name: [] for name in STRATEGIES}
    values = wide.to_numpy()
    short_idx = wide.columns.get_loc("always_short")
    for _ in range(10000):
        indices = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        means = values[indices].mean(axis=0)
        for i, name in enumerate(wide.columns):
            boot[name].append(means[i])
            if name in diff:
                diff[name].append(means[i] - means[short_idx])
    scores = {}
    for name in wide.columns:
        selected = events.loc[events.strategy == name]
        returns = wide[name].to_numpy()
        scores[name] = {"n_events": len(returns), "compound_net_event_return": float(np.prod(1 + returns) - 1),
                        "mean_net_event_return": float(returns.mean()), "net_win_fraction": float((returns > 0).mean()),
                        "mean_net_event_return_winter_bootstrap_ci95": np.quantile(boot[name], [.025, .975]).tolist(),
                        "long_events": int(selected.direction.eq(1).sum()), "short_events": int(selected.direction.eq(-1).sum())}
        if name in diff:
            scores[name]["mean_net_advantage_vs_always_short_ci95"] = np.quantile(diff[name], [.025, .975]).tolist()
    return {"models": scores, "n_winter_blocks": len(groups), "first_target_month": str(wide.index.min().date()),
            "last_target_month": str(wide.index.max().date()), "trading_alpha_verified": False,
            "original_vintage_operational_verification": False,
            "interpretation": "Exploratory historical ETF strategy diagnostic; not annualized, not risk-adjusted alpha, and no complete original-vintage or borrow archive"}


def run(predictions: Path, out: Path = DEFAULT) -> dict:
    prices = load_prices(out / "inputs/ung_yahoo_chart.json")
    events, skipped = make_events(pd.read_csv(predictions), prices)
    summary = summarize(events)
    summary["protocol"] = json.loads((out / "protocol.json").read_text())
    summary["source_manifest"] = json.loads((out / "source_manifest.json").read_text())
    summary["prediction_file_sha256"] = hashlib.sha256(predictions.read_bytes()).hexdigest()
    summary["skipped_events"] = skipped.to_dict("records")
    prices.to_csv(out / "daily_ung_prices.csv", index_label="date", float_format="%.12g")
    events.to_csv(out / "events.csv", index=False, float_format="%.12g")
    skipped.to_csv(out / "skipped.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str, allow_nan=False) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=ROOT / "results/satellite_validation/gas/predictions.csv")
    parser.add_argument("--out", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(run(args.predictions, args.out)["models"], indent=2))
