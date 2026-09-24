#!/usr/bin/env python3
"""Supply-vs-demand and property-type features for the satellite REIT study.

This module deliberately has no dependency on ``satellite_reit_backtest``.  It
accepts small, tidy panels so that a data vendor can be swapped without
changing the feature definitions.  All features are stamped with an
``available_date`` after the source observation date; callers should still
lag the resulting position by one return period.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


SUPPLY_DEMAND_REQUIRED = {"period", "geo", "built_up_km2", "population"}
PROPERTY_NUMERIC_COLUMNS = [
    "footprint_area_m2", "height_m", "floor_count", "compactness",
    "parking_area_ratio", "roof_unit_count", "window_density",
    "building_age_years", "has_substation", "has_cooling_plant",
]
PROPERTY_CATEGORICAL_COLUMNS = ["poi_category", "land_use"]
PROPERTY_TYPES = ["apartment", "logistics", "office", "retail", "data_center", "self_storage"]


def _period_end(value: Any) -> pd.Timestamp:
    """Convert annual integers or date-like values to a period end."""

    if isinstance(value, (int, np.integer)) or (isinstance(value, float) and value.is_integer()):
        return pd.Timestamp(year=int(value), month=12, day=31)
    return pd.Timestamp(value)


def _growth(current: pd.Series, periods: int) -> pd.Series:
    previous = current.shift(periods)
    valid = current.gt(0) & previous.gt(0)
    result = pd.Series(np.nan, index=current.index, dtype=float)
    result.loc[valid] = (current.loc[valid] / previous.loc[valid]).pow(1.0 / periods) - 1.0
    return result


def _safe_weighted_mean(frame: pd.DataFrame, columns: list[str], weights: dict[str, float]) -> pd.Series:
    """Weighted row mean that renormalizes when a proxy is missing."""

    weighted = []
    denominator = pd.Series(0.0, index=frame.index)
    for column in columns:
        if column not in frame:
            continue
        weight = float(weights.get(column, 0.0))
        values = frame[column]
        weighted.append(values.fillna(0.0) * weight)
        denominator = denominator.add(values.notna().astype(float) * weight, fill_value=0.0)
    if not weighted:
        return pd.Series(np.nan, index=frame.index)
    return sum(weighted).div(denominator.replace(0.0, np.nan))


def supply_demand_features(
    panel: pd.DataFrame,
    *,
    growth_periods: int = 1,
    availability_lag_months: int = 12,
    demand_weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build a point-in-time supply/demand panel.

    ``panel`` is tidy with one or more rows per ``geo``/``period`` and must
    contain built-up area and population.  ``night_lights`` is optional and
    should be a stable annual composite (not a contemporaneous market
    variable).  Source rows are summed by geography and period.  Growth is
    calculated only from observations at or before the source ``period_end``;
    the features are released at ``period_end + availability_lag_months``.

    Positive ``excess_supply`` means construction is outrunning demand.  The
    investable demand-minus-supply signal is its negative.
    """

    missing = SUPPLY_DEMAND_REQUIRED.difference(panel.columns)
    if missing:
        raise ValueError(f"missing supply/demand columns: {sorted(missing)}")
    if not isinstance(growth_periods, int) or growth_periods < 1:
        raise ValueError("growth_periods must be a positive integer")
    frame = panel.copy()
    frame["period_end"] = frame["period"].map(_period_end)
    for column in ["built_up_km2", "population", "night_lights"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if frame[column].lt(0).any():
                raise ValueError(f"{column} cannot be negative")
    value_columns = [column for column in ["built_up_km2", "population", "night_lights"] if column in frame]
    grouped = (
        frame.groupby(["geo", "period_end"], as_index=False, observed=True)[value_columns]
        .sum(min_count=1)
        .sort_values(["geo", "period_end"])
    )
    grouped["period"] = grouped["period_end"].dt.year
    for source, output in [("built_up_km2", "built_up_growth"), ("population", "population_growth"), ("night_lights", "night_lights_growth")]:
        if source in grouped:
            grouped[output] = grouped.groupby("geo", observed=True)[source].transform(
                lambda values: _growth(values, growth_periods)
            )
    weights = demand_weights or {"population_growth": 0.6, "night_lights_growth": 0.4}
    demand_columns = [column for column in ["population_growth", "night_lights_growth"] if column in grouped]
    grouped["demand_growth"] = _safe_weighted_mean(grouped, demand_columns, weights)
    grouped["excess_supply"] = grouped["built_up_growth"] - grouped["demand_growth"]
    grouped["demand_minus_supply"] = -grouped["excess_supply"]
    grouped["built_up_per_capita_m2"] = (
        grouped["built_up_km2"].mul(1_000_000).div(grouped["population"].replace(0.0, np.nan))
    )
    grouped["available_date"] = grouped["period_end"] + pd.DateOffset(months=int(availability_lag_months))
    grouped["source_period_end"] = grouped["period_end"]
    grouped["proxy_count"] = grouped[demand_columns].notna().sum(axis=1)
    return grouped[
        ["geo", "period", "source_period_end", "available_date", "built_up_km2", "population"]
        + (["night_lights"] if "night_lights" in grouped else [])
        + ["built_up_growth", "population_growth"]
        + (["night_lights_growth"] if "night_lights_growth" in grouped else [])
        + ["demand_growth", "excess_supply", "demand_minus_supply", "built_up_per_capita_m2", "proxy_count"]
    ].sort_values(["available_date", "geo"])


def download_world_bank_population(
    countries: list[str],
    *,
    start_year: int = 2000,
    end_year: int | None = None,
    session: Any | None = None,
) -> pd.DataFrame:
    """Download WDI total population as a tidy demand-proxy panel.

    The WDI endpoint is public and does not require an API key.  ``countries``
    are ISO-2/ISO-3 country codes accepted by the endpoint.  The returned
    panel can be joined to a matching built-up or metro aggregation before
    calling :func:`supply_demand_features`.  Downloads are intentionally not
    cached here; persist the response and checksum in a research manifest.
    """

    if start_year > (end_year if end_year is not None else start_year):
        raise ValueError("start_year must not exceed end_year")
    import requests

    client = session or requests
    end = int(end_year if end_year is not None else pd.Timestamp.today().year)
    rows: list[dict[str, Any]] = []
    for country in countries:
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/SP.POP.TOTL"
        response = client.get(url, params={"format": "json", "per_page": 1000, "date": f"{start_year}:{end}"}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if len(payload) < 2 or payload[1] is None:
            continue
        for item in payload[1]:
            if item.get("value") is None:
                continue
            rows.append({
                "period": int(item["date"]),
                "geo": item.get("countryiso3code") or str(country).upper(),
                "population": float(item["value"]),
                "source": "World Bank WDI SP.POP.TOTL",
                "source_url": url,
            })
    if not rows:
        return pd.DataFrame(columns=["period", "geo", "population", "source", "source_url"])
    return pd.DataFrame(rows).sort_values(["geo", "period"]).reset_index(drop=True)


def supply_demand_signal(features: pd.DataFrame, *, geo_column: str = "geo") -> pd.DataFrame:
    """Return a cross-sectional, neutralized demand-minus-supply signal."""

    required = {geo_column, "available_date", "demand_minus_supply"}
    if not required.issubset(features.columns):
        raise ValueError(f"missing signal columns: {sorted(required.difference(features.columns))}")
    output = features[[geo_column, "available_date", "demand_minus_supply"]].copy()
    output["signal"] = output.groupby("available_date", observed=True)["demand_minus_supply"].transform(
        lambda values: values - values.mean()
    )
    gross = output.groupby("available_date", observed=True)["signal"].transform(lambda values: values.abs().sum())
    output["signal"] = output["signal"].div(gross.replace(0.0, np.nan)).fillna(0.0)
    return output


def validate_property_features(frame: pd.DataFrame) -> None:
    """Validate the footprint feature contract before classification."""

    missing = {"building_id"}.difference(frame.columns)
    if missing:
        raise ValueError(f"missing property feature columns: {sorted(missing)}")
    if frame["building_id"].duplicated().any():
        raise ValueError("building_id must be unique for a footprint snapshot")
    for column in PROPERTY_NUMERIC_COLUMNS:
        if column in frame:
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().any() and (values.dropna() < 0).any() and column not in {"has_substation", "has_cooling_plant"}:
                raise ValueError(f"{column} cannot be negative")


_TYPE_RULES = {
    "apartment": {"numeric": {"footprint_area_m2": (2500, 3500), "height_m": (18, 14), "floor_count": (5, 4), "compactness": (0.62, 0.25)}, "tags": ["residential", "apartment", "multi-family"]},
    "logistics": {"numeric": {"footprint_area_m2": (12000, 11000), "height_m": (10, 8), "floor_count": (1.5, 1.5), "compactness": (0.78, 0.2), "parking_area_ratio": (0.18, 0.2)}, "tags": ["warehouse", "logistics", "industrial", "distribution"]},
    "office": {"numeric": {"footprint_area_m2": (7000, 7000), "height_m": (26, 17), "floor_count": (7, 5), "compactness": (0.58, 0.25)}, "tags": ["office", "commercial", "business"]},
    "retail": {"numeric": {"footprint_area_m2": (5500, 7000), "height_m": (9, 7), "floor_count": (2, 2), "compactness": (0.55, 0.3), "parking_area_ratio": (0.38, 0.3)}, "tags": ["retail", "shopping", "supermarket", "mall"]},
    "data_center": {"numeric": {"footprint_area_m2": (9000, 10000), "height_m": (12, 9), "floor_count": (2.5, 2), "compactness": (0.8, 0.2), "has_substation": (1, 0.5), "has_cooling_plant": (1, 0.5)}, "tags": ["data center", "datacenter", "server", "cloud"]},
    "self_storage": {"numeric": {"footprint_area_m2": (4000, 5500), "height_m": (11, 9), "floor_count": (3, 3), "compactness": (0.78, 0.2)}, "tags": ["self-storage", "storage", "mini-storage"]},
}


def _numeric_likelihood(value: Any, target: float, scale: float) -> float | None:
    if pd.isna(value):
        return None
    return float(np.exp(-abs(float(value) - target) / max(scale, 1e-9)))


def classify_property_types(frame: pd.DataFrame, *, confidence_floor: float = 0.30) -> pd.DataFrame:
    """Classify footprint rows with transparent rules and evidence columns.

    This is a bootstrapping layer, not ground truth: numeric likelihoods and
    OSM/parcel tags are combined into scores, then softmax-normalized.  A
    later run can train the optional supervised model below on manually
    reviewed labels while retaining these rule scores as audit features.
    """

    validate_property_features(frame)
    output = frame.copy()
    text = output.get("poi_category", pd.Series("", index=output.index)).fillna("").astype(str).str.lower()
    land = output.get("land_use", pd.Series("", index=output.index)).fillna("").astype(str).str.lower()
    scores = pd.DataFrame(index=output.index)
    for kind, rules in _TYPE_RULES.items():
        score = pd.Series(0.0, index=output.index)
        evidence = pd.Series(0, index=output.index, dtype=int)
        for column, (target, scale) in rules["numeric"].items():
            if column not in output:
                continue
            likelihood = output[column].map(lambda value: _numeric_likelihood(value, target, scale))
            score = score.add(likelihood.fillna(0.0), fill_value=0.0)
            evidence = evidence.add(likelihood.notna().astype(int), fill_value=0)
        keyword = "|".join(rules["tags"])
        tagged = text.str.contains(keyword, regex=True, na=False) | land.str.contains(keyword, regex=True, na=False)
        score = score + tagged.astype(float) * 3.0
        evidence = evidence + tagged.astype(int)
        scores[f"score_{kind}"] = score
        output[f"evidence_{kind}"] = evidence
    # Softmax keeps confidence comparable across rows with different scales.
    logits = scores.to_numpy(dtype=float)
    logits = logits - logits.max(axis=1, keepdims=True)
    probabilities = np.exp(logits)
    probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
    probability_frame = pd.DataFrame(probabilities, index=output.index, columns=[f"prob_{kind}" for kind in PROPERTY_TYPES])
    output = pd.concat([output, scores, probability_frame], axis=1)
    ordered = probability_frame.to_numpy()
    best = ordered.argmax(axis=1)
    second = np.partition(ordered, -2, axis=1)[:, -2]
    output["property_type"] = np.array(PROPERTY_TYPES, dtype=object)[best]
    output["confidence"] = ordered[np.arange(len(output)), best]
    output["confidence_margin"] = output["confidence"] - second
    evidence_columns = [f"evidence_{kind}" for kind in PROPERTY_TYPES]
    output["evidence_count"] = output[evidence_columns].max(axis=1)
    output.loc[(output["confidence"] < float(confidence_floor)) | (output["evidence_count"] < 2), "property_type"] = "unknown"
    return output


@dataclass
class PropertyTypeModel:
    """Fitted supervised model plus the exact feature contract used."""

    pipeline: Any
    feature_columns: list[str]
    label_column: str


def fit_property_type_model(frame: pd.DataFrame, *, label_column: str = "property_type", random_state: int = 7) -> PropertyTypeModel:
    """Fit a reproducible, auditable baseline on reviewed footprint labels."""

    validate_property_features(frame)
    if label_column not in frame:
        raise ValueError(f"missing label column: {label_column}")
    labels = frame[label_column].astype(str)
    if labels.nunique() < 2:
        raise ValueError("at least two property types are required")
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = [column for column in PROPERTY_NUMERIC_COLUMNS if column in frame.columns]
    categorical = [column for column in PROPERTY_CATEGORICAL_COLUMNS if column in frame.columns]
    if not numeric and not categorical:
        raise ValueError("no model features supplied")
    transformer = ColumnTransformer([
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical),
    ])
    pipeline = Pipeline([
        ("features", transformer),
        ("model", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=random_state)),
    ])
    pipeline.fit(frame[numeric + categorical], labels)
    return PropertyTypeModel(pipeline, numeric + categorical, label_column)


def validate_property_type_model(model: PropertyTypeModel, frame: pd.DataFrame) -> dict[str, Any]:
    """Return holdout-free diagnostics for a supplied validation slice."""

    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

    if model.label_column not in frame:
        raise ValueError(f"missing label column: {model.label_column}")
    actual = frame[model.label_column].astype(str)
    predicted = model.pipeline.predict(frame[model.feature_columns])
    labels = sorted(set(actual) | set(predicted))
    return {
        "rows": int(len(frame)),
        "accuracy": round(float(accuracy_score(actual, predicted)), 4),
        "macro_f1": round(float(f1_score(actual, predicted, average="macro", zero_division=0)), 4),
        "labels": labels,
        "confusion_matrix": confusion_matrix(actual, predicted, labels=labels).tolist(),
    }
