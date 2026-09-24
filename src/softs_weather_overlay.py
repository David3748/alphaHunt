#!/usr/bin/env python3
"""Literature-defined, stage-aware weather overlay for soft commodities."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


SOFTS = ["coffee", "sugar", "cocoa", "cotton"]


def expanding_seasonal_z(series: pd.Series, min_years: int) -> pd.Series:
    week = series.index.isocalendar().week.astype(int)
    result = pd.Series(np.nan, index=series.index, dtype=float)
    for number in sorted(week.unique()):
        sample = series[week == number]
        mean = sample.expanding(min_periods=min_years).mean().shift(1)
        std = sample.expanding(min_periods=min_years).std(ddof=1).shift(1)
        result.loc[sample.index] = (sample - mean) / std.replace(0, np.nan)
    return result.clip(-4, 4)


def load_power_region(raw_dir: Path, region: str) -> pd.DataFrame:
    frames = []
    for path in sorted(raw_dir.glob(f"power_{region}_*.json")):
        payload = json.loads(path.read_text())
        parameters = payload["properties"]["parameter"]
        frame = pd.DataFrame(parameters)
        frame.index = pd.to_datetime(frame.index, format="%Y%m%d")
        frame = frame.replace(float(payload["header"]["fill_value"]), np.nan)
        frames.append(frame.astype(float))
    if not frames:
        raise FileNotFoundError(f"No NASA POWER cache found for {region}")
    result = pd.concat(frames).sort_index()
    return result[~result.index.duplicated(keep="last")]


def weekly_anomalies(daily: pd.DataFrame, min_years: int) -> pd.DataFrame:
    weekly = pd.DataFrame({
        "precip": daily["PRECTOTCORR"].resample("W-FRI").sum(min_count=4),
        "tmax": daily["T2M_MAX"].resample("W-FRI").mean(),
        "tmin": daily["T2M_MIN"].resample("W-FRI").min(),
    })
    for variable in ["precip", "tmax", "tmin"]:
        weekly[f"{variable}_z"] = expanding_seasonal_z(weekly[variable], min_years)
    return weekly


def rule_score(anomalies: pd.DataFrame, rule: dict) -> pd.Series:
    dry = (-anomalies["precip_z"]).clip(lower=0)
    wet = anomalies["precip_z"].clip(lower=0)
    heat = anomalies["tmax_z"].clip(lower=0)
    cold = (-anomalies["tmin_z"]).clip(lower=0)
    # Combined reproductive-stage drought and heat has super-additive damage.
    combination = np.minimum(dry, heat)
    score = (float(rule.get("dry", 0)) * dry + float(rule.get("wet", 0)) * wet
             + float(rule.get("heat", 0)) * heat + float(rule.get("cold", 0)) * cold
             + float(rule.get("combo", 0)) * combination)
    active = score.index.month.isin(rule["months"])
    return score.where(active, 0.0)


def build_stage_weather_signal(raw_dir: Path, nasa_config: dict, overlay_config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    min_years = int(overlay_config["seasonal_min_years"])
    region_scores, commodity_scores = {}, {}
    for commodity in SOFTS:
        weighted = []
        total_weight = 0.0
        for region, rules in overlay_config["rules"][commodity].items():
            weight = float(nasa_config["regions"][region].get(commodity, 0))
            if weight <= 0:
                continue
            anomalies = weekly_anomalies(load_power_region(raw_dir, region), min_years)
            score = sum((rule_score(anomalies, rule) for rule in rules), start=pd.Series(0.0, index=anomalies.index))
            score = score.clip(0, 4)
            region_scores[(commodity, region)] = score
            weighted.append(weight * score)
            total_weight += weight
        commodity_scores[commodity] = sum(weighted) / total_weight
    panel = pd.DataFrame(commodity_scores).sort_index()
    persistent = panel.ewm(halflife=float(overlay_config["persistence_halflife_weeks"]), min_periods=4).mean()
    observable = persistent.shift(int(overlay_config["information_lag_weeks"]))
    relative = observable.sub(observable.mean(axis=1), axis=0)
    signal = relative.div(relative.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    return signal, pd.concat(region_scores, axis=1).sort_index()


def monthly_stage_weather_signal(raw_dir: Path, nasa_config: dict, overlay_config: dict,
                                 monthly_index: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    weekly, regions = build_stage_weather_signal(raw_dir, nasa_config, overlay_config)
    monthly = weekly.resample("ME").last().reindex(monthly_index).ffill().fillna(0.0)
    return monthly, regions
