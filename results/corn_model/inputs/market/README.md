# CORN ETF market snapshot

Proxy: Teucrium CORN ETF adjusted daily closes in USD, **not actual December corn futures**.

- Coverage: 2010-06-09 through 2026-09-25, 4,100 sessions; no missing expected session in that span.
- Retrieved: 2026-09-27 22:52 UTC. September 25 was the latest completed session at retrieval. This snapshot is not live.
- `manifest.json`: exact Yahoo URL, hashes, calendar version, prior-study overlap audit and limitations.
- `corn_yahoo_chart.json`: untouched public response; `adjusted_prices.csv`: validated extraction.
- `xnys_schedule.csv`: independently generated exchange-session dates and UTC opens/closes, including holidays/early closes, through 2027. Future calendar dates do not imply known prices.
- `futures_feasibility.json`: bounded actual-December-contract search and official fund metadata provenance.

Offline: `python3 src/corn_market_data.py`. Explicit refresh: install `refresh_requirements.txt` and run with `--refresh`. Full timing and cost contract: `docs/notes/corn_market_sources.md`.
