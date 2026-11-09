"""File system operations: safe moves and copies (with undo), folder listing, reveal in file manager.

Moving rules
  * never overwrite: name clashes get " (2)", " (3)" ...
  * same disk  -> rename (instant; every timestamp, incl. Windows creation time, is kept)
  * other disk -> copy, restore modified + creation time, verify the copy byte-for-byte (hash),
                  only then delete the source
  * sidecars travel along: photo.jpg.xmp / photo.xmp, and engine files: Unity .meta, Godot .import
  * catalog rows are updated in place, so tags and dates stay attached
  * every move is logged and can be undone

Copying rules
  * any folder, also outside the library (a client folder, a USB stick); never overwrites
  * modified + creation time kept, every copy verified byte-for-byte
  * XMP sidecars are copied too; Unity .meta and Godot .import/.uid are not (a copy needs its own
    GUID/UID - the engine makes new ones)
  * copies inside the library are in the catalog at once, with the original's tags, source,
    author and licence (no re-indexing: same content, same previews)
  * undo deletes the copies that are still unchanged
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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


def copy_file(src: str, dst: str) -> None:
    """Copy one file keeping its timestamps, verified byte-for-byte. dst must not exist."""
    if os.path.lexists(dst):
        raise OpError(f"Target exists: {dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
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


def _sidecars(path: str, xmp_only: bool = False) -> list[str]:
    """Files that travel with `path`: XMP sidecars, Unity .meta (keeps references to the asset
    working), Godot .import/.uid. xmp_only: just the XMP sidecars (for copies - an engine file
    holds the asset's own GUID/UID, which a copy must not share)."""
    stem = os.path.splitext(path)[0]
    out = []
    cands = (path + ".xmp", stem + ".xmp", path + ".XMP", stem + ".XMP")
    if not xmp_only:
        cands += (path + ".meta", path + ".import", path + ".uid")
    for cand in cands:
        if os.path.isfile(cand) and cand not in out:
            out.append(cand)
    return out


def _sidecar_target(src: str, sc: str, dst: str) -> str:
    """Where sidecar `sc` of `src` goes when `src` becomes `dst` (photo.jpg.xmp or photo.xmp style)."""
    if sc.startswith(src):
        return dst + sc[len(src):]
    return os.path.splitext(dst)[0] + sc[len(os.path.splitext(src)[0]):]


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
    dest = _dest_dir(dest)
    root = db.root_for(dest, "library")
    if root is None:
        raise OpError("The destination must be inside a library folder. Add it to the library first.")
    rows = _rows(db, ids)
    plan = plan_moves(db, _entries(ids, rows, dest, by_year, date_prefix), rows)
    plan.update(dest=dest, root_id=root["id"])
    return plan


def plan_copy(db: Database, ids: list[int], dest: str, by_year: bool = False, date_prefix: bool = False) -> dict:
    """Plan copying files into one folder - anywhere, also outside the library."""
    dest = _dest_dir(dest)
    rows = _rows(db, ids)
    plan = plan_moves(db, _entries(ids, rows, dest, by_year, date_prefix), rows, copy=True)
    plan["copies"] = plan.pop("moves")
    plan.update(dest=dest, in_library=db.root_for(dest, "library") is not None)
    return plan


def _dest_dir(dest: str) -> str:
    dest = (dest or "").strip().strip('"')
    if not dest:
        raise OpError("Choose a destination folder")
    return os.path.normpath(os.path.abspath(os.path.expanduser(dest)))


def _entries(ids, rows: dict, dest: str, by_year: bool, date_prefix: bool) -> list[dict]:
    entries = []
    for fid in dict.fromkeys(int(i) for i in ids):
        r = rows.get(fid)
        target = dest
        if r and by_year and r[4]:
            target = os.path.join(dest, f"{datetime.fromtimestamp(r[4]):%Y}")
        entries.append({"id": fid, "dir": target, "date_prefix": date_prefix})
    return entries


def _rows(db: Database, ids) -> dict:
    ids = list(dict.fromkeys(int(i) for i in ids))
    rows = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for r in db.cat.execute(f"SELECT id, path, name, size, date_ts FROM files WHERE id IN ({q})", chunk):
            rows[r[0]] = r
    return rows


def plan_moves(db: Database, entries: list[dict], rows: dict | None = None, copy: bool = False) -> dict:
    """entries: [{'id', 'dir', 'date_prefix', ...extra keys copied to the move}] - each file may
    have its own destination. Destinations outside the library are refused per file.
    copy: plan copies instead - any destination, and a copy into the file's own folder gets " (2)"."""
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
        if not copy:
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
        if not copy and os.path.normcase(dst) == os.path.normcase(src):
            skipped.append({"id": fid, "from": src, "reason": "already there", **extra})
            continue
        want = dst
        dst = _free_name(dst, taken)
        taken.add(os.path.normcase(dst))
        cross = cross or not _same_device(src, target_dir)
        total += size or 0
        moves.append({"id": fid, "from": src, "to": dst, "renamed": os.path.basename(dst) != name, **extra,
                      **({"want": want} if copy else {})})
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


# Copied files that are new catalog rows: everything but the place and the stack is the original's.
_NOT_CLONED = {"id", "root_id", "path", "dir", "name", "ext", "size", "mtime_ns", "seen", "stack_id"}


def _clone_row(db: Database, fid: int, dst: str, root_id: int, keep_tags: bool) -> int | None:
    """A catalog row for the copy `dst` of file `fid`: same content, so the same hash, dates,
    metadata and previews - nothing to index again. With keep_tags also its tags and its
    source / author / licence."""
    c = db.cat
    cols = [r[1] for r in c.execute("PRAGMA table_info(files)") if r[1] not in _NOT_CLONED]
    st = os.stat(dst)
    name = os.path.basename(dst)
    cl = ", ".join(cols)
    with db.write_lock:
        c.execute("BEGIN")
        try:
            # A stale row for the target path (file deleted outside, not rescanned yet) would clash.
            c.execute("DELETE FROM files WHERE path = ?", (dst,))
            new = c.execute(
                f"INSERT INTO files(root_id, path, dir, name, ext, size, mtime_ns, seen, {cl}) "
                f"SELECT ?, ?, ?, ?, ?, ?, ?, 0, {cl} FROM files WHERE id = ?",
                (root_id, dst, os.path.dirname(dst), name, os.path.splitext(name)[1].lower(),
                 st.st_size, st.st_mtime_ns, fid)).lastrowid
            if keep_tags:
                c.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) "
                          "SELECT ?, tag_id, source, confidence, added FROM file_tags WHERE file_id = ?", (new, fid))
                c.execute("INSERT OR IGNORE INTO file_info(file_id, source, author, licence, updated) "
                          "SELECT ?, source, author, licence, updated FROM file_info WHERE file_id = ?", (new, fid))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return new


def add_derived_file(db: Database, path: str, root_id: int, like_id: int, keep_tags: bool = True) -> int:
    """A new file Kurator made from catalog file `like_id` (a resampled copy...): a catalog row to be
    indexed (pending), starting with the original's date, and with its tags, source, author and
    licence when keep_tags."""
    st = os.stat(path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    h = handler_for(ext)
    orig = db.cat.execute("SELECT date_ts, date_source FROM files WHERE id = ?", (like_id,)).fetchone()
    ts, src = (orig[0], orig[1]) if orig and orig[0] else dates.resolve_date({}, name, st)
    c = db.cat
    with db.write_lock:
        c.execute("BEGIN")
        try:
            c.execute("DELETE FROM files WHERE path = ?", (path,))
            new = c.execute(
                "INSERT INTO files(root_id, path, dir, name, ext, kind, size, mtime_ns, date_ts, date_source, status, seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0,0)",
                (root_id, path, os.path.dirname(path), name, ext, h.kind if h else "image", st.st_size, st.st_mtime_ns,
                 ts, src)).lastrowid
            if keep_tags:
                c.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) "
                          "SELECT ?, tag_id, source, confidence, ? FROM file_tags WHERE file_id = ?", (new, time.time(), like_id))
                c.execute("INSERT OR IGNORE INTO file_info(file_id, source, author, licence, updated) "
                          "SELECT ?, source, author, licence, updated FROM file_info WHERE file_id = ?", (new, like_id))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return new


def indexed_root(db: Database, folder: str, cache: dict) -> int | None:
    """The library root that will index files in `folder` (None: outside the library, or in a
    folder the scan skips, like a hidden one or Unity's Library/)."""
    if folder not in cache:
        r = db.root_for(folder, "library")
        if r is not None:
            from .scanner import _excluded, _norm
            if _excluded(_norm(r["path"]), _norm(folder)):
                r = None
        cache[folder] = r["id"] if r else None
    return cache[folder]


def _delivered(db: Database, fid: int, src: str, clean: bool, credit: bool) -> bytes | None:
    """The bytes to deliver for one file (private details out, credit in), or None: copy as it is."""
    if not (clean or credit):
        return None
    from . import delivery, fileinfo
    if os.path.splitext(src)[1].lower() not in delivery.CLEANABLE:
        return None
    with open(src, "rb") as f:
        data = f.read()
    return delivery.prepare(data, os.path.splitext(src)[1], clean, fileinfo.get(db, fid) if credit else None)


def _write_like(src: str, dst: str, data: bytes) -> None:
    """Write new bytes at dst (must not exist) with the times of src."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".kurator-part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        shutil.copystat(src, tmp)
        if os.path.lexists(dst):
            raise OpError(f"Target exists: {dst}")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    birth = getattr(os.stat(src), "st_birthtime", None)
    if birth and os.name == "nt":
        set_creation_time(dst, birth)


def execute_copies(db: Database, scanner, copies: list[dict], keep_tags: bool = True, clean: bool = False,
                   credit: bool = False) -> dict:
    """Perform planned copies. Copies inside the library join the catalog at once.
    clean / credit: copies for delivery (see delivery.py) - then without sidecars when cleaning."""
    done, failed = [], []
    roots: dict[str, int | None] = {}
    changed = 0
    with scanner.fs_lock:
        for m in copies:
            src, dst, fid = m["from"], m["to"], m["id"]
            try:
                if not os.path.isfile(src):
                    raise OpError("missing on disk")
                if os.path.lexists(dst):
                    dst = _free_name(dst, set())
                data = _delivered(db, fid, src, clean, credit)
                if data is None:
                    copy_file(src, dst)
                else:
                    _write_like(src, dst, data)
                    changed += 1
                side = []
                for sc in ([] if clean else _sidecars(src, xmp_only=True)):
                    sdst = _sidecar_target(src, sc, dst)
                    if os.path.lexists(sdst):
                        continue   # a stray sidecar of that name stays as it is
                    try:
                        copy_file(sc, sdst)
                        side.append(sdst)
                    except OSError:
                        pass
                # photo.jpg.xmp: written for the copy later (tagging) unless one was there before
                own_xmp = dst + ".xmp" in side or not os.path.lexists(dst + ".xmp")
                st = os.stat(dst)
                new_id = None
                rid = indexed_root(db, os.path.dirname(dst), roots)
                if rid is not None:   # same content: the original's row; changed content: indexed anew
                    new_id = _clone_row(db, fid, dst, rid, keep_tags) if data is None else add_derived_file(db, dst, rid, fid, keep_tags)
                done.append({"id": new_id, "src_id": fid, "from": src, "to": dst, "size": st.st_size,
                             "mtime_ns": st.st_mtime_ns, "sidecars": side, "own_xmp": own_xmp})
            except Exception as e:
                failed.append({"id": fid, "from": src, "to": dst, "error": f"{type(e).__name__}: {e}"})
    op_id = None
    if done:
        with db.write_lock:
            op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                   (time.time(), "copy", json.dumps(done))).lastrowid
    if changed and any(d["id"] is not None for d in done):
        scanner.request_process()
    scanner._bump()
    return {"op_id": op_id, "copied": len(done), "failed": failed, "cleaned": changed,
            "new_ids": [d["id"] for d in done if d["id"] is not None],
            "indexed": sum(1 for d in done if d["id"] is not None)}


# already compressed: stored as they are in a zip (deflating them again costs time and saves nothing)
_STORED = {".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp", ".gif", ".avif", ".heic", ".heif", ".jxl", ".mp4", ".m4v",
           ".mov", ".webm", ".mkv", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".flac", ".zip", ".7z", ".rar", ".kra",
           ".ora", ".procreate", ".glb", ".ktx2", ".basis", ".dds"}


def copy_to_zip(db: Database, scanner, copies: list[dict], dest: str, name: str, clean: bool = False,
                credit: bool = False) -> dict:
    """The planned copies as one zip archive in `dest` (relative names as planned)."""
    import zipfile
    stem = _safe_stem(name or "") or f"delivery_{datetime.now():%Y-%m-%d}"
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    zpath = _free_name(os.path.join(dest, stem + ".zip"), set())
    tmp = zpath + ".kurator-part"
    os.makedirs(dest, exist_ok=True)
    failed, n, names = [], 0, set()
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            for m in copies:
                src = m["from"]
                arc = os.path.relpath(m.get("want") or m["to"], dest).replace(os.sep, "/")   # (only clashes inside the zip count)
                stem_a, ext_a = os.path.splitext(arc)
                k = 2
                while arc.lower() in names:
                    arc = f"{stem_a} ({k}){ext_a}"
                    k += 1
                try:
                    if not os.path.isfile(src):
                        raise OpError("missing on disk")
                    ext = os.path.splitext(src)[1].lower()
                    ctype = zipfile.ZIP_STORED if ext in _STORED else zipfile.ZIP_DEFLATED
                    data = _delivered(db, m["id"], src, clean, credit)
                    if data is None:
                        z.write(src, arc, compress_type=ctype)
                    else:
                        zi = zipfile.ZipInfo(arc, date_time=time.localtime(os.stat(src).st_mtime)[:6])
                        zi.compress_type = ctype
                        z.writestr(zi, data)
                    names.add(arc.lower())
                    n += 1
                except Exception as e:  # noqa: BLE001
                    failed.append({"id": m["id"], "from": src, "error": f"{type(e).__name__}: {e}"})
        if not n:
            raise OpError("Nothing could be put into the zip")
        os.replace(tmp, zpath)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    st = os.stat(zpath)
    entry = {"id": None, "src_id": None, "from": None, "to": zpath, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
             "sidecars": [], "own_xmp": False}
    with db.write_lock:
        op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                               (time.time(), "copy", json.dumps([entry]))).lastrowid
    scanner._bump()
    return {"op_id": op_id, "copied": n, "failed": failed, "zip": zpath, "bytes": st.st_size, "new_ids": [], "indexed": 0}


def _undo_copies(db: Database, scanner, entries: list[dict]) -> dict:
    """Delete copies Kurator made - only those still exactly as they were copied."""
    removed, kept = 0, []
    with scanner.fs_lock:
        for e in entries:
            path = e["to"]
            if e.get("id") is not None:   # moved since (inside Kurator): follow it
                row = db.cat.execute("SELECT path FROM files WHERE id = ?", (e["id"],)).fetchone()
                if row:
                    path = row[0]
            try:
                st = os.stat(path)
            except OSError:
                kept.append({"path": path, "reason": "already gone"})
                continue
            if (st.st_size, st.st_mtime_ns) != (e["size"], e["mtime_ns"]):
                kept.append({"path": path, "reason": "changed since it was copied"})
                continue
            try:
                os.remove(path)
            except OSError as ex:
                kept.append({"path": path, "reason": f"{type(ex).__name__}: {ex}"})
                continue
            removed += 1
            # its sidecars: the copied ones, and photo.jpg.xmp written for it since (tagging)
            side = [*e.get("sidecars", []), *([path + ".xmp"] if e.get("own_xmp") and path == e["to"] else [])]
            for sc in dict.fromkeys(side):
                try:
                    os.remove(sc)
                except OSError:
                    pass
            with db.write_lock:
                db.cat.execute("DELETE FROM files WHERE path = ?", (path,))
    scanner._bump()
    return {"removed": removed, "kept": kept}


def undo_operation(db: Database, scanner, op_id: int) -> dict:
    row = db.cat.execute("SELECT kind, data, undone FROM operations WHERE id = ?", (op_id,)).fetchone()
    if not row:
        raise OpError("No such operation")
    if row[2]:
        raise OpError("Already undone")
    entries = json.loads(row[1])
    if row[0] in ("copy", "resample", "delete", "rename"):
        if row[0] == "delete":            # back from the trash, with catalog entries and tags
            from . import trash
            res = trash.undo_delete(db, scanner, entries)
        elif row[0] == "rename":          # names may have been swapped: two steps again
            res = execute_renames(db, scanner, [{"id": e["id"], "from": e["to"], "to": e["from"]} for e in entries], kind="undo")
        else:                             # new files: undo removes them
            res = _undo_copies(db, scanner, entries)
        with db.write_lock:
            db.cat.execute("UPDATE operations SET undone = 1 WHERE id = ?", (op_id,))
        return res
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


# ----------------------------------------------------------------------- rename
_TOKEN = re.compile(r"\{(\w+)(?::([^{}]*))?\}")
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
TOKENS = ("name", "n", "date", "year", "month", "day", "time", "w", "h", "folder", "tag", "tags", "project", "author", "ext")
CASES = ("keep", "lower", "upper", "title")


def _safe_stem(s: str) -> str:
    s = _ILLEGAL.sub("_", s).strip().rstrip(" .")[:200]
    return s + "_" if s.lower() in _RESERVED else s


def _tag_names(db: Database, ids: list[int]) -> dict[int, dict[str, list[str]]]:
    out: dict[int, dict[str, list[str]]] = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for fid, name, facet in db.cat.execute(
                f"SELECT ft.file_id, t.name, t.facet FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
                f"WHERE ft.file_id IN ({q}) ORDER BY t.name COLLATE NOCASE", chunk):
            out.setdefault(fid, {}).setdefault(facet, []).append(name)
    return out


def render_name(template: str, row: dict, n: int, pad: int, tagmap: dict, info: dict) -> str:
    """One file's new name (without extension) from a template like "{date}_{tag}_{n}"."""
    stem, ext = os.path.splitext(row["name"])
    ts = row.get("date_ts")
    dt = datetime.fromtimestamp(ts) if ts else None
    subj = [t.split("/")[-1] for t in tagmap.get("subject", [])]

    def tok(m):
        key, arg = m.group(1).lower(), m.group(2)
        if key == "name":
            return stem
        if key == "n":
            width = int(arg) if arg and arg.isdigit() else pad
            return str(n).zfill(width)
        if key in ("date", "year", "month", "day", "time"):
            if dt is None:
                return ""
            fmt = arg if key == "date" and arg else {"date": "%Y-%m-%d", "year": "%Y", "month": "%m", "day": "%d", "time": "%H%M%S"}[key]
            return dt.strftime(fmt)
        if key == "w":
            return str(row.get("width") or "")
        if key == "h":
            return str(row.get("height") or "")
        if key == "folder":
            return os.path.basename(os.path.dirname(row["path"]))
        if key == "tag":
            return subj[0] if subj else ""
        if key == "tags":
            return (arg if arg is not None else "_").join(subj)
        if key == "project":
            pr = tagmap.get("project", [])
            return pr[0].split("/")[0] if pr else ""
        if key == "author":
            return info.get("author") or ""
        if key == "ext":
            return ext.lstrip(".")
        raise OpError(f"Unknown name part {{{m.group(1)}}} - use {', '.join('{' + t + '}' for t in TOKENS)}")
    return _TOKEN.sub(tok, template)


def plan_rename(db: Database, ids: list[int], template: str = "{name}", start: int = 1, step: int = 1, pad: int = 0,
                find: str = "", replace: str = "", regex: bool = False, case: str = "keep") -> dict:
    """New names for files in their own folders: [{'id', 'from', 'to', 'clash'}]. ids in the order
    the counter {n} counts. Extensions stay. Names freed by the batch can be reused (renumbering)."""
    from . import fileinfo
    if case not in CASES:
        raise OpError("Case must be keep, lower, upper or title")
    if not (template or "").strip():
        raise OpError("Give a name pattern, e.g. {name} or {date}_{n}")
    pat = None
    if find:
        try:
            pat = re.compile(find if regex else re.escape(find))
        except re.error as e:
            raise OpError(f"Search pattern: {e}") from None
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise OpError("Select files to rename first")
    rows = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for r in db.cat.execute(f"SELECT id, path, name, date_ts, width, height FROM files WHERE id IN ({q})", chunk):
            rows[r[0]] = dict(r)
    tagmap = _tag_names(db, ids)
    infos = fileinfo.get_many(db, ids)
    wanted, skipped = [], []
    for k, fid in enumerate(ids):
        r = rows.get(fid)
        if r is None:
            skipped.append({"id": fid, "reason": "not in catalog"})
            continue
        if not os.path.isfile(r["path"]):
            skipped.append({"id": fid, "from": r["path"], "reason": "missing on disk"})
            continue
        stem = render_name(template, r, start + k * step, max(0, int(pad or 0)), tagmap.get(fid, {}), infos.get(fid, {}))
        if pat is not None:
            try:
                stem = pat.sub(replace or "", stem)
            except (re.error, IndexError) as e:
                raise OpError(f"Replacement: {e}") from None
        stem = {"lower": str.lower, "upper": str.upper, "title": str.title}.get(case, lambda x: x)(stem)
        stem = _safe_stem(stem)
        if not stem:
            skipped.append({"id": fid, "from": r["path"], "reason": "the new name would be empty"})
            continue
        new = os.path.join(os.path.dirname(r["path"]), stem + os.path.splitext(r["name"])[1])
        if new == r["path"]:
            skipped.append({"id": fid, "from": r["path"], "reason": "name unchanged"})
            continue
        wanted.append((fid, r["path"], new))
    freed = {os.path.normcase(src) for _, src, _ in wanted}
    taken: set[str] = set()
    out = []
    for fid, src, new in wanted:
        stem, ext = os.path.splitext(new)
        cand, n = new, 2
        while (os.path.normcase(cand) in taken
               or (os.path.lexists(cand) and os.path.normcase(cand) not in freed and os.path.normcase(cand) != os.path.normcase(src))):
            cand = f"{stem} ({n}){ext}"
            n += 1
        taken.add(os.path.normcase(cand))
        out.append({"id": fid, "from": src, "to": cand, "clash": cand != new})
    return {"renames": out, "skipped": skipped, "count": len(out)}


def execute_renames(db: Database, scanner, items: list[dict], kind: str = "rename") -> dict:
    """Rename in two steps (all to temporary names, then to the new ones), so names can be swapped
    or shifted (renumbering). Sidecars follow; catalog rows keep their ids, tags and dates."""
    done, failed = [], []
    with scanner.fs_lock:
        staged = []
        for i, it in enumerate(items):
            src = it["from"]
            if not os.path.isfile(src):
                failed.append({"id": it["id"], "from": src, "error": "missing on disk"})
                continue
            tmp = os.path.join(os.path.dirname(src), f".kurator-rename-{os.getpid()}-{i}{os.path.splitext(src)[1]}")
            try:
                os.rename(src, tmp)
            except OSError as e:
                failed.append({"id": it["id"], "from": src, "error": f"{type(e).__name__}: {e}"})
                continue
            side = []
            for sc in _sidecars(src):
                stmp = tmp + "." + str(len(side)) + ".side"
                try:
                    os.rename(sc, stmp)
                    side.append((sc, stmp))
                except OSError:
                    pass
            staged.append((it, tmp, side))
        for it, tmp, side in staged:
            src, dst = it["from"], it["to"]
            if os.path.lexists(dst):
                dst = _free_name(dst, set())
            try:
                os.rename(tmp, dst)
            except OSError as e:
                try:
                    os.rename(tmp, src)
                    dst = src
                except OSError:
                    dst = tmp
                failed.append({"id": it["id"], "from": src, "to": it["to"], "error": f"{type(e).__name__}: {e}"})
            moved_side = []
            for sc, stmp in side:
                target = _sidecar_target(src, sc, dst) if dst not in (src, tmp) else sc
                if target != sc and os.path.lexists(target):
                    target = _free_name(target, set())
                try:
                    os.rename(stmp, target)
                    if target != sc:
                        moved_side.append([sc, target])
                except OSError:
                    pass
            if dst not in (src, tmp):
                done.append({"id": it["id"], "from": src, "to": dst, "sidecars": moved_side})
        if done:   # catalog: first out of the way, then the new paths (they may be each other's old ones)
            c = db.cat
            with db.write_lock:
                c.execute("BEGIN")
                try:
                    for d in done:
                        c.execute("UPDATE files SET path = ? WHERE id = ?", (f"\0renaming:{d['id']}", d["id"]))
                    ids = {d["id"] for d in done}
                    for d in done:
                        stale = c.execute("SELECT id FROM files WHERE path = ?", (d["to"],)).fetchone()
                        if stale and stale[0] not in ids:
                            c.execute("DELETE FROM files WHERE id = ?", (stale[0],))
                        name = os.path.basename(d["to"])
                        c.execute("UPDATE files SET path = ?, name = ?, ext = ? WHERE id = ?",
                                  (d["to"], name, os.path.splitext(name)[1].lower(), d["id"]))
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise
    op_id = None
    if done:
        with db.write_lock:
            op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                   (time.time(), kind, json.dumps(done))).lastrowid
    scanner._bump()
    return {"op_id": op_id, "renamed": len(done), "moved": len(done), "failed": failed}


# ----------------------------------------------------------------------- destination folders
RECENT_MAX = 30


def recent_folders(db: Database) -> list[dict]:
    """Folders used as destinations (move, copy, resample, sheets, export, datasets), newest first."""
    return list(db.get_setting("recent_dests", []) or [])


def remember_folder(db: Database, path: str | None) -> None:
    if not path or not str(path).strip():
        return
    p = os.path.normpath(os.path.abspath(os.path.expanduser(str(path).strip().strip('"'))))
    lst = [r for r in recent_folders(db) if os.path.normcase(r["path"]) != os.path.normcase(p)]
    db.set_setting("recent_dests", ([{"path": p, "used": time.time()}] + lst)[:RECENT_MAX])


def forget_folder(db: Database, path: str) -> list[dict]:
    lst = [r for r in recent_folders(db) if os.path.normcase(r["path"]) != os.path.normcase(str(path))]
    db.set_setting("recent_dests", lst)
    return lst


def make_folder(parent: str, name: str) -> str:
    """A new folder in `parent` (the folder picker's "New folder")."""
    parent = os.path.normpath(os.path.abspath(os.path.expanduser(parent)))
    if not os.path.isdir(parent):
        raise OpError(f"Not a folder: {parent}")
    clean = _safe_stem(name or "")
    if not clean or clean in (".", ".."):
        raise OpError("Give the new folder a name")
    path = os.path.join(parent, clean)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise OpError(f"Can't make {path}: {e.strerror or e}") from None
    return path
