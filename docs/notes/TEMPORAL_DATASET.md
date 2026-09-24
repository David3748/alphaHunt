# alphaHunt temporal research dataset

This store preserves what was knowable at prediction time and what the database
itself knew at any later audit time. It is intended for reproducible research,
not live trading.

## Artifacts

- DuckDB catalog: `research/alphahunt.duckdb`
- Immutable SHA-256 content: `research/blobs/`
- Portable table snapshots: `research/parquet/`
- Builder and query API: `src/temporal_store.py`
- High-volume extraction/forecast runner: `src/comprehensive_lab.py`
- SEC acceptance-time enrichment: `src/sec_enrich.py`
- Raw SEC filing corpus ingester: `src/sec_corpus.py`
- Handoff and remaining work: `PROGRESS.md`

The original experiment JSONL files remain unchanged under `lab_runs/`.

## Time model

- `effective_from` / `effective_to`: when a fact applies economically.
- `available_at`: earliest time that fact could have been known by a market participant.
- `system_from` / derived `system_to`: when a particular version existed in this store.
- `prediction_as_of`: the information cutoff for a forecast.
- `recorded_at`: when an immutable analysis or prediction row entered the store.

`document_history`, `claim_history`, `security_history`, and `outcome_history`
derive `system_to` with `LEAD(system_from)`. Corrections append a new version; they
do not overwrite the previous version.

Each case points to a frozen `input_manifest`. Each manifest lists exact document
or claim version IDs and content hashes. Creation fails if an item was unavailable
at `knowledge_at` or invisible at `system_as_of`. A final prediction must use the
case's prediction-time manifest. Outcome labelers can use a distinct manifest whose
knowledge cutoff is the end of the outcome horizon.

## Build and verify

Run from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs migrate
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs validate
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs export-parquet --output research/parquet
```

Migration is transactional and idempotent. It reuses the first migration's stable
system timestamp from `store_metadata`.

## Point-in-time access

CLI example:

```bash
python3 src/temporal_store.py \
  --db research/alphahunt.duckdb \
  --blobs research/blobs \
  documents-as-of \
  --issuer 1065280 \
  --knowledge-at 2024-01-26T23:59:59.999999+00:00 \
  --system-as-of 2026-08-22T19:31:42.744331+00:00
```

Python example:

```python
from pathlib import Path
from temporal_store import TemporalStore

with TemporalStore(
    Path("research/alphahunt.duckdb"),
    Path("research/blobs"),
    read_only=True,
) as store:
    documents = store.documents_as_of(
        issuer_id="1065280",
        knowledge_at="2024-01-26T23:59:59.999999+00:00",
        system_as_of="2026-08-22T19:31:42.744331+00:00",
    )
```

## Backtest snapshots

`backtest_inputs(experiment_id, system_as_of, evaluation_at, split)` returns only:

- predictions recorded by the selected database system time;
- the outcome version visible at that system time; and
- outcomes whose `available_at` is no later than the evaluation cutoff.

`record_backtest(...)` stores the knowledge policy, universe version, execution
assumptions, metrics, and a deterministic hash of the exact prediction/outcome
version pairs used. It does not invent portfolio assumptions: callers must state
entry timing, costs, sizing, overlap, and benchmark policy explicitly.

```python
rows = store.backtest_inputs(
    experiment_id,
    system_as_of="2026-08-22T19:31:42.744331+00:00",
    evaluation_at="2026-08-22T19:31:42.744331+00:00",
    split="validation",
)

backtest_id = store.record_backtest(
    experiment_id=experiment_id,
    system_as_of="2026-08-22T19:31:42.744331+00:00",
    evaluation_at="2026-08-22T19:31:42.744331+00:00",
    knowledge_policy="strict_available_at",
    execution={"entry": "next_session_open", "cost_bps": 20},
    metrics={"n": len(rows)},
    created_at="2026-08-22T19:31:42.744331+00:00",
    split="validation",
    universe_version="replace-with-security-master-version",
)
```

## Current limitations

- Exact SEC acceptance timestamps were matched for all 1,490 referenced primary
  filings and 329 non-empty aggregate evidence packs. Eleven empty future packs
  have no source accession and intentionally remain without an exact timestamp.
- The corpus stores 994 MB of original filing HTML and 139 MB of derived clean text.
  The derivation hash is recorded in each primary filing's metadata.
- The migrated universe was assembled from current listings and therefore has
  survivorship bias. Add historical listings and delisted securities before making
  investment claims.
- Corporate actions, historical identifier mappings, exchange calendars, borrow,
  trading costs, and next-session execution rules are not yet first-class datasets.
- `claim_versions` contains 14,037 exact-quote-grounded observations from five
  specialist lenses and two independent replicates. Another 6,392 proposed claims
  failed exact grounding and remain auditable in analysis output but are excluded
  from prediction manifests.
- The false-distress long model failed sealed validation. Its rows are retained as
  negative research evidence, not as a deployable signal.
- The comprehensive long model also failed the sealed false-distress validation
  subset. See `lab_runs/comprehensive_long/report.md`.
