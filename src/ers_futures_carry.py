#!/usr/bin/env python3
"""Ingest USDA ERS weekly contract settlements and construct grain calendar carry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests


DEFAULT_URL = "https://www.ers.usda.gov/media/6498/input-data-csv-file.csv?v=11049"
COMMODITY_MAP = {"Corn": "corn", "Soybeans": "soy", "Wheat": "wheat"}


def download(url: str, target: Path, refresh: bool = False) -> None:
    if target.exists() and target.stat().st_size > 1_000_000 and not refresh:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with temp.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
    temp.replace(target)


def prepared_curve_rows(rows: pd.DataFrame) -> pd.DataFrame:
    x = rows[
        (rows["item"] == "Futures price daily")
        & rows["commodity"].isin(COMMODITY_MAP)
        & (rows["futures_exchange"] == "CBOT")
    ].copy()
    x["date"] = pd.to_datetime(x["data_source_date"], errors="coerce")
    x["contract_month"] = pd.to_datetime(x["futures_contract"], format="%Y-%m", errors="coerce")
    x["value"] = pd.to_numeric(x["value"], errors="coerce")
    x = x.dropna(subset=["date", "contract_month", "value"])
    x = x[x["value"] > 0]
    x["approx_expiry"] = x["contract_month"] + pd.Timedelta(days=14)
    return x


def carry_from_rows(rows: pd.DataFrame, min_days_to_expiry: int = 28) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use the first two eligible CBOT maturities; positive means backwardation."""
    x = prepared_curve_rows(rows)
    eligible = x[(x["approx_expiry"] - x["date"]).dt.days >= min_days_to_expiry]

    records = []
    for (commodity, observed), group in eligible.groupby(["commodity", "date"], sort=True):
        curve = group.sort_values("contract_month").drop_duplicates("contract_month")
        if len(curve) < 2:
            continue
        front, deferred = curve.iloc[0], curve.iloc[1]
        gap_days = int((deferred["contract_month"] - front["contract_month"]).days)
        if gap_days <= 0:
            continue
        annualized = np.log(float(front["value"]) / float(deferred["value"])) * 365.25 / gap_days
        records.append({
            "date": observed,
            "commodity": COMMODITY_MAP[commodity],
            "annualized_carry": annualized,
            "front_contract": front["contract_month"].strftime("%Y-%m"),
            "deferred_contract": deferred["contract_month"].strftime("%Y-%m"),
            "front_price": float(front["value"]),
            "deferred_price": float(deferred["value"]),
            "contract_gap_days": gap_days,
        })
    detail = pd.DataFrame(records).sort_values(["date", "commodity"])
    panel = detail.pivot(index="date", columns="commodity", values="annualized_carry")
    panel = panel.reindex(columns=["corn", "soy", "wheat"]).resample("W-FRI").last()
    return panel, detail


def contract_returns_from_rows(rows: pd.DataFrame, carry_detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return each selected front contract over the next observation, without roll-price jumps."""
    x = prepared_curve_rows(rows)
    lookup = x.drop_duplicates(["commodity", "date", "contract_month"]).set_index(
        ["commodity", "date", "contract_month"]
    )["value"]
    records = []
    for commodity, selected in carry_detail.groupby("commodity", sort=True):
        selected = selected.sort_values("date").reset_index(drop=True)
        source_name = next(name for name, mapped in COMMODITY_MAP.items() if mapped == commodity)
        previous_contract = None
        for i in range(len(selected) - 1):
            current = selected.iloc[i]
            next_date = pd.Timestamp(selected.iloc[i + 1]["date"])
            contract_text = str(current["front_contract"])
            contract = pd.to_datetime(contract_text, format="%Y-%m")
            key_now = (source_name, pd.Timestamp(current["date"]), contract)
            key_next = (source_name, next_date, contract)
            rolled = previous_contract is not None and contract_text != previous_contract
            previous_contract = contract_text
            if key_now not in lookup.index or key_next not in lookup.index:
                continue
            start_price, end_price = float(lookup.loc[key_now]), float(lookup.loc[key_next])
            records.append({
                "date": next_date,
                "commodity": commodity,
                "return": end_price / start_price - 1.0,
                "held_contract": contract_text,
                "rolled_at_start": bool(rolled),
            })
    detail = pd.DataFrame(records).sort_values(["date", "commodity"])
    returns = detail.pivot(index="date", columns="commodity", values="return")
    rolls = detail.pivot(index="date", columns="commodity", values="rolled_at_start").astype("boolean")
    returns = returns.reindex(columns=["corn", "soy", "wheat"]).resample("W-FRI").last()
    rolls = rolls.reindex(columns=["corn", "soy", "wheat"]).astype("boolean")
    rolls = rolls.resample("W-FRI").max().astype("boolean").fillna(False).astype(bool)
    return returns, rolls


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=Path("research/slow_crop_alpha/raw/ers_inputdata.csv"))
    parser.add_argument("--min-days-to-expiry", type=int, default=28)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    download(args.url, args.output, args.refresh)
    rows = pd.read_csv(args.output, low_memory=False)
    carry, detail = carry_from_rows(rows, args.min_days_to_expiry)
    contract_returns, roll_flags = contract_returns_from_rows(rows, detail)
    derived = args.output.parent
    carry.to_parquet(derived / "ers_weekly_carry.parquet")
    detail.to_parquet(derived / "ers_carry_contract_pairs.parquet", index=False)
    contract_returns.to_parquet(derived / "ers_contract_returns.parquet")
    roll_flags.to_parquet(derived / "ers_contract_roll_flags.parquet")
    manifest = {
        "source": args.url,
        "sha256": sha256(args.output),
        "definition": "log(front/deferred) annualized by maturity gap; positive is backwardation",
        "minimum_days_to_approximate_expiry": args.min_days_to_expiry,
        "approximate_expiry": "15th calendar day of contract month",
        "coverage": {"start": str(carry.index.min().date()), "end": str(carry.index.max().date())},
    }
    (derived / "ers_carry_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
