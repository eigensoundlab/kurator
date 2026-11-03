"""Blender, when it is installed: turn .blend files into glTF for the 3D viewer.

Blender runs in the background with its factory settings and with Python auto-run switched off
(--disable-autoexec), so scripts or drivers stored in a .blend file never run. The result is cached
by content hash, so each file is converted once.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import threading

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# Runs inside Blender. Export options differ between versions: try the full set, then the basics.
EXPORT = r"""
import sys, bpy
out = sys.argv[sys.argv.index("--") + 1]
full = dict(filepath=out, export_format="GLB", export_apply=True, use_visible=True, export_animations=False,
            export_cameras=False, export_lights=False, export_extras=False, export_draco_mesh_compression_enable=False)
try:
    bpy.ops.export_scene.gltf(**full)
except TypeError:
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
"""


class BlenderError(RuntimeError):
    pass


def find() -> str | None:
    """Blender's executable: KURATOR_BLENDER, the PATH, or the usual install folders."""
    env = os.environ.get("KURATOR_BLENDER")
    if env == "off":
        return None
    if env and os.path.isfile(env):
        return env
    exe = shutil.which("blender")
    if exe:
        return exe
    cands: list[str] = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
            cands += glob.glob(os.path.join(base, "Steam", "steamapps", "common", "Blender", "blender.exe"))
    elif os.uname().sysname == "Darwin":
        cands += ["/Applications/Blender.app/Contents/MacOS/Blender"]
    else:
        cands += ["/snap/bin/blender", "/usr/local/bin/blender", "/opt/blender/blender"]
    cands = [c for c in cands if os.path.isfile(c)]

    def version(p: str) -> tuple:
        m = re.search(r"(\d+)\.(\d+)", p)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    return max(cands, key=version) if cands else None


def to_glb(src: str, h: str, cache_dir: str, timeout: float = 300) -> str:
    """Convert src (.blend) to a cached .glb named after its content hash; returns the .glb path."""
    exe = find()
    if not exe:
        raise BlenderError("Install Blender to view .blend files in 3D (or set KURATOR_BLENDER to blender.exe)")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{h}.glb")
    with _locks_guard:
        lock = _locks.setdefault(h, threading.Lock())
    with lock:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        tmp = out + ".part.glb"
        script = os.path.join(cache_dir, "_kurator_export.py")   # a file: multi-line arguments are fragile on Windows
        with open(script, "w", encoding="utf-8") as f:
            f.write(EXPORT)
        args = [exe, "--background", "--factory-startup", "--disable-autoexec", src, "--python", script, "--", tmp]
        try:
            r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               timeout=timeout, creationflags=_NO_WINDOW, check=False)
        except subprocess.TimeoutExpired:
            raise BlenderError("Blender took too long to convert this file") from None
        except OSError as e:
            raise BlenderError(f"Could not start Blender: {e}") from None
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            raise BlenderError("Blender could not export this file" + (": " + " / ".join(tail) if tail else ""))
        os.replace(tmp, out)
        return out
