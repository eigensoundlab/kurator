#!/usr/bin/env bash
# Start Kurator. Any options are passed on, e.g.:  ./kurator.sh --library ~/Art --port 8766
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "[Kurator] Not installed yet - running install.sh first ..."
    bash ./install.sh
fi
exec .venv/bin/python -m kurator "$@"
