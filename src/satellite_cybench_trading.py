"""Fixed, costed CORN ETF diagnostic; forecast value and market value are separate.

Only prior-year reported harvested area determines county aggregation weights.
No current yield, current area, or future price enters signal construction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .satellite_hurricane_trading import load_prices
except ImportError:
    from satellite_hurricane_trading import load_prices

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/cybench_trading"
YEARS = tuple(range(2013, 2024))
FORECASTS = ("weather", "satellite", "trend")
STRATEGIES = ("satellite", "weather", "incremental_overlay", "cash", "always_long", "always_short")
CONTROLS = ("weather", "cash", "always_long", "always_short")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def aggregate_forecasts(predictions, statistics, county_ids, years=YEARS, issue_month=8):
    """Keep explicit abstentions and coverage against the fixed prior-reporting universe."""
    if issue_month not in (8, 9):
        raise ValueError("Only the two frozen horizons are permitted")
    predictions, statistics = predictions.copy(), statistics.copy()
    predictions["adm_id"] = predictions.adm_id.astype(str)
    statistics["adm_id"] = statistics.adm_id.astype(str)
    if predictions.duplicated(["adm_id", "year"]).any():
        raise ValueError("Duplicate county forecast")
    if statistics.duplicated(["adm_id", "harvest_year"]).any():
        raise ValueError("Duplicate county/year statistics")
    if not predictions.eligible.isin([True, False]).all():
        raise ValueError("Forecast eligibility must be boolean")
    county_ids = set(map(str, county_ids))
    statistics = statistics[statistics.adm_id.isin(county_ids)].copy()
    statistics["available_at"] = pd.to_datetime((statistics.harvest_year + 1).astype(str) + "-06-30")
    predictions["forecast_at"] = pd.to_datetime(predictions.forecast_at, utc=True).dt.tz_localize(None)
    rows, weights = [], []
    for year in years:
        issue = pd.Timestamp(year=year, month=issue_month, day=15, hour=12)
        past = statistics[(statistics.harvest_year < year) & (statistics.available_at < issue)]
        valid_labels = past[np.isfinite(past["yield"]) & past["yield"].gt(0)]
        count = valid_labels.groupby("adm_id").harvest_year.nunique()
        universe = set(count[count >= 8].index)
        previous = past[past.harvest_year.eq(year - 1)].set_index("adm_id").reindex(sorted(universe))
        area = previous.harvest_area.where(np.isfinite(previous.harvest_area) & previous.harvest_area.gt(0))
        denominator = float(area.sum())
        current = predictions[predictions.year.eq(year) & predictions.adm_id.isin(universe)].set_index("adm_id")
        if not current.forecast_at.eq(issue).all():
            raise ValueError("Unexpected county forecast issue")
        available = current.eligible & np.isfinite(current[list(FORECASTS)]).all(axis=1)
        support = current.loc[available].join(area.rename("prior_area_ha"), how="inner")
        support = support[np.isfinite(support.prior_area_ha) & support.prior_area_ha.gt(0)]
        numerator = float(support.prior_area_ha.sum())
        coverage = numerator / denominator if denominator > 0 else np.nan
        row = {"year": year, "forecast_at": issue, "eligible": False, "reason": "",
               "history_counties": len(universe), "prior_area_counties": int(area.notna().sum()),
               "missing_prior_area_counties": int(area.isna().sum()), "forecast_weighted_counties": len(support),
               "total_reported_prior_area_ha": denominator, "forecast_prior_area_ha": numerator,
               "prior_area_coverage": coverage, "weight_harvest_year": year - 1,
               **{name: np.nan for name in FORECASTS}}
        if denominator <= 0:
            row["reason"] = "No positive reported prior-year area in prior-reporting universe"
        elif coverage < .8:
            row["reason"] = "Forecasts cover less than80percent of reported prior area"
        else:
            row["eligible"] = True
            for name in FORECASTS:
                row[name] = float(np.average(support[name], weights=support.prior_area_ha))
        for county, item in support.iterrows():
            weights.append({"year": year, "adm_id": county, "weight_harvest_year": year - 1,
                            "prior_area_ha": float(item.prior_area_ha),
                            "normalized_weight": float(item.prior_area_ha / numerator),
                            "event_eligible": row["eligible"]})
        rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(weights, columns=["year", "adm_id", "weight_harvest_year", "prior_area_ha", "normalized_weight", "event_eligible"])


def event_return(direction, asset_return, days, cost_multiplier=1.):
    if direction not in (-1, 0, 1) or days <= 0 or not np.isfinite(asset_return):
        raise ValueError("Invalid position, return or holding period")
    if not np.isfinite(cost_multiplier) or cost_multiplier <= 0:
        raise ValueError("Invalid execution cost multiplier")
    execution = .005 * abs(direction) * cost_multiplier
    borrow = .03 * days / 365 if direction < 0 else 0.
    gross = direction * asset_return
    net = gross - execution - borrow
    if net <= -1:
        raise ValueError("Bankruptcy; event compounding is invalid")
    return {"direction": direction, "gross_return": gross, "execution_cost": execution,
            "borrow_cost": borrow, "net_return": net}


def make_events(forecasts, prices, sessions, cost_multiplier=1., issue_month=8):
    if issue_month not in (8, 9):
        raise ValueError("Only the two frozen horizons are permitted")
    if forecasts.year.duplicated().any():
        raise ValueError("Duplicate aggregate forecast year")
    if sessions.duplicated().any() or not sessions.is_monotonic_increasing:
        raise ValueError("Invalid exchange sessions")
    if prices.index.duplicated().any() or not prices.index.is_monotonic_increasing:
        raise ValueError("Invalid price date index")
    rows, skipped = [], []
    for item in forecasts.sort_values("year").to_dict("records"):
        year = int(item["year"])
        issue = pd.Timestamp(item["forecast_at"])
        if issue != pd.Timestamp(year=year, month=issue_month, day=15, hour=12):
            raise ValueError("Unexpected aggregate issue time")
        if not item["eligible"]:
            skipped.append({"year": year, "reason": item["reason"]})
            continue
        if not all(np.isfinite(item[name]) for name in FORECASTS):
            raise ValueError("Eligible aggregate has missing forecasts")
        expected = sessions[(sessions > issue.normalize()) & (sessions <= pd.Timestamp(year, 10, 31))]
        if len(expected) == 0 or (expected[0] - issue.normalize()).days > 4 or (pd.Timestamp(year, 10, 31) - expected[-1]).days > 4:
            skipped.append({"year": year, "reason": "Incomplete exchange calendar window"})
            continue
        window = prices.reindex(expected)
        if window.isna().any() or not np.isfinite(window).all() or (window <= 0).any():
            skipped.append({"year": year, "reason": "Missing or invalid expected-session adjusted close"})
            continue
        entry, finish = expected[0], expected[-1]
        days = int((finish - entry).days)
        asset_return = float(window.iloc[-1] / window.iloc[0] - 1)
        directions = {name: -int(np.sign(item[name] - item["trend"])) for name in ("satellite", "weather")}
        directions.update(incremental_overlay=-int(np.sign(item["satellite"] - item["weather"])),
                          cash=0, always_long=1, always_short=-1)
        for name in STRATEGIES:
            rows.append({"year": year, "forecast_at": issue, "entry": entry, "exit": finish,
                         "holding_calendar_days": days, "strategy": name,
                         "entry_adjusted_close": float(window.iloc[0]), "exit_adjusted_close": float(window.iloc[-1]),
                         "asset_return": asset_return, "prior_area_coverage": item["prior_area_coverage"],
                         **event_return(directions[name], asset_return, days, cost_multiplier)})
    return pd.DataFrame(rows), pd.DataFrame(skipped, columns=["year", "reason"])


def block_samples(wide, draws=10000, years=YEARS):
    """Resample shared calendar-year blocks, retaining any abstention gaps."""
    values = wide.reindex(years).to_numpy(float)
    n = len(years)
    starts = np.random.default_rng(20260927).integers(0, n, size=(draws, (n + 4) // 5))
    ids = ((starts[:, :, None] + np.arange(5)) % n).reshape(draws, -1)[:, :n]
    sample = values[ids]
    counts = np.isfinite(sample).sum(axis=1)
    return np.divide(np.nansum(sample, axis=1), counts, out=np.full(counts.shape, np.nan), where=counts > 0)


def summarize(events):
    if events.empty:
        return {"n_events": 0, "economic_diagnostic_gate_passed": False, "trading_alpha_verified": False,
                "original_vintage_operational_verification": False, "strategies": {}, "status": "All events abstained"}
    if events.duplicated(["year", "strategy"]).any():
        raise ValueError("Duplicate strategy event")
    wide = events.pivot(index="year", columns="strategy", values="net_return").reindex(columns=STRATEGIES).sort_index()
    if wide.isna().any().any():
        raise ValueError("Strategies must share event support")
    boot = block_samples(wide)
    scores = {}
    for index, name in enumerate(STRATEGIES):
        selected = events[events.strategy.eq(name)]
        values = wide[name].to_numpy()
        samples = boot[:, index]
        samples = samples[np.isfinite(samples)]
        comparisons = {}
        for control in CONTROLS:
            ci = STRATEGIES.index(control)
            diff = boot[:, index] - boot[:, ci]
            diff = diff[np.isfinite(diff)]
            comparisons[control] = {"mean_net_advantage": float((wide[name] - wide[control]).mean()),
                                    "mean_net_advantage_ci95": np.quantile(diff, [.025, .975]).tolist(),
                                    "compound_event_return_difference": float(np.prod(1 + values) - np.prod(1 + wide[control].to_numpy()))}
        scores[name] = {"n_events": len(values), "mean_net_event_return": float(values.mean()),
                        "compound_net_event_return": float(np.prod(1 + values) - 1),
                        "mean_net_event_return_ci95": np.quantile(samples, [.025, .975]).tolist(),
                        "long_events": int(selected.direction.eq(1).sum()), "short_events": int(selected.direction.eq(-1).sum()),
                        "cash_events": int(selected.direction.eq(0).sum()), "comparisons": comparisons}
    sat = scores["satellite"]
    gate = sat["mean_net_event_return_ci95"][0] > 0 and sat["comparisons"]["weather"]["mean_net_advantage_ci95"][0] > 0
    return {"n_events": len(wide), "strategies": scores, "economic_diagnostic_gate_passed": bool(gate),
            "trading_alpha_verified": False, "original_vintage_operational_verification": False,
            "cross_candidate_multiple_testing_adjusted": False,
            "interpretation": "Fixed exploratory CORN futures-ETF event returns using current-vintage county forecasts; maximum11years, not risk-adjusted alpha or historical operational verification"}


def verify_inputs(out):
    manifest = json.loads((out / "source_manifest.json").read_text())
    entries = [manifest[key] for key in ("prices", "county_stats", "locations", "calendar")]
    entries.extend(manifest["metadata_sources"])
    for item in entries:
        if digest(out / item["path"]) != item["sha256"]:
            raise ValueError("Changed trading input: " + item["path"])
    return manifest


def run(out=DEFAULT, predictions_path=None, issue_month=8):
    protocol = json.loads((out / "protocol.json").read_text())
    if issue_month != protocol.get("horizon_month", 8):
        raise ValueError("Output directory protocol and requested horizon differ")
    forecast_folder = "cybench_maize" if issue_month == 8 else "cybench_maize_late"
    predictions_path = predictions_path or ROOT / "results/satellite_validation" / forecast_folder / "predictions.csv.gz"
    manifest = verify_inputs(out)
    predictions = pd.read_csv(predictions_path, dtype={"adm_id": str},
                              usecols=["adm_id", "year", "eligible", "forecast_at", *FORECASTS])
    statistics = pd.read_csv(out / manifest["county_stats"]["path"], dtype={"adm_id": str})
    locations = pd.read_csv(out / manifest["locations"]["path"], dtype={"adm_id": str})
    forecasts, weights = aggregate_forecasts(predictions, statistics, locations.adm_id, issue_month=issue_month)
    prices = load_prices(out / manifest["prices"]["path"], "CORN")
    sessions = pd.DatetimeIndex(pd.read_csv(out / manifest["calendar"]["path"], parse_dates=["date"]).date)
    events, skipped = make_events(forecasts, prices, sessions, issue_month=issue_month)
    doubled, doubled_skipped = make_events(forecasts, prices, sessions, cost_multiplier=2., issue_month=issue_month)
    if not skipped.equals(doubled_skipped):
        raise ValueError("Cost sensitivity changed event support")
    summary = summarize(events)
    summary["double_execution_cost_sensitivity"] = summarize(doubled)
    summary.update(issue_month=issue_month, protocol_sha256=digest(out / "protocol.json"), code_sha256=digest(__file__),
                   source_manifest_sha256=digest(out / "source_manifest.json"), predictions_sha256=digest(predictions_path),
                   skipped_events=skipped.to_dict("records"),
                   aggregation_coverage=forecasts.drop(columns=list(FORECASTS)).replace({np.nan: None}).to_dict("records"))
    for name, frame in [("aggregate_forecasts.csv", forecasts), ("county_weights.csv.gz", weights),
                        ("events.csv", events), ("events_double_cost.csv", doubled), ("skipped.csv", skipped)]:
        frame.to_csv(out / name, index=False, float_format="%.12g",
                     compression={"method": "gzip", "mtime": 0} if name.endswith(".gz") else None)
    prices.to_csv(out / "adjusted_prices.csv", index_label="date", float_format="%.12g")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str, allow_nan=False) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--issue-month", type=int, choices=[8, 9], default=8)
    args = parser.parse_args()
    result = run(args.output_dir, args.predictions, args.issue_month)
    print(json.dumps({"n_events": result["n_events"], "economic_diagnostic_gate_passed": result["economic_diagnostic_gate_passed"],
                      "strategies": result["strategies"]}, indent=2))
