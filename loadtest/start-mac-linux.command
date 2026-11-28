#!/bin/bash
# Double-click on macOS (or run: bash start-mac-linux.command): sets up the load test (first time only)
# and opens its page in your browser.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Python 3.10 or newer is needed: https://www.python.org/downloads/"; read -r -p "Press Enter"; exit 1; fi
if [ ! -x venv/bin/python ]; then
  echo "First run: installing the load-test libraries (about 1 minute)..."
  "$PY" -m venv venv && venv/bin/python -m pip install --quiet --upgrade pip \
    && venv/bin/python -m pip install --quiet -r requirements.txt || { read -r -p "Install failed. Press Enter"; exit 1; }
fi
# Many simulated users need many open connections.
ulimit -n 10240 2>/dev/null || true
exec venv/bin/python ui.py "$@"
