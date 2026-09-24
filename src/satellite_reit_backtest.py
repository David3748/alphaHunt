#!/usr/bin/env python3
"""Slow cross-country REIT supply-cycle backtest using GHSL built-up area.

This is a retrospective research prototype.  GHSL R2025A is a consistently
reprocessed historical reconstruction, not a point-in-time archive.  The code
therefore keeps a conservative operational lag but does not claim vintage-safe
satellite availability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf


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
    response = requests.get(url, timeout=240)
    response.raise_for_status()
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(response.content)
    temporary.replace(target)


def extract_statistics(archive_path: Path, raw_dir: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]
        if len(members) != 1:
            raise ValueError(f"Expected one XLSX statistics file, found {members}")
        target = raw_dir / Path(members[0]).name
        if not target.exists():
            archive.extract(members[0], raw_dir)
        return target


def _rank_growth_signal(epoch: pd.DataFrame, config: dict) -> pd.DataFrame:
    epoch = epoch.copy()
    epoch.index = pd.to_datetime(epoch.index.astype(str) + "-12-31") + pd.DateOffset(
        months=int(config["satellite_operational_lag_months"])
    )
    epoch.index.name = "available_date"
    percentile = epoch.rank(axis=1, pct=True, method="average")
    return normalize_gross(-(percentile.sub(percentile.mean(axis=1), axis=0)))


def _rank_high_signal(epoch: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Long high observations, with the same conservative epoch availability lag."""
    epoch = epoch.copy()
    epoch.index = pd.to_datetime(epoch.index.astype(str) + "-12-31") + pd.DateOffset(
        months=int(config["satellite_operational_lag_months"])
    )
    epoch.index.name = "available_date"
    percentile = epoch.rank(axis=1, pct=True, method="average")
    return normalize_gross(percentile.sub(percentile.mean(axis=1), axis=0))


def load_country_supply(workbook: Path, config: dict):
    columns = ["ID_MTUC", "ID_UC_G0", "UNLocName", "Year", "BU_km2", "POP"]
    raw = pd.read_excel(workbook, sheet_name="UC_STATS", usecols=columns)
    wanted = {meta["country"]: ticker for ticker, meta in config["instruments"].items()}
    raw = raw[raw["UNLocName"].isin(wanted)].copy()
    raw["BU_km2"] = pd.to_numeric(raw["BU_km2"], errors="coerce")
    raw["POP"] = pd.to_numeric(raw["POP"], errors="coerce")
    raw = raw[raw["Year"].between(1975, int(config["satellite_last_observed_epoch"]))]
    raw["ticker"] = raw["UNLocName"].map(wanted)
    country = raw.groupby(["Year", "UNLocName"], observed=True).agg(
        built_up_km2=("BU_km2", "sum"), population=("POP", "sum"), urban_centres=("ID_MTUC", "nunique")
    ).reset_index()
    country["ticker"] = country["UNLocName"].map(wanted)
    country = country.sort_values(["ticker", "Year"])
    country["built_up_5y_cagr"] = country.groupby("ticker")["built_up_km2"].pct_change(fill_method=None)
    country["built_up_5y_cagr"] = (1 + country["built_up_5y_cagr"]).pow(1 / 5) - 1
    country["built_up_per_capita_m2"] = country["built_up_km2"] * 1_000_000 / country["population"]

    epoch = country.pivot(index="Year", columns="ticker", values="built_up_5y_cagr")
    # The returned primitive is the constrained-supply hypothesis: lower
    # construction growth is long.  The backtest also evaluates the opposite
    # urban-growth/demand interpretation explicitly rather than hiding sign risk.
    signal = _rank_growth_signal(epoch, config)

    # Institutional real estate is concentrated in major metros. Select each
    # interval's city cohort using population at the start of the interval, so
    # endpoint growth cannot determine which cities enter the measurement.
    major_rows = []
    top_n = int(config["major_city_count"])
    for ticker, rows in raw.groupby("ticker", observed=True):
        years = sorted(rows["Year"].dropna().astype(int).unique())
        for year in years[1:]:
            prior_year = year - 5
            prior = rows[rows["Year"].eq(prior_year)].dropna(subset=["BU_km2", "POP"])
            cohort = prior.nlargest(top_n, "POP")
            ids = set(cohort["ID_UC_G0"])
            prior_built = float(cohort["BU_km2"].sum())
            current = rows[rows["Year"].eq(year) & rows["ID_UC_G0"].isin(ids)]
            current_built = float(current["BU_km2"].sum())
            prior_population = float(cohort["POP"].sum())
            current_population = float(current["POP"].sum())
            if min(prior_built, current_built, prior_population, current_population) <= 0:
                continue
            built_up_growth = (current_built / prior_built) ** (1 / 5) - 1
            population_growth = (current_population / prior_population) ** (1 / 5) - 1
            major_rows.append({
                "ticker": ticker, "Year": year, "prior_year": prior_year,
                "city_count": len(ids), "prior_built_up_km2": prior_built,
                "built_up_km2": current_built,
                "prior_population": prior_population, "population": current_population,
                "built_up_5y_cagr": built_up_growth,
                "population_5y_cagr": population_growth,
                "demand_minus_supply": population_growth - built_up_growth,
            })
    major_city = pd.DataFrame(major_rows)
    major_epoch = major_city.pivot(index="Year", columns="ticker", values="built_up_5y_cagr")
    major_signal = _rank_growth_signal(major_epoch[list(config["core_universe"])], config)
    demand_supply_epoch = major_city.pivot(index="Year", columns="ticker", values="demand_minus_supply")
    demand_supply_signal = _rank_high_signal(
        demand_supply_epoch[list(config["core_universe"])], config
    )
    return country, signal, major_city, major_signal, demand_supply_signal


def normalize_gross(frame: pd.DataFrame) -> pd.DataFrame:
    neutral = frame.sub(frame.mean(axis=1), axis=0)
    return neutral.div(neutral.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def download_market_data(config: dict, raw_dir: Path, refresh: bool = False) -> pd.DataFrame:
    cache = raw_dir / "reit_total_return_prices_usd.parquet"
    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        last_complete = (pd.Timestamp.today().to_period("M") - 1).to_timestamp("M")
        return cached.loc[:last_complete]
    instruments = config["instruments"]
    tickers = list(instruments)
    fx_tickers = sorted({meta["fx"] for meta in instruments.values() if meta["fx"]})
    symbols = tickers + fx_tickers
    downloaded = yf.download(
        symbols, start="2001-01-01", auto_adjust=False, actions=False,
        progress=False, threads=True, group_by="column"
    )
    adjusted = downloaded["Adj Close"] if isinstance(downloaded.columns, pd.MultiIndex) else downloaded
    local = adjusted.reindex(columns=tickers).copy()
    usd = pd.DataFrame(index=local.index)
    for ticker, meta in instruments.items():
        series = local[ticker]
        fx = meta["fx"]
        if meta["fx_mode"] == "multiply":
            series = series * adjusted[fx].reindex(series.index).ffill()
        elif meta["fx_mode"] == "divide":
            series = series / adjusted[fx].reindex(series.index).ffill()
        usd[ticker] = series
    last_complete = (pd.Timestamp.today().to_period("M") - 1).to_timestamp("M")
    monthly = usd.resample("ME").last().loc[pd.Timestamp(config["start_date"]):last_complete]
    monthly.to_parquet(cache)
    local.resample("ME").last().to_parquet(raw_dir / "reit_total_return_prices_local.parquet")
    return monthly


def download_country_equity_data(config: dict, raw_dir: Path, refresh: bool = False):
    cache = raw_dir / "country_equity_total_return_prices_usd.parquet"
    local_cache = raw_dir / "country_equity_total_return_prices_local.parquet"
    last_complete = (pd.Timestamp.today().to_period("M") - 1).to_timestamp("M")
    if cache.exists() and local_cache.exists() and not refresh:
        return pd.read_parquet(cache).loc[:last_complete], pd.read_parquet(local_cache).loc[:last_complete]
    mapping = config["country_equity_benchmarks"]
    fx_tickers = sorted({meta["fx"] for meta in config["instruments"].values() if meta["fx"]})
    downloaded = yf.download(
        sorted(set(mapping.values())) + fx_tickers, start="2001-01-01", auto_adjust=False,
        actions=False, progress=False, threads=True, group_by="column"
    )
    adjusted = downloaded["Adj Close"] if isinstance(downloaded.columns, pd.MultiIndex) else downloaded
    monthly = adjusted.resample("ME").last().loc[:last_complete]
    usd = pd.DataFrame({ticker: monthly[benchmark] for ticker, benchmark in mapping.items()})
    local = pd.DataFrame(index=monthly.index)
    for ticker in mapping:
        meta = config["instruments"][ticker]
        if meta["fx_mode"] == "multiply":
            local[ticker] = usd[ticker] / monthly[meta["fx"]]
        elif meta["fx_mode"] == "divide":
            local[ticker] = usd[ticker] * monthly[meta["fx"]]
        else:
            local[ticker] = usd[ticker]
    usd.to_parquet(cache)
    local.to_parquet(local_cache)
    return usd, local


def relative_value_signal(prices: pd.DataFrame, config: dict) -> pd.DataFrame:
    log_prices = np.log(prices)
    lookback = int(config["relative_value_lookback_months"])
    minimum = int(config["relative_value_min_months"])
    mean = log_prices.rolling(lookback, min_periods=minimum).mean()
    std = log_prices.rolling(lookback, min_periods=minimum).std()
    own_z = (log_prices - mean) / std.replace(0, np.nan)
    smoothed = own_z.rolling(int(config["relative_value_smoothing_months"]), min_periods=1).mean()
    return normalize_gross(-smoothed.clip(-3, 3))


def momentum_signal(prices: pd.DataFrame, config: dict) -> pd.DataFrame:
    lookback = int(config["momentum_lookback_months"])
    skip = int(config["momentum_skip_months"])
    momentum = prices.shift(skip) / prices.shift(lookback + skip) - 1
    return normalize_gross(momentum)


def monthly_satellite_signal(epoch_signal: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    union = epoch_signal.index.union(index).sort_values()
    return epoch_signal.reindex(union).ffill().reindex(index).fillna(0.0)


def blend(*weighted: tuple[float, pd.DataFrame]) -> pd.DataFrame:
    # Do not re-lever disagreement away; cancellation naturally moves to cash.
    return sum(weight * signal for weight, signal in weighted).fillna(0.0)


def strategy_returns(asset_returns: pd.DataFrame, targets: pd.DataFrame, cost_bps: float):
    positions = targets.shift(1).reindex(asset_returns.index).fillna(0.0)
    gross_return = (positions * asset_returns).sum(axis=1)
    turnover = positions.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = positions.iloc[0].abs().sum()
    net = gross_return - turnover * float(cost_bps) / 10_000.0
    return net, turnover, positions


def hac_tstat(values: pd.Series, max_lag: int = 3) -> float | None:
    x = values.dropna().to_numpy()
    if len(x) < 3:
        return None
    demeaned = x - x.mean()
    n = len(x)
    long_run_variance = float(np.dot(demeaned, demeaned) / n)
    for lag in range(1, min(max_lag, n - 1) + 1):
        weight = 1 - lag / (max_lag + 1)
        long_run_variance += 2 * weight * float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
    standard_error = math.sqrt(max(long_run_variance, 0.0) / n)
    return float(x.mean() / standard_error) if standard_error else None


def metrics(returns: pd.Series, turnover: pd.Series, gross: pd.Series) -> dict:
    valid = returns.dropna()
    if valid.empty:
        return {}
    wealth = (1 + valid).cumprod()
    annual_return = valid.mean() * 12
    annual_vol = valid.std(ddof=1) * math.sqrt(12)
    drawdown = wealth / wealth.cummax() - 1
    return {
        "start": valid.index.min().date().isoformat(), "end": valid.index.max().date().isoformat(),
        "months": int(len(valid)), "annual_return_pct": round(float(annual_return * 100), 2),
        "annual_vol_pct": round(float(annual_vol * 100), 2),
        "sharpe": round(float(annual_return / annual_vol), 3) if annual_vol else None,
        "max_drawdown_pct": round(float(drawdown.min() * 100), 2),
        "hit_rate_pct": round(float((valid > 0).mean() * 100), 2),
        "avg_monthly_turnover": round(float(turnover.reindex(valid.index).mean()), 3),
        "avg_gross_exposure": round(float(gross.reindex(valid.index).mean()), 3),
        "hac_mean_tstat": round(hac_tstat(valid), 3) if hac_tstat(valid) is not None else None,
        "final_growth_of_1": round(float(wealth.iloc[-1]), 3),
    }


def run(config: dict, prices: pd.DataFrame, local_prices: pd.DataFrame,
        equity_prices: pd.DataFrame, local_equity_prices: pd.DataFrame,
        epoch_signal: pd.DataFrame, major_epoch_signal: pd.DataFrame,
        major_demand_supply_epoch_signal: pd.DataFrame):
    tickers = list(config["instruments"])
    prices = prices.reindex(columns=tickers).ffill(limit=1)
    returns = prices.pct_change(fill_method=None)
    # Common complete-history window avoids an expanding-universe performance bias.
    common = prices.dropna().index
    prices, returns = prices.loc[common], returns.loc[common].fillna(0.0)
    supply_constraint = monthly_satellite_signal(epoch_signal.reindex(columns=tickers), prices.index)
    urban_growth = -supply_constraint
    relative_value = relative_value_signal(prices, config)
    momentum = momentum_signal(prices, config)
    w = config["signal_weights"]
    targets = {
        "satellite_supply_constraint": supply_constraint,
        "satellite_urban_growth": urban_growth,
        "relative_value": relative_value,
        "momentum": momentum,
        "urban_growth_plus_value": blend((0.80, urban_growth), (0.20, relative_value)),
        "three_sleeve_experiment": blend(
            (float(w["satellite_supply"]), urban_growth),
            (float(w["relative_value"]), relative_value),
            (float(w["momentum"]), momentum),
        ),
    }
    strategy, held, statistics = {}, {}, {}
    evaluation_start = pd.Timestamp(config["evaluation_start"])
    validation_start = pd.Timestamp(config["validation_start"])
    samples = {
        "full": (evaluation_start, None),
        "development": (evaluation_start, validation_start - pd.offsets.MonthEnd(1)),
        "validation": (validation_start, None),
    }
    satellite_start = pd.Timestamp(config["satellite_evaluation_start"])
    satellite_samples = {
        "full": (satellite_start, None),
        "development": (satellite_start, validation_start - pd.offsets.MonthEnd(1)),
        "validation": (validation_start, None),
    }
    for name, target in targets.items():
        ret, turnover, positions = strategy_returns(
            returns, target, float(config["transaction_cost_bps_per_turnover"])
        )
        strategy[name] = ret
        held[name] = positions
        gross = positions.abs().sum(axis=1)
        statistics[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], gross.loc[start:end])
            for label, (start, end) in samples.items()
        }
    # Cleaner production candidate: use only diversified ETF/property-fund
    # legs.  The broad sample's Singapore and Hong Kong legs are individual
    # REITs and inject avoidable company-specific risk.
    core_columns = list(config["core_universe"])
    core_target = normalize_gross(urban_growth[core_columns])
    equity_returns = equity_prices.reindex(prices.index).pct_change(fill_method=None).fillna(0.0)
    extra = {
        "satellite_etf_core": (returns[core_columns], core_target),
        "satellite_country_equity": (equity_returns[core_columns], core_target),
        "satellite_reit_minus_equity": (returns[core_columns] - equity_returns[core_columns], core_target),
    }
    for name, (return_panel, target) in extra.items():
        ret, turnover, positions = strategy_returns(
            return_panel, target, float(config["transaction_cost_bps_per_turnover"])
        )
        strategy[name] = ret
        held[name] = positions
        gross = positions.abs().sum(axis=1)
        statistics[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], gross.loc[start:end])
            for label, (start, end) in satellite_samples.items()
        }
    major_growth = -monthly_satellite_signal(
        major_epoch_signal.reindex(columns=core_columns), prices.index
    )
    major_growth = normalize_gross(major_growth)
    major_demand_supply = monthly_satellite_signal(
        major_demand_supply_epoch_signal.reindex(columns=core_columns), prices.index
    )
    major_demand_supply = normalize_gross(major_demand_supply)
    local_reit_returns = local_prices.reindex(prices.index)[core_columns].pct_change(fill_method=None).fillna(0.0)
    local_equity_returns = local_equity_prices.reindex(prices.index)[core_columns].pct_change(fill_method=None).fillna(0.0)
    residual_panel = local_reit_returns - local_equity_returns
    major_panels = {
        "major_city_usd_reit": returns[core_columns],
        "major_city_local_reit": local_reit_returns,
        "major_city_local_country_equity": local_equity_returns,
        "major_city_reit_specific": residual_panel,
    }
    for name, return_panel in major_panels.items():
        # The residual implementation trades both the REIT and equity hedge.
        cost_multiplier = 2.0 if name == "major_city_reit_specific" else 1.0
        ret, turnover, positions = strategy_returns(
            return_panel, major_growth,
            float(config["transaction_cost_bps_per_turnover"]) * cost_multiplier
        )
        strategy[name] = ret
        held[name] = positions
        gross = positions.abs().sum(axis=1) * cost_multiplier
        statistics[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], gross.loc[start:end])
            for label, (start, end) in satellite_samples.items()
        }
    for name, target in {
        "major_city_demand_minus_supply_reit_specific": major_demand_supply,
        "major_city_growth_demand_blend_reit_specific": blend(
            (0.50, major_growth), (0.50, major_demand_supply)
        ),
    }.items():
        ret, turnover, positions = strategy_returns(
            residual_panel, target,
            float(config["transaction_cost_bps_per_turnover"]) * 2
        )
        strategy[name] = ret
        held[name] = positions
        gross = positions.abs().sum(axis=1) * 2
        statistics[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], gross.loc[start:end])
            for label, (start, end) in satellite_samples.items()
        }
    # Weighting robustness: the selected linear-rank portfolio is compared with
    # equal-weight top/bottom pairs and a single winner/loser pair. These are
    # diagnostics, not parameters selected to maximize the reported result.
    ranks = major_growth.rank(axis=1, method="first")
    equal_tail = pd.DataFrame(0.0, index=ranks.index, columns=ranks.columns)
    winner_loser = equal_tail.copy()
    for date, row in ranks.iterrows():
        equal_tail.loc[date, row.nlargest(2).index] = 0.25
        equal_tail.loc[date, row.nsmallest(2).index] = -0.25
        winner_loser.loc[date, row.idxmax()] = 0.5
        winner_loser.loc[date, row.idxmin()] = -0.5
    for name, target in {
        "major_city_equal_tail_reit_specific": equal_tail,
        "major_city_winner_loser_reit_specific": winner_loser,
    }.items():
        ret, turnover, positions = strategy_returns(
            residual_panel, target, float(config["transaction_cost_bps_per_turnover"]) * 2
        )
        strategy[name] = ret; held[name] = positions
        gross = positions.abs().sum(axis=1) * 2
        statistics[name] = {
            label: metrics(ret.loc[start:end], turnover.loc[start:end], gross.loc[start:end])
            for label, (start, end) in satellite_samples.items()
        }
    equal_weight = returns.mean(axis=1)
    strategy["equal_weight_reits"] = equal_weight
    zero = pd.Series(0.0, index=equal_weight.index)
    one = pd.Series(1.0, index=equal_weight.index)
    statistics["equal_weight_reits"] = {
        label: metrics(equal_weight.loc[start:end], zero.loc[start:end], one.loc[start:end])
        for label, (start, end) in samples.items()
    }
    diagnostics = pd.concat({"supply_constraint": supply_constraint, "urban_growth": urban_growth,
                             "major_city_growth": major_growth, "relative_value": relative_value,
                             "major_city_demand_minus_supply": major_demand_supply,
                             "momentum": momentum}, axis=1)
    robustness = {
        "leave_one_market_out": {}, "satellite_epoch_windows": {},
        "demand_blend_leave_one_market_out": {},
        "demand_blend_cost_stress": {},
        "signal_weighting": {
            name: statistics[name]["full"] for name in [
                "major_city_reit_specific", "major_city_equal_tail_reit_specific",
                "major_city_winner_loser_reit_specific"
            ]
        },
    }
    for omitted in core_columns:
        columns = [column for column in core_columns if column != omitted]
        target = normalize_gross(major_growth[columns])
        ret, turnover, positions = strategy_returns(
            local_reit_returns[columns] - local_equity_returns[columns], target,
            float(config["transaction_cost_bps_per_turnover"]) * 2
        )
        robustness["leave_one_market_out"][omitted] = metrics(
            ret.loc[satellite_start:], turnover.loc[satellite_start:],
            positions.abs().sum(axis=1).mul(2).loc[satellite_start:]
        )
        growth_subset = normalize_gross(major_growth[columns])
        demand_subset = normalize_gross(major_demand_supply[columns])
        blend_subset = blend((0.50, growth_subset), (0.50, demand_subset))
        blend_ret, blend_turnover, blend_positions = strategy_returns(
            residual_panel[columns], blend_subset,
            float(config["transaction_cost_bps_per_turnover"]) * 2
        )
        robustness["demand_blend_leave_one_market_out"][omitted] = metrics(
            blend_ret.loc[satellite_start:], blend_turnover.loc[satellite_start:],
            blend_positions.abs().sum(axis=1).mul(2).loc[satellite_start:]
        )
    blend_target = blend((0.50, major_growth), (0.50, major_demand_supply))
    for per_leg_cost_bps in (0, 20, 50):
        stress_ret, stress_turnover, stress_positions = strategy_returns(
            residual_panel, blend_target, per_leg_cost_bps * 2
        )
        robustness["demand_blend_cost_stress"][str(per_leg_cost_bps)] = metrics(
            stress_ret.loc[satellite_start:], stress_turnover.loc[satellite_start:],
            stress_positions.abs().sum(axis=1).mul(2).loc[satellite_start:]
        )
    epoch_windows = {
        "2005_signal": (pd.Timestamp("2011-01-31"), pd.Timestamp("2011-12-31")),
        "2010_signal": (pd.Timestamp("2012-01-31"), pd.Timestamp("2016-12-31")),
        "2015_signal": (pd.Timestamp("2017-01-31"), pd.Timestamp("2021-12-31")),
        "2020_signal": (pd.Timestamp("2022-01-31"), None),
    }
    core_ret = pd.Series(strategy["major_city_reit_specific"])
    core_positions = held["major_city_reit_specific"]
    for label, (start, end) in epoch_windows.items():
        core_turnover = core_positions.diff().abs().sum(axis=1)
        robustness["satellite_epoch_windows"][label] = metrics(
            core_ret.loc[start:end], core_turnover.loc[start:end],
            core_positions.abs().sum(axis=1).mul(2).loc[start:end]
        )
    positive = sum(item["annual_return_pct"] > 0 for item in robustness["satellite_epoch_windows"].values())
    total = len(robustness["satellite_epoch_windows"])
    sign_p = sum(math.comb(total, count) for count in range(positive, total + 1)) / 2 ** total
    robustness["regime_sign_test"] = {
        "positive_regimes": positive, "total_regimes": total,
        "one_sided_p_value_under_fair_signs": sign_p,
        "note": "Exact upper-tail sign probability under p=0.5; the regime count is very small."
    }
    return statistics, pd.DataFrame(strategy), pd.concat(held, axis=1), diagnostics, returns, robustness


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/satellite_reit_alpha.json"))
    parser.add_argument("--output", type=Path, default=Path("research/satellite_reit_alpha"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    raw_dir = args.output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive = raw_dir / "ghs_wup_mtuc_statistics.zip"
    download(config["ghsl_statistics_url"], archive, args.refresh)
    workbook = extract_statistics(archive, raw_dir)
    (country_supply, epoch_signal, major_city_supply, major_epoch_signal,
     major_demand_supply_epoch_signal) = load_country_supply(workbook, config)
    prices = download_market_data(config, raw_dir, args.refresh)
    last_complete = (pd.Timestamp.today().to_period("M") - 1).to_timestamp("M")
    local_prices = pd.read_parquet(raw_dir / "reit_total_return_prices_local.parquet").loc[:last_complete]
    equity_prices, local_equity_prices = download_country_equity_data(config, raw_dir, args.refresh)
    stats, returns, positions, diagnostics, asset_returns, robustness = run(
        config, prices, local_prices, equity_prices, local_equity_prices,
        epoch_signal, major_epoch_signal, major_demand_supply_epoch_signal
    )
    country_supply.to_csv(args.output / "country_satellite_supply.csv", index=False)
    major_city_supply.to_csv(args.output / "major_city_satellite_supply.csv", index=False)
    epoch_signal.to_csv(args.output / "satellite_epoch_signal.csv")
    major_epoch_signal.to_csv(args.output / "major_city_epoch_signal.csv")
    major_demand_supply_epoch_signal.to_csv(args.output / "major_city_demand_supply_epoch_signal.csv")
    returns.to_parquet(args.output / "strategy_returns.parquet")
    positions.to_parquet(args.output / "positions.parquet")
    diagnostics.to_parquet(args.output / "signal_diagnostics.parquet")
    asset_returns.to_parquet(args.output / "asset_returns_usd.parquet")
    (args.output / "metrics.json").write_text(json.dumps(stats, indent=2))
    (args.output / "robustness.json").write_text(json.dumps(robustness, indent=2))
    manifest = {
        "model_status": "retrospective satellite reconstruction; not point-in-time validated",
        "market_data_status": "Yahoo adjusted-close research proxy; includes distributions but is not an institutional index",
        "universe": config["instruments"],
        "ghsl_archive_sha256": sha256(archive),
        "ghsl_workbook_sha256": sha256(workbook),
        "price_coverage": [prices.index.min().date().isoformat(), prices.index.max().date().isoformat()],
        "common_asset_coverage": [asset_returns.index.min().date().isoformat(), asset_returns.index.max().date().isoformat()],
        "observed_satellite_epochs": sorted(country_supply["Year"].unique().astype(int).tolist()),
        "satellite_signal_change_dates": [date.date().isoformat() for date in epoch_signal.loc["2000":].index],
        "settings": config,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(stats["major_city_reit_specific"]["full"], indent=2))


if __name__ == "__main__":
    main()
