#!/usr/bin/env bash
set -euo pipefail

root=/opt/alphahunt
run_dir="$root/lab_runs/century_safety"
source_dir="$root/lab_runs/century_safety_source"
log="$run_dir/century_hypotheses.log"

cd "$root"
echo "$(date -u +%FT%TZ) waiting for official century evaluation" >>"$log"
while [[ ! -s "$run_dir/results.json" || ! -s "$run_dir/outcomes.jsonl" ]]; do
  sleep 60
done

echo "$(date -u +%FT%TZ) starting locked secondary hypotheses" >>"$log"
"$root/.venv/bin/python" "$root/src/century_hypotheses.py" \
  --run-dir "$run_dir" --source-dir "$source_dir" --placebo-draws 500 \
  >>"$log" 2>&1

rclone copyto "$run_dir/century_hypotheses.json" \
  "gdrive:alphaHunt/century_safety/century_hypotheses.json" >>"$log" 2>&1
rclone copyto "$run_dir/century_hypotheses.md" \
  "gdrive:alphaHunt/century_safety/century_hypotheses.md" >>"$log" 2>&1
echo "$(date -u +%FT%TZ) hypothesis evaluation complete" >>"$log"
