#!/usr/bin/env python3
"""Audit/replay the committed construction composites without claiming PIT alpha.

The legacy CSVs identify only the endpoint scene's created timestamp. They omit
complete scene lineage, including some initial baseline scenes. The dependency
guard below is therefore a lower bound on true feature availability, not a
certificate that a composite could have been built historically. Recomputing
pixels from an immutable, publication-filtered scene manifest is the remedy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Rule:
    stall_days: int = 120
    min_step_ha: float = 0.3
    relative_step: float = 0.05
    under_way_ha: float = 1.0
    window_days: int = 75
    season_days: int = 45
    min_recent_observations: int = 3
    max_scene_age_days: int = 30
    processing_lag_days: int = 1


def _utc(value):
    return pd.to_datetime(value, utc=True)


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """Fail closed on missing timestamps, invalid measurements, or duplicates."""
    frame = frame.copy()
    required = {"date", "created", "in_baseline", "new_built_ha", "window_scenes"}
    if missing := required - set(frame.columns):
        raise ValueError(f"Missing columns: {sorted(missing)}")
    frame["date"] = _utc(frame["date"])
    frame["created"] = _utc(frame["created"])
    frame["new_built_ha"] = pd.to_numeric(frame["new_built_ha"], errors="raise")
    frame["window_scenes"] = pd.to_numeric(frame["window_scenes"], errors="raise")
    if frame[["date", "created", "new_built_ha", "window_scenes"]].isna().any().any():
        raise ValueError("Missing scene timestamp or measurement")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate acquisition dates require explicit version lineage")
    if (frame["created"] < frame["date"]).any():
        raise ValueError("Scene creation precedes acquisition")
    if not np.isfinite(frame["new_built_ha"]).all() or (frame["new_built_ha"] < 0).any():
        raise ValueError("Invalid built-area measurement")
    mapping = {"true": True, "false": False}
    flags = frame["in_baseline"].astype(str).str.lower().map(mapping)
    if flags.isna().any():
        raise ValueError("in_baseline must be boolean")
    frame["in_baseline"] = flags
    return frame.sort_values("date").reset_index(drop=True)


def availability_audit(frame: pd.DataFrame, public_since: str, rule: Rule = Rule()) -> pd.DataFrame:
    """Track known input dependencies under the legacy 75d/seasonal algorithm.

    The same day-of-year distance as the inherited algorithm is deliberately
    reproduced. Baseline-year outputs are never used for alerts. Every output
    identifies whether a known constituent was published after its endpoint.
    """
    frame = normalize(frame)
    date, created = frame["date"], frame["created"]
    doy = date.dt.dayofyear.to_numpy()
    rows = []
    public_at = _utc(public_since) + pd.Timedelta(days=1)
    lag = pd.Timedelta(days=rule.processing_lag_days)
    for i, row in frame.iterrows():
        distance = np.abs(doy - doy[i])
        distance = np.minimum(distance, 365 - distance)
        baseline = frame["in_baseline"] & (distance <= rule.season_days)
        window = (date > row["date"] - pd.Timedelta(days=rule.window_days)) & (date <= row["date"])
        dependency = baseline | window
        last_created = created[dependency].max()
        rows.append({
            **row.to_dict(),
            "known_dependency_created": last_created,
            "endpoint_available_at": max(row["created"] + lag, public_at),
            "dependency_available_at_lower_bound": max(last_created + lag, public_at),
            "known_dependency_count": int(dependency.sum()),
            "known_future_acquisition_inputs": int((baseline & (date > row["date"])).sum()),
            "known_later_publication_inputs": int((dependency & (created > row["created"])).sum()),
            "publication_lag_days": (row["created"] - row["date"]).total_seconds() / 86400,
            "strict_point_in_time_certified": False,
        })
    return pd.DataFrame(rows)


def _record_state(known: pd.DataFrame, rule: Rule):
    """Reconstruct only the history known at this decision; never revise alerts."""
    underway = known[known["new_built_ha"] >= rule.under_way_ha]
    if underway.empty:
        return None
    start = underway["date"].iloc[0]
    series = known[known["date"] >= start]
    record_date, record = start, float(series["new_built_ha"].iloc[0])
    for row in series.itertuples():
        if row.new_built_ha >= record + max(rule.min_step_ha, rule.relative_step * record):
            record_date, record = row.date, float(row.new_built_ha)
    return record_date, record


def replay(audited: pd.DataFrame, public_since: str, mode: str = "dependency_guard",
           rule: Rule = Rule(), as_of: str = "2026-09-26") -> tuple[pd.DataFrame, pd.DataFrame]:
    """One alert per no-progress episode, including still-open episodes.

    Decisions happen when new data becomes available (plus processing lag).
    The latest acquisition must be fresh and at least three observations must
    be known from the recent window. Publication of an old reprocessed scene
    cannot create a backdated alert. The endpoint mode is a diagnostic only.
    """
    if mode not in {"endpoint_only", "dependency_guard"}:
        raise ValueError(f"Unknown mode: {mode}")
    column = "endpoint_available_at" if mode == "endpoint_only" else "dependency_available_at_lower_bound"
    data = audited.loc[~audited["in_baseline"]].copy()
    data["available_at"] = data[column]
    end = _utc(as_of) + pd.Timedelta(days=1)
    public_at = _utc(public_since) + pd.Timedelta(days=1)
    decisions, alerts, alerted = [], [], set()
    for now in sorted(data.loc[(data["available_at"] < end) & (data["available_at"] >= public_at), "available_at"].unique()):
        now = pd.Timestamp(now)
        known = data[data["available_at"] <= now].sort_values("date")
        latest = known.iloc[-1]
        recent = known[known["date"] > now - pd.Timedelta(days=rule.window_days)]
        age = (now - latest["date"]).total_seconds() / 86400
        state = _record_state(known, rule)
        valid = age <= rule.max_scene_age_days and len(recent) >= rule.min_recent_observations
        record_date, record = state if state else (pd.NaT, float("nan"))
        no_progress_days = (latest["date"] - record_date).days if state else 0
        warning = bool(valid and state and no_progress_days >= rule.stall_days)
        record_key = (record_date, record) if state else None
        item = {
            "mode": mode, "decision_at": now, "latest_acquired": latest["date"],
            "latest_age_days": round(age, 4), "recent_observations": len(recent),
            "record_date": record_date, "record_ha": record,
            "no_progress_days": no_progress_days, "coverage_eligible": valid,
            "warning": warning, "strict_point_in_time_certified": False,
        }
        decisions.append(item)
        if warning and record_key not in alerted:
            alerts.append(item)
            alerted.add(record_key)
    columns = ["mode", "decision_at", "latest_acquired", "latest_age_days", "recent_observations",
               "record_date", "record_ha", "no_progress_days", "coverage_eligible", "warning",
               "strict_point_in_time_certified"]
    return pd.DataFrame(decisions, columns=columns), pd.DataFrame(alerts, columns=columns)


def _hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(config_path: Path, data_dir: Path, output_dir: Path, as_of: str, rule: Rule = Rule()) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text())
    rows, all_alerts, audits, sensitivities, inputs = [], [], [], [], []
    for site in config["sites"]:
        if site["kind"] != "data_center":
            continue
        path = data_dir / f"construction_{site['id']}.csv"
        inputs.append({"path": str(path), "sha256": _hash(path)})
        raw = pd.read_csv(path)
        audited = availability_audit(raw, site["public_since"], rule)
        audited.insert(0, "site_id", site["id"])
        audits.append(audited)
        post = audited[~audited["in_baseline"]]
        base = {
            "site_id": site["id"], "independent_campus": site["id"] != "corz_denton_blind",
            "public_since": site["public_since"], "aoi_drawn": site["aoi_drawn"],
            "rows": len(raw), "post_baseline_rows": len(post),
            "max_acquisition_gap_days": int(audited["date"].diff().dt.days.max()),
            "endpoint_lag_over_30d_rows": int((audited["publication_lag_days"] > 30).sum()),
            "post_baseline_known_publication_leak_rows": int((post["known_later_publication_inputs"] > 0).sum()),
            "max_endpoint_publication_lag_days": round(float(audited["publication_lag_days"].max()), 2),
            "config_delay_disclosed": site.get("delivery", {}).get("delay_disclosed"),
        }
        for mode in ("endpoint_only", "dependency_guard"):
            decisions, alerts = replay(audited, site["public_since"], mode, rule, as_of)
            decisions.insert(0, "site_id", site["id"])
            decisions.to_csv(output_dir / f"replay_{site['id']}_{mode}.csv", index=False)
            alerts.insert(0, "site_id", site["id"])
            all_alerts.append(alerts)
            before_cut = alerts[alerts["decision_at"] < _utc("2025-11-10")]
            before_oct = alerts[alerts["decision_at"] < _utc("2025-10-24")]
            before_rfs = alerts[alerts["decision_at"] < _utc("2025-10-27")]
            rows.append({**base, "mode": mode, "alert_episodes": len(alerts),
                         "alerts_before_nov10_2025": len(before_cut),
                         "alerts_before_oct24_2025_public_delay": len(before_oct),
                         "alerts_before_oct27_2025_apld_rfs": len(before_rfs),
                         "first_alert_at": str(alerts["decision_at"].min()) if len(alerts) else None,
                         "coverage_eligible_decisions": int(decisions["coverage_eligible"].sum()),
                         "total_decisions": len(decisions)})
        for days in (90, 120, 150, 180):
            variant = Rule(**{**asdict(rule), "stall_days": days})
            _, alerts = replay(audited, site["public_since"], "dependency_guard", variant, as_of)
            sensitivities.append({"site_id": site["id"], "stall_days": days,
                                  "alert_episodes": len(alerts),
                                  "before_nov10_2025": int((alerts["decision_at"] < _utc("2025-11-10")).sum())})
    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "site_summary.csv", index=False)
    nonempty_alerts = [alerts for alerts in all_alerts if not alerts.empty]
    combined_alerts = pd.concat(nonempty_alerts, ignore_index=True) if nonempty_alerts else all_alerts[0]
    combined_alerts.to_csv(output_dir / "alert_episodes.csv", index=False)
    pd.concat(audits, ignore_index=True).to_csv(output_dir / "input_timestamp_audit.csv", index=False)
    pd.DataFrame(sensitivities).to_csv(output_dir / "threshold_sensitivity.csv", index=False)
    report = {
        "as_of": as_of, "rule": asdict(rule), "verdict": "construction_stall_alpha_not_validated",
        "strict_point_in_time_certified": False,
        "independent_campuses": int(summary["site_id"].nunique() - 1),
        "timing_scope": "causal replay of supplied composite values; incomplete scene lineage prevents PIT certification",
        "dependency_guard": "availability lower bound from exported endpoint scenes in baseline and trailing window",
        "event_labels": "issuer reports verified separately; other no-delay-found labels remain unknown outcomes",
        "inputs": [{"path": str(config_path), "sha256": _hash(config_path)}, *inputs],
        "limitations": [
            "AOIs and site universe selected retrospectively; blind Denton box is a sensitivity check, not an independent campus",
            "Legacy composites cannot be recomputed from rounded CSVs; not all constituent scenes were exported",
            "Created is the current asset version publication time; earlier original versions may have existed but are not evidenced",
            "No schedule-vintage ledger or complete building-level outcomes, so false-positive rate cannot be reliably estimated",
            "No investment return or incremental predictive benefit is established by this audit",
        ],
        "sites": summary.where(pd.notna(summary), None).to_dict("records"),
    }
    (output_dir / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/satellite_sites.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("results/satellite_sites"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/satellite_validation/construction"))
    parser.add_argument("--as-of", default="2026-09-26")
    args = parser.parse_args()
    result = run(args.config, args.data_dir, args.output_dir, args.as_of)
    print(json.dumps({"verdict": result["verdict"], "independent_campuses": result["independent_campuses"],
                      "strict_point_in_time_certified": False, "output_dir": str(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
