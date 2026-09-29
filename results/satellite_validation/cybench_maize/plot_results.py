#!/usr/bin/env python3
"""Draw the two fixed county-yield forecast horizons; never refit or select rows."""
from pathlib import Path
import json
import hashlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SPECS = [
    (ROOT, "August 15 · primary", "April–July observations", "#087F8C"),
    (ROOT.parent / "cybench_maize_late", "September 15 · prespecified secondary", "April–August observations", "#7856A6"),
]
BASELINES = [("weather", "Weather model"), ("trend", "County linear trend"),
             ("persistence", "Last released yield"), ("recent_mean", "Mean of last 5 yields")]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.titlesize": 11, "axes.labelsize": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#B8BDC2", "text.color": "#202A33",
                         "axes.labelcolor": "#202A33", "xtick.color": "#4B5560",
                         "ytick.color": "#4B5560", "figure.facecolor": "white"})
    datasets = []
    for directory, title, period, color in SPECS:
        summary = json.loads((directory / "summary.json").read_text())
        audit = json.loads((directory / "independent_validation_audit.json").read_text())
        assert audit["audit_passed"] and audit["summary_sha256"] == sha(directory / "summary.json")
        annual = pd.read_csv(directory / "annual_losses.csv")
        datasets.append((directory, title, period, color, summary, annual))
    fig = plt.figure(figsize=(14.2, 8.0))
    grid = fig.add_gridspec(2, 2, left=.07, right=.97, bottom=.21, top=.82,
                           wspace=.36, hspace=.50, width_ratios=[1.13, 1.])
    forest = fig.add_subplot(grid[:, 1])
    bounds = []
    for index, (directory, title, period, color, summary, annual) in enumerate(datasets):
        ax = fig.add_subplot(grid[index, 0])
        ax.plot(annual.year, np.sqrt(annual.weather_mse), color="#6B7280", lw=1.8,
                marker="o", ms=3.3, label="Weather")
        ax.plot(annual.year, np.sqrt(annual.satellite_mse), color=color, lw=2.,
                marker="o", ms=3.5, label="Weather + NDVI")
        ax.set_title(title, loc="left", pad=18, weight="bold")
        ax.text(0, 1.025, f"{period}  ·  {summary['n_county_years']:,} scored county-years",
                transform=ax.transAxes, fontsize=9, color="#65717C")
        ax.set_ylabel("Annual RMSE (t/ha)")
        ax.set_xticks(np.arange(2013, 2024, 2))
        ax.grid(axis="y", color="#E5E7EB", lw=.7)
        ax.set_axisbelow(True)
        ax.margins(x=.025)
        ax.legend(frameon=False, loc="lower left", ncol=2, fontsize=8.5)
        ax.set_ylim(bottom=0)
        if index == 1:
            ax.set_xlabel("Harvest year")
        for row, (baseline, _) in enumerate(BASELINES):
            comparison = summary["comparisons"][baseline]
            point = comparison["rmse_reduction"] * 100
            low, high = np.asarray(comparison["rmse_reduction_ci95"]) * 100
            y = 3 - row + (.13 if index == 0 else -.13)
            forest.errorbar(point, y, xerr=[[point - low], [high - point]],
                            color=color, fmt="o" if index == 0 else "s", ms=5,
                            capsize=3, lw=1.8, zorder=4)
            bounds.extend([low, high])
    forest.set_title("RMSE improvement over each comparator", loc="left", pad=18, weight="bold")
    forest.text(0, 1.025, "Point estimates and paired 95% year-block intervals",
                transform=forest.transAxes, fontsize=9, color="#65717C")
    forest.set_yticks([3, 2, 1, 0], [label for _, label in BASELINES])
    forest.set_ylim(-.65, 3.7)
    forest.axvline(0, color="#A1A7AE", lw=1)
    forest.axvline(5, color="#A87919", lw=1.3, ls=(0, (4, 3)))
    forest.text(5, 3.43, "5% materiality bar", color="#946B15", fontsize=8.5,
                ha="left", va="bottom", rotation=0)
    forest.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    forest.set_xlim(min(-1.5, min(bounds) - 2), max(8, max(bounds) + 3))
    forest.set_xlabel("Lower forecast error →")
    forest.grid(axis="x", color="#ECEFF1", lw=.7)
    forest.set_axisbelow(True)
    forest.legend(handles=[Line2D([0], [0], color=spec[3], marker="o" if i == 0 else "s",
                                 lw=1.8, label="Aug 15 primary" if i == 0 else "Sep 15 secondary")
                           for i, spec in enumerate(SPECS)],
                  frameon=False, fontsize=9, loc="lower right")
    fig.text(.07, .94, "County maize yield: what vegetation adds to a weather forecast",
             fontsize=18, weight="bold")
    fig.text(.07, .901, "US county forecasts, 2013–2023  ·  two fixed issue dates  ·  current-vintage scientific test",
             fontsize=11, color="#5E6B76")
    statuses = []
    for _, title, _, _, summary, _ in datasets:
        gain = summary["comparisons"]["weather"]["rmse_reduction"] * 100
        statuses.append(f"{'Aug 15' if 'August' in title else 'Sep 15'}: {gain:+.2f}% vs weather; frozen materiality gate {'PASS' if summary['forecast_gate_passed'] else 'FAIL'}")
    fig.text(.07, .143, "   |   ".join(statuses), fontsize=10.3, weight="bold")
    fig.text(.07, .102, "Equal county weights within year and equal year weights. Horizons have different eligible support. Intervals use shared 5-calendar-year blocks (10,000 draws).",
             fontsize=9, color="#5E6B76")
    fig.text(.07, .072, "Revised archives and fixed 2021 crop geography; 2022/2023 are post-reference-year checks, not certified historical releases. Weather is AgERA5 reanalysis.",
             fontsize=9, color="#5E6B76")
    fig.text(.07, .042, "Exploratory tests; intervals are not adjusted across candidates. Forecast accuracy does not establish trading returns. Sources: USDA NASS / CY-Bench / NASA MODIS.",
             fontsize=9, color="#5E6B76")
    for suffix in ("png", "pdf"):
        fig.savefig(ROOT / f"county_forecast_comparison.{suffix}", dpi=190, facecolor="white")
    plt.close(fig)
    manifest = {str(directory.name): {"summary_sha256": sha(directory / "summary.json"),
                                      "annual_losses_sha256": sha(directory / "annual_losses.csv"),
                                      "audit_sha256": sha(directory / "independent_validation_audit.json")}
                for directory, *_ in datasets}
    manifest["plot_script_sha256"] = sha(__file__)
    (ROOT / "plot_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    run()
