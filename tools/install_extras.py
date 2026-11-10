"""Install Kurator's optional packages one at a time, ready-made builds only, and say plainly what is missing.

The core app is installed before this runs. Some packages are compiled programs that their authors build for
each Python version and system; right after a new Python comes out (or on an older macOS) there may be no
build yet, and pip would then try to compile it and fail with a long error. Here each package is tried on its
own with --only-binary, so one missing package never stops the install, and the summary says what it does,
why it is missing and how to get it.

    python tools/install_extras.py formats [3d] [ai]
Exit code 0 even when something is missing (Kurator works without these); 2 for bad arguments.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:     # Python 3.10: Kurator needs 3.11, the installers check that first
    tomllib = None

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWEST_TESTED = (3, 14)          # the newest Python every package has ready-made builds for

WHAT = {
    "pi-heif": "HEIC / HEIF pictures (iPhone photos)",
    "pypdfium2": "PDF and Illustrator previews",
    "dracopy": "Draco-compressed glTF",
    "usd-core": "binary USD files",
    "texture2ddecoder": "compressed textures in glTF / USD",
    "torch": "AI features (PyTorch)",
    "torchvision": "AI features (PyTorch vision)",
    "open-clip-torch": "AI image search",
    "transformers": "AI sound search",
}
ON_MAC_INSTEAD = {
    "pi-heif": "macOS decodes them itself, so Kurator uses that instead.",
    "pypdfium2": "Kurator asks macOS (Quick Look) for the previews instead - fine for the grid, without page navigation.",
}


def name_of(req: str) -> str:
    return re.match(r"[A-Za-z0-9._-]+", req).group(0).lower().replace("_", "-")


def extras(groups: list[str]) -> list[tuple[str, str]]:
    with open(os.path.join(HERE, "pyproject.toml"), "rb") as f:
        opt = tomllib.load(f)["project"]["optional-dependencies"]
    out = []
    for g in groups:
        if g not in opt:
            raise SystemExit(f"unknown group {g!r} (have: {', '.join(opt)})")
        out += [(g, r) for r in opt[g] if not r.startswith("kurator[")]
    return out


def mac_version() -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return ()


def why(name: str, py: tuple[int, int] | None = None, mac: tuple[int, ...] | None = None) -> tuple[str, str]:
    """(why it is missing, how to get it) - for this Python and Mac unless given"""
    py = py or sys.version_info[:2]
    newest = ".".join(map(str, NEWEST_TESTED))
    if mac is None:
        mac = mac_version() if sys.platform == "darwin" else ()
    if name == "pypdfium2" and mac and mac < (13,):    # one build for every Python: the macOS version decides
        return (f"pypdfium2 is built for macOS 13 or newer (this Mac has {'.'.join(map(str, mac))})", "")
    if py > NEWEST_TESTED:
        again = "install.bat fresh" if sys.platform == "win32" else "./install.sh fresh"
        return (f"there is no ready-made {name} for Python {py[0]}.{py[1]} yet",
                f"install Python {newest} too (https://www.python.org/downloads/), then run  {again}"
                "  - it prefers that version")
    system = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, platform.system())
    return (f"there is no ready-made {name} for Python {py[0]}.{py[1]} on {system} {platform.machine()}",
            "run the installer again later - new builds come out regularly")


def main(argv: list[str]) -> int:
    groups = [a for a in argv if a not in ("--quiet",)]
    if not groups:
        print(__doc__)
        return 2
    if tomllib is None:
        print("[Kurator] Python 3.11 or newer is needed.")
        return 2
    missing = []
    for group, req in extras(groups):
        name = name_of(req)
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--upgrade-strategy", "eager", "--quiet",
               "--only-binary=:all:", "--disable-pip-version-check", req]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            continue
        log = (r.stderr or r.stdout or "").strip().splitlines()
        no_build = any("No matching distribution" in ln or "from versions: none" in ln for ln in log)
        reason, fix = why(name) if no_build else (log[-1][:200] if log else "pip failed", "run the installer again later")
        missing.append((group, name, reason, fix))
    if not missing:
        return 0
    print("[Kurator] Installed without these (Kurator works without them):")
    for group, name, reason, fix in missing:
        print(f"          - {WHAT.get(name, name)}: {reason}.")
        if sys.platform == "darwin" and name in ON_MAC_INSTEAD:
            print(f"            {ON_MAC_INSTEAD[name]}")
        if fix:
            print(f"            To get it: {fix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
