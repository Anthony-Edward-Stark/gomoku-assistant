#!/bin/zsh
set -euo pipefail
task_dir="${0:A:h}"
task_env="$task_dir/.venv"
task_python="${ASSISTANT_PYTHON:-python3}"
if [[ ! -x "$task_env/bin/python" ]] || ! "$task_env/bin/python" -c 'import cv2, numpy' 2>/dev/null; then
  if ! "$task_python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    print -u2 '请安装 Python 3.12，或用 ASSISTANT_PYTHON 指定可用的解释器。'
    exit 1
  fi
  "$task_python" -m venv "$task_env"
  "$task_env/bin/python" -m pip install -r "$task_dir/requirements.txt"
fi
mkdir -p "$task_dir/bin"
if [[ ! -x "$task_dir/bin/phone-capture" || "$task_dir/capture.swift" -nt "$task_dir/bin/phone-capture" ]]; then
  /usr/bin/swiftc -O -target "$(/usr/bin/uname -m)-apple-macosx14.0" "$task_dir/capture.swift" -o "$task_dir/bin/phone-capture"
fi
exec "$task_env/bin/python" "$task_dir/app.py" "$@"
