# Corn market data and paper execution

The basic model uses **CORN, a corn futures ETF proxy**, with 4,100 Yahoo adjusted daily closes from June 9, 2010 through September 25, 2026. Retrieval occurred September 27, 2026 at 22:52 UTC. The last quote matches the latest completed US equity session at retrieval. All expected sessions between the first and last quote are present. This is a dated snapshot, not a live quote or a claim that later forecasts have market outcomes.

A bounded search did not find a complete credential-free archive of actual December corn contracts for 2013–2023. [CME's settlement FAQ](https://www.cmegroup.com/articles/faqs/access-to-cme-group-settlement-data-faq.html) directs the former settlement files to DataMine and describes fees/login ordering. Yahoo's December 2023 symbol returned 404; December 2013 returned no historical bars. These failed probes do not establish that free data never exist. The requested contract-specific history remains an extension requiring an authentic contract identifier, expiration and settlement provenance; the code does not substitute a continuous futures symbol.

[Teucrium's primary fund facts](https://etfs.teucrium.com/CORN/fund_facts) give a June 9, 2010 inception and NYSE Arca listing. [Its product methodology](https://teucrium.com/corn) describes an unleveraged fund holding corn futures across several maturities. Its returns therefore differ from a December contract and spot corn. Fund expenses and rolling effects are already reflected in the ETF price. Current sponsor pages are saved with provenance in `futures_feasibility.json`; they are descriptive metadata, not evidence of original historical holdings.

`results/corn_model/inputs/market/manifest.json` records the exact Yahoo query, raw response checksum, derived price checksum and calendar checksum. All 2,768 overlapping 2013–2023 dates match the previously audited CORN study, within its CSV rounding (maximum absolute difference 4.85e-11 USD). That earlier study remains unchanged. Adjusted prices are from the currently retrieved vintage and may reflect later corporate-action adjustments; they are appropriate for return ratios, not a claim that an adjusted dollar price was executable at the historical close.

The saved calendar is [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) 4.13.2, XNYS, covering 2010–2027. Its US equity core-session calendar is used for NYSE Arca daily ETF closes and includes exchange holidays and early closes. The loader checks file hashes, validates session dates and close times, rejects duplicates/nonpositive/nonfinite prices or prices before the session close, and independently reconstructs the CSV from the raw Yahoo response. Explicitly missing sessions remain gaps and never receive a carried-forward price.

## Execution contract

```python
from src.corn_market_data import load_market, market_status, paper_trade
market = load_market()  # Offline; no exchange_calendars import needed.
trade = paper_trade(market, "2023-08-15T12:00:00Z", direction=1,
                    holding_sessions=20)
```

- Issue timestamps must include a timezone. Entry is the **first session on a strictly later New York calendar date**, at that session's close. Even a preopen issue waits until the next date, a conservative fixed convention.
- `holding_sessions=20` exits at the close **20 subsequent sessions after entry**; the complete quote window contains 21 sessions. Alternatively, `exit_on="YYYY-MM-DD"` chooses the last session on/before that fixed date and overrides the session count. Exit must follow entry.
- Decisions are supplied by the caller as `-1`, `0`, or `+1`; this module neither builds signals nor fits them. Exposure is one initial notional without leverage or rebalancing. An ETF short is still a borrow-dependent hypothetical position.
- Default entry and exit costs each equal 25 basis points of initial notional. Short borrow equals 3% annualized times calendar holding days/365. Cash pays no costs. Cash controls require the same valid market window as active positions.
- Every expected session in the event must have a price. Missing entry, exit or interior quotes abstain instead of moving the trade or filling data. A future/uncompleted exit returns `status="unavailable"` with dates where known and no return. An exhausted initial notional returns `status="bankrupt"`, never a clipped return that can be compounded.
- `executed` is a hypothetical completed paper event, not a broker fill. Results are JSON-safe with entry/exit dates, UTC closing timestamps, holding period, gross return, execution cost, borrow cost and net return. The caller must enforce portfolio overlap rules and keep all predictive inputs available at issue time.

The 25 bp fills and 3% borrow are transparent assumptions, not measured spreads, locates or capacity. No financing interest, tax or intraday liquidation is modeled. This helper establishes reproducible execution arithmetic; it does not establish profitable commodity trading.

## Reproduction

Offline status: `python3 src/corn_market_data.py`.

Tests: `python3 -m pytest -q tests/test_corn_market_data.py`.

Explicit network refresh: install `results/corn_model/inputs/market/refresh_requirements.txt`, then run `python3 src/corn_market_data.py --refresh`. Refresh obtains the latest daily response, excludes any uncompleted session, rebuilds the calendar and rewrites this market snapshot/manifest. It does not change prior studies. A refreshed source can revise historical prices; inspect its overlap audit and revalidate dependent outputs deliberately. The general repository requirements provide NumPy/Pandas offline; the calendar package is refresh-only.
