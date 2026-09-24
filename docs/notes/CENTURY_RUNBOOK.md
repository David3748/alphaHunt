# One-day century dataset runbook

## Scope

The one-button run applies the already-validated safety pipeline to every detailed
SEC 10-K, 10-Q, 20-F, and 40-F in the structured filing feed from 2009 through
2025. The requested 2001–2008 period requires a separate pre-XBRL historical
identifier pass and is deliberately not represented as complete.

Each eligible case receives five reusable, quote-grounded extraction lenses and
two independent safety syntheses. All steps append checkpoints and are safe to
resume. Post-signal prices are not opened until every synthesis is present.

## Local play

```sh
chmod +x scripts/play-century
scripts/play-century
```

The launcher reads the API key without echoing it. It never writes the key to a
file. To stop and resume, run the same command again. Inspect progress with:

```sh
python3 src/century_pipeline.py status
```

## Google Drive archival

Install `rclone`, run `rclone config`, and create a Drive remote. Then set
`drive_remote` in `config/century.json`, for example
`gdrive:alphaHunt/century_safety`. The archive stage compresses durable JSONL,
results, and manifests with Zstandard, records SHA-256 checksums, and uploads
with checksum verification. Reconstructible HTTP caches and `node_modules` are
not archived.

## Azure for Students

Use an Ubuntu VM with 16–32 vCPUs, at least 32 GB RAM, and 200 GB temporary SSD.
Clone or upload the project, install Docker, configure the Drive remote, then:

```sh
docker build -f cloud/Dockerfile.century -t alphahunt-century .
docker run --rm -it \
  -e OPENROUTER_API_KEY \
  -v "$PWD/lab_runs:/app/lab_runs" \
  -v "$PWD/archives:/app/archives" \
  -v "$HOME/.config/rclone:/root/.config/rclone:ro" \
  alphahunt-century
```

Prefer a standard VM for the first run. Spot eviction is safe because stages are
resumable, but it can jeopardize the one-day deadline. Set an Azure budget alert
before launch and delete the VM after the archive manifest is verified.

## Expected critical path

- SEC enumeration and historical-symbol resolution: 2–5 hours.
- Pre-signal price screening and filing packs: 4–10 hours, overlapping cached work.
- Ox extraction and synthesis: about 7 calls per eligible case; observed throughput
  makes 60,000–100,000 calls an 8–14 hour stage if the provider remains stable.
- Outcome evaluation, compression, and upload: 1–3 hours. The default uses 250
  random-score placebos; rerun with a larger count only for a final deep audit.

Provider quotas, SEC/Yahoo throttling, and pre-2009 identifier coverage prevent a
hard 24-hour guarantee. The state file records every completed stage and failure.
