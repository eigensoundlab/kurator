#!/usr/bin/env bash
# Kurator - install or repair on Linux / macOS.
# Creates its own virtual environment in .venv (your system Python is not touched),
# installs the newest compatible versions of all dependencies and checks them for
# known vulnerabilities. Safe to run again at any time.
#   ./install.sh        core app
#   ./install.sh ai     + optional AI features (PyTorch; CUDA on Linux with an NVIDIA GPU, Metal on Apple)
#   ./install.sh 3d     + optional 3D add-on: Draco-compressed glTF, binary USD (DracoPy, usd-core)
#   ./install.sh ai 3d  both
#   ./install.sh fresh  start over: a new .venv with the best Python found (e.g. after installing another Python)
# HEIC and PDF support (and the add-ons) are ready-made packages added one by one: if one has no build for
# this Python or system yet, Kurator is installed without it and the end says what is missing and why.
set -euo pipefail
cd "$(dirname "$0")"

FRESH=""
for a in "$@"; do
    if [ "$a" = "fresh" ]; then FRESH=1; fi
done

# The newest Python that every package has ready-made builds for comes first; a brand-new one last.
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

if [ -n "$FRESH" ] && [ -d .venv ]; then
    echo "[Kurator] Starting over: removing .venv (your settings and catalog are kept elsewhere) ..."
    rm -rf .venv
fi
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 14) else 1)' 2>/dev/null \
   && "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 14) else 1)' 2>/dev/null; then
    echo "[Kurator] Note: .venv uses a Python newer than some packages are built for; $("$PY" --version) is here too."
    echo "          Run  ./install.sh fresh  to rebuild .venv with it."
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
PARTS="formats"
WANT_AI=""; WANT_3D=""
for a in "$@"; do
    if [ "$a" = "ai" ]; then WANT_AI=1; fi
    if [ "$a" = "3d" ]; then WANT_3D=1; fi
done
if "$VPY" -c "import open_clip" 2>/dev/null; then WANT_AI=1; fi
if "$VPY" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pxr') else 1)" 2>/dev/null; then WANT_3D=1; fi
if [ -n "$WANT_3D" ]; then PARTS="$PARTS 3d"; fi
if [ -n "$WANT_AI" ]; then PARTS="$PARTS ai"; fi
"$VPY" -m pip install --upgrade --upgrade-strategy eager --quiet ".[audit]"
echo "[Kurator] Adding HEIC and PDF support${WANT_3D:+, the 3D add-on}${WANT_AI:+, the AI features} ..."
# shellcheck disable=SC2086
"$VPY" tools/install_extras.py $PARTS
if [ -n "$WANT_3D" ] && "$VPY" -c "import pxr" 2>/dev/null; then echo "[Kurator] 3D add-on installed: Draco glTF and binary USD can be read."; fi
if [ -n "$WANT_AI" ] && "$VPY" -c "import torch" 2>/dev/null; then
    "$VPY" -c "import torch; print('[Kurator] AI device:', 'CUDA GPU' if torch.cuda.is_available() else ('Apple GPU' if torch.backends.mps.is_available() else 'CPU'))" || true
fi

echo "[Kurator] Checking installed packages for known vulnerabilities (pip-audit) ..."
if ! "$VPY" tools/audit_deps.py; then
    echo "[Kurator] WARNING: pip-audit reported a problem (see above). Run ./update.sh later to get fixed versions."
fi

chmod +x kurator.sh update.sh install.sh 2>/dev/null || true
echo
echo "[Kurator] Ready. Start it with:  ./kurator.sh"
echo "          or with a folder:       ./kurator.sh --library ~/Pictures/Art"
