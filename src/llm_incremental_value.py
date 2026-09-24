#!/usr/bin/env python3
"""Does the LLM synthesis add ranking value beyond cheap mechanical features?

Post-outcome, exploratory analysis (not a pre-registered test). Builds one row per
century case (2009-2025) and per matured live-2026 case with:

- mechanical features computed only from prices on or before the signal date:
  drawdown from 1y high, 1/6/12-month momentum, 63-day volatility, log price,
  log 63-day average dollar volume (size/liquidity proxy);
- the four averaged Ox synthesis fields (p_plus20, expected, p_positive, downside);
- the frozen-protocol 90-day outcome (next eligible close after acceptance,
  conservative terminal bound) and excess vs SPY over the same window.

Then compares, walk-forward by year (train strictly on earlier years):
  M0 mechanical only, M1 mechanical + LLM, M2 LLM only,
on rank IC, top-decile excess, and the causal top-decile portfolio IR. The
live-2026 cohort is scored with models trained on all century years.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_lab
import ox_lab as ox
import sealed_safety_eval as ev
from live_predictions import ROOT, mean_scores

OUT = ROOT / "lab_runs/llm_incremental"
MECH = ["drawdown", "mom_21", "mom_126", "mom_252", "vol_63", "log_price", "log_dollar_vol"]
LLM = ["p20", "exp", "ppos", "down"]
MODELS = {"M0_mech": MECH, "M1_mech_llm": MECH + LLM, "M2_llm": LLM}
COHORTS = {"hist_2009_2018": (2009, 2018), "disc_2019_2020": (2019, 2020),
           "fwd_2021_2025": (2021, 2025)}


def fetch(tickers: set[str], run_dir: Path, start: dt.date, end: dt.date) -> dict:
    http = ox.CachedHTTP(run_dir / "cache" / "outcome_price", min_interval=0.08)

    def work(symbol):
        try:
            return symbol, long_lab.chart_series(symbol, start, end, http)[0]
        except Exception:
            return symbol, []

    out = {}
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for n, (symbol, series) in enumerate(pool.map(work, sorted(tickers | {"SPY"})), 1):
            out[symbol] = [{"date": r["date"].isoformat(), "close": r["close"],
                            "volume": r["volume"]} for r in series]
            if n % 250 == 0:
                print(f"  prices {n}/{len(tickers) + 1}", file=sys.stderr)
    return out


def mechanical(case: dict, series: list[dict]) -> dict | None:
    """Features from closes strictly available at the signal (<= signal day)."""
    accepted = case.get("accepted") or ""
    day = dt.date.fromisoformat(case["cutoff"])
    try:
        parsed = dt.datetime.strptime(accepted.split(".")[0], "%Y-%m-%d %H:%M:%S")
        # after-close acceptance: that day's close is known; pre-close: use prior day
        day = parsed.date() if parsed.time() >= dt.time(16, 0) else parsed.date() - dt.timedelta(days=1)
    except ValueError:
        pass
    hist = [r for r in series if dt.date.fromisoformat(r["date"]) <= day]
    if len(hist) < 64:
        return None
    closes = np.array([r["close"] for r in hist[-253:]])
    vols = np.array([r["volume"] for r in hist[-63:]])
    last = closes[-1]

    def mom(n):
        return last / closes[-1 - n] - 1 if len(closes) > n else np.nan

    rets = np.diff(np.log(closes[-64:]))
    dollar = float(np.mean(vols * closes[-63:]))
    return {"drawdown": last / closes.max() - 1, "mom_21": mom(21), "mom_126": mom(126),
            "mom_252": mom(252), "vol_63": float(np.std(rets) * math.sqrt(252)),
            "log_price": math.log(last) if last > 0 else np.nan,
            "log_dollar_vol": math.log(dollar) if dollar > 0 else np.nan}


def outcome(case: dict, prices: dict, spy: dict) -> dict | None:
    trade, status = ev.make_trade(case, prices, "conservative")
    if status != "ok" or not trade:
        return None
    e, x = spy.get(trade["entry_date"]), spy.get(trade["exit_date"])
    if not e or not x:
        return None
    return {"entry_date": trade["entry_date"], "stock_ret": trade["stock_return"],
            "excess": trade["stock_return"] - (x / e - 1),
            "terminal_gap": trade["terminal_history_gap"]}


def build(cases: list[dict], scores: dict, prices: dict, cohort: str) -> list[dict]:
    spy = {r["date"]: r["close"] for r in prices["SPY"]}
    rows = []
    for case in cases:
        s = scores.get(case["case_id"])
        if not s or any(s[f] is None for f in LLM):
            continue
        feats = mechanical(case, prices.get(case["ticker"], []))
        out = outcome(case, prices, spy)
        if feats is None or out is None:
            continue
        rows.append({"case_id": case["case_id"], "ticker": case["ticker"], "cutoff": case["cutoff"],
                     "accepted": case.get("accepted") or "", "cohort": cohort,
                     **feats, **{f: s[f] for f in LLM}, **out})
    return rows


def load_dataset(refresh: bool) -> pd.DataFrame:
    path = OUT / "dataset.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path)
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()

    # century: reuse the evaluator's cached price window (same URLs -> cache hits)
    wrap = json.load((ROOT / "lab_runs/century_safety/wrapper_index.json").open())
    century = [{"case_id": k, **v} for k, v in wrap.items()]
    days = [dt.date.fromisoformat(c["cutoff"]) for c in century]
    print(f"century: {len(century)} cases", file=sys.stderr)
    cprices = fetch({c["ticker"] for c in century}, ROOT / "lab_runs/century_safety",
                    min(days) - dt.timedelta(days=400), max(days) + dt.timedelta(days=200))
    rows = build(century, mean_scores(ROOT / "lab_runs/century_safety/syntheses.jsonl"),
                 cprices, "century")

    # live 2026: every case whose 90-day window has elapsed (not just signals)
    live = []
    for line in (ROOT / "lab_runs/live_2026/cases.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if (today - dt.date.fromisoformat(r["cutoff"])).days >= 92:
            live.append({"case_id": r["case_id"], "ticker": r.get("ticker") or "",
                         "cutoff": r["cutoff"], "accepted": r.get("accepted") or ""})
    print(f"live 2026 matured: {len(live)} cases", file=sys.stderr)
    lprices = fetch({c["ticker"] for c in live if c["ticker"]}, ROOT / "lab_runs/live_2026",
                    dt.date(2024, 12, 1), today)
    rows += build(live, mean_scores(ROOT / "lab_runs/live_2026/syntheses.jsonl"),
                  lprices, "live_2026")
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def month_rank(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Cross-sectional percentile ranks within signal month (removes timing)."""
    month = df["cutoff"].str[:7]
    return df[cols].groupby(month).rank(pct=True).fillna(0.5)


def ic(pred: pd.Series, y: pd.Series) -> float:
    ok = pred.notna() & y.notna()
    return float(spearmanr(pred[ok], y[ok]).statistic) if ok.sum() > 10 else float("nan")


def monthly_ic(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """Mean monthly cross-sectional IC and its t-stat."""
    vals = [ic(g[col], g["excess"]) for _, g in df.groupby(df["cutoff"].str[:7]) if len(g) >= 8]
    vals = [v for v in vals if not math.isnan(v)]
    if len(vals) < 3:
        return float("nan"), float("nan")
    return statistics.fmean(vals), statistics.fmean(vals) / (statistics.stdev(vals) / math.sqrt(len(vals)))


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, cols: list[str], kind: str) -> np.ndarray:
    xtr, xte = month_rank(train, cols).values, month_rank(test, cols).values
    ytr = train.groupby(train["cutoff"].str[:7])["excess"].rank(pct=True).values
    if kind == "ridge":
        model = Ridge(alpha=10.0)
    else:
        model = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200,
                                              min_samples_leaf=100, l2_regularization=1.0,
                                              random_state=0)
    model.fit(xtr, ytr)
    return model.predict(xte), model.predict(xtr)


def top_decile(df: pd.DataFrame, col: str) -> dict:
    cut = df[col].quantile(0.9)
    top = df[df[col] >= cut]
    return {"n": len(top), "mean_excess": float(top["excess"].mean()),
            "median_excess": float(top["excess"].median()), "win": float((top["excess"] > 0).mean())}


def walk_forward(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    century = df[df.cohort == "century"].copy()
    century["year"] = century["cutoff"].str[:4].astype(int)
    parts = []
    for year in range(2012, 2026):
        train, test = century[century.year < year], century[century.year == year].copy()
        if test.empty:
            continue
        for name, cols in MODELS.items():
            pred, train_pred = fit_predict(train, test, cols, kind)
            test[name] = pred
            # causal, scale-stable score: percentile vs the model's own training predictions
            test[f"{name}_pct"] = np.searchsorted(np.sort(train_pred), pred) / len(train_pred)
        parts.append(test)
    live = df[df.cohort == "live_2026"].copy()
    if not live.empty:
        for name, cols in MODELS.items():
            pred, train_pred = fit_predict(century, live, cols, kind)
            live[name] = pred
            live[f"{name}_pct"] = np.searchsorted(np.sort(train_pred), pred) / len(train_pred)
    return pd.concat(parts + [live])


def portfolio_ir(rows: pd.DataFrame, score_col: str, prices: dict, years: tuple[int, int]) -> dict:
    recs = [{"case_id": r.case_id, "ticker": r.ticker, "cutoff": r.cutoff, "accepted": r.accepted,
             "score": float(getattr(r, score_col))} for r in rows.itertuples()]
    chosen = ev.causal_select(sorted(recs, key=lambda x: (x["accepted"] or x["cutoff"], x["case_id"])))
    chosen = [c for c in chosen if years[0] <= int(c["cutoff"][:4]) <= years[1]]
    if not chosen:
        return {"trades": 0}
    end = (dt.date(years[1], 12, 31) + dt.timedelta(days=181)).isoformat()
    m, _, _ = ev.evaluate_selection(chosen, prices, "conservative", start=f"{years[0]}-01-01", end=end)
    return {k: m.get(k) for k in ("trades", "exposure_matched_information_ratio",
                                   "exposure_matched_excess_annualized", "trade_win_rate",
                                   "mean_trade_return")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="rebuild dataset.csv")
    ap.add_argument("--no-portfolio", action="store_true")
    args = ap.parse_args()
    df = load_dataset(args.refresh)
    df = df[df["excess"].notna()].copy()
    df["neg_down"] = -df["down"]
    df["neg_drawdown"] = -df["drawdown"]  # deeper drawdown = higher
    report = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "n": df.groupby("cohort").size().to_dict()}

    # 1. single-feature monthly ICs by cohort
    df["year"] = df["cutoff"].str[:4].astype(int)
    feats = ["p20", "exp", "ppos", "neg_down", "neg_drawdown", "mom_21", "mom_126", "mom_252",
             "vol_63", "log_price", "log_dollar_vol"]
    single = {}
    groups = {name: df[(df.cohort == "century") & df.year.between(*yrs)] for name, yrs in COHORTS.items()}
    groups["live_2026"] = df[df.cohort == "live_2026"]
    for gname, g in groups.items():
        single[gname] = {f: monthly_ic(g, f) for f in feats}
    report["single_feature_monthly_ic"] = single

    # 2. does p20 survive controlling for mechanical features? residual IC
    resid = {}
    for gname, g in groups.items():
        if len(g) < 50:
            continue
        x = month_rank(g, MECH).values
        y = month_rank(g, ["p20"])["p20"].values
        beta, *_ = np.linalg.lstsq(np.c_[np.ones(len(x)), x], y, rcond=None)
        r2 = 1 - np.var(y - np.c_[np.ones(len(x)), x] @ beta) / np.var(y)
        g = g.assign(p20_resid=y - np.c_[np.ones(len(x)), x] @ beta)
        resid[gname] = {"r2_p20_on_mech": float(r2), "resid_ic": monthly_ic(g, "p20_resid")}
    report["p20_residual_after_mechanical"] = resid

    # 3. walk-forward models
    wf = {}
    for kind in ("ridge", "gbm"):
        pred = walk_forward(df, kind)
        pred["year"] = pred["cutoff"].str[:4].astype(int)
        res = {}
        pgroups = {name: pred[(pred.cohort == "century") & pred.year.between(max(yrs[0], 2012), yrs[1])]
                   for name, yrs in COHORTS.items()}
        pgroups["live_2026"] = pred[pred.cohort == "live_2026"]
        for gname, g in pgroups.items():
            res[gname] = {m: {"monthly_ic": monthly_ic(g, m), "top_decile": top_decile(g, m)}
                          for m in MODELS}
            res[gname]["raw_p20"] = {"monthly_ic": monthly_ic(g, "p20"), "top_decile": top_decile(g, "p20")}
        wf[kind] = res
        pred.to_csv(OUT / f"walk_forward_{kind}.csv", index=False)
    report["walk_forward"] = wf

    # 4. causal top-decile portfolios (frozen protocol) on walk-forward ridge scores
    if not args.no_portfolio:
        pred = pd.read_csv(OUT / "walk_forward_ridge.csv")
        cen = pred[pred.cohort == "century"]
        wrap = json.load((ROOT / "lab_runs/century_safety/wrapper_index.json").open())
        days = [dt.date.fromisoformat(v["cutoff"]) for v in wrap.values()]
        raw = fetch(set(cen.ticker), ROOT / "lab_runs/century_safety",
                    min(days) - dt.timedelta(days=400), max(days) + dt.timedelta(days=200))
        raw["^IRX"] = [{"date": r["date"].isoformat(), "close": r["close"]} for r in long_lab.chart_series(
            "^IRX", min(days) - dt.timedelta(days=400), max(days) + dt.timedelta(days=200),
            ox.CachedHTTP(ROOT / "lab_runs/century_safety/cache/outcome_price", min_interval=0.08))[0]]
        port = {}
        for col in ("M0_mech_pct", "M1_mech_llm_pct", "M2_llm_pct", "p20", "neg_drawdown"):
            port[col] = {"hist_2012_2018": portfolio_ir(cen, col, raw, (2012, 2018)),
                         "fwd_2021_2025": portfolio_ir(cen, col, raw, (2021, 2025))}
        report["portfolio_causal_top_decile"] = port

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    print(json.dumps(report, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
