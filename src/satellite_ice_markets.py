#!/usr/bin/env python3
"""Satellite Arctic sea-ice event forecasts and read-only prediction-market paper screen.

Historical hindcasts use the current NSIDC archive, not original daily vintages.
All market requests are GET. This module never submits orders.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "satellite_ice_markets"
NSIDC = "https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv"
NSIDC_WORKBOOK = "https://noaadata.apps.nsidc.org/NOAA/G02135/seaice_analysis/Sea_Ice_Index_Daily_Extent_G02135_v4.0.xlsx"
KALSHI = "https://external-api.kalshi.com/trade-api/v2/events/KXARCTICICEMIN-26OCT01"
POLY = "https://gamma-api.polymarket.com/events?slug=min-arctic-sea-ice-extent-this-summer"
CLOB = "https://clob.polymarket.com"
ISSUE_DATES = ((8, 15), (9, 1), (9, 15), (9, 25))
LABEL_EMBARGO = (10, 15)  # prior-year settlement availability assumption
OBSERVATION_LAG_DAYS = 3  # assumption; historical releases not reconstructed
MIN_TRAIN = 20
K_ANALOGS = 12
FEE_RESERVE = 0.02  # USD per contract screening allowance, not verified exchange fee
EDGE_BUFFER = 0.03
MAX_CONTRACTS = 5
POLY_BINS = [(None, 4.0), (4.0, 4.2), (4.2, 4.4), (4.4, 4.6),
             (4.6, 4.8), (4.8, 5.0), (5.0, None)]


def _save_response(session: requests.Session, url: str, dest: Path) -> dict:
    response = session.get(url, timeout=90)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "bytes": len(response.content), "http_last_modified": response.headers.get("Last-Modified")}


def fetch(out: Path = OUT) -> dict:
    """Capture one dated, checkable market and NSIDC snapshot using public GETs."""
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    manifest = {}
    for name, url in (("nsidc_daily.csv", NSIDC), ("nsidc_daily_workbook.xlsx", NSIDC_WORKBOOK),
                      ("kalshi_event.json", KALSHI + "?with_nested_markets=true"),
                      ("polymarket_event.json", POLY)):
        manifest[name] = _save_response(session, url, raw / name)
    event = json.loads((raw / "polymarket_event.json").read_text())[0]
    for market in event["markets"]:
        for side, token in zip(("yes", "no"), json.loads(market["clobTokenIds"])):
            name = f"poly_{market['id']}_{side}_book.json"
            url = f"{CLOB}/book?token_id={token}"
            manifest[name] = _save_response(session, url, raw / name)
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_daily(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(path.read_bytes()), skiprows=[1], skipinitialspace=True)
    required = {"Year", "Month", "Day", "Extent", "Missing"}
    if not required.issubset(frame.columns):
        raise ValueError("Unexpected NSIDC columns")
    frame["date"] = pd.to_datetime(frame[["Year", "Month", "Day"]].rename(
        columns={"Year": "year", "Month": "month", "Day": "day"}), errors="raise")
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted dates")
    if not frame.Extent.between(0, 20).all() or not frame.Missing.between(0, 20).all():
        raise ValueError("Missing or invalid NSIDC values")
    return frame.set_index("date")[["Extent", "Missing"]]


def verify_poly_workbook(csv_daily: pd.DataFrame, workbook: Path) -> dict:
    """Polymarket names the NH-Daily-Extent worksheet, so audit its CSV equivalence."""
    sheet = pd.read_excel(workbook, sheet_name="NH-Daily-Extent")
    month_names = {name: n for n, name in enumerate(calendar.month_name) if name}
    months = sheet.iloc[:, 0].ffill().map(month_names)
    if months.isna().any() or sheet.iloc[:, 1].isna().any():
        raise ValueError("Unexpected NSIDC workbook date layout")
    count = 0
    for year in range(2000, 2027):
        if year not in sheet.columns:
            raise ValueError(f"Missing workbook year {year}")
        for month, day, value in zip(months, sheet.iloc[:, 1], sheet[year]):
            if pd.isna(value):
                continue
            try:
                stamp = pd.Timestamp(year=year, month=int(month), day=int(day))
            except ValueError:
                continue
            if stamp not in csv_daily.index or abs(float(csv_daily.loc[stamp, "Extent"]) - float(value)) > 1e-9:
                raise ValueError(f"NSIDC daily CSV/workbook mismatch on {stamp.date()}")
            count += 1
    if count < 9000:
        raise ValueError("Too few workbook comparison points")
    return {"matching_daily_values_2000_to_2026": count, "sheet": "NH-Daily-Extent"}


def season(daily: pd.DataFrame, year: int, issue: date, window_start: date) -> tuple[float, float, float, float]:
    """Return observed min, current extent, past-7d slope, final min; no future in features."""
    end = date(year, 10, 1)
    if not window_start <= issue <= end:
        raise ValueError("Issue outside settlement window")
    available = issue - timedelta(days=OBSERVATION_LAG_DAYS)
    before = daily.loc[pd.Timestamp(window_start):pd.Timestamp(available), "Extent"]
    complete = daily.loc[pd.Timestamp(window_start):pd.Timestamp(end), "Extent"]
    if len(before) < 8 or len(complete) < len(before):
        raise ValueError("Insufficient satellite observations")
    if len(before) != (available - window_start).days + 1 or len(complete) != (end - window_start).days + 1:
        raise ValueError("Historical season has skipped daily observations")
    if before.isna().any() or complete.isna().any():
        raise ValueError("Missing extent")
    if (pd.Timestamp(end) - complete.index.max()).days > 0:
        raise ValueError("Unobserved settlement day")
    return float(before.min()), float(before.iloc[-1]), float(before.iloc[-1] - before.iloc[-8]), float(complete.min())


def historical_rows(daily: pd.DataFrame, target_year: int, issue_month: int, issue_day: int,
                    window_month: int = 8, window_day: int = 1) -> list[dict]:
    rows = []
    for year in range(1988, target_year):
        issue = date(year, issue_month, issue_day)
        if date(year, *LABEL_EMBARGO) >= date(target_year, issue_month, issue_day):
            continue
        try:
            observed, current, slope, final = season(daily, year, issue, date(year, window_month, window_day))
        except ValueError:
            continue
        rows.append({"year": year, "observed_min": observed, "current": current,
                     "slope_7d": slope, "final_min": final,
                     "remaining_drop": max(0.0, observed - final)})
    return rows


def analog_drops(rows: list[dict], observed: float, current: float, slope: float) -> np.ndarray:
    """Fixed nearest-season analogs. Features concern *remaining* melt, not a trend fit."""
    if len(rows) < MIN_TRAIN:
        raise ValueError("Need at least 20 prior complete seasons")
    frame = pd.DataFrame(rows)
    # Fixed physical scales (million sq km) prevent recalibrating on the target year.
    distances = ((frame.current.to_numpy() - observed - (current - observed)) / 0.5) ** 2
    distances += ((frame.slope_7d.to_numpy() - slope) / 0.25) ** 2
    distances += ((frame.current.to_numpy() - current) / 1.5) ** 2
    nearest = np.lexsort((frame.year.to_numpy(), distances))[:K_ANALOGS]
    return frame.remaining_drop.to_numpy()[nearest]


def probability(samples: np.ndarray, observed_min: float, lower: float | None,
                upper: float | None) -> float:
    """Laplace-smoothed analog probability with logically certain states enforced."""
    if lower is not None and observed_min < lower:
        return 0.0
    if upper is not None and observed_min < upper and lower is None:
        return 1.0
    finals = observed_min - samples
    yes = np.ones(len(samples), dtype=bool)
    if lower is not None:
        yes &= finals >= lower
    if upper is not None:
        yes &= finals < upper
    return float((yes.sum() + 0.5) / (len(samples) + 1))


def categorical_probabilities(samples: np.ndarray, observed_min: float,
                              bins: list[tuple[float | None, float | None]]) -> list[float]:
    """One coherent distribution over exhaustive bins; impossible upper bins get zero."""
    final = observed_min - samples
    allowed = [i for i, (low, _) in enumerate(bins) if low is None or low <= observed_min]
    counts = np.zeros(len(bins), dtype=float)
    for value in final:
        matches = [i for i, (low, high) in enumerate(bins)
                   if (low is None or value >= low) and (high is None or value < high)]
        if len(matches) != 1:
            raise ValueError("Polymarket bins are not exhaustive and disjoint")
        counts[matches[0]] += 1
    counts[allowed] += 1 / len(allowed)  # one total Dirichlet pseudocount
    return (counts / counts.sum()).tolist()


def forecast(daily: pd.DataFrame, year: int, issue: date, window_start: date,
             lower: float | None = None, upper: float | None = None) -> dict:
    observed, current, slope, _ = season(daily, year, issue, window_start) if year < daily.index.max().year else season_live(daily, issue, window_start)
    rows = historical_rows(daily, year, issue.month, issue.day, window_start.month, window_start.day)
    samples = analog_drops(rows, observed, current, slope)
    return {"year": year, "issue_date": issue.isoformat(), "assumed_latest_available":
            (issue - timedelta(days=OBSERVATION_LAG_DAYS)).isoformat(),
            "window_start": window_start.isoformat(), "window_end": date(year, 10, 1).isoformat(),
            "observed_min": observed, "current_extent": current, "slope_7d": slope,
            "analog_years": K_ANALOGS, "training_years": len(rows),
            "predicted_final_min": float(observed - samples.mean()),
            "predicted_probability": probability(samples, observed, lower, upper),
            "analog_remaining_drops": samples.tolist()}


def season_live(daily: pd.DataFrame, issue: date, window_start: date) -> tuple[float, float, float, float]:
    available = issue - timedelta(days=OBSERVATION_LAG_DAYS)
    before = daily.loc[pd.Timestamp(window_start):pd.Timestamp(available), "Extent"]
    if (len(before) < 8 or before.index.max().date() != available
        or len(before) != (available - window_start).days + 1):
        raise ValueError("Satellite snapshot stale for assumed availability cutoff")
    return float(before.min()), float(before.iloc[-1]), float(before.iloc[-1] - before.iloc[-8]), float("nan")


def backtest(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    predictions = []
    for year in range(2008, min(2026, int(daily.index.max().year))):
        for month, day in ISSUE_DATES:
            issue = date(year, month, day)
            try:
                observed, current, slope, final = season(daily, year, issue, date(year, 8, 1))
                rows = historical_rows(daily, year, month, day)
                samples = analog_drops(rows, observed, current, slope)
            except ValueError:
                continue
            unconditional = np.array([r["remaining_drop"] for r in rows])
            row = {"year": year, "issue": issue.isoformat(), "final_min": final,
                   "observed_min": observed, "current": current, "slope_7d": slope,
                   "analog_forecast": observed - samples.mean(),
                   "all_years_forecast": observed - unconditional.mean(),
                   "persistence_forecast": observed,
                   "n_training": len(rows)}
            for threshold in (4.4, 4.5, 4.6, 4.8, 5.0):
                key = str(threshold).replace('.', '_')
                row[f"label_below_{key}"] = int(final < threshold)
                row[f"analog_below_{key}"] = probability(samples, observed, None, threshold)
                row[f"all_years_below_{key}"] = probability(unconditional, observed, None, threshold)
            actual_bin = [i for i, (low, high) in enumerate(POLY_BINS)
                          if (low is None or final >= low) and (high is None or final < high)]
            if len(actual_bin) != 1:
                raise ValueError("Historical final minimum missed Polymarket bins")
            row["actual_bin"] = actual_bin[0]
            for model_name, drops in (("analog", samples), ("all_years", unconditional)):
                for i, p in enumerate(categorical_probabilities(drops, observed, POLY_BINS)):
                    row[f"{model_name}_bin_{i}"] = p
            predictions.append(row)
    panel = pd.DataFrame(predictions)
    if panel.empty:
        raise ValueError("No held-out predictions")
    metrics = {}
    for date_tuple, group in panel.groupby(panel.issue.str[5:]):
        metrics[date_tuple] = {"years": len(group)}
        for name in ("analog", "all_years", "persistence"):
            metrics[date_tuple][f"{name}_rmse"] = float(np.sqrt(np.mean((group[f"{name}_forecast"] - group.final_min) ** 2)))
        for name in ("analog", "all_years"):
            labels = np.concatenate([group[f"label_below_{str(t).replace('.', '_')}"] for t in (4.4, 4.5, 4.6, 4.8, 5.0)])
            probs = np.concatenate([group[f"{name}_below_{str(t).replace('.', '_')}"] for t in (4.4, 4.5, 4.6, 4.8, 5.0)])
            metrics[date_tuple][f"{name}_brier"] = float(np.mean((labels - probs) ** 2))
            bins = group[[f"{name}_bin_{i}" for i in range(len(POLY_BINS))]].to_numpy()
            truth = np.eye(len(POLY_BINS))[group.actual_bin.to_numpy(dtype=int)]
            metrics[date_tuple][f"{name}_poly_multiclass_brier"] = float(np.mean(np.sum((bins - truth) ** 2, axis=1)))
    return panel, {"status": "current_vintage_historical_hindcast", "metrics_by_issue": metrics,
                   "historical_market_return_verified": False, "original_release_verified": False,
                   "prospective_calibration_verified": False}


def _best_ask(book: dict) -> tuple[float | None, float]:
    asks = [(float(q["price"]), float(q["size"])) for q in book.get("asks", []) if float(q["size"]) > 0]
    if not asks:
        return None, 0.0
    price, size = min(asks)
    return price, size


def _contract(platform: str, market_id: str, label: str, p: float, yes_ask: float | None,
              yes_size: float, no_ask: float | None, no_size: float) -> list[dict]:
    result = []
    for side, probability_yes, ask, size in (("YES", p, yes_ask, yes_size),
                                              ("NO", 1 - p, no_ask, no_size)):
        edge = None if ask is None else probability_yes - ask - FEE_RESERVE
        result.append({"platform": platform, "market_id": market_id, "outcome": label,
                       "side": side, "model_probability": round(probability_yes, 6),
                       "ask": ask, "ask_size": size, "reserved_cost_per_contract": FEE_RESERVE,
                       "net_edge_per_contract": None if edge is None else round(edge, 6),
                       "eligible": bool(ask is not None and 0 < ask < 1 and size >= MAX_CONTRACTS
                                        and edge is not None and edge >= EDGE_BUFFER)})
    return result


def _parse_poly_bin(question: str) -> tuple[float | None, float | None]:
    q = question.lower()
    numbers = [float(v) for v in re.findall(r"\d+(?:\.\d+)?(?=m\b)", q)]
    if "less than" in q and len(numbers) == 1:
        return None, numbers[0]
    if "at least" in q and len(numbers) == 1:
        return numbers[0], None
    if "between" in q and len(numbers) == 2:
        return numbers[0], numbers[1]
    raise ValueError(f"Unrecognized Polymarket bin: {question}")


def screen(out: Path = OUT, issue: date | None = None) -> dict:
    manifest = json.loads((out / "source_manifest.json").read_text())
    raw = out / "raw"
    for name, item in manifest.items():
        if hashlib.sha256((raw / name).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Snapshot hash mismatch: {name}")
    daily = load_daily(raw / "nsidc_daily.csv")
    now = datetime.now(timezone.utc)
    issue = issue or now.date()
    if issue.year != 2026:
        raise ValueError("Bundled contract snapshot is for 2026 only")
    # A stale order book is never tradeable. Re-fetch before every new paper screen.
    oldest = min(datetime.fromisoformat(v["retrieved_at_utc"]) for v in manifest.values())
    latest = max(datetime.fromisoformat(v["retrieved_at_utc"]) for v in manifest.values())
    if (latest - oldest).total_seconds() > 300:
        raise ValueError("Snapshot collection exceeded five minutes")
    ice = season_live(daily, issue, date(2026, 8, 1))
    rows = historical_rows(daily, 2026, issue.month, issue.day)
    analog_samples = analog_drops(rows, ice[0], ice[1], ice[2])
    # The simpler all-years distribution has better held-out threshold Brier
    # scores on three of four issue dates; the analog fit is only diagnostic.
    samples = np.array([r["remaining_drop"] for r in rows])
    estimates = {"issue_date": issue.isoformat(), "last_assumed_available":
                 (issue - timedelta(days=OBSERVATION_LAG_DAYS)).isoformat(),
                 "observed_min": ice[0], "current_extent": ice[1], "slope_7d": ice[2],
                 "n_training_years": len(rows), "pricing_model": "all_prior_years_remaining_drop",
                 "analog_diagnostic_years": len(analog_samples),
                 "predicted_final_min": float(ice[0] - samples.mean()),
                 "historical_remaining_drops": samples.tolist(),
                 "analog_diagnostic_predicted_final_min": float(ice[0] - analog_samples.mean())}
    contracts = []
    kalshi = json.loads((raw / "kalshi_event.json").read_text())["event"]
    kalshi_history = daily.loc[pd.Timestamp("2025-12-19"):pd.Timestamp(issue - timedelta(days=OBSERVATION_LAG_DAYS)), "Extent"]
    if len(kalshi_history) != (issue - timedelta(days=OBSERVATION_LAG_DAYS) - date(2025, 12, 19)).days + 1:
        raise ValueError("Missing observations in Kalshi settlement window")
    kalshi_observed = float(kalshi_history.min())
    estimates["kalshi_observed_min_since_2025_12_19"] = kalshi_observed
    for market in kalshi["markets"]:
        rule = market["rules_primary"] + " " + market["rules_secondary"]
        if ("December 19, 2025" not in rule or "October 01, 2026" not in rule
            or "Sea Ice Index Version 4 Northern Hemisphere daily CSV" not in rule
            or "below" not in rule or market["status"] != "active"):
            raise ValueError(f"Kalshi rule/status changed: {market['ticker']}")
        threshold = float(market["cap_strike"])
        if kalshi_observed < threshold:
            p = 1.0
        else:
            future_minima = np.minimum(kalshi_observed, ice[0] - samples)
            p = float(((future_minima < threshold).sum() + 0.5) / (len(samples) + 1))
        contracts += _contract("Kalshi", market["ticker"], f"min < {threshold}", p,
                               float(market["yes_ask_dollars"]), float(market["yes_ask_size_fp"]),
                               float(market["no_ask_dollars"]), float(market.get("no_ask_size_fp", market["yes_bid_size_fp"])))
    poly = json.loads((raw / "polymarket_event.json").read_text())[0]
    if ("August 1, 2026 and October 1, 2026" not in poly["description"]
        or "NH-Daily-Extent" not in poly["description"] or not poly["active"] or poly["closed"]):
        raise ValueError("Polymarket rule/status changed")
    bins = [_parse_poly_bin(m["question"]) for m in poly["markets"]]
    if bins != POLY_BINS:
        raise ValueError("Unexpected Polymarket bin boundaries")
    bin_probabilities = categorical_probabilities(samples, ice[0], bins)
    for market, p in zip(poly["markets"], bin_probabilities):
        yes_ask, yes_size = _best_ask(json.loads((raw / f"poly_{market['id']}_yes_book.json").read_text()))
        no_ask, no_size = _best_ask(json.loads((raw / f"poly_{market['id']}_no_book.json").read_text()))
        contracts += _contract("Polymarket", market["id"], market["question"], p,
                               yes_ask, yes_size, no_ask, no_size)
    snapshot_age_seconds = (now - latest).total_seconds()
    stale = snapshot_age_seconds > 300 or snapshot_age_seconds < -60
    candidates = sorted((c for c in contracts if c["eligible"] and not stale),
                        key=lambda c: c["net_edge_per_contract"], reverse=True)
    # This is an exploratory screen, never an instruction to execute: the 2026
    # analog model has not been prospectively calibrated, and quotes expire.
    return {"model": estimates, "snapshot_retrieved_first_utc": oldest.isoformat(),
            "snapshot_retrieved_last_utc": latest.isoformat(), "snapshot_age_seconds": snapshot_age_seconds,
            "snapshot_expired": stale, "polymarket_bin_probability_sum": sum(bin_probabilities), "cost_policy":
            {"reserved_cost_per_contract": FEE_RESERVE, "minimum_edge": EDGE_BUFFER,
             "min_top_of_book_size": MAX_CONTRACTS, "max_contracts": MAX_CONTRACTS},
            "contracts": contracts, "paper_candidates": candidates,
            "decision": "NO_LIVE_TRADE_UNVERIFIED_MODEL",
            "note": "Paper candidates are price screens, not verified alpha or executable current orders. Daily history is current vintage; historical original releases and historical order books are absent."}


def run(out: Path = OUT, refresh: bool = False, issue: date | None = None) -> dict:
    if refresh:
        fetch(out)
    daily = load_daily(out / "raw/nsidc_daily.csv")
    workbook_check = verify_poly_workbook(daily, out / "raw/nsidc_daily_workbook.xlsx")
    panel, metrics = backtest(daily)
    metrics["polymarket_workbook_check"] = workbook_check
    panel.to_csv(out / "hindcasts.csv", index=False)
    (out / "backtest.json").write_text(json.dumps(metrics, indent=2) + "\n")
    card = screen(out, issue)
    (out / "paper_screen.json").write_text(json.dumps(card, indent=2) + "\n")
    return {"backtest": metrics, "paper_screen": card}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--refresh", action="store_true", help="GET new NSIDC and market snapshots")
    parser.add_argument("--issue", type=date.fromisoformat)
    args = parser.parse_args()
    result = run(args.out, args.refresh, args.issue)
    print(json.dumps({"backtest": result["backtest"], "model": result["paper_screen"]["model"],
                      "candidates": result["paper_screen"]["paper_candidates"],
                      "decision": result["paper_screen"]["decision"]}, indent=2))
