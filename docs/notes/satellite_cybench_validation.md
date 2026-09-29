# County maize forecasts from raw satellite vegetation

The experiment estimates official US county maize yield before the final county reports. It tests whether raw MODIS vegetation measurements add information beyond a strong reanalysis-weather model, county yield trends and prior outcomes. It does not claim every county is still before physical harvest.

The primary issue date is August 15. A September 15 secondary date was fixed before any model fit, to test whether observing August grain filling improves accuracy at the cost of one month of lead time. Both dates use identical models and evaluation rules; both results are retained.

## Source and timing

CY-Bench version 1.10 ([fixed Zenodo record](https://zenodo.org/records/17279151)) supplies USDA NASS county grain-maize yields, AgERA5 weather and crop-weighted MOD09CMG vegetation indices. The [dataset paper](https://essd.copernicus.org/articles/18/3997/2026/essd-18-3997-2026.html) documents the prepared data. Source offsets, archive CRCs, SHA-256 hashes and license notices accompany compact local inputs. Full weather CSVs remain extraction scratch files; committed monthly sufficient statistics reproduce the forecast features without network access.

The NDVI path selects observations only within each eight-day window. It does not use the smoothed FAPAR product. Each complete window, through its final day's end, receives a fourteen-day buffer. Monthly means require at least two valid composites and half the eligible expected composites. Weather months require every calendar day and finite values; incomplete sums and interpolation are forbidden.

AgERA5 assimilates both satellite and ground information. It is a revised reanalysis comparator, not a purely ground measurement or a certified original-release forecast input. The crop mask describes 2021 and was published later; fixed geography creates historical hindsight. The 2022/2023 sign check is after the map reference year, not proof of historical public availability. This is a current-vintage scientific forecast experiment, not a tradable historical release reconstruction.

The initial label audit found 218 zero-yield entries with neither reported area nor production, all in 2003–2009. No actual model had fitted. Their exact upstream origin was not established. A documented pre-fit repair treats those unsupported labels as missing, preserves every raw record, and keeps genuine zero yields with supporting positive area. No held-out-year labels changed.

## Frozen forecast and verification

Each annual forecast trains only on earlier released yields, with June 30 following harvest as the conservative label-release assumption. Counties need eight prior valid labels. County intercepts and linear trends are fitted from all available past labels, including years whose imagery is cloudy. A fixed gradient-boosting model learns residual yield from April–July weather; the satellite model adds four monthly NDVI means. The September secondary adds August to both information sets. No parameter search occurs.

Models share training and evaluation support. Unknown current yields do not suppress forecasts. Records without sufficient history, complete features or a positive trend prediction remain explicit abstentions. Comparators are matched weather, county trend, latest yield and the five-year mean.

Evaluation moves forward through 2013–2023. County losses are averaged within each year, then years receive equal weight. Confidence intervals resample five-year calendar blocks shared across all counties. Thousands of correlated county rows are not treated as thousands of independent seasons.

The gate requires at least five percent lower RMSE than the strongest comparator, lower MAE and a positive 95% paired gain interval against every comparator, adequate coverage, and lower error than weather and trend in both 2022 and 2023. Intervals are not adjusted for the wider exploratory research search. A separate CORN ETF test uses prior-year acreage weights and execution/borrow costs; forecast accuracy does not establish trading alpha.

## Reproduction

```sh
python3 results/satellite_validation/cybench_maize/rebuild.py
python3 src/satellite_cybench_validation.py
python3 src/satellite_cybench_validation.py --late-season
```

The exact protocols, source-quality amendments, predictions, annual losses and machine-readable decisions are under `results/satellite_validation/cybench_maize/` and `cybench_maize_late/`. Independent source and prediction audits accompany them.
