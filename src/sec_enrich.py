#!/usr/bin/env python3
"""Append exact SEC acceptance-time revisions to migrated evidence packs."""

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


def filing_index(cik: str, http: ox.CachedHTTP) -> dict[str, dict]:
    _, rows = ox.submission_rows(cik, http, include_archives=True)
    return {row["accessionNumber"]: row for row in rows}


def collect_indexes(ciks: list[str], cache: Path, concurrency: int = 8) -> tuple[dict, dict]:
    http = ox.CachedHTTP(cache, min_interval=0.11)
    indexes, failures = {}, {}
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(filing_index, cik, http): cik for cik in ciks}
        completed = 0
        for future in cf.as_completed(futures):
            cik = futures[future]
            try:
                indexes[cik] = future.result()
            except Exception as exc:
                failures[cik] = str(exc)
            completed += 1
            if completed % 25 == 0:
                print(f"SEC metadata {completed}/{len(ciks)} failures={len(failures)}", file=sys.stderr)
    return indexes, failures


def source_fallback_time(source: dict) -> dt.datetime | None:
    value = source.get("date")
    if not value:
        return None
    try:
        return ts.parse_time(value, end_of_day=True)
    except ValueError:
        return None


def enrich(db_path: Path, blob_root: Path, cache: Path,
           concurrency: int = 8, system_time=None, batch_name: str = "default") -> dict:
    with ts.TemporalStore(db_path, blob_root) as store:
        documents = store.db.execute("""
            SELECT version_id, issuer_id, source, source_key, document_type,
                   effective_from, effective_to, content_hash, metadata_json
            FROM document_history
            WHERE source='legacy_evidence_pack' AND system_to IS NULL
            ORDER BY source_key
        """).fetchall()
        documents = [row for row in documents
                     if json.loads(row[8]).get("needs_acceptance_time_enrichment")]
        ciks = sorted({str(row[1]) for row in documents if row[1]}, key=int)
        indexes, failures = collect_indexes(ciks, cache, concurrency)
        proposed = ts.parse_time(system_time or dt.datetime.now(dt.timezone.utc)).isoformat()
        store.db.execute("BEGIN")
        try:
            key = f"sec_acceptance_enrichment_system_time_{batch_name}"
            saved = store.set_metadata_once(key, proposed, proposed)
            recorded_at = ts.parse_time(saved)
            revised = exact = mixed = unmatched_total = 0
            for (_, issuer_id, source, source_key, document_type, effective_from,
                 effective_to, content_hash, metadata_json) in documents:
                metadata = json.loads(metadata_json)
                sources = metadata.get("sources") or []
                times, unmatched, enriched_sources = [], [], []
                issuer_index = indexes.get(str(issuer_id), {})
                for item in sources:
                    current = dict(item)
                    filing = issuer_index.get(item.get("accession"))
                    acceptance = filing.get("acceptanceDateTime") if filing else None
                    if acceptance:
                        current["acceptanceDateTime"] = acceptance
                        current["primaryDocument"] = filing.get("primaryDocument")
                        current["reportDate"] = filing.get("reportDate")
                        times.append(ts.parse_time(acceptance))
                    else:
                        unmatched.append(item.get("accession"))
                        fallback = source_fallback_time(item)
                        if fallback:
                            times.append(fallback)
                    enriched_sources.append(current)
                if not times:
                    unmatched_total += len(unmatched)
                    continue
                precision = "exact" if sources and not unmatched else "mixed"
                new_metadata = dict(metadata)
                new_metadata.update({
                    "sources": enriched_sources,
                    "availability_precision": precision,
                    "needs_acceptance_time_enrichment": bool(unmatched),
                    "unmatched_accessions": unmatched,
                    "acceptance_enriched_at": recorded_at.isoformat(),
                })
                store.append_document(
                    source, source_key, content_hash, effective_from, max(times), recorded_at,
                    issuer_id=str(issuer_id), document_type=document_type,
                    effective_to=effective_to, metadata=new_metadata)
                revised += 1
                exact += int(precision == "exact")
                mixed += int(precision == "mixed")
                unmatched_total += len(unmatched)
            result = {"issuers": len(ciks), "issuer_failures": failures,
                      "documents_considered": len(documents), "documents_revised": revised,
                      "exact_documents": exact, "mixed_documents": mixed,
                      "unmatched_accessions": unmatched_total,
                      "system_time": recorded_at.isoformat()}
            store.db.execute("COMMIT")
            return result
        except Exception:
            store.db.execute("ROLLBACK")
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sec-enrich", description=__doc__)
    parser.add_argument("--db", type=Path, default=ts.DEFAULT_DB)
    parser.add_argument("--blobs", type=Path, default=ts.DEFAULT_BLOBS)
    parser.add_argument("--cache", type=Path, default=Path("research/sec_cache"))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--system-time", default=None)
    parser.add_argument("--batch-name", default="default")
    args = parser.parse_args(argv)
    print(json.dumps(enrich(args.db, args.blobs, args.cache, args.concurrency,
                            args.system_time, args.batch_name), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
