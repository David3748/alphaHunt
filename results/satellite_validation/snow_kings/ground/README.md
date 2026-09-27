# Kings River sources selected before forecast results

The target is CDEC KGF (Kings River–Pine Flat Dam), monthly sensor65 full-natural flow in acre-feet. Metadata at `https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=KGF` distinguishes this from sensor8 daily CFS. KGF is at36.831N,119.335W. No daily CFS-to-volume approximation is needed.

The upstream polygon is USGS NLDI basin of station11221500 (Kings River below Pine Flat Dam). WGS84 ellipsoidal polygon area is1544.4705 square miles against1545 reported by USGS; discrepancy0.0343%. The selected satellite grid uses18624 equal-area pixel centers in the full unsplit basin. Basin bounds are[-119.42264357,36.592904125,-118.334284974,37.209267426]. The latitude/longitude centroid used to rank snow stations is[-118.836830115,36.918648590].

CDEC snow station search supplies the in-basin candidate inventory. The `basin` search parameter did not limit the returned catalog, so code explicitly selected rows labelled KINGS RIVER and checked their point-in-polygon membership. Distance ranking uses WGS84 geodesic distance to the basin centroid. The search includes monthly manual snow courses as well as daily stations; individual metadata must verify daily sensor3 before selection.

Raw snapshots document exclusions without inspecting runoff outcomes:

- SMD, RDC, RTT, WDH: four nearest stations, monthly manual surveys only.
- WWC West Woodchuck Meadow: nearest daily station,14.273km,9100ft;22/24 valid late-May years; initial2007 and later2011 missing.
- BCB Blackcap Basin:17.384km,10180ft;17/24 valid years,9/10 initial years.
- KUB starts daily snow in2025, outside the sample; other closer stations only monthly.
- BIM Big Meadows:22.583km,20/24 valid years,8/10 initial years.
- MTM Mitchell Meadow:23.105km,23/24 valid years,9/10 initial years.
- STL State Lakes:23.502km,20/24 valid years,8/10 initial years.

A valid year requires a nonnegative, unflagged sensor3 value on at least one of May29–31. The latest such observation is used. Zero remains a real valid SWE observation, while missing and negative measurements are not imputed. Original current-archive flags and observation timestamps are retained in source CSVs.

The precipitation series is the unchanged CDEC5SI October–May regional index used in the San Joaquin experiment. It is an adjacent/northern regional weather comparator, not a Kings-specific basin rainfall measurement. Source data and geographic selection history remain in `../snow_daily/ground`; summer ground manifests link hashes without duplicating that raw archive.

All sources are current vintages. A June15 nominal issue supplies fourteen days after May ends, but historical publication timestamps and unrevised original values are not certified. Modern MODIS processing dates may also be later than the nominal issue. This supports scientific chronological testing only.
