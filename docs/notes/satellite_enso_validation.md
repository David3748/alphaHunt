# September satellite-blended SST and Texas winter precipitation

The fixed chronological test improves point forecast error, but **fails the predefined uncertainty gate**. Over 29 held-out winters (1998–2026), September Niño 3.4 sea-surface temperature reduces ensuing Texas December–February rainfall RMSE by 9.74% against the expanding winter mean and 36.24% against the prior winter. The paired two-winter block 95% interval for the improvement over the stronger mean baseline is **−0.150 to +0.457 inches**, crossing zero. This is provisional evidence, not verified robust forecast utility or trading alpha.

| Model | Test RMSE, inches | Test MAE, inches |
|---|---:|---:|
| Expanding OLS: September Niño 3.4 SST | 1.604 | 1.238 |
| Expanding historical winter mean | 1.777 | 1.458 |
| Previous winter precipitation | 2.516 | 2.128 |

The SST model wins against the mean in 20 of 29 winters. Its MAE improves 15.08%. It does not dominate every period or ENSO event; the full prediction table preserves all outcomes, including the poor 2016 prediction.

## Frozen evaluation

`results/satellite_validation/enso/protocol.json` was written before the outcome calculation. The state, predictor month, target season, expanding linear model, initial 15 training winters, baselines and gate were fixed once. There was no search across states, horizons, transformations or model hyperparameters. Training winters are 1983–1997 initially; evaluation starts in 1998. September of year *t* predicts December *t* through February *t+1*, whose label is winter year *t+1*. All predictions use only earlier winter labels. Forecast issue is October 15, 47 days before the target begins. The fixed gate required at least 5% lower RMSE against both baselines, lower MAE, and a strictly positive 95% paired block RMSE-improvement interval against the historical mean. The final condition failed.

The predictor uses absolute September SST. Subtracting a fixed September climatological normal yields identical OLS predictions with an intercept; using the absolute value avoids introducing a future-period anomaly normal. Uncertainty uses 10,000 deterministic paired circular block draws with two adjacent winters per block; this recognizes some interannual dependence but does not remove model-development or broader candidate-selection uncertainty.

## Sources and availability

- [NOAA CPC monthly index directory](https://www.cpc.ncep.noaa.gov/data/indices/) explicitly identifies the [downloaded `sstoi.indices`](https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices) as monthly **OISST v2.1** and defines Niño 3.4 as 5°N–5°S, 170°W–120°W. This is separate from its ERSST indices.
- [NOAA OISST product documentation](https://www.ncei.noaa.gov/products/optimum-interpolation-sst) describes a blend of satellite retrievals and in-situ ships, buoys and Argo observations. The archive incorporates version changes and historical reprocessing, including changes from 2016 onward. Therefore this test establishes neither pure satellite attribution nor incremental information beyond an in-situ ENSO index.
- [NOAA metadata](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01606) describes preliminary daily OISST at about one day and final data after two weeks. The October 15 issue assumes September data available after that delay. Actual historical CPC monthly publication timestamps are not preserved here. Moving the operational issue to October 31 would retain the same feature values and still precede December, but is not an original-publication audit.
- [NOAA NCEI Climate at a Glance Texas three-month February-ending precipitation](https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/41/pcp/3/2/1982-2026.csv) supplies the independent target. Its CSV header verifies Texas, December–February and inches. Prior winter labels receive an assumed April 1 availability embargo, well before the following October forecast. These are current revised labels; exact original issue dates are not reconstructed.
- The [CPC revision history](https://www.cpc.ncep.noaa.gov/data/indices/Readme.index.shtml) also documents index recalculations. Current vintage chronological skill must not be presented as a point-in-time original-vintage backtest.
- [NOAA's Texas ENSO discussion](https://www.climate.gov/news-features/blogs/enso/la-ninas-delayed-effect-sizzling-texas-summers) supplies the physical rationale: La Niña tends to dry Texas winter and spring. This is an established public forecasting relationship. Any grain, hydropower or energy value would require incremental skill over available operational forecasts and a separate economic or trading test.

Raw snapshots, input SHA-256 hashes, the frozen protocol, dates, labels, coefficients, every forecast and metrics are preserved in `results/satellite_validation/enso/`. `original_vintage_operational_verification`, `incremental_satellite_vs_in_situ_verified` and `trading_alpha_verified` are all false.

## Reproduce

```sh
python3 src/satellite_enso_validation.py
python3 -m pytest -q tests/test_satellite_enso_validation.py
```

The six tests check winter alignment, feature availability, future-label and future-feature isolation, invariance to fixed anomaly normalization, and the preserved provisional outcome. `--fetch` replaces raw snapshots with current provider data and will consequently change provenance hashes and potentially results.
