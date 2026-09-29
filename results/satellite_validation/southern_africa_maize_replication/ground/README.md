Ground rainfall controls for the fixed Zambia/Zimbabwe confirmation
==================================================================

`ZM_ground_augsep.csv` and `ZW_ground_augsep.csv` contain August and September
1982–2024 monthly rainfall totals in millimetres. They contain no crop outcomes.
The requested confirmation countries were fixed by the main protocol before
this extraction. This directory did not select countries or optimize a crop belt.

The geometry is the [World Bank Official Boundaries Admin0 10m archive](https://datacatalogfiles.worldbank.org/ddh-published/0038272/DR0046659/wb_countries_admin0_10m.zip),
linked directly from its [official catalog](https://datacatalog.worldbank.org/search/dataset/0038272/world-bank-official-boundaries).
The HTTP version is `2026-05-14T09:49:40.4368371Z`, and the full downloaded ZIP
SHA256 is `019642b2a80f07fdae56baf15760971bf45119110bb0d9a90255df89be7e635c`.
The national polygons were selected using ISO_A2 ZM and ZW, with no simplification.
EPSG:4326 CPC grid-cell centers covered by the polygons are included, including
exact boundary points. There are 249 Zambia cells and 135 Zimbabwe cells.
The masks and geometry hashes were frozen at 2026-09-27 03:25:19 UTC, before
reading any rainfall values, in `geography_freeze.json`.
Current national geometry is held constant through the study; it is not a
historical boundary reconstruction or crop-area mask.

The source is [NOAA PSL's CPC Unified gauge-based daily precipitation archive](https://psl.noaa.gov/thredds/catalog/Datasets/cpc_global_precip/catalog.html).
Each source year is a lossless numeric regional subset obtained through OPeNDAP
and saved in HDF5. Every snapshot has its source URL, coordinate/time slice,
units, retrieval timestamp, source history, and SHA256 sidecar. Only August 1
through September 30 is retrieved. Daily valid values within each national mask
are cosine-latitude weighted, then summed across all calendar days of the month.
A day needs at least 90% valid mask cells; any missing calendar day or low-coverage
day makes the whole month unavailable. Missing values are never replaced by zero.

Both countries have 83 eligible months out of 86. August 1985, September 1986,
and September 2004 each have a missing source day and remain NaN. Exact daily
grid coverage and monthly eligibility are saved separately. These are counts of
interpolated grid cells, not measurements of gauge density.

The [CPC source documentation](https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/DOCU/PRCP_CU_GAUGE_V1.0GLB_0.50deg_README.txt)
warns that tropical African analysis quality is limited and that the historical
and real-time station networks differ. These controls use today's archive and
are not a certification of historical data vintages. The model's assumed daily
availability is day-end plus 10 days: the latest September input is available by
October 10, before its October 20 forecast issue. No October observation enters.

Offline rebuild and semantic tests, from the repository root:

```sh
python3 results/satellite_validation/southern_africa_maize_replication/ground/rebuild_ground.py
python3 -m pytest -q results/satellite_validation/southern_africa_maize_replication/ground/test_ground.py
```

Offline dependencies are listed in `requirements.txt`; `refresh_requirements.txt`
adds NetCDF4 for the explicit `--refresh` network option. Refresh runs four
processes, since the underlying NetCDF library is not thread safe. The default
rebuild checks frozen geography, mask, and source snapshot hashes before deriving
the CSVs. Existing cached source checksums must match even during refresh.
