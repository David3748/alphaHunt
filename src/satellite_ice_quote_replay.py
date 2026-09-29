#!/usr/bin/env python3
"""One-season Kalshi sea-ice quote replay; hypothetical fills, not verified alpha."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time as sleep_time

import numpy as np
import pandas as pd
import requests

from src.satellite_ice_markets import (OUT, ISSUE_DATES, NSIDC, FEE_RESERVE,
                                        EDGE_BUFFER, MAX_CONTRACTS, historical_rows,
                                        load_daily)

RAW = OUT / "quote_replay_2025" / "raw"
BASE = "https://external-api.kalshi.com/trade-api/v2"
EVENT = "KXARCTICICEMIN-25OCT01"


def stamp(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def _store(session: requests.Session, url: str, params: dict, path: Path) -> dict:
    for attempt in range(6):
        response = session.get(url, params=params, timeout=40)
        if response.status_code != 429:
            response.raise_for_status()
            break
        sleep_time.sleep(min(20, 3 * (attempt + 1)))
    else:
        raise RuntimeError(f"Kalshi rate limited: {url}")
    path.write_bytes(response.content)
    return {"url": response.url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(response.content).hexdigest(), "bytes": len(response.content)}


def valid_market(market: dict) -> bool:
    # Four 2025 contracts have a source clarification; two also have
    # contradictory archived 'Above' wording. Exclude all four fail-closed.
    return (market.get("event_ticker") == EVENT
            and " is below " in market.get("rules_primary", "")
            and not market.get("rules_secondary")
            and market.get("result") in ("yes", "no")
            and market.get("strike_type") == "less")


def fetch(out: Path = RAW) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    manifest = {}
    market_url = BASE + "/historical/markets"
    manifest["markets.json"] = _store(session, market_url,
                                        {"event_ticker": EVENT, "limit": 200}, out / "markets.json")
    markets = json.loads((out / "markets.json").read_text())["markets"]
    if len(markets) != 13 or sum(valid_market(m) for m in markets) != 9:
        raise ValueError("2025 historical Kalshi universe changed")
    for market in markets:
        if not valid_market(market):
            continue
        ticker = market["ticker"]
        name = ticker + "_candles.json"
        url = BASE + f"/historical/markets/{ticker}/candlesticks"
        params = {"start_ts": stamp("2025-08-14T00:00:00"),
                  "end_ts": stamp("2025-09-26T12:00:00"), "period_interval": 1440}
        manifest[name] = _store(session, url, params, out / name)
        sleep_time.sleep(1.1)
    (out.parent / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def select_candle(candles: list[dict], issue: date) -> dict | None:
    # Price at a completed 04:00 UTC daily candle, before the noon UTC decision.
    decision = datetime.combine(issue, time(12, 0), tzinfo=timezone.utc)
    prior = [c for c in candles if c["end_period_ts"] <= decision.timestamp()]
    if not prior:
        return None
    candle = max(prior, key=lambda c: c["end_period_ts"])
    age = decision.timestamp() - candle["end_period_ts"]
    if age < 0 or age > 36 * 3600:
        return None
    return candle


def replay(root: Path = OUT) -> dict:
    raw = root / "quote_replay_2025" / "raw"
    manifest = json.loads((raw.parent / "source_manifest.json").read_text())
    for name, entry in manifest.items():
        if hashlib.sha256((raw / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Historical quote hash mismatch: {name}")
    daily = load_daily(root / "raw/nsidc_daily.csv")
    markets = json.loads((raw / "markets.json").read_text())["markets"]
    if len(markets) != 13:
        raise ValueError("Historical market universe incomplete")
    excluded = [{"ticker": m["ticker"], "reason": "ambiguous_above_rule_or_source_clarification"}
                for m in markets if not valid_market(m)]
    if len(excluded) != 4:
        raise ValueError("Unexpected invalid market count")
    rows = []
    for month, day in ISSUE_DATES:
        issue = date(2025, month, day)
        cutoff = issue - timedelta(days=3)
        observed = daily.loc[pd.Timestamp("2024-12-19"):pd.Timestamp(cutoff), "Extent"]
        if len(observed) != (cutoff - date(2024, 12, 19)).days + 1:
            raise ValueError("Incomplete 2025 observed Kalshi window")
        observed_min = float(observed.min())
        train = historical_rows(daily, 2025, month, day)
        drops = np.array([r["remaining_drop"] for r in train])
        for market in markets:
            if not valid_market(market):
                continue
            ticker = market["ticker"]
            if not (datetime.fromisoformat(market["open_time"].replace("Z", "+00:00"))
                    <= datetime.combine(issue, time(12), tzinfo=timezone.utc)
                    < datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))):
                continue
            candles = json.loads((raw / f"{ticker}_candles.json").read_text())["candlesticks"]
            candle = select_candle(candles, issue)
            if candle is None:
                continue
            bid = candle["yes_bid"]["close"]
            ask = candle["yes_ask"]["close"]
            if bid is None or ask is None:
                continue
            bid, ask = float(bid), float(ask)
            if not (0 <= bid <= ask <= 1):
                raise ValueError(f"Crossed or invalid historical quote: {ticker}")
            threshold = float(market["cap_strike"])
            if observed_min < threshold:
                p_yes = 1.0
            else:
                p_yes = float(((np.minimum(observed_min, observed_min - drops) < threshold).sum() + .5)
                              / (len(drops) + 1))
            persistence_yes = float(observed_min < threshold)
            for side, probability, entry_ask in (("YES", p_yes, ask), ("NO", 1-p_yes, 1-bid)):
                edge = probability - entry_ask - FEE_RESERVE
                won = (market["result"] == side.lower())
                persistence_p = persistence_yes if side == "YES" else 1 - persistence_yes
                rows.append({"issue": issue.isoformat(), "quote_end_utc":
                             datetime.fromtimestamp(candle["end_period_ts"], timezone.utc).isoformat(),
                             "ticker": ticker, "side": side, "result": market["result"],
                             "observed_min": observed_min, "training_years": len(train),
                             "model_probability": p_yes if side == "YES" else 1-p_yes,
                             "historical_ask": entry_ask, "candle_volume": float(candle["volume"]),
                             "net_edge": edge, "screen_pass": bool(0 < entry_ask < 1 and edge >= EDGE_BUFFER),
                             "persistence_probability": persistence_p,
                             "persistence_net_edge": persistence_p - entry_ask - FEE_RESERVE,
                             "persistence_screen_pass": bool(0 < entry_ask < 1 and persistence_p - entry_ask - FEE_RESERVE >= EDGE_BUFFER),
                             "settlement_payoff": 1.0 if won else 0.0,
                             "hypothetical_pnl_per_contract": (1.0 if won else 0.0) - entry_ask - FEE_RESERVE})
    panel = pd.DataFrame(rows).sort_values(["issue", "ticker", "side"])
    if panel.empty:
        raise ValueError("No historical quote rows")
    qualifying = panel.loc[panel.screen_pass].sort_values(["issue", "net_edge"], ascending=[True, False])
    selected = qualifying.iloc[0].to_dict() if len(qualifying) else None
    # Volume is an imperfect market-activity proxy, not evidence that the
    # displayed ask had size or that our order could have filled.
    active = qualifying.loc[qualifying.candle_volume >= MAX_CONTRACTS]
    active_selected = active.iloc[0].to_dict() if len(active) else None
    naive = panel.loc[panel.persistence_screen_pass].sort_values(
        ["issue", "persistence_net_edge"], ascending=[True, False])
    naive_selected = naive.iloc[0].to_dict() if len(naive) else None
    summary = {"status": "one_season_hypothetical_quote_replay_not_executable_return",
               "year": 2025, "n_markets_total": len(markets), "n_markets_valid": len(markets) - len(excluded),
               "excluded_markets": excluded, "n_quote_sides": len(panel),
               "n_screen_passes": len(qualifying),
               "one_position_rule": "First issue date with qualifying ask; choose highest model net edge; hold to settlement.",
               "selected": selected, "hypothetical_5_contract_pnl":
               None if selected is None else MAX_CONTRACTS * selected["hypothetical_pnl_per_contract"],
               "recent_volume_proxy_min_contracts": MAX_CONTRACTS,
               "volume_proxy_selected": active_selected,
               "volume_proxy_hypothetical_5_contract_pnl":
               None if active_selected is None else MAX_CONTRACTS * active_selected["hypothetical_pnl_per_contract"],
               "persistence_benchmark_selected": naive_selected,
               "persistence_benchmark_hypothetical_5_contract_pnl":
               None if naive_selected is None else MAX_CONTRACTS * naive_selected["hypothetical_pnl_per_contract"],
               "historical_quote_size_available": False,
               "historical_original_nsidc_release_available": False,
               "rule_source_stable_across_2025": False,
               "untouched_hypothesis_test": False,
               "verified_trading_alpha": False,
               "interpretation": "Historical bid/ask candles contain prices, not executable depth. The 2025 event had an NSIDC data-source warning and the model was developed after 2025 outcomes; this is a one-season exploratory replay."}
    panel.to_csv(raw.parent / "all_quote_decisions.csv", index=False)
    (raw.parent / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.refresh:
        fetch()
    print(json.dumps(replay(), indent=2))
