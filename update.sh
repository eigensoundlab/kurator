#!/usr/bin/env bash
# Kurator - update on Linux / macOS: get the newest code (if this is a git clone),
# upgrade all dependencies to their newest compatible versions, and re-check security.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d .git ]; then
    if command -v git >/dev/null 2>&1; then
        echo "[Kurator] Getting the newest version from GitHub ..."
        git pull --ff-only || echo "[Kurator] git pull failed (local changes?) - continuing with the dependency update."
    else
        echo "[Kurator] git not found - skipping code update."
    fi
else
    echo "[Kurator] Not a git clone - to update the code, download the newest release from"
    echo "          https://github.com/eigensoundlab/kurator and unzip it over this folder."
fi

exec bash ./install.sh "$@"
