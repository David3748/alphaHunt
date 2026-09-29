"""Explicit source refresh, without opening forecasts or joining to prices."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import pandas as pd
import requests

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(raw_stats):
    import exchange_calendars as xc
    if not (OUT / "protocol.json").exists() or not (OUT / "late/protocol.json").exists():
        raise ValueError("Freeze both trading protocols before refreshing sources")
    county = ROOT / "results/satellite_validation/cybench_maize"
    source = json.loads((county / "source_manifest.json").read_text())
    member = next(m for m in source["members"] if m["name"].endswith("yield_maize_US.csv"))
    if sha(raw_stats) != member["raw_sha256"]:
        raise ValueError("Raw area statistics differ from audited CY-Bench member")
    (OUT / "inputs").mkdir(exist_ok=True)
    params = {"period1": int(pd.Timestamp("2013-01-01", tz="UTC").timestamp()),
              "period2": int(pd.Timestamp("2024-01-01", tz="UTC").timestamp()),
              "interval": "1d", "events": "div,splits"}
    response = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/CORN", params=params,
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload["chart"].get("error"):
        raise ValueError("Invalid price response")
    path = OUT / "inputs/corn_yahoo_chart.json"
    path.write_bytes(response.content)
    manifest = {"prices": {"path": str(path.relative_to(OUT)), "url": response.url,
                            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": sha(path)},
                "metadata_sources": []}
    for name, url in [("teucrium_fund_facts.html", "https://etfs.teucrium.com/CORN/fund_facts"),
                      ("teucrium_corn.html", "https://teucrium.com/corn")]:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        path = OUT / "inputs" / name
        path.write_bytes(response.content)
        manifest["metadata_sources"].append({"path": str(path.relative_to(OUT)), "url": response.url,
                                              "sha256": sha(path), "retrieved_at_utc": datetime.now(timezone.utc).isoformat()})
    statistics = pd.read_csv(raw_stats, dtype={"adm_id": str})
    path = OUT / "inputs/county_yield_area.csv.gz"
    statistics[["adm_id", "harvest_year", "yield", "harvest_area"]].to_csv(path, index=False,
                                                                         compression={"method": "gzip", "mtime": 0})
    manifest["county_stats"] = {"path": str(path.relative_to(OUT)), "sha256": sha(path),
                                 "raw_member_sha256": sha(raw_stats), "source_record": "https://zenodo.org/records/17279151",
                                 "archive_member": member["name"], "units": {"yield": "t/ha", "harvest_area": "ha"},
                                 "availability_assumption": "harvestyear+1June30; exactprior-yearareaonly"}
    path = OUT / "inputs/locations.csv"
    shutil.copyfile(county / "locations.csv", path)
    manifest["locations"] = {"path": str(path.relative_to(OUT)), "sha256": sha(path),
                              "source": "Fixed county validation Census universe; cybench_maize/source_manifest.json"}
    sessions = xc.get_calendar("XNYS", start="2013-01-01", end="2023-12-31").sessions
    if sessions.tz is not None:
        sessions = sessions.tz_localize(None)
    path = OUT / "inputs/xnys_sessions.csv"
    sessions.to_series(index=None).to_csv(path, index=False, header=["date"])
    manifest["calendar"] = {"path": str(path.relative_to(OUT)), "sha256": sha(path), "calendar": "XNYS",
                             "library": "exchange_calendars", "version": xc.__version__,
                             "source": "https://github.com/gerrymanoim/exchange_calendars"}
    (OUT / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for entry in [manifest[k] for k in ("prices", "county_stats", "locations", "calendar")] + manifest["metadata_sources"]:
        entry["path"] = "../" + entry["path"]
    (OUT / "late/source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Refreshed raw sources; no forecasts opened or directions computed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", required=True)
    parser.add_argument("--raw-stats", type=Path, default=ROOT / "work/audit_construction/cybench_raw/yield_maize_US.csv")
    main(parser.parse_args().raw_stats)
