"""File system operations: safe moves (with undo), folder listing, reveal in file manager.

Moving rules
  * never overwrite: name clashes get " (2)", " (3)" ...
  * same disk  -> rename (instant; every timestamp, incl. Windows creation time, is kept)
  * other disk -> copy, restore modified + creation time, verify the copy byte-for-byte (hash),
                  only then delete the source
  * sidecars travel along: photo.jpg.xmp / photo.xmp, and engine files: Unity .meta, Godot .import
  * catalog rows are updated in place, so tags and dates stay attached
  * every move is logged and can be undone
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

from . import dates
from .db import Database
from .handlers import handler_for

SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", "__pycache__", "node_modules"}


class OpError(ValueError):
    pass


# ----------------------------------------------------------------- timestamps
def set_creation_time(path: str, ts: float) -> bool:
    """Windows only: set the file's creation time. Elsewhere it can't be set (returns False)."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    k32.SetFileTime.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    FILE_WRITE_ATTRIBUTES, OPEN_EXISTING, SHARE_ALL, BACKUP_SEMANTICS = 0x100, 3, 0x7, 0x02000000
    h = k32.CreateFileW(path, FILE_WRITE_ATTRIBUTES, SHARE_ALL, None, OPEN_EXISTING, BACKUP_SEMANTICS, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return False
    try:
        ft = int(ts * 10_000_000) + 116444736000000000  # 100-ns intervals since 1601
        created = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
        return bool(k32.SetFileTime(h, ctypes.byref(created), None, None))
    finally:
        k32.CloseHandle(h)


def _hash_file(path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_device(src: str, dst_dir: str) -> bool:
    d = dst_dir
    while not os.path.exists(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    try:
        return os.stat(src).st_dev == os.stat(d).st_dev
    except OSError:
        return False


def move_file(src: str, dst: str) -> str:
    """Move one file keeping its timestamps. Returns 'rename' or 'copy'. dst must not exist."""
    if os.path.lexists(dst):
        raise OpError(f"Target exists: {dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if _same_device(src, os.path.dirname(dst)):
        os.rename(src, dst)
        return "rename"
    st = os.stat(src)
    birth = getattr(st, "st_birthtime", None)
    try:
        shutil.copy2(src, dst)  # data + modified/accessed time + permission bits
        if birth and os.name == "nt":
            set_creation_time(dst, birth)
        if os.path.getsize(dst) != st.st_size or _hash_file(dst) != _hash_file(src):
            raise OpError("Copy verification failed")
    except BaseException:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise
    os.remove(src)
    return "copy"


def _sidecars(path: str) -> list[str]:
    """Files that travel with `path`: XMP sidecars, Unity .meta (keeps references to the asset
    working), Godot .import/.uid."""
    stem = os.path.splitext(path)[0]
    out = []
    for cand in (path + ".xmp", stem + ".xmp", path + ".XMP", stem + ".XMP", path + ".meta", path + ".import", path + ".uid"):
        if os.path.isfile(cand) and cand not in out:
            out.append(cand)
    return out


def _free_name(path: str, taken: set[str]) -> str:
    def key(p):
        return os.path.normcase(p)
    if not os.path.lexists(path) and key(path) not in taken:
        return path
    stem, ext = os.path.splitext(path)
    for n in range(2, 10000):
        cand = f"{stem} ({n}){ext}"
        if not os.path.lexists(cand) and key(cand) not in taken:
            return cand
    raise OpError(f"No free name for {path}")


# ----------------------------------------------------------------------- move
def plan_move(db: Database, ids: list[int], dest: str, by_year: bool = False, date_prefix: bool = False) -> dict:
    """Plan moving files into one destination folder (optionally into year subfolders)."""
    dest = os.path.normpath(os.path.abspath(os.path.expanduser(dest.strip().strip('"'))))
    root = db.root_for(dest, "library")
    if root is None:
        raise OpError("The destination must be inside a library folder. Add it to the library first.")
    rows = _rows(db, ids)
    entries = []
    for fid in dict.fromkeys(int(i) for i in ids):
        r = rows.get(fid)
        target = dest
        if r and by_year and r[4]:
            target = os.path.join(dest, f"{datetime.fromtimestamp(r[4]):%Y}")
        entries.append({"id": fid, "dir": target, "date_prefix": date_prefix})
    plan = plan_moves(db, entries, rows)
    plan.update(dest=dest, root_id=root["id"])
    return plan


def _rows(db: Database, ids) -> dict:
    ids = list(dict.fromkeys(int(i) for i in ids))
    rows = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for r in db.cat.execute(f"SELECT id, path, name, size, date_ts FROM files WHERE id IN ({q})", chunk):
            rows[r[0]] = r
    return rows


def plan_moves(db: Database, entries: list[dict], rows: dict | None = None) -> dict:
    """entries: [{'id', 'dir', 'date_prefix', ...extra keys copied to the move}] - each file may
    have its own destination. Destinations outside the library are refused per file."""
    rows = rows if rows is not None else _rows(db, [e["id"] for e in entries])
    moves, skipped = [], []
    taken: set[str] = set()
    total = 0
    cross = False
    lib_ok: dict[str, bool] = {}
    for e in entries:
        fid = e["id"]
        extra = {k: v for k, v in e.items() if k not in ("id", "dir", "date_prefix")}
        r = rows.get(fid)
        if r is None:
            skipped.append({"id": fid, "reason": "not in catalog", **extra})
            continue
        _, src, name, size, ts = r
        target_dir = os.path.normpath(os.path.abspath(e["dir"]))
        if target_dir not in lib_ok:
            lib_ok[target_dir] = db.root_for(target_dir, "library") is not None
        if not lib_ok[target_dir]:
            skipped.append({"id": fid, "from": src, "reason": "destination outside the library", **extra})
            continue
        if not os.path.isfile(src):
            skipped.append({"id": fid, "from": src, "reason": "missing on disk", **extra})
            continue
        new_name = name
        if e.get("date_prefix") and ts and dates.parse_filename_date(name) is None:
            new_name = f"{datetime.fromtimestamp(ts):%Y-%m-%d}_{name}"
        dst = os.path.join(target_dir, new_name)
        if os.path.normcase(dst) == os.path.normcase(src):
            skipped.append({"id": fid, "from": src, "reason": "already there", **extra})
            continue
        dst = _free_name(dst, taken)
        taken.add(os.path.normcase(dst))
        cross = cross or not _same_device(src, target_dir)
        total += size or 0
        moves.append({"id": fid, "from": src, "to": dst, "renamed": os.path.basename(dst) != name, **extra})
    return {"moves": moves, "skipped": skipped, "bytes": total, "cross_device": cross, "root_id": None}


def _update_row(db: Database, fid: int, new_path: str, root_id: int | None) -> None:
    c = db.cat
    # A stale row for the target path (file deleted outside, not rescanned yet) would clash.
    c.execute("DELETE FROM files WHERE path = ? AND id != ?", (new_path, fid))
    name = os.path.basename(new_path)
    if root_id is None:
        r = db.root_for(new_path, None)
        root_id = r["id"] if r else None
    c.execute("UPDATE files SET path=?, dir=?, name=?, ext=?, root_id=COALESCE(?, root_id) WHERE id=?",
              (new_path, os.path.dirname(new_path), name, os.path.splitext(name)[1].lower(), root_id, fid))


def execute_moves(db: Database, scanner, moves: list[dict], root_id: int | None, kind: str = "move") -> dict:
    """Perform planned moves. Each finished move is committed and logged immediately."""
    done, failed = [], []
    with scanner.fs_lock:
        for m in moves:
            src, dst, fid = m["from"], m["to"], m["id"]
            try:
                if not os.path.isfile(src):
                    raise OpError("missing on disk")
                if os.path.lexists(dst):
                    dst = _free_name(dst, set())
                method = move_file(src, dst)
                side = []
                for sc in _sidecars(src):
                    suffix = sc[len(os.path.splitext(src)[0]):] if not sc.startswith(src) else sc[len(src):]
                    base = dst if sc.startswith(src) else os.path.splitext(dst)[0]
                    sdst = _free_name(base + suffix, set())
                    try:
                        move_file(sc, sdst)
                        side.append([sc, sdst])
                    except OSError:
                        pass
                with db.write_lock:
                    db.cat.execute("BEGIN")
                    _update_row(db, fid, dst, root_id)
                    db.cat.execute("COMMIT")
                done.append({"id": fid, "from": src, "to": dst, "method": method, "sidecars": side})
            except Exception as e:
                failed.append({"id": fid, "from": src, "to": dst, "error": f"{type(e).__name__}: {e}"})
    op_id = None
    if done:
        with db.write_lock:
            op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                   (time.time(), kind, json.dumps(done))).lastrowid
    scanner._bump()
    return {"op_id": op_id, "moved": len(done), "failed": failed,
            "copied": sum(1 for d in done if d["method"] == "copy")}


def undo_operation(db: Database, scanner, op_id: int) -> dict:
    row = db.cat.execute("SELECT kind, data, undone FROM operations WHERE id = ?", (op_id,)).fetchone()
    if not row:
        raise OpError("No such operation")
    if row[2]:
        raise OpError("Already undone")
    entries = json.loads(row[1])
    back = []
    for e in reversed(entries):
        back.append({"id": e["id"], "from": e["to"], "to": e["from"]})
    res = execute_moves(db, scanner, back, None, kind="undo")
    # sidecars back home
    for e in entries:
        for orig, moved in e.get("sidecars", []):
            if os.path.isfile(moved) and not os.path.lexists(orig):
                try:
                    move_file(moved, orig)
                except OSError:
                    pass
    with db.write_lock:
        db.cat.execute("UPDATE operations SET undone = 1 WHERE id = ?", (op_id,))
    return res


# --------------------------------------------------------------- browsing
def places() -> dict:
    home = os.path.expanduser("~")
    named = []
    for label, sub in (("Home", ""), ("Desktop", "Desktop"), ("Downloads", "Downloads"),
                       ("Pictures", "Pictures"), ("Documents", "Documents")):
        p = os.path.join(home, sub) if sub else home
        if os.path.isdir(p):
            named.append({"name": label, "path": os.path.normpath(p)})
    if os.name == "nt":
        if hasattr(os, "listdrives"):
            try:
                drives = os.listdrives()
            except OSError:
                drives = []
        else:
            drives = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
    else:
        drives = ["/"]
        for base in ("/Volumes", "/media", "/mnt"):
            if os.path.isdir(base):
                try:
                    drives += [os.path.join(base, d) for d in sorted(os.listdir(base))
                               if os.path.isdir(os.path.join(base, d))]
                except OSError:
                    pass
    return {"places": named, "drives": [{"name": d, "path": d} for d in drives]}


def list_dir(path: str, limit: int = 5000) -> dict:
    path = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isdir(path):
        raise OpError(f"Not a folder: {path}")
    dirs, images = [], 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        low = e.name.lower()
                        if low.startswith(".") or low.startswith("$") or low in SKIP_DIR_NAMES:
                            continue
                        dirs.append({"name": e.name, "path": e.path})
                    elif handler_for(os.path.splitext(e.name)[1]) is not None:
                        images += 1
                except OSError:
                    continue
    except PermissionError:
        raise OpError(f"No permission to read {path}")
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None,
            "dirs": dirs[:limit], "truncated": len(dirs) > limit, "images": images}


def reveal(path: str) -> None:
    """Show the file in Explorer / Finder / the default file manager."""
    if not os.path.exists(path):
        raise OpError("File is missing on disk")
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def open_default(path: str) -> None:
    """Open a file with its default application."""
    if not os.path.isfile(path):
        raise OpError("File is missing on disk")
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - a catalogued file, opened like a double-click in Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
