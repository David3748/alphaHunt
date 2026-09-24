#!/usr/bin/env python3
"""form4_history.py — full-history Form 4 open-market BUY study, 2003->2026.

Stages:
  enumerate  list every Form 4 for universe issuers via data.sec.gov submissions
  fetch      download + parse each XML; keep code-P buys >= MIN_VALUE;
             classify discretionary via 10b5-1 checkbox / footnote keywords (no LLM)
  backtest   forward-excess buckets + long-only NAV + regenerate form4.html

Usage: python3 src/form4_history.py {enumerate|fetch|backtest|run}
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import re
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab
import long_lab
from backtest_signals import parse_date, forward_excess, fetch_all_prices, START, END

RUN = ROOT / "lab_runs" / "unstructured_proto"
MIN_VALUE = 25_000.0
SAMPLE_MOD = 15  # deterministic uniform subsample: keep adsh hash % 15 == 0
PLAN_RE = re.compile(r"10b5\s*-?\s*1|pre-?\s?arranged|trading\s+plan|rule\s*10b5", re.I)

WRITE_LOCK = threading.Lock()


def load_universe():
    cases_path = ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"
    m = {}
    if cases_path.exists():
        for row in ox_lab.load_jsonl(cases_path):
            cik = str(row.get("cik", "")).zfill(10)
            t = row.get("ticker", "")
            if cik and t and cik not in m:
                m[cik] = {"ticker": t, "company": row.get("company", "")}
    # extend with company_tickers for wider coverage
    try:
        http = ox_lab.CachedHTTP(RUN / "cache" / "http", min_interval=0.0)
        data = http.json("https://www.sec.gov/files/company_tickers.json")
        for _, rec in data.items():
            cik = str(rec.get("cik_str", "")).zfill(10)
            t = rec.get("ticker", "")
            if cik and t and cik not in m:
                m[cik] = {"ticker": t, "company": rec.get("title", "")}
    except Exception as e:
        print(f"company_tickers failed ({e}); using corpus universe only")
    return m


def cmd_enumerate(universe, run_dir):
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.12)
    out_path = run_dir / "form4_enum.jsonl"
    done_ciks = set()
    if out_path.exists():
        for r in ox_lab.load_jsonl(out_path):
            done_ciks.add(r.get("cik"))
    todo = [c for c in universe if c not in done_ciks]
    print(f"{len(todo)} CIKs to enumerate ({len(done_ciks)} done)", flush=True)

    def enum_one(cik):
        found = []
        seen_acc = set()

        def harvest(d):
            forms = d.get("form", [])
            accs = d.get("accessionNumber", [])
            dates = d.get("filingDate", [])
            docs = d.get("primaryDocument", [])
            for f, a, dt_, doc in zip(forms, accs, dates, docs):
                if f == "4" and a not in seen_acc:
                    seen_acc.add(a)
                    found.append({"cik": cik, "adsh": a, "file_date": dt_,
                                  "doc": doc, "year": int(dt_[:4]) if dt_ else 0})

        # page 1: recent (nested under filings.recent)
        try:
            d = http.json(f"https://data.sec.gov/submissions/CIK{cik}.json")
            harvest(d.get("filings", {}).get("recent", {}))
        except Exception:
            pass
        # history pages: -001, -002 ... (flat)
        page = 1
        while page <= 40:
            try:
                d = http.json(f"https://data.sec.gov/submissions/CIK{cik}-submissions-{page:03d}.json")
                harvest(d)
                if len(d.get("form", [])) < 100:
                    break
            except Exception:
                break
            page += 1
        return {"cik": cik, "n": len(found), "items": found}

    count = 0
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(enum_one, c): c for c in todo}
        for fut in cf.as_completed(futures):
            res = fut.result()
            with WRITE_LOCK, out_path.open("a") as fh:
                fh.write(json.dumps(res) + "\n")
            count += 1
            if count % 200 == 0:
                print(f"  {count}/{len(todo)} CIKs", flush=True)
    total = sum(r["n"] for r in ox_lab.load_jsonl(out_path))
    print(f"enumerate done: {total} Form 4s across {count} CIKs", flush=True)


PLAN_TAGS = ("<transactionCode>",)


def parse_xml(text):
    """Return (buys_value_total, is_plan_related, footnotes_joined, officer_title)."""
    codes = re.findall(r"<transactionCode>\s*([A-Z])\s*</transactionCode>", text)
    shares = re.findall(r"<transactionShares>.*?<value>([\d.,]+)</value>", text, re.S)
    prices = re.findall(r"<transactionPricePerShare>.*?<value>([\d.,]+)</value>", text, re.S)
    footnotes = re.findall(r"<footnote[^>]*>(.*?)</footnote>", text, re.S | re.I)
    title = ""
    mt = re.search(r"<officerTitle>(.*?)</officerTitle>", text, re.S)
    if mt:
        title = ox_clean(mt.group(1))
    aff = bool(re.search(r"<aff10b5One>\s*true", text, re.I))

    def num(s):
        try:
            return float(str(s).replace(",", ""))
        except ValueError:
            return 0.0

    buy_val = 0.0
    for i, c in enumerate(codes):
        if c != "P":
            continue
        sh = num(shares[i]) if i < len(shares) else 0.0
        px = num(prices[i]) if i < len(prices) else 0.0
        buy_val += sh * px
    fn_join = ox_clean(" ".join(footnotes))
    plan = aff or bool(PLAN_RE.search(fn_join))
    return buy_val, plan, fn_join[:400], title


def ox_clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:300]


def cmd_fetch(universe, run_dir, concurrency=12):
    enum_path = run_dir / "form4_enum.jsonl"
    out_path = run_dir / "form4_hist_buys.jsonl"
    done = set()
    if out_path.exists():
        done = {r.get("adsh") for r in ox_lab.load_jsonl(out_path)}
    jobs = []
    for rec in ox_lab.load_jsonl(enum_path):
        for it in rec.get("items", []):
            adsh = it["adsh"]
            if adsh in done or it["year"] < 2003:
                continue
            info = universe.get(it["cik"])
            if not info:
                continue
            # deterministic uniform subsample to fit overnight budget
            h = int(hashlib.md5(adsh.encode()).hexdigest(), 16)
            if h % SAMPLE_MOD != 0:
                continue
            jobs.append({**it, **info})
    print(f"{len(jobs)} Form 4s to fetch/parse ({len(done)} already saved, sampled 1/{SAMPLE_MOD})", flush=True)

    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.09)

    def fetch_one(job):
        nodash = job["adsh"].replace("-", "")
        doc = job.get("doc") or ""
        doc = doc.split("/")[-1]  # strip xslF345X03/ prefix
        if not doc:
            return None
        url = f"https://www.sec.gov/Archives/edgar/data/{job['cik']}/{nodash}/{doc}"
        try:
            raw = http.get(url, timeout=45)
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return None
        buy_val, plan, ev, title = parse_xml(text)
        if buy_val < MIN_VALUE:
            return None
        return {"adsh": job["adsh"], "cik": job["cik"], "ticker": job["ticker"],
                "company": job["company"], "file_date": job["file_date"],
                "buy_value": round(buy_val), "discretionary": (not plan),
                "evidence": ev, "role": title}

    kept = 0
    scanned = 0
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(fetch_one, j): j for j in jobs}
        for fut in cf.as_completed(futures):
            scanned += 1
            res = fut.result()
            if res:
                with WRITE_LOCK:
                    ox_lab.append_jsonl(out_path, res)
                kept += 1
            if scanned % 1000 == 0:
                rate = scanned / max(time.monotonic() - t0, 1)
                eta_min = (len(jobs) - scanned) / max(rate, 0.1) / 60
                print(f"  {scanned}/{len(jobs)} scanned, {kept} buys kept, ETA {eta_min:.0f} min", flush=True)
    print(f"fetch done: {kept} buy events from {scanned} filings", flush=True)


def cmd_backtest(run_dir):
    from backtest_signals import load_forensic_cases  # noqa: F401 (keep parity)
    rows = ox_lab.load_jsonl(run_dir / "form4_hist_buys.jsonl")
    disc = [r for r in rows if r.get("discretionary")]
    print(f"{len(rows)} buys total, {len(disc)} discretionary", flush=True)
    tickers = {r["ticker"] for r in disc}
    prices = fetch_all_prices(tickers, run_dir)
    spy = prices.get("SPY", [])

    events = []
    for r in disc:
        d = parse_date(r.get("file_date"))
        if not d or r["ticker"] not in prices:
            continue
        ex = forward_excess(prices[r["ticker"]], spy, d, 90)
        if ex is None:
            continue
        events.append({"ticker": r["ticker"], "date": d.isoformat(),
                       "excess": round(ex * 100, 2), "value": r.get("buy_value"),
                       "role": r.get("role"), "evidence": r.get("evidence")})
    xs = [e["excess"] for e in events]
    print(f"events with returns: {len(events)}, mean excess {mean(xs):+.2f}%"
          if xs else "no events")

    # yearly buckets
    yrs = defaultdict(list)
    for e in events:
        yrs[e["date"][:4]].append(e["excess"])
    for y in sorted(yrs):
        ys = yrs[y]
        win = sum(1 for x in ys if x > 0) / len(ys) * 100 if ys else 0
        print(f"  {y}: n={len(ys):>4} mean={mean(ys):>+7.2f}% win={win:.0f}%")

    out = run_dir / "form4_hist_events.json"
    out.write_text(json.dumps(events, indent=1))
    print(f"saved -> {out}")


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["enumerate", "fetch", "backtest", "run"])
    ap.add_argument("--concurrency", type=int, default=12)
    args = ap.parse_args()
    run_dir = RUN
    (run_dir / "cache" / "http").mkdir(parents=True, exist_ok=True)
    uni = load_universe()
    print(f"universe: {len(uni)} issuers")
    if args.stage in ("enumerate", "run"):
        cmd_enumerate(uni, run_dir)
    if args.stage in ("fetch", "run"):
        cmd_fetch(uni, run_dir, args.concurrency)
    if args.stage in ("backtest", "run"):
        cmd_backtest(run_dir)


if __name__ == "__main__":
    main()