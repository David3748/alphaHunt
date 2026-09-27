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
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "satellite-validation"})
    fig, ax = plt.subplots(figsize=(10, 6))
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
    fig.text(.02, .025, "*Solar uses revised imagery published too late for the proposed nowcast. Other gains fail uncertainty or confirmation gates.\nDifferent targets and error metrics; bar lengths are not a ranking of investability.", fontsize=9, color="#555555")
    fig.tight_layout(rect=(0, .09, 1, 1))
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
    results["verification_requirement_met"] = any(results["forecast_verification"].values())
    if (OUT / "literature/evidence.json").exists():
        results["external_literature_evidence"] = "literature/evidence.json; externally published, not locally replicated and not a substitute for the forecast verification gate"
    if trade:
        results["market_test"] = trade
    (OUT / "summary.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    manifest = {}
    paths = sorted((ROOT / "results/satellite_sites").glob("*.csv"))
    paths += sorted(OUT.glob("**/input*"))
    paths += sorted((OUT / "solar/inputs").glob("*.json"))
    paths += sorted((OUT / "enso/inputs").glob("*"))
    paths += [OUT / "smelters/production_labels.csv", OUT / "trading/weekly_wheat_inputs.csv"]
    for path in paths:
        if path.is_file():
            manifest[str(path.relative_to(ROOT))] = {"bytes": path.stat().st_size,
                                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (OUT / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(OUT / "summary.json")


if __name__ == "__main__":
    main()
