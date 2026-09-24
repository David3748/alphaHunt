#!/usr/bin/env bash
set -u
root=/opt/alphahunt
run="$root/lab_runs/obscure_miner"
mkdir -p "$run"
echo "$(date -u +%FT%TZ) queued behind century results" >>"$run/miner.log"
while [[ ! -s "$root/lab_runs/century_safety/results.json" ]]; do sleep 60; done
echo "$(date -u +%FT%TZ) starting source miner" >>"$run/miner.log"
cd "$root" || exit 1
while true; do
  "$root/.venv/bin/python" src/obscure_miner.py play --config config/obscure_miner.json \
    >>"$run/miner.log" 2>&1 && break
  echo "$(date -u +%FT%TZ) miner failed; retrying cached work in 60s" >>"$run/miner.log"
  sleep 60
done
zstd -q -T0 -6 -f "$run/discoveries.jsonl" -o "$run/discoveries.jsonl.zst"
zstd -q -T0 -6 -f "$run/documents.jsonl" -o "$run/documents.jsonl.zst"
zstd -q -T0 -6 -f "$run/analyses.jsonl" -o "$run/analyses.jsonl.zst"
rclone copy "$run" "gdrive:alphaHunt/obscure_miner" --include '*.zst' --include '*.log'
echo "$(date -u +%FT%TZ) miner complete" >>"$run/miner.log"
