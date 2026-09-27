# Atlantic seasonal hurricane-risk forecast

**The frozen forecast gate fails.** For 27 chronological held-out years, 1999–2025, adding June satellite-enhanced sea temperatures improves the regression benchmark but does not beat a simple recent climatology on RMSE. No formula, region, horizon, penalty or gate was adjusted after viewing results.

| Model | RMSE, ACE | MAE, ACE |
|---|---:|---:|
| Trend, prior season, current June–July activity | 57.143 | 45.263 |
| Same controls plus June MDR and Niño3.4 SST | 54.192 | 38.893 |
| Expanding historical mean | 55.244 | 44.935 |
| Previous ten years' mean | **52.608** | 40.925 |
| Prior-season persistence | 56.491 | 45.364 |

Satellite-enhanced SST reduces RMSE **5.16%** versus the regression controls and reduces MAE **14.07%**. Its paired five-year-block-bootstrap 95% RMSE-gain interval is **−2.580 to 10.092 ACE**, crossing zero. Against the strongest baseline, the recent ten-year mean, its RMSE is **3.01% worse**. All 27 planned years are eligible. This is not verified usefulness under the predeclared gate and is not a trading-alpha result.

The physical hypothesis is established: tropical Atlantic warmth and Pacific ENSO conditions influence Atlantic storm development through thermodynamics and wind shear, as explained by [NOAA AOML](https://www.aoml.noaa.gov/general/project/hrdls3.html). The two boxes were frozen before examining outcomes: main development region 10–20°N, 80–20°W; Niño3.4 5°S–5°N, 170–120°W. June grid cells are weighted by cosine latitude, with land/missing cells excluded and exact box bounds reapplied after server subsetting.

[NOAA OISST v2.1](https://www.ncei.noaa.gov/products/optimum-interpolation-sst) genuinely incorporates satellite observations, alongside ships, buoys and Argo. It is a **satellite-enhanced blend**, not an isolated satellite-only measurement or proof that satellites add value over every in-situ SST product. Two June-only regional NetCDF subsets, retrieved from [NOAA PSL THREDDS](https://psl.noaa.gov/thredds/catalog/Datasets/noaa.oisst.v2.highres/catalog.html), span 1982–2025. The [NCEI metadata](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C01606) describes near-real-time preliminary files, finalized files after about two weeks, and version-2.1 reprocessing from 2016. June SST gets a conservative 31-day lag, placing assumed availability on July 31 before an August 1 forecast. July SST is excluded. Original monthly publication timestamps are not reconstructed.

The independent target is August 1–November 30 Atlantic storm energy derived from [NHC HURDAT2](https://www.nhc.noaa.gov/data/), version `hurdat2-1851-2025-091226.txt`. We sum squared winds in knots divided by 10,000 at 00/06/12/18 UTC for tropical storm, hurricane or subtropical storm states with winds ≥34 kt, consistently across years. Extra landfall/intensity timestamps, extratropical phases and depressions are excluded. This follows the six-hour storm-energy construction described by [NOAA CPC](https://www.cpc.ncep.noaa.gov/products/outlooks/hurricane2020/August/Background.html). It is a basin risk index, not insured losses, landfalls or offshore production losses.

Every August 1 forecast uses expanding training years from 1982 through the previous year, with at least 17 observations. Both regression models use a fixed ridge penalty of 5 and training-only feature standardization. The common controls are year trend, previous August–November ACE and current June–July ACE. The latter stops at July 31, 18 UTC. The satellite model adds only the two frozen June SST means. Predictions are clipped at zero. Four baselines, identical eligible years and paired circular five-year bootstrap blocks were fixed in advance. Per-year predictions are preserved for inspection.

HURDAT2 is a post-analysis best-track archive: current June–July features and historical labels can contain revisions to contemporaneous wind reports. OISST is also a current archive. These limits preclude original-vintage certification, but do not alter the negative result of this scientific retrospective test. The forecast is not claimed to outperform existing NOAA/CSU forecasts, and numerous candidates were examined in the broader project without a familywise adjustment.

Reproduce offline with core NumPy/pandas dependencies:

```sh
python3 src/satellite_hurricane_validation.py
python3 -m pytest -q tests/test_satellite_hurricane_validation.py
```

Eleven semantic tests cover ACE construction, source/target timing, current June–July control inclusion, no future-label/SST influence, missing years and claim limits. `results/satellite_validation/hurricane/` contains raw regional grids, the compressed original NHC archive, source hashes/URLs, the pre-fit protocol and code hash, annual activity, panel and predictions. Explicit network refresh uses `fetch_inputs.py` in that directory and additionally requires requests and h5py. Data provided by NOAA PSL, Boulder, Colorado, USA, and NOAA NCEI/NHC. OISST citation: Huang et al. (2020), [doi:10.25921/RE9P-PT57](https://doi.org/10.25921/RE9P-PT57), June 1982–2025 subsets described above.

A post-fit implementation audit separated unknown current outcomes from forecast eligibility. Missing target outcomes now retain predictions, training excludes missing past labels, and scoring waits for observed labels. The immutable first result and before/after hashes are preserved; all historical predictions and metrics are exactly unchanged.
