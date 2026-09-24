#!/usr/bin/env python3
"""backtest_signals.py — join extracted event catalogs to forward 90-day excess
returns and report whether each signal predicts returns.

Not LLM-bound (Yahoo price fetch). Reuses long_lab.chart_series for prices.
For each source: read extracted jsonl -> dedupe events -> fetch per-ticker price
series + SPY -> compute forward excess return over H trading days -> report
rank-correlation, red-flag bucket means, and top-decile mean excess return.

Usage: python3 src/backtest_signals.py [--horizon 90] [--source NAME]
"""

import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab
import long_lab

try:
    import fidelity_exclusions as fx
except ImportError:  # backtest must work standalone without the exclusion module
    fx = None


# ── Source configs: what to backtest ─────────────────────────────────────────
# fields: ticker, date, score (scalar, higher = stronger signal), redflag (bool),
#         direction ("short" = high score/redflag -> expect negative return)
SOURCES = {
    "forensic_going_concern": {
        "file": "forensic/going_concern.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_loss_contingency": {
        "file": "forensic/loss_contingency.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_related_party": {
        "file": "forensic/related_party.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_goodwill": {
        "file": "forensic/goodwill_impairment.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_covenant": {
        "file": "forensic/debt_covenant.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_revenue": {
        "file": "forensic/revenue_recognition.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_customer_conc": {
        "file": "forensic/customer_concentration.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_supplier_conc": {
        "file": "forensic/supplier_concentration.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_pension": {
        "file": "forensic/pension_assumptions.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_subsequent": {
        "file": "forensic/subsequent_events.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "forensic_mda_consistency": {
        "file": "forensic/mda_consistency.jsonl",
        "ticker": "ticker", "date": "cutoff", "score": "severity",
        "redflag": "is_red_flag", "direction": "short",
    },
    "departures": {
        "file": "departures_extracted.jsonl",
        "ticker": "ticker", "date": "filed_date", "score": None,
        "redflag": "is_unexpected", "direction": "short",
    },
    "form4_discretionary_sell": {
        "file": "form4_extracted.jsonl",
        "ticker": "ticker", "date": "file_date", "score": None,
        "redflag": "is_discretionary", "direction": "short",
        "filter": {"dominant_direction": ["open_market_sell"]},
    },
    "form4_discretionary_buy": {
        "file": "form4_extracted.jsonl",
        "ticker": "ticker", "date": "file_date", "score": None,
        "redflag": "is_discretionary", "direction": "long",
        "filter": {"dominant_direction": ["open_market_buy"]},
    },
    "cpsc": {
        "file": "cpsc_resolved.jsonl",
        "ticker": "ticker", "date": "recall_date", "score": None,
        "redflag": "severity", "redflag_values": ["fire_burn", "child_injury", "electrocution", "chemical"],
        "direction": "short",
    },
    "fda_recalls": {
        "file": "fda_resolved.jsonl",
        "ticker": "ticker", "date": "recall_initiation_date", "score": None,
        "redflag": "recall_class", "redflag_values": ["Class I"],
        "direction": "short",
    },
    "sbir": {
        "file": "sbir_resolved.jsonl",
        "ticker": "ticker", "date": "award_year", "score": None,
        "redflag": "phase_transition_detected", "direction": "long",
    },
    "comment_letters": {
        "file": "comment_letters_extracted.jsonl",
        "ticker": "ticker", "date": "file_date", "score": "severity",
        "redflag": None, "direction": "short",
    },
    "formd": {
        "file": "formd_extracted.jsonl",
        "ticker": "ticker", "date": "file_date", "score": None,
        "redflag": "dilution_signal", "redflag_values": ["high_dilution", "distress_financing"],
        "direction": "short",
    },
}

START = dt.date(2009, 1, 1)
END = dt.date(2026, 8, 24)

FORENSIC_LENSES = [
    "going_concern", "loss_contingency", "related_party", "goodwill_impairment",
    "debt_covenant", "revenue_recognition", "customer_concentration",
    "supplier_concentration", "pension_assumptions", "subsequent_events",
    "mda_consistency",
]


def load_forensic_cases(run_dir):
    """Composite view: per case_id, per-lens (severity, redflag), averaged over replicates."""
    cases = {}
    for lens in FORENSIC_LENSES:
        p = run_dir / "forensic" / f"{lens}.jsonl"
        if not p.exists():
            continue
        per_case = {}
        for r in ox_lab.load_jsonl(p):
            cid = r.get("case_id")
            if not cid:
                continue
            t = r.get("ticker")
            d = parse_date(r.get("cutoff"))
            if not t or not d:
                continue
            try:
                sev = float(r.get("severity") or 0)
            except (TypeError, ValueError):
                sev = 0.0
            per_case.setdefault(cid, []).append((t, d, sev, bool(r.get("is_red_flag"))))
        for cid, rows in per_case.items():
            t, d0 = rows[0][0], rows[0][1]
            sev_avg = sum(x[2] for x in rows) / len(rows)
            red_any = any(x[3] for x in rows)
            case = cases.setdefault(cid, {"ticker": t, "date": d0, "lenses": {}})
            # skip conflicting ticker/date across lenses for same case (shouldn't happen)
            case["lenses"][lens] = (sev_avg, red_any)
    for c in cases.values():
        c["n_red"] = sum(1 for sev, red in c["lenses"].values() if red)
        c["sev_sum"] = sum(sev for sev, _ in c["lenses"].values())
    return cases


def close_on_or_before(rows, day):
    """Last close at or before `day` using sorted rows of {date, close}."""
    import bisect as bs
    dates = [r["date"] for r in rows]
    i = bs.bisect_right(dates, day)
    if i == 0:
        return None
    return rows[i - 1]["close"]


def sim_portfolio(signals, prices, spy_rows, horizon=90, max_positions=20,
                  borrow_annual=0.02, hedge_ratio=0.0, label="portfolio"):
    """Overlapping-cohort daily NAV simulation.
    signals: [{ticker, date, dir}] dir +1 long / -1 short. Equal weight among
    active positions capped at max_positions (FIFO). Shorts pay borrow.
    hedge_ratio: fraction of SPY notional held as hedge (long SPY when shorting
    stocks, i.e. subtract hedge_ratio*spy_ret daily from the book)."""
    cal = [r["date"] for r in spy_rows]
    closes = {t: sorted(rows, key=lambda r: r["date"]) for t, rows in prices.items()}
    spy_close = {r["date"]: r["close"] for r in spy_rows}

    entries = []
    for s in signals:
        i = bisect.bisect_left(cal, s["date"])
        if i >= len(cal) - horizon - 1:
            continue
        if s["ticker"] not in closes:
            continue
        entries.append((i, s))
    entries.sort(key=lambda x: x[0])

    active = []  # dicts: ticker,dir,last,exit_day
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    navs = []
    bench = []
    exposure = []
    ei = 0
    borrow_daily = borrow_annual / 252.0

    for day_i in range(len(cal)):
        day = cal[day_i]
        while ei < len(entries) and entries[ei][0] == day_i:
            if len(active) < max_positions:
                _, s = entries[ei]
                c0 = close_on_or_before(closes[s["ticker"]], day)
                if c0 and c0 > 0:
                    active.append({"ticker": s["ticker"], "dir": s["dir"],
                                   "last": c0, "exit_day": day_i + horizon})
            ei += 1
        rets = []
        done = []
        for pos in active:
            if day_i >= pos["exit_day"]:
                done.append(pos)
                continue
            c = close_on_or_before(closes[pos["ticker"]], day)
            if not c or c <= 0 or pos["last"] <= 0:
                continue
            r = c / pos["last"] - 1.0
            net = pos["dir"] * r - (borrow_daily if pos["dir"] == -1 else 0.0)
            # short-side gap-ups can exceed -100%; margin calls cap position loss
            net = max(net, -0.95)
            pos["last"] = c
            rets.append(net)
        for pos in done:
            active.remove(pos)
        if cal[day_i] not in spy_close:
            continue
        w = 1.0 / len(active) if active else 0.0
        port_ret = sum(w * r for r in rets)
        spy_ret = spy_close[day] / spy_close[cal[day_i - 1]] - 1.0 if day_i > 0 else 0.0
        # hedge: offset the book's SPY beta. Only applied while carrying positions.
        if active and hedge_ratio:
            port_ret += hedge_ratio * spy_ret
        nav *= (1.0 + port_ret)
        peak = max(peak, nav)
        max_dd = min(max_dd, nav / peak - 1.0)
        navs.append(port_ret)
        bench.append(spy_ret)
        exposure.append(len(active))

    if len(navs) < 100:
        return {"label": label, "status": "insufficient"}

    years = len(navs) / 252.0
    total_nav = math.prod(1 + r for r in navs)
    cagr = total_nav ** (1 / years) - 1.0
    vol = statistics.stdev(navs) * math.sqrt(252) if len(navs) > 1 else 0.0
    sharpe = cagr / vol if vol > 0 else 0.0
    spy_total = math.prod(1 + r for r in bench)
    spy_cagr = spy_total ** (1 / years) - 1.0
    te = statistics.stdev([a - b for a, b in zip(navs, bench)]) * math.sqrt(252) if len(navs) > 1 else 0.0
    ir = (cagr - spy_cagr) / te if te > 0 else 0.0
    return {
        "label": label, "status": "ok",
        "n_signals": len(entries),
        "avg_positions": round(sum(exposure) / len(exposure), 1),
        "total_return_pct": round((total_nav - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2),
        "vol_pct": round(vol * 100, 1),
        "sharpe_rf0": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "spy_cagr_pct": round(spy_cagr * 100, 2),
        "info_ratio_vs_spy": round(ir, 2),
        "years": round(years, 1),
    }


def run_composite(run_dir, horizon, exclude=None):
    """Composite + portfolio sims.

    exclude: None (default, legacy behavior unchanged) or a path to a merged
    fidelity_exclusions JSON / bare list / prebuilt list. When provided,
    entry signals falling inside an exclusion window are skipped at entry
    (PIT-strict: event_date <= entry <= window_end).
    """
    print("loading forensic lenses...", flush=True)
    cases = load_forensic_cases(run_dir)
    print(f"{len(cases)} unique cases with lens coverage", flush=True)

    # forward excess per case (reuse price fetch machinery)
    tickers = {c["ticker"] for c in cases.values()}
    prices = fetch_all_prices(tickers, run_dir)
    spy = prices.get("SPY")
    if not spy:
        print("no SPY")
        return

    exs = []
    signals_short = []
    signals_long_subseq = []
    for cid, c in cases.items():
        trows = prices.get(c["ticker"])
        if not trows:
            continue
        ex = forward_excess(trows, spy, c["date"], horizon)
        if ex is None:
            continue
        c["excess"] = ex
        exs.append(c)
        n_red = c["n_red"]
        # subsequent_events is a long signal; exclude it from the short composite
        se_red = c["lenses"].get("subsequent_events", (0, False))[1]
        core_red = n_red - (1 if se_red else 0)
        if core_red >= 1 and se_red:
            continue  # conflicting; skip ambiguous
        if core_red >= 1:
            signals_short.append({"ticker": c["ticker"], "date": c["date"], "dir": -1,
                                  "score": core_red})
        elif se_red:
            signals_long_subseq.append({"ticker": c["ticker"], "date": c["date"], "dir": +1})

    # optional Fidelity exclusion filter (default off — legacy behavior unchanged)
    excl_report = None
    if exclude:
        signals_short, signals_long_subseq, excl_report = apply_exclusion_filter(
            {"signals_short": signals_short, "signals_long_subseq": signals_long_subseq},
            exclude)
        print(f"  exclusion filter: {json.dumps(excl_report)}", flush=True)

    # bucket table by number of red flags
    buckets = {}
    for c in exs:
        b = min(c["n_red"], 4)
        buckets.setdefault(b, []).append(c["excess"])
    print("\n=== composite red-flag buckets (forward 90d excess vs SPY) ===")
    for b in sorted(buckets):
        xs = buckets[b]
        print(f"  {b}+ flags: n={len(xs):>5}  mean={mean(xs)*100:>7.2f}%  hit_neg={sum(1 for x in xs if x<0)/len(xs)*100:.1f}%")

    results = {"buckets": {str(b): {"n": len(v), "mean_excess_pct": round(mean(v)*100, 2)}
                            for b, v in sorted(buckets.items())}, "portfolios": []}

    # portfolio sims
    print("\n=== portfolio simulations (90d holds, 20 slots, equal weight, shorts pay 2% borrow) ===")
    sims = []
    if signals_short:
        sims.append(("composite_short", signals_short, 0.0))
        sims.append(("composite_short_HEDGED", signals_short, 1.0))
    gc = [{"ticker": c["ticker"], "date": c["date"], "dir": -1}
          for c in exs if c["lenses"].get("going_concern", (0, False))[1]]
    if gc:
        sims.append(("going_concern_only_short", gc, 0.0))
        sims.append(("going_concern_short_HEDGED", gc, 1.0))
    rp = [{"ticker": c["ticker"], "date": c["date"], "dir": -1}
          for c in exs if c["lenses"].get("related_party", (0, False))[1]]
    if rp:
        sims.append(("related_party_only_short", rp, 0.0))
    heavy = [{"ticker": c["ticker"], "date": c["date"], "dir": -1}
             for c in exs if sum(1 for _, red in c["lenses"].values() if red) >= 3]
    if heavy:
        sims.append(("composite_3plus_flags_short", heavy, 0.0))
        sims.append(("composite_3plus_short_HEDGED", heavy, 1.0))
    if signals_long_subseq:
        sims.append(("subsequent_events_long", signals_long_subseq, 0.0))

    for label, sigs, hedge in sims:
        r = sim_portfolio(sigs, prices, spy, horizon=horizon, hedge_ratio=hedge, label=label)
        results["portfolios"].append(r)
        print(json.dumps(r))

    out = run_dir / "composite_backtest.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nsaved → {out}")


def load(path):
    return ox_lab.load_jsonl(path) if path.exists() else []


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d{4}", s):  # year-only (SBIR award_year)
        return dt.date(int(s), 1, 1)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def fetch_all_prices(tickers, run_dir):
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.0)
    prices = {}
    tickers = sorted(set(tickers)) + ["SPY"]
    print(f"  fetching prices for {len(tickers)} tickers...", flush=True)
    def one(t):
        try:
            rows, _ = long_lab.chart_series(t, START, END, http)
            return t, rows
        except Exception:
            return t, []
    with cf.ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(one, t): t for t in tickers}
        for fut in cf.as_completed(futures):
            t, rows = fut.result()
            if rows:
                prices[t] = rows
    print(f"  got prices for {len(prices)}/{len(tickers)} tickers", flush=True)
    return prices


def forward_excess(ticker_rows, spy_rows, entry_date, horizon):
    """Return forward excess return over `horizon` trading days, or None if insufficient data."""
    entry = long_lab.first_after(ticker_rows, entry_date)
    if entry is None:
        return None
    idx = ticker_rows.index(entry)
    if idx + horizon >= len(ticker_rows):
        return None
    exit_row = ticker_rows[idx + horizon]
    spy_entry = long_lab.first_after(spy_rows, entry_date)
    if spy_entry is None:
        return None
    spy_idx = spy_rows.index(spy_entry)
    if spy_idx + horizon >= len(spy_rows):
        return None
    spy_exit = spy_rows[spy_idx + horizon]
    if entry["close"] <= 0 or exit_row["close"] <= 0 or spy_entry["close"] <= 0 or spy_exit["close"] <= 0:
        return None
    t_ret = exit_row["close"] / entry["close"] - 1.0
    s_ret = spy_exit["close"] / spy_entry["close"] - 1.0
    return t_ret - s_ret


def spearman(xs, ys):
    if len(xs) < 3:
        return 0.0
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n))
    vy = sum((ry[i] - my) ** 2 for i in range(n))
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def load_exclusions(exclude):
    """Resolve --exclude to a merged exclusion list (None when off).

    Accepts a JSON path (bare list or {exclusions:[...]} envelope) or an
    in-memory list. Returns [] when off. Raises ImportError only when a
    filter is requested but fidelity_exclusions is unavailable.
    """
    if not exclude:
        return []
    if isinstance(exclude, (list, tuple)):
        return list(exclude)
    if fx is None:
        raise ImportError("fidelity_exclusions unavailable; cannot apply --exclude")
    return fx.load_exclusions_json(exclude)


def apply_exclusion_filter(signal_groups, exclude):
    """Skip excluded names at entry for each {group: [signals]} mapping.

    Returns (filtered_groups_tuple, report). PIT-strict via fidelity_exclusions.
    Default-off: exclude=None returns inputs unchanged with zero skips.
    """
    names = list(signal_groups)
    if not exclude:
        return tuple(signal_groups[n] for n in names), \
            {n: {"kept": len(signal_groups[n]), "skipped_excluded": 0} for n in names}
    if fx is None:
        raise ImportError("fidelity_exclusions unavailable; cannot apply --exclude")
    excl = load_exclusions(exclude)
    out, report = [], {}
    for n in names:
        kept, skipped = fx.filter_signals(signal_groups[n], excl)
        out.append(kept)
        report[n] = {"kept": len(kept), "skipped_excluded": len(skipped)}
    return tuple(out), report


def paper_long_only(signals, exclude=None, hold_days=90, max_positions=20):
    """Tiny paper portfolio helper: long-only, fixed holds, price-free.

    Applies the optional exclusion list at entry, then tracks overlapping
    cohorts (FIFO cap). Reports counts and holding windows — not returns.
    Research only, no trading advice.
    """
    if fx is None:
        raise ImportError("fidelity_exclusions unavailable; cannot run paper_long_only")
    return fx.paper_long_only([{"ticker": s.get("ticker"),
                                "date": (s.get("date").isoformat()
                                         if isinstance(s.get("date"), dt.date) else s.get("date"))}
                               for s in (signals or [])],
                              load_exclusions(exclude) or None,
                              hold_days=hold_days, max_positions=max_positions)


def backtest_source(name, cfg, run_dir, horizon, exclude=None):
    path = run_dir / cfg["file"]
    rows = load(path)
    if not rows:
        return {"name": name, "status": "no_data"}
    # apply optional filter
    if "filter" in cfg:
        f = cfg["filter"]
        rows = [r for r in rows if r.get(list(f.keys())[0]) in list(f.values())[0]]
    # dedupe by case (forensic has replicates) or by natural key
    events = {}
    for r in rows:
        t = r.get(cfg["ticker"])
        d = parse_date(r.get(cfg["date"]))
        if not t or not d:
            continue
        key = (t, d.isoformat())
        if key in events:
            continue
        score = r.get(cfg["score"]) if cfg["score"] else None
        rf = cfg.get("redflag")
        if rf:
            rv = cfg.get("redflag_values")
            if rv is not None:
                red = r.get(rf) in rv
            else:
                red = bool(r.get(rf))
        else:
            red = None
        events[key] = {"ticker": t, "date": d, "score": score, "redflag": red}
    if not events:
        return {"name": name, "status": "no_ticker"}

    # optional Fidelity exclusion filter at entry (default off)
    n_pre_excl = len(events)
    n_skipped_excl = 0
    if exclude:
        excl = load_exclusions(exclude)
        kept = {}
        for key, e in events.items():
            if fx.is_excluded(e["ticker"], e["date"], excl) is not None:
                n_skipped_excl += 1
                continue
            kept[key] = e
        events = kept
        if not events:
            return {"name": name, "status": "all_excluded", "n": n_pre_excl}

    prices = fetch_all_prices([e["ticker"] for e in events.values()], run_dir)
    spy = prices.get("SPY")
    if not spy:
        return {"name": name, "status": "no_spy"}

    exs = []
    for e in events.values():
        trows = prices.get(e["ticker"])
        if not trows:
            continue
        ex = forward_excess(trows, spy, e["date"], horizon)
        if ex is None:
            continue
        e["excess"] = ex
        exs.append(e)

    if len(exs) < 10:
        return {"name": name, "status": "insufficient", "n": len(exs)}

    n = len(exs)
    # red-flag bucket vs baseline
    red = [e for e in exs if e["redflag"] is True]
    non = [e for e in exs if e["redflag"] is False]
    red_mean = mean([e["excess"] for e in red]) if red else 0.0
    non_mean = mean([e["excess"] for e in non]) if non else 0.0
    red_hit = sum(1 for e in red if e["excess"] < 0) / len(red) if red else 0.0

    # rank correlation for score sources
    corr = None
    top_decile_mean = None
    scored = [e for e in exs if e.get("score") is not None]
    if len(scored) >= 10:
        xs = [e["score"] for e in scored]
        ys = [e["excess"] for e in scored]
        corr = spearman(xs, ys)
        if cfg["direction"] == "short":
            corr = -corr  # positive = score predicts underperformance
        top = sorted(scored, key=lambda e: e["score"], reverse=True)[: max(1, len(scored) // 10)]
        top_decile_mean = mean([e["excess"] for e in top])

    # binomial-ish: hit rate on red flag
    return {
        "name": name,
        "status": "ok",
        "n": n,
        "n_excluded_at_entry": n_skipped_excl if exclude else 0,
        "n_red": len(red),
        "n_non_red": len(non),
        "red_mean_excess": round(red_mean * 100, 2),
        "non_red_mean_excess": round(non_mean * 100, 2),
        "spread_bps": round((red_mean - non_mean) * 10000, 1),
        "red_negative_hit_rate": round(red_hit * 100, 1),
        "score_rank_corr": round(corr, 3) if corr is not None else None,
        "top_decile_mean_excess": round(top_decile_mean * 100, 2) if top_decile_mean is not None else None,
        "direction": cfg["direction"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    ap.add_argument("--horizon", type=int, default=90)
    ap.add_argument("--source", default=None)
    ap.add_argument("--portfolio", action="store_true", help="composite + portfolio sims")
    ap.add_argument("--exclude", default=None,
                    help="optional merged exclusions JSON (fidelity_exclusions); "
                         "default off — century/sealed defaults unchanged")
    ap.add_argument("--paper-long", action="store_true",
                    help="paper-only long cohort summary (long-only, 90d holds) "
                         "from --signals JSON ([{ticker, date}]); price-free, no PnL claim")
    ap.add_argument("--signals", default=None,
                    help="signals JSON for --paper-long (list or {signals:[...]})")
    ap.add_argument("--holds", type=int, default=90,
                    help="hold days for --paper-long (default 90)")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)

    if args.portfolio:
        run_composite(run_dir, args.horizon, exclude=args.exclude)
        return

    if args.paper_long:
        if not args.signals:
            print("--paper-long requires --signals PATH")
            return
        sigs = json.loads(Path(args.signals).read_text())
        if isinstance(sigs, dict):
            sigs = sigs.get("signals", sigs.get("exclusions", []))
        print(json.dumps(paper_long_only(sigs, exclude=args.exclude,
                                         hold_days=args.holds), indent=2))
        return

    names = [args.source] if args.source else list(SOURCES.keys())
    results = []
    for name in names:
        if name not in SOURCES:
            print(f"unknown source {name}")
            continue
        print(f"\n=== backtest {name} ===")
        r = backtest_source(name, SOURCES[name], run_dir, args.horizon, exclude=args.exclude)
        results.append(r)
        print(json.dumps(r, indent=2))

    out = run_dir / "backtest_report.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nreport → {out}")


if __name__ == "__main__":
    main()