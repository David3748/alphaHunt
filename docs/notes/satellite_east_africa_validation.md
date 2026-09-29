# Satellite-enhanced ocean temperatures and East African short rains

**The fixed forecast gate fails.** September ocean temperatures reduce point forecast errors for future November–December rainfall, but uncertainty remains too wide to call the improvement verified. This is a rainfall forecast, not a crop, electricity, insurance-loss or market-return forecast.

| Primary model | RMSE, mm | MAE, mm |
|---|---:|---:|
| Trend, previous season, August/September and early-October ground rain | 74.315 | 57.524 |
| Same controls plus September Indian Ocean Dipole and Niño3.4 | **64.495** | **51.799** |
| Expanding historical mean | 70.630 | 55.196 |
| Previous ten training years’ mean | 74.236 | 58.643 |
| Previous season | 110.977 | 89.750 |

Adding ocean temperatures reduces RMSE **13.21%** versus the ground/history regression and **8.69%** versus the strongest baseline, the expanding mean. MAE improves 9.95% and 6.15%, respectively. However, paired circular five-year-block-bootstrap 95% RMSE-gain intervals are **−6.854 to 22.173 mm** versus the regression and **−7.840 to 20.480 mm** versus the mean. Both include no improvement. Satellite predictions have lower squared error than the mean in 12 of 23 years. The preset gate requires at least 5% RMSE improvement over the strongest baseline, lower MAE, and a positive lower confidence bound against every baseline. It fails without any post-fit changes.

The target is mean land rainfall in a fixed rectangle, **5°S–5°N, 34–42°E**, centered on Kenya but crossing national boundaries. Grid cells receive cosine-latitude area weights; November and December monthly totals are added. The mechanism is established: Indian Ocean temperature contrasts affect East African rainfall, as explained by the [UK Met Office](https://www.metoffice.gov.uk/services/government/contingency-planners/seasonal-forecasts-and-climate-drivers-resources). This is a test of a known public relationship, without a claim of novelty or superiority to official seasonal forecasts.

Two September ocean predictors were fixed before examining outcomes: western Indian Ocean SST, 10°S–10°N and 50–70°E, minus southeastern Indian Ocean SST, 10°S–0°N and 90–110°E; and Niño3.4 SST, 5°S–5°N and 170–120°W. All use [NOAA OISST v2.1](https://www.ncei.noaa.gov/products/optimum-interpolation-sst), which combines satellite and in-situ observations. Satellite-only incremental attribution is not established. The [NCEI metadata](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C01606) describes finalized data after about two weeks and historical reprocessing. We assign September SST availability to October 20 at 00 UTC, using 17 days plus a three-day buffer; the forecast issues at noon that day, before November starts. No incomplete September 2026 forecast is emitted.

The independent target is [DWD GPCC Monitoring v2022](https://opendata.dwd.de/climate_environment/GPCC/html/gpcc_monitoring_v2022_doi_download.html), a 1° gauge-only monthly product based on SYNOP/CLIMAT observations. All 88 November/December target months, 1982–2025, contain 77 valid regional land cells and **14–38 gauges inside the rectangle**. This is still an interpolated regional estimate; cell-level rain need not have a colocated gauge. The source uses climatological information during interpolation. Product latency is approximately two months, so September GPCC is unavailable for an October forecast. Prior-season labels receive a conservative 90-day lag. Cite Schneider et al. (2022), [doi:10.5676/DWD_GPCC/MP_M_V2022_100](https://doi.org/10.5676/DWD_GPCC/MP_M_V2022_100).

Recent-weather controls instead use the [CPC Unified daily gauge analysis](https://www.cpc.ncep.noaa.gov/products/precip/realtime/GIS/retro.shtml), summed for August, September and October 1–9. A ten-day availability buffer puts the latest ground observation before the issue. **CPC explicitly warns of poor analysis quality in tropical Africa.** Its [official README](https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/DOCU/PRCP_CU_GAUGE_V1.0GLB_0.50deg_README.txt) also distinguishes historical and real-time station networks, describes revisions, and notes uncertain daily accumulation boundaries across countries. These limitations apply to the comparator. They are not removed by reporting complete grid coverage. The independent target and the climate-mean baselines provide additional comparisons.

Both regression models use year trend, prior November–December rain, and identical known local rainfall controls. The satellite model adds only the two fixed September SST predictors. Ridge penalty is fixed at 5, with an unpenalized intercept, training-only standardization and predictions clipped at zero. Training expands from 1982 and requires 17 complete prior years. Current or future labels never enter fitting or feature eligibility; an unknown current outcome can still receive a forecast. All baselines share eligible years. The bootstrap resamples calendar-year positions in five-year blocks, retaining missing-year gaps.

The planned evaluation covers 1999–2025. No 1981 GPCC prior season exists, and August 1985, September 1986 and September 2004 each have one entirely missing CPC day. Their monthly controls remain missing; they are never partial sums or zero-filled. Forecasts abstain in 1999–2001 for insufficient training and in 2004 for unavailable current controls, leaving **23 evaluated years: 2002–2025 except 2004**. Training exclusions and abstentions are recorded individually.

Two changes occurred before the first fit and are preserved with timestamps and hashes. First, a timing audit strengthened the original August/September baseline by adding already observable October 1–9 rain to both models. The original information set remains a secondary diagnostic: satellite RMSE 61.089 mm versus its regression 73.285 mm and the mean 70.630 mm, but its uncertainty intervals also cross zero. It cannot replace the primary result. Second, the NOAA PSL GPCC v2020 mirror had time coordinates through 2025 but missing values after April 2021. The same gauge-monitoring target was retrieved directly from DWD in v2022 for every year, rather than mixing product versions. The stale mirror, failed source evidence, original protocol and both pre-fit amendments remain available.

The default run is offline with NumPy and pandas:

```sh
python3 src/satellite_east_africa_validation.py
python3 -m pytest -q tests/test_satellite_east_africa_validation.py
```

Nine semantic tests cover release chronology, current/future outcome perturbations, missing current outcomes, future predictors, identical recent-rain controls, early-October cutoffs, missing months and claim limits. Inputs, grids, gauge coverage, per-year forecasts, both model specifications, protocols, code hashes, source manifests and results are under `results/satellite_validation/east_africa/`. Regional OISST/CPC arrays are lossless local HDF5 snapshots of OPeNDAP subsets; GPCC snapshots retain precipitation and gauge-count grids plus original download hashes. Data provided by NOAA PSL, Boulder, Colorado, USA, NOAA NCEI/CPC, and DWD GPCC.

For explicit network refresh, install `refresh_requirements.txt` in that directory, then run `fetch_inputs.py`, `fetch_october.py` and `fetch_gpcc.py`. The NOAA subset endpoint returned disk-full errors during retrieval; its OPeNDAP service supplied identical archive regions. Default evaluation does not depend on that service or local scratch downloads.

These are present-archive retrospective results, not a reconstruction of exact historical releases. Many different mechanisms were considered in the broader project without a familywise adjustment. No box, period, feature, penalty, gate or missing-data rule was changed after observing the forecast metrics.
