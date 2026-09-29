#!/usr/bin/env python3
"""Build the satellite continuation's evidence summary from checked-in results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/satellite_validation"


def read(name):
    return json.loads((OUT / name / "summary.json").read_text())


def main():
    crop, heat, solar, building = [read(name) for name in ("third_signal", "smelters", "solar", "construction")]
    confirm = json.loads((OUT / "third_signal/confirmation_2025.json").read_text())
    cm = crop["primary_metrics"]
    refined = heat["production_validation"]["refined_copper_kt"]
    throughput = heat["production_validation"]["concentrates_smelted_kt"]
    sm = solar["metrics"]["models"]
    trade = read("trading") if (OUT / "trading/summary.json").exists() else None
    enso = read("enso") if (OUT / "enso/summary.json").exists() else None
    seaice = read("seaice") if (OUT / "seaice/summary.json").exists() else None
    gas = read("gas") if (OUT / "gas/summary.json").exists() else None
    gas_trade = read("gas_trading") if (OUT / "gas_trading/summary.json").exists() else None
    newer = {name: read(name) for name in ("goes_solar", "hydro", "hurricane", "pacific_hurricane", "snow_daily", "snow_summer", "snow_kings", "east_africa", "south_africa_maize", "southern_africa_maize_replication", "cybench_maize", "cybench_maize_late") if (OUT / name / "summary.json").exists()}
    labels = ["Smelter refined copper\nMAE, 8 quarters", "Smelter throughput\nMAE, 8 quarters",
              "Corn + MODIS vegetation\nRMSE, 7 years", "Wheat + MODIS, 2025\nRMSE, 1 year",
              "Solar generation + CERES*\nRMSE, 72 months"]
    gains = [refined["comparisons"]["persistence"]["mae_reduction_pct"],
             throughput["comparisons"]["persistence"]["mae_reduction_pct"],
             cm["improvement_vs_weather_pct"], confirm["improvement_vs_weather_pct"],
             sm["satellite"]["rmse_improvement_pct"]]
    if enso:
        labels.append("Ocean SST → winter rain\nRMSE, 29 winters")
        gains.append(100 * enso["comparisons"]["climatology"]["rmse_reduction_fraction"])
    if seaice:
        labels.append("July → September sea ice**\nRMSE, 26 years")
        gains.append(100 * seaice["comparisons"]["trend_prior"]["rmse_reduction_fraction"])
    if gas:
        labels.append("Gas demand + satellite temperature\nRMSE vs ground weather, 156 months")
        gains.append(100 * gas["comparisons"]["ground_hdd_satellite_vs_ground_hdd"]["rmse_reduction_fraction"])
    if "goes_solar" in newer:
        labels.append("Operational GOES solar\nRMSE vs weather, 42 months")
        gains.append(newer["goes_solar"]["comparisons"]["weather"]["rmse_reduction_pct"])
    if "hydro" in newer:
        labels.append("Altimetry + ground-storage hydro\nRMSE, 88 months")
        gains.append(100 * newer["hydro"]["comparisons"]["ground_storage_satellite_vs_ground_storage"]["rmse_reduction_fraction"])
    if "hurricane" in newer:
        labels.append("Atlantic storm energy + ocean SST\nRMSE vs recent climate, 27 years")
        gains.append(100 * newer["hurricane"]["comparisons"]["recent_climatology"]["rmse_reduction_fraction"])
    if "pacific_hurricane" in newer:
        labels.append("Pacific storm energy + ocean SST\nRMSE vs recent climate, 27 years")
        gains.append(100 * newer["pacific_hurricane"]["comparisons"]["recent_climatology"]["rmse_reduction_fraction"])
    if "snow_summer" in newer:
        labels.append("May snow → summer runoff\nRMSE vs fresh-flow controls, 14 years")
        gains.append(100 * newer["snow_summer"]["fresh_may_flow"]["comparisons"]["weather_flow"]["rmse_reduction"])
    if "east_africa" in newer:
        result = newer["east_africa"]
        labels.append("Ocean SST → East African short rains\nRMSE vs strongest baseline")
        gains.append(100 * result["comparisons"][result["strongest_baseline"]]["rmse_reduction_fraction"])
    if "snow_kings" in newer:
        labels.append("May snow → Kings River runoff\nRMSE vs fresh-flow controls, 14 years")
        gains.append(100 * newer["snow_kings"]["kings"]["fresh_may_flow"]["comparisons"]["weather_flow"]["rmse_reduction"])
    if "south_africa_maize" in newer:
        result = newer["south_africa_maize"]["primary_yield"]["completed_harvest_stress"]
        if result.get("n_test_years"):
            labels.append("September ocean SST → maize yield\nSouth Africa, strongest baseline")
            gains.append(100 * result["comparisons"][result["strongest_baseline"]]["rmse_reduction"])
    if "cybench_maize" in newer:
        result = newer["cybench_maize"]
        labels.append("Raw NDVI → US county maize yield\nRMSE vs strongest baseline")
        gains.append(100 * result["comparisons"][result["strongest_baseline"]]["rmse_reduction"])
    if "cybench_maize_late" in newer:
        result = newer["cybench_maize_late"]
        labels.append("Raw NDVI → county maize, September\nRMSE vs strongest baseline")
        gains.append(100 * result["comparisons"][result["strongest_baseline"]]["rmse_reduction"])
    vhp = {}
    for name in ("vhp_wheat", "vhp_wheat_panel"):
        path = ROOT / "results" / name / "summary.json"
        if path.exists():
            vhp[name] = json.loads(path.read_text())
    if "vhp_wheat" in vhp:
        labels.append("Crop-masked vegetation → Texas wheat\nRMSE vs weather, 23 years")
        gains.append(100 * vhp["vhp_wheat"]["primary_yield"]["comparisons"]["weather"]["rmse_reduction"])
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "satellite-validation"})
    fig, ax = plt.subplots(figsize=(11, max(7.5, len(gains) * .67 + 2)))
    bars = ax.barh(np.arange(len(gains)), gains, color=["#44729d" if x >= 0 else "#bf654f" for x in gains], height=.57)
    bars[4].set_hatch("///")
    ax.set_yticks(np.arange(len(gains)), labels)
    ax.invert_yaxis()
    ax.axvline(0, color="#777777", linewidth=.8)
    ax.set_xlim(min(gains) - 12, max(gains) + 12)
    for i, gain in enumerate(gains):
        ax.text(gain + (1 if gain >= 0 else -1), i, f"{gain:+.1f}%", ha="left" if gain >= 0 else "right", va="center")
    ax.set_xlabel("Reduction in error versus stated baseline (higher is better)")
    ax.set_title("Satellite evidence: measured improvements are not trading returns", loc="left", weight="bold", pad=16)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", alpha=.15)
    fig.text(.02, .025, "*Solar imagery arrives too late for the proposed nowcast. **Sea ice clears its matched-model test, but revisions remain\nand its advantage over the simpler trend model is uncertain. Different metrics; these are not returns or investability rankings.", fontsize=9, color="#555555")
    fig.tight_layout(rect=(0, .10, 1, 1))
    fig.savefig(OUT / "evidence.svg", metadata={"Date": None})
    svg = OUT / "evidence.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    fig.savefig(OUT / "evidence.png", dpi=160)
    plt.close(fig)
    results = {
        "evaluation_scope": "Exploratory satellite research; forecast usefulness and trading alpha evaluated separately",
        "implemented_candidates": ["construction_progress", "industrial_heat", "crop_vegetation", "solar_irradiance"],
        "forecast_verification": {"construction": False, "smelters": False,
                                  "crop_primary": crop["status"] == "forecast_utility_pass",
                                  "solar": solar["forecast_usefulness_verified"]},
        "trading_alpha_verified": False,
        "solar_retrospective_estimation_gain_pct": sm["satellite"]["rmse_improvement_pct"],
        "crop_primary_rmse_gain_pct": cm["improvement_vs_weather_pct"],
        "selected_wheat_confirmation_gain_pct": confirm["improvement_vs_weather_pct"],
        "construction_strict_pit_certified": building["strict_point_in_time_certified"],
    }
    if enso:
        results["additional_enso_evaluation"] = enso
        results["forecast_verification"]["enso"] = enso["predefined_forecast_gate_passed"]
        results["implemented_candidates"].append("satellite_ocean_temperature")
    if seaice:
        results["additional_seaice_evaluation"] = seaice
        results["seaice_matched_model_forecast_gate_passed"] = seaice["predefined_forecast_gate_passed"]
        results["forecast_verification"]["seaice_robust"] = bool(
            seaice["predefined_forecast_gate_passed"]
            and seaice["comparisons"]["trend"]["rmse_reduction_ci95_million_km2"][0] > 0)
        results["seaice_claim_scope"] = "Current-vintage physical forecast ablation; stronger trend-only uncertainty and historical revisions prevent a broader robust or economic utility claim"
        results["implemented_candidates"].append("passive_microwave_sea_ice")
    if gas:
        results["additional_gas_evaluation"] = gas
        results["forecast_verification"]["gas_incremental"] = gas["incremental_usefulness_gate_passed"]
        results["implemented_candidates"].append("satellite_atmospheric_temperature")
    for name, result in newer.items():
        results["additional_" + name + "_evaluation"] = result
        key = {"goes_solar": "forecast_usefulness_verified", "hydro": "incremental_gate_passed", "hurricane": "forecast_gate_passed", "pacific_hurricane": "forecast_gate_passed", "snow_daily": "all_strong_comparator_gates_passed", "snow_summer": "all_strong_comparator_gates_passed", "east_africa": "forecast_gate_passed", "snow_kings": "geographic_confirmation_gate_passed", "south_africa_maize": "robust_primary_forecast_gate_passed", "southern_africa_maize_replication": "robust_confirmation_gate_passed", "cybench_maize": "forecast_gate_passed", "cybench_maize_late": "forecast_gate_passed"}[name]
        results["forecast_verification"][name] = bool(result.get(key, False))
        if name == "southern_africa_maize_replication":
            results["forecast_verification"][name] = bool(result["primary_yield"]["robust_confirmation_gate_passed"])
            results["forecast_verification"][name + "_production"] = bool(result["secondary_production"]["robust_confirmation_gate_passed"])
        results["implemented_candidates"].append(name)
    if (OUT / "hurricane_trading/summary.json").exists():
        results["hurricane_market_test"] = read("hurricane_trading")
    if (OUT / "cybench_trading/summary.json").exists():
        results["cybench_market_test"] = read("cybench_trading")
    if (OUT / "cybench_trading/late/summary.json").exists():
        results["cybench_late_market_test"] = read("cybench_trading/late")
    if (OUT / "annual_solar/summary.json").exists():
        results["discarded_annual_solar_monthly_diagnostic"] = "Annual-reporting monthly values before2023 are allocations, not independent monthly measurements; not eligible for validation"
    for name, result in vhp.items():
        results["additional_" + name + "_evaluation"] = result
        # The geographic panel is confirmation, not a new independent signal family.
        results["forecast_verification"][name] = bool(result.get("forecast_gate_passed", False))
    results["verified_small_historical_forecast_improvements"] = {
        name: bool(result.get("statistical_skill_components_passed", False))
        for name, result in newer.items() if name in ("cybench_maize", "cybench_maize_late")
    }
    results["frozen_practical_gate_met"] = any(results["forecast_verification"].values())
    results["verification_requirement_met"] = bool(
        results["frozen_practical_gate_met"]
        or any(results["verified_small_historical_forecast_improvements"].values()))
    results["verification_interpretation"] = (
        "User accepts forecast improvement without a minimum size. Independently reproduced small current-vintage county-yield gains meet that narrow definition; original 5% materiality gates remain false. Original-release operational and trading value remain unverified. See cybench_maize/interpretation_record.json; decomposition is disclosed after results and does not change any fit or original gate.")
    if (OUT / "literature/evidence.json").exists():
        results["external_literature_evidence"] = "literature/evidence.json; externally published, not locally replicated and not a substitute for the forecast verification gate"
    if trade:
        results["market_test"] = trade
    if gas_trade:
        results["gas_market_test"] = gas_trade
    if (OUT / "snow/summary.json").exists():
        results["snow_feasibility"] = "snow/summary.json; causal historical archive not established; no fitted forecast counted"
    (OUT / "summary.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    manifest = {}
    paths = sorted((ROOT / "results/satellite_sites").glob("*.csv"))
    paths += sorted(path for path in OUT.glob("**/input*") if path.name != "input_manifest.json")
    paths += sorted((OUT / "solar/inputs").glob("*.json"))
    paths += sorted((OUT / "enso/inputs").glob("*"))
    paths += sorted((OUT / "seaice/inputs").glob("*"))
    paths += sorted((OUT / "gas/inputs").glob("*"))
    paths += sorted((OUT / "gas/ground_hdd/raw").glob("*"))
    paths += [OUT / "gas/ground_hdd/monthly_hdd.csv"]
    paths += sorted((OUT / "gas_trading/inputs").glob("*"))
    paths += sorted((OUT / "snow").glob("*.csv"))
    paths += [OUT / "smelters/production_labels.csv", OUT / "trading/weekly_wheat_inputs.csv"]
    for name in ("goes_solar", "hydro", "hurricane", "hurricane_trading", "pacific_hurricane", "snow_daily", "snow_summer", "east_africa", "snow_kings", "south_africa_maize", "southern_africa_maize_replication", "cybench_maize", "cybench_maize_late", "cybench_trading", "annual_solar"):
        paths += [p for p in (OUT / name).rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc") and p.name not in ("summary.json", "panel.csv", "predictions.csv", "events.csv", "events_double_cost.csv", "daily_prices.csv") and "__pycache__" not in p.parts]
    for name in ("vhp_wheat", "vhp_wheat_panel"):
        paths += [p for p in (ROOT / "results" / name).rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts and p.name != "summary.json"]
    for path in paths:
        if path.is_file():
            manifest[str(path.relative_to(ROOT))] = {"bytes": path.stat().st_size,
                                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (OUT / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(OUT / "summary.json")


if __name__ == "__main__":
    main()
