from pathlib import Path
import sys
import io
import json

import h5py
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_goes_extract import (PLANTS, aggregate, compact_snapshots, cosine_solar_zenith,
                                    decode_numeric, expected_slots, filename_dates, source_for)


def test_filename_has_observation_and_actual_creation_time():
    key = "OR_ABI-L2-DSRC-M6_G17_s20200012001214_e20200012003587_c20200012006465.nc"
    dates = filename_dates(key)
    assert dates["scan_start"] == pd.Timestamp("2020-01-01T20:01:21.4Z")
    assert dates["created_at"] == pd.Timestamp("2020-01-01T20:06:46.5Z")
    assert dates["scan_start"] < dates["scan_end"] < dates["created_at"]


def test_fixed_satellite_and_algorithm_transition_dates():
    assert source_for(pd.Timestamp("2023-01-04T16:00Z")) == ("17", "ABI-L2-DSRC")
    assert source_for(pd.Timestamp("2023-01-04T18:00Z")) == ("18", "ABI-L2-DSRC")
    assert source_for(pd.Timestamp("2024-04-17T16:00Z")) == ("18", "ABI-L2-DSRC")
    assert source_for(pd.Timestamp("2024-04-17T18:00Z")) == ("18", "ABI-L2-DSRF")


def test_signed_netcdf_packing_is_decoded_unsigned_and_fill_stays_missing():
    with h5py.File(io.BytesIO(), "w") as f:
        dataset = f.create_dataset("DSR", data=np.array([10000, -25536, -1], dtype="int16"))
        dataset.attrs["_Unsigned"] = b"true"
        dataset.attrs["_FillValue"] = [-1]
        dataset.attrs["scale_factor"] = [.02]
        values, valid = decode_numeric(dataset, slice(None))
        np.testing.assert_allclose(values[:2], [200, 800])
        np.testing.assert_array_equal(valid, [True, True, False])


def test_physical_night_slots_are_night_in_every_month():
    for day in pd.date_range("2020-01-15", "2020-12-15", freq="MS", tz="UTC"):
        for hour in (4, 6, 8, 10):
            for p in PLANTS.values():
                assert cosine_solar_zenith(day + pd.Timedelta(hours=hour), p["lat"], p["lon"]) < 0
    assert cosine_solar_zenith(pd.Timestamp("2020-06-21T20:00Z"), 35.383, -120.067) > .9


def test_final_next_day_midnight_endpoint_present():
    slots = expected_slots("2020-01-01", "2020-01-31")
    assert slots[-1] == pd.Timestamp("2020-02-01T00:00Z")
    assert 12 in slots.hour


def fake_points(tmp_path, missing=None, late=None):
    records = []
    for slot in expected_slots("2020-01-01", "2020-01-31"):
        if slot == missing:
            continue
        # Real GOES timestamps mix whole and fractional seconds in one archive.
        created = slot + pd.Timedelta(minutes=10, milliseconds=100 if slot.hour == 20 else 0)
        availability = pd.Timestamp("2020-02-20T00:00Z") if slot == late else created
        records.append({"key": str(slot), "slot": slot.isoformat(), "scan_start": slot.isoformat(),
                        "created_at": created.isoformat(), "s3_last_modified": availability.isoformat(),
                        "available_at": availability.isoformat(), "error": None,
                        "values": [{"plant_code": code, "dsr_w_m2": 500., "pixel_coverage": 1., "good_quality_fraction": 1.}
                                   for code in PLANTS]})
    (tmp_path / "point_values.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (tmp_path / "object_manifest.csv").write_text("test\n")


def test_missing_daylight_stays_missing_and_is_not_zero_imputed(tmp_path):
    fake_points(tmp_path, missing=pd.Timestamp("2020-01-15T20:00Z"))
    monthly = aggregate(tmp_path, "2020-01-01", "2020-01-31")
    assert monthly.complete_days.eq(30).all()
    assert monthly.daylight_coverage.between(.9, 1, inclusive="left").all()
    daily = pd.read_csv(tmp_path / "daily_irradiance.csv")
    assert daily.loc[daily.date == "2020-01-15", "ghi_kwh_m2_day"].isna().all()
    assert monthly.eligible.all()


def test_late_source_cannot_change_forecast_month_integral(tmp_path):
    fake_points(tmp_path, late=pd.Timestamp("2020-01-15T20:00Z"))
    monthly = aggregate(tmp_path, "2020-01-01", "2020-01-31")
    assert monthly.complete_days.eq(30).all()
    assert (pd.to_datetime(monthly.last_source_available_at) < pd.to_datetime(monthly.forecast_at)).all()


def test_compressed_point_snapshot_replays_identical_monthly_values(tmp_path):
    fake_points(tmp_path)
    aggregate(tmp_path, "2020-01-01", "2020-01-31")
    original = (tmp_path / "monthly_irradiance.csv").read_bytes()
    compact_snapshots(tmp_path)
    assert not (tmp_path / "point_values.jsonl").exists()
    assert (tmp_path / "point_values.jsonl.gz").exists()
    aggregate(tmp_path, "2020-01-01", "2020-01-31")
    assert (tmp_path / "monthly_irradiance.csv").read_bytes() == original
