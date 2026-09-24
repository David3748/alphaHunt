#!/usr/bin/env bash
set -u

root=/opt/alphahunt
run_dir="$root/lab_runs/century_safety"
log="$run_dir/century_supervisor.log"

cd "$root" || exit 1
while [[ ! -s "$run_dir/results.json" ]]; do
  if pgrep -f '[c]entury_pipeline.py.* play' >/dev/null; then
    sleep 60
    continue
  fi
  echo "$(date -u +%FT%TZ) resuming century pipeline from extract" >>"$log"
  "$root/.venv/bin/python" src/century_pipeline.py --config config/century.json \
    play --from-stage extract >>lab_runs/century_pipeline.log 2>&1
  status=$?
  echo "$(date -u +%FT%TZ) pipeline exit=$status; retrying after 60s if incomplete" >>"$log"
  sleep 60
done
echo "$(date -u +%FT%TZ) official results present; supervisor complete" >>"$log"
