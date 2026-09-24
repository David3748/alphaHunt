#!/usr/bin/env python3
"""Fetch and ingest every unique primary SEC filing referenced by the lab cases."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox
import temporal_store as ts


def referenced_filings(store: ts.TemporalStore) -> list[dict]:
    rows = store.db.execute("""
        SELECT issuer_id, metadata_json
        FROM document_history
        WHERE source='legacy_evidence_pack' AND system_to IS NULL
    """).fetchall()
    existing = {row[0] for row in store.db.execute(
        "SELECT source_key FROM document_history WHERE source='sec_primary' AND system_to IS NULL"
    ).fetchall()}
    unique = {}
    for issuer_id, metadata_json in rows:
        for source in (json.loads(metadata_json).get("sources") or []):
            accession = source.get("accession")
            primary = source.get("primaryDocument")
            if not accession or not primary or accession in existing:
                continue
            item = dict(source)
            item["issuer_id"] = str(issuer_id)
            item["url"] = (f"https://www.sec.gov/Archives/edgar/data/{int(issuer_id)}/"
                           f"{accession.replace('-', '')}/{primary}")
            unique[(str(issuer_id), accession)] = item
    return [unique[key] for key in sorted(unique, key=lambda value: (int(value[0]), value[1]))]


def fetch_all(rows: list[dict], http: ox.CachedHTTP,
              concurrency: int) -> tuple[list[dict], list[dict]]:
    fetched, failures = [], []

    def work(row):
        raw = http.get(row["url"], timeout=90)
        return row, raw

    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, row): row for row in rows}
        completed = 0
        for future in cf.as_completed(futures):
            row = futures[future]
            try:
                source, raw = future.result()
                fetched.append({"source": source, "raw": raw})
            except Exception as exc:
                failures.append({"issuer_id": row["issuer_id"], "accession": row["accession"],
                                 "url": row["url"], "error": str(exc)})
            completed += 1
            if completed % 100 == 0:
                print(f"SEC filings {completed}/{len(rows)} failures={len(failures)}", file=sys.stderr)
    return fetched, failures


def ingest(db_path: Path, blob_root: Path, cache: Path,
           concurrency: int = 8, system_time=None, batch_name: str = "default") -> dict:
    with ts.TemporalStore(db_path, blob_root) as store:
        rows = referenced_filings(store)
        http = ox.CachedHTTP(cache, min_interval=0.11)
        fetched, failures = fetch_all(rows, http, concurrency)
        proposed = ts.parse_time(system_time or dt.datetime.now(dt.timezone.utc)).isoformat()
        store.db.execute("BEGIN")
        try:
            saved = store.set_metadata_once(
                f"sec_primary_corpus_system_time_{batch_name}", proposed, proposed)
            recorded_at = ts.parse_time(saved)
            raw_bytes = text_bytes = documents = 0
            for item in fetched:
                source, raw = item["source"], item["raw"]
                cleaned = ox.clean_document(raw)
                raw_hash = store.put_content(
                    raw, "text/html", {"source": "sec_primary", "url": source["url"],
                                       "accession": source["accession"], "representation": "raw"},
                    recorded_at)
                text_hash = store.put_content(
                    cleaned, "text/plain", {"source": "sec_primary", "url": source["url"],
                                            "accession": source["accession"],
                                            "representation": "clean_text", "derived_from": raw_hash},
                    recorded_at)
                available = source.get("acceptanceDateTime") or source.get("date")
                store.append_document(
                    "sec_primary", source["accession"], raw_hash,
                    source.get("reportDate") or source.get("date") or available,
                    ts.parse_time(available, end_of_day=not bool(source.get("acceptanceDateTime"))),
                    recorded_at, issuer_id=source["issuer_id"], document_type=source.get("form"),
                    metadata={"accession": source["accession"], "form": source.get("form"),
                              "filing_date": source.get("date"),
                              "acceptanceDateTime": source.get("acceptanceDateTime"),
                              "availability_precision": "exact" if source.get("acceptanceDateTime") else "date_only",
                              "primaryDocument": source.get("primaryDocument"), "url": source["url"],
                              "text_content_hash": text_hash})
                raw_bytes += len(raw)
                text_bytes += len(cleaned.encode())
                documents += 1
            result = {"referenced_filings": len(rows), "documents_ingested": documents,
                      "failures": failures, "raw_bytes": raw_bytes, "clean_text_bytes": text_bytes,
                      "system_time": recorded_at.isoformat(), "summary": store.summary()}
            store.db.execute("COMMIT")
            return result
        except Exception:
            store.db.execute("ROLLBACK")
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sec-corpus", description=__doc__)
    parser.add_argument("--db", type=Path, default=ts.DEFAULT_DB)
    parser.add_argument("--blobs", type=Path, default=ts.DEFAULT_BLOBS)
    parser.add_argument("--cache", type=Path, default=Path("research/sec_filing_cache"))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--system-time", default=None)
    parser.add_argument("--batch-name", default="default")
    args = parser.parse_args(argv)
    print(json.dumps(ingest(args.db, args.blobs, args.cache, args.concurrency,
                            args.system_time, args.batch_name), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
