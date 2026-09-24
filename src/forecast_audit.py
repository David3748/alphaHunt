#!/usr/bin/env python3
"""Leakage and calibration audit of the LLM P(+20%) forecasts.

Post-hoc and exploratory (not a pre-registered test). The question is whether the
2009-2025 backtest edge survives in 2026, when the forecasting model can no longer
have seen the outcomes, and whether its probabilities mean what they say.

Inputs (produced by existing pipelines; a snapshot is committed in data/audit_inputs/
and used automatically when lab_runs/ is absent, e.g. in a fresh clone):
- lab_runs/llm_incremental/dataset.csv: one row per case with the averaged Ox
  synthesis fields, mechanical price features and the frozen-protocol 90-day
  excess return vs SPY (century 2009-2025 + matured live-2026 cases);
- lab_runs/live_2026/outcomes_forward.json: graded live trades of the locked rules;
- lab_runs/llm_incremental/walk_forward_ridge.csv: walk-forward ridge scores per case
  (price features only, LLM outputs only, both), trained on strictly earlier years;
- sites/strategy-site/public/data/p_plus20_trades.json: the backtest trade ledger
  of the same locked P(+20%) rule;
- results/redaction_audit and results/memorization_probe summaries.

Outputs: results/forecast_audit/{report.json,report.md} and docs/figures/*.svg.

    python3 src/forecast_audit.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data/audit_inputs"  # committed copy of the inputs, for clones without lab_runs/


def _input(run_path: str, name: str) -> Path:
    path = ROOT / run_path
    return path if path.exists() else SNAPSHOT / name


DATASET = _input("lab_runs/llm_incremental/dataset.csv", "dataset.csv")
INCREMENTAL = _input("lab_runs/llm_incremental/report.json", "llm_incremental_report.json")
LIVE_TRADES = _input("lab_runs/live_2026/outcomes_forward.json", "live_outcomes_forward.json")
WALK_FORWARD = _input("lab_runs/llm_incremental/walk_forward_ridge.csv", "walk_forward_scores.csv")
WALK_FORWARD_COLS = ["case_id", "M0_mech", "M1_mech_llm", "M2_llm"]
BACKTEST_TRADES = ROOT / "sites/strategy-site/public/data/p_plus20_trades.json"
FORECASTS_MADE = "2026-08-24"  # live syntheses were generated 2026-08-24 (UTC)
HIT = 0.20                     # the forecast event: beat SPY by >= 20 pp over 90 days
MIN_MONTH = 8                  # cases needed for a monthly cross-sectional IC

PERIODS = {
    "2009-2018 historical holdout": lambda d: (d.cohort == "century") & (d.year <= 2018),
    "2019-2020 discovery": lambda d: (d.cohort == "century") & d.year.between(2019, 2020),
    "2021-2025 forward holdout": lambda d: (d.cohort == "century") & (d.year >= 2021),
    "2026 live (post-cutoff)": lambda d: d.cohort == "live_2026",
}


# ---------------------------------------------------------------- data

def load(path: Path = DATASET) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["excess"].notna() & df["p20"].notna()].copy()
    df["year"] = df["cutoff"].str[:4].astype(int)
    df["month"] = df["cutoff"].str[:7]
    df["hit"] = (df["excess"] >= HIT).astype(int)
    df["p"] = (df["p20"] / 100).clip(0.005, 0.995)
    return df


# ---------------------------------------------------------------- ranking

def monthly_ics(df: pd.DataFrame, col: str = "p20") -> np.ndarray:
    vals = [spearmanr(g[col], g["excess"]).statistic
            for _, g in df.groupby("month") if len(g) >= MIN_MONTH and g[col].nunique() > 1]
    return np.array([v for v in vals if not math.isnan(v)])


def bootstrap_ci(values: np.ndarray, stat=np.mean, draws: int = 2000, seed: int = 0) -> list[float]:
    if len(values) < 2:
        return [float("nan"), float("nan")]
    rng = np.random.default_rng(seed)
    boots = [stat(rng.choice(values, len(values))) for _ in range(draws)]
    return [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def quintile_edge(df: pd.DataFrame, col: str = "p20", draws: int = 1000, seed: int = 0) -> dict:
    """Top- vs bottom-quintile (ranked within month) hit rate and excess, month-block bootstrap."""
    d = df.assign(q=df.groupby("month")[col].rank(pct=True))
    months = d["month"].unique()

    def edge(frame):
        top, bot = frame[frame.q > 0.8], frame[frame.q <= 0.2]
        return (top.hit.mean() - bot.hit.mean(), top.excess.mean() - bot.excess.mean(),
                top.hit.mean(), top.excess.mean())

    point = edge(d)
    rng = np.random.default_rng(seed)
    groups = {m: g for m, g in d.groupby("month")}
    boots = []
    for _ in range(draws):
        sample = pd.concat([groups[m] for m in rng.choice(months, len(months))])
        boots.append(edge(sample))
    boots = np.array(boots)
    ci = lambda i: [float(np.nanpercentile(boots[:, i], 2.5)), float(np.nanpercentile(boots[:, i], 97.5))]
    return {"top_hit_rate": float(point[2]), "top_mean_excess": float(point[3]),
            "hit_rate_spread": float(point[0]), "hit_rate_spread_ci": ci(0),
            "excess_spread": float(point[1]), "excess_spread_ci": ci(1)}


def auc(score: pd.Series, label: pd.Series) -> float:
    pos, neg = score[label == 1].values, score[label == 0].values
    if not len(pos) or not len(neg):
        return float("nan")
    ranks = pd.Series(np.r_[pos, neg]).rank().values
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def signal_block(df: pd.DataFrame) -> dict:
    ics = monthly_ics(df)
    se = ics.std(ddof=1) / math.sqrt(len(ics)) if len(ics) > 1 else float("nan")
    return {"n": int(len(df)), "months": int(len(ics)), "base_hit_rate": float(df.hit.mean()),
            "mean_excess": float(df.excess.mean()),
            "monthly_ic": float(ics.mean()) if len(ics) else float("nan"),
            "monthly_ic_t": float(ics.mean() / se) if len(ics) > 1 else float("nan"),
            "monthly_ic_ci": bootstrap_ci(ics),
            "auc_hit": auc(df.p20, df.hit), **quintile_edge(df)}


# ---------------------------------------------------------------- memory vs market

def memory_vs_market(df: pd.DataFrame, scores: pd.DataFrame, draws: int = 1000, seed: int = 0) -> dict:
    """Did the LLM lose more skill in 2026 than a walk-forward model on price features alone,
    which cannot remember anything? Difference-in-differences, 2021-2025 vs 2026, with a
    calendar-month block bootstrap (resampling the same months for both scorers)."""
    d = df.drop(columns=[c for c in WALK_FORWARD_COLS[1:] if c in df.columns]).merge(
        scores[WALK_FORWARD_COLS], on="case_id", how="inner")
    pre, post = d[(d.cohort == "century") & (d.year >= 2021)], d[d.cohort == "live_2026"]
    cols = {"llm_p20": "p20", "llm_walk_forward": "M2_llm", "mechanical_walk_forward": "M0_mech"}
    out = {"n": {"2021-2025": int(len(pre)), "2026": int(len(post))}, "auc_hit": {}, "monthly_ic": {}}
    for name, col in cols.items():
        out["auc_hit"][name] = {"2021-2025": auc(pre[col], pre.hit), "2026": auc(post[col], post.hit)}
        out["monthly_ic"][name] = {"2021-2025": float(monthly_ics(pre, col).mean()),
                                   "2026": float(monthly_ics(post, col).mean())}
        a, b = out["auc_hit"][name]["2021-2025"], out["auc_hit"][name]["2026"]
        out["auc_hit"][name]["share_of_above_chance_skill_lost"] = (a - b) / (a - 0.5) if a > 0.5 else None

    def ic_by_month(frame, col):
        return {m: spearmanr(g[col], g["excess"]).statistic for m, g in frame.groupby("month")
                if len(g) >= MIN_MONTH and g[col].nunique() > 1}
    ic = {(p, c): ic_by_month(f, c) for p, f in (("pre", pre), ("post", post)) for c in ("p20", "M0_mech")}
    months = {p: sorted(set(ic[(p, "p20")]) & set(ic[(p, "M0_mech")])) for p in ("pre", "post")}
    groups = {p: {m: g for m, g in f.groupby("month")} for p, f in (("pre", pre), ("post", post))}

    def auc_did(a, b):
        return (auc(a.p20, a.hit) - auc(b.p20, b.hit)) - (auc(a.M0_mech, a.hit) - auc(b.M0_mech, b.hit))

    def ic_did(mp, mq):
        mean = lambda p, c, ms: float(np.mean([ic[(p, c)][m] for m in ms]))
        return (mean("pre", "p20", mp) - mean("post", "p20", mq)) - (mean("pre", "M0_mech", mp) - mean("post", "M0_mech", mq))

    rng = np.random.default_rng(seed)
    boots = {"auc": [], "ic": []}
    all_months = {p: sorted(groups[p]) for p in groups}
    for _ in range(draws):
        ma = rng.choice(all_months["pre"], len(all_months["pre"]))
        mb = rng.choice(all_months["post"], len(all_months["post"]))
        boots["auc"].append(auc_did(pd.concat([groups["pre"][m] for m in ma]), pd.concat([groups["post"][m] for m in mb])))
        boots["ic"].append(ic_did(rng.choice(months["pre"], len(months["pre"])), rng.choice(months["post"], len(months["post"]))))
    point = {"auc": auc_did(pre, post), "ic": ic_did(months["pre"], months["post"])}
    out["difference_in_differences_llm_p20_vs_mechanical"] = {
        k: {"point": float(point[k]), "ci": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))],
            "share_of_draws_above_zero": float(np.mean(np.array(v) > 0))} for k, v in boots.items()}
    out["months"] = {"2021-2025": len(all_months["pre"]), "2026": len(all_months["post"])}
    return out


# ---------------------------------------------------------------- calibration

def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def calibration_block(p: np.ndarray, y: np.ndarray, clim: np.ndarray, bins: int = 10) -> dict:
    """Brier skill vs a climatology forecast, ECE, and logistic calibration slope/intercept."""
    p, y, clim = map(lambda a: np.asarray(a, dtype=float), (p, y, clim))
    brier, brier_clim = float(np.mean((p - y) ** 2)), float(np.mean((clim - y) ** 2))
    order = np.argsort(p, kind="stable")
    ece = 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk):
            ece += len(chunk) / len(p) * abs(p[chunk].mean() - y[chunk].mean())
    lr = LogisticRegression(C=1e6).fit(logit(p).reshape(-1, 1), y)
    return {"brier": brier, "brier_climatology": brier_clim, "brier_skill": 1 - brier / brier_clim,
            "ece": float(ece), "calibration_slope": float(lr.coef_[0][0]),
            "calibration_intercept": float(lr.intercept_[0]),
            "mean_forecast": float(p.mean()), "observed_rate": float(y.mean())}


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    order = np.argsort(p, kind="stable")
    rows = []
    for chunk in np.array_split(order, bins):
        if not len(chunk):
            continue
        n, k = len(chunk), float(y[chunk].sum())
        z, phat = 1.96, k / n
        centre = (phat + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        rows.append({"forecast": float(p[chunk].mean()), "observed": phat, "n": n,
                     "ci": [max(0.0, centre - half), min(1.0, centre + half)]})
    return rows


def walk_forward_recalibration(df: pd.DataFrame, min_train: int = 200) -> pd.DataFrame:
    """Platt-rescale p20 each year using only strictly earlier years; climatology = earlier base rate.

    The live-2026 cohort is scored with everything from 2009-2025 (and no 2026 data).
    """
    out = []
    for year in sorted(df.year.unique()):
        train = df[df.year < year]
        test = df[df.year == year].copy()
        if len(train) < min_train or train.hit.nunique() < 2:
            continue
        lr = LogisticRegression(C=1e6).fit(logit(train.p).reshape(-1, 1), train.hit)
        test["p_recal"] = lr.predict_proba(logit(test.p).reshape(-1, 1))[:, 1]
        test["p_clim"] = train.hit.mean()
        out.append(test)
    return pd.concat(out) if out else df.iloc[0:0].assign(p_recal=[], p_clim=[])


# ---------------------------------------------------------------- live trades vs backtest

def trade_stats(excess: list[float], draws: int = 5000, seed: int = 0) -> dict:
    arr = np.array(excess, dtype=float)
    return {"n": int(len(arr)), "mean": float(arr.mean()), "median": float(np.median(arr)),
            "win_rate": float((arr > 0).mean()), "mean_ci": bootstrap_ci(arr, draws=draws, seed=seed)}


def surprise(backtest: list[float], live: list[float], draws: int = 20000, seed: int = 0) -> float:
    """P(mean of len(live) trades drawn from the backtest ledger <= observed live mean)."""
    rng = random.Random(seed)
    target, n = statistics.fmean(live), len(live)
    hits = sum(statistics.fmean(rng.choices(backtest, k=n)) <= target for _ in range(draws))
    return (hits + 1) / (draws + 1)


def worst_run(ledger: list[dict], n: int) -> dict:
    """Worst mean excess over n consecutive backtest trades (by entry date): a clustering-aware yardstick."""
    rows = sorted((t for t in ledger if t.get("excess_return") is not None), key=lambda t: (t["entry_date"], t["ticker"]))
    means = [statistics.fmean(t["excess_return"] for t in rows[i:i + n]) for i in range(len(rows) - n + 1)]
    i = int(np.argmin(means))
    return {"n": n, "worst_mean": means[i], "from": rows[i]["entry_date"], "to": rows[i + n - 1]["entry_date"],
            "share_of_runs_below_zero": sum(m < 0 for m in means) / len(means)}


def live_vs_backtest(live_path: Path = LIVE_TRADES, ledger_path: Path = BACKTEST_TRADES) -> dict:
    live = json.loads(live_path.read_text())
    ledger = json.loads(ledger_path.read_text())["trades"]
    back = [t["excess_return"] for t in ledger if t.get("excess_return") is not None]
    fwd = [t["excess_return"] for t in ledger if t.get("excess_return") is not None and t["cutoff"] >= "2021"]
    out = {"graded_on": live["generated_at"], "backtest_p20_2011_2025": trade_stats(back),
           "backtest_p20_2021_2025": trade_stats(fwd), "live": {}}
    for rule in ("p20", "uxd", "blend"):
        rows = [t for t in live["trades"] if t["rule"] == rule and t.get("excess_pct") is not None]
        if not rows:
            continue
        ex = [t["excess_pct"] / 100 for t in rows]
        prospective = [t["excess_pct"] / 100 for t in rows
                       if (pd.Timestamp(t["cutoff"]) + pd.Timedelta(days=90)).date().isoformat() > FORECASTS_MADE]
        out["live"][rule] = {**trade_stats(ex), "prospective_n": len(prospective),
                             "trades": [{k: t[k] for k in ("ticker", "cutoff", "exit_date", "excess_pct")} for t in rows]}
    out["live"]["p20"]["p_backtest_would_do_this_badly"] = surprise(back, [t["excess_pct"] / 100 for t in out["live"]["p20"]["trades"]])
    out["live"]["p20"]["worst_backtest_run"] = worst_run(ledger, out["live"]["p20"]["n"])
    return out


# ---------------------------------------------------------------- figures

THEMES = {
    "light": {"ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "surface": "#ffffff"},
    "dark": {"ink": "#f0f6fc", "ink2": "#c3c2b7", "muted": "#9198a1", "grid": "#2c2c2a",
             "axis": "#3d444d", "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "surface": "#0d1117"},
}


def _style(ax, t):
    ax.set_facecolor("none")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelsize=9, length=0, pad=6)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def figures(yearly: pd.DataFrame, rel: dict, trades: dict, out_dir: Path, cutoff: dict | None = None) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for mode, t in THEMES.items():
        plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "svg.fonttype": "none",
                             "svg.hashsalt": "alphahunt",  # deterministic ids: re-renders don't churn git
                             "text.color": t["ink"], "axes.labelcolor": t["ink2"]})

        # 1. ranking signal by year: monthly IC and top-vs-bottom quintile hit-rate spread
        fig, axes = plt.subplots(2, 1, figsize=(8, 5.6), sharex=True, gridspec_kw={"hspace": 0.35})
        fig.patch.set_alpha(0)
        yrs = yearly["year"].values
        colors = [t["s2"] if y == 2026 else t["s1"] for y in yrs]
        for ax, col, lo, hi, title, fmt in (
                (axes[0], "monthly_ic", "ic_lo", "ic_hi", "Rank IC of P(+20%) vs 90-day excess return (mean of monthly ICs)", "{:.2f}"),
                (axes[1], "hit_spread", "hs_lo", "hs_hi", "+20 pp hit rate: top minus bottom quintile", "{:+.0%}")):
            _style(ax, t)
            ax.axhline(0, color=t["axis"], linewidth=1)
            for x, v, l, h, c in zip(yrs, yearly[col], yearly[lo], yearly[hi], colors):
                ax.plot([x, x], [l, h], color=c, linewidth=2, solid_capstyle="round", alpha=0.55)
                ax.plot(x, v, "o", color=c, markersize=7, markeredgecolor=t["surface"], markeredgewidth=1.5)
            ax.set_title(title, loc="left", fontsize=10.5, color=t["ink"], pad=8)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _, f=fmt: f.format(v)))
        axes[1].set_xticks(yrs)
        axes[1].set_xticklabels([str(y) for y in yrs], rotation=0, fontsize=8.5)
        handles = [axes[0].plot([], [], "o", color=t["s1"], label="2011-2025: model may know the outcome")[0],
                   axes[0].plot([], [], "o", color=t["s2"], label="2026: after the model's training data")[0]]
        fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.07, 0.93), frameon=False, fontsize=8.5,
                   labelcolor=t["ink2"], ncol=2)
        axes[1].text(0.0, -0.3, "Bars: 95% bootstrap intervals over months. Years with < 3 usable months omitted.",
                     transform=axes[1].transAxes, fontsize=8, color=t["muted"])
        path = out_dir / f"signal_by_year-{mode}.svg"
        fig.savefig(path, bbox_inches="tight", transparent=True, metadata={"Date": None})
        plt.close(fig)
        written.append(str(path))

        # 2. reliability diagram
        fig, ax = plt.subplots(figsize=(5.6, 5.0))
        fig.patch.set_alpha(0)
        _style(ax, t)
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.plot([0, 0.5], [0, 0.5], color=t["muted"], linewidth=1, linestyle=(0, (1, 2)))
        ax.text(0.37, 0.405, "perfectly calibrated", rotation=38, fontsize=8, color=t["muted"])
        for key, color, label in (("2009-2025", t["s1"], "2009-2025 (9.8k cases)"),
                                  ("2026", t["s2"], "2026 live (1.0k cases)")):
            rows = rel[key]
            xs = [r["forecast"] for r in rows]
            ys = [r["observed"] for r in rows]
            for r in rows:
                ax.plot([r["forecast"]] * 2, r["ci"], color=color, linewidth=1.5, alpha=0.4)
            ax.plot(xs, ys, color=color, linewidth=2, solid_joinstyle="round")
            ax.plot(xs, ys, "o", color=color, markersize=6, markeredgecolor=t["surface"], markeredgewidth=1.5,
                    label=label)
        ax.set_xlim(0, 0.5)
        ax.set_ylim(0, 0.5)
        ax.set_xlabel("Forecast P(beat SPY by 20+ pp in 90 days), decile mean", fontsize=9)
        ax.set_ylabel("Observed frequency", fontsize=9)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
        ax.legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor=t["ink2"])
        ax.set_title("Calibration of the LLM's P(+20%)", loc="left", fontsize=10.5, color=t["ink"], pad=8)
        path = out_dir / f"calibration-{mode}.svg"
        fig.savefig(path, bbox_inches="tight", transparent=True, metadata={"Date": None})
        plt.close(fig)
        written.append(str(path))

        # 3. per-trade excess: backtest ledger vs live trades of the same locked rule
        fig, ax = plt.subplots(figsize=(8, 2.9))
        fig.patch.set_alpha(0)
        _style(ax, t)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        ax.axvline(0, color=t["axis"], linewidth=1)
        rng = np.random.default_rng(1)
        rows = [(1, trades["backtest"], t["s1"], "Backtest 2011-2025\n(270 trades)"),
                (0, trades["live"], t["s2"], "Live 2026\n(12 trades)")]
        for y, vals, color, label in rows:
            vals = np.clip(np.array(vals), -1, 1.5)
            ax.scatter(vals, y + rng.uniform(-0.18, 0.18, len(vals)), s=16 if len(vals) > 50 else 34,
                       color=color, alpha=0.45 if len(vals) > 50 else 0.85, edgecolors=t["surface"], linewidths=0.8)
            m = float(np.mean(trades["backtest_raw" if y else "live_raw"]))
            lo, hi = trades["ci_backtest" if y else "ci_live"]
            ax.plot([lo, hi], [y + 0.32] * 2, color=t["ink2"], linewidth=2, solid_capstyle="round")
            ax.plot(m, y + 0.32, "D", color=t["ink"], markersize=6)
            ax.text(hi + 0.03, y + 0.32, f"mean {m:+.0%}", va="center", fontsize=8.5, color=t["ink"])
        ax.set_yticks([0, 1])
        ax.set_yticklabels([rows[1][3], rows[0][3]], fontsize=8.5, color=t["ink2"])
        ax.set_ylim(-0.5, 1.6)
        ax.set_xlim(-1.02, 1.55)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
        ax.set_xlabel("90-day excess return vs SPY per trade (clipped at -100% / +150%); diamond = mean, bar = 95% CI",
                      fontsize=8.5)
        ax.set_title("Same locked P(+20%) rule: backtest vs live", loc="left", fontsize=10.5, color=t["ink"], pad=8)
        path = out_dir / f"backtest_vs_live-{mode}.svg"
        fig.savefig(path, bbox_inches="tight", transparent=True, metadata={"Date": None})
        plt.close(fig)
        written.append(str(path))

        # 4. before/after-cutoff probe: AUC in vs after Haiku's training data, per scorer
        if cutoff:
            fig, ax = plt.subplots(figsize=(8, 3.0))
            fig.patch.set_alpha(0)
            _style(ax, t)
            ax.grid(axis="y", visible=False)
            ax.grid(axis="x", color=t["grid"], linewidth=0.8)
            ax.axvline(0.5, color=t["axis"], linewidth=1)
            ax.text(0.5, 2.62, "chance", ha="center", fontsize=8, color=t["muted"])
            rows = [("Haiku reading the\nscrubbed filing", "haiku_p_outperform"),
                    ("Backtest forecaster\n(Ox Alpha)", "ox_p20_backtest_forecaster"),
                    ("Price features only\n(cannot remember)", "mechanical_walk_forward_model")]
            for y, (label, key) in zip((2, 1, 0), rows):
                a, z = cutoff["by_scorer"][key]["in_training"]["auc"], cutoff["by_scorer"][key]["after_training"]["auc"]
                ax.annotate("", xy=(z + 0.012, y), xytext=(a - 0.012, y),
                            arrowprops={"arrowstyle": "-|>", "color": t["muted"], "linewidth": 1.5})
                ax.plot(a, y, "o", color=t["s1"], markersize=9, markeredgecolor=t["surface"], markeredgewidth=1.5)
                ax.plot(z, y, "o", color=t["s2"], markersize=9, markeredgecolor=t["surface"], markeredgewidth=1.5)
                ax.text(a, y + 0.22, f"{a:.2f}", fontsize=8.5, color=t["ink2"], ha="center")
                ax.text(z, y + 0.22, f"{z:.2f}", fontsize=8.5, color=t["ink2"], ha="center")
            ax.set_yticks([2, 1, 0])
            ax.set_yticklabels([r[0] for r in rows], fontsize=8.5, color=t["ink2"])
            ax.set_ylim(-0.55, 2.85)
            ax.set_xlim(0.4, 0.8)
            ax.set_xlabel("AUC, 90-day winners (beat SPY by 25+ pp) vs losers (trailed by 25+), 50 filings per period",
                          fontsize=8.5)
            dd = cutoff["difference_in_differences"]["haiku_vs_mechanical"]
            ax.set_title(f"All three lose about as much skill across the cutoff (Haiku minus price model: {dd['point']:+.2f})",
                         loc="left", fontsize=10.5, color=t["ink"], pad=22)
            handles = [ax.plot([], [], "o", color=t["s1"], label="2023-24 filings: outcome in Haiku's training data")[0],
                       ax.plot([], [], "o", color=t["s2"], label="Aug 2025 on: outcome after it")[0]]
            ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.0), frameon=False, fontsize=8.5,
                      labelcolor=t["ink2"], ncol=2, borderaxespad=0.1)
            path = out_dir / f"cutoff_probe-{mode}.svg"
            fig.savefig(path, bbox_inches="tight", transparent=True, metadata={"Date": None})
            plt.close(fig)
            written.append(str(path))
    return written


# ---------------------------------------------------------------- report

def build(df: pd.DataFrame) -> dict:
    report = {"dataset_rows": {k: int(v) for k, v in df.groupby("cohort").size().items()},
              "forecast_event": "excess return vs SPY >= +20 pp over 90 calendar days", "periods": {}}
    wf = walk_forward_recalibration(df)
    for name, mask in PERIODS.items():
        sub = df[mask(df)]
        block = {"signal": signal_block(sub)}
        w = wf[mask(wf)]
        if len(w):
            block["calibration_raw"] = calibration_block(w.p, w.hit, w.p_clim)
            block["calibration_recalibrated"] = calibration_block(w.p_recal, w.hit, w.p_clim)
        report["periods"][name] = block
    live = df[df.cohort == "live_2026"]
    prospective = live[(pd.to_datetime(live.cutoff) + pd.Timedelta(days=90)).dt.strftime("%Y-%m-%d") > FORECASTS_MADE]
    if len(prospective) >= 20:
        report["periods"]["2026 strictly prospective subset"] = {
            "signal": {"n": int(len(prospective)), "note": f"90-day window closed after forecasts were made ({FORECASTS_MADE})",
                       "pooled_spearman": float(spearmanr(prospective.p20, prospective.excess).statistic),
                       "auc_hit": auc(prospective.p20, prospective.hit),
                       "base_hit_rate": float(prospective.hit.mean())}}

    yearly = []
    for year in sorted(df.year.unique()):
        sub = df[df.year == year]
        ics = monthly_ics(sub)
        if len(ics) < 3:
            continue
        q = quintile_edge(sub, draws=400, seed=year)
        yearly.append({"year": int(year), "n": int(len(sub)), "months": int(len(ics)),
                       "monthly_ic": float(ics.mean()), "ic_lo": bootstrap_ci(ics)[0], "ic_hi": bootstrap_ci(ics)[1],
                       "hit_spread": q["hit_rate_spread"], "hs_lo": q["hit_rate_spread_ci"][0],
                       "hs_hi": q["hit_rate_spread_ci"][1], "forecast_sd": float(sub.p20.std()),
                       "base_hit_rate": float(sub.hit.mean())})
    report["by_year"] = yearly

    cen, liv = df[df.cohort == "century"], df[df.cohort == "live_2026"]
    report["reliability"] = {"2009-2025": reliability(cen.p.values, cen.hit.values),
                             "2026": reliability(liv.p.values, liv.hit.values)}
    ics_fwd, ics_live = monthly_ics(df[PERIODS["2021-2025 forward holdout"](df)]), monthly_ics(liv)
    diff = [np.mean(np.random.default_rng(i).choice(ics_live, len(ics_live)))
            - np.mean(np.random.default_rng(10_000 + i).choice(ics_fwd, len(ics_fwd))) for i in range(2000)]
    report["ic_change_2026_vs_2021_2025"] = {"point": float(ics_live.mean() - ics_fwd.mean()),
                                             "ci": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))]}

    if INCREMENTAL.exists():
        inc = json.loads(INCREMENTAL.read_text())
        ridge = inc["walk_forward"]["ridge"]
        report["incremental_value_ridge"] = {
            period: {m: {"monthly_ic": ridge[period][m]["monthly_ic"][0],
                         "top_decile_mean_excess": ridge[period][m]["top_decile"]["mean_excess"]}
                     for m in ("M0_mech", "M1_mech_llm", "M2_llm", "raw_p20")}
            for period in ("fwd_2021_2025", "live_2026") if period in ridge}
        report["causal_portfolios"] = inc.get("portfolio_causal_top_decile", {})
    for name in ("redaction_audit", "memorization_probe", "recall_probe", "identification_probe", "cutoff_probe",
                 "ticker_audit"):
        path = ROOT / "results" / name / "summary.json"
        if path.exists():
            summary = json.loads(path.read_text())
            report[name] = {k: v for k, v in summary.items() if k != "rows"}
    if LIVE_TRADES.exists() and BACKTEST_TRADES.exists():
        report["live_vs_backtest"] = live_vs_backtest()
    if WALK_FORWARD.exists():
        report["memory_vs_market"] = memory_vs_market(df, pd.read_csv(WALK_FORWARD, usecols=WALK_FORWARD_COLS))
    return report


def pct(x, signed=False, digits=0):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}")


def markdown(r: dict) -> str:
    L = ["# Forecast audit: leakage and calibration", "",
         "Post-hoc, exploratory analysis generated by `src/forecast_audit.py`. Forecast event: "
         f"{r['forecast_event']}.", "", "## Ranking signal by period", "",
         "| Period | Cases | Months | Base rate | Monthly IC [95% CI] | AUC | Top-quintile hit rate | Top-bottom hit spread [95% CI] | Top-bottom excess spread |",
         "| --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |"]
    for name, b in r["periods"].items():
        s = b["signal"]
        if "monthly_ic" not in s:
            continue
        L.append(f"| {name} | {s['n']:,} | {s['months']} | {pct(s['base_hit_rate'])} | {s['monthly_ic']:.3f} "
                 f"[{s['monthly_ic_ci'][0]:.3f}, {s['monthly_ic_ci'][1]:.3f}] | {s['auc_hit']:.3f} | "
                 f"{pct(s['top_hit_rate'])} | {pct(s['hit_rate_spread'], True)} [{pct(s['hit_rate_spread_ci'][0], True)}, "
                 f"{pct(s['hit_rate_spread_ci'][1], True)}] | {pct(s['excess_spread'], True, 1)} |")
    c = r["ic_change_2026_vs_2021_2025"]
    L += ["", f"Change in monthly IC, 2026 vs 2021-2025: {c['point']:+.3f} (95% CI {c['ci'][0]:+.3f} to {c['ci'][1]:+.3f}).", ""]
    sp = r["periods"].get("2026 strictly prospective subset")
    if sp:
        s = sp["signal"]
        L += [f"Strictly prospective 2026 subset ({s['note']}): {s['n']} cases, pooled Spearman "
              f"{s['pooled_spearman']:.3f}, AUC {s['auc_hit']:.3f}.", ""]
    L += ["## Calibration (walk-forward; climatology = base rate of strictly earlier years)", "",
          "| Period | Mean forecast | Observed rate | Brier skill (raw) | Brier skill (Platt, walk-forward) | ECE raw | Calibration slope |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, b in r["periods"].items():
        if "calibration_raw" not in b:
            continue
        a, z = b["calibration_raw"], b["calibration_recalibrated"]
        L.append(f"| {name} | {pct(a['mean_forecast'], digits=1)} | {pct(a['observed_rate'], digits=1)} | "
                 f"{a['brier_skill']:+.3f} | {z['brier_skill']:+.3f} | {a['ece']:.3f} | {a['calibration_slope']:.2f} |")
    lv = r.get("live_vs_backtest")
    if lv:
        b, f, p = lv["backtest_p20_2011_2025"], lv["backtest_p20_2021_2025"], lv["live"]["p20"]
        L += ["", "## Locked P(+20%) rule: live trades vs its own backtest", "",
              "| Sample | Trades | Mean excess [95% CI] | Median | Win rate |", "| --- | ---: | --- | ---: | ---: |",
              f"| Backtest 2011-2025 | {b['n']} | {pct(b['mean'], True, 1)} [{pct(b['mean_ci'][0], True, 1)}, {pct(b['mean_ci'][1], True, 1)}] | {pct(b['median'], True, 1)} | {pct(b['win_rate'])} |",
              f"| Backtest 2021-2025 | {f['n']} | {pct(f['mean'], True, 1)} [{pct(f['mean_ci'][0], True, 1)}, {pct(f['mean_ci'][1], True, 1)}] | {pct(f['median'], True, 1)} | {pct(f['win_rate'])} |",
              f"| Live 2026 (graded {lv['graded_on']}) | {p['n']} | {pct(p['mean'], True, 1)} [{pct(p['mean_ci'][0], True, 1)}, {pct(p['mean_ci'][1], True, 1)}] | {pct(p['median'], True, 1)} | {pct(p['win_rate'])} |",
              "", f"Probability that {p['n']} trades drawn independently from the backtest ledger average this badly: "
              + (f"< 0.0001 (no resample of 20,000)" if p['p_backtest_would_do_this_badly'] < 1e-4
                 else f"{p['p_backtest_would_do_this_badly']:.4f}") + ". Trades cluster in time, so a fairer yardstick is "
              f"the backtest's worst run of {p['n']} consecutive trades: {pct(p['worst_backtest_run']['worst_mean'], True, 1)} "
              f"({p['worst_backtest_run']['from']} to {p['worst_backtest_run']['to']}); "
              f"{pct(p['worst_backtest_run']['share_of_runs_below_zero'])} of all {p['n']}-trade runs averaged below zero. "
              "Other locked rules live: "
              + ", ".join(f"`{k}` {v['n']} trades, mean {pct(v['mean'], True, 1)}, win {pct(v['win_rate'])}"
                          for k, v in lv["live"].items() if k != "p20") + "."]
    ra, mp = r.get("redaction_audit"), r.get("memorization_probe")
    if ra:
        a = ra["century"]
        L += ["", "## Could the model know which company it was reading?", "",
              f"- {pct(a['share_any_identifier'])} of the {a['packs']:,} century evidence packs still contain a direct "
              f"identifier after issuer redaction: the company's distinctive name word survives in "
              f"{pct(a['share_name_token_survives'])} of packs that have one, and the cover-page address in "
              f"{pct(a['share_cover_address'])}."]
    if mp:
        c, lvc = mp["century_2011_2024"], mp["live_2026_control"]
        L += [f"- With every name token and the ticker scrubbed, Claude Haiku 4.5 named {c['identified']}/{c['n']} "
              f"historical companies from 4.5k characters of MD&A alone ({pct(c['identification_rate'])}), "
              f"but its recalled post-filing direction was right in only "
              f"{pct(c['recall_direction_accuracy'])} of the {c['identified_with_directional_recall']} cases where it "
              f"offered one, and its P(+20%) did not separate winners from losers (AUC {c['p_beat20_auc_winners_vs_losers']:.2f}; "
              f"2026 control {lvc['p_beat20_auc_winners_vs_losers']:.2f})."]
    ip_ = r.get("identification_probe")
    if ip_:
        h, c = ip_["historical_2011_2024"], ip_["control_2026"]
        L += [f"- Identification probe (100 fresh Haiku contexts, 10k chars of strictly scrubbed MD&A each): named the "
              f"company in {h['top1']} of {h['n']} 2011-2024 filings ({pct(h['top1_rate'], digits=1)}, 95% CI {pct(h['top1_ci'][0])}-{pct(h['top1_ci'][1])}) "
              f"(top-3 {pct(h['top3_rate'])}) and {pct(c['top1_rate'])} of 2026 filings; its confidence separated hits "
              f"from misses with AUC {h['confidence_auc']:.2f}."]
    rp = r.get("recall_probe")
    if rp:
        h, c, d = rp["historical_2011_2024"], rp["control_2026"], rp.get("diagnostics", {})
        L += [f"- Given only the company name, ticker and filing date (100 fresh Haiku contexts, one case each), "
              f"Haiku's P(outperform) separated 2011-2024 winners from losers with AUC {h['auc_p_outperform']:.2f} "
              f"[{h['auc_ci'][0]:.2f}, {h['auc_ci'][1]:.2f}], permutation p = {h['auc_p_value']:.3f}; on 2026 controls "
              f"AUC {c['auc_p_outperform']:.2f} [{c['auc_ci'][0]:.2f}, {c['auc_ci'][1]:.2f}]. It never claimed a specific "
              f"memory of the window."]
        if d:
            L += [f"- On the same cases the filing-reading forecaster's P(+20%) had AUC "
                  f"{d['historical']['ox_p20_auc_same_cases']:.2f} (historical) vs {d['control_2026']['ox_p20_auc_same_cases']:.2f} "
                  f"(2026), and Haiku's name-only guesses correlated {d['historical']['spearman_haiku_name_only_vs_ox_filing']:.2f} "
                  f"with it historically vs {d['control_2026']['spearman_haiku_name_only_vs_ox_filing']:.2f} in 2026."]
    cp_ = r.get("cutoff_probe")
    if cp_:
        b = cp_["by_scorer"]
        rows = [("Haiku reading the scrubbed filing, P(outperform)", "haiku_p_outperform"),
                ("Backtest forecaster (Ox), P(+20%)", "ox_p20_backtest_forecaster"),
                ("Mechanical walk-forward model (price features only)", "mechanical_walk_forward_model")]
        dd = cp_["difference_in_differences"]["haiku_vs_mechanical"]
        L += ["", "## Does the backtest overstate skill? Before/after the training cutoff", "",
              f"{cp_['answered']} fresh Haiku contexts, one filing each, under the backtest's own leakage instruction: "
              "50 filings from 2023-01 to 2024-09 (outcome inside Haiku's training data) and 50 from 2025-08 on "
              "(outcome after it); each arm 25 stocks that beat SPY by 25+ points over 90 days and 25 that trailed by 25+.", "",
              "| Scorer | AUC in training [95% CI] | AUC after training [95% CI] | Drop [95% CI] |",
              "| --- | --- | --- | --- |"]
        for label, key in rows:
            a, z, g = b[key]["in_training"], b[key]["after_training"], b[key]["gap"]
            L.append(f"| {label} | {a['auc']:.2f} [{a['ci'][0]:.2f}, {a['ci'][1]:.2f}] | {z['auc']:.2f} "
                     f"[{z['ci'][0]:.2f}, {z['ci'][1]:.2f}] | {g['point']:+.2f} [{g['ci'][0]:+.2f}, {g['ci'][1]:+.2f}] |")
        ex = cp_.get("excluding_flagged_tickers", {})
        L += ["", f"Difference-in-differences, Haiku minus the mechanical model: {dd['point']:+.2f} "
              f"[{dd['ci'][0]:+.2f}, {dd['ci'][1]:+.2f}] (paired bootstrap). A model that cannot remember anything lost as "
              "much skill across the cutoff as Haiku did, so on this sample the drop is the period, not memory. "
              "The interval is wide: the probe rules out only a very large memorization effect."
              + (f" Dropping the {len(ex['dropped'])} cases priced on another filer's ticker gives "
                 f"{ex['did_haiku_vs_mechanical']['point']:+.2f}." if ex else "")]
    mm = r.get("memory_vs_market")
    if mm:
        a, ic, dd = mm["auc_hit"], mm["monthly_ic"], mm["difference_in_differences_llm_p20_vs_mechanical"]
        L += ["", "## Memory or market, on the full cohorts", "",
              f"The same comparison on every case: {mm['n']['2021-2025']:,} filings from 2021-2025 vs {mm['n']['2026']:,} "
              "live 2026 filings, scored by the LLM and by walk-forward ridge models trained on strictly earlier years.", "",
              "| Scorer | AUC (+20 pp) 2021-25 | AUC 2026 | Share of above-chance skill lost | Monthly IC 2021-25 | IC 2026 |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for name, label in (("llm_p20", "LLM P(+20%)"), ("llm_walk_forward", "Walk-forward model on LLM outputs"),
                            ("mechanical_walk_forward", "Walk-forward model on price features")):
            lost = a[name]["share_of_above_chance_skill_lost"]
            L.append(f"| {label} | {a[name]['2021-2025']:.3f} | {a[name]['2026']:.3f} | {pct(lost) if lost is not None else 'n/a'} | "
                     f"{ic[name]['2021-2025']:.3f} | {ic[name]['2026']:.3f} |")
        L += ["", f"Difference-in-differences, LLM P(+20%) minus the price model (calendar-month block bootstrap, "
              f"{mm['months']['2026']} live months): ranking IC {dd['ic']['point']:+.3f} [{dd['ic']['ci'][0]:+.3f}, {dd['ic']['ci'][1]:+.3f}]; "
              f"tail AUC {dd['auc']['point']:+.3f} [{dd['auc']['ci'][0]:+.3f}, {dd['auc']['ci'][1]:+.3f}], "
              f"{pct(dd['auc']['share_of_draws_above_zero'])} of draws above zero. The ranking held for both scorers. In the tail, "
              "the LLM lost more AUC points than the price model, but both lost most of their above-chance skill, and the price "
              "model had little to lose: an additive reading leaves room for memory, a proportional one does not."]
    ta_ = r.get("ticker_audit")
    if ta_:
        fl = ta_["owner_flags"]
        n = fl["probable"] + fl["possible"]
        m = ta_["metric_sensitivity"]
        L += ["", "## Data quality: cases priced on another company's stock", "",
              f"{n} of {ta_['cases']:,} cases ({n / ta_['cases']:.1%}) carry a ticker that another CIK reports as its own "
              f"({fl['probable']} probable, {fl['possible']} possible), mostly from the resolver's file-name fallback; "
              f"{ta_['owner_flags_by_ticker'].get('F', 0)} filings from unrelated small companies were priced as Ford (F). "
              f"Flagged backtest trades: {len(ta_['backtest_trades_flagged'])} of {ta_['backtest_trades']} (a subsidiary "
              f"priced on its parent); flagged live trades: {len(ta_['live_trades_flagged'])} of {ta_['live_trades']}. "
              "Without any flagged case, monthly IC is "
              + ", ".join(f"{v['without_any_flag']['monthly_ic']:.3f} (was {v['all']['monthly_ic']:.3f}) for {k}"
                          for k, v in m.items()) + ". Details: `results/ticker_audit/report.md`."]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/forecast_audit")
    ap.add_argument("--figures", type=Path, default=ROOT / "docs/figures")
    ap.add_argument("--snapshot", action="store_true", help="copy the current inputs into data/audit_inputs/")
    args = ap.parse_args()
    if args.snapshot:
        SNAPSHOT.mkdir(parents=True, exist_ok=True)
        for src, name in ((DATASET, "dataset.csv"), (INCREMENTAL, "llm_incremental_report.json"),
                          (LIVE_TRADES, "live_outcomes_forward.json")):
            if src.parent != SNAPSHOT:
                (SNAPSHOT / name).write_bytes(src.read_bytes())
        if WALK_FORWARD.parent != SNAPSHOT:
            pd.read_csv(WALK_FORWARD, usecols=WALK_FORWARD_COLS).to_csv(SNAPSHOT / "walk_forward_scores.csv", index=False)
    df = load()
    report = build(df)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    (args.out / "report.md").write_text(markdown(report), encoding="utf-8")
    lv = report.get("live_vs_backtest")
    if lv:
        ledger = json.loads(BACKTEST_TRADES.read_text())["trades"]
        back = [t["excess_return"] for t in ledger if t.get("excess_return") is not None]
        live = [t["excess_pct"] / 100 for t in lv["live"]["p20"]["trades"]]
        trades = {"backtest": back, "live": live, "backtest_raw": back, "live_raw": live,
                  "ci_backtest": lv["backtest_p20_2011_2025"]["mean_ci"], "ci_live": lv["live"]["p20"]["mean_ci"]}
        for path in figures(pd.DataFrame(report["by_year"]), report["reliability"], trades, args.figures,
                            cutoff=report.get("cutoff_probe")):
            print("wrote", path)
    print((args.out / "report.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
