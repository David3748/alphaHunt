#!/usr/bin/env python3
"""Bitemporal, point-in-time research store for alphaHunt.

The storage model deliberately distinguishes:

* effective time: when a fact applies in the economic world;
* available time: when a market participant could first know it; and
* system time: when this database recorded a version.

Version tables are append-only. ``*_history`` views derive ``system_to`` with
LEAD(), so corrections never overwrite earlier database states. Large source
payloads live in a content-addressed blob directory; DuckDB stores their hashes,
lineage, and temporal metadata.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox


UTC = dt.timezone.utc
FAR_FUTURE = dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
DEFAULT_DB = Path("research/alphahunt.duckdb")
DEFAULT_BLOBS = Path("research/blobs")


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS store_metadata (
    key VARCHAR PRIMARY KEY,
    value_json JSON NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS content_objects (
    content_hash VARCHAR PRIMARY KEY,
    storage_uri VARCHAR NOT NULL,
    byte_length BIGINT NOT NULL,
    media_type VARCHAR NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    metadata_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_versions (
    experiment_id VARCHAR PRIMARY KEY,
    experiment_name VARCHAR NOT NULL,
    experiment_version VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    code_hash VARCHAR,
    config_json JSON NOT NULL,
    status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS security_versions (
    version_id VARCHAR PRIMARY KEY,
    security_id VARCHAR NOT NULL,
    issuer_id VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    exchange VARCHAR,
    instrument_type VARCHAR NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    system_from TIMESTAMPTZ NOT NULL,
    attributes_json JSON NOT NULL,
    row_hash VARCHAR NOT NULL,
    UNIQUE(security_id, system_from)
);

CREATE TABLE IF NOT EXISTS document_versions (
    version_id VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL,
    issuer_id VARCHAR,
    source VARCHAR NOT NULL,
    source_key VARCHAR NOT NULL,
    document_type VARCHAR,
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL,
    system_from TIMESTAMPTZ NOT NULL,
    content_hash VARCHAR NOT NULL REFERENCES content_objects(content_hash),
    metadata_json JSON NOT NULL,
    row_hash VARCHAR NOT NULL,
    UNIQUE(document_id, system_from)
);

CREATE TABLE IF NOT EXISTS claim_versions (
    version_id VARCHAR PRIMARY KEY,
    claim_id VARCHAR NOT NULL,
    issuer_id VARCHAR NOT NULL,
    claim_type VARCHAR NOT NULL,
    claim_key VARCHAR NOT NULL,
    value_json JSON NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL,
    system_from TIMESTAMPTZ NOT NULL,
    source_document_id VARCHAR,
    evidence_json JSON NOT NULL,
    extractor_name VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    confidence DOUBLE,
    row_hash VARCHAR NOT NULL,
    UNIQUE(claim_id, system_from)
);

CREATE TABLE IF NOT EXISTS input_manifests (
    manifest_id VARCHAR PRIMARY KEY,
    knowledge_at TIMESTAMPTZ NOT NULL,
    system_as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    manifest_hash VARCHAR NOT NULL UNIQUE,
    metadata_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS manifest_items (
    manifest_id VARCHAR NOT NULL REFERENCES input_manifests(manifest_id),
    ordinal INTEGER NOT NULL,
    item_type VARCHAR NOT NULL,
    logical_id VARCHAR NOT NULL,
    version_id VARCHAR NOT NULL,
    content_hash VARCHAR,
    role VARCHAR,
    PRIMARY KEY(manifest_id, ordinal)
);

CREATE TABLE IF NOT EXISTS experiment_cases (
    case_id VARCHAR PRIMARY KEY,
    experiment_id VARCHAR NOT NULL REFERENCES experiment_versions(experiment_id),
    security_id VARCHAR NOT NULL,
    prediction_as_of TIMESTAMPTZ NOT NULL,
    horizon_end TIMESTAMPTZ,
    split VARCHAR,
    manifest_id VARCHAR NOT NULL REFERENCES input_manifests(manifest_id),
    selection_json JSON NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_id VARCHAR PRIMARY KEY,
    experiment_id VARCHAR NOT NULL REFERENCES experiment_versions(experiment_id),
    case_id VARCHAR NOT NULL REFERENCES experiment_cases(case_id),
    role VARCHAR NOT NULL,
    replicate INTEGER,
    as_of_at TIMESTAMPTZ NOT NULL,
    system_as_of TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    manifest_id VARCHAR NOT NULL REFERENCES input_manifests(manifest_id),
    model VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    code_version VARCHAR,
    output_json JSON NOT NULL,
    row_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    prediction_id VARCHAR PRIMARY KEY,
    experiment_id VARCHAR NOT NULL REFERENCES experiment_versions(experiment_id),
    case_id VARCHAR NOT NULL REFERENCES experiment_cases(case_id),
    task VARCHAR NOT NULL,
    replicate INTEGER,
    horizon_days INTEGER NOT NULL,
    prediction_as_of TIMESTAMPTZ NOT NULL,
    system_as_of TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    manifest_id VARCHAR NOT NULL REFERENCES input_manifests(manifest_id),
    model VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    code_version VARCHAR,
    probability DOUBLE,
    decision VARCHAR,
    output_json JSON NOT NULL,
    supersedes_prediction_id VARCHAR,
    row_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS outcome_versions (
    version_id VARCHAR PRIMARY KEY,
    outcome_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL REFERENCES experiment_cases(case_id),
    label_name VARCHAR NOT NULL,
    horizon_start TIMESTAMPTZ NOT NULL,
    horizon_end TIMESTAMPTZ NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    system_from TIMESTAMPTZ NOT NULL,
    value_json JSON NOT NULL,
    source VARCHAR NOT NULL,
    row_hash VARCHAR NOT NULL,
    UNIQUE(outcome_id, system_from)
);

CREATE TABLE IF NOT EXISTS experiment_metrics (
    metric_set_id VARCHAR PRIMARY KEY,
    experiment_id VARCHAR NOT NULL REFERENCES experiment_versions(experiment_id),
    split VARCHAR NOT NULL,
    generated_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    metrics_json JSON NOT NULL,
    row_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_id VARCHAR PRIMARY KEY,
    experiment_id VARCHAR NOT NULL REFERENCES experiment_versions(experiment_id),
    created_at TIMESTAMPTZ NOT NULL,
    knowledge_policy VARCHAR NOT NULL,
    system_as_of TIMESTAMPTZ NOT NULL,
    universe_version VARCHAR,
    execution_json JSON NOT NULL,
    metrics_json JSON NOT NULL,
    input_hash VARCHAR NOT NULL
);

CREATE OR REPLACE VIEW security_history AS
SELECT *, LEAD(system_from) OVER (PARTITION BY security_id ORDER BY system_from) AS system_to
FROM security_versions;

CREATE OR REPLACE VIEW document_history AS
SELECT *, LEAD(system_from) OVER (PARTITION BY document_id ORDER BY system_from) AS system_to
FROM document_versions;

CREATE OR REPLACE VIEW claim_history AS
SELECT *, LEAD(system_from) OVER (PARTITION BY claim_id ORDER BY system_from) AS system_to
FROM claim_versions;

CREATE OR REPLACE VIEW outcome_history AS
SELECT *, LEAD(system_from) OVER (PARTITION BY outcome_id ORDER BY system_from) AS system_to
FROM outcome_versions;
"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(*parts: Any) -> str:
    raw = "\x1f".join(canonical_json(part) if not isinstance(part, str) else part for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_time(value: Any, end_of_day: bool = False) -> dt.datetime:
    if isinstance(value, dt.datetime):
        result = value
    elif isinstance(value, dt.date):
        result = dt.datetime.combine(value, dt.time.max if end_of_day else dt.time.min)
    else:
        text = str(value)
        if len(text) == 10:
            result = dt.datetime.combine(dt.date.fromisoformat(text), dt.time.max if end_of_day else dt.time.min)
        else:
            result = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def json_value(value: Any) -> str:
    return canonical_json(value if value is not None else {})


class TemporalStore:
    def __init__(self, db_path: Path = DEFAULT_DB, blob_root: Path = DEFAULT_BLOBS,
                 read_only: bool = False):
        self.db_path = Path(db_path)
        self.blob_root = Path(blob_root)
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.blob_root.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.db_path), read_only=read_only)
        if not read_only:
            self.db.execute(SCHEMA_SQL)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def set_metadata_once(self, key: str, value: Any, recorded_at: Any) -> Any:
        row = self.db.execute("SELECT value_json FROM store_metadata WHERE key = ?", [key]).fetchone()
        if row:
            return json.loads(row[0])
        self.db.execute("INSERT INTO store_metadata VALUES (?, ?, ?)",
                        [key, json_value(value), parse_time(recorded_at)])
        return value

    def put_content(self, content: str | bytes, media_type: str = "text/plain",
                    metadata: dict | None = None, recorded_at: Any | None = None) -> str:
        raw = content.encode() if isinstance(content, str) else bytes(content)
        content_hash = hashlib.sha256(raw).hexdigest()
        path = self.blob_root / content_hash[:2] / content_hash
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise RuntimeError(f"content-address collision: {content_hash}")
        else:
            temp = path.with_suffix(f".tmp-{os.getpid()}")
            temp.write_bytes(raw)
            os.replace(temp, path)
        when = parse_time(recorded_at or dt.datetime.now(UTC))
        self.db.execute("""
            INSERT INTO content_objects VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO NOTHING
        """, [content_hash, str(path), len(raw), media_type, when, json_value(metadata)])
        return content_hash

    def register_experiment(self, name: str, version: str, created_at: Any,
                            config: dict, code_hash: str | None = None,
                            status: str = "research") -> str:
        experiment_id = digest("experiment", name, version)
        self.db.execute("""
            INSERT INTO experiment_versions VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id) DO NOTHING
        """, [experiment_id, name, version, parse_time(created_at), code_hash,
              json_value(config), status])
        return experiment_id

    def append_security(self, issuer_id: str, ticker: str, system_from: Any,
                        valid_from: Any = "1900-01-01", valid_to: Any | None = None,
                        exchange: str | None = None, instrument_type: str = "common_stock",
                        attributes: dict | None = None) -> tuple[str, str]:
        security_id = digest("security", str(issuer_id), ticker.upper())
        payload = {
            "security_id": security_id, "issuer_id": str(issuer_id), "ticker": ticker.upper(),
            "exchange": exchange, "instrument_type": instrument_type,
            "valid_from": parse_time(valid_from).isoformat(),
            "valid_to": parse_time(valid_to).isoformat() if valid_to else None,
            "attributes": attributes or {},
        }
        row_hash = digest(payload)
        system_time = parse_time(system_from)
        existing = self.db.execute(
            "SELECT version_id FROM security_versions WHERE security_id=? AND system_from=?",
            [security_id, system_time],
        ).fetchone()
        if existing:
            return security_id, existing[0]
        version_id = digest("security_version", security_id, system_time.isoformat(), row_hash)
        self.db.execute("""
            INSERT INTO security_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
        """, [version_id, security_id, str(issuer_id), ticker.upper(), exchange,
              instrument_type, parse_time(valid_from), parse_time(valid_to) if valid_to else None,
              system_time, json_value(attributes), row_hash])
        return security_id, version_id

    def append_document(self, source: str, source_key: str, content_hash: str,
                        effective_from: Any, available_at: Any, system_from: Any,
                        issuer_id: str | None = None, document_type: str | None = None,
                        effective_to: Any | None = None,
                        metadata: dict | None = None) -> tuple[str, str]:
        if not self.db.execute("SELECT 1 FROM content_objects WHERE content_hash=?", [content_hash]).fetchone():
            raise ValueError(f"unknown content hash: {content_hash}")
        document_id = digest("document", source, source_key)
        payload = {
            "document_id": document_id, "issuer_id": issuer_id, "source": source,
            "source_key": source_key, "document_type": document_type,
            "effective_from": parse_time(effective_from).isoformat(),
            "effective_to": parse_time(effective_to).isoformat() if effective_to else None,
            "available_at": parse_time(available_at).isoformat(), "content_hash": content_hash,
            "metadata": metadata or {},
        }
        row_hash = digest(payload)
        system_time = parse_time(system_from)
        version_id = digest("document_version", document_id, system_time.isoformat(), row_hash)
        self.db.execute("""
            INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
        """, [version_id, document_id, issuer_id, source, source_key, document_type,
              parse_time(effective_from), parse_time(effective_to) if effective_to else None,
              parse_time(available_at), system_time, content_hash, json_value(metadata), row_hash])
        return document_id, version_id

    def append_claim(self, issuer_id: str, claim_type: str, claim_key: str,
                     value: Any, effective_from: Any, available_at: Any,
                     system_from: Any, extractor_name: str, extractor_version: str,
                     source_document_id: str | None = None, evidence: Any = None,
                     confidence: float | None = None, effective_to: Any | None = None) -> tuple[str, str]:
        claim_id = digest("claim", issuer_id, claim_type, claim_key)
        payload = {
            "claim_id": claim_id, "issuer_id": issuer_id, "claim_type": claim_type,
            "claim_key": claim_key, "value": value,
            "effective_from": parse_time(effective_from).isoformat(),
            "effective_to": parse_time(effective_to).isoformat() if effective_to else None,
            "available_at": parse_time(available_at).isoformat(),
            "source_document_id": source_document_id, "evidence": evidence or [],
            "extractor_name": extractor_name, "extractor_version": extractor_version,
            "confidence": confidence,
        }
        row_hash = digest(payload)
        system_time = parse_time(system_from)
        version_id = digest("claim_version", claim_id, system_time.isoformat(), row_hash)
        self.db.execute("""
            INSERT INTO claim_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
        """, [version_id, claim_id, issuer_id, claim_type, claim_key, json_value(value),
              parse_time(effective_from), parse_time(effective_to) if effective_to else None,
              parse_time(available_at), system_time, source_document_id, json_value(evidence or []),
              extractor_name, extractor_version, confidence, row_hash])
        return claim_id, version_id

    def documents_as_of(self, issuer_id: str, knowledge_at: Any,
                        system_as_of: Any) -> list[dict]:
        rows = self.db.execute("""
            SELECT version_id, document_id, source, source_key, document_type,
                   effective_from, effective_to, available_at, system_from,
                   system_to, content_hash, metadata_json
            FROM document_history
            WHERE issuer_id = ? AND available_at <= ? AND system_from <= ?
              AND (system_to IS NULL OR ? < system_to)
            ORDER BY available_at, source_key
        """, [issuer_id, parse_time(knowledge_at), parse_time(system_as_of),
              parse_time(system_as_of)]).fetchall()
        columns = [d[0] for d in self.db.description]
        return [dict(zip(columns, row)) for row in rows]

    def claims_as_of(self, issuer_id: str, valid_at: Any, knowledge_at: Any,
                     system_as_of: Any) -> list[dict]:
        rows = self.db.execute("""
            SELECT version_id, claim_id, claim_type, claim_key, value_json,
                   effective_from, effective_to, available_at, system_from,
                   system_to, evidence_json, confidence
            FROM claim_history
            WHERE issuer_id = ?
              AND effective_from <= ? AND (effective_to IS NULL OR ? < effective_to)
              AND available_at <= ?
              AND system_from <= ? AND (system_to IS NULL OR ? < system_to)
            ORDER BY claim_type, claim_key
        """, [issuer_id, parse_time(valid_at), parse_time(valid_at),
              parse_time(knowledge_at), parse_time(system_as_of),
              parse_time(system_as_of)]).fetchall()
        columns = [d[0] for d in self.db.description]
        return [dict(zip(columns, row)) for row in rows]

    def create_manifest(self, knowledge_at: Any, system_as_of: Any,
                        items: Iterable[dict], metadata: dict | None = None,
                        created_at: Any | None = None) -> str:
        knowledge = parse_time(knowledge_at)
        system_time = parse_time(system_as_of)
        normalized = []
        for index, item in enumerate(items):
            current = dict(item)
            current.setdefault("ordinal", index)
            if current["item_type"] == "document":
                row = self.db.execute("""
                    SELECT document_id, available_at, system_from, system_to, content_hash
                    FROM document_history WHERE version_id = ?
                """, [current["version_id"]]).fetchone()
                if not row:
                    raise ValueError(f"unknown document version: {current['version_id']}")
                logical_id, available, system_from, system_to, content_hash = row
                if available > knowledge:
                    raise ValueError(f"lookahead document {logical_id}: {available} > {knowledge}")
                if system_from > system_time or (system_to is not None and system_time >= system_to):
                    raise ValueError(f"document version not visible at system time: {current['version_id']}")
                current.setdefault("logical_id", logical_id)
                current.setdefault("content_hash", content_hash)
            elif current["item_type"] == "claim":
                row = self.db.execute("""
                    SELECT claim_id, available_at, system_from, system_to
                    FROM claim_history WHERE version_id = ?
                """, [current["version_id"]]).fetchone()
                if not row:
                    raise ValueError(f"unknown claim version: {current['version_id']}")
                logical_id, available, system_from, system_to = row
                if available > knowledge:
                    raise ValueError(f"lookahead claim {logical_id}: {available} > {knowledge}")
                if system_from > system_time or (system_to is not None and system_time >= system_to):
                    raise ValueError(f"claim version not visible at system time: {current['version_id']}")
                current.setdefault("logical_id", logical_id)
            else:
                raise ValueError(f"unsupported manifest item type: {current['item_type']}")
            normalized.append({
                "ordinal": int(current["ordinal"]), "item_type": current["item_type"],
                "logical_id": current["logical_id"], "version_id": current["version_id"],
                "content_hash": current.get("content_hash"), "role": current.get("role"),
            })
        normalized.sort(key=lambda x: x["ordinal"])
        manifest_payload = {
            "knowledge_at": knowledge.isoformat(), "system_as_of": system_time.isoformat(),
            "items": normalized, "metadata": metadata or {},
        }
        manifest_hash = digest(manifest_payload)
        manifest_id = digest("manifest", manifest_hash)
        self.db.execute("""
            INSERT INTO input_manifests VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(manifest_id) DO NOTHING
        """, [manifest_id, knowledge, system_time,
              parse_time(created_at or dt.datetime.now(UTC)), manifest_hash, json_value(metadata)])
        for item in normalized:
            self.db.execute("""
                INSERT INTO manifest_items VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(manifest_id, ordinal) DO NOTHING
            """, [manifest_id, item["ordinal"], item["item_type"], item["logical_id"],
                  item["version_id"], item["content_hash"], item["role"]])
        return manifest_id

    def register_case(self, case_id: str, experiment_id: str, security_id: str,
                      prediction_as_of: Any, manifest_id: str, recorded_at: Any,
                      horizon_end: Any | None = None, split: str | None = None,
                      selection: dict | None = None):
        manifest = self.db.execute(
            "SELECT knowledge_at FROM input_manifests WHERE manifest_id=?", [manifest_id]).fetchone()
        if not manifest:
            raise ValueError(f"unknown manifest: {manifest_id}")
        if manifest[0] != parse_time(prediction_as_of):
            raise ValueError("case prediction_as_of must equal manifest knowledge_at")
        self.db.execute("""
            INSERT INTO experiment_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO NOTHING
        """, [case_id, experiment_id, security_id, parse_time(prediction_as_of),
              parse_time(horizon_end) if horizon_end else None, split, manifest_id,
              json_value(selection), parse_time(recorded_at)])

    def insert_analysis(self, *, experiment_id: str, case_id: str, role: str,
                        as_of_at: Any, system_as_of: Any, recorded_at: Any,
                        manifest_id: str, model: str, output: dict,
                        replicate: int | None = None, generated_at: Any | None = None,
                        prompt_version: str = "legacy-v1", schema_version: str = "legacy-v1",
                        code_version: str | None = None) -> str:
        self._validate_run_manifest(
            case_id, as_of_at, system_as_of, manifest_id,
            require_case_manifest=False,
        )
        payload = {"experiment_id": experiment_id, "case_id": case_id, "role": role,
                   "replicate": replicate, "as_of_at": parse_time(as_of_at).isoformat(),
                   "model": model, "output": output}
        row_hash = digest(payload)
        analysis_id = digest("analysis", payload)
        self.db.execute("""
            INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(analysis_id) DO NOTHING
        """, [analysis_id, experiment_id, case_id, role, replicate, parse_time(as_of_at),
              parse_time(system_as_of), parse_time(generated_at) if generated_at else None,
              parse_time(recorded_at), manifest_id, model, prompt_version, schema_version,
              code_version, json_value(output), row_hash])
        return analysis_id

    def insert_prediction(self, *, experiment_id: str, case_id: str, task: str,
                          horizon_days: int, prediction_as_of: Any, system_as_of: Any,
                          recorded_at: Any, manifest_id: str, model: str, output: dict,
                          probability: float | None = None, decision: str | None = None,
                          replicate: int | None = None, generated_at: Any | None = None,
                          prompt_version: str = "legacy-v1", schema_version: str = "legacy-v1",
                          code_version: str | None = None,
                          supersedes_prediction_id: str | None = None) -> str:
        self._validate_run_manifest(case_id, prediction_as_of, system_as_of, manifest_id)
        if probability is not None and not 0 <= probability <= 1:
            raise ValueError("probability must be in [0,1]")
        payload = {"experiment_id": experiment_id, "case_id": case_id, "task": task,
                   "replicate": replicate, "horizon_days": horizon_days,
                   "prediction_as_of": parse_time(prediction_as_of).isoformat(),
                   "model": model, "output": output}
        row_hash = digest(payload)
        prediction_id = digest("prediction", payload)
        self.db.execute("""
            INSERT INTO prediction_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(prediction_id) DO NOTHING
        """, [prediction_id, experiment_id, case_id, task, replicate, horizon_days,
              parse_time(prediction_as_of), parse_time(system_as_of),
              parse_time(generated_at) if generated_at else None, parse_time(recorded_at),
              manifest_id, model, prompt_version, schema_version, code_version,
              probability, decision, json_value(output), supersedes_prediction_id, row_hash])
        return prediction_id

    def _validate_run_manifest(self, case_id: str, as_of_at: Any,
                               system_as_of: Any, manifest_id: str,
                               require_case_manifest: bool = True):
        row = self.db.execute("""
            SELECT c.prediction_as_of, c.manifest_id, m.knowledge_at, m.system_as_of
            FROM experiment_cases c JOIN input_manifests m ON m.manifest_id=?
            WHERE c.case_id=?
        """, [manifest_id, case_id]).fetchone()
        if not row:
            raise ValueError("unknown case or manifest")
        case_asof, case_manifest, knowledge, manifest_system = row
        requested_asof = parse_time(as_of_at)
        if knowledge != requested_asof:
            raise ValueError("run as-of does not match its frozen manifest")
        if require_case_manifest and (case_manifest != manifest_id or case_asof != requested_asof):
            raise ValueError("prediction as-of does not match frozen case manifest")
        if manifest_system != parse_time(system_as_of):
            raise ValueError("system_as_of does not match frozen case manifest")

    def append_outcome(self, *, case_id: str, label_name: str, value: Any,
                       horizon_start: Any, horizon_end: Any, available_at: Any,
                       system_from: Any, source: str) -> tuple[str, str]:
        outcome_id = digest("outcome", case_id, label_name,
                            parse_time(horizon_start).isoformat(), parse_time(horizon_end).isoformat())
        payload = {"outcome_id": outcome_id, "case_id": case_id, "label_name": label_name,
                   "horizon_start": parse_time(horizon_start).isoformat(),
                   "horizon_end": parse_time(horizon_end).isoformat(),
                   "available_at": parse_time(available_at).isoformat(),
                   "value": value, "source": source}
        row_hash = digest(payload)
        system_time = parse_time(system_from)
        version_id = digest("outcome_version", outcome_id, system_time.isoformat(), row_hash)
        self.db.execute("""
            INSERT INTO outcome_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
        """, [version_id, outcome_id, case_id, label_name, parse_time(horizon_start),
              parse_time(horizon_end), parse_time(horizon_end), parse_time(available_at),
              system_time, json_value(value), source, row_hash])
        return outcome_id, version_id

    def insert_metrics(self, experiment_id: str, split: str, metrics: dict,
                       recorded_at: Any, generated_at: Any | None = None) -> str:
        row_hash = digest(experiment_id, split, metrics)
        metric_set_id = digest("metrics", experiment_id, split, row_hash)
        self.db.execute("""
            INSERT INTO experiment_metrics VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_set_id) DO NOTHING
        """, [metric_set_id, experiment_id, split,
              parse_time(generated_at) if generated_at else None, parse_time(recorded_at),
              json_value(metrics), row_hash])
        return metric_set_id

    def backtest_inputs(self, experiment_id: str, system_as_of: Any,
                        evaluation_at: Any, split: str | None = None) -> list[dict]:
        """Return immutable predictions paired with outcomes visible at both cutoffs."""
        params: list[Any] = [experiment_id, parse_time(system_as_of),
                             parse_time(system_as_of), parse_time(evaluation_at)]
        split_sql = ""
        if split is not None:
            split_sql = " AND c.split = ?"
            params.append(split)
        rows = self.db.execute(f"""
            SELECT p.prediction_id, p.case_id, c.security_id, c.split,
                   p.task, p.replicate, p.prediction_as_of, p.horizon_days,
                   p.probability, p.decision, p.manifest_id,
                   o.version_id AS outcome_version_id, o.label_name,
                   o.horizon_end, o.available_at AS outcome_available_at,
                   o.value_json AS outcome_json
            FROM prediction_runs p
            JOIN experiment_cases c USING(case_id)
            JOIN outcome_history o USING(case_id)
            WHERE p.experiment_id = ?
              AND p.recorded_at <= ?
              AND o.system_from <= ?
              AND (o.system_to IS NULL OR ? < o.system_to)
              AND o.available_at <= ?
              {split_sql}
            ORDER BY p.prediction_as_of, p.case_id, p.replicate
        """, [params[0], params[1], params[2], params[2], params[3], *params[4:]]).fetchall()
        columns = [d[0] for d in self.db.description]
        return [dict(zip(columns, row)) for row in rows]

    def record_backtest(self, experiment_id: str, system_as_of: Any,
                        evaluation_at: Any, knowledge_policy: str,
                        execution: dict, metrics: dict, created_at: Any,
                        split: str | None = None,
                        universe_version: str | None = None) -> str:
        inputs = self.backtest_inputs(experiment_id, system_as_of, evaluation_at, split)
        input_ids = [(row["prediction_id"], row["outcome_version_id"]) for row in inputs]
        input_hash = digest("backtest_inputs", input_ids)
        payload = {
            "experiment_id": experiment_id, "system_as_of": parse_time(system_as_of).isoformat(),
            "evaluation_at": parse_time(evaluation_at).isoformat(), "split": split,
            "knowledge_policy": knowledge_policy, "execution": execution,
            "metrics": metrics, "input_hash": input_hash,
        }
        backtest_id = digest("backtest", payload)
        execution_payload = dict(execution)
        execution_payload.update({"evaluation_at": parse_time(evaluation_at).isoformat(),
                                  "split": split, "input_count": len(inputs)})
        self.db.execute("""
            INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(backtest_id) DO NOTHING
        """, [backtest_id, experiment_id, parse_time(created_at), knowledge_policy,
              parse_time(system_as_of), universe_version, json_value(execution_payload),
              json_value(metrics), input_hash])
        return backtest_id

    def validate_integrity(self, verify_blobs: bool = True) -> list[str]:
        issues = []
        future_items = self.db.execute("""
            SELECT mi.manifest_id, dh.document_id, dh.available_at, m.knowledge_at
            FROM manifest_items mi
            JOIN input_manifests m USING(manifest_id)
            JOIN document_history dh ON dh.version_id=mi.version_id
            WHERE mi.item_type='document' AND dh.available_at > m.knowledge_at
        """).fetchall()
        issues.extend(f"manifest lookahead: {row}" for row in future_items)
        stale_items = self.db.execute("""
            SELECT mi.manifest_id, mi.version_id
            FROM manifest_items mi
            JOIN input_manifests m USING(manifest_id)
            JOIN document_history dh ON dh.version_id=mi.version_id
            WHERE mi.item_type='document' AND
              (dh.system_from > m.system_as_of OR (dh.system_to IS NOT NULL AND m.system_as_of >= dh.system_to))
        """).fetchall()
        issues.extend(f"manifest system-version mismatch: {row}" for row in stale_items)
        future_claims = self.db.execute("""
            SELECT mi.manifest_id, ch.claim_id, ch.available_at, m.knowledge_at
            FROM manifest_items mi
            JOIN input_manifests m USING(manifest_id)
            JOIN claim_history ch ON ch.version_id=mi.version_id
            WHERE mi.item_type='claim' AND ch.available_at > m.knowledge_at
        """).fetchall()
        issues.extend(f"manifest claim lookahead: {row}" for row in future_claims)
        stale_claims = self.db.execute("""
            SELECT mi.manifest_id, mi.version_id
            FROM manifest_items mi
            JOIN input_manifests m USING(manifest_id)
            JOIN claim_history ch ON ch.version_id=mi.version_id
            WHERE mi.item_type='claim' AND
              (ch.system_from > m.system_as_of OR (ch.system_to IS NOT NULL AND m.system_as_of >= ch.system_to))
        """).fetchall()
        issues.extend(f"manifest claim system-version mismatch: {row}" for row in stale_claims)
        case_mismatch = self.db.execute("""
            SELECT c.case_id FROM experiment_cases c JOIN input_manifests m USING(manifest_id)
            WHERE c.prediction_as_of != m.knowledge_at
        """).fetchall()
        issues.extend(f"case manifest mismatch: {row[0]}" for row in case_mismatch)
        early_outcomes = self.db.execute(
            "SELECT outcome_id FROM outcome_versions WHERE available_at < horizon_end").fetchall()
        issues.extend(f"outcome available before horizon end: {row[0]}" for row in early_outcomes)
        if verify_blobs:
            for content_hash, uri in self.db.execute(
                    "SELECT content_hash, storage_uri FROM content_objects").fetchall():
                path = Path(uri)
                if not path.exists():
                    issues.append(f"missing blob: {content_hash}")
                elif hashlib.sha256(path.read_bytes()).hexdigest() != content_hash:
                    issues.append(f"corrupt blob: {content_hash}")
        return issues

    def summary(self) -> dict:
        tables = ("content_objects", "experiment_versions", "security_versions",
                  "document_versions", "claim_versions", "input_manifests",
                  "manifest_items", "experiment_cases", "analysis_runs",
                  "prediction_runs", "outcome_versions", "experiment_metrics",
                  "backtest_runs")
        return {table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}

    def export_parquet(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        for table in self.summary():
            target = root / f"{table}.parquet"
            quoted_target = str(target).replace("'", "''")
            self.db.execute(
                f"COPY {table} TO '{quoted_target}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )


def code_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def case_time(case: dict) -> dt.datetime:
    return parse_time(case["cutoff"], end_of_day=True)


def horizon_time(case: dict, days: int = 90) -> dt.datetime:
    if case.get("horizon_end"):
        return parse_time(case["horizon_end"], end_of_day=True)
    return case_time(case) + dt.timedelta(days=days)


def migrate_pack(store: TemporalStore, case: dict, experiment_name: str,
                 migration_time: dt.datetime, future: bool = False) -> tuple[str, str]:
    text_key = "future_text" if future else "snapshot_text"
    text = case.get(text_key) or ""
    role = "outcome_sources" if future else "prediction_sources"
    content_hash = store.put_content(
        text, "text/plain", {"legacy_case_id": case["case_id"], "role": role}, migration_time)
    source_key = f"{experiment_name}:{case['case_id']}:{role}"
    source_rows = case.get("future_sources" if future else "snapshot_sources") or []
    effective = min((r.get("date") for r in source_rows if r.get("date")), default=case["cutoff"])
    available = horizon_time(case) if future else case_time(case)
    document_id, version_id = store.append_document(
        "legacy_evidence_pack", source_key, content_hash, effective, available,
        migration_time, issuer_id=str(case["cik"]), document_type=role,
        metadata={"sources": source_rows, "availability_precision": "date_only",
                  "needs_acceptance_time_enrichment": True, "legacy_migration": True})
    manifest_id = store.create_manifest(
        available, migration_time,
        [{"item_type": "document", "logical_id": document_id,
          "version_id": version_id, "content_hash": content_hash, "role": role}],
        {"legacy_case_id": case["case_id"], "purpose": role}, migration_time)
    return document_id, manifest_id


def migrate_dilution(store: TemporalStore, run_dir: Path,
                     migration_time: dt.datetime) -> dict:
    cases = ox.load_jsonl(run_dir / "cases.jsonl")
    if not cases:
        return {"cases": 0}
    experiment_id = store.register_experiment(
        "dilution_90d", "v1", migration_time,
        {"source_dir": str(run_dir), "legacy_experiment": cases[0].get("experiment"),
         "task": "material dilutive financing within 90 calendar days"},
        code_hash(Path(__file__).with_name("ox_lab.py")), "completed_research")
    manifests, outcome_manifests = {}, {}
    for case in cases:
        security_id, _ = store.append_security(
            str(case["cik"]), case["ticker"], migration_time,
            attributes={"company": case.get("company"), "legacy_current_universe": True})
        _, manifest_id = migrate_pack(store, case, "dilution_90d", migration_time)
        _, outcome_manifest = migrate_pack(store, case, "dilution_90d", migration_time, future=True)
        manifests[case["case_id"]] = manifest_id
        outcome_manifests[case["case_id"]] = outcome_manifest
        store.register_case(
            case["case_id"], experiment_id, security_id, case_time(case), manifest_id,
            migration_time, horizon_time(case), case.get("split"),
            {"anchor_form": case.get("anchor_form"), "anchor_accession": case.get("anchor_accession"),
             "selection_note": case.get("selection_note")})
    for row in ox.load_jsonl(run_dir / "forecasts.jsonl"):
        case = next((c for c in cases if c["case_id"] == row.get("case_id")), None)
        result = row.get("result") or {}
        if not case:
            continue
        probability = result.get("probability_pct")
        store.insert_prediction(
            experiment_id=experiment_id, case_id=case["case_id"], task="dilution_90d",
            replicate=row.get("replicate"), horizon_days=90, prediction_as_of=case_time(case),
            system_as_of=migration_time, generated_at=row.get("generated_at"),
            recorded_at=migration_time, manifest_id=manifests[case["case_id"]],
            model=row.get("model") or "stealth/ox-alpha", prompt_version="ox_lab_forecast_v1",
            schema_version="dilution_forecast_v1", code_version=code_hash(Path(__file__).with_name("ox_lab.py")),
            probability=float(probability) / 100 if isinstance(probability, (int, float)) else None,
            decision=result.get("risk_band"), output=result)
    for row in ox.load_jsonl(run_dir / "outcomes.jsonl"):
        case = next((c for c in cases if c["case_id"] == row.get("case_id")), None)
        if not case:
            continue
        store.insert_analysis(
            experiment_id=experiment_id, case_id=case["case_id"], role="outcome_labeler",
            replicate=row.get("replicate"), as_of_at=horizon_time(case), system_as_of=migration_time,
            generated_at=row.get("generated_at"), recorded_at=migration_time,
            manifest_id=outcome_manifests[case["case_id"]], model=row.get("model") or "stealth/ox-alpha",
            prompt_version="ox_lab_outcome_v1", schema_version="dilution_outcome_v1",
            code_version=code_hash(Path(__file__).with_name("ox_lab.py")), output=row.get("result") or {})
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    metric_cases = {row["case_id"]: row for row in metrics.get("cases", [])}
    for case in cases:
        scored = metric_cases.get(case["case_id"], {})
        value = {"label": scored.get("label"), "returns": case.get("returns") or {},
                 "outcome_routes": scored.get("outcome_routes")}
        store.append_outcome(
            case_id=case["case_id"], label_name="dilution_90d", value=value,
            horizon_start=case_time(case), horizon_end=horizon_time(case),
            available_at=horizon_time(case), system_from=migration_time,
            source="legacy_model_consensus_and_yahoo")
    if metrics:
        store.insert_metrics(experiment_id, "all", metrics, migration_time,
                             metrics.get("generated_at"))
    return {"experiment_id": experiment_id, "cases": len(cases)}


def migrate_long(store: TemporalStore, run_dir: Path,
                 migration_time: dt.datetime) -> dict:
    cases = ox.load_jsonl(run_dir / "cases.jsonl")
    if not cases:
        return {"cases": 0}
    experiment_id = store.register_experiment(
        "false_distress_long", "v1", migration_time,
        {"source_dir": str(run_dir), "legacy_experiment": cases[0].get("experiment"),
         "task": "+20% excess return in 90d without -25% drawdown"},
        code_hash(Path(__file__).with_name("long_lab.py")), "failed_validation")
    manifests = {}
    case_map = {case["case_id"]: case for case in cases}
    for case in cases:
        security_id, _ = store.append_security(
            str(case["cik"]), case["ticker"], migration_time,
            attributes={"company": case.get("company"), "legacy_current_universe": True})
        _, manifest_id = migrate_pack(store, case, "false_distress_long", migration_time)
        manifests[case["case_id"]] = manifest_id
        store.register_case(
            case["case_id"], experiment_id, security_id, case_time(case), manifest_id,
            migration_time, horizon_time(case), case.get("split"),
            {"market_at_cutoff": case.get("market_at_cutoff"),
             "selection_note": case.get("selection_note")})
    for row in ox.load_jsonl(run_dir / "analyses.jsonl"):
        case = case_map.get(row.get("case_id"))
        if not case:
            continue
        store.insert_analysis(
            experiment_id=experiment_id, case_id=case["case_id"], role=row.get("role") or "analyst",
            as_of_at=case_time(case), system_as_of=migration_time,
            generated_at=row.get("generated_at"), recorded_at=migration_time,
            manifest_id=manifests[case["case_id"]], model=row.get("model") or "stealth/ox-alpha",
            prompt_version=f"long_lab_{row.get('role')}_v1", schema_version=f"{row.get('role')}_v1",
            code_version=code_hash(Path(__file__).with_name("long_lab.py")), output=row.get("result") or {})
    for row in ox.load_jsonl(run_dir / "syntheses.jsonl"):
        case = case_map.get(row.get("case_id"))
        result = row.get("result") or {}
        if not case:
            continue
        probability = result.get("long_success_probability_pct")
        store.insert_prediction(
            experiment_id=experiment_id, case_id=case["case_id"], task="false_distress_long_90d",
            horizon_days=90, prediction_as_of=case_time(case), system_as_of=migration_time,
            generated_at=row.get("generated_at"), recorded_at=migration_time,
            manifest_id=manifests[case["case_id"]], model=row.get("model") or "stealth/ox-alpha",
            prompt_version="long_lab_synthesis_v1", schema_version="false_distress_synthesis_v1",
            code_version=code_hash(Path(__file__).with_name("long_lab.py")),
            probability=float(probability) / 100 if isinstance(probability, (int, float)) else None,
            decision=result.get("decision"), output=result)
    for case in cases:
        store.append_outcome(
            case_id=case["case_id"], label_name="false_distress_long_90d",
            value=case.get("outcome") or {}, horizon_start=case_time(case),
            horizon_end=horizon_time(case), available_at=horizon_time(case),
            system_from=migration_time, source="yahoo_adjusted_close")
    for split in ("all", "development", "validation"):
        suffix = "" if split == "all" else f"_{split}"
        path = run_dir / f"metrics{suffix}.json"
        if path.exists():
            metrics = json.loads(path.read_text())
            store.insert_metrics(experiment_id, split, metrics, migration_time,
                                 metrics.get("generated_at"))
    return {"experiment_id": experiment_id, "cases": len(cases)}


def migrate_all(store: TemporalStore, dilution_dir: Path, long_dir: Path,
                system_time: Any | None = None) -> dict:
    store.db.execute("BEGIN")
    try:
        proposed = parse_time(system_time or dt.datetime.now(UTC)).isoformat()
        saved = store.set_metadata_once("legacy_migration_system_time", proposed, proposed)
        migration_time = parse_time(saved)
        result = {
            "migration_time": migration_time.isoformat(),
            "dilution": migrate_dilution(store, dilution_dir, migration_time),
            "long": migrate_long(store, long_dir, migration_time),
            "summary": store.summary(),
        }
        store.db.execute("COMMIT")
        return result
    except Exception:
        store.db.execute("ROLLBACK")
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="temporal-store", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--blobs", type=Path, default=DEFAULT_BLOBS)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--dilution-dir", type=Path, default=Path("lab_runs/dilution90"))
    migrate.add_argument("--long-dir", type=Path, default=Path("lab_runs/long_dev"))
    migrate.add_argument("--system-time", default=None)
    sub.add_parser("summary")
    validate = sub.add_parser("validate")
    validate.add_argument("--skip-blobs", action="store_true")
    export = sub.add_parser("export-parquet")
    export.add_argument("--output", type=Path, default=Path("research/parquet"))
    query = sub.add_parser("documents-as-of")
    query.add_argument("--issuer", required=True)
    query.add_argument("--knowledge-at", required=True)
    query.add_argument("--system-as-of", required=True)
    args = parser.parse_args(argv)
    with TemporalStore(args.db, args.blobs) as store:
        if args.cmd == "init":
            result = store.summary()
        elif args.cmd == "migrate":
            result = migrate_all(store, args.dilution_dir, args.long_dir, args.system_time)
        elif args.cmd == "summary":
            result = store.summary()
        elif args.cmd == "validate":
            issues = store.validate_integrity(not args.skip_blobs)
            result = {"ok": not issues, "issues": issues, "summary": store.summary()}
            print(json.dumps(result, indent=2, default=str))
            return 0 if not issues else 1
        elif args.cmd == "export-parquet":
            store.export_parquet(args.output)
            result = {"output": str(args.output), "tables": store.summary()}
        else:
            result = store.documents_as_of(args.issuer, args.knowledge_at, args.system_as_of)
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
