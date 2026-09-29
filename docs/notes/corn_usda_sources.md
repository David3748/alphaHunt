# USDA corn forecasts as published

`results/corn_model/inputs/usda/wasde_corn_vintages.csv` contains 195 actual WASDE
releases, April 9, 2010 through September 11, 2026, with 585 report × marketing-year
rows. All ten selected balance-sheet quantities are complete in this snapshot.
No models, strategies, or comparisons of prediction performance were run during
this source preparation.

The [official historical WASDE archive](https://www.usda.gov/historical-wasde-report-data-3)
explicitly distinguishes these report vintages from subsequently revised PSD
history. It says the archive records estimates as they appeared in each report;
individual WASDE publications remain the official record. We retain the two
official 2010–2015 and 2016–2020 ZIP archives plus all 68 linked monthly CSVs from
2021 through September 2026. Original CSV bytes are stored losslessly in gzip;
original ZIP bytes are unchanged. The full selected archive occupies about 10 MB,
including provenance and PDF spot checks. Source hashes identify both original
download bytes and stored compressed files.

USDA's front-end returned HTTP403 to this client for several archive paths. The
same files were obtained from USDA's public `usda.azureedge.us` CDN at identical
paths. `source_manifest.json` records canonical URLs, actual download URLs,
retrieval time, byte counts, and SHA256 hashes. Its saved landing page records
the exact links and the agency's vintage-data statement.

The requested 2000–March2010 period is not present in the official bulk archive.
USDA described pre-2010 bulk compilation as unfinished in its
[2025 data-user meeting response](https://data.nass.usda.gov/Education_and_Outreach/Meeting/2025/2025%20Spring%20Data%20Users%20Meeting%20Question%20and%20Answer%20Summary%20with%20Slides.pdf).
Individual report PDFs exist in ESMIS, but this ingestion deliberately does not
claim to have parsed that older period or substitute today's revised history.
The priority 2013–2023 period has every actual monthly release. October2013,
January2019, and October2025 are absent in the bulk archive and remain absent in
the normalized table. [USDA's October2013 cancellation notice](https://www.nass.usda.gov/Statistics_by_State/South_Dakota/Publications/Crop_Progress_%26_Condition/2013/ShutdownSD2013.pdf)
is saved locally; its next scheduled release was November8. The
[February2019 report](https://esmis.nal.usda.gov/usda-esmis/files/3t945q76s/nz806541q/6682x9431/latest.pdf)
also explicitly states that no January2019 WASDE would be published. Missing
reports are never synthesized, forward-filled, or treated as zero revisions.

## Schema and timing

Each key is `(report_date, marketing_year)`. A release normally has three crop
years; all three are retained, including older-year estimates as they stood in
that release. The parser selects only **Corn / United States / U.S. Feed Grain
and Corn Supply and Use / Annual**. It excludes the world table in metric tons,
aggregate feed grains, and reliability statistics. It selects the publication's
current estimates, not the previous-month comparison column shown alongside
them in a PDF.

| Column | Meaning |
|---|---|
| `report_date` | Official `ReleaseDate`, ISO calendar date; not the monthly report label |
| `published_at` | Official `ReleaseDate` + `ReleaseTime`, Eastern local time converted to UTC |
| `publication_time_status` | Explicit date/time source and documented timezone convention |
| `release_time_eastern` | Original report clock time, retained separately |
| `marketing_year` | USDA `YYYY/YY`; corn marketing year begins September1 |
| `marketing_year_start` | Integer crop/marketing-year start, never inferred from issue month |
| `wasde_number` | Original issue number |
| `projection_estimate_flag` | Original projection/estimate flag, including blank historical flags |
| `yield_bu_acre` | Bushels per harvested acre |
| `harvested_area_m_acres` | Million harvested acres |
| `production_m_bu` | Million bushels produced |
| `ending_stocks_m_bu` | Million bushels of ending stocks |
| `domestic_use_m_bu` | Million bushels, the published Domestic, Total field |
| `total_use_m_bu` | Million bushels, the published Use, Total field, including exports |
| `exports_m_bu` | Million bushels exported |
| `beginning_stocks_m_bu`, `imports_m_bu`, `total_supply_m_bu` | Additional original supply-account fields, million bushels |
| `source_file`, `source_sha256` | Original download identity, linked to the source manifest |

`published_at` describes availability of **the information in the report**, not
availability of the consolidated CSV service. Historical CSVs were compiled
later; current monthly CSV posting can also lag the actual report. Original
release clocks in the data are08:30 through2012 and12:00 from2013. The
[January8,2013 USDA notice](https://www.nass.usda.gov/Newsroom/Notices/2013/01_08_2013.php)
confirms the transition effective January11. The notice is saved in
`record_checks/`. Some agency prose loosely writes EDT or EST year-round; we use
`America/New_York` civil time and its historical daylight-saving rules, not a
fixed UTC offset. Thus August2013 noon is16:00UTC, January2013 noon17:00UTC.
This is a published release clock, not a measured CDN delivery latency.

Unit checks fail closed; missing quantities remain blank/NaN rather than zero.
No carrying between reports, choosing a later revision, or replacing historical
values with a final estimate occurs. Duplicate report/marketing-year fields,
nonfinite/negative quantities, inconsistent report metadata, and material
domestic-plus-export accounting mismatches raise errors. Small accounting
rounding differences are tolerated as explicitly noted in WASDE reports.

## Verification and offline use

Three independent original PDFs were checked at page12: August12,2013;
September12,2018; September12,2023. All **30 selected quantities** match the latest
projection column exactly. The2013 file is the archived `ORIG` publication.
Files, source hashes, and comparison details are in `record_checks/`. This
checks the parser and current-vintage column selection; it is not a claim that
every archive row has separately been checked against a hard-copy original.

Fourteen semantic tests cover vintage retention, wrong-table exclusion, unit
validation, daylight-saving conversion, duplicate rejection, missing versus
zero, malformed metadata, accounting, and offline input drift. The default
rebuild verifies every frozen source hash and does not access the network.

```bash
python3 src/corn_usda_data.py
python3 -m pytest tests/test_corn_usda_data.py -q
# Explicit refresh only; changes the source snapshot and requires review:
python3 src/corn_usda_data.py --fetch
```

Python callers can use `from src.corn_usda_data import load_vintages` and
`load_vintages()`; it returns `report_date` as a pandas date and `published_at`
as a UTC-aware timestamp. The loader does not fill missing reports. To form a
point-in-time anchor, select the intended `marketing_year_start`, then only
rows whose `published_at` is strictly before the decision timestamp. The basic
corn model defines its target as the first release in the next calendar month
for that same marketing year. If that month has no report, its outcome stays
missing; it is not filled from a later release. Other targets must state their
release-selection rule explicitly.

The source is U.S. federal government statistical data. Attribute USDA World
Agricultural Outlook Board and the historical WASDE archive; the normalized
table is a research transformation, not a new official USDA release.
