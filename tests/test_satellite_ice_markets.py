"""Settlement mapping, temporal isolation, and executable-quote safeguards."""
from datetime import date
from pathlib import Path
import numpy as np
import pytest

from src import satellite_ice_markets as ice


def test_named_polymarket_worksheet_matches_daily_csv():
    daily = ice.load_daily(ice.OUT / "raw/nsidc_daily.csv")
    check = ice.verify_poly_workbook(daily, ice.OUT / "raw/nsidc_daily_workbook.xlsx")
    assert check["matching_daily_values_2000_to_2026"] > 9000
    assert check["sheet"] == "NH-Daily-Extent"


def test_current_features_exclude_unavailable_satellite_days():
    daily = ice.load_daily(ice.OUT / "raw/nsidc_daily.csv")
    issue = date(2026, 9, 29)
    baseline = ice.season_live(daily, issue, date(2026, 8, 1))
    revised = daily.copy()
    revised.loc["2026-09-27", "Extent"] = 0.1
    assert ice.season_live(revised, issue, date(2026, 8, 1))[:3] == baseline[:3]
    assert baseline[0] == 4.574


def test_walk_forward_forecast_does_not_use_its_own_final_label():
    daily = ice.load_daily(ice.OUT / "raw/nsidc_daily.csv")
    original, _ = ice.backtest(daily)
    changed = daily.copy()
    changed.loc["2025-10-01", "Extent"] = 3.0
    altered, _ = ice.backtest(changed)
    original_2025 = original.loc[original.year.eq(2025)]
    altered_2025 = altered.loc[altered.year.eq(2025)]
    assert original_2025.all_years_forecast.tolist() == altered_2025.all_years_forecast.tolist()
    assert original_2025.analog_forecast.tolist() == altered_2025.analog_forecast.tolist()
    assert not original_2025.final_min.equals(altered_2025.final_min)


def test_polymarket_bins_are_exclusive_and_threshold_breach_is_certain():
    drops = np.zeros(43)
    probabilities = ice.categorical_probabilities(drops, 4.574, ice.POLY_BINS)
    assert sum(probabilities) == pytest.approx(1)
    assert probabilities[4:] == [0, 0, 0]
    assert probabilities[3] > 0.98
    assert ice.probability(drops, 4.574, None, 4.6) == 1
    assert ice.probability(drops, 4.574, 4.6, 4.8) == 0


def test_quote_uses_cheapest_ask_and_requires_liquidity_and_buffer():
    assert ice._best_ask({"asks": [{"price": "0.9", "size": "10"},
                                    {"price": "0.8", "size": "8"}]}) == (0.8, 8)
    assert ice._best_ask({"asks": []}) == (None, 0)
    thin = ice._contract("test", "id", "outcome", 0.9, 0.5, 1, 0.5, 1)
    assert not any(row["eligible"] for row in thin)
    expensive = ice._contract("test", "id", "outcome", 0.99, 0.98, 100, 0.98, 100)
    assert not any(row["eligible"] for row in expensive)


def test_saved_market_snapshot_has_no_paper_candidate():
    # This is an archived quote screen, not proof of current market state.
    card = ice.screen(ice.OUT, date(2026, 9, 29))
    assert card["model"]["pricing_model"] == "all_prior_years_remaining_drop"
    assert card["polymarket_bin_probability_sum"] == pytest.approx(1)
    assert card["decision"] == "NO_LIVE_TRADE_UNVERIFIED_MODEL"
    assert not any(contract["eligible"] for contract in card["contracts"])
