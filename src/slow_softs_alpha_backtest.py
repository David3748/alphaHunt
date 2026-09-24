#!/usr/bin/env python3
"""Slow cross-soft research model using World Bank prices, NASA weather and CFTC COT.

The World Bank series are monthly physical/benchmark prices, not investable ICE
futures excess-return indices. Results are therefore an economic signal screen.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    from .softs_weather_overlay import monthly_stage_weather_signal
except ImportError:
    from softs_weather_overlay import monthly_stage_weather_signal


SOFTS = ["coffee", "sugar", "cocoa", "cotton"]
MONTHS_PER_YEAR = 12.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, refresh: bool = False) -> None:
    if target.exists() and not refresh:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(response.content)
    temporary.replace(target)


def read_world_bank_prices(path: Path, config: dict) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Monthly Prices", header=4)
    date_column = raw.columns[0]
    wanted = {meta["world_bank_column"]: soft for soft, meta in config["commodities"].items()}
    missing = set(wanted) - set(raw.columns)
    if missing:
        raise ValueError(f"World Bank columns not found: {sorted(missing)}")
    date_text = raw[date_column].astype(str).str.strip()
    valid = date_text.str.fullmatch(r"\d{4}M\d{2}")
    raw = raw.loc[valid].copy()
    dates = pd.PeriodIndex(
        raw[date_column].astype(str).str.replace("M", "-", regex=False), freq="M"
    ).to_timestamp("M")
    prices = raw[list(wanted)].rename(columns=wanted).apply(pd.to_numeric, errors="coerce")
    prices.index = dates
    return prices[SOFTS].sort_index()


def read_cftc_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".txt")]
        if len(names) != 1:
            raise ValueError(f"Expected one CFTC text file in {path}, found {names}")
        with archive.open(names[0]) as handle:
            return pd.read_csv(io.BytesIO(handle.read()), low_memory=False)


def build_cot_panel(rows: pd.DataFrame, config: dict) -> pd.DataFrame:
    rows = rows.copy()
    rows.columns = [str(column).strip() for column in rows.columns]
    rows["date"] = pd.to_datetime(rows["As of Date in Form YYYY-MM-DD"], errors="coerce")
    rows["market"] = rows["Market and Exchange Names"].astype(str).str.strip().str.upper()
    rows["open_interest"] = pd.to_numeric(rows["Open Interest (All)"], errors="coerce")
    rows["commercial_long"] = pd.to_numeric(rows["Commercial Positions-Long (All)"], errors="coerce")
    rows["commercial_short"] = pd.to_numeric(rows["Commercial Positions-Short (All)"], errors="coerce")
    frames = []
    for soft, meta in config["commodities"].items():
        prefix = meta["cftc_name"].upper() + " -"
        selected = rows[rows["market"].str.startswith(prefix)].copy()
        if selected.empty:
            continue
        selected["soft"] = soft
        selected["commercial_net_share"] = (
            selected["commercial_long"] - selected["commercial_short"]
        ) / selected["open_interest"].replace(0, np.nan)
        frames.append(selected[["date", "soft", "commercial_net_share"]])
    if not frames:
        raise ValueError("No configured soft contracts found in CFTC rows")
    long = pd.concat(frames).drop_duplicates(["date", "soft"], keep="last")
    return long.pivot(index="date", columns="soft", values="commercial_net_share").sort_index().reindex(columns=SOFTS)


def normalize_gross(frame: pd.DataFrame) -> pd.DataFrame:
    neutral = frame.sub(frame.mean(axis=1), axis=0)
    return neutral.div(neutral.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def relative_value_signal(prices: pd.DataFrame, config: dict) -> pd.DataFrame:
    log_prices = np.log(prices)
    lookback, minimum = int(config["rv_lookback_months"]), int(config["rv_min_months"])
    mean = log_prices.rolling(lookback, min_periods=minimum).mean()
    std = log_prices.rolling(lookback, min_periods=minimum).std()
    signal = -(log_prices - mean) / std.replace(0, np.nan)
    signal = signal.rolling(int(config["rv_smoothing_months"]), min_periods=1).mean().clip(-3, 3)
    return normalize_gross(signal)


def weather_signal(features: pd.DataFrame, monthly_index: pd.DatetimeIndex, config: dict) -> pd.DataFrame:
    stress = pd.DataFrame({soft: features[(soft, "stress")] for soft in SOFTS})
    level_window = int(config["weather_level_weeks"])
    trajectory_window = int(config["weather_trajectory_weeks"])
    level = stress.rolling(level_window, min_periods=max(4, level_window // 2)).mean()
    recent = stress.rolling(trajectory_window, min_periods=trajectory_window).mean()
    outlook = (0.75 * level + 0.25 * (recent - recent.shift(trajectory_window))).shift(
        int(config["weather_information_lag_weeks"])
    )
    monthly = outlook.resample("ME").last().reindex(monthly_index).ffill()
    return normalize_gross(monthly.clip(-3, 3))


def positioning_signal(cot: pd.DataFrame, monthly_index: pd.DatetimeIndex, config: dict) -> pd.DataFrame:
    # CFTC positions are measured Tuesday and normally released Friday. Apply the
    # configured publication lag before deciding which observation was knowable.
    released = cot.copy()
    released.index = released.index + pd.Timedelta(days=int(config["cot_publication_lag_days"]))
    lookback, minimum = int(config["cot_lookback_weeks"]), int(config["cot_min_weeks"])
    mean = released.rolling(lookback, min_periods=minimum).mean()
    std = released.rolling(lookback, min_periods=minimum).std()
    # Hedging-pressure sign: producers being unusually net short implies that
    # longs must absorb more hedging demand and should earn the risk premium.
    hedging_pressure = -((released - mean) / std.replace(0, np.nan)).clip(-3, 3)
    smoothed = hedging_pressure.rolling(int(config["cot_smoothing_weeks"]), min_periods=4).mean()
    monthly = smoothed.resample("ME").last().reindex(monthly_index).ffill()
    return normalize_gross(monthly)


def blend(*weighted: tuple[float, pd.DataFrame]) -> pd.DataFrame:
    # Fixed sleeve weights are intentionally not re-levered after disagreement.
    combined = sum(weight * signal for weight, signal in weighted)
    return combined.sub(combined.mean(axis=1), axis=0).fillna(0.0)


def strategy_returns(asset_returns: pd.DataFrame, targets: pd.DataFrame, cost_bps: float) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    positions = targets.shift(1).reindex(asset_returns.index).fillna(0.0)
    gross = (positions * asset_returns).sum(axis=1)
    turnover = positions.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = positions.iloc[0].abs().sum()
    return gross - turnover * cost_bps / 10_000.0, turnover, positions


def metrics(returns: pd.Series, turnover: pd.Series, gross: pd.Series) -> dict:
    valid = returns.dropna()
    if valid.empty:
        return {}
    wealth = (1 + valid).cumprod()
    annual_vol = valid.std(ddof=1) * math.sqrt(MONTHS_PER_YEAR)
    drawdown = wealth / wealth.cummax() - 1
    demeaned = valid.to_numpy() - valid.mean()
    n = len(demeaned)
    long_run_variance = float(np.dot(demeaned, demeaned) / n)
    max_lag = min(3, n - 1)
    for lag in range(1, max_lag + 1):
        weight = 1 - lag / (max_lag + 1)
        long_run_variance += 2 * weight * float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
    standard_error = math.sqrt(max(long_run_variance, 0.0) / n)
    return {
        "start": valid.index.min().date().isoformat(), "end": valid.index.max().date().isoformat(),
        "months": int(len(valid)), "annual_return_pct": round(float(valid.mean() * 12 * 100), 2),
        "annual_vol_pct": round(float(annual_vol * 100), 2),
        "sharpe": round(float(valid.mean() * 12 / annual_vol), 3) if annual_vol else None,
        "max_drawdown_pct": round(float(drawdown.min() * 100), 2),
        "hit_rate_pct": round(float((valid > 0).mean() * 100), 2),
        "avg_monthly_turnover": round(float(turnover.reindex(valid.index).mean()), 3),
        "avg_gross_exposure": round(float(gross.reindex(valid.index).mean()), 3),
        "hac_mean_tstat": round(float(valid.mean() / standard_error), 3) if standard_error else None,
        "final_growth_of_1": round(float(wealth.iloc[-1]), 3),
    }


def run(config: dict, prices: pd.DataFrame, features: pd.DataFrame, cot: pd.DataFrame,
        stage_weather: pd.DataFrame | None = None):
    prices = prices.loc[pd.Timestamp(config["start_date"]):, SOFTS].dropna(how="all")
    asset_returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    rv = relative_value_signal(prices, config)
    weather = weather_signal(features, prices.index, config)
    if stage_weather is None:
        stage_weather = weather
    else:
        stage_weather = stage_weather.reindex(prices.index).fillna(0.0)
    positioning = positioning_signal(cot, prices.index, config)
    # Slow implementation cannot win the immediate repricing race. The same
    # stage-aware damage estimate is therefore traded as a one-month-later
    # overreaction/reversion signal, while the directional version remains as
    # an explicit ablation.
    weather_reversion = -stage_weather
    weights = config["weights"]
    targets = {
        "slow_relative_value": rv,
        "slow_weather": weather,
        "stage_weather": stage_weather,
        "stage_weather_reversion": weather_reversion,
        "slow_positioning": positioning,
        "rv_plus_weather": blend((0.75, rv), (0.25, weather)),
        "rv_plus_positioning": blend((0.75, rv), (0.25, positioning)),
        "positioning_plus_stage_weather": blend((0.80, positioning), (0.20, stage_weather)),
        "positioning_plus_weather_reversion": blend((0.75, positioning),
                                                      (0.25, weather_reversion)),
        "all_three": blend((float(weights["relative_value"]), rv),
                           (float(weights["weather"]), weather),
                           (float(weights["positioning"]), positioning)),
        "rv_positioning_stage_weather": blend((0.30, rv), (0.50, positioning),
                                                (0.20, stage_weather)),
        "slow_softs_core": blend((0.25, rv), (0.50, positioning),
                                  (0.25, weather_reversion)),
    }
    returns, positions, stats = {}, {}, {}
    samples = {
        "full": (pd.Timestamp(config["evaluation_start"]), None),
        "common_cot_window": (pd.Timestamp("2004-01-31"), None),
        "development_2004_2015": (pd.Timestamp("2004-01-31"), pd.Timestamp("2015-12-31")),
        "validation_2016_2020": (pd.Timestamp("2016-01-31"), pd.Timestamp("2020-12-31")),
        "holdout_2021": (pd.Timestamp(config["holdout_start"]), None),
    }
    for name, target in targets.items():
        ret, turnover, held = strategy_returns(asset_returns, target, float(config["transaction_cost_bps_per_turnover"]))
        returns[name], positions[name] = ret, held
        stats[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], held.abs().sum(axis=1).loc[start:end])
            for label, (start, end) in samples.items()
        }
    diagnostics = pd.concat({"rv": rv, "weather": weather, "stage_weather": stage_weather,
                             "positioning": positioning}, axis=1)
    return stats, pd.DataFrame(returns), pd.concat(positions, axis=1), diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/slow_softs_alpha.json"))
    parser.add_argument("--weather-dir", type=Path, default=Path("research/weather_commodities"))
    parser.add_argument("--nasa-config", type=Path, default=Path("config/nasa_crop_weather.json"))
    parser.add_argument("--weather-overlay-config", type=Path, default=Path("config/softs_weather_overlay.json"))
    parser.add_argument("--output", type=Path, default=Path("research/slow_softs_alpha"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    raw_dir = args.output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    world_bank_path = raw_dir / "world-bank-pink-sheet-monthly.xlsx"
    download(config["world_bank_url"], world_bank_path, args.refresh)
    end_year = pd.Timestamp.today().year
    cot_frames, cot_hashes = [], {}
    for year in range(2001, end_year + 1):
        target = raw_dir / f"cftc-legacy-{year}.zip"
        download(config["cftc_url_template"].format(year=year), target, args.refresh)
        cot_frames.append(read_cftc_archive(target))
        cot_hashes[str(year)] = sha256(target)
    prices = read_world_bank_prices(world_bank_path, config)
    cot = build_cot_panel(pd.concat(cot_frames, ignore_index=True), config)
    features = pd.read_parquet(args.weather_dir / "weekly_weather_features.parquet")
    nasa_config = json.loads(args.nasa_config.read_text())
    overlay_config = json.loads(args.weather_overlay_config.read_text())
    stage_weather, regional_weather = monthly_stage_weather_signal(
        args.weather_dir / "raw", nasa_config, overlay_config, prices.index)
    stats, returns, positions, diagnostics = run(config, prices, features, cot, stage_weather)
    sensitivity_specs = {
        "base": {},
        "weather: 2w lag / 8w level": {"weather_information_lag_weeks": 2, "weather_level_weeks": 8},
        "weather: 8w lag / 12w level": {"weather_information_lag_weeks": 8, "weather_level_weeks": 12},
        "rv: 24m lookback": {"rv_lookback_months": 24, "rv_min_months": 18},
        "rv: 60m lookback": {"rv_lookback_months": 60, "rv_min_months": 36},
        "positioning: 26w smooth": {"cot_smoothing_weeks": 26},
    }
    sensitivity = {}
    for label, overrides in sensitivity_specs.items():
        variant_stats = run({**config, **overrides}, prices, features, cot, stage_weather)[0]
        sensitivity[label] = {
            name: slices["common_cot_window"] for name, slices in variant_stats.items()
        }
    args.output.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(raw_dir / "world_bank_softs_prices.parquet")
    cot.to_parquet(raw_dir / "cftc_commercial_net_share.parquet")
    returns.to_parquet(args.output / "strategy_returns.parquet")
    positions.to_parquet(args.output / "positions.parquet")
    diagnostics.to_parquet(args.output / "signal_diagnostics.parquet")
    regional_weather.to_parquet(args.output / "regional_stage_weather.parquet")
    (args.output / "metrics.json").write_text(json.dumps(stats, indent=2))
    (args.output / "sensitivity.json").write_text(json.dumps(sensitivity, indent=2))
    overlay_sensitivity = {}
    for label, overrides in {
        "2w lag / 4w half-life": {"persistence_halflife_weeks": 4},
        "base: 2w lag / 8w half-life": {},
        "4w lag / 8w half-life": {"information_lag_weeks": 4},
        "2w lag / 13w half-life": {"persistence_halflife_weeks": 13},
    }.items():
        variant_weather, _ = monthly_stage_weather_signal(
            args.weather_dir / "raw", nasa_config, {**overlay_config, **overrides}, prices.index)
        variant_stats = run(config, prices, features, cot, variant_weather)[0]
        overlay_sensitivity[label] = {
            name: slices for name, slices in variant_stats.items()
            if name in {"stage_weather", "stage_weather_reversion",
                        "positioning_plus_weather_reversion", "slow_softs_core"}
        }
    (args.output / "weather_overlay_sensitivity.json").write_text(json.dumps(overlay_sensitivity, indent=2))
    manifest = {
        "price_source": "World Bank Pink Sheet monthly benchmark prices",
        "return_status": "economic proxy; not ICE futures excess returns",
        "price_sha256": sha256(world_bank_path), "cftc_sha256": cot_hashes,
        "price_coverage": [prices.dropna(how="all").index.min().date().isoformat(), prices.dropna(how="all").index.max().date().isoformat()],
        "cot_coverage": [cot.index.min().date().isoformat(), cot.index.max().date().isoformat()],
        "ice_carry_status": "not included: auditable historical ICE contract curves require licensed data",
        "weather_overlay": "region/stage-specific nonlinear stress, traded as delayed reversal",
        "weather_overlay_config_sha256": sha256(args.weather_overlay_config),
        "validation_status": "research-contaminated; 2021+ is diagnostic, not sealed"
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(stats["slow_softs_core"], indent=2))


if __name__ == "__main__":
    main()
