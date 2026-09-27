# Sierra satellite snow cover: feasibility audit

**Status: not validated. No forecast was fitted.** Open satellite measurements and
independent runoff labels exist, but the readily downloadable long snow archive
is unsuitable for a causal historical April forecast without reconstructing its
publication vintages. This is a data-feasibility result, not evidence against the
physical usefulness of snow observations.

## Sources that work without credentials

- [Microsoft's MODIS snow-cover catalog](https://planetarycomputer.microsoft.com/dataset/modis-10A2-061)
  exposes MOD10A2/MYD10A2 Collection 6.1 imagery through its public STAC API. NASA
  describes [MOD10A2](https://modis-snow-ice.gsfc.nasa.gov/?c=MOD10A2) as an eight-day
  maximum snow extent product. Both Terra and Aqua occur in the collection;
  platform selection must be fixed rather than accidentally mixing observations.
- [NSIDC Snow Today download instructions](https://nsidc.org/snow-today/get-snow-data)
  provide working anonymous FTP access at `dtn.rc.colorado.edu`, under
  `/shares/snow-today`. Regional summary files, including HUC regions, are
  documented by the [viewer guide](https://nsidc.org/snow-today/how-use).
- California DWR publishes independent monthly full-natural-flow observations.
  [CDEC SBF metadata](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=SBF)
  identifies the current San Joaquin/Friant runoff endpoint. SJF metadata says
  its full-natural-flow sensors moved to SBF on October 3, 2024. Sensor 65 is the
  monthly acre-foot series. MIL is the reservoir station and should not be
  silently substituted for full-natural-flow labels.

## Why the long archive cannot be backdated

The official [SPIRES_HIST Version 1 description](https://nsidc.org/data/spires_hist/versions/1)
states that a year's snow-free background uses reflectance typically measured in
August-September of that year. It also describes interpolation and smoothing.
An April predictor assembled from these completed-year values can therefore
depend on future information. The HIST coverage, March 2000-September 2025, is
not a record of April-available measurements.

[SPIRES_NRT Version 1](https://nsidc.org/data/spires_nrt/versions/1) lists coverage
beginning October 2025. Older Snow Today display archives exist, but their
algorithms and contents differ: the inspected 2024-2025 directories contain SWE
summaries, while 2021-2022 also contain snow-cover plot summaries. In-situ SWE is
not a satellite signal. No homogeneous long sequence of original April releases
was established in this bounded audit.

Live STAC checks independently confirm delayed production of historical MODIS
Collection 6.1 scenes over the Sierra:

| Acquisition year, late March/early April | Terra product creation |
|---|---|
| 2004 | May 1, 2020 |
| 2014 | September 10, 2021 |
| 2018 | November 19, 2021 |
| 2020 | November 30, 2020 |

These products cannot be assigned artificial acquisition-plus-seven-day release
dates. Recent causally available Collection 6.1 seasons could support a smaller
study, but few independent annual runoff outcomes remain, and source first-seen
timestamps still require an audit. Current archive creation metadata is useful
evidence of exclusion, not proof of historical delivery.

## Requirements before fitting

A credible extension needs basin polygons matched to the runoff station;
cloud/no-data handling fixed before evaluation; forecasts issued after every
included image is available; and an incremental comparison against observed
April snow-water content, winter precipitation and/or DWR's contemporaneous
seasonal forecast. Climatology alone would be too weak a benchmark. Use a fixed
chronological test period and resample whole water years, since Sierra basins
share storms. Freeze all choices before inspecting held-out runoff performance.

`results/satellite_validation/snow/` preserves endpoint evidence, a runoff
download, FTP metadata and the live archive timing samples. No RMSE improvement,
utility pass or trading result is claimed.
