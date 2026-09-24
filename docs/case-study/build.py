#!/usr/bin/env python3
"""Build the case-study page from committed results.

Every number on the page comes from files in this repository:
results/forecast_audit/report.json, results/redaction_audit/summary.json,
results/memorization_probe/summary.json, data/audit_inputs/dataset.csv and the
strategy-site exports. Writes docs/index.html (GitHub Pages) and, with
--fragment, a body-only copy for hosts that supply their own <head>.

    python3 docs/case-study/build.py [--fragment PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SITE = ROOT / "sites/strategy-site/public/data"

IDENT_ROWS = {  # hits, a rename, near-misses with the right clues, and a 2026 filing, in display order
    "I001": "Novavax", "I030": "Ionis Pharmaceuticals", "I075": "Coeur Mining", "I097": "Kosmos Energy",
    "I023": "VivoSim Labs (2026)", "I031": "Altisource Portfolio Solutions", "I054": "CEL-SCI", "I088": "Zoom Video",
}
PERIOD_LABELS = {
    "2009-2018 historical holdout": "2009–18 holdout",
    "2019-2020 discovery": "2019–20 discovery",
    "2021-2025 forward holdout": "2021–25 holdout",
    "2026 live (post-cutoff)": "2026 live",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def data() -> dict:
    audit = load(ROOT / "results/forecast_audit/report.json")
    series = load(SITE / "timeseries.json")
    ledger = load(SITE / "p_plus20_trades.json")["trades"]
    lvb = audit["live_vs_backtest"]
    live = lvb["live"]["p20"]
    periods = audit["periods"]
    with (ROOT / "data/audit_inputs/dataset.csv").open() as fh:
        century = [float(r["excess"]) for r in csv.DictReader(fh) if r["cohort"] == "century" and r["excess"]]
    ident = load(ROOT / "results/identification_probe/summary.json")
    by_item = {r["item"]: r for r in ident["rows"]}
    cen = ident["historical_2011_2024"]
    red = load(ROOT / "results/redaction_audit/summary.json")["all"]
    ridge = audit["incremental_value_ridge"]["fwd_2021_2025"]
    recall = load(ROOT / "results/recall_probe/summary.json")
    sys.path.insert(0, str(ROOT / "src"))
    import recall_probe as rp

    def auc_row(label, rows, key, seed):
        scores = [float(r[key]) for r in rows]
        labels = [r["winner"] for r in rows]
        return {"label": label, "n": len(rows), "auc": rp.auc(scores, labels), "ci": rp.auc_ci(scores, labels, seed=seed)}

    hist_rows = [r for r in recall["rows"] if r["cohort"] == "historical"]
    ctrl_rows = [r for r in recall["rows"] if r["cohort"] == "control_2026"]
    quotes = {r["company"]: r for r in recall["rows"]}
    port = audit["causal_portfolios"]

    return {
        "equity": [{"d": p["d"], "nav": round(p["nav"], 4), "spy": round(p["spy"], 4)} for p in series["series"]],
        "metrics": {"ir": series["metrics"]["exposure_matched_information_ratio"], "cagr": series["metrics"]["cagr"]},
        "backtest": {"n": lvb["backtest_p20_2011_2025"]["n"], "mean": lvb["backtest_p20_2011_2025"]["mean"],
                     "ci": lvb["backtest_p20_2011_2025"]["mean_ci"],
                     "trades": [{"t": t["ticker"], "f": t["cutoff"], "x": round(t["excess_return"], 4)}
                                for t in ledger if t.get("excess_return") is not None]},
        "live": {"n": live["n"], "mean": live["mean"], "ci": live["mean_ci"],
                 "wins": sum(t["excess_pct"] > 0 for t in live["trades"]),
                 "trades": [{"t": t["ticker"], "f": t["cutoff"], "x_date": t["exit_date"], "x": t["excess_pct"] / 100}
                            for t in live["trades"]]},
        "worstRun": live["worst_backtest_run"]["worst_mean"],
        "otherRules": [{"name": {"uxd": "Upside × drawdown", "blend": "Blend of four outputs"}[k],
                        "mean": v["mean"], "n": v["n"]} for k, v in lvb["live"].items() if k != "p20"],
        "byYear": [{"year": r["year"], "n": r["n"], "months": r["months"], "ic": r["monthly_ic"], "lo": r["ic_lo"],
                    "hi": r["ic_hi"], "hs": r["hit_spread"], "hslo": r["hs_lo"], "hshi": r["hs_hi"]}
                   for r in audit["by_year"]],
        "periods": {"live": {"ic": periods["2026 live (post-cutoff)"]["signal"]["monthly_ic"]},
                    "fwd": {"base": sum(x >= 0.2 for x in century) / len(century)}},
        "periodRows": [{"name": PERIOD_LABELS[k], "n": v["signal"]["n"], "ic": v["signal"]["monthly_ic"],
                        "auc": v["signal"]["auc_hit"], "top": v["signal"]["top_hit_rate"],
                        "base": v["signal"]["base_hit_rate"]}
                       for k, v in periods.items() if k in PERIOD_LABELS],
        "calRows": [{"name": PERIOD_LABELS[k].split(" ")[0], "fc": v["calibration_raw"]["mean_forecast"],
                     "obs": v["calibration_raw"]["observed_rate"], "bss": v["calibration_raw"]["brier_skill"],
                     "bssR": v["calibration_recalibrated"]["brier_skill"]}
                    for k, v in periods.items() if "calibration_raw" in v],
        "reliability": {"hist": audit["reliability"]["2009-2025"], "live": audit["reliability"]["2026"]},
        "incremental": {"icLLM": ridge["M2_llm"]["monthly_ic"], "icMech": ridge["M0_mech"]["monthly_ic"],
                        "irLLM": port["M2_llm_pct"]["fwd_2021_2025"]["exposure_matched_information_ratio"],
                        "irMech": port["M0_mech_pct"]["fwd_2021_2025"]["exposure_matched_information_ratio"]},
        "redaction": {"packs": red["packs"], "anyId": red["share_any_identifier"],
                      "name": red["share_name_token_survives"], "address": red["share_cover_address"]},
        "recall": {
            "rows": [auc_row("Haiku · name + date only", hist_rows, "p_outperform", 1),
                     auc_row("Haiku · name + date only", ctrl_rows, "p_outperform", 2),
                     auc_row("Ox · full filing", hist_rows, "ox_p20", 3),
                     auc_row("Ox · full filing", ctrl_rows, "ox_p20", 4)],
            "pValue": recall["historical_2011_2024"]["auc_p_value"],
            "rhoHist": recall["diagnostics"]["historical"]["spearman_haiku_name_only_vs_ox_filing"],
            "rhoCtrl": recall["diagnostics"]["control_2026"]["spearman_haiku_name_only_vs_ox_filing"],
            "large": recall["diagnostics"]["historical_auc_by_liquidity"]["large"],
            "small": recall["diagnostics"]["historical_auc_by_liquidity"]["small"],
            "quotes": [{"company": name, "filed": quotes[key]["filed"], "excess": quotes[key]["excess"],
                        "text": quotes[key]["recalled"]}
                       for key, name in (("COEUR D ALENE MINES CORP", "Coeur d’Alene Mines"),
                                         ("Kosmos Energy Ltd.", "Kosmos Energy"),
                                         ("Baker Hughes Co", "Baker Hughes"))]},
        "probe": {"idRate": cen["top1_rate"], "top3Rate": cen["top3_rate"], "confAuc": cen["confidence_auc"],
                  "live": ident["control_2026"]["top1_rate"], "n": cen["n"],
                  "rows": [{"company": name, "filed": by_item[i]["filed"], "hit": by_item[i]["top1"],
                            "guess": by_item[i]["company_guess"], "conf": by_item[i]["confidence"],
                            "clue": by_item[i]["clue"]}
                           for i, name in IDENT_ROWS.items()]},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fragment", type=Path, help="also write a body-only copy here")
    args = ap.parse_args()
    payload = json.dumps(data(), separators=(",", ":")).replace("</", "<\\/")
    page = (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/", payload)
    doc = ('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + page + "\n</html>\n")
    (ROOT / "docs/index.html").write_text(doc, encoding="utf-8")
    print("wrote docs/index.html", len(doc), "bytes")
    if args.fragment:
        args.fragment.write_text(page, encoding="utf-8")
        print("wrote", args.fragment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
