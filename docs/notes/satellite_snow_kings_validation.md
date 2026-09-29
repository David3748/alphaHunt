# Kings River confirmation of the May snow forecast

The geographic confirmation **failed**. The unchanged May-snow model improved Kings July–September runoff RMSE by 4.21% against precipitation and prior flow, and by only 1.72% when the ground model included May flow. Both uncertainty intervals include zero. Pooling Kings with the earlier San Joaquin result cannot rescue the stronger comparison: pooled fresh-flow improvement is 2.77%, with interval −1.06% to +12.99%.

| Evaluation | Ground baseline | RMSE without snow AF | RMSE with snow AF | Reduction | 95% reduction interval |
|---|---|---:|---:|---:|---:|
| Kings2010–2023,14years | Rain + Oct–Aprilflow | 158,197 | 151,533 | 4.21% | −0.76% to20.01% |
| Same | Rain + Oct–Mayflow | 166,665 | 163,794 | 1.72% | −1.91% to8.45% |
| Kings2012–2023,12years | Rain + Oct–Aprilflow + SWE | 204,816 | 201,768 | 1.49% | −30.31% to2.73% |
| Same | Rain + Oct–Mayflow + SWE | 207,320 | 206,949 | 0.18% | −24.45% to1.31% |

Adding snow worsens Kings MAE by4.14% and7.30% in the two ground-SWE comparisons. The primary and fresh-flow MAE improvements without SWE are3.70% and0.45%. On full Kings support, climatology and persistence RMSE are320,392 and400,869AF, which are substantially weaker than weather/flow models.

| Two-basin equal-weight pooling | Annual support | RMSE reduction | 95% reduction interval |
|---|---:|---:|---:|
| Primary prior-April-flow model |14years |6.34% |0.88% to25.94% |
| Stronger fresh-May-flow model |14years |2.77% |−1.06% to12.99% |
| Primary + groundSWE |7years |3.20% |−19.61% to12.85% |
| FreshMayflow + groundSWE |7years |−15.90% |−142.05% to2.13% |

The pre-result gate required at least5% Kings fresh-flow improvement and lower MAE, plus at least5% pooled fresh-flow improvement and a positive lower uncertainty bound. It failed both requirements. The positive pooled primary result must not replace the failed confirmation criterion.

## Unchanged model and geographic design

`results/satellite_validation/snow_kings/protocol.json` was frozen before any Kings snow/outcome join. Target, horizon, estimator, training rule and satellite-quality rules transfer unchanged from the San Joaquin summer experiment: June15 issue, May1–31 Terra MOD10A1Collection6.1 snow-presence frequency, July–September natural-flow sum, training-standardized ridge alpha5, ten minimum prior training years, expanding chronological training and equal nonnegative clipping.

The satellite statistic requires screenedNDSI0–100 with BasicQA0/1, at least three valid May observations per pixel and at least90% qualifying basin pixels. Snow means positive screenedNDSI; cloudy, water, no-decision, night and fill codes remain missing. It is not SWE or fractional snow-covered area. No June observations or future fill are used. All742 source/QA pairs downloaded and were verified against saved hashes. There are18,624 equal-area basin pixel centers.

[USGS station11221500](https://waterdata.usgs.gov/monitoring-location/USGS-11221500) is Kings River below Pine Flat Dam, with published area1545square miles. The [NLDI upstream polygon](https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-11221500/basin?f=json) measures1544.47square miles, a0.034% mismatch. [CDEC KGF metadata](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=KGF) identifies the same Pine Flat location and provides monthly sensor65 full-natural flow in acre-feet. Sensor8 is dailyCFS; it was not substituted for the available monthly volume.

The regional precipitation input remains5SI October–May, as frozen for transfer. It represents adjacent/northern San Joaquin weather rather than a Kings-specific basin rainfall measurement. Flow controls are the Kings target station's own preceding October–April and, in the stronger model, October–May totals.

## Ground snow selection and support

CDEC station metadata, geographic distance and data completeness were examined before forecast results. WWC West Woodchuck Meadow is the nearest in-basin station with dailySWE,14.27km from the watershed centroid and9100ft elevation. Four closer stations supply monthly manual surveys only. WWC has valid May29–31 readings in22/24years, missing2007 and2011. Its latest valid unflagged nonnegative reading is used; no missing or negative reading becomes zero.

The initial metadata protocol unnecessarily required every station to have all ten initial2000–2009 readings. All five nearby daily stations missed one or two initial years despite extensive overall coverage. A documented **pre-result** `ground_selection_addendum.json` removes that extra station-screen rule and retains nearest WWC, while leaving the model's minimum ten actual prior training years intact. The ground-SWE diagnostic consequently starts in2012 and uses12years. This change was based on source metadata before any Kings scores; it did not relax model or confirmation criteria. The original protocol and all alternative-source coverage snapshots remain available.

All four Kings model variants use matched training/test support within each comparison. The pooled SWE analysis intersects both basins' evaluation years and has only seven calendar years; it cannot be counted as fourteen independent observations.

## Pooling, uncertainty and limits

Each basin's error is normalized by its fixed2000–2009 mean summer runoff, computed entirely from initial training labels: Kings151,470.1AF and SanJoaquin162,073.7AF. Squared and absolute normalized losses have equal basin weight. Circular two-calendar-year bootstrap blocks are shared across basins; missing calendar years remain gaps, and a year missing in either basin is excluded from both. The seed is20260927 with10,000draws. Two nearby basins do not turn14weather years into28independent years.

This is a geographic check on adjacent watersheds sharing regional weather, after the earlier San Joaquin March failure and qualified summer result. It is neither an independent climate sample nor an adjustment for all prior candidate searches. Current MODIS reprocessing and revised CDEC values are not proven available on historical June15issue dates. Nominal14day buffers do not establish original releases. A single ground snow station is not an official basin-wide DWR forecast. No financial alpha or operational readiness is established.

## Reproduction

```bash
python3 src/satellite_snow_kings_validation.py
python3 -m pytest -q tests/test_satellite_snow_daily_validation.py tests/test_satellite_snow_summer_validation.py tests/test_satellite_snow_kings_validation.py
```

Default replay uses committed source pixels and CSVs without network. Explicit `--fetch` needs the raster dependencies listed in `snow_daily/refresh_requirements.txt`; existing source manifests select the same satellite granules. The Kings module reuses the unchanged summer predictor and produces standalone and pooled results. Twenty-four semantic tests cover all three snow studies, including target units, training-only scales, common pooled support, shared calendar years and missing outcomes. All prior failed and qualified results remain separate.

Offline rebuild verifies saved satellite, pixel, flow and ground-source SHA256 hashes before reuse. `target_quality.json` retains monthly flags:291total months have272blank,15r and4e; the72July–September target months have68blank and4r. Flagged targets remain under the frozen current-vintage protocol, without post-result exclusions.
