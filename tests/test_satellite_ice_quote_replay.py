"""Historical Kalshi quote replay is reproducible and preserves its limits."""
from datetime import date, datetime, timezone
import json
import pytest

from src import satellite_ice_quote_replay as replay


def test_ambiguous_2025_rules_are_excluded():
    markets = json.loads((replay.RAW / "markets.json").read_text())["markets"]
    assert len(markets) == 13
    valid = [m for m in markets if replay.valid_market(m)]
    assert len(valid) == 9
    assert all(" is below " in m["rules_primary"] and not m["rules_secondary"] for m in valid)
    assert {m["ticker"].split("-T")[-1] for m in markets if not replay.valid_market(m)} == {
        "4.5", "4.3", "3.2", "3.4"}


def test_issue_uses_only_completed_recent_candle():
    issue = date(2025, 8, 15)
    def ts(value):
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
    before = {"end_period_ts": ts("2025-08-15T04:00:00")}
    future = {"end_period_ts": ts("2025-08-15T13:00:00")}
    assert replay.select_candle([future, before], issue) == before
    assert replay.select_candle([future], issue) is None
    assert replay.select_candle([{"end_period_ts": ts("2025-08-13T00:00:00")}], issue) is None


def test_archived_replay_discloses_unverified_fills_and_two_sensitivities():
    result = replay.replay()
    assert result["n_markets_valid"] == 9
    assert result["n_screen_passes"] == 6
    assert result["selected"]["ticker"].endswith("T4.4")
    assert result["selected"]["side"] == "NO"
    assert result["selected"]["candle_volume"] == 0
    assert result["hypothetical_5_contract_pnl"] == 4.2
    assert result["volume_proxy_selected"]["issue"] == "2025-09-01"
    assert result["volume_proxy_hypothetical_5_contract_pnl"] == 2.45
    assert result["persistence_benchmark_selected"]["ticker"].endswith("T5.2")
    assert result["persistence_benchmark_hypothetical_5_contract_pnl"] == pytest.approx(-0.2)
    assert not result["historical_quote_size_available"]
    assert not result["verified_trading_alpha"]
