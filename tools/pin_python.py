"""Pin the Python the desktop app installs Kurator with: python-build-standalone builds (the ones uv uses)
of one Python version, with their SHA-256 checksums, written to desktop/src-tauri/python-runtime.json.

    python tools/pin_python.py RELEASE [MINOR]        e.g.  python tools/pin_python.py 20261026 3.14

RELEASE is a release of https://github.com/astral-sh/python-build-standalone/releases (a date). The app
checks every download against these checksums, so a new pin only comes with a new app version.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

TARGETS = ["x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc", "x86_64-apple-darwin", "aarch64-apple-darwin",
           "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"]
BASE = "https://github.com/astral-sh/python-build-standalone/releases/download"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop", "src-tauri", "python-runtime.json")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    release, minor = argv[0], (argv[1] if len(argv) > 1 else "3.14")
    with urllib.request.urlopen(f"{BASE}/{release}/SHA256SUMS", timeout=60) as r:
        sums = r.read().decode()
    assets, version = {}, None
    for target in TARGETS:
        pat = re.compile(rf"^([0-9a-f]{{64}})\s+(cpython-({re.escape(minor)}\.\d+)\+{release}-{target}-install_only_stripped\.tar\.gz)$", re.M)
        m = pat.search(sums)
        if not m:
            print(f"no {minor} build for {target} in {release}")
            return 1
        version = version or m.group(3)
        assets[target] = {"url": f"{BASE}/{release}/{m.group(2).replace('+', '%2B')}", "sha256": m.group(1)}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"version": version, "release": release, "assets": assets}, f, indent=2)
        f.write("\n")
    print(f"pinned Python {version} ({release}) in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
