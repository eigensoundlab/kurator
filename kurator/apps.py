"""Open with: programs a file can be opened in besides its default one.

Kurator looks for well-known art, 3D and sound programs where their installers put them, and you can
add any other program in Settings. A request names an app by its id from this list - never a path -
so only programs found here or added in Settings can be started.
"""

from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import sys

from .db import Database

KINDS = ("image", "audio", "video", "model")
MAX_FILES = 50


class AppError(Exception):
    pass


# id, name, kinds, extensions (None: every file of those kinds), where installers put them:
# Windows (under Program Files and the per-user Programs folder), macOS app bundles, Linux commands
KNOWN = [
    ("krita", "Krita", ("image",), None,
     ["Krita (x64)/bin/krita.exe", "Krita/bin/krita.exe"], ["Krita.app"], ["krita"]),
    ("photoshop", "Photoshop", ("image",), None,
     ["Adobe/Adobe Photoshop */Photoshop.exe"], ["Adobe Photoshop */Adobe Photoshop *.app"], []),
    ("gimp", "GIMP", ("image",), None,
     ["GIMP 3/bin/gimp-3*.exe", "GIMP 2/bin/gimp-2.*.exe"], ["GIMP*.app"], ["gimp"]),
    ("affinity", "Affinity", ("image",), None,
     ["Affinity/Affinity/Affinity.exe", "Affinity/Photo 2/Photo.exe", "Affinity/Designer 2/Designer.exe"],
     ["Affinity.app", "Affinity Photo 2.app", "Affinity Designer 2.app"], []),
    ("pixelmator", "Pixelmator Pro", ("image",), None, [], ["Pixelmator Pro.app"], []),
    ("aseprite", "Aseprite", ("image",), None,
     ["Aseprite/Aseprite.exe", "Steam/steamapps/common/Aseprite/Aseprite.exe"], ["Aseprite.app"], ["aseprite"]),
    ("paintnet", "Paint.NET", ("image",), None, ["paint.net/paintdotnet.exe"], [], []),
    ("inkscape", "Inkscape", ("image",), (".svg", ".svgz", ".ai", ".eps", ".pdf", ".cdr", ".png"),
     ["Inkscape/bin/inkscape.exe"], ["Inkscape.app"], ["inkscape"]),
    ("blender", "Blender", ("model",), (".blend", ".blend1", ".blend2"),
     ["Blender Foundation/Blender */blender.exe"], ["Blender.app"], ["blender"]),
    ("audacity", "Audacity", ("audio",), None, ["Audacity/Audacity.exe"], ["Audacity.app"], ["audacity"]),
    ("reaper", "REAPER", ("audio",), None, ["REAPER (x64)/reaper.exe", "REAPER/reaper.exe"], ["REAPER.app"], ["reaper"]),
    ("vlc", "VLC", ("audio", "video"), None, ["VideoLAN/VLC/vlc.exe"], ["VLC.app"], ["vlc"]),
]


# several files in one piece: sounds as tracks of a project, pictures as layers of one picture
LAYOUTS = {"reaper": "tracks", "krita": "layers", "gimp": "layers", "mypaint": "layers"}


def layout_of(app_id: str, path: str) -> str | None:
    if app_id in LAYOUTS:
        return LAYOUTS[app_id]
    base = os.path.basename(path).lower()
    return next((v for k, v in LAYOUTS.items() if k in base), None)


def _program_dirs() -> list[str]:
    dirs = [os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Programs"))
    out: list[str] = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _newest(paths: list[str]) -> str | None:
    """Of several installed versions (Blender 4.2, Blender 4.5...) the newest one."""
    def key(p: str):
        return [int(t) if t.isdigit() else t.lower() for t in _split_digits(p)]
    return max(paths, key=key) if paths else None


def _split_digits(s: str) -> list[str]:
    out, cur = [], ""
    for ch in s:
        if cur and ch.isdigit() != cur[-1].isdigit():
            out.append(cur)
            cur = ""
        cur += ch
    return out + ([cur] if cur else [])


def _find(win: list[str], mac: list[str], cmds: list[str]) -> str | None:
    if os.name == "nt":
        for base in _program_dirs():
            for pat in win:
                hit = _newest(glob.glob(os.path.join(base, pat)))
                if hit:
                    return hit
        return None
    if sys.platform == "darwin":
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            for pat in mac:
                hit = _newest(glob.glob(os.path.join(base, pat)))
                if hit:
                    return hit
        return None
    for c in cmds:
        hit = shutil.which(c)
        if hit:
            return hit
    return None


def _custom_id(path: str) -> str:
    return "c-" + hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:10]


def settings(db: Database) -> dict:
    s = db.get_setting("open_with", {}) or {}
    return {"custom": list(s.get("custom") or []), "hidden": list(s.get("hidden") or [])}


def list_apps(db: Database) -> list[dict]:
    """Found and added programs: [{id, name, kinds, exts, path, custom, hidden}]."""
    s = settings(db)
    hidden = set(s["hidden"])
    out = []
    for app_id, name, kinds, exts, win, mac, cmds in KNOWN:
        path = _find(win, mac, cmds)
        if path:
            out.append({"id": app_id, "name": name, "kinds": list(kinds), "exts": list(exts) if exts else None,
                        "path": path, "custom": False, "hidden": app_id in hidden, "layout": layout_of(app_id, path)})
    for c in s["custom"]:
        cid = _custom_id(c["path"])
        out.append({"id": cid, "name": c["name"], "kinds": list(c.get("kinds") or KINDS), "exts": None,
                    "path": c["path"], "custom": True, "hidden": False, "missing": not os.path.exists(c["path"]),
                    "layout": layout_of(cid, c["path"])})
    return out


def save(db: Database, custom: list[dict], hidden: list[str]) -> list[dict]:
    clean = []
    for c in custom[:40]:
        name = str(c.get("name") or "").strip()[:40]
        path = os.path.normpath(os.path.expanduser(str(c.get("path") or "").strip().strip('"')))
        if not str(c.get("path") or "").strip():
            continue
        if not os.path.isabs(path):
            raise AppError(f"{path}: give the full path of the program")
        if not os.path.exists(path):
            raise AppError(f"{path}: no such program")
        if os.path.isdir(path) and not path.lower().endswith(".app"):
            raise AppError(f"{path} is a folder: give the program itself")
        kinds = [k for k in (c.get("kinds") or []) if k in KINDS] or list(KINDS)
        clean.append({"name": name or os.path.splitext(os.path.basename(path))[0], "path": path, "kinds": kinds})
    known = {k[0] for k in KNOWN}
    db.set_setting("open_with", {"custom": clean, "hidden": sorted({h for h in hidden if h in known})})
    return list_apps(db)


def command(path: str, files: list[str]) -> list[str]:
    if sys.platform == "darwin" and path.lower().rstrip("/").endswith(".app"):
        return ["open", "-a", path, *files]
    return [path, *files]


def open_with(db: Database, app_id: str, files: list[str], layout: str | None = None,
              durations: list[float | None] | None = None) -> str:
    """Start the app with the files - or, with layout, with one project of them (tracks: a REAPER
    project; layers: an OpenRaster picture). Returns the app's name."""
    app = next((a for a in list_apps(db) if a["id"] == app_id), None)
    if app is None:
        raise AppError("That program is no longer installed - see Settings → Open with")
    if not os.path.exists(app["path"]):
        raise AppError(f"{app['name']}: {app['path']} is missing")
    if layout:
        from . import handoff
        if layout != app.get("layout"):
            raise AppError(f"{app['name']} can't open files as {layout}")
        out = handoff.folder(db.data_dir)
        try:
            if layout == "tracks":
                files = [handoff.reaper_project(list(zip(files[:200], durations or [None] * len(files))), out)]
            else:
                files = [handoff.openraster(files[:64], out)]
        except (OSError, ValueError) as e:
            raise AppError(f"Could not put the files together: {e}") from None
    files = files[:MAX_FILES]
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kw["cwd"] = os.path.dirname(app["path"])
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(command(app["path"], files), **kw)  # noqa: S603 - a program from the list above
    except OSError as e:
        raise AppError(f"{app['name']} did not start: {e.strerror or e}") from None
    return app["name"]
