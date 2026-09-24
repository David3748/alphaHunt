#!/usr/bin/env python3
"""NASA crop-weather and grain relative-value backtest.

This is an indicative research backtest, not a claim that current reprocessed
NASA/MERRA-2 values reproduce the exact files visible to traders historically.
Raw downloads and derived tables are cached so each run remains auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf


POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETERS = "PRECTOTCORR,T2M_MAX,T2M_MIN"
ANNUAL_WEEKS = 52.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def fetch_power_point(name: str, lat: float, lon: float, start: str, end: str,
                      cache: Path, refresh: bool = False) -> pd.DataFrame:
    cache.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    # POWER daily requests are split to keep responses modest and retryable.
    ranges = [(start, "20191231"), ("20200101", end)]
    for chunk_start, chunk_end in ranges:
        if chunk_start > end or chunk_end < start:
            continue
        chunk_start, chunk_end = max(start, chunk_start), min(end, chunk_end)
        target = cache / f"power_{name}_{chunk_start}_{chunk_end}.json"
        if refresh or not target.exists():
            params = {
                "parameters": PARAMETERS,
                "community": "AG",
                "longitude": lon,
                "latitude": lat,
                "start": chunk_start,
                "end": chunk_end,
                "format": "JSON",
            }
            last_error: Exception | None = None
            for attempt in range(5):
                try:
                    response = requests.get(POWER_URL, params=params, timeout=120)
                    response.raise_for_status()
                    payload = response.json()
                    if "properties" not in payload:
                        raise ValueError(payload)
                    atomic_json(target, payload)
                    break
                except Exception as exc:  # network retries are intentionally narrow
                    last_error = exc
                    time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"NASA POWER failed for {name}: {last_error}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        params = payload["properties"]["parameter"]
        frame = pd.DataFrame(params)
        frame.index = pd.to_datetime(frame.index, format="%Y%m%d")
        frame = frame.replace(float(payload["header"]["fill_value"]), np.nan)
        frames.append(frame.astype(float))
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out["region"] = name
    return out


def fetch_prices(config: dict, start: str, end: str, cache: Path,
                 refresh: bool = False) -> pd.DataFrame:
    target = cache / "grain_futures_yahoo.csv"
    cache.mkdir(parents=True, exist_ok=True)
    tickers = {k: v["ticker"] for k, v in config["commodities"].items()}
    cached_columns: set[str] = set()
    if target.exists():
        cached_columns = set(pd.read_csv(target, nrows=1).columns) - {"date"}
    if refresh or not target.exists() or not set(tickers).issubset(cached_columns):
        frames = []
        for commodity, ticker in tickers.items():
            raw = yf.download(ticker, start=start, end=end, auto_adjust=False,
                              progress=False, timeout=60)
            if raw.empty:
                raise RuntimeError(f"No price history returned for {ticker}")
            series = raw["Close"]
            if isinstance(series, pd.DataFrame):
                series = series.iloc[:, 0]
            frames.append(series.rename(commodity))
        pd.concat(frames, axis=1).sort_index().to_csv(target, index_label="date")
    prices = pd.read_csv(target, index_col="date", parse_dates=True)
    return prices.loc[pd.Timestamp(start):pd.Timestamp(end)].astype(float)


def expanding_seasonal_z(series: pd.Series, min_years: int = 5) -> pd.Series:
    iso_week = series.index.isocalendar().week.astype(int)
    result = pd.Series(np.nan, index=series.index, dtype=float)
    for week in sorted(set(iso_week)):
        values = series[iso_week == week]
        mean = values.expanding(min_periods=min_years).mean().shift(1)
        std = values.expanding(min_periods=min_years).std(ddof=1).shift(1)
        result.loc[values.index] = (values - mean) / std.replace(0, np.nan)
    return result.clip(-4, 4)


def build_weather(config: dict, daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    regions = config["regions"]
    features: dict[tuple[str, str], pd.Series] = {}
    signals: dict[str, pd.Series] = {}
    for commodity, meta in config["commodities"].items():
        weights = {r: float(v.get(commodity, 0)) for r, v in regions.items()}
        weights = {r: w for r, w in weights.items() if w > 0}
        total = sum(weights.values())
        weights = {r: w / total for r, w in weights.items()}
        sub = daily[daily["region"].isin(weights)].copy()
        sub["weight"] = sub["region"].map(weights)
        weighted = sub.groupby(sub.index).apply(
            lambda x: pd.Series({
                "precip": np.average(x["PRECTOTCORR"], weights=x["weight"]),
                "tmax": np.average(x["T2M_MAX"], weights=x["weight"]),
                "tmin": np.average(x["T2M_MIN"], weights=x["weight"]),
            }), include_groups=False)
        weekly = pd.DataFrame({
            "precip": weighted["precip"].resample("W-FRI").sum(min_count=4),
            "tmax": weighted["tmax"].resample("W-FRI").mean(),
            "tmin": weighted["tmin"].resample("W-FRI").min(),
        })
        weekly["precip_z"] = expanding_seasonal_z(weekly["precip"])
        weekly["tmax_z"] = expanding_seasonal_z(weekly["tmax"])
        weekly["tmin_z"] = expanding_seasonal_z(weekly["tmin"])
        stress_weights = meta.get("stress_weights", {"dry": 0.6, "heat": 0.4})
        # Positive means supply stress. Wet is one-sided because excess rain can
        # impede harvest/disease-prone crops; cold captures coffee frost risk.
        weekly["stress"] = (
            -float(stress_weights.get("dry", 0)) * weekly["precip_z"]
            + float(stress_weights.get("heat", 0)) * weekly["tmax_z"]
            - float(stress_weights.get("cold", 0)) * weekly["tmin_z"]
            + float(stress_weights.get("wet", 0)) * weekly["precip_z"].clip(lower=0)
        ).clip(-3, 3)
        start_week, end_week = meta["season_weeks"]
        active = weekly.index.isocalendar().week.astype(int).between(start_week, end_week)
        weekly["stress"] = weekly["stress"].where(active, 0.0)
        for col in weekly:
            features[(commodity, col)] = weekly[col]
        signals[commodity] = weekly["stress"]
    feature_frame = pd.concat(features, axis=1).sort_index()
    signal_frame = pd.DataFrame(signals).sort_index()
    signal_frame = signal_frame.div(signal_frame.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    return feature_frame, signal_frame


def rv_signal(weekly_prices: pd.DataFrame, lookback: int, minimum: int) -> pd.DataFrame:
    logp = np.log(weekly_prices)
    own_z = (logp - logp.rolling(lookback, min_periods=minimum).mean()) / \
        logp.rolling(lookback, min_periods=minimum).std()
    relative = own_z.sub(own_z.mean(axis=1), axis=0)
    signal = -relative.clip(-2.5, 2.5)
    signal = signal.sub(signal.mean(axis=1), axis=0)
    return signal.div(signal.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def normalize_gross(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.div(frame.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def strategy_returns(asset_returns: pd.DataFrame, positions: pd.DataFrame,
                     cost_bps: float) -> tuple[pd.Series, pd.Series]:
    positions = positions.reindex(asset_returns.index).fillna(0)
    gross = (positions * asset_returns).sum(axis=1)
    turnover = positions.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = positions.iloc[0].abs().sum()
    net = gross - turnover * cost_bps / 10_000.0
    return net, turnover


def metrics(returns: pd.Series, turnover: pd.Series) -> dict[str, float | int | str]:
    r = returns.dropna()
    if r.empty:
        return {}
    equity = (1 + r).cumprod()
    vol = r.std(ddof=1) * math.sqrt(ANNUAL_WEEKS)
    sharpe = r.mean() * ANNUAL_WEEKS / vol if vol else np.nan
    drawdown = equity / equity.cummax() - 1
    demeaned = r.values - r.mean()
    n = len(demeaned)
    long_run_var = float(np.dot(demeaned, demeaned) / n)
    for lag in range(1, min(4, n - 1) + 1):
        weight = 1.0 - lag / 5.0  # Bartlett kernel, four weekly lags
        autocov = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        long_run_var += 2.0 * weight * autocov
    mean_se = math.sqrt(max(long_run_var, 0.0) / n)
    hac_tstat = float(r.mean() / mean_se) if mean_se else np.nan
    return {
        "start": r.index.min().date().isoformat(),
        "end": r.index.max().date().isoformat(),
        "weeks": int(len(r)),
        "annual_return_pct": round(float(r.mean() * ANNUAL_WEEKS * 100), 2),
        "annual_vol_pct": round(float(vol * 100), 2),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown_pct": round(float(drawdown.min() * 100), 2),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 2),
        "avg_weekly_turnover": round(float(turnover.reindex(r.index).mean()), 3),
        "hac_mean_tstat": round(hac_tstat, 3),
        "final_growth_of_1": round(float(equity.iloc[-1]), 3),
    }


def render_report(output: Path, config: dict, all_metrics: dict, hashes: dict,
                  coverage: dict, split_metrics: dict) -> None:
    def table(rows: dict) -> str:
        keys = ["annual_return_pct", "annual_vol_pct", "sharpe", "max_drawdown_pct",
                "hit_rate_pct", "avg_weekly_turnover", "hac_mean_tstat", "final_growth_of_1"]
        header = "| Strategy | " + " | ".join(k.replace("_", " ") for k in keys) + " |\n"
        sep = "|---" * (len(keys) + 1) + "|\n"
        body = "".join("| " + name + " | " + " | ".join(str(vals.get(k, "")) for k in keys) + " |\n"
                       for name, vals in rows.items())
        return header + sep + body

    report = f"""# NASA crop-weather and grain relative-value backtest

Generated from 2001 onward; the first five years are effectively a seasonal-normalization warm-up.

## Result

{table(all_metrics)}

### 2015-present stability slice

{table(split_metrics)}

The combined strategy is the pre-specified 50/50 mix of the weather signal and relative-value
mean reversion, re-normalized to one unit of gross exposure. Results are net of
{config['transaction_cost_bps_per_turnover']:.1f} bps per unit of turnover.

## What was tested

- **Weather observable proxy:** production-region weighted NASA POWER precipitation and maximum
  temperature; coffee also includes minimum-temperature/frost stress. Expanding same-week
  climatology, long supply stress and short unusually favorable weather, with next-week execution.
  This tests signal existence, but not exact archive tradability.
- **Weather with NASA lag:** the identical signal delayed by {config['weather_release_lag_days']} days
  plus next-week execution, approximating MERRA-2 publication latency.
- **Relative value:** each of corn, soy and wheat's log price is standardized against its own trailing
  {config['rv_lookback_weeks']} weeks, demeaned across corn/soy/wheat, then positioned against the
  relative extreme. The signal uses only data through the signal week.
- **Combined:** fixed {config['combined_weather_weight']:.0%} all-crop weather observable proxy and
  {config['combined_rv_weight']:.0%} grain relative value. No weight search was performed.

## Coverage and provenance

```json
{json.dumps(coverage, indent=2, sort_keys=True)}
```

Input hashes are recorded in `manifest.json`. NASA POWER values are current reprocessed
MERRA-2/POWER history, not a reconstruction of every vintage originally released. Yahoo generic
front-month futures are convenient research proxies but are not an institutional continuous-futures
series: rolls, delivery transitions, and vendor corrections can affect both returns and the relative
value signal.

## Interpretation constraints

This is an indicative hypothesis screen. A deployable result requires original-vintage NRT weather,
proper back-adjusted or excess-return futures, historical crop-production weights and calendars,
contract roll/limit/liquidity rules, and a sealed holdout or prospective collection period. The
observable proxy must not be presented as a point-in-time NASA backtest.
"""
    (output / "report.md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/nasa_crop_weather.json"))
    parser.add_argument("--output", type=Path, default=Path("research/weather_commodities"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output
    raw = output / "raw"
    output.mkdir(parents=True, exist_ok=True)

    start = pd.Timestamp(config["start_date"])
    # MERRA-2 normally arrives weeks after observation; avoid requesting an incomplete tail.
    weather_end = pd.Timestamp(date.today() - timedelta(days=config["weather_release_lag_days"]))
    price_end = pd.Timestamp(date.today() + timedelta(days=1))
    daily_parts = []
    unique_points: dict[tuple[float, float], str] = {}
    for name, meta in config["regions"].items():
        point = (float(meta["lat"]), float(meta["lon"]))
        unique_points.setdefault(point, name)
        daily_parts.append(fetch_power_point(
            name, point[0], point[1], start.strftime("%Y%m%d"), weather_end.strftime("%Y%m%d"),
            raw, args.refresh))
    daily = pd.concat(daily_parts).sort_index()
    features, weather_signal = build_weather(config, daily)
    prices = fetch_prices(config, start.strftime("%Y-%m-%d"), price_end.strftime("%Y-%m-%d"),
                          raw, args.refresh)
    weekly_prices = prices.resample("W-FRI").last().ffill(limit=1)
    asset_returns = weekly_prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)

    observable_pos = weather_signal.shift(1).reindex(asset_returns.index).fillna(0)
    lag_weeks = 1 + math.ceil(config["weather_release_lag_days"] / 7)
    nasa_lag_pos = weather_signal.shift(lag_weeks).reindex(asset_returns.index).fillna(0)
    grains = ["corn", "soy", "wheat"]
    softs = ["coffee", "sugar", "cocoa", "cotton"]
    grain_weather_pos = normalize_gross(observable_pos[grains]).reindex(
        columns=asset_returns.columns, fill_value=0)
    soft_weather_pos = normalize_gross(observable_pos[softs]).reindex(
        columns=asset_returns.columns, fill_value=0)
    rv_pos = rv_signal(weekly_prices[grains], config["rv_lookback_weeks"],
                       config["rv_min_weeks"]).shift(1)
    rv_pos = rv_pos.reindex(columns=asset_returns.columns, fill_value=0).fillna(0)
    combined_pos = (config["combined_weather_weight"] * observable_pos +
                    config["combined_rv_weight"] * rv_pos)
    combined_pos = normalize_gross(combined_pos)
    grain_combined_pos = normalize_gross(
        config["combined_weather_weight"] * grain_weather_pos
        + config["combined_rv_weight"] * rv_pos)

    positions = {
        "grain_weather_observable_proxy": grain_weather_pos,
        "soft_weather_observable_proxy": soft_weather_pos,
        "all_crop_weather_observable_proxy": observable_pos,
        "all_crop_weather_nasa_28d_lag": nasa_lag_pos,
        "coffee_weather": normalize_gross(observable_pos[["coffee"]]).reindex(columns=asset_returns.columns, fill_value=0),
        "sugar_weather": normalize_gross(observable_pos[["sugar"]]).reindex(columns=asset_returns.columns, fill_value=0),
        "cocoa_weather": normalize_gross(observable_pos[["cocoa"]]).reindex(columns=asset_returns.columns, fill_value=0),
        "cotton_weather": normalize_gross(observable_pos[["cotton"]]).reindex(columns=asset_returns.columns, fill_value=0),
        "grain_relative_value_mean_reversion": rv_pos,
        "grain_weather_plus_relative_value": grain_combined_pos,
        "all_weather_plus_grain_relative_value": combined_pos,
    }
    returns, turnovers = {}, {}
    all_metrics, split_metrics = {}, {}
    for name, pos in positions.items():
        returns[name], turnovers[name] = strategy_returns(
            asset_returns, pos, config["transaction_cost_bps_per_turnover"])
        valid_start = max(start, pd.Timestamp("2006-01-01"))
        sample = returns[name].loc[valid_start:]
        turn = turnovers[name].loc[valid_start:]
        all_metrics[name] = metrics(sample, turn)
        split_metrics[name] = metrics(sample.loc["2015-01-01":], turn.loc["2015-01-01":])

    features.to_parquet(output / "weekly_weather_features.parquet")
    weekly_prices.to_parquet(output / "weekly_prices.parquet")
    pd.concat(returns, axis=1).to_parquet(output / "strategy_returns.parquet")
    pd.concat({k: v for k, v in positions.items()}, axis=1).to_parquet(output / "positions.parquet")
    metrics_obj = {"full": all_metrics, "from_2015": split_metrics}
    atomic_json(output / "metrics.json", metrics_obj)

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, series in returns.items():
        growth = (1 + series.loc["2006-01-01":]).cumprod()
        ax.plot(growth.index, growth, label=name.replace("_", " "))
    ax.set_yscale("log")
    ax.set_title("NASA crop-weather and grain relative-value strategies (net)")
    ax.set_ylabel("Growth of $1 (log scale)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "equity_curves.png", dpi=160)
    plt.close(fig)

    hashes = {str(p.relative_to(output)): sha256(p) for p in sorted(raw.glob("*")) if p.is_file()}
    coverage = {
        "weather_start": daily.index.min().date().isoformat(),
        "weather_end": daily.index.max().date().isoformat(),
        "price_start": prices.index.min().date().isoformat(),
        "price_end": prices.index.max().date().isoformat(),
        "n_weather_regions": len(config["regions"]),
        "n_unique_weather_points": len(unique_points),
        "n_daily_weather_rows": len(daily),
        "price_source": "Yahoo Finance generic front-month futures",
        "weather_source": "NASA POWER daily API; MERRA-2/POWER",
    }
    manifest = {
        "config": config,
        "coverage": coverage,
        "raw_sha256": hashes,
        "known_point_in_time_limit": "Current reprocessed history is not original-vintage NRT data.",
    }
    atomic_json(output / "manifest.json", manifest)
    render_report(output, config, all_metrics, hashes, coverage, split_metrics)
    print(json.dumps(metrics_obj, indent=2))


if __name__ == "__main__":
    main()
