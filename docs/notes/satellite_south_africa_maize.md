# September satellite SST and South African maize

The fixed experiment fails verification for both maize yield and its prespecified secondary, total production. September Niño3.4 SST adds some information to the matched weather regression, but it does not consistently improve on the stronger simple time-trend comparator. Neither the conservative FAOSTAT release case nor the optimistic completed-harvest information check passes the frozen all-comparator gate. No model or threshold was changed after observing these results.

| Target and information assumption | Years | Weather RMSE | Satellite RMSE | Trend RMSE | Satellite gain against best baseline |
|---|---:|---:|---:|---:|---:|
| Yield, FAO lag | 22 | 0.890 t/ha | 0.857 | 0.789 | −8.52%; CI −22.50% to +15.44% |
| Yield, latest completed harvest | 24 | 0.815 t/ha | 0.728 | 0.720 | −1.15%; CI −11.13% to +16.51% |
| Production, FAO lag | 22 | 2.646 million t | 2.506 | 2.405 | −4.20%; CI −18.87% to +8.76% |
| Production, latest completed harvest | 24 | 2.595 million t | 2.347 | 2.388 | +1.74%; CI −14.83% to +12.63% |

The following features were fixed before any outcome join: September mean absolute Niño3.4 sea-surface temperature; August and September maize-belt ground rainfall separately; linear calendar time; and latest released yield or production. The regressions use ridge alpha 5 and training-only standardization, with an unpenalized intercept. Training expands from 15 eligible prior observations. Comparators include the same regression without SST, the released-history mean, the most recent ten released outcomes' mean, persistence, unpenalized linear trend, and a separately reported prior-year SST placebo. Bootstrap uncertainty uses 10,000 paired five-calendar-year blocks, retaining missing years. The gate requires at least 20 held-out years, ≥5% RMSE improvement against the strongest baseline, and lower MAE and a positive lower 95% RMSE-gain bound against every main comparator. The primary and stronger-information cases must both pass. They do not.

## Sources and temporal alignment

[FAOSTAT's methodology](https://files-faostat.fao.org/production/QCL/QCL_methodology_e.pdf) assigns annual crops to the calendar year of the bulk harvest. This avoids a misleading USDA marketing-year mapping: South African PSD “2024/25” begins in May 2025, whereas the FAO calendar-2024 label concerns the preceding harvest. The [official bulk archive](https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip) supplied 135 South African maize rows: production, harvested area and yield for 1980–2024, all marked official figures. Primary yield is tonnes divided by harvested hectares, cross-checked against the published kg/ha series. Target-year harvested area is never a predictor. Production is separately evaluated in million tonnes.

[USDA's crop-calendar description](https://apps.fas.usda.gov/PSDOnline/Circulars/2025/09/production.pdf), page 11, says planting can start in early October, with most harvest deliveries in May–August. Accordingly, October 20 is described as **early planting**, not uniformly before planting. The target is annual harvest-year output; the May–August dates in the panel describe the typical bulk-harvest season, not a monthly aggregation of FAO statistics. The forecast precedes the following harvest by several months. [USDA's 2016 analysis](https://ipad.fas.usda.gov/highlights/2016/05/SouthAfricaElNino/index.htm) explains that ENSO drought can reduce both planting and yield. That mechanism motivated the total-production secondary, frozen before the first fit.

The SST source is [NOAA OISST](https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.oisst.v2.highres/sst.mon.mean.nc), a satellite and in-situ blend. September values are averaged over 5°S–5°N, 170°W–120°W using cosine-latitude weights. A 20-day allowance after September 30 covers documented 17-day final processing plus a buffer; issue time is October 20 at noon UTC. Absolute SST avoids full-history anomaly normalizations. Its historical processing and first-release timestamps remain unverified; the experiment cannot isolate incremental satellite value over in-situ ENSO measurements.

Ground controls are NOAA CPC's gauge-only daily precipitation, subset through OPeNDAP to the fixed maize-belt rectangle 24–30°S, 25–31°E. Coordinates and the 90% daily grid-coverage rule were fixed geographically before outcome joins. Every calendar day is required, with a ten-day source allowance. Missing days in August 1985, September 1986 and September 2004 make those seasons unavailable, rather than zero rain. Consequently the conservative test covers 2002–2024 excluding 2005 (22 years); the stronger-information case covers 2000–2024 excluding 2005 (24 years). The spatial rectangle is a maize-belt proxy, and the current gauge archive may include revisions. Exact source URLs, numeric subsets, original metadata and hashes are retained.

The primary FAO case assumes publication at the end of the year following each harvest year. At an October issue this makes the latest available target three harvest years old. This is a conservative reporting assumption, not proof that farmers lacked better local information. The pre-fit addendum therefore also admits the latest completed harvest's **final** outcome at September 30, a deliberately optimistic revision/oracle information check. The [September 2024 national supply-and-demand report](https://www.namc.co.za/wp-content/uploads/2024/10/September-2024-SASDE-report.-1-Oct-2024-FINAL.pdf) demonstrates that local current-harvest estimates existed well before FAOSTAT's subsequent annual update. Neither case is an original-vintage operational backtest, and no superiority to contemporary expert ENSO or crop forecasts was established.

## Fixed-model bias diagnostic

After preserving the failures, a diagnostic measured the existing regressions' time coefficients and forecast bias. It did **not** fit a corrected model. In the completed-harvest yield case, mean time coefficients were 0.0693 t/ha/year for weather, 0.0639 for satellite, and 0.0965 for the trend-only model. Mean forecast errors were −0.357, −0.362 and −0.227 t/ha respectively. For production, corresponding biases were −0.809, −0.845 and −0.464 million tonnes. These different conditional coefficients do not prove the penalty caused the errors, but they support investigating whether shrinkage of technological trend is an inappropriate structural constraint.

A future correction can leave intercept and time unpenalized while regularizing other predictors. Let `U=[1,time]`, let `Z` contain the other predictors standardized using training data, and let `M_U` remove the fitted projection on `U`. Fit `beta=(Z'M_U Z+5I)^(-1)Z'M_U y`, then `gamma=OLS(U,y-Z beta)`; forecast `U_new gamma+Z_new beta`. This remains only a proposal here. An independent replication protocol must be frozen before fitting it; the failed South African holdout cannot become untouched confirmation after a model change.

## Reproduction

```sh
python3 src/satellite_south_africa_maize.py
python3 results/satellite_validation/south_africa_maize/diagnose_frozen_bias.py
python3 -m pytest -q tests/test_satellite_south_africa_maize.py
```

Eighteen semantic tests cover calendar-year alignment, publication embargoes, late inputs, complete ground months, future-label and target-area invariance, secondary production units, and enforcement of the stronger trend comparator. Independent review found no source-year alignment, future-weather, current-area or train-standardization leakage. The scientific result remains negative. Protocols, source manifests, complete forecasts, abstentions and both reporting regimes are retained under `results/satellite_validation/south_africa_maize/`; this is another exploratory candidate, without multiple-candidate correction or traded-profit verification.
