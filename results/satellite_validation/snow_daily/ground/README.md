# Friant watershed and public ground controls

The primary control is the official San Joaquin 5-station precipitation index, summed from October through March for each water year, converted from inches to millimetres. Geography and complete 2000–2025 coverage determined this selection before any runoff outcomes were joined or models fitted. `selection_protocol.json` records that decision. [DWR identifies the index among its operational snow and precipitation products](https://water.ca.gov/Programs/Flood-Management/Flood-Data/Snow-Surveys).

`winter_ground.csv` provides 26 complete regional precipitation seasons, local Huntington Lake rain for 24 seasons, and late-March Huntington Lake snow water equivalent for 24 seasons. The regional index includes Calaveras Big Trees, Hetch Hetchy, Yosemite, North Fork, and Huntington Lake; it represents a broader region than the specific Friant watershed. CDEC's station metadata and raw monthly/daily CSV responses are retained alongside hashes and URLs.

The local HNT station is at 37.227570, -119.220482, elevation 7,000 feet, within the upstream basin. Its unflagged incremental daily rain must cover at least 90% of October–March days; 2013 and 2023 fail and stay missing. No rainfall is imputed or scaled for missing days. The accumulated HNT record is retained for inspection but is not substituted to conceal missing incremental readings. Ground SWE uses the latest valid reading in March 29–31, with negative sensor values treated as invalid rather than zero. The 2015 and 2023 seasons lack such a reading. Raw observations, including their original flags, remain available.

The primary monthly index retains official revised values (flag r), with their count reported explicitly. Monthly timestamps label the month, rather than publication. **Exact April 1 availability of these current archive values has not been verified.** The optional October–February sum allows a conservative lag sensitivity; it is not a second selected model. Daily CDEC observation times also do not establish their original public release time. These limitations concern all claims of operational forecasting.

`basin_full.geojson` is the [USGS NLDI upstream basin](https://api.water.usgs.gov/docs/nldi/basin/) of [USGS 11251000, San Joaquin River below Friant](https://waterdata.usgs.gov/monitoring-location/USGS-11251000/). Its geodesic area is 1,679.11 square miles, compared with the site's published 1,676 square miles. The full-catchment boundary is about 0.19% larger; the endpoint for splitting the outlet catchment failed with HTTP 502. This difference is disclosed instead of presenting the polygon as an exact surveyed reservoir boundary. The NLDI method follows upstream NHDPlusV2 catchments. Basin metadata, site metadata, and source responses are preserved.

Reproduce the features and semantic checks offline:

```sh
python3 results/satellite_validation/snow_daily/ground/rebuild.py
python3 -m pytest -q results/satellite_validation/snow_daily/ground/test_snow_ground_rebuild.py
```

The extractor reads no runoff outcomes and fits no forecast. Snow-water comparisons should use identical complete-year support so missing years do not create an apparent satellite advantage.
