"""Small USDA-anchored corn model and explicit, unlevered paper-trading diagnostic.

Run ``python -m src.corn_model backtest`` or ``forecast --as-of 2023-09-15``.
County vegetation improves a physical proxy; this module tests its translation
into a national USDA revision. USDA forecasts are never called market consensus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/corn_model"
CONFIG = ROOT / "config/corn_model.json"
MODELS = ("usda", "bias", "weather", "satellite")
FEATURES = {
    "weather": ["weather_anomaly_bu_acre", "is_september"],
    "satellite": ["weather_anomaly_bu_acre", "is_september", "ndvi_increment_bu_acre"],
}
T_HA_PER_BU_ACRE = .0628
HECTARES_PER_ACRE = .40468564224


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    """Strict JSON, including explicit nulls for unavailable observations."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA:
        return None
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(clean(value), indent=2, allow_nan=False) + "\n")


def configuration():
    return json.loads(CONFIG.read_text())


def load_proxies(directory=None, extra_path=None):
    directory = Path(directory or DEFAULT / "inputs/satellite")
    manifest = json.loads((directory / "manifest.json").read_text())
    frames = []
    for item in manifest["files"]:
        path = directory / item["path"]
        if digest(path) != item["sha256"]:
            raise ValueError(f"Satellite snapshot hash mismatch: {path}")
        frames.append(pd.read_csv(path))
    if extra_path:
        # A supplied new vintage may extend history but cannot silently replace it.
        frames.append(pd.read_csv(extra_path))
    return pd.concat(frames, ignore_index=True)


def build_panel(vintages, proxies):
    """As-of features and separately dated next-month evaluation labels.

    Future targets are attached for scoring only, never for selecting an anchor,
    eligible feature row, model fit, or position. A cancelled report stays missing.
    Naive county issue timestamps follow the archived pipeline's UTC convention.
    """
    v, p = vintages.copy(), proxies.copy()
    required = {"published_at", "report_date", "marketing_year_start", "yield_bu_acre",
                "harvested_area_m_acres", "production_m_bu", "ending_stocks_m_bu", "total_use_m_bu"}
    if not required.issubset(v):
        raise ValueError("Missing USDA columns: " + str(sorted(required - set(v))))
    prequired = {"year", "forecast_at", "eligible", "weather", "satellite", "trend", "prior_area_coverage"}
    if not prequired.issubset(p):
        raise ValueError("Missing county proxy columns: " + str(sorted(prequired - set(p))))
    v["published_at"] = pd.to_datetime(v.published_at, utc=True, errors="raise")
    v["report_date"] = pd.to_datetime(v.report_date, errors="raise").dt.strftime("%Y-%m-%d")
    p["forecast_at"] = pd.to_datetime(p.forecast_at, utc=True, errors="raise")
    if v.published_at.isna().any() or p.forecast_at.isna().any():
        raise ValueError("Missing publication or forecast timestamp")
    if v.duplicated(["published_at", "marketing_year_start"]).any():
        raise ValueError("Duplicate USDA vintage/marketing year")
    if p.duplicated(["year", "forecast_at"]).any():
        raise ValueError("Duplicate county forecast")
    if not p.eligible.isin([True, False]).all():
        raise ValueError("County eligibility must be boolean")
    if not p.year.eq(p.forecast_at.dt.year).all() or not p.forecast_at.dt.month.isin([8, 9]).all():
        raise ValueError("County forecasts require matching harvest year and August/September issue")
    expected_issues = pd.to_datetime(p.year.astype(str) + "-" + p.forecast_at.dt.month.astype(str)
                                    + "-15 12:00:00", utc=True)
    if not p.forecast_at.eq(expected_issues).all():
        raise ValueError("County issue must be August or September 15 at 12:00 UTC")
    coverage = pd.to_numeric(p.prior_area_coverage, errors="raise")
    if ((coverage < 0) | (coverage > 1 + 1e-9)).any():
        raise ValueError("Invalid reported-area coverage")
    v = v.sort_values("published_at")
    rows = []
    for item in p.sort_values("forecast_at").to_dict("records"):
        issue, year = item["forecast_at"], int(item["year"])
        target_month = (issue.tz_localize(None).to_period("M") + 1).strftime("%Y-%m")
        row = {"year": year, "forecast_at": issue, "target_month": target_month,
               "eligible": False, "reason": "", "coverage": float(item["prior_area_coverage"]),
               "prior_area_footprint": np.nan, "is_september": float(issue.month == 9),
               "weather_anomaly_bu_acre": np.nan, "ndvi_increment_bu_acre": np.nan,
               "target_yield_bu_acre": np.nan, "target_available_at": pd.NaT,
               "target_report_date": None, "label_revision_bu_acre": np.nan}
        anchors = v[(v.marketing_year_start == year) & (v.published_at < issue)]
        if anchors.empty:
            row["reason"] = "No same-crop-year USDA report published before issue"
            rows.append(row)
            continue
        anchor = anchors.iloc[-1]
        row.update(usda_report_date=anchor.report_date, usda_published_at=anchor.published_at,
                   usda_yield_bu_acre=float(anchor.yield_bu_acre),
                   harvested_area_m_acres=float(anchor.harvested_area_m_acres),
                   usda_production_m_bu=float(anchor.production_m_bu),
                   usda_ending_stocks_m_bu=float(anchor.ending_stocks_m_bu),
                   total_use_m_bu=float(anchor.total_use_m_bu))
        previous_area = v[(v.marketing_year_start == year - 1) & (v.published_at < issue)]
        area_ha = item.get("forecast_prior_area_ha", np.nan)
        if not previous_area.empty:
            national_acres = float(previous_area.iloc[-1].harvested_area_m_acres) * 1e6
            if np.isfinite(area_ha) and area_ha > 0 and national_acres > 0:
                row["prior_area_footprint"] = float(area_ha / HECTARES_PER_ACRE / national_acres)
        target = v[(v.marketing_year_start == year) & v.report_date.str.startswith(target_month)
                   & (v.published_at > issue)]
        if not target.empty:
            target = target.iloc[0]
            row.update(target_yield_bu_acre=float(target.yield_bu_acre),
                       target_available_at=target.published_at, target_report_date=target.report_date,
                       label_revision_bu_acre=float(target.yield_bu_acre - anchor.yield_bu_acre))
        if not item["eligible"]:
            row["reason"] = "County forecast is ineligible"
        elif not np.isfinite(row["coverage"]) or row["coverage"] < .8:
            row["reason"] = "Less than 80% of the reported eligible county area is covered"
        elif not all(np.isfinite(item[k]) and item[k] > 0 for k in ("weather", "satellite", "trend")):
            row["reason"] = "Missing or invalid matched county forecasts"
        elif not all(np.isfinite(row[k]) and row[k] > 0 for k in
                     ("usda_yield_bu_acre", "harvested_area_m_acres", "usda_production_m_bu", "total_use_m_bu")):
            row["reason"] = "Missing or invalid USDA supply anchor"
        elif not np.isfinite(row["usda_ending_stocks_m_bu"]) or row["usda_ending_stocks_m_bu"] < 0:
            row["reason"] = "Missing or invalid USDA ending stocks"
        else:
            row.update(eligible=True,
                       weather_anomaly_bu_acre=float((item["weather"] - item["trend"]) / T_HA_PER_BU_ACRE),
                       ndvi_increment_bu_acre=float((item["satellite"] - item["weather"]) / T_HA_PER_BU_ACRE))
        rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result["target_available_at"] = pd.to_datetime(result.target_available_at, utc=True)
    return result


def ridge_prediction(train, row, columns, alpha):
    x = train[columns].to_numpy(float)
    y = train.label_revision_bu_acre.to_numpy(float)
    means, scales = x.mean(axis=0), x.std(axis=0)
    scales = np.where(scales > 1e-12, scales, 1.)
    z = (x - means) / scales
    intercept = float(y.mean())
    coef = np.linalg.solve(z.T @ z + alpha * np.eye(len(columns)), z.T @ (y - intercept))
    test = np.asarray([row[c] for c in columns], float)
    prediction = intercept + ((test - means) / scales) @ coef
    return float(prediction), {"features": columns, "training_means": means.tolist(),
                               "training_scales": scales.tolist(), "standardized_coefficients": coef.tolist(),
                               "intercept_revision_bu_acre": intercept}


def forecast_row(row, panel, config=None):
    c = config or configuration()
    if panel.duplicated(["year", "forecast_at"]).any():
        raise ValueError("Duplicate calibration forecast issue")
    if not np.isfinite(c["ridge_alpha"]) or c["ridge_alpha"] <= 0:
        raise ValueError("Ridge regularization must be positive")
    row = dict(row)
    result = {k: row.get(k) for k in ("year", "forecast_at", "target_month", "usda_report_date",
                                    "usda_published_at", "usda_yield_bu_acre", "harvested_area_m_acres",
                                    "coverage", "prior_area_footprint")}
    result.update(status="abstain", reason="", models={}, training_years=[], training_rows=0,
                  data_status="Historical research inputs; county data are revised archives",
                  original_vintage_operational_verification=False, trading_alpha_verified=False)
    if not row.get("eligible", False):
        result["reason"] = row.get("reason") or "Input row is ineligible"
        return clean(result)
    if row["coverage"] < c["minimum_reported_area_coverage"]:
        result["reason"] = "Insufficient reported county coverage"
        return clean(result)
    issue = pd.Timestamp(row["forecast_at"])
    released = pd.to_datetime(panel.target_available_at, utc=True)
    train = panel[panel.eligible & (panel.year < row["year"]) & (released < issue)
                  & np.isfinite(panel.label_revision_bu_acre)].copy()
    train = train[np.isfinite(train[FEATURES["satellite"]]).all(axis=1)]
    years = sorted(map(int, train.year.unique()))
    result.update(training_years=years, training_rows=len(train))
    if len(years) < c["min_training_years"] or len(train) < c["min_training_rows"]:
        result["reason"] = "Need at least five prior harvest years and eight released calibration outcomes"
        return clean(result)
    same_horizon = train[train.is_september == row["is_september"]]
    if same_horizon.empty:
        result["reason"] = "No released prior outcomes for this forecast horizon"
        return clean(result)
    revisions = {"usda": (0., {}), "bias": (float(same_horizon.label_revision_bu_acre.mean()), {})}
    for model, columns in FEATURES.items():
        revisions[model] = ridge_prediction(train, row, columns, c["ridge_alpha"])
    for model, (delta, fitted) in revisions.items():
        predicted_yield = row["usda_yield_bu_acre"] + delta
        production_change = delta * row["harvested_area_m_acres"]
        production = row["usda_production_m_bu"] + production_change
        stocks = row["usda_ending_stocks_m_bu"] + production_change
        if not np.isfinite(predicted_yield) or predicted_yield <= 0 or production <= 0 or stocks < 0:
            result["reason"] = "Calibrated forecast implies a nonphysical supply scenario"
            result["models"] = {}
            return clean(result)
        result["models"][model] = {
            "predicted_yield_bu_acre": predicted_yield, "predicted_revision_bu_acre": delta,
            "conditional_production_m_bu": production,
            "conditional_production_change_m_bu": production_change,
            "conditional_ending_stocks_m_bu": stocks,
            "conditional_stocks_to_use": stocks / row["total_use_m_bu"], "calibration": fitted,
        }
    result.update(status="ready", reason="")
    return clean(result)


def paper_signal(forecast, consensus=None, threshold=.5, model="satellite"):
    """A fixed research position, not an expected-return or probability estimate."""
    if not np.isfinite(threshold) or threshold < 0 or model not in MODELS:
        raise ValueError("Invalid signal threshold or model")
    signal = {"status": "unavailable", "bias": "unavailable", "direction": 0,
              "reference_kind": "latest_usda", "yield_gap_bu_acre": None,
              "threshold_bu_acre": threshold, "eligible_for_live_trading": False,
              "reason": forecast.get("reason", "")}
    if forecast.get("status") != "ready":
        return signal
    reference = float(forecast["usda_yield_bu_acre"])
    if consensus is not None:
        required = {"published_at", "marketing_year_start", "target_month", "expected_yield_bu_acre", "source"}
        if not required.issubset(consensus):
            raise ValueError("Consensus requires timestamp, crop year, target month, yield and source")
        published = pd.Timestamp(consensus["published_at"])
        if published.tzinfo is None or pd.isna(published):
            raise ValueError("Consensus publication needs an explicit timezone")
        if published >= pd.Timestamp(forecast["forecast_at"]):
            raise ValueError("Consensus was not published before the forecast")
        if int(consensus["marketing_year_start"]) != forecast["year"] or consensus["target_month"] != forecast["target_month"]:
            raise ValueError("Consensus and forecast target differ")
        reference = float(consensus["expected_yield_bu_acre"])
        if not np.isfinite(reference) or reference <= 0 or not str(consensus["source"]).strip():
            raise ValueError("Invalid consensus value or source")
        signal["reference_kind"] = "user_supplied_analyst_consensus"
        signal["consensus_source"] = str(consensus["source"])
        signal["consensus_published_at"] = published.isoformat()
    gap = forecast["models"][model]["predicted_yield_bu_acre"] - reference
    direction = 1 if gap < -threshold else (-1 if gap > threshold else 0)
    signal.update(status="paper_only", bias={1: "long", -1: "short", 0: "flat"}[direction], direction=direction,
                  reference_yield_bu_acre=reference, yield_gap_bu_acre=gap, reason="",
                  interpretation="Lower predicted supply is a bullish hypothesis; prices and market consensus may disagree")
    return clean(signal)


def forecast_as_of(panel, as_of, config=None):
    stamp = pd.Timestamp(as_of)
    if pd.isna(stamp):
        raise ValueError("A valid forecast issue is required")
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
        if len(str(as_of)) == 10:
            stamp += pd.Timedelta(hours=12)
    selected = panel[panel.forecast_at.eq(stamp)]
    if selected.empty:
        return {"status": "abstain", "reason": "No matching county forecast at the requested issue; stale observations are never carried forward",
                "forecast_at": stamp.isoformat(), "latest_county_forecast_at": panel.forecast_at.max().isoformat() if len(panel) else None,
                "models": {}, "eligible_for_live_trading": False}
    if len(selected) != 1:
        raise ValueError("Ambiguous forecast issue")
    return forecast_row(selected.iloc[0], panel, config)


def score_forecasts(predictions, config=None):
    c = config or configuration()
    rows = predictions[predictions.status.eq("ready") & np.isfinite(predictions.target_yield_bu_acre)].copy()
    if rows.empty:
        return {"n_events": 0, "n_years": 0, "models": {}, "trading_alpha_verified": False}
    annual = {}
    metrics = {}
    for model in MODELS:
        err = rows[f"{model}_yield_bu_acre"] - rows.target_yield_bu_acre
        losses = pd.DataFrame({"year": rows.year, "mse": err**2, "mae": abs(err)}).groupby("year").mean()
        annual[model] = losses.mse
        metrics[model] = {"rmse_bu_acre": float(np.sqrt(losses.mse.mean())),
                          "mae_bu_acre": float(losses.mae.mean())}
    years = np.arange(int(rows.year.min()), int(rows.year.max()) + 1)
    losses = pd.DataFrame(annual).reindex(years)
    rng = np.random.default_rng(c["random_seed"])
    block = c["bootstrap_year_block"]
    starts = rng.integers(0, len(years), size=(c["bootstrap_draws"], (len(years) + block - 1) // block))
    ids = ((starts[..., None] + np.arange(block)) % len(years)).reshape(len(starts), -1)[:, :len(years)]
    sample = losses.to_numpy()[ids]
    count = np.isfinite(sample).sum(axis=1)
    means = np.divide(np.nansum(sample, axis=1), count, out=np.full(count.shape, np.nan), where=count > 0)
    rmses = np.sqrt(means)
    comparisons = {}
    for control in ("usda", "bias", "weather"):
        base = rmses[:, MODELS.index(control)]
        sat = rmses[:, MODELS.index("satellite")]
        valid = np.isfinite(base) & (base > 0) & np.isfinite(sat)
        gains = 1 - sat[valid] / base[valid]
        base_metric = metrics[control]["rmse_bu_acre"]
        comparisons[control] = {"rmse_reduction": 1 - metrics["satellite"]["rmse_bu_acre"] / base_metric if base_metric else None,
                                "conditional_ci95": np.quantile(gains, [.025, .975]).tolist() if len(gains) else None}
    return clean({"n_events": len(rows), "n_years": int(rows.year.nunique()),
                  "evaluation_years": sorted(map(int, rows.year.unique())), "models": metrics,
                  "satellite_comparisons": comparisons, "trading_alpha_verified": False,
                  "interpretation": "Exploratory next-report revision test; paired calendar-year uncertainty, no correction for prior research search"})


def evaluate_market(cards, market, config=None):
    from .corn_market_data import paper_trade
    c = config or configuration()
    events = []
    for card in cards:
        if card["status"] != "ready":
            continue
        for model in (*MODELS, "always_long", "always_short"):
            direction = 1 if model == "always_long" else (-1 if model == "always_short" else
                        paper_signal(card, threshold=c["yield_signal_threshold_bu_acre"], model=model)["direction"])
            trade = paper_trade(market, card["forecast_at"], direction,
                                holding_sessions=c["holding_sessions"], entry_cost_bps=c["entry_cost_bps"],
                                exit_cost_bps=c["exit_cost_bps"], short_borrow_rate=c["annual_short_borrow_rate"])
            stress = paper_trade(market, card["forecast_at"], direction,
                                 holding_sessions=c["holding_sessions"], entry_cost_bps=2*c["entry_cost_bps"],
                                 exit_cost_bps=2*c["exit_cost_bps"], short_borrow_rate=c["annual_short_borrow_rate"])
            if ((trade.get("net_return") is None) != (stress.get("net_return") is None)
                    or any(trade.get(k) != stress.get(k) for k in ("entry", "exit"))):
                raise ValueError("Cost sensitivity changed observable event support")
            events.append({"year": card["year"], "forecast_at": card["forecast_at"], "strategy": model,
                           "direction": direction, **trade, "double_cost_net_return": stress.get("net_return")})
    return pd.DataFrame(events)


def market_summary(events, config=None):
    c = config or configuration()
    if events.empty:
        return {"n_events": 0, "strategies": {}, "trading_alpha_verified": False}
    if events.duplicated(["forecast_at", "strategy"]).any():
        raise ValueError("Duplicate strategy/issue in market evaluation")
    if not np.isfinite(events.net_return.dropna()).all():
        raise ValueError("Nonfinite observed market return")
    wide = events.pivot(index="forecast_at", columns="strategy", values="net_return")
    support = wide.dropna().index
    strategies = {}
    for name, data in events.groupby("strategy", sort=False):
        valid = data[data.forecast_at.isin(support)].sort_values("forecast_at")
        # Fixed full-notional event returns only compound when windows do not overlap.
        entry_col = "entry" if "entry" in valid else "entry_date"
        exit_col = "exit" if "exit" in valid else "exit_date"
        overlapping = False
        if len(valid) and entry_col in valid and exit_col in valid:
            entries = pd.to_datetime(valid[entry_col], utc=True)
            exits = pd.to_datetime(valid[exit_col], utc=True)
            overlapping = bool((entries.iloc[1:].to_numpy() < exits.iloc[:-1].to_numpy()).any())
        returns = valid.net_return.to_numpy(float)
        bankrupt = bool((returns <= -1).any())
        stress_bankrupt = bool((valid.double_cost_net_return <= -1).any())
        compounding_valid = len(returns) and not overlapping and not bankrupt
        strategies[name] = {"n_events": len(valid), "n_positions": int(valid.direction.ne(0).sum()),
                            "mean_net_event_return": float(returns.mean()) if len(returns) else None,
                            "compound_net_event_return": float(np.prod(1+returns)-1) if compounding_valid else None,
                            "double_cost_compound_return": float(np.prod(1+valid.double_cost_net_return)-1) if compounding_valid and not stress_bankrupt else None,
                            "overlapping_windows": overlapping,
                            "bankruptcy_encountered": bankrupt,
                            "double_cost_bankruptcy_encountered": stress_bankrupt,
                            "unavailable_or_unmatched_events": int(len(data)-len(valid))}
    if len(support):
        matched = events[events.forecast_at.isin(support)]
        annual = matched.groupby(["year", "strategy"]).net_return.mean().unstack()
        years = np.arange(int(annual.index.min()), int(annual.index.max()) + 1)
        annual = annual.reindex(years)
        block = c["bootstrap_year_block"]
        starts = np.random.default_rng(c["random_seed"]).integers(
            0, len(years), size=(c["bootstrap_draws"], (len(years)+block-1)//block))
        ids = ((starts[..., None] + np.arange(block)) % len(years)).reshape(len(starts), -1)[:, :len(years)]
        sample = annual.to_numpy()[ids]
        count = np.isfinite(sample).sum(axis=1)
        means = np.divide(np.nansum(sample, axis=1), count, out=np.full(count.shape, np.nan), where=count > 0)
        for i, name in enumerate(annual.columns):
            finite = means[np.isfinite(means[:, i]), i]
            strategies[name]["mean_annual_event_return_ci95"] = np.quantile(finite, [.025, .975]).tolist() if len(finite) else None
            if "weather" in annual:
                diff = means[:, i] - means[:, list(annual.columns).index("weather")]
                diff = diff[np.isfinite(diff)]
                strategies[name]["paired_mean_advantage_vs_weather_ci95"] = np.quantile(diff, [.025, .975]).tolist() if len(diff) else None
    return clean({"n_events": int(events.forecast_at.nunique()), "strategies": strategies,
                  "matched_events": len(support),
                  "instrument": "CORN futures ETF, not an individual corn futures contract",
                  "trading_alpha_verified": False,
                  "interpretation": "Hypothetical 1x event-window returns using USDA as reference, not market consensus; not annualized or risk-adjusted alpha"})


def load_inputs(extra_path=None):
    from .corn_usda_data import load_vintages
    return load_vintages(), load_proxies(extra_path=extra_path)


def backtest(output=DEFAULT):
    from .corn_market_data import load_market
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    c = configuration()
    vintages, proxies = load_inputs()
    panel = build_panel(vintages, proxies)
    cards = [forecast_row(row, panel, c) for row in panel.to_dict("records")]
    prediction_rows = []
    for row, card in zip(panel.to_dict("records"), cards):
        flat = {k: row.get(k) for k in ("year", "forecast_at", "target_month", "target_report_date", "target_available_at",
                                      "target_yield_bu_acre", "label_revision_bu_acre", "coverage", "prior_area_footprint")}
        flat.update(status=card["status"], reason=card["reason"], training_rows=card["training_rows"])
        flat.update({f"{model}_yield_bu_acre": card["models"].get(model, {}).get("predicted_yield_bu_acre", np.nan) for model in MODELS})
        prediction_rows.append(flat)
    predictions = pd.DataFrame(prediction_rows)
    events = evaluate_market(cards, load_market(DEFAULT / "inputs/market"), c)
    inputs = {str(path.relative_to(ROOT)): {"bytes": path.stat().st_size, "sha256": digest(path)}
              for path in sorted((DEFAULT / "inputs").rglob("*")) if path.is_file()}
    write_json(output / "input_manifest.json", inputs)
    protocol = json.loads((DEFAULT / "protocol.json").read_text())
    summary = {"forecast": score_forecasts(predictions, c), "trading": market_summary(events, c),
               "configuration": c, "code_sha256": digest(__file__), "config_sha256": digest(CONFIG),
               "protocol_sha256": digest(DEFAULT / "protocol.json"),
               "configuration_matches_fixed_protocol": digest(CONFIG) == protocol["config_sha256"],
               "input_manifest_sha256": digest(output / "input_manifest.json"),
               "source_adapter_sha256": {name: digest(ROOT / "src" / name) for name in
                                          ("corn_usda_data.py", "corn_market_data.py")},
               "latest_county_forecast_at": panel.forecast_at.max(),
               "latest_usda_publication_at": vintages.published_at.max(),
               "consensus_available": False, "original_vintage_operational_verification": False,
               "trading_alpha_verified": False}
    for name, frame in [("panel.csv", panel), ("predictions.csv", predictions), ("paper_trades.csv", events)]:
        frame.to_csv(output / name, index=False, float_format="%.12g")
    write_json(output / "forecast_cards.json", cards)
    write_json(output / "summary.json", summary)
    latest = [card for card in cards if card["status"] == "ready"]
    if latest:
        example = {**latest[-1], "paper_signal": paper_signal(latest[-1], threshold=c["yield_signal_threshold_bu_acre"]),
                   "historical_example_only": True}
        write_json(output / "historical_example.json", example)
    return clean(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backtest", help="Reproduce the fixed historical diagnostic offline")
    back.add_argument("--output", type=Path, default=DEFAULT)
    fore = sub.add_parser("forecast", help="Write a dated forecast/paper card; no live orders")
    fore.add_argument("--as-of", required=True, help="UTC ISO timestamp or YYYY-MM-DD (12:00 UTC)")
    fore.add_argument("--satellite-input", type=Path, help="CSV with new matching-year aggregate forecasts")
    fore.add_argument("--consensus", type=Path, help="JSON containing a genuine timestamped analyst survey")
    fore.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()
    if args.command == "backtest":
        result = backtest(args.output)
    else:
        vintages, proxies = load_inputs(args.satellite_input)
        panel = build_panel(vintages, proxies)
        result = forecast_as_of(panel, args.as_of)
        consensus = json.loads(args.consensus.read_text()) if args.consensus else None
        result["paper_signal"] = paper_signal(result, consensus)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
    print(json.dumps(clean(result), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
