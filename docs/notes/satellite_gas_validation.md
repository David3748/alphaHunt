# Satellite temperature does not improve the ground-weather gas nowcast

The fixed experiment fails its incremental-usefulness criterion. Across **156 winter months in 2000–2025**, adding satellite lower-troposphere temperature to a ground heating-degree-day model increases gas-demand forecast RMSE by **5.61%**. The satellite-only extension improves a simple temporal model by 12.40%, but that improvement's paired 95% interval includes zero. These results do not establish economic or trading alpha.

| Rolling forecast model | RMSE, billion cubic feet/day | MAE, billion cubic feet/day |
|---|---:|---:|
| Temporal baseline: same-month trend and previous-year demand | 2.8993 | 2.2511 |
| Temporal baseline plus satellite temperature | 2.5398 | 1.8526 |
| Temporal baseline plus ground heating degree days | **1.1123** | **0.8479** |
| Temporal baseline plus ground HDD and satellite temperature | 1.1747 | 0.9138 |
| Previous-year same-month demand | 3.2526 | 2.5539 |

The satellite-minus-baseline RMSE improvement is 0.3595 Bcf/day, with a paired 95% interval of **[-0.0153, 0.7134]**. The incremental satellite improvement over the ground-HDD model is **-0.0624 Bcf/day**, interval **[-0.1582, 0.0264]**. Negative improvement means worse predictions. The 2021–2025 sensitivity also fails: adding satellite temperature raises ground-HDD RMSE by 12.62%.

## Fixed application and evaluation

The mechanism was selected before inspecting joined outcomes: cold weather drives residential space heating and natural-gas demand. EIA documents this relationship in its [weather-sensitivity study](https://www.eia.gov/outlooks/steo/special/pdf/2014_sp_03.pdf). UAH's microwave observations measure lower-troposphere temperature, which might proxy the surface conditions driving demand. This is an economic **nowcast of an elapsed month before delayed official publication**, not a forecast of future weather or future demand.

`results/satellite_validation/gas/protocol.json` was frozen at **2026-09-27 01:51:20 UTC** before the satellite and demand outcomes were joined. The protocol uses October–March target months from January 2000 through December 2025. For each month, it fits OLS using exactly the previous ten observations of the same calendar month. All models include the same intercept, linear trend, and actual previous-year same-month gas use. The satellite extension adds one USA48 anomaly; the ground benchmark adds utility-gas-weighted national HDD; the combined model adds both. No horizon, geography, training window, model, or target was tuned after seeing results.

Gas volume in million cubic feet is converted to billion cubic feet per calendar day; HDD is also divided by calendar days. Prior-year gas uses the prior month's own number of days, including leap-year differences. Forecasts are issued on the 15th of the following month. Ground and satellite data are assumed available by the 14th; EIA targets are assumed available at the end of the second following month. Training labels must precede the issue date. Missing data cause abstention on the common evaluation sample; the retrieved sample has no abstentions.

The uncertainty calculation resamples paired errors in circular blocks of two winter years, using 10,000 draws and seed 20260927. October–December belong to the following winter year. There are 27 winter clusters, including the two partial boundary winters. The frozen gate requires at least 5% lower RMSE, lower MAE, and a positive lower 95% confidence bound both for the satellite extension over the temporal baseline and for the combined model over the ground-HDD model. Neither comparison passes all requirements.

## Public inputs and practical limitations

- **Satellite:** [UAH version 6.1 lower-troposphere monthly temperatures](https://www.nsstc.uah.edu/data/msu/v6.1/tlt/uahncdc_lt_6.1.txt), selecting the column explicitly labeled USA48. [UAH's climate page](https://www.nsstc.uah.edu/climate/) describes processing and public release. The [October 2025 report](https://www.nsstc.uah.edu/climate/2025/October2025/GTR_202510_v2.pdf) gives November 4 as that month's release date. This single example supports plausible early-month availability, not all historical release dates.
- **Independent economic outcome:** [EIA U.S. residential gas consumption](https://www.eia.gov/dnav/ng/hist_xls/N3010US2m.xls), series N3010US2. The [Natural Gas Monthly publication](https://www.eia.gov/naturalgas/monthly/) reports June 2026 data released August 31, illustrating the reporting lag. Exact historical releases and revisions have not been reconstructed.
- **Ground benchmark:** [NOAA CPC utility-gas-weighted daily heating degree days](https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data/), CONUS row, summed to months. The frozen comparator uses 2010 ACS utility-gas customer weights. The independent retrieval includes every day in 1990–2025, monthly CSV, raw annual text files, hashes, and an offline rebuild under `results/satellite_validation/gas/ground_hdd/`. [CPC documentation](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/ddayexp.shtml) explains the 65°F heating threshold and weighting. These archived values were reprocessed with later weights; they are not original historical vintages.

All three sources are **current revised archives**. The chronological split prevents use of future rows but does not undo later product revisions. The gas outcome covers all U.S. states, whereas the satellite predictor covers the contiguous 48 states and the ground benchmark is CONUS. Satellite temperatures are a deep atmospheric layer rather than surface temperatures; public ground weather is the more direct heating-demand measure.

EIA [changed residential consumption estimation in August 2010](https://www.eia.gov/naturalgas/monthly/comparisonv.php), improving calendar-month alignment using utility system sendout. Earlier observations were not revised under that method. The prespecified 2021–2025 sensitivity therefore has only post-change training **targets**; some January–March 2011 training rows still use 2010 lagged gas features. It is not an entirely post-change-input experiment. The sample was not altered after this distinction was identified.

This test cannot rule out every satellite application or model. It does show that this fixed UAH application supplies no demonstrated incremental forecast value once readily available ground weather is included. No gas-price prediction, trading profit, prospective deployment, or familywise adjustment across the broader satellite candidate search is claimed here. A separate trading diagnostic, if added, must retain those distinctions.

## Reproduction

From the repository root, the default uses committed CSV snapshots and requires no network, API key, or Excel engine:

```sh
python3 src/satellite_gas_validation.py
python3 -m pytest -q tests/test_satellite_gas_validation.py
```

`panel.csv`, `predictions.csv`, and `summary.json` include the source alignment, forecast dates, training boundaries, metrics, intervals, and claim flags. Raw source hashes are in `source_manifest.json`; the summary hashes the three offline CSVs and validator source. Nine tests cover the named UAH column, exact calendar alignment and leap days, current/future-outcome independence, future-weather independence, training windows, missing data, unavailable inputs, and claim boundaries.

Refreshing the raw UAH/EIA sources is optional and changes the data vintage. It requires the separately declared refresh dependencies; ground-HDD refresh is maintained separately with its own provenance.

```sh
python3 -m pip install -r results/satellite_validation/gas/refresh_requirements.txt
python3 src/satellite_gas_validation.py --fetch
```
