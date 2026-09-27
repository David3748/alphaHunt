# Ground-weather comparator

`monthly_weather.csv` is the primary comparator. Station selection used distance from Topaz (35.383, -120.067) and valid-day coverage in 2015–2025, without reading generation outcomes. The parent fixed the closest station separately for each variable before any model fit; `per_variable_selection_addendum.json` preserves this change from the initial same-station rule.

| Variable | Station | Distance | Valid-day coverage, 2015–2025 |
|---|---|---:|---:|
| TMAX | La Panza RAWS, USR0000CLAP | 10.93 km | 99.975% |
| TMIN | La Panza RAWS, USR0000CLAP | 10.93 km | 99.950% |
| PRCP | Salinas Dam, USC00047672 | 39.95 km | 100% |

The composite has 132/132 complete months in 2015–2025. The separately preserved airport sensitivity is San Luis Obispo McChesney Field, USW00093206, at 54.80 km, with 131/132 complete months; March 2019 is incomplete. The earlier closest all-network selection, Twitchell Dam, is retained but is not the primary model.

Monthly dates are month starts. `tmean_c` and `dtr_c` are averages of paired daily `(TMAX+TMIN)/2` and `TMAX-TMIN`; `prcp_mm` is the sum of observed daily precipitation. Features require at least 90% of calendar days, including paired temperature coverage. Quality-flagged observations and -9999 are missing, never zero. There is no imputation. Rain amounts are not scaled for missing days. `coverage` is the minimum coverage across the three elements and temperature pairs. The parent prespecified OLS weather features `tmean_c`, `dtr_c`, and `log1p(prcp_mm)`.

`assumed_available_date` is month-end + 14 days, **an assumption, not original-vintage certification**. [NOAA's GHCN-Daily documentation](https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily) describes daily updates and real-time streams, followed by archive replacements typically 45–60 days after month-end, and continuing reprocessing. The selected archive has RAWS source flag U for temperature and WxCoder3 flag 7 for rain. The airport uses flags W/1, not original real-time flag A. Consequently neither file proves that its exact historical values were available after 14 days. At a four-month issue delay, routine archive latency is less concerning, but later revisions remain untested.

Run offline:

```sh
python3 results/satellite_validation/goes_solar/ground_weather/rebuild.py
python3 -m pytest -q results/satellite_validation/goes_solar/ground_weather/test_ground_weather_rebuild.py
```

`manifest.json`, raw files, and the full distance/coverage audit retain source URLs, hashes, retrieval timestamps, and HTTP modification dates. Raw station files and station inventory use deterministic gzip compression (22 MB reduced to 3.6 MB); the manifest records compressed hashes and the original response-byte hashes, and rebuilding verifies the decompressed original hashes. Five semantic tests cover missing/quality flags, units, calendar denominators, paired coverage, and mixed-station rejection. The extractor reads no generation labels or forecast scores.
