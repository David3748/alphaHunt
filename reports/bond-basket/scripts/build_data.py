#!/usr/bin/env python3
"""Build the long-dated hyperscaler bond basket dataset + dashboard.

Methodology
-----------
The dashboard tracks the excess return of SHORTING a basket of long-dated
(10Y/30Y) bonds issued by the hyperscalers (MSFT, AAPL, AMZN, GOOGL, META,
NVDA) against a duration-matched Treasury hedge.  Shorting the basket while
long the hedge isolates the *credit-spread* P&L: you profit when the basket's
spreads widen and lose when they tighten.

Data:
  * Treasury constant-maturity yields (10Y, 30Y) -- REAL market data, pulled
    daily from Yahoo Finance (^TNX, ^TYX), 10y lookback.
  * Issuer credit spreads (OAS in bps over the matched Treasury tenor) --
    ESTIMATED.  Precise per-issue historical OAS is not freely available, so
    each issuer carries an anchor curve (documented points in BOND_SPECS,
    calibrated to approximate real levels at key dates such as the 2020
    COVID spike, the 2022 rates repricing and the 2025-26 AI-capex widening)
    plus a small deterministic daily noise.  Swap these anchors for real
    Finra/TRACE or Bloomberg data to run this with live marks.

Per-bond daily arithmetic (credit-index / OAS excess standard):
  * yield_t   = treasury_t(tenor) + spread_t / 1e4
  * duration  = modified duration of a par bond at yield_t (constant maturity)
  * indicative price = clean price of the fixed-coupon issue at yield_t
  * short-side daily excess return = spread P&L + carry:
      spread P&L = +duration x (spread change)      (short profits when spreads widen)
      carry      = -spread / 1e4 / 365              (short pays the spread each day)
  * basket daily excess = weighted sum over issuers; cumulative compounds daily.

Outputs
-------
  * bond-basket/bond_basket_data.json   -- raw dataset (dates, treasuries, bonds)
  * bond-basket/bond_basket.html        -- self-contained interactive dashboard
"""

import json
import math
import os
import random
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DATA = os.path.join(ROOT, "bond_basket_data.json")
OUT_HTML = os.path.join(ROOT, "bond_basket.html")
TEMPLATE = os.path.join(HERE, "app_template.html")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) alphaHunt/bond-basket"
D2 = timedelta(days=1)

# --- Bond universe ----------------------------------------------------------
# tenor: "10Y" or "30Y"  -> matched against ^TNX / ^TYX
# start:  first date the bond participates in the basket (issuance)
BOND_SPECS = {
    "MSFT":  dict(name="Microsoft Corp",          coupon=4.500, maturity="2040-02-01", tenor="30Y", start="2016-09-01", rating="AA"),
    "AAPL":  dict(name="Apple Inc",               coupon=3.850, maturity="2043-08-04", tenor="30Y", start="2016-09-01", rating="AA+"),
    "GOOGL": dict(name="Alphabet Inc",            coupon=1.900, maturity="2051-08-15", tenor="30Y", start="2016-09-01", rating="AA-"),
    "AMZN":  dict(name="Amazon.com Inc",          coupon=2.500, maturity="2033-09-01", tenor="10Y", start="2016-09-01", rating="AA-"),
    "META":  dict(name="Meta Platforms Inc",      coupon=3.850, maturity="2047-08-15", tenor="30Y", start="2016-09-01", rating="A+"),
    "NVDA":  dict(name="NVIDIA Corp",             coupon=3.200, maturity="2035-04-10", tenor="10Y", start="2025-04-01", rating="AA-"),
}

DEFAULT_WEIGHTS = {  # sum to 1 over the participating set
    "MSFT": 0.25, "AAPL": 0.20, "AMZN": 0.20, "GOOGL": 0.20, "META": 0.15, "NVDA": 0.00,
}

# Anchor (date, OAS in bps) curves -- ESTIMATED, see module docstring.
SPREAD_ANCHORS = {
    "MSFT":  [("2016-09-01", 95), ("2018-01-01", 90), ("2020-02-14", 80), ("2020-03-23", 175),
              ("2020-06-30", 95), ("2021-06-30", 60), ("2022-06-30", 120), ("2022-10-31", 135),
              ("2023-12-31", 70), ("2024-12-31", 55), ("2025-06-30", 75), ("2026-08-31", 88)],
    "AAPL":  [("2016-09-01", 85), ("2018-01-01", 80), ("2020-02-14", 72), ("2020-03-23", 160),
              ("2020-06-30", 88), ("2021-06-30", 55), ("2022-06-30", 112), ("2022-10-31", 125),
              ("2023-12-31", 62), ("2024-12-31", 48), ("2025-06-30", 68), ("2026-08-31", 80)],
    "GOOGL": [("2016-09-01", 90), ("2018-01-01", 85), ("2020-02-14", 76), ("2020-03-23", 165),
              ("2020-06-30", 92), ("2021-06-30", 58), ("2022-06-30", 115), ("2022-10-31", 130),
              ("2023-12-31", 66), ("2024-12-31", 52), ("2025-06-30", 72), ("2026-08-31", 84)],
    "META":  [("2016-09-01", 115), ("2018-01-01", 115), ("2020-02-14", 100), ("2020-03-23", 210),
              ("2020-06-30", 130), ("2021-06-30", 85), ("2022-06-30", 175), ("2022-10-31", 195),
              ("2023-12-31", 95), ("2024-12-31", 80), ("2025-06-30", 105), ("2026-08-31", 118)],
    "AMZN":  [("2016-09-01", 95), ("2018-01-01", 92), ("2020-02-14", 82), ("2020-03-23", 185),
              ("2020-06-30", 100), ("2021-06-30", 68), ("2022-06-30", 122), ("2022-10-31", 140),
              ("2023-12-31", 72), ("2024-12-31", 58), ("2025-06-30", 82), ("2026-08-31", 96)],
    "NVDA":  [("2025-04-01", 78), ("2025-06-30", 85), ("2025-12-31", 75), ("2026-08-31", 88)],
}

# --- Treasury fetching ------------------------------------------------------
TENORS = {"10Y": "^TNX", "30Y": "^TYX"}


def fetch_yahoo(symbol: str) -> tuple[list[date], list[float]]:
    end = date.today()
    start = end - timedelta(days=10 * 366)
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?period1={p1}&period2={p2}&interval=1d&events=div")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out: list[tuple[date, float]] = []
    for t, c in zip(ts, closes):
        if c is None or (isinstance(c, float) and math.isnan(c)):
            continue
        d = datetime.fromtimestamp(t, timezone.utc).date()
        out.append((d, float(c)))
    out.sort()
    return [d for d, _ in out], [c for _, c in out]


def interpolate_spread(dates: list[date], anchors: list[tuple[str, float]]) -> list[float]:
    pts = [(datetime.strptime(a, "%Y-%m-%d").date(), float(v)) for a, v in anchors]
    vals = []
    for d in dates:
        if d <= pts[0][0]:
            vals.append(pts[0][1])
            continue
        if d >= pts[-1][0]:
            vals.append(pts[-1][1])
            continue
        for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
            if d0 <= d <= d1:
                span = (d1 - d0).days or 1
                vals.append(v0 + (v1 - v0) * (d - d0).days / span)
                break
    return vals


def stable_seed(sym: str) -> int:
    """Deterministic per-issuer seed (Python's built-in hash() is randomized
    per process, which would change the noise on every rebuild)."""
    return (sum(ord(c) * (i + 1) for i, c in enumerate(sym)) * 2654435761) % 2**31


def smooth_noise(n: int, seed: int, sigma: float = 0.7, clip: float = 5.0) -> list[float]:
    """Small, slowly mean-reverting wiggle around the anchor path, so daily
    spread moves stay realistic (a few tenths of a bp/day) instead of
    dominating the macro anchor signal."""
    rng = random.Random(seed)
    out = []
    x = 0.0
    for _ in range(n):
        x = 0.995 * x + rng.gauss(0, sigma)
        x = max(-clip, min(clip, x))
        out.append(x)
    return out


def bond_price(y: float, coupon: float, tenor: int) -> float:
    """Constant-maturity proxy: clean price of a bond with `tenor` years left
    and an annual coupon of `coupon` dollars per $100 face."""
    disc = [(1.0 + y) ** k for k in range(1, tenor + 1)]
    clean = coupon * sum(1.0 / d for d in disc) + 100.0 / disc[-1]
    return clean


def main() -> None:
    print("Fetching Treasury yields from Yahoo Finance ...")
    raw = {}
    for tenor, sym in TENORS.items():
        ds, ys = fetch_yahoo(sym)
        raw[tenor] = (ds, ys)
        print(f"  {tenor} ({sym}): {ds[0]} -> {ds[-1]}  ({len(ds)} points)")

    base_dates = [d for d in raw["10Y"][0] if d in set(raw["30Y"][0])]
    treas = {}
    for tenor in TENORS:
        dmap = dict(zip(raw[tenor][0], raw[tenor][1]))
        treas[tenor] = [dmap[d] for d in base_dates]
        # backfill the first NaN with previous non-null
        for i, v in enumerate(treas[tenor]):
            if v is None:
                j = i - 1
                while j >= 0 and treas[tenor][j] is None:
                    j -= 1
                treas[tenor][i] = treas[tenor][j] if j >= 0 else 2.0
        treas[tenor] = [float(v) if v is not None else 2.0 for v in treas[tenor]]

    print(f"Common trading grid: {base_dates[0]} -> {base_dates[-1]}  ({len(base_dates)} days)")

    # --- per-bond series ---------------------------------------------------
    bonds = {}
    for sym, spec in BOND_SPECS.items():
        tenor = spec["tenor"]
        n = int(tenor.replace("Y", ""))
        anchors = SPREAD_ANCHORS[sym]
        spread = interpolate_spread(base_dates, anchors)
        spread = [s + nse for s, nse in zip(spread, smooth_noise(len(spread), seed=stable_seed(sym)))]
        start_i = next((i for i, d in enumerate(base_dates) if d >= datetime.strptime(spec["start"], "%Y-%m-%d").date()), 0)
        ys, pr, dur = [], [], []
        dspread, pnl, carry, excess = [], [], [], []
        prev_sp = None
        for i, (d, sp) in enumerate(zip(base_dates, spread)):
            ty = treas[tenor][i]
            y_pct = ty + sp / 1e4            # yield in percent
            y = y_pct / 100.0                # decimal fraction
            # indicative price of the actual fixed-coupon issue (constant-maturity proxy)
            p = bond_price(y, spec["coupon"], n)
            # modified duration of a par bond at the current yield (credit-index standard)
            du = modified_duration(y, y_pct, n)
            ds = 0.0 if prev_sp is None else sp - prev_sp
            # daily short-side OAS excess, in percent of notional:
            #   spread P&L  = +duration * (spread change)  (short profits when spreads widen)
            #   carry       = -spread / 1e4 / 365          (short pays the spread each day)
            spread_pnl = du * (ds / 1e4) * 100.0
            carry_d    = -(sp / 1e4) / 365.0 * 100.0
            ys.append(y_pct); pr.append(p); dur.append(du)
            dspread.append(ds); pnl.append(spread_pnl)
            carry.append(carry_d); excess.append(spread_pnl + carry_d)
            prev_sp = sp
        for k in range(start_i):
            spread[k] = None
            ys[k] = None
            pr[k] = None
            dur[k] = None
            dspread[k] = None
            pnl[k] = 0.0
            carry[k] = 0.0
            excess[k] = 0.0
        bonds[sym] = {
            "name": spec["name"], "coupon": spec["coupon"], "maturity": spec["maturity"],
            "tenor": tenor, "rating": spec["rating"], "start": spec["start"],
            "startIndex": start_i,
            "spread": spread, "yield": ys, "price": pr, "duration": dur,
            "dSpread": dspread, "spreadPnl": pnl, "carry": carry, "excess": excess,
        }

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "treasury": "Yahoo Finance ^TNX (10Y), ^TYX (30Y) daily closes",
            "spreads": "ESTIMATED model (see build_data.py) - anchors + seeded noise",
        },
        "start": str(base_dates[0]), "end": str(base_dates[-1]),
        "dates": [str(d) for d in base_dates],
        "treasuries": treas,
        "bonds": bonds,
        "defaultWeights": DEFAULT_WEIGHTS,
        "method": (
            "Short basket vs duration-matched Treasury, measured as credit-index "
            "OAS excess. Daily short excess per bond = -duration x (spread change) "
            "minus spread carry (spread/1e4/365). Basket daily excess is the weighted "
            "sum across issuers; cumulative excess compounds daily."
        ),
    }

    with open(OUT_DATA, "w") as fh:
        json.dump(payload, fh)

    with open(TEMPLATE, "r") as fh:
        html = fh.read()
    html = html.replace("/*__BOND_DATA__*/", json.dumps(payload))
    with open(OUT_HTML, "w") as fh:
        fh.write(html)

    print(f"Wrote {OUT_DATA} ({os.path.getsize(OUT_DATA)//1024} KB)")
    print(f"Wrote {OUT_HTML} ({os.path.getsize(OUT_HTML)//1024} KB)")


def modified_duration(y: float, coupon: float, n: int) -> float:
    if y <= 0:
        return n
    num = 0.0
    den = 0.0
    for k in range(1, n + 1):
        df = (1 + y) ** k
        cf = coupon if k < n else coupon + 100.0
        num += k * cf / df
        den += cf / df
    mac = num / den
    return mac / (1 + y)


if __name__ == "__main__":
    main()