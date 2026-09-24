#!/usr/bin/env python3
"""alphahunt - systematic event-driven trade hunter.

Sweeps SEC EDGAR full-text search for M&A / event signals in recent filings,
pulls deal-relevant fundamentals (shares outstanding) and live prices, and
computes gross + annualized spreads so event windows can be ranked by EV.

Stdlib only. All HTTP is read-only GET with a proper User-Agent (SEC policy).

Usage:
  alphahunt scan [--days N] [--phrase "..."] [--forms 8-K,DEFM14A] [--top N]
  alphahunt price TICKER [TICKER...]
  alphahunt spread TICKER DEAL_PRICE [--close YYYY-MM-DD]
  alphahunt shares CIK_OR_TICKER
  alphahunt insiders CIK [--days N]      # open-market Form 4 buy clusters
  alphahunt activists [--days N]         # fresh SC 13D / 13D/A activist stakes
"""

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import subagents as sa

SEC_UA = "alphaHunt research contact@example.com"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:0>10}.json"
SHARES_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{:0>10}/dei/EntityCommonStockSharesOutstanding.json"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

MNA_ITEMS = {
    "2.01": "completion of acquisition",
    "5.01": "change in control",
    "1.01": "material agreement",
}


def http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- EDGAR FTS

def search_filings(phrase: str, forms: str, start: str, end: str) -> list[dict]:
    """Return normalized hits from EDGAR full-text search."""
    params = {
        "q": f'"{phrase}"',
        "forms": forms,
        "dateRange": "custom",
        "startdt": start,
        "enddt": end,
    }
    url = FTS_URL + "?" + urllib.parse.urlencode(params)
    data = http_json(url)
    out = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        names = src.get("display_names") or []
        ticker = ""
        name = names[0] if names else ""
        if len(names) > 1 and names[-1] and len(names[-1]) <= 6:
            ticker = names[-1]
        elif names and re.match(r"^[A-Z.]{1,6}$", names[0]):
            ticker = names[0]
            name = names[1] if len(names) > 1 else names[0]
        ciks = src.get("ciks") or []
        out.append(
            {
                "company": name,
                "ticker": ticker,
                "cik": str(ciks[0]) if isinstance(ciks, list) and ciks else str(ciks),
                "form": src.get("file_type") or src.get("form") or "",
                "filed": (src.get("file_date") or "")[:10],
                "items": sorted(src.get("robo_form_headers", []) or []),
            }
        )
    return out


def classify(hit: dict) -> str:
    """Rough triage of an 8-K hit into deal-risk buckets."""
    items = hit.get("items", [])
    if "2.01" in items or "5.01" in items:
        return "closed/completing"
    if hit.get("form", "").startswith("DEF"):
        return "proxy/vote pending"
    if "1.01" in items:
        return "agreement signed"
    return "other"


# ---------------------------------------------------------------- market data

def get_price(ticker: str) -> float | None:
    url = CHART_URL.format(urllib.parse.quote(ticker.upper())) + "?interval=1d&range=5d"
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode()).get("chart", {}).get("result", [])
    except urllib.error.HTTPError as exc:
        print(f"warn: price lookup failed for {ticker}: HTTP {exc.code}", file=sys.stderr)
        return None
    if not meta:
        return None
    return meta[0].get("meta", {}).get("regularMarketPrice")


def get_shares(cik_or_ticker: str) -> tuple[int | None, str]:
    """Latest dei:EntityCommonStockSharesOutstanding for a CIK."""
    cik = cik_or_ticker.strip()
    if not cik.isdigit():
        raise SystemExit("shares lookup needs a numeric CIK (see `scan` output)")
    data = http_json(SHARES_URL.format(cik))
    units = data.get("units", {}).get("shares", [])
    if not units:
        return None, ""
    latest = max(units, key=lambda u: (u.get("end", ""), u.get("filed", "")))
    return int(latest["val"]), latest.get("end", "")


# ---------------------------------------------------------------- math

def gross_spread(price: float, deal_price: float) -> float:
    """Fraction gained buying at `price` and receiving `deal_price`."""
    if price <= 0:
        raise ValueError("price must be positive")
    return deal_price / price - 1.0


def annualized(spread_frac: float, days_to_close: float) -> float:
    if days_to_close <= 0:
        raise ValueError("days_to_close must be positive")
    return (1.0 + spread_frac) ** (365.0 / days_to_close) - 1.0


def package_value(total_usd: float, shares: int) -> float:
    if shares <= 0:
        raise ValueError("shares must be positive")
    return total_usd / shares


# ---------------------------------------------------------------- commands

def cmd_scan(args) -> int:
    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)
    hits = search_filings(args.phrase, args.forms, start.isoformat(), end.isoformat())
    scored = []
    for h in hits:
        bucket = classify(h)
        h["bucket"] = bucket
        weight = {"closed/completing": 0, "proxy/vote pending": 2, "agreement signed": 3}.get(bucket, 1)
        h["rank"] = weight
        scored.append(h)
    scored.sort(key=lambda h: (-h["rank"], h["filed"]), reverse=False)
    scored.sort(key=lambda h: -h["rank"])
    for h in scored[: args.top]:
        px = get_price(h["ticker"]) if h["ticker"] else None
        px_s = f"${px:.2f}" if px else "n/a"
        print(f"{h['filed']} {h['form']:<9} {h['ticker'] or '----':<6} {px_s:>7}  {h['bucket']:<18} "
              f"cik={h['cik']:<11} {h['company'][:44]}")
    print(f"\n{len(hits)} filings matched; showing top {min(args.top, len(scored))} by deal relevance.")
    print("Next: alphahunt shares <cik>  +  alphahunt spread <ticker> <deal_price>")
    return 0


def cmd_price(args) -> int:
    for t in args.tickers:
        px = get_price(t)
        print(f"{t.upper():<8} {'$' + format(px, '.2f') if px else 'n/a'}")
    return 0


def cmd_spread(args) -> int:
    px = get_price(args.ticker)
    if px is None:
        print(f"no price for {args.ticker}", file=sys.stderr)
        return 1
    sp = gross_spread(px, args.deal_price)
    line = f"{args.ticker.upper()}  mkt=${px:.2f}  deal=${args.deal_price:.2f}  gross={sp:+.2%}"
    if args.close:
        days = (dt.date.fromisoformat(args.close) - dt.date.today()).days
        line += f"  close={args.close} ({days}d)  annualized={annualized(sp, days):+.1%}"
    print(line)
    return 0


def cmd_shares(args) -> int:
    n, asof = get_shares(args.cik)
    if n is None:
        print("no shares-outstanding data found", file=sys.stderr)
        return 1
    print(f"CIK {args.cik}: {n:,} shares outstanding as of {asof}")
    return 0


# ---------------------------------------------------------------- insider buys

def http_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def recent_form4_filings(cik: str, days: int) -> list[dict]:
    """Form 4 filings for a CIK within the last `days` days."""
    data = http_json(SUBMISSIONS_URL.format(cik))
    r = data["filings"]["recent"]
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for form, date, acc, doc in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"]):
        if form == "4" and date >= cutoff:
            acc_nodash = acc.replace("-", "")
            out.append(
                {
                    "filed": date,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}",
                }
            )
    return out


def parse_form4_buys(xml_text: str) -> list[dict]:
    """Extract open-market purchases (transaction code P) from a Form 4 XML.

    Returns entries with reporting owner, role, shares, price. Excludes
    option exercises (M), exempt/gift codes, and 10b5-1 planned trades.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    def txt(el, path):
        node = el.find(path)
        return node.text.strip() if node is not None and node.text else ""

    owner_el = root.find("reportingOwner")
    owner = txt(owner_el, "reportingOwnerId/reportingOwnerName") if owner_el is not None else ""
    is_director = txt(owner_el, "reportingOwnerRelationship/isDirector").upper() == "1"
    is_officer = txt(owner_el, "reportingOwnerRelationship/isOfficer").upper() == "1"
    officer_title = txt(owner_el, "reportingOwnerRelationship/officerTitle")
    role = ("director" if is_director else "") or ("officer:" + officer_title if is_officer else "") or "other"

    # Footnote ids referenced by 10b5-1 flag on transactions
    buys = []
    for tx in root.iter("nonDerivativeTransaction"):
        if txt(tx, "transactionCoding/transactionCode") != "P":
            continue
        f10b5 = txt(tx, "transactionCoding/transactionCodeEquation")  # rarely used
        foot_ids = [f.text for f in tx.findall(".//footnoteId") if f.text]
        shares = txt(tx, "transactionAmounts/transactionShares/value")
        price = txt(tx, "transactionAmounts/transactionPricePerShare/value")
        tdate = txt(tx, "transactionDate/value")
        if not shares:
            continue
        buys.append(
            {
                "owner": owner,
                "role": role,
                "shares": float(shares),
                "price": float(price) if price else None,
                "date": tdate,
                "footnote_ids": foot_ids,
                "_10b5_1_flag": bool(f10b5),
            }
        )
    return buys


def detect_cluster(buys: list[dict], window_days: int = 14) -> dict | None:
    """Flag when >=2 distinct insiders made open-market buys within a window."""
    if len(buys) < 2:
        return None
    owners = sorted({b["owner"] for b in buys})
    dates = sorted(b["date"] for b in buys if b["date"])
    if len(owners) < 2 or not dates:
        return None
    span = None
    try:
        ds = [dt.date.fromisoformat(d[:10]) for d in dates]
        span = (max(ds) - min(ds)).days
    except ValueError:
        return None
    if span > window_days:
        return None
    total_shares = sum(b["shares"] for b in buys)
    priced = [(b["shares"], b["price"]) for b in buys if b["price"]]
    avg_px = (sum(s * p for s, p in priced) / sum(s for s, _ in priced)) if priced else None
    return {"owners": owners, "n_buyers": len(owners), "span_days": span,
            "total_shares": total_shares, "avg_price": avg_px}


def cmd_insiders(args) -> int:
    filings = recent_form4_filings(args.cik, args.days)
    if not filings:
        print(f"no Form 4s for CIK {args.cik} in last {args.days}d")
        return 0
    all_buys = []
    for f in filings:
        try:
            xml_text = http_text(f["url"])
        except urllib.error.HTTPError as exc:
            print(f"warn: fetch failed HTTP {exc.code}", file=sys.stderr)
            continue
        all_buys.extend(parse_form4_buys(xml_text))
    if not all_buys:
        print(f"no open-market purchases (code P) in {len(filings)} Form 4s")
        return 0
    for b in all_buys:
        px_s = f"${b['price']:.2f}" if b["price"] else "?"
        val_s = f" (~${b['shares'] * b['price']:,.0f})" if b["price"] else ""
        print(f"{b['date']}  BUY  {b['owner'][:34]:<34} {b['role'][:22]:<22} "
              f"{b['shares']:>12,.0f} sh @ {px_s}{val_s}")
    cluster = detect_cluster(all_buys)
    if cluster:
        print(f"\n*** CLUSTER: {cluster['n_buyers']} distinct insiders bought within "
              f"{cluster['span_days']}d, {cluster['total_shares']:,.0f} shares total"
              + (f", avg ${cluster['avg_price']:.2f}" if cluster["avg_price"] else ""))
    return 0


def load_watchlist(path: str) -> list[str]:
    """One CIK per line; # comments (full-line or inline) and blanks ignored."""
    out = []
    with open(path) as fh:
        for line in fh:
            entry = line.split("#", 1)[0].strip()
            if entry and entry.isdigit():
                out.append(entry)
    return out


def recent_submissions_by_form(cik: str, days: int) -> dict[str, list[dict]]:
    """Group a CIK's recent filings by form type."""
    data = http_json(SUBMISSIONS_URL.format(cik))
    r = data["filings"]["recent"]
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    grouped: dict[str, list[dict]] = {}
    for form, date, acc, doc in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"]):
        if date >= cutoff:
            acc_nodash = acc.replace("-", "")
            grouped.setdefault(form, []).append(
                {"filed": date,
                 "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"}
            )
    return grouped


def cmd_activists(args) -> int:
    """Watchlist-based activist/ownership monitor (FTS does not index
    ownership forms, and daily index files are access-restricted)."""
    ciks = load_watchlist(args.file)
    if not ciks:
        print("watchlist empty - put one CIK per line in the file", file=sys.stderr)
        return 1
    own_forms = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
    alerts = 0
    for cik in ciks:
        try:
            grouped = recent_submissions_by_form(cik, args.days)
        except Exception as exc:
            print(f"warn: CIK {cik}: {exc}", file=sys.stderr)
            continue
        hits = [(f, rows) for f, rows in grouped.items() if f in own_forms]
        if not hits:
            continue
        name = ""
        try:
            name = http_json(SUBMISSIONS_URL.format(cik)).get("name", "")
        except Exception:
            pass
        print(f"\n=== {name or 'CIK ' + cik} ({cik}) ===")
        for f, rows in sorted(hits):
            for row in rows:
                print(f"  {row['filed']} {f:<10} {row['url']}")
                alerts += 1
    print(f"\n{alerts} ownership-form events across {len(ciks)} watchlist names in {args.days}d.")
    return 0


# ---------------------------------------------------------------- market-wide sweep

UNIVERSE_URL = "https://www.sec.gov/files/company_tickers.json"


def build_universe(path: str = "data/universe.json") -> int:
    """Download SEC's full list of ~11k exchange-listed filers with tickers."""
    data = http_json(UNIVERSE_URL)
    rows = []
    for v in data.values():
        rows.append({"ticker": v["ticker"], "name": v["title"], "cik": str(v["cik_str"])})
    json.dump(rows, open(path, "w"))
    return len(rows)


def signal_score(quote: dict) -> tuple[int, list[str]]:
    """Deterministic event-signal score for one quote. Higher = more interesting."""
    flags, score = [], 0
    px, hi, lo = quote.get("price"), quote.get("hi52"), quote.get("lo52")
    if not px or px <= 0:
        return 0, ["no price"]
    if hi and lo and hi > lo:
        pos = (px - lo) / (hi - lo)          # 0 = at 52w low
        drawdown = 1 - px / hi               # fraction off the high
        if pos < 0.08:
            score += 3; flags.append(f"at 52w low ({pos:.0%} of range)")
        elif pos < 0.20:
            score += 2; flags.append("near 52w low")
        if drawdown > 0.60:
            score += 3; flags.append(f"-{drawdown:.0%} off high")
        elif drawdown > 0.40:
            score += 1; flags.append(f"-{drawdown:.0%} off high")
    if px < 1.0:
        score += 1; flags.append("sub-$1 (shell/going-concern zone)")
    return score, flags


def fetch_quote(ticker: str) -> dict | None:
    url = CHART_URL.format(urllib.parse.quote(ticker)) + "?interval=1d&range=1y"
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode()).get("chart", {}).get("result", [])
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None
    if not res:
        return None
    meta = res[0].get("meta", {})
    return {"price": meta.get("regularMarketPrice"),
            "hi52": meta.get("fiftyTwoWeekHigh"),
            "lo52": meta.get("fiftyTwoWeekLow")}


def cmd_sweep(args) -> int:
    universe = json.load(open(args.universe))
    # optional liquidity/price gates to keep request count sane
    if args.limit:
        universe = universe[:args.limit]
    print(f"sweeping {len(universe)} tickers at concurrency {args.concurrency}...", file=sys.stderr)
    results, done = [], 0

    def work(row):
        q = fetch_quote(row["ticker"])
        if not q:
            return None
        score, flags = signal_score({**q, **row})
        return {**row, **q, "score": score, "flags": "; ".join(flags)}

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for res in pool.map(work, universe):
            done += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(universe)}", file=sys.stderr)
            if res and res["score"] > 0:
                results.append(res)

    results.sort(key=lambda r: -r["score"])
    json.dump(results, open(args.out, "w"))
    print(f"{len(results)} flagged (score>0) of {done} swept; written to {args.out}")
    for r in results[: args.top]:
        print(f"  {r['ticker']:<6} ${r['price'] if r['price'] else 0:>8.2f}  score={r['score']}  {r['flags']}")
    return 0


# ---------------------------------------------------------------- deep hunt (LLM swarm)

def _smart_excerpt(txt: str) -> str:
    """Pick the window of filing text most likely to contain deal terms."""
    patterns = [
        r"\$[\d,.]+( in cash)?(,)? without interest",
        r"per share in cash",
        r"\$[\d,.]+ per share",
        r"Agreement and Plan of Merger",
        r"Contingent Value Right",
    ]
    for pat in patterns:
        m = re.search(pat, txt)
        if m:
            i = m.start()
            return txt[max(0, i - 1200): i + 10800]
    return txt[:12000]


DEAL_TERM_RE = re.compile(
    r"(\$[\d,.]+ per share|per share in cash|Agreement and Plan of Merger|"
    r"Contingent Value Right|exchange ratio|termination fee)", re.I
)


def _fetch_filing_text(ctx: dict) -> str:
    """Deterministic evidence pack: fetch up to 3 recent filing docs and keep
    whichever contains the densest deal-terms language."""
    cik = ctx.get("cik")
    if not cik:
        return ""
    grouped = recent_submissions_by_form(str(cik), days=21)
    urls = []
    for form in ("DEFM14A", "S-4", "8-K", "DEF 14A", "PREM14A"):
        for row in grouped.get(form, [])[:1]:
            urls.append(row["url"])
    best = ""
    for url in urls[:3]:
        try:
            raw = http_text(url)
        except urllib.error.HTTPError:
            continue
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
        excerpt = _smart_excerpt(txt)
        score = len(DEAL_TERM_RE.findall(excerpt)) * 1000 + min(len(excerpt), 12000) // 10
        if score > len(DEAL_TERM_RE.findall(best)) * 1000 or not best:
            best = excerpt
        if DEAL_TERM_RE.search(best) and len(best) > 5000:
            break
    return best


def _get_market_data(ctx: dict) -> dict:
    ticker = ctx.get("ticker")
    data = {"ticker": ticker, "cik": ctx.get("cik"), "company": ctx.get("company")}
    if ticker:
        px = get_price(ticker)
        if px:
            data["last_price"] = px
        shares = None
        try:
            shares, asof = get_shares(str(ctx["cik"]))
            if shares:
                data["shares_outstanding"] = shares
                data["market_cap_usd"] = int(shares * px) if px else None
        except Exception:
            pass
    return data


def cmd_deep(args) -> int:
    api_key = sa.get_api_key(args.api_key)
    client = sa.OpenRouter(api_key, model=args.model)

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)
    hits = search_filings(args.phrase, args.forms, start.isoformat(), end.isoformat())
    seen, candidates = set(), []
    for h in hits:
        key = h["cik"]
        if key in seen or not key:
            continue
        seen.add(key)
        bucket = classify(h)
        if bucket == "other":
            continue
        candidates.append(h)

    # merge quant-flagged names from a full-market sweep
    if args.from_sweep:
        swept = json.load(open(args.from_sweep))
        for row in swept[:args.top_sweep]:
            if row["cik"] in seen:
                continue
            seen.add(row["cik"])
            candidates.append({
                "ticker": row["ticker"], "company": row["name"], "cik": row["cik"],
                "form": "quant", "filed": "", "items": [],
                "bucket": f"quant flag: {row['flags']}",
            })

    candidates = candidates[: args.max_candidates]

    print(f"deep hunt: {len(candidates)} candidates x {len(sa.ROLES)} subagent roles "
          f"(model={args.model}, concurrency={args.concurrency})", file=sys.stderr)
    results = sa.deep_hunt(
        candidates, client,
        fetch_filing_text=_fetch_filing_text,
        get_market_data=_get_market_data,
        concurrency=args.concurrency,
    )
    ranked = sa.rank_results(results)

    lines = ["# alphahunt deep report", "",
             f"model={args.model} calls={client.calls} "
             f"tokens(p/c)={client.total_prompt_tokens}/{client.total_completion_tokens}", ""]
    for r in ranked:
        sk = r.get("skeptic") or {}
        th = r.get("thesis") or {}
        terms = r.get("terms") or {}
        lines.append(f"## {r.get('ticker') or r.get('company') or r.get('cik')} — {r.get('company', '')}")
        if r.get("error"):
            lines.append(f"- ERROR: {r['error']}")
        cons = terms.get("consideration") or {}
        lines.append(f"- event: {terms.get('event_type')} / counterparty: {terms.get('acquirer_or_counterparty')}")
        lines.append(f"- consideration: cash={cons.get('cash_per_share')} ratio={cons.get('stock_exchange_ratio')}")
        lines.append(f"- skeptic: {sk.get('verdict')} | p(complete)={sk.get('probability_deal_completes_pct')}% | kill: {sk.get('biggest_kill_factor')}")
        lines.append(f"- thesis: {th.get('direction')} via {th.get('structure')} | sizing: {th.get('position_sizing_hint')}")
        lines.append(f"- EV note: {th.get('expected_value_note')}")
        lines.append("")
    report = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(report)
    print(report)
    print(f"[written to {args.out}]", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- investigate (forensic swarm)

def _company_docs(cik: str, days: int = 365, max_docs: int = 8) -> list[dict]:
    """Collect full text of the most substantive recent filings for a CIK."""
    grouped = recent_submissions_by_form(str(cik), days)
    picks = []
    for form in ("10-K", "10-Q", "20-F", "6-K", "DEFM14A", "DEF 14A", "8-K", "S-1", "F-1"):
        for row in grouped.get(form, []):
            picks.append((form, row))
    picks.sort(key=lambda t: t[1]["filed"], reverse=True)
    docs = []
    for form, row in picks[:max_docs]:
        try:
            raw = http_text(row["url"])
        except urllib.error.HTTPError:
            continue
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
        if len(txt) < 500:
            continue
        docs.append({"label": f"{form} {row['filed']}", "text": txt[:60000],
                     "filed": row["filed"]})
    return docs


def _clinical_trials(company: str) -> str | None:
    """Registered trials from ClinicalTrials.gov API v2, if any."""
    params = urllib.parse.urlencode({
        "query.intr": company.replace(" ", "+"),
        "filter.overallStatus": "RECRUITING|ACTIVE_NOT_RECRUITING|COMPLETED|TERMINATED",
        "pageSize": "20",
        "fields": "NCTId|BriefTitle|OverallStatus|Condition|EnrollmentCount|StartDate",
    })
    url = f"https://clinicaltrials.gov/api/v2/studies?{params}"
    try:
        data = http_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None
    studies = data.get("studies", [])
    if not studies:
        return None
    out = []
    for s in studies:
        p = s.get("protocolSection", {})
        ident = p.get("identificationModule", {})
        status = p.get("statusModule", {})
        out.append({
            "nct": ident.get("nctId"),
            "title": ident.get("briefTitle"),
            "status": status.get("overallStatus"),
            "start": status.get("startDateStruct", {}).get("date"),
        })
    return json.dumps(out)


def cmd_investigate(args) -> int:
    api_key = sa.get_api_key(args.api_key)
    client = sa.OpenRouter(api_key, model=args.model)

    cik = args.cik
    if not cik.isdigit():
        # resolve ticker via SEC ticker file
        data = http_json("https://www.sec.gov/files/company_tickers.json")
        cik = ""
        for v in data.values():
            if v["ticker"].upper() == args.cik.upper():
                cik = str(v["cik_str"])
                break
        if not cik:
            print(f"unknown ticker {args.cik}", file=sys.stderr)
            return 1

    name = http_json(SUBMISSIONS_URL.format(cik)).get("name", args.cik)
    print(f"investigating {name} (CIK {cik})...", file=sys.stderr)
    docs = _company_docs(cik, days=args.days, max_docs=args.max_docs)
    if not docs:
        print("no substantive filings found", file=sys.stderr)
        return 1
    print(f"fetched {len(docs)} docs: {[d['label'] for d in docs]}", file=sys.stderr)

    registry = _clinical_trials(name) if args.trials else None
    if registry:
        print("registered trials found - adding trials auditor", file=sys.stderr)

    result = sa.investigate(
        [{**d, "company": name} for d in docs], client,
        registry_json=registry,
        claims_text=" ".join(d["text"][:3000] for d in docs[:3]),
        concurrency=args.concurrency,
    )

    lines = [f"# Forensic dossier: {name}", "",
             f"model={args.model} calls={client.calls} "
             f"tokens(p/c)={client.total_prompt_tokens}/{client.total_completion_tokens}", ""]
    syn = result.get("synthesis", {})
    lines.append(f"## Verdict: {syn.get('verdict', '?').upper()}")
    lines.append(f"**{syn.get('headline', '')}**\n")
    for f in syn.get("top_findings", []):
        lines.append(f"- {f}")
    lines.append(f"\nRed flags: {syn.get('red_flag_count', '?')}")
    lines.append(f"Trade implication: {syn.get('trade_implication', 'n/a')}")
    lines.append("\n### Verify next")
    for v in syn.get("what_to_verify_next", []):
        lines.append(f"- {v}")
    lines.append("\n### All findings by document")
    for f in result.get("all_findings", []):
        lines.append(f"- **[{f.get('severity', '?')}]** {f.get('doc')}: "
                     f"{f.get('why_it_matters') or f.get('gap') or f.get('quote', '')[:200]}")
    report = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(report)
    print(report)
    print(f"\n[written to {args.out}]", file=sys.stderr)
    return 0


def cmd_batch(args) -> int:
    """Run investigate over many companies with resume support."""
    import os
    import time
    api_key = sa.get_api_key(args.api_key)
    client = sa.OpenRouter(api_key, model=args.model)

    swept = json.load(open(args.from_sweep))
    rows = [r for r in swept
            if r.get("price", 999) <= args.max_price]
    if args.shards > 1:
        rows = [r for i, r in enumerate(rows) if i % args.shards == args.shard]
    rows = rows[: args.max_companies]
    os.makedirs(args.dir, exist_ok=True)
    print(f"batch: {len(rows)} companies -> {args.dir}/", file=sys.stderr)

    t0 = time.time()
    done_before = len([f for f in os.listdir(args.dir) if f.endswith(".md")])
    for i, row in enumerate(rows):
        out_path = os.path.join(args.dir, f"{row['ticker']}.md")
        if os.path.exists(out_path):
            continue
        try:
            name = http_json(SUBMISSIONS_URL.format(row["cik"])).get("name", "")
            docs = _company_docs(row["cik"], days=args.days, max_docs=args.max_docs)
            if not docs:
                open(out_path, "w").write(f"# {name}: no substantive filings\n")
                continue
            result = sa.investigate([{**d, "company": name} for d in docs], client,
                                    concurrency=args.concurrency)
            syn = result.get("synthesis", {})
            lines = [f"# {name} ({row['ticker']}) - ${row['price']} | score={row['score']}",
                     f"swept flags: {row['flags']}", "",
                     f"## Verdict: {str(syn.get('verdict', '?')).upper()}",
                     f"**{syn.get('headline', '')}**\n"]
            for f in syn.get("top_findings", []):
                lines.append(f"- {f}")
            lines.append(f"\nRed flags: {syn.get('red_flag_count', '?')}")
            lines.append(f"Trade implication: {syn.get('trade_implication', 'n/a')}")
            lines.append("\n### Findings")
            for f in result.get("all_findings", []):
                lines.append(f"- **[{f.get('severity', '?')}]** {f.get('doc')}: "
                             f"{(f.get('why_it_matters') or f.get('gap') or f.get('quote', ''))[:220]}")
            open(out_path, "w").write("\n".join(lines))
        except KeyboardInterrupt:
            print("interrupted - progress saved, rerun to resume", file=sys.stderr)
            return 130
        except Exception as exc:
            open(out_path, "w").write(f"# ERROR: {exc}\n")
            print(f"warn: {row['ticker']}: {exc}", file=sys.stderr)
        elapsed = time.time() - t0
        done_now = len([f for f in os.listdir(args.dir) if f.endswith(".md")])
        rate = done_now / max(elapsed, 1) * 3600
        print(f"[{i+1}/{len(rows)}] {row['ticker']:<6} ok "
              f"({done_now} total, ~{rate:.0f}/hr, "
              f"{len(rows)-i-1} left, eta {(len(rows)-i-1)/max(rate,0.01):.1f}h)",
              file=sys.stderr)
    print(f"batch complete: {args.dir}/", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="alphahunt", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="EDGAR full-text sweep for event signals")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--phrase", default="merger agreement")
    s.add_argument("--forms", default="8-K,DEFM14A")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("price", help="live last price via Yahoo chart API")
    s.add_argument("tickers", nargs="+")
    s.set_defaults(func=cmd_price)

    s = sub.add_parser("spread", help="gross/annualized arb spread vs deal price")
    s.add_argument("ticker")
    s.add_argument("deal_price", type=float)
    s.add_argument("--close", help="expected close date YYYY-MM-DD")
    s.set_defaults(func=cmd_spread)

    s = sub.add_parser("shares", help="latest shares outstanding from SEC XBRL")
    s.add_argument("cik")
    s.set_defaults(func=cmd_shares)

    s = sub.add_parser("insiders", help="open-market Form 4 buy cluster check")
    s.add_argument("cik")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(func=cmd_insiders)

    s = sub.add_parser("activists", help="watchlist monitor for SC 13D/G ownership events")
    s.add_argument("--file", default="data/watchlist.txt")
    s.add_argument("--days", type=int, default=14)
    s.set_defaults(func=cmd_activists)

    s = sub.add_parser("deep", help="LLM subagent swarm over event candidates (OpenRouter)")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--phrase", default="merger agreement")
    s.add_argument("--forms", default="8-K,DEFM14A")
    s.add_argument("--from-sweep", dest="from_sweep", default=None,
                   help="merge top quant-flagged names from sweep-results.json")
    s.add_argument("--top-sweep", type=int, default=25, help="how many swept names to merge")
    s.add_argument("--max-candidates", type=int, default=10)
    s.add_argument("--concurrency", type=int, default=16)
    s.add_argument("--model", default=sa.DEFAULT_MODEL)
    s.add_argument("--api-key", default=None, help="or set OPENROUTER_API_KEY")
    s.add_argument("--out", default="reports/deep-report.md")
    s.set_defaults(func=cmd_deep)

    s = sub.add_parser("universe", help="download full SEC ticker universe (~11k)")
    s.add_argument("--out", default="data/universe.json")
    s.set_defaults(func=lambda a: print(f"universe: {build_universe(a.out)} tickers -> {a.out}") or 0)

    s = sub.add_parser("sweep", help="deterministic signal sweep across the whole market")
    s.add_argument("--universe", default="data/universe.json")
    s.add_argument("--limit", type=int, default=None, help="only first N tickers (testing)")
    s.add_argument("--concurrency", type=int, default=48)
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--out", default="data/sweep-results.json")
    s.set_defaults(func=cmd_sweep)

    s = sub.add_parser("investigate", help="forensic swarm dossier on one company")
    s.add_argument("cik", help="CIK or ticker")
    s.add_argument("--days", type=int, default=365)
    s.add_argument("--max-docs", type=int, default=8)
    s.add_argument("--concurrency", type=int, default=16)
    s.add_argument("--trials", action="store_true", help="cross-check ClinicalTrials.gov")
    s.add_argument("--model", default=sa.DEFAULT_MODEL)
    s.add_argument("--api-key", default=None, help="or set OPENROUTER_API_KEY")
    s.add_argument("--out", default="reports/investigate-report.md")
    s.set_defaults(func=cmd_investigate)

    s = sub.add_parser("batch", help="run investigate over many swept companies, resumable")
    s.add_argument("--from-sweep", dest="from_sweep", default="data/sweep-results.json")
    s.add_argument("--max-price", type=float, default=5.0, help="micro-cap price gate")
    s.add_argument("--max-companies", type=int, default=100)
    s.add_argument("--days", type=int, default=365)
    s.add_argument("--max-docs", type=int, default=6)
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--dir", default="dossiers")
    s.add_argument("--shards", type=int, default=1, help="split work across N processes")
    s.add_argument("--shard", type=int, default=0, help="this process's shard index")
    s.add_argument("--model", default=sa.DEFAULT_MODEL)
    s.add_argument("--api-key", default=None)
    s.set_defaults(func=cmd_batch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
