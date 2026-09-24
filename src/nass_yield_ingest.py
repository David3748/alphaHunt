#!/usr/bin/env python3
"""Stream-filter the public USDA NASS crops bulk file to state yield rows."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
from pathlib import Path

import requests


KEEP_COMMODITIES = {"CORN", "SOYBEANS", "WHEAT"}


def keep(row: dict[str, str]) -> bool:
    try:
        year = int(row.get("YEAR", "0"))
    except ValueError:
        return False
    return (
        row.get("SOURCE_DESC") == "SURVEY"
        and row.get("COMMODITY_DESC") in KEEP_COMMODITIES
        and row.get("STATISTICCAT_DESC") == "YIELD"
        and row.get("UNIT_DESC") == "BU / ACRE"
        and row.get("DOMAIN_DESC") == "TOTAL"
        and row.get("AGG_LEVEL_DESC") == "STATE"
        and row.get("FREQ_DESC") == "ANNUAL"
        and year >= 2001
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    with requests.get(args.url, stream=True, timeout=(30, 600)) as response:
        response.raise_for_status()
        response.raw.decode_content = True
        with gzip.GzipFile(fileobj=response.raw) as gz, \
                io.TextIOWrapper(gz, encoding="utf-8", errors="replace", newline="") as text, \
                tmp.open("w", encoding="utf-8", newline="") as destination:
            reader = csv.DictReader(text, delimiter="\t")
            if reader.fieldnames is None:
                raise RuntimeError("NASS bulk file has no header")
            writer = csv.DictWriter(destination, fieldnames=reader.fieldnames, delimiter="\t")
            writer.writeheader()
            for row in reader:
                if keep(row):
                    writer.writerow({k: v.replace("\x00", "") for k, v in row.items()})
    tmp.replace(args.output)


if __name__ == "__main__":
    main()
