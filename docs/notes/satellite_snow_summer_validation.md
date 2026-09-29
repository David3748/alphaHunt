# May satellite snow and July–September runoff

This fixed continuation improves the primary precipitation/prior-flow forecast, but does **not** pass all stronger-comparator gates. Across 14 held-out years (2010–2023), May satellite snow reduces the primary RMSE by **10.11%** and MAE by **17.65%**, with a paired calendar-block 95% RMSE-gain interval of **+2.42% to +32.66%**. Allowing the ground baseline to use May flow reduces the incremental satellite gain to **4.86%**, with interval **−0.58% to +20.88%**. The latter result falls short of the fixed 5% and positive-interval criteria. This supports a qualified historical forecast finding, not robust operational or financial alpha.

| Common support | Ground baseline | Baseline RMSE AF | With snow RMSE AF | RMSE reduction | 95% reduction interval |
|---|---|---:|---:|---:|---:|
| 2010–2023, 14 years | Oct–May rain + Oct–April flow | 128,917 | 115,879 | 10.11% | 2.42% to 32.66% |
| Same | Oct–May rain + Oct–May flow | 127,565 | 121,366 | 4.86% | −0.58% to 20.88% |
| Ground-SWE support, 9 years | Rain + Oct–April flow + May SWE | 474,878 | 435,484 | 8.30% | −20.35% to 31.76% |
| Same | Rain + Oct–May flow + May SWE | 453,074 | 423,121 | 6.61% | −20.49% to 22.31% |

The expanding historical mean and prior-year summer runoff have RMSE 281,603 and 363,881 AF on the 14-year primary support. They are weaker than both weather/flow baselines. Every table pair uses identical training and evaluation support, and the SWE output files include the corresponding models without SWE on that same support.

## Why this separate continuation exists

The preceding March-snow study failed to improve April–July runoff forecasts. March snow presence can be widespread even when snow depth differs; remaining May high-elevation snow may be more relevant to summer melt timing and water supply. This later observation and future target were selected on that mechanism. `snow_summer/protocol.json` froze the May feature, June 15 issue, July–September target, fixed ridge penalty, stronger baselines, sample rule and uncertainty procedure **before the May-feature/summer-target join or metrics**.

This is an exploratory continuation in the same basin after a failed horizon. The underlying monthly runoff table had already been retrieved and March-horizon outcomes viewed. It must not be described as untouched preregistration, a new independent basin, or a statistically corrected discovery across all mechanisms investigated.

## Fixed implementation

The target is the sum of CDEC SBF sensor 65 July–September full-natural flow in acre-feet. Forecast issue is June 15, after a nominal fourteen-day source-delivery buffer; all target months are future. Predictors are October–May 5SI regional precipitation and preceding October–April natural flow. A diagnostic includes May flow as potentially available by June 15. Additional matched-support diagnostics use the latest valid HNT snow-water-equivalent observation on May 29–31.

Training-standardized ridge regression uses **alpha 5**, an unpenalized intercept and a fixed zero-variance scale of one. It expands through prior eligible years only, requiring ten training years; no penalty or feature search occurred. Every regression clips negative predictions to zero. Unknown current targets do not suppress forecasts. Evaluation uses actual labels only after predictions exist. Uncertainty resamples two-calendar-year circular blocks with the missing calendar-year positions retained, 10,000 draws and seed 20260927.

MODIS extraction uses the identical frozen March-study quality rule, now for May 1–31: Terra MOD10A1 Collection 6.1 screened NDSI 0–100 and Basic QA 0 or 1; snow presence is positive screened NDSI. Per-pixel valid-day snow frequency requires three valid days, and at least 90% of basin pixels must qualify. The equal-area average covers the same 20,267 pixel centers within the authoritative USGS Friant watershed. Clouds, water, night, no-decision and fill are missing, not zero snow. No later imagery or temporal gap filling enters the feature.

All 742 daily source/QA pairs downloaded successfully. The current Planetary Computer mirror stops in March 2024, so May 2024 is an explicit missing-source abstention. No other product replaced it. The fixed initial sample yields 14 evaluated years, rather than silently moving the split. Current-vintage source URLs, processing dates and pixel hashes are retained; compact original pixel snapshots permit offline re-extraction.

## Ground-SWE diagnostic limitation

The SWE model's large error is genuine extrapolation under the frozen rule, not evidence that the satellite defeated a strong operational forecaster. The 2000–2009 May HNT observations range from approximately 3–24 mm; May 2010 is 287 mm. Even standardized ridge extrapolates far beyond the training range and predicts 1.71 million AF against 306 thousand AF actual. Raw units and values were checked; no unsupported deletion, clipping or refit was introduced. Missing/invalid SWE further reduces support to nine years. The fresher-flow comparison is consequently the meaningful challenge, and it does not confirm a positive lower uncertainty bound.

The unchanged primary satellite prediction also beats the fresh-flow ground model by 9.16% on the same 14 years, but that direct cross-model comparison has interval −1.46% to +23.50%. This descriptive post-result check does not rescue the stronger-comparator gate or select a different model.

## Source and claim limits

Sources and quality definitions are documented in [the daily snow study](satellite_snow_daily_validation.md). Primary documentation includes [NASA MOD10A1](https://modis-snow-ice.gsfc.nasa.gov/?c=MOD10A1), the [NSIDC Collection 6.1 guide](https://nsidc.org/sites/default/files/mod10a1-v061-userguide_1.pdf), [CDEC SBF metadata](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=SBF), and [USGS NLDI](https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-11251000/basin?f=json). Ground raw data and their independently frozen geographic selection are reused from `snow_daily/ground`; source hashes are recorded in the summer ground manifest.

This is a current-vintage scientific forecast study. Historical MODIS reprocessing and revised CDEC values were not necessarily available on the nominal issue date. A fourteen-day buffer does not certify original releases. Snow presence is not water depth or SWE; clouds, forests, terrain, sampling and station representativeness remain limits. One basin and 14 annual outcomes provide a small sample. No proprietary information advantage, tradable return or production readiness is demonstrated.

## Reproduction

Offline, from repository root:

```bash
python3 src/satellite_snow_summer_validation.py
python3 -m pytest -q tests/test_satellite_snow_daily_validation.py tests/test_satellite_snow_summer_validation.py
```

For explicit refresh, install `snow_daily/refresh_requirements.txt`, then run:

```bash
python3 src/satellite_snow_summer_validation.py --fetch --workers 8
```

The source, ground features, panel, four complete prediction sets and JSON results remain separate from the failed March study. Nineteen semantic tests cover both studies, including target/prior-flow boundaries, training-only standardization, constant features, chronology, missingness, matched supports and unknown-outcome forecasts.
