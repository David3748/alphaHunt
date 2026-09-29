Fixed CORN ETF trading diagnostic
================================

This is separate from the county-yield forecast test. Its signals are hypothetical
historical signals made from today's revised data and a static crop map; it does
not certify an operational strategy or trading alpha. Both declared horizons are
reported, with August 15 primary and September 15 secondary. September was frozen
before the first county model fit and before any forecast/price join.

The primary August test has 11 annual events. After 50 bp roundtrip execution costs
and 3% annual short borrow, the satellite strategy returned **+6.69% compounded
across events**, versus **−2.36%** for the weather strategy. Satellite mean event
return was **+0.72%**, with a paired five-calendar-year block interval of
**−1.08% to +2.40%**. Its mean advantage over weather was +0.80 percentage points,
with interval 0.00 to +1.60 points. The economic diagnostic gate failed.
The satellite-minus-weather overlay lost 30.79% compounded. All fixed controls,
both execution-cost assumptions, and full events are retained in `summary.json`.
These returns are event compounding, not an annualized return.

The separately declared September test also has 11 events. Satellite and weather
give the same trade direction in every event and both lose **18.88% compounded**.
Mean net event return is **−1.79%**, with 95% block interval **−3.66% to −0.01%**.
The overlay loses 25.38%; the always-long control gains 15.58%. Doubling execution
costs changes the satellite loss to 23.32%. Its economic diagnostic gate also
fails. Complete secondary results are in `late/summary.json`; the primary result
is neither replaced nor selected away.

Each county forecast uses only the **previous year's reported harvested area**
as its weight. The area is assumed available by June 30 following harvest. The
coverage denominator is reported positive prior-year area of fixed source/Census
counties with at least eight earlier valid yield labels. Counties without exact
prior-year area receive no carried-forward or future area; their number is
reported. Forecasts must cover at least 80% of this denominator. All 11 primary
events passed, with minimum coverage 81.57% in 2013. Coverage describes reported
area in that universe, not true total US maize area.
The frozen history rule counts positive yield labels. The audited source has
no genuine zero-yield observations: its 218 zero placeholders have neither area
nor production. A future dataset containing documented total crop failure would
need a newly specified history rule; no such cohort was silently removed here.

Weather and satellite forecasts are compared with the same weighted county
trend: forecast above trend shorts CORN, below trend goes long, equal means cash.
The separate overlay compares satellite with weather. The position is 100% of
capital, with 25 bp execution cost at each end and 3% annual borrowing on short
positions. Doubled execution costs leave the borrowing assumption unchanged.
All strategies share dates and eligible support, including cash and fixed long
and short controls.

Entry is the first session strictly after the 15th of the declared issue month;
exit is the last session on or before October 31. Every expected XNYS session must
have a valid CORN adjusted close. The source date index uses New York market
dates. The committed calendar was generated using exchange_calendars 4.13.2;
the normal US equity calendar applies to these NYSE Arca event windows.

[Teucrium's fund facts](https://etfs.teucrium.com/CORN/fund_facts) date inception
to June 9, 2010. [The sponsor's product description](https://teucrium.com/corn)
explains its exposure to CBOT corn futures across maturities. CORN does not track
spot corn. Fund expenses, futures roll and distributions are already reflected
in adjusted share returns, so no separate expense or roll charge is subtracted.
Yahoo Finance chart responses, exact requests, retrieval times and hashes are
saved. Prices, current yield, current area and current production are never used
to choose the trade direction.

Frozen files are `protocol.json`, `late/protocol.json`,
`secondary_horizon_freeze.json`, and `code_freeze.json`. The primary first result
is preserved in `first_fixed_summary.json`. A subsequent engineering guard
prevents a September invocation from overwriting the primary output directory;
`postfit_output_guard.json` records that every primary score, coverage value and
cost sensitivity remained exactly unchanged. No trading parameter was retuned.

Offline reproduction from the repository root:

```sh
python3 src/satellite_cybench_trading.py
python3 src/satellite_cybench_trading.py --issue-month 9 --output-dir results/satellite_validation/cybench_trading/late
python3 -m pytest -q tests/test_satellite_cybench_trading.py
```

The default analysis uses committed snapshots with integrity checks and requires
only NumPy/Pandas. The companion `fetch_inputs.py --refresh` explicitly refreshes
sources, verifies the raw CY-Bench county-statistics member against its audited
SHA, and requires `refresh_requirements.txt`. Forecasts are generated separately
by the county validation pipeline. Thirteen semantic tests pass. Independent
code review checked prior-area coverage, event boundaries, short-borrow and
execution costs, and shared calendar-block resampling. No orders were submitted.
