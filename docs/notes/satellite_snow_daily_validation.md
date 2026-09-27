# Raw daily MODIS snow and San Joaquin seasonal runoff

The frozen experiment failed to establish incremental usefulness. For 15 chronological held-out years (2010–2024), adding March MODIS snow presence to winter precipitation and prior flow **increased RMSE by 1.32%** and MAE by 1.06%. The paired two-year-block 95% interval for RMSE reduction is −13.65% to +9.16%. The deliberately weaker persistence baseline was beaten, but that does not establish satellite value beyond available weather observations.

| Common support | Model | RMSE, acre-feet | MAE, acre-feet |
|---|---|---:|---:|
| 2010–2024, 15 years | Precipitation + prior flow | 610,322 | 388,964 |
| Same | Plus MODIS snow | 618,362 | 393,089 |
| Same | Historical seasonal mean | 896,542 | 754,766 |
| Same | Prior-year seasonal runoff | 1,220,745 | 968,574 |
| Ground-SWE support, 13 years | Precipitation + flow + ground SWE | 627,142 | 415,644 |
| Same | Plus MODIS snow | 597,210 | 384,799 |

The ground-SWE diagnostic improves RMSE by 4.77% and MAE by 7.42%, with RMSE-reduction interval −15.17% to +12.62%. All four diagnostic models use identical training and evaluation support. Missing HNT SWE excludes 2015 and 2023; this changes both training histories and evaluated years. It cannot replace the failed predeclared full-support result.

## Frozen design and timing

`results/satellite_validation/snow_daily/protocol.json` was saved before any snow/runoff join. A pre-evaluation `protocol_addendum.json` changes the issue from April 1 to **April 15**, allows a nominal fourteen-day delivery buffer for March composites and ground totals, adds the matched ground-SWE benchmark, and freezes a separate 2026 confirmation. The target, April–July full-natural runoff, is therefore a partly begun seasonal nowcast. The initial protocol remains preserved; the addendum supersedes the April 1 assumption retained in the original ground-source CSV.

The feature uses only Terra MOD10A1 Collection 6.1 daily March observations. Values 0–100 with Basic QA 0 or 1 are valid; positive screened NDSI means snow detected. For each basin pixel, calculate the fraction of valid days with snow, requiring three valid March days. Average these pixel frequencies over the basin, requiring at least 90% of basin pixels to qualify. Missing cloud, water, night, no-decision and fill codes never become zero snow. This is **snow-presence frequency, not snow-water equivalent, depth or fractional snow-covered area**. No subsequent-month observation, temporal interpolation or SPIRES same-year August/September background enters the feature.

The primary model adds this single snow feature to October–March regional precipitation and October–February natural flow. Expanding ordinary least squares begins after ten eligible years; negative predictions are clipped to zero for every regression. There is no parameter, year, threshold or target search. Forecast existence depends on observable inputs and past labels, never on the current outcome. Unknown outcomes can still receive forecasts; evaluation alone requires actual runoff.

## Sources and extraction

The [NASA MOD10A1 description](https://modis-snow-ice.gsfc.nasa.gov/?c=MOD10A1) describes a daily product from the best available same-day observation. The [NSIDC Collection 6.1 guide](https://nsidc.org/sites/default/files/mod10a1-v061-userguide_1.pdf) defines the NDSI and QA codes. The [MODIS Collection 6 guide](https://landweb.modaps.eosdis.nasa.gov/data/userguide/MODIS-snow-user-guide-C6.pdf) explains that positive NDSI can indicate snow and that NDSI must not be mistaken for the old fractional-snow-cover field. Low-light, forest, cloud, terrain and detection-screen errors remain possible.

Anonymous [Planetary Computer STAC](https://planetarycomputer.microsoft.com/api/stac/v1/search) and public SAS access supplied 755 unique daily Terra source pairs from one tile, `h08v05`. All pairs downloaded successfully. Thirteen superseded granules are recorded; the latest processing timestamp per acquisition day was selected deterministically before model evaluation. The source manifest retains unsigned asset URLs, item hashes, creation timestamps and compact pixel-snapshot hashes. Original pixel values and Basic QA for 20,267 basin pixel centers occupy about 6.3 MB; HTTP raster windows avoided downloading global imagery. All 2000–2024 feature years pass the frozen pixel-coverage rule. Missing days remain visible: March 2000 has 27 scenes, 2002 has 22, and 2024 has 24. March 2025 has none and is an explicit abstention.

The [USGS NLDI](https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-11251000/basin?f=json) supplies the full catchment upstream of San Joaquin River below Friant. Its unsplit upstream area is 1,679.11 square miles versus 1,676 published for the gauge; the roughly 0.19% difference is documented in `ground/README.md`. The raster is an equal-area sinusoidal grid, so unweighted pixel averages are area-weighted at the selected pixel-center resolution.

CDEC [SBF metadata](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=SBF) and sensor 65 monthly full-natural flow supply the four-month acre-foot target and prior flow. SJF's full-natural-flow sensors moved to SBF in 2024. The geographically selected regional five-station San Joaquin precipitation index (5SI) provides six complete October–March monthly values for every year. HNT Huntington Lake provides the optional local precipitation and late-March snow-water-equivalent measurements. Peer selection was frozen before reading runoff outcomes; original responses, flags, URLs, hashes and rebuild code are under `ground/`.

## Limits and blocked confirmation

This is a current-vintage scientific study. Older daily granules were processed in 2020–2021, and two March 2023 granules have April 25 creation dates, after the nominal April 15 issue. Current CDEC monthly precipitation and natural-flow archives also include revisions. The fourteen-day buffer is an information-age assumption, not proof that these exact values were public then. No operational first-release, proprietary-information or trading-alpha claim follows.

The separate 2026 confirmation was frozen before the main fit. It remains **unscored**, not selectively omitted: the anonymous mirror has no March 2026 granules, and the primary NSIDC HDF endpoint redirects to Earthdata Login (HTTP 302). `confirmation_access.json` records the query and access evidence. Peer-supplied 2026 ground features remain separate. Neither browse images lacking QA nor another satellite product were substituted, and 2026 runoff outcomes were not fetched for a signal that cannot be built. The same access limitation prevents a 2025 forecast from this mirror.

One basin and 15 annual labels determine statistical power, regardless of how many daily pixels are read. Missingness can depend on weather. Ground SWE at one station is not an official basin-wide DWR seasonal forecast. Several other satellite mechanisms were already examined in this research, so the nominal bootstrap interval is not a family-wise discovery guarantee.

## Reproduction

From the repository root, default execution needs only NumPy and pandas and uses committed compact snapshots:

```bash
python3 src/satellite_snow_daily_validation.py
python3 -m pytest -q tests/test_satellite_snow_daily_validation.py
```

An explicit current-archive refresh also needs requests and rasterio:

```bash
python3 -m pip install -r results/satellite_validation/snow_daily/refresh_requirements.txt
python3 src/satellite_snow_daily_validation.py --fetch --workers 8
```

The output includes annual snow coverage, all training and evaluation years, forecasts, matched SWE forecasts, abstentions, uncertainty intervals and hashes. Thirteen semantic tests cover source screening, missingness, coverage gates, chronology, matched SWE support, bootstrap reproducibility and forecasts with unknown outcomes.
