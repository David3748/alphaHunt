# Prepared crop-data replication feasibility

Recommendation made before any new county-yield model was fitted: use raw eight-day NDVI from the U.S. maize subset of [CY-Bench](https://zenodo.org/records/17279151), together with its independent NASS county labels and AgERA5 weather. Anonymous byte-range retrieval was verified, so downloading the entire 6.23 GB archive is unnecessary. Selected U.S. maize members contain 14.0 MB compressed NDVI, 0.78 MB county labels, and 718.7 MB daily weather. The source archive contains 2001–2023 observations; the new model protocol independently fixes 2003–2023. The dataset is EUPL-1.2. Source files were inspected for layout and provenance, without fitting or scoring outcomes to choose a winner.

This is a replication of a published dataset and physical mechanism, with a stronger chronological evaluation; it is not an exact reproduction of a published weather-ablation success. The [CY-Bench paper](https://doi.org/10.5194/essd-18-3997-2026) supplies a leave-one-year-out benchmark and recommends forward evaluation where data permit. Raw NDVI avoids the uncertainty of the smoothed fPAR channel. Static 2021 crop masks remain a clearly disclosed fixed-geography/current-vintage limitation.

Other bounded sources inspected:

| Source | Access and layout | Why it was not selected |
|---|---|---|
| [Paudel et al 2023 county sample](https://zenodo.org/records/7751191), [paper](https://doi.org/10.1088/1748-9326/acf50e) | Downloaded 50.1 MB county-data.zip; MD5 matched. 917 counties with 2000–2018 weather/FAPAR; separate NASS county labels 1994–2018. CSV keys COUNTY_ID,FYEAR,DEKAD. CC-BY 4.0. | Smoothed Copernicus FAPAR and incompletely specified crop masking require additional timing audit. Its grid-level yield labels are themselves satellite-modeled SCYM outputs, so those would not be independent truth. The county labels are suitable. |
| [SpatioTemporalYield](https://github.com/ellaampy/SpatioTemporalYield), [author data](https://huggingface.co/datasets/ellaampy/SpatioTemporalYield) | HTTP206 ranges verified for 15.4 GB ZIP. MODIS reflectance/NDVI, Daymet weather, USDA county labels;2003–2021, five Corn Belt states. | Prepared pixel arrays need further reduction and much larger transfer. Published test has only two final years; a broader forward test and stronger weather/trend comparison would still be necessary. |
| [Peng et al 2018](https://doi.org/10.1029/2018GL079291) | Published seasonal-climate/EVI maize mechanism with independent official labels. | No prepared county-feature release located in bounded primary-source search; raw-source reconstruction would be a separate large task. |

The Paudel archive remains in scratch for source inspection only. It was not fitted as an alternative to select whichever gives the best result. CY-Bench was chosen for longer prepared coverage, direct NDVI isolation, county outcome granularity and manageable retrieval.
