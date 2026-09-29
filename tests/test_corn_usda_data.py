"""Semantic checks for report-vintage ingestion, not model performance."""
import gzip
import json

import numpy as np
import pandas as pd
import pytest

from src.corn_usda_data import (FIELDS, TITLE, discover_sources, parse_vintage_csv,
                                rebuild, sha256)


def source_rows(date="2013-08-12", market_year="2013/14", release_time="12:00:00.0000000"):
    values = {"yield_bu_acre": 154.4, "harvested_area_m_acres": 89.1,
              "production_m_bu": 13763, "ending_stocks_m_bu": 1837,
              "domestic_use_m_bu": 11510, "total_use_m_bu": 12735,
              "exports_m_bu": 1225, "beginning_stocks_m_bu": 719,
              "imports_m_bu": 90, "total_supply_m_bu": 14572}
    return pd.DataFrame([{"Commodity": "Corn", "Region": "United States", "ReportTitle": TITLE,
                          "AnnualQuarterFlag": "Annual", "Attribute": attribute, "Unit": unit,
                          "MarketYear": market_year, "Value": str(values[column]), "WasdeNumber": "521",
                          "ReleaseDate": date, "ReleaseTime": release_time,
                          "ForecastYear": date[:4], "ForecastMonth": str(int(date[5:7])),
                          "ProjEstFlag": "Proj."}
                         for attribute, (column, unit) in FIELDS.items()])


def parse(frame):
    return parse_vintage_csv(frame.to_csv(index=False).encode(), "fixture.csv", "fixturehash")


def test_preserves_separate_vintages_for_same_marketing_year():
    august = source_rows()
    september = source_rows(date="2013-09-12")
    september.loc[september.Attribute.eq("yield per harvested acre"), "Value"] = "155.3"
    out = parse(pd.concat([august, september]))
    assert out.yield_bu_acre.tolist() == [154.4, 155.3]
    assert out.marketing_year.tolist() == ["2013/14", "2013/14"]


def test_excludes_reliability_statistics_and_world_metric_units():
    genuine = source_rows()
    other = genuine.copy()
    other.ReportTitle = "Reliability of United States August Projections"
    other.Value = "999"
    world = genuine.copy()
    world.ReportTitle = "World Corn Supply and Use"
    world.Unit = "Million Metric Tons"
    assert len(parse(pd.concat([genuine, other, world]))) == 1


@pytest.mark.parametrize("date,clock,expected", [
    ("2012-08-10", "08:30:00", "2012-08-10T12:30:00+00:00"),
    ("2013-01-11", "12:00:00", "2013-01-11T17:00:00+00:00"),
    ("2013-08-12", "12:00:00", "2013-08-12T16:00:00+00:00"),
])
def test_official_report_clock_observes_eastern_dst(date, clock, expected):
    assert parse(source_rows(date=date, release_time=clock)).published_at.iloc[0] == expected


def test_duplicate_field_fails_instead_of_taking_later_value():
    source = source_rows()
    with pytest.raises(ValueError, match="Duplicate"):
        parse(pd.concat([source, source.iloc[[0]]]))


def test_wrong_units_fail_closed():
    source = source_rows()
    source.loc[0, "Unit"] = "Million Metric Tons"
    with pytest.raises(ValueError, match="Unexpected unit"):
        parse(source)


def test_missing_value_is_not_filled_and_real_zero_is_preserved():
    source = source_rows()
    source.loc[source.Attribute.eq("yield per harvested acre"), "Value"] = "NA"
    source.loc[source.Attribute.eq("imports"), "Value"] = "0"
    out = parse(source).iloc[0]
    assert np.isnan(out.yield_bu_acre)
    assert out.imports_m_bu == 0


@pytest.mark.parametrize("value", ["-1", "inf"])
def test_invalid_physical_quantities_fail(value):
    source = source_rows()
    source.loc[0, "Value"] = value
    with pytest.raises(ValueError, match="Invalid"):
        parse(source)


def test_missing_time_and_wrong_marketing_year_fail():
    with pytest.raises(ValueError, match="release time"):
        parse(source_rows(release_time=""))
    with pytest.raises(ValueError, match="Nonconsecutive"):
        parse(source_rows(market_year="2013/15"))


def test_unexpected_use_accounting_does_not_pass():
    source = source_rows()
    source.loc[source.Attribute.eq("use, total"), "Value"] = "1"
    with pytest.raises(ValueError, match="accounting mismatch"):
        parse(source)


def test_discovery_does_not_follow_revised_psd_or_random_csv():
    html = '<a href="/sites/default/files/documents/oce-wasde-report-data-2023-08.csv">vintage</a><a href="https://example.com/psd.csv">revised</a>'
    assert discover_sources(html) == ["https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2023-08.csv"]


def test_offline_rebuild_rejects_changed_snapshot(tmp_path):
    (tmp_path / "raw").mkdir()
    landing = gzip.compress(b"metadata", mtime=0)
    (tmp_path / "raw/landing.gz").write_bytes(landing)
    original = source_rows().to_csv(index=False).encode()
    compressed = gzip.compress(original, mtime=0)
    (tmp_path / "raw/data.csv.gz").write_bytes(compressed)
    manifest = {"landing_stored_path": "raw/landing.gz", "landing_stored_sha256": sha256(landing),
                "sources": [{"stored_path": "raw/data.csv.gz", "original_name": "data.csv",
                             "stored_sha256": sha256(compressed), "source_sha256": sha256(original)}]}
    (tmp_path / "source_manifest.json").write_text(json.dumps(manifest))
    assert len(rebuild(tmp_path)) == 1
    (tmp_path / "raw/data.csv.gz").write_bytes(compressed + b"drift")
    with pytest.raises(ValueError, match="Snapshot hash mismatch"):
        rebuild(tmp_path)
