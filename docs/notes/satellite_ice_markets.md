# Satellite sea ice → Kalshi and Polymarket paper screen

This is a read-only research model for the 2026 Arctic sea-ice minimum contracts. It estimates the chance of each settlement outcome from the [NSIDC Sea Ice Index v4 daily satellite series](https://nsidc.org/data/g02135/versions/4), then compares that chance with the *ask* and top-of-book size in public [Kalshi](https://kalshi.com/markets/kxarcticicemin/arctic-sea-ice-min-extent/kxarcticicemin-26oct01) and [Polymarket](https://polymarket.com/event/min-arctic-sea-ice-extent-this-summer) markets. It does not connect to an account or place orders.

**As captured September 29, 2026 00:57 UTC:** the model makes **no paper trade**. The NSIDC minimum through the deliberately lagged September 26 observation is **4.574 million km²**. Kalshi's below-4.6 outcome is already established by an observed day, but its YES ask is $1.00 with no ask size. The below-4.5 outcome is still open; the model assigns 1.28% YES versus a $0.01 ask, yielding a negative $0.0172 edge after the reserved cost. For Polymarket's 4.4–4.6 bin, the model assigns 98.08% YES versus a $0.996 ask; its NO side is 1.92% versus a $0.028 ask. Both have negative cost-adjusted edge. Quotes expire quickly; [the dated screen](../../results/satellite_ice_markets/paper_screen.json) is historical evidence, not a current offer.

## Exact contract mapping

Kalshi settles YES when the lowest **individual daily** extent between **December 19, 2025 and October 1, 2026** is strictly below its strike. Polymarket settles one of seven disjoint bins using the **August 1–October 1, 2026** minimum in NSIDC's `NH-Daily-Extent` worksheet, at 0.001 million km² precision; it excludes revisions made after the initial October 1 publication. The model keeps the two measurement windows separate. The NSIDC daily CSV and named worksheet agreed on all **9,767** shared values from 2000–2026 in the captured source snapshot. The workbook was checked because Polymarket explicitly names it, while Kalshi explicitly names the daily CSV. Polymarket's boundary phrasing is interpreted as lower-inclusive, upper-exclusive; an exact boundary requires checking the resolver's final interpretation.

Only daily-complete seasons from **1988 onward** enter training. Earlier seasons include every-other-day observations, which can miss a true daily minimum. The signal uses the observed minimum through issue date minus **three days**. For each prior complete year, it measures any further fall between that cutoff and October 1. The primary model uses the empirical distribution of those prior-year remaining falls; the 12-nearest-season analog model is kept as a diagnostic. Each prior year's label is embargoed until October 15. A half-count smooths each binary probability; one total pseudocount smooths the seven-bin distribution so its probabilities sum to one. These are deliberately simple empirical estimates and do not give credible tail precision.

## Historical check

The chronological hindcast covers **18 held-out years, 2008–2025**, at each of four issue dates. The benchmark uses the observed minimum and all prior-year remaining falls; the nearest-season candidate does not consistently improve on it. Brier scores below average five binary thresholds per year; lower is better. Multiclass Brier uses the seven Polymarket bins.

| Issue | Primary RMSE (million km²) | Analog RMSE | Primary binary Brier | Analog binary Brier | Primary bin Brier | Analog bin Brier |
|---|---:|---:|---:|---:|---:|---:|
| Aug 15 | 0.2708 | 0.2853 | 0.1329 | 0.1341 | 0.7495 | 0.7690 |
| Sep 1 | 0.1120 | 0.1180 | 0.0529 | 0.0584 | 0.5862 | 0.5779 |
| Sep 15 | 0.0550 | 0.0562 | 0.02434 | 0.02410 | 0.3444 | 0.3471 |
| Sep 25 | 0.0000 | 0.0000 | 0.00012 | 0.00053 | 0.00083 | 0.00383 |

The September 25 perfect continuous score reflects the fact that, in these 18 current-vintage seasons, no new minimum occurred after the assumed September 22 cutoff. It is a useful description of the archived seasons, **not** proof of an error-free live forecast. The test is not independent of NSIDC's current revised data, and it has no historical executable order books. No trading return or net alpha is verified. The cost reserve is an **assumed $0.02 per contract screening allowance**, plus a required **$0.03 model edge** and at least five contracts at the best ask; it is not a claim of the platform's exact current fee. Fee, funding, regional eligibility, and market-rule checks would be necessary before any real trading.

This model is a closer settlement match than the [corn model](corn_model.md): the sea-ice market directly resolves from satellite-derived NSIDC values. It does not make the earlier July-to-September [physical sea-ice forecast](satellite_seaice_validation.md) a demonstrated trading strategy. The 2026 market and model choices were made after viewing current contract prices, so the September 2026 price screen is exploratory rather than untouched validation.

## 2025 Kalshi quote replay

A separate [one-season replay](../../results/satellite_ice_markets/quote_replay_2025/summary.json) uses Kalshi's [historical bid/ask candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks) and actual 2025 contract resolutions. Thirteen markets were archived. Four had a later data-source clarification, and two of those also have contradictory “Above” wording, so all four are excluded. Of the nine with unambiguous archived “below” wording, 52 side/date quotes are usable at the four fixed issue dates. The signal uses only NSIDC observations through three days before each issue and prior-year outcomes; a candle must end before the 12:00 UTC decision. One position at most is selected at the *first* issue with a positive cost-adjusted edge.

| Replay rule | Chosen quote | Hypothetical P&L for five contracts after $0.02/contract allowance |
|---|---|---:|
| Satellite remaining-melt model | Aug 15, NO below 4.4m, ask $0.14; settlement NO | **+$4.20** |
| Same model, require ≥5 contracts of recent *market-wide* volume | Sep 1, NO below 4.4m, ask $0.49; settlement NO | **+$2.45** |
| No-further-melt persistence benchmark | Aug 15, NO below 5.2m, ask $0.02; settlement YES | **−$0.20** |

These are **hypothetical quote payoffs, not realized or fill-verified returns**. The selected August 15 candle had **zero trades** and historical candles do not preserve order-book size; even the September 1 market-wide volume of 230 contracts does not establish executable NO depth at the displayed ask. The 2025 event warned of a potential NSIDC data-source change, the archived daily values are revised, and this replay was designed after 2025 resolved. One season and one chosen position cannot validate trading alpha. The [full quote-decision table](../../results/satellite_ice_markets/quote_replay_2025/all_quote_decisions.csv), [raw Kalshi responses](../../results/satellite_ice_markets/quote_replay_2025/raw), and [hash manifest](../../results/satellite_ice_markets/quote_replay_2025/source_manifest.json) show the selection and exclusions.

## Reproduce

```sh
python3 -m src.satellite_ice_markets
python3 -m src.satellite_ice_quote_replay
python3 -m pytest -q tests/test_satellite_ice_markets.py
python3 -m pytest -q tests/test_satellite_ice_quote_replay.py
```

The default run uses captured provider files, verifies every SHA-256 checksum, checks the two NSIDC formats, regenerates [all hindcasts](../../results/satellite_ice_markets/hindcasts.csv) and [metrics](../../results/satellite_ice_markets/backtest.json), and marks a market snapshot expired five minutes after capture. `--refresh` explicitly downloads a new set of public source and book snapshots. The event identifiers and exact rules are currently 2026-specific and fail closed if those market rules change. The source [manifest](../../results/satellite_ice_markets/source_manifest.json) records retrieval times, URLs, and hashes; quote timestamps and every outcome are in the paper screen.

The quote-replay command uses captured 2025 Kalshi market metadata and daily bid/ask candles. Its own `--refresh` explicitly refreshes that historical snapshot.
