"""CORN ETF proxy data and fixed, unlevered paper execution; no signal fitting.

Execution uses adjusted-close return ratios, not claimed historical order fills.
Naive issue timestamps are rejected. Entry is the first US equity session whose
New York calendar date is strictly after the issue's New York calendar date.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_DIR = ROOT / "results/corn_model/inputs/market"
NY = "America/New_York"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Timestamp must be finite and explicitly timezone-aware")
    return stamp.tz_convert("UTC")


def _dates(index):
    dates = pd.DatetimeIndex(index)
    if dates.tz is not None or dates.hasnans or not dates.equals(dates.normalize()):
        raise ValueError("Session dates must be naive calendar dates at midnight")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("Session dates must be unique and increasing")
    return dates


@dataclass
class MarketData:
    prices: pd.Series
    schedule: pd.DataFrame
    as_of: pd.Timestamp
    provenance: dict

    def __post_init__(self):
        self.as_of = utc_timestamp(self.as_of)
        self.prices = self.prices.copy().astype(float)
        self.schedule = self.schedule.copy()
        self.prices.index = _dates(self.prices.index)
        self.schedule.index = _dates(self.schedule.index)
        if self.prices.empty or self.schedule.empty:
            raise ValueError("Price and calendar data must not be empty")
        for column in ("open_at", "close_at"):
            self.schedule[column] = pd.to_datetime(self.schedule[column], utc=True)
            if self.schedule[column].isna().any():
                raise ValueError("Missing calendar open/close timestamp")
            if not self.schedule[column].dt.tz_convert(NY).dt.tz_localize(None).dt.normalize().equals(
                pd.Series(self.schedule.index, index=self.schedule.index, name=column)
            ):
                raise ValueError("Calendar timestamps and New York session dates disagree")
        if not self.schedule.open_at.lt(self.schedule.close_at).all():
            raise ValueError("Calendar close must follow open")
        if not np.isfinite(self.prices).all() or self.prices.le(0).any():
            raise ValueError("Prices must be finite and positive; missing quotes must remain missing sessions")
        if not self.prices.index.isin(self.schedule.index).all():
            raise ValueError("Price on a date absent from the exchange calendar")
        if self.schedule.loc[self.prices.index, "close_at"].gt(self.as_of).any():
            raise ValueError("Snapshot contains a price before its session has closed")

    @property
    def sessions(self):
        return self.schedule.index


def parse_yahoo(payload, schedule, as_of, symbol="CORN"):
    """Validate one instrument; exclude any unfinished daily bar without filling."""
    chart = payload.get("chart", {})
    if chart.get("error") or len(chart.get("result") or []) != 1:
        raise ValueError("Yahoo response must contain one error-free result")
    result = chart["result"][0]
    meta = result.get("meta", {})
    if meta.get("symbol") != symbol or meta.get("currency") != "USD":
        raise ValueError("Unexpected instrument or price currency")
    stamps = pd.to_datetime(result.get("timestamp", []), unit="s", utc=True)
    dates = stamps.tz_convert(NY).tz_localize(None).normalize()
    _dates(dates)
    values = result["indicators"]["adjclose"][0]["adjclose"]
    if len(values) != len(dates):
        raise ValueError("Yahoo timestamps and prices differ in length")
    prices = pd.Series(values, index=dates, name="adjusted_close", dtype=float)
    if not prices.index.isin(schedule.index).all():
        raise ValueError("Yahoo quote does not match a saved exchange session")
    complete = schedule.loc[prices.index, "close_at"].le(utc_timestamp(as_of)).to_numpy()
    prices = prices.iloc[np.flatnonzero(complete)]
    if not np.isfinite(prices).all() or prices.le(0).any():
        raise ValueError("Invalid completed Yahoo adjusted close")
    return prices


def load_market(directory=DEFAULT_MARKET_DIR):
    """Load the hashed offline snapshot; never downloads or silently repairs it."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("symbol"), manifest.get("currency"), manifest.get("asset_kind")) != (
        "CORN", "USD", "corn_futures_etf_proxy"
    ):
        raise ValueError("This loader requires the declared USD CORN ETF proxy snapshot")
    required = {"corn_yahoo_chart.json", "adjusted_prices.csv", "xnys_schedule.csv"}
    paths = [item["path"] for item in manifest["files"]]
    if len(paths) != len(set(paths)) or not required.issubset(paths):
        raise ValueError("Every raw price, derived price and calendar file must have a checksum")
    for item in manifest["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Manifest path must remain inside the snapshot")
        if sha256(directory / relative) != item["sha256"]:
            raise ValueError("Market input checksum mismatch: " + str(relative))
    schedule = pd.read_csv(directory / "xnys_schedule.csv", parse_dates=["date"]).set_index("date")
    for key in ("open_at", "close_at"):
        schedule[key] = pd.to_datetime(schedule[key], utc=True)
    prices = pd.read_csv(directory / "adjusted_prices.csv", parse_dates=["date"]).set_index("date").adjusted_close
    market = MarketData(prices, schedule, manifest["retrieved_at_utc"], manifest)
    raw = json.loads((directory / "corn_yahoo_chart.json").read_text())
    reconstructed = parse_yahoo(raw, market.schedule, market.as_of)
    if not reconstructed.index.equals(market.prices.index) or not np.allclose(
        reconstructed.to_numpy(), market.prices.to_numpy(), rtol=1e-13, atol=0
    ):
        raise ValueError("Saved prices disagree with raw Yahoo response")
    return market


def market_status(market):
    """Describe snapshot freshness; future scheduled sessions are never prices."""
    complete = market.schedule.index[market.schedule.close_at.le(market.as_of)]
    last = market.prices.index[-1]
    missing = complete[complete > last]
    return {"symbol": market.provenance.get("symbol", "CORN"),
            "asset_kind": market.provenance.get("asset_kind", "corn_futures_etf_proxy"),
            "snapshot_as_of_utc": market.as_of.isoformat(),
            "first_price_date": market.prices.index[0].date().isoformat(),
            "latest_price_date": last.date().isoformat(),
            "latest_completed_session": complete[-1].date().isoformat() if len(complete) else None,
            "missing_completed_sessions_after_latest_price": int(len(missing)),
            "is_live_quote": False, "price_basis": "Yahoo adjusted daily close, current retrieved vintage"}


def paper_trade(market, issue_at, direction, holding_sessions=20, exit_on=None,
                entry_cost_bps=25., exit_cost_bps=25., short_borrow_rate=.03):
    """Return a JSON-safe single-position result; 20 means 20 sessions AFTER entry.

    With exit_on, exit is the last session on/before that New York calendar date;
    holding_sessions is ignored. A price gap anywhere in the window abstains.
    Costs are fractions of initial notional; no leverage, interest, or rebalancing.
    A cash decision still uses the common window and requires valid prices, so
    control and active decisions have the same observable event support.
    """
    issue = utc_timestamp(issue_at)
    if isinstance(direction, (bool, np.bool_)) or direction not in (-1, 0, 1):
        raise ValueError("direction must be -1, 0, or +1")
    costs = (entry_cost_bps, exit_cost_bps, short_borrow_rate)
    if not all(np.isfinite(x) and x >= 0 for x in costs):
        raise ValueError("Costs must be finite and nonnegative")
    if entry_cost_bps + exit_cost_bps >= 10000:
        raise ValueError("Execution costs must be less than full initial notional")
    if exit_on is None and (isinstance(holding_sessions, bool) or not isinstance(holding_sessions, (int, np.integer)) or holding_sessions < 1):
        raise ValueError("holding_sessions must be a positive integer")
    issue_date = issue.tz_convert(NY).tz_localize(None).normalize()
    result = {"status": "unavailable", "reason": "", "symbol": market.provenance.get("symbol", "CORN"),
              "asset_kind": market.provenance.get("asset_kind", "corn_futures_etf_proxy"),
              "issue_at": issue.isoformat(), "direction": int(direction),
              "entry": None, "exit": None, "entry_at": None, "exit_at": None,
              "entry_adjusted_close": None, "exit_adjusted_close": None,
              "holding_sessions": None, "holding_calendar_days": None,
              "asset_return": None, "gross_return": None, "execution_cost": None,
              "borrow_cost": None, "net_return": None, "bankrupt": False,
              "entry_cost_bps": float(entry_cost_bps), "exit_cost_bps": float(exit_cost_bps),
              "short_borrow_rate": float(short_borrow_rate), "notional_fraction": float(abs(direction)),
              "paper_only": True, "snapshot_as_of_utc": market.as_of.isoformat()}

    def unavailable(reason):
        result["reason"] = reason
        return result

    sessions = market.sessions
    if issue_date < sessions[0] or issue_date >= sessions[-1]:
        return unavailable("Issue outside verified exchange calendar coverage")
    entry_i = int(sessions.searchsorted(issue_date, side="right"))
    if exit_on is None:
        exit_i = entry_i + int(holding_sessions)
        if exit_i >= len(sessions):
            return unavailable("Holding period exceeds verified exchange calendar coverage")
    else:
        finish = pd.Timestamp(exit_on)
        if pd.isna(finish):
            raise ValueError("exit_on must be a valid calendar date")
        if finish.tzinfo is not None:
            finish = finish.tz_convert(NY).tz_localize(None)
        if finish != finish.normalize():
            raise ValueError("exit_on is a calendar date, not an intraday timestamp")
        if finish > sessions[-1] or finish < sessions[0]:
            return unavailable("Exit outside verified exchange calendar coverage")
        exit_i = int(sessions.searchsorted(finish, side="right")) - 1
    if exit_i <= entry_i:
        return unavailable("Exit must follow entry by at least one exchange session")
    entry, finish = sessions[entry_i], sessions[exit_i]
    entry_at, exit_at = market.schedule.loc[entry, "close_at"], market.schedule.loc[finish, "close_at"]
    result.update(entry=entry.date().isoformat(), exit=finish.date().isoformat(),
                  entry_at=entry_at.isoformat(), exit_at=exit_at.isoformat(),
                  holding_sessions=exit_i-entry_i, holding_calendar_days=int((finish-entry).days))
    if entry_at <= issue:
        raise ValueError("Entry must be strictly after issue timestamp")
    if exit_at > market.as_of:
        return unavailable("Holding period has not completed at the snapshot retrieval time")
    expected = sessions[entry_i:exit_i+1]
    prices = market.prices.reindex(expected)
    if prices.isna().any() or not np.isfinite(prices).all() or prices.le(0).any():
        return unavailable("Missing or invalid adjusted close on an expected exchange session")
    asset_return = float(prices.iloc[-1] / prices.iloc[0] - 1)
    gross = float(direction * asset_return)
    execution = float(abs(direction) * (entry_cost_bps + exit_cost_bps) / 10000)
    borrow = float(short_borrow_rate * result["holding_calendar_days"] / 365 if direction == -1 else 0.)
    net = gross - execution - borrow
    result.update(status="cash" if direction == 0 else "executed", reason="", asset_return=asset_return,
                  entry_adjusted_close=float(prices.iloc[0]), exit_adjusted_close=float(prices.iloc[-1]),
                  gross_return=gross, execution_cost=execution, borrow_cost=borrow, net_return=net,
                  bankrupt=bool(net <= -1))
    if result["bankrupt"]:
        result.update(status="bankrupt", reason="Loss exhausts initial notional; do not compound this return")
    return result


def refresh_market(directory=DEFAULT_MARKET_DIR):
    """Explicit network refresh. Installs nothing; preserves the prior study inputs."""
    import requests
    import exchange_calendars as xc

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    as_of = pd.Timestamp(datetime.now(timezone.utc))
    calendar = xc.get_calendar("XNYS", start="2010-01-01", end=f"{as_of.year + 1}-12-31")
    schedule = calendar.schedule[["open", "close"]].rename(columns={"open": "open_at", "close": "close_at"})
    schedule.index = schedule.index.tz_localize(None)
    params = {"period1": 1262304000, "period2": int(as_of.timestamp()), "interval": "1d", "events": "div,splits"}
    response = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/CORN", params=params,
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    prices = parse_yahoo(response.json(), schedule, as_of)
    market = MarketData(prices, schedule, as_of, {"symbol": "CORN"})
    raw_path = directory / "corn_yahoo_chart.json"
    raw_path.write_bytes(response.content)
    schedule.to_csv(directory / "xnys_schedule.csv", index_label="date")
    prices.to_csv(directory / "adjusted_prices.csv", index_label="date", float_format="%.15g")
    old = ROOT / "results/satellite_validation/cybench_trading"
    historical = pd.read_csv(old / "adjusted_prices.csv", parse_dates=["date"]).set_index("date").iloc[:, 0]
    overlap = prices.reindex(historical.index)
    manifest = {"symbol": "CORN", "asset_kind": "corn_futures_etf_proxy", "currency": "USD",
                "retrieved_at_utc": as_of.isoformat(), "url": response.url,
                "price_basis": "Yahoo adjusted daily close; retrospectively adjusted current vintage",
                "original_vintage_prices_verified": False, "actual_december_futures": False,
                "calendar": {"name": "XNYS", "library": "exchange_calendars", "version": xc.__version__,
                             "url": "https://github.com/gerrymanoim/exchange_calendars",
                             "scope": "US equity core sessions, used for NYSE Arca ETF daily closes; includes early closes"},
                "coverage": market_status(market),
                "prior_audited_snapshot": {"path": "results/satellite_validation/cybench_trading/source_manifest.json",
                    "sha256": sha256(old / "source_manifest.json"), "raw_price_sha256": sha256(old / "inputs/corn_yahoo_chart.json"),
                    "overlap_rows": len(historical), "missing_overlap_rows": int(overlap.isna().sum()),
                    "max_absolute_adjusted_price_change": float(np.max(np.abs(overlap.to_numpy()-historical.to_numpy())))},
                "limitations": ["ETF holds multiple futures maturities; not December futures or spot corn",
                    "Adjusted-close ratios are a total-return proxy, not historical executable adjusted prices",
                    "Execution and borrow fees are assumptions, not verified spreads or locates",
                    "Fund expenses and rolling effects are already reflected in ETF prices",
                    "Snapshot completion does not prove contemporaneous original-vintage availability"],
                "files": [{"path": name, "sha256": sha256(directory / name)} for name in
                          ("corn_yahoo_chart.json", "adjusted_prices.csv", "xnys_schedule.csv")]}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return load_market(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-dir", type=Path, default=DEFAULT_MARKET_DIR)
    parser.add_argument("--refresh", action="store_true", help="Explicitly retrieve latest public Yahoo prices and rebuild calendar")
    args = parser.parse_args()
    data = refresh_market(args.market_dir) if args.refresh else load_market(args.market_dir)
    print(json.dumps(market_status(data), indent=2))
