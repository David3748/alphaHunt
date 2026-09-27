#!/usr/bin/env python3
"""Explicit network refresh of the frozen Pacific inputs; reject changed sources."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import requests
import pandas as pd

OUT = Path(__file__).resolve().parent

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", required=True)
    parser.parse_args()
    manifest = json.loads((OUT / "source_manifest.json").read_text())
    sst, storms = manifest
    parent = OUT / sst["derived_from"]
    if digest(parent.read_bytes()) != sst["parent_sha256"]:
        raise ValueError("Frozen parent OISST values changed; refresh adjacent hurricane inputs first")
    frame = pd.read_csv(parent)
    derived = frame[["year", "source_month", "nino34_sst_c", "nino34_ocean_cells"]].to_csv(index=False).encode()
    if digest(derived) != sst["sha256"]:
        raise ValueError("Derived OISST snapshot differs")
    response = requests.get(storms["url"], timeout=120)
    response.raise_for_status()
    if digest(response.content) != storms["raw_download_sha256"]:
        raise ValueError("NHC source changed; do not silently replace the frozen study")
    compressed = gzip.compress(response.content, mtime=0)
    # gzip's OS byte differs across Python builds. The raw source checksum,
    # not compressed bytes, verifies an identical refresh on all platforms.
    (OUT / sst["path"]).write_bytes(derived)
    existing = OUT / storms["path"]
    if existing.exists() and digest(gzip.decompress(existing.read_bytes())) == storms["raw_download_sha256"]:
        print("Verified existing exact frozen snapshots")
        return
    # Preserve the original gzip header metadata for its stored checksum.
    for os_byte in (3, 255):
        candidate = compressed[:9] + bytes([os_byte]) + compressed[10:]
        if digest(candidate) == storms["sha256"]:
            existing.write_bytes(candidate)
            print("Restored exact frozen snapshots")
            return
    raise ValueError("Compression bytes differ; raw source matches, but frozen snapshot was not replaced")

if __name__ == "__main__":
    main()
