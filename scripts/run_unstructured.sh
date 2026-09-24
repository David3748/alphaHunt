#!/usr/bin/env bash
# run_unstructured.sh — VM-side launcher for the unstructured proto pipeline
# Key resolution order: env var -> /opt/alphahunt/.openrouter_key (chmod 600)
set -euo pipefail

cd /opt/alphahunt
source .venv/bin/activate

if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f /opt/alphahunt/.openrouter_key ]; then
  export OPENROUTER_API_KEY="$(cat /opt/alphahunt/.openrouter_key)"
fi

echo "=== unstructured_proto START $(date -u) ==="
mkdir -p lab_runs/unstructured_proto

echo "--- PULL ---"
python3 src/unstructured_proto.py pull --config config/unstructured_proto.json

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "!!! NO API KEY — pull finished, extraction blocked."
  echo "!!! Fix: ssh VM 'printf %s \"<key>\" > /opt/alphahunt/.openrouter_key && chmod 600 /opt/alphahunt/.openrouter_key'"
  echo "!!! then rerun this script (pulls are cached and will be skipped)."
  exit 0
fi

echo "--- EXTRACT ---"
python3 src/unstructured_proto.py extract --config config/unstructured_proto.json

echo "--- EXPORT ---"
python3 src/unstructured_proto.py export --config config/unstructured_proto.json 2>/dev/null || echo "Export skipped (rclone)"

echo "=== unstructured_proto DONE $(date -u) ==="