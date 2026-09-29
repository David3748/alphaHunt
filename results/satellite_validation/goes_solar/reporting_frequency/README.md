# Plant reporting-frequency and annual-label audit

The official EIA-923 final ZIP workbooks give the following plant-frame classifications. Selection uses fixed plant IDs, not generation behavior.

| Plant | 2015–2016 | 2017–2022 | 2023–2025 |
|---|---|---|---|
| California Valley Solar Ranch, 57439 | M | A | AM |
| Topaz Solar Farm, 57695 | M | M | M |

`plant_reporting_frequency.csv` preserves each of the 22 plant-year rows, its Excel row number, URL, and source SHA. `manifest.json` records ZIP and workbook hashes. M denotes monthly respondents; A denotes annual respondents. The workbook's legacy file-layout text does not explain AM.

The distinction between A and AM matters. In its [November 21, 2022 proposal](https://www.govinfo.gov/content/pkg/FR-2022-11-21/pdf/2022-25287.pdf), EIA described adding monthly operational detail for annual renewable/storage respondents. [Current instructions, page 16](https://www.eia.gov/survey/form/eia_923/instructions.pdf) require that monthly detail from renewable plants. The code transition and those documents support interpreting AM as annual filings containing monthly detail. This is an inference from the metadata and reporting change, not a recovered historical dictionary entry. **CVSR's 2017–2022 monthly values must not be treated as independently reported monthly measurements.** The annual totals avoid this issue. EIA also describes monthly imputation for facilities outside its monthly sample in the [technical notes, page 3](https://www.eia.gov/electricity/monthly/pdf/technotes.pdf).

After the metadata audit, the parent requested annual label ingestion only. `annual_generation.csv` contains the annual `Net Generation (Megawatthours)` field from the exact PV/SUN row in each workbook. All 22 totals reconcile exactly to the sum of the twelve published monthly fields. This consistency check does not imply that every monthly component was measured independently. The extractor does not fit or select a model.

Selected primary release announcements were preserved in `evidence/`, with hashes and retrieval times in `release_evidence_manifest.json`:

| Data year | Early release | Final release checked |
|---|---|---|
| 2020 | 2021-06-11 | — |
| 2021 | 2022-06-15 | 2022-10-14 |
| 2023 | 2024-07-09 | — |
| 2024 | 2025-06-16 | 2025-09-18 |
| 2025 | 2026-07-01 | 2026-09-14 |

The [2023](https://www.eia.gov/about/new/?r=790), [2024](https://www.eia.gov/about/new/index.php?r=450), and [2025](https://www.eia.gov/about/new/?r=70) early-release announcements establish that annual product publication occurred well after a following-January-14 forecast. They do not prove which plants were included in each preliminary release, or rule out earlier company/other disclosures. The November 1 training embargo is a conservative assumed rule, not the first release date. Exact original plant-label vintages were not reconstructed; current final archives can contain later corrections. Relative-offset EIA news URLs may change content, so local HTML snapshots preserve the retrieved evidence.

Reproduce label ingestion from the cached official ZIPs, or download them if missing:

```sh
python3 results/satellite_validation/goes_solar/reporting_frequency/extract.py
```

ZIPs are retained in `work/eia_frequency_audit/`. No outcome fit, horizon selection, or forecast claim is part of this audit.
