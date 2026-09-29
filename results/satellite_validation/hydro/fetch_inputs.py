"""Explicit network refresh of fixed Sobradinho source snapshots (no fitting)."""
from __future__ import annotations
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile

import fsspec
import pandas as pd
import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parent
S3 = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def subset(item):
    kind, period, url = item
    path = ROOT / "inputs" / f"{kind}_{period}.parquet"
    columns = (["din_instante", "nom_usina", "id_ons", "val_geracao"] if kind == "generation"
               else ["din_instante", "nom_reservatorio", "id_reservatorio", "val_nivelmontante", "val_vazaoafluente"])
    head = requests.head(url, timeout=45)
    head.raise_for_status()
    field = "nom_usina" if kind == "generation" else "nom_reservatorio"
    if path.exists():
        selected = pd.read_parquet(path)
    else:
        with fsspec.open(url, "rb", block_size=2**20) as source:
            table = pq.read_table(source, columns=columns)
        frame = table.to_pandas()
        # Official BAUSB remains stable when the displayed name becomes UHE Sobradinho in2025-10.
        mask = frame.id_ons.eq("BAUSB") if kind == "generation" else frame[field].str.upper().eq("SOBRADINHO")
        selected = frame.loc[mask].copy()
    if selected.empty:
        raise ValueError(f"No Sobradinho identity match in {url}")
    if not path.exists():
        selected.to_parquet(path, index=False)
    print(f"{kind} {period}: {len(selected)} rows", flush=True)
    return {"kind": kind, "period": period, "url": url, "http_last_modified": head.headers.get("Last-Modified"),
            "http_etag": head.headers.get("ETag"), "original_file_bytes": head.headers.get("Content-Length"),
            "retrieved_at_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "columns": columns,
            "filter": "id_ons == BAUSB" if kind == "generation" else f"{field}.upper() == SOBRADINHO", "selected_rows": len(selected),
            "path": str(path.relative_to(ROOT)), "selected_snapshot_sha256": file_hash(path)}


def main():
    assert (ROOT / "protocol.json").exists(), "Freeze protocol before fetching outcomes"
    (ROOT / "inputs").mkdir(exist_ok=True)
    (ROOT / "metadata").mkdir(exist_ok=True)
    manifest = []
    urls = {
        "nasa_lake_page.html": "https://earth.gsfc.nasa.gov/gwm/lake/",
        "nasa_10day_product_table.xlsx": "https://earth.gsfc.nasa.gov/gwm/html/WATER-MONITOR.LakesReservoirs.10day.xlsx",
        "nasa_hep_archive.tgz": "https://har.gsfc.nasa.gov/pub/danu/power/Reservoir_data/GWM_HEP_reservoirs.tgz",
        "ons_generation_metadata.json": "https://dados.ons.org.br/api/3/action/package_show?id=geracao-usina-2",
        "ons_hydrology_metadata.json": "https://dados.ons.org.br/api/3/action/package_show?id=dados-hidrologicos-res",
        "aviso_gdr_latency.html": "https://www.aviso.altimetry.fr/en/data/products/wind/wave-products/gdr-ogdr-osdr-ra2-wwv.html",
    }
    for name, url in urls.items():
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        if name == "nasa_hep_archive.tgz":
            archive = tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz")
            path = ROOT / "inputs/lake000345.10d.2.txt"
            path.write_bytes(archive.extractfile("lake000345.10d.2.txt").read())
        else:
            path = ROOT / "metadata" / name
            path.write_bytes(response.content)
        manifest.append({"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                         "http_last_modified": response.headers.get("Last-Modified"),
                         "download_sha256": hashlib.sha256(response.content).hexdigest(),
                         "path": str(path.relative_to(ROOT)), "selected_snapshot_sha256": file_hash(path)})
    items = []
    for year in range(2008, 2026):
        items.append(("hydrology", str(year), f"{S3}/dados_hidrologicos_di/DADOS_HIDROLOGICOS_RES_{year}.parquet"))
        if year <= 2021:
            items.append(("generation", str(year), f"{S3}/geracao_usina_2_ho/GERACAO_USINA-2_{year}.parquet"))
        else:
            for month in range(1, 13):
                period = f"{year}_{month:02d}"
                items.append(("generation", period, f"{S3}/geracao_usina_2_ho/GERACAO_USINA-2_{period}.parquet"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        manifest.extend(pool.map(subset, items))
    (ROOT / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
