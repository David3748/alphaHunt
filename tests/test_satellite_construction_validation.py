import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from satellite_construction_validation import Rule, availability_audit, normalize, replay


def frame(dates, values=None, created=None, baseline=None):
    return pd.DataFrame({
        "date": dates,
        "created": created or [(pd.Timestamp(x) + pd.Timedelta(days=1)).isoformat() for x in dates],
        "in_baseline": baseline or [False] * len(dates),
        "new_built_ha": values or [2.0] * len(dates),
        "window_scenes": [3] * len(dates),
    })


def test_delayed_dependency_blocks_composite_even_when_endpoint_is_published():
    data = frame(["2024-06-01", "2025-06-01", "2025-06-20"],
                 created=["2026-08-01", "2025-06-02", "2025-06-21"],
                 baseline=[True, False, False])
    audit = availability_audit(data, "2024-01-01")
    last = audit.iloc[-1]
    assert last["known_later_publication_inputs"] == 1
    assert last["endpoint_available_at"] == pd.Timestamp("2025-06-22", tz="UTC")
    assert last["dependency_available_at_lower_bound"] == pd.Timestamp("2026-08-02", tz="UTC")
    assert not last["strict_point_in_time_certified"]


def test_public_since_gate_and_processing_lag():
    data = frame(["2025-01-01", "2025-05-01", "2025-05-15", "2025-05-20"])
    audit = availability_audit(data, "2025-06-01")
    decisions, alerts = replay(audit, "2025-06-01", "endpoint_only")
    assert (decisions.decision_at >= pd.Timestamp("2025-06-02", tz="UTC")).all()
    assert len(alerts) == 1
    assert alerts.iloc[0].decision_at == pd.Timestamp("2025-06-02", tz="UTC")


def test_cloud_gap_is_abstention_not_stall_evidence():
    data = frame(["2025-01-01", "2025-05-20"])
    audit = availability_audit(data, "2024-01-01")
    decisions, alerts = replay(audit, "2024-01-01", "endpoint_only")
    assert alerts.empty
    assert not decisions.iloc[-1].coverage_eligible


def test_reprocessed_old_scene_never_backdates_alert():
    data = frame(["2025-01-01", "2025-05-01", "2025-05-15", "2025-05-20"],
                 created=["2025-01-02", "2026-08-01", "2026-08-01", "2026-08-01"])
    audit = availability_audit(data, "2024-01-01")
    decisions, alerts = replay(audit, "2024-01-01", "endpoint_only")
    assert alerts.empty
    assert decisions.iloc[-1].latest_age_days > 400


def test_future_scene_does_not_change_past_replay():
    first = frame(["2025-01-01", "2025-05-01", "2025-05-15", "2025-05-20"])
    extra = frame(["2025-12-20"], [40.0])
    a = replay(availability_audit(first, "2024-01-01"), "2024-01-01", as_of="2025-06-01")
    b = replay(availability_audit(pd.concat([first, extra]), "2024-01-01"), "2024-01-01", as_of="2025-06-01")
    pd.testing.assert_frame_equal(a[0], b[0])
    pd.testing.assert_frame_equal(a[1], b[1])


def test_one_alert_per_open_episode_and_reset_after_real_progress():
    data = frame(["2025-01-01", "2025-05-01", "2025-05-15", "2025-05-20", "2025-06-01",
                  "2025-10-01", "2025-10-10", "2025-10-20"],
                 values=[2, 2, 2, 2, 4, 4, 4, 4])
    _, alerts = replay(availability_audit(data, "2024-01-01"), "2024-01-01", "endpoint_only")
    assert len(alerts) == 2
    assert alerts.record_ha.tolist() == [2, 4]


@pytest.mark.parametrize("column,value", [("created", None), ("new_built_ha", -1), ("in_baseline", "unknown")])
def test_bad_provenance_or_measurement_fails_closed(column, value):
    data = frame(["2025-01-01"])
    data[column] = data[column].astype(object)
    data.loc[0, column] = value
    with pytest.raises(ValueError):
        normalize(data)


def test_delayed_composite_is_not_certified_even_after_all_known_inputs_arrive():
    data = frame(["2025-01-01", "2025-05-01", "2025-05-15", "2025-05-20"])
    _, alerts = replay(availability_audit(data, "2024-01-01"), "2024-01-01")
    assert len(alerts) == 1
    assert not alerts.strict_point_in_time_certified.any()
