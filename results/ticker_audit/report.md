# Ticker audit

Which cases were priced on another company's stock? See `src/ticker_audit.py` for the method.

## Live 2026 cohort: tickers taken from the XBRL file name

22 eligible filings had no dei:TradingSymbol, so the resolver used the file name. 17 of them collide with another filer's ticker:

| filed | filer | ticker | priced as |
| --- | --- | --- | --- |
| 2026-01-16 | BTCS LABS INC. | BTCS | BTCS INC. |
| 2026-02-26 | RIDGEWOOD ENERGY S FUND LLC | S | SENTINELONE, INC. |
| 2026-02-26 | RIDGEWOOD ENERGY U FUND LLC | U | UNITY SOFTWARE INC. |
| 2026-02-27 | NEW MOUNTAIN PRIVATE CREDIT FUND | NMG | NOUVEAU MONDE GRAPHITE INC. |
| 2026-03-04 | NEW MOUNTAIN GUARDIAN IV BDC, L.L.C. | NMG | NOUVEAU MONDE GRAPHITE INC. |
| 2026-03-05 | NEW MOUNTAIN GUARDIAN IV INCOME FUND, L.L.C. | NMG | NOUVEAU MONDE GRAPHITE INC. |
| 2026-03-06 | ARES REAL ESTATE INCOME TRUST INC. | ARE | ALEXANDRIA REAL ESTATE EQUITIES, INC. |
| 2026-03-18 | CARLYLE CREDIT SOLUTIONS, INC. | CARS | CARS.COM INC. |
| 2026-03-23 | RISE COMPANIES CORP | RC | READY CAPITAL CORP |
| 2026-03-24 | CIRCLE ENERGY, INC./NV | CRCL | CIRCLE INTERNET GROUP, INC. |
| 2026-03-26 | KKR INFRASTRUCTURE CONGLOMERATE LLC | KKR | KKR & CO. INC. |
| 2026-03-26 | KKR PRIVATE EQUITY CONGLOMERATE LLC | KKR | KKR & CO. INC. |
| 2026-03-31 | BALLY'S CHICAGO, INC. | BALY | BALLY'S CORP |
| 2026-04-14 | GO GO BUYERS, INC. | GOGO | GOGO INC. |
| 2026-05-05 | RIDGEWOOD ENERGY W FUND LLC | W | WAYFAIR INC. |
| 2026-05-15 | TRANSIT PRO TECH INC. | G | GENPACT LTD |
| 2026-05-26 | T-REX ACQUISITION CORP. | TREX | TREX CO INC |

## All cases: priced on a ticker another CIK reports as its own

446 of 10787 cases (4.1%): 316 probable (the ticker's owner also filed that year) and 130 possible (may be a ticker reassigned later, a reorganisation, or a subsidiary priced on its parent).

Most affected tickers: F (166), ARCT (23), C (17), AEI (12), PBF (11), NRG (9), MARA (8), WTI (8), CLB (7), ANGI (6), CPA (6), S (6). Filers priced as Ford (F) include 1847 Holdings LLC, AUGUSTA GOLD CORP., AURA SYSTEMS INC, AVRA Medical Robotics, Inc., Aeluma, Inc., Aerkomm Inc., Allarity Therapeutics, Inc., Allegro Merger Corp..

By year: 2011 4, 2012 7, 2013 5, 2014 21, 2015 29, 2016 30, 2017 1, 2018 11, 2019 7, 2020 38, 2021 17, 2022 194, 2023 30, 2024 18, 2025 17, 2026 17.

## Effect on the headline metrics

| period | cases | monthly IC | AUC (+20 pp) | without probable | without any flag |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2009-2018 historical holdout | 1,758 | 0.152 | 0.605 | IC 0.152, AUC 0.604 (n=1,730) | IC 0.192, AUC 0.617 (n=1,650) |
| 2019-2020 discovery | 1,634 | 0.137 | 0.619 | IC 0.142, AUC 0.623 (n=1,603) | IC 0.145, AUC 0.623 (n=1,589) |
| 2021-2025 forward holdout | 6,415 | 0.243 | 0.634 | IC 0.247, AUC 0.628 (n=6,168) | IC 0.251, AUC 0.627 (n=6,139) |
| 2026 live (post-cutoff) | 985 | 0.282 | 0.514 | IC 0.271, AUC 0.512 (n=975) | IC 0.275, AUC 0.512 (n=968) |

## Trades

- Backtest P(+20%) ledger: 1 of 270 trades flagged: PBF 2021-08-04 (PBF Holding Co LLC, +58.8%, probable).
- Live 2026 locked rules: 0 of 74 trades flagged.
