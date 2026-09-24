# Hyperscaler bond basket — short excess return tracker

Interactive, self-contained dashboard for tracking the **excess return of
shorting a basket of long-dated (10Y/30Y) hyperscaler bonds** hedged with
duration-matched Treasuries. Shorting the basket against the Treasury hedge
isolates the *credit-spread P&L*: you profit when hyperscaler spreads widen
(e.g. 2020 COVID, 2022 rates repricing, the 2025–26 AI-capex widening) and
bleed carry when they are tight.

## Files

| File | Purpose |
|------|---------|
| `bond_basket.html` | The dashboard. Fully self-contained (data embedded) — open it directly in a browser. |
| `bond_basket_data.json` | The raw dataset the dashboard embeds (for inspection / scripting). |
| `scripts/build_data.py` | Rebuilds both files. Requires only Python 3 stdlib + network (Yahoo Finance). Deterministic: issuer noise uses a stable per-name seed, so rebuilds are reproducible. |
| `scripts/app_template.html` | HTML/JS source template; `build_data.py` injects the JSON in place of `/*__BOND_DATA__*/`. |

## Rebuild

```bash
python3 scripts/build_data.py
```

## Dashboard (Figures)

* **I — The cumulative line.** Compounded short-basket excess return with regime
  bands (COVID 2020, the 2022 repricing, the 2025–26 AI-capex widening), an
  end-of-line value, overlay options, a hover/click readout, and a **drag-to-zoom
  mini-map** of the full history.
* **II — The cycles.** Two panels: the rolling 12-month (trailing 252-day)
  compounded excess as green/red bars, and the rolling 12-month **Sharpe ratio**
  (annualized mean ÷ std of daily excess over the trailing 252 days).
* **III — The levels.** Weighted basket OAS (left axis) against the 30Y Treasury
  (right axis) — the two drivers.
* **IV — The constituents.** Each issuer's OAS as a toggleable line.
* **V — Attribution.** Each name's arithmetic contribution to the selected
  window's excess (weights applied), as diverging bars.
* **VI — The construction.** Weight sliders plus a live composition bar.
* **VII — The inputs for a given day.** Day aggregates, an auto "drivers"
  narrative, and the per-bond input table.
* A stat band with mini sparklines and an auto-generated **Observation**
  paragraph that reads the selected window in plain English.
* Issuer colors are consistent across every figure.

## Methodology

* **Basket** — MSFT 4.500% ’40 · AAPL 3.850% ’43 · GOOGL 1.900% ’51 · AMZN
  2.500% ’33 · META 3.850% ’47 · NVDA 3.200% ’35 (enters 2025-04-01). Weights
  are user-adjustable in the UI (normalized daily; a name is excluded before
  its `start` date). Default weights: MSFT 25 / AAPL 20 / AMZN 20 / GOOGL 20 /
  META 15 / NVDA 0.
* **Treasury hedge** — real constant-maturity yields, daily closes from Yahoo
  Finance (`^TNX` 10Y, `^TYX` 30Y), 10y lookback.
* **Per-bond daily short excess** (credit-index / OAS-excess standard, in % of
  notional):
  * `spread P&L  = +duration × Δspread`  (short profits when spreads widen)
  * `carry       = −spread / 1e4 / 365`  (short pays the spread each day)
  * duration is the modified duration of a par bond at the current yield;
    "indicative price" is the constant-maturity clean price of the actual
    fixed-coupon issue.
* **Basket** — daily excess = weighted sum across participating issuers;
  **cumulative excess compounds daily** over the selected window.

## Data caveat — issuer spreads are a model

Treasury yields are real market data. The **issuer OAS spreads are an
ESTIMATED MODEL**: each issuer has an anchor curve (`SPREAD_ANCHORS` in
`build_data.py`) calibrated to approximate real levels at key dates, plus a
small deterministic daily wiggle. Swap the anchors for real Finra/TRACE or
Bloomberg marks to run the tracker on live data — the downstream math and the
dashboard are data-agnostic.

Research tool, not investment advice.