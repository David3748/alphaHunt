# Operational GOES irradiance archive extraction

This extractor reconstructs irradiance features at Topaz Solar Farm and California Valley Solar Ranch from public operational GOES objects. It does not load generation outcomes or fit a model; the separate frozen solar-validation protocol controls the forecast experiment.

NOAA's [DSR product description](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01524) defines instantaneous surface shortwave irradiance, including direct and diffuse radiation, in W/m². These are instantaneous flux observations, not hourly energy totals. The [public GOES-17 S3 bucket](https://noaa-goes17.s3.amazonaws.com/index.html) and corresponding GOES-18 bucket allow unauthenticated listing and downloads. Their DSR history starts on December 5, 2019, although the underlying instrument/product is older.

## Source selection and availability

The extractor uses GOES-17 through January 4, 2023 at 18:00 UTC, then GOES-18, following [NOAA's operational handover](https://www.ospo.noaa.gov/data/messages/2023/01/MSG_20230104_1805.html). Each sampled hour selects its earliest scan whose file creation **and** S3 object LastModified precede the forecast issue, month-end plus 14 days. The [GOES Product Definition and User's Guide](https://www.goes-r.gov/users/docs/PUG-main-vol1.pdf) distinguishes observation start/end and file creation. A creation timestamp alone does not prove public dissemination; the S3 timestamp supplies an additional conservative object-availability gate. A later object replacement or replication may cause an old measurement to be excluded even if an older version was once available. Historical S3 version logs were not recovered.

For example, the January 1, 2020 20:00 UTC GOES-17 object began observing at 20:01:21.4, finished at 20:03:58.7, was created at 20:06:46.5, and has S3 LastModified 20:07:01. Compact original files and their hashes are retained under `examples/`. Every extracted object retains its key, URL, size, ETag, observation start, creation timestamp, S3 modification, and decoded point values. Small-file SHA256 is recorded after whole-file reads; large files are accessed with HTTP byte ranges, so their ETags are preserved without falsely labeling them full-file SHA256 hashes.

The netCDF creation attribute must agree with the filename. Files lacking the expected real-time production metadata are excluded and their rejection records remain in the snapshot. Daytime fill values are never converted to zero. The signed integer packing used by older files is decoded as unsigned before applying scale factors, while retaining the original fill mask.

## Fixed geography and product change

The fixed Topaz cell is latitude 35.25–35.50°, longitude -120.25–-120.00°. The adjacent CVSR cell is latitude 35.25–35.50°, longitude -120.00–-119.75°. Older CONUS files provide one 0.25° pixel for each cell. New full-disk files are projected with their own geostationary projection metadata; all pixel centers inside each same geographic cell are averaged. At least 90% of its pixels must contain valid DSR between 0 and 1,500 W/m² with quality flag 0 or 1. Flag 1 includes degraded observations; their fraction is preserved rather than represented as exclusively good quality.

The [GOES-18 full-validation notice](https://www.ospo.noaa.gov/operations/goes/product-quality-overview/ps-pvr/goes-18/ABI/Shortwave%20Radiation%20Budget%20%28Downward%20S-W%20Radiation_%20Surface%20_%20Reflected%20S-W%20Radiation_%20TOA%29/Full/GOES-18_ABI_L2_SRB_Full_ReadMe.pdf) documents a substantial change at **16:40 UTC on April 17, 2024**: the baseline hourly 0.25° CONUS product was replaced by a 10-minute 2 km full-disk Enterprise product. Keeping the geographic footprint fixed limits spatial mismatch; it does not remove the algorithm change. The forecast evaluation must retain the prespecified regime split. GOES-17 also had documented thermal/cloud-mask weaknesses; its [provisional quality notice](https://www.ospo.noaa.gov/operations/goes/product-quality-overview/ps-pvr/goes-17/ABI/Shortwave%20Radiation%20Budget/Provisional/GOES-17_ABI_L2_SRB_Provisional_ReadMe.pdf) describes product limitations. This archive provides timely operational measurements, not uniform perfect-quality reanalysis.

## Integration and missing observations

The frozen sample uses UTC hours 00, 02, 12, 14, 16, 18, 20, and 22. Other two-hour grid points are nighttime at both sites. The [NOAA solar-position equations](https://gml.noaa.gov/grad/solcalc/solareqns.PDF) identify geometric nighttime, when zero solar flux is assigned without downloading a missing daytime measurement. The daylight/night rule is fixed independently of plant outcomes. Actual scan timestamps, including minute offsets, determine trapezoidal integration weights.

Daily energy integrates the complete two-hour grid through the following day's 00 UTC endpoint and converts Wh/m² to kWh/m². A missing daylight observation makes that daily integral missing. The monthly estimate is the mean of complete daily integrals and is eligible only with at least 90% of the expected daylight slots available before issue. The output also reports complete days, calendar days, complete-day fraction, expected/valid daylight slots, quality fractions, and latest accepted source availability. An eligible month may have fewer than 90% complete days: Topaz January 2020 has 94.87% valid daylight slots but only 23 of 31 complete days. If missingness follows clouds, selecting complete days can bias monthly irradiance. The frozen slot-coverage gate was not changed after this issue was identified. This sparse sampling estimates monthly irradiance; it does not capture every sub-hour cloud fluctuation. Days and months follow UTC, which differs slightly from the local calendar used by the facility.

## Reproduction

Offline integration reads saved point provenance; it makes no network calls:

```sh
python3 src/satellite_goes_extract.py --compact
python3 -m pytest -q tests/test_satellite_goes_extract.py
```

The large manifest and point snapshots support deterministic gzip compression. `monthly_irradiance.csv` and `daily_irradiance.csv` remain plain CSV files. `extraction_summary.json` records hashes, coverage and download accounting. Source-documentation URLs are also saved in `source_documentation.json`.

Optional new retrieval uses resumable point checkpoints and ranges for the large full-disk files:

```sh
python3 -m pip install -r results/satellite_validation/goes_solar/extraction_requirements.txt
python3 src/satellite_goes_extract.py --fetch --workers 12 --compact
```

Refresh changes the source retrieval vintage; the saved archive is the reproducible input to the reported study.
