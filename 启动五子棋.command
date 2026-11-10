#!/bin/zsh
set -euo pipefail
task_dir="${0:A:h}"
if /usr/bin/curl -fsS http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
  /usr/bin/open http://127.0.0.1:8765
  exit 0
fi
"$task_dir/run.sh" "$@" &
task_pid=$!
trap 'kill -TERM "$task_pid" 2>/dev/null || true; wait "$task_pid" 2>/dev/null || true' EXIT INT TERM
for task_attempt in {1..60}; do
  if /usr/bin/curl -fsS http://127.0.0.1:8765/api/state >/dev/null; then
    /usr/bin/open http://127.0.0.1:8765
    break
  fi
  sleep 1
done
wait "$task_pid"
