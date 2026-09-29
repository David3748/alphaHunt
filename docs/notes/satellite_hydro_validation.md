# Satellite reservoir levels and Sobradinho generation

**Result: the frozen incremental-usefulness gate fails.** Satellite altimetry contains a useful reservoir-state signal relative to past generation, seasonality, and inflow, but readily available ground reservoir levels are better. The satellite adds no reliable improvement once those levels enter the model. This is an independently implemented future-month forecast study using genuine satellite measurements, not an operationally certified backtest or verified trading alpha.

| Model | Test RMSE, MW | Test MAE, MW |
|---|---:|---:|
| Seasonality, trend, lagged generation and inflow | 121.893 | 81.675 |
| Same controls plus satellite level and change | 98.425 | 65.984 |
| Same controls plus ground reservoir level | 89.873 | 58.196 |
| Ground-level model plus satellite | 89.717 | 58.682 |
| Two-month generation persistence | 124.982 | 83.742 |

The test covers 88 eligible months in January 2018–December 2025. Satellite features reduce RMSE by **19.253%** versus generation/inflow controls; paired circular 12-calendar-month block bootstrap gives a 95% RMSE-gain interval of **9.217–36.991 MW**. Against the ground-level model, the satellite's incremental gain is **0.174%**, its interval is **−1.230–1.570 MW**, and MAE worsens **0.835%**. The predeclared gate requires at least 5% lower RMSE, lower MAE and a positive confidence-interval lower bound for both comparisons; it therefore fails. No model, target, split, feature lag or gate was changed after inspecting results.

## Sources and mechanism

Reservoir elevation reflects the water available for generation and hydraulic head. NASA's [GWM lake service](https://earth.gsfc.nasa.gov/gwm/lake/) distributes radar-altimeter lake/reservoir measurements and explains the mission, footprint, and quality limitations. Its linked [HEP text archive](https://har.gsfc.nasa.gov/pub/danu/power/Reservoir_data/GWM_HEP_reservoirs.tgz) contains `lake000345.10d.2.txt`, Sobradinho, processing version TPJOJS.2.5. The saved raw file identifies observation time, relative water height, error estimate and GDR/IGDR source flag. This study uses only post-July-2008 records and excludes TOPEX/Poseidon: the [official product table](https://earth.gsfc.nasa.gov/gwm/html/WATER-MONITOR.LakesReservoirs.10day.xlsx) warns about its TP/Jason-2 merger, narrow crossing and distance from the dam. Valid observations require error ≤1 m, valid height/time and no ice flag. Simultaneous valid Jason-3/Sentinel-6 tandem observations are averaged, without selecting a measurement according to outcomes. There are 693 valid distinct observation timestamps in the snapshot.

The independent outcome is [ONS verified hourly generation](https://dados.ons.org.br/dataset/geracao-usina-2), restricted to stable official plant identifier `BAUSB`. Its displayed name changes from `Sobradinho` to `UHE Sobradinho` in October 2025; the plant is unchanged. The [ONS daily hydraulic dataset](https://dados.ons.org.br/dataset/dados-hidrologicos-res) provides actual inflow and upstream ground water level. These offer particularly relevant controls for weather and storage. Monthly means require ≥95% of expected hourly/daily observations. The selected original rows are preserved as small Parquet snapshots; source URLs, projections, filters, HTTP ETags/modification times and snapshot SHA-256 values are recorded. ONS describes recurring consistency checks and possible post-publication updates.

## Frozen forecasting design and its limits

Forecast issue is midnight on the first day of the target month. Models use an expanding training window starting January 2010, with at least 72 complete prior months and training targets whose assumed release precedes the issue. The baseline is OLS with annual/semiannual sin/cos terms, a linear trend, two-month and twelve-month lagged generation, and two-month lagged inflow. Satellite additions are the latest eligible relative height and its change from a measurement at least 30 days earlier. The stronger model includes two-month lagged ground reservoir elevation. All models use identical test months.

We impose a 90-day satellite lag: [AVISO's primary source table](https://www.aviso.altimetry.fr/en/data/products/wind/wave-products/gdr-ogdr-osdr-ra2-wwv.html) lists Jason-3 GDR delivery below 90 days and Jason-2 about two months. This conservative upstream-product lag is **not proof of historical GWM publication dates**. The latest accepted measurement is at most 130 days old and its comparison reading at most 180 days old; there is no interpolation or smoothing. ONS monthly observations have an assumed 30-day lag. Under the strict before-issue rule, January observations are not yet available at March 1, causing all eight March abstentions; they were not silently included or replaced.

The sources are current archives. Mission bias corrections, product revisions and original data vintages were not reconstructed. Consequently the statistical intervals describe this retrospective archive experiment, not certified historical operational performance. Eight calendar years also limit independent evidence, and many other mechanisms were examined in the broader project without a familywise correction. Ground storage is public at this site; satellite value where gauges are unavailable is a hypothesis for a separate test, not demonstrated generalization here. No financial returns were tested for this mechanism.

`protocol.json` was written before examining generation values. `code_freeze.json` captures the initial pre-fit implementation. Two runs stopped before fitting: one exposed legitimate simultaneous tandem-satellite observations, and another exposed numeric strings in old ONS Parquet files. The two `pre_fit_*` records preserve these ingestion fixes and updated code hashes. Their fixes do not change target, evaluation dates, latency, quality cutoff, models or gate.

## Reproduction

```sh
python3 -m pip install -r results/satellite_validation/hydro/requirements.txt
python3 src/satellite_hydro_validation.py
python3 -m pytest -q tests/test_satellite_hydro_validation.py
```

Default execution is offline and verifies every selected source snapshot checksum. Core dependencies are NumPy, pandas and PyArrow. To reconstruct downloads explicitly:

```sh
python3 -m pip install -r results/satellite_validation/hydro/refresh_requirements.txt
python3 results/satellite_validation/hydro/fetch_inputs.py
```

The retrieval helper additionally uses requests, fsspec and aiohttp. It reads projected columns from official Parquet sources and saves only the selected reservoir/plant. Existing selected ONS files are reused; remove a selected file explicitly before intentionally refreshing that source. Full NASA/ONS multi-plant archives are not required for offline reproduction. Results, per-month forecasts, yearly metrics, data provenance and frozen protocols are under `results/satellite_validation/hydro/`.
