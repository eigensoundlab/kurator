#!/usr/bin/env bash
# Kurator - install or repair on Linux / macOS.
# Creates its own virtual environment in .venv (your system Python is not touched),
# installs the newest compatible versions of all dependencies and checks them for
# known vulnerabilities. Safe to run again at any time.
#   ./install.sh        core app
#   ./install.sh ai     + optional AI features (PyTorch; CUDA on Linux with an NVIDIA GPU, Metal on Apple)
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[Kurator] Python 3.11 or newer was not found. Install it (e.g. 'sudo apt install python3 python3-venv'"
    echo "          or 'brew install python') and run this script again."
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "[Kurator] Creating virtual environment in .venv with $PY ..."
    if ! "$PY" -m venv .venv; then
        echo "[Kurator] Could not create the virtual environment."
        echo "          On Debian/Ubuntu install it with:  sudo apt install python3-venv"
        exit 1
    fi
fi
VPY=.venv/bin/python

echo "[Kurator] Updating pip ..."
"$VPY" -m pip install --upgrade --quiet pip

echo "[Kurator] Installing Kurator and the newest compatible dependencies ..."
EXTRAS="audit"
if [ "${1:-}" = "ai" ] || "$VPY" -c "import open_clip" 2>/dev/null; then EXTRAS="audit,ai"; fi
"$VPY" -m pip install --upgrade --upgrade-strategy eager --quiet ".[$EXTRAS]"
if [ "$EXTRAS" = "audit,ai" ]; then
    "$VPY" -c "import torch; print('[Kurator] AI device:', 'CUDA GPU' if torch.cuda.is_available() else ('Apple GPU' if torch.backends.mps.is_available() else 'CPU'))" || true
fi

echo "[Kurator] Checking installed packages for known vulnerabilities (pip-audit) ..."
if ! "$VPY" -m pip_audit --progress-spinner off; then
    echo "[Kurator] WARNING: pip-audit reported a problem (see above). Run ./update.sh later to get fixed versions."
fi

chmod +x kurator.sh update.sh install.sh 2>/dev/null || true
echo
echo "[Kurator] Ready. Start it with:  ./kurator.sh"
echo "          or with a folder:       ./kurator.sh --library ~/Pictures/Art"
