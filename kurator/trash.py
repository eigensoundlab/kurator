"""Deleting files, two ways (⚙ Settings → Delete):

* system (default): the Windows Recycle Bin / macOS Trash / the desktop's trash on Linux (Send2Trash).
  Restore a file there and Kurator gives it its tags back (they are parked by content hash).
  Ctrl+Z puts files back from the Recycle Bin (Windows), the freedesktop trash (Linux) or the
  Trash (macOS: found by name, size and time).
* kurator: Kurator's own holding folder on the same disk - Kurator's data folder when it is on
  that disk, else a hidden ".kurator-trash" folder at the top of the disk (or of the library
  folder when the top can't be written). Deleting is a rename (instant), Ctrl+Z always works,
  and the folder is emptied after N days or with *Empty* in Settings.

Sidecars (XMP, Unity .meta, Godot .import/.uid) go with their file and come back with it. Every
delete is an operation that Ctrl+Z undoes: files go back, with their catalog entries, tags,
source / author / licence and stack - nothing has to be indexed again.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import time
from datetime import datetime
from urllib.parse import unquote

from . import fileops
from .db import Database
from .fileops import OpError

MODES = ("system", "kurator")
HOLD_NAME = ".kurator-trash"
DEFAULT_DAYS = 30


def mode(db: Database) -> str:
    m = db.get_setting("delete_mode", "system")
    return m if m in MODES else "system"


def keep_days(db: Database) -> int:
    try:
        return max(0, int(db.get_setting("trash_days", DEFAULT_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


def system_available() -> bool:
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- the holding folder
def _mount_point(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.ismount(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def _dev(path: str) -> int | None:
    p = path
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    try:
        return os.stat(p).st_dev
    except OSError:
        return None


def holding_root(db: Database, path: str) -> str:
    """Kurator's holding folder on the disk of `path` (so deleting is a rename)."""
    dev = _dev(path)
    data = os.path.join(db.data_dir, "trash")
    if dev is not None and dev == _dev(db.data_dir):
        return data
    top = _mount_point(path)
    cand = os.path.join(top, HOLD_NAME)
    if os.path.isdir(cand) or os.access(top, os.W_OK):
        return cand
    root = db.root_for(path, "library")
    if root is not None:
        return os.path.join(root["path"], HOLD_NAME)   # hidden: never indexed
    raise OpError(f"No place to keep deleted files on the disk of {path}")


def _known_roots(db: Database) -> list[str]:
    return list(db.get_setting("trash_roots", []) or [])


def _remember_root(db: Database, root: str) -> None:
    roots = _known_roots(db)
    if root not in roots:
        db.set_setting("trash_roots", roots + [root])


def holding_info(db: Database) -> dict:
    """What Kurator's holding folders keep: files, bytes, folders."""
    files = size = 0
    roots = []
    for r in _known_roots(db):
        if not os.path.isdir(r):
            continue
        roots.append(r)
        for dp, _dn, fn in os.walk(r):
            for f in fn:
                if f == "kurator-trash.json":
                    continue
                files += 1
                try:
                    size += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    return {"files": files, "bytes": size, "roots": roots}


def empty_holding(db: Database, older_than_days: float | None = None) -> dict:
    """Delete batches in the holding folders for good (all, or those older than N days)."""
    removed = 0
    now = time.time()
    for r in _known_roots(db):
        if not os.path.isdir(r):
            continue
        for name in os.listdir(r):
            batch = os.path.join(r, name)
            if not os.path.isdir(batch):
                continue
            try:
                age = now - os.stat(os.path.join(batch, "kurator-trash.json")).st_mtime
            except OSError:
                age = now - os.stat(batch).st_mtime
            if older_than_days is not None and age < older_than_days * 86400:
                continue
            for dp, _dn, fn in os.walk(batch):
                removed += sum(1 for f in fn if f != "kurator-trash.json")
            shutil.rmtree(batch, ignore_errors=True)
        try:
            if r.endswith(HOLD_NAME) and not os.listdir(r):
                os.rmdir(r)
        except OSError:
            pass
    return {"removed": removed}


def auto_empty(db: Database) -> dict:
    days = keep_days(db)
    return empty_holding(db, days) if days else {"removed": 0}


# ---------------------------------------------------------------- the system trash
def no_recycle_bin(path: str) -> bool:
    """Windows keeps no Recycle Bin on network shares and removable drives: a "recycled" file there
    would be gone for good. Such files go to Kurator's trash folder instead."""
    if os.name != "nt":
        return False
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if drive.startswith(("\\\\", "//")):           # \\server\share
        return True
    try:
        import ctypes
        kind = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
    except Exception:  # noqa: BLE001
        return True                                  # unsure: the safe way
    return kind in (2, 4)                            # DRIVE_REMOVABLE, DRIVE_REMOTE


def to_system_trash(path: str) -> None:
    try:
        from send2trash import send2trash
    except ImportError:
        raise OpError("The system trash needs the Send2Trash package: run update.bat / update.sh, "
                      "or choose Kurator's trash folder in ⚙ Settings → Delete") from None
    send2trash(path)


def _filetime(ft: int) -> float:
    return (ft - 116444736000000000) / 10_000_000


def _read_index_file(p: str) -> tuple[str, int, float] | None:
    """A Windows Recycle Bin $I file: (original path, size, deletion time)."""
    try:
        with open(p, "rb") as f:
            data = f.read(70000)
    except OSError:
        return None
    if len(data) < 24:
        return None
    ver, size, ft = struct.unpack_from("<qqq", data, 0)
    if ver == 1:                                   # Vista - 8.1: a fixed 260-character field
        raw = data[24:24 + 520]
    elif ver == 2 and len(data) >= 28:             # Windows 10+: length-prefixed
        n = struct.unpack_from("<i", data, 24)[0]
        raw = data[28:28 + 2 * max(0, n)]
    else:
        return None
    name = raw.decode("utf-16-le", "ignore").split("\0", 1)[0]
    return (name, size, _filetime(ft)) if name else None


def _windows_bins(path: str) -> list[str]:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    top = os.path.join(drive + os.sep, "$Recycle.Bin")
    try:
        return [os.path.join(top, d) for d in os.listdir(top)]
    except OSError:
        return []


def _restore_windows(path: str, size: int, deleted_after: float, bins: list[str] | None = None) -> bool:
    best = None
    want = os.path.normcase(os.path.abspath(path))
    for b in bins if bins is not None else _windows_bins(path):
        try:
            names = os.listdir(b)
        except OSError:
            continue                               # another user's bin
        for n in names:
            if not n.startswith("$I"):
                continue
            rec = _read_index_file(os.path.join(b, n))
            if not rec or os.path.normcase(rec[0]) != want or rec[1] != size or rec[2] < deleted_after - 120:
                continue
            item = os.path.join(b, "$R" + n[2:])
            if os.path.isfile(item) and (best is None or rec[2] > best[0]):
                best = (rec[2], item, os.path.join(b, n))
    if not best:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.rename(best[1], path)
    try:
        os.remove(best[2])
    except OSError:
        pass
    return True


def _fd_trashes(path: str) -> list[tuple[str, str]]:
    """freedesktop.org trash folders that may hold `path`: (trash folder, top folder)."""
    xdg = os.path.expanduser(os.environ.get("XDG_DATA_HOME") or "~/.local/share")
    out = [(os.path.join(xdg, "Trash"), xdg)]
    top = _mount_point(path)
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    out += [(os.path.join(top, ".Trash", uid), top), (os.path.join(top, ".Trash-" + uid), top)]
    return out


def _restore_freedesktop(path: str, deleted_after: float, trashes: list[tuple[str, str]] | None = None) -> bool:
    want = os.path.abspath(path)
    best = None
    for trash, top in trashes if trashes is not None else _fd_trashes(path):
        info_dir = os.path.join(trash, "info")
        try:
            names = os.listdir(info_dir)
        except OSError:
            continue
        for n in names:
            if not n.endswith(".trashinfo"):
                continue
            p = os.path.join(info_dir, n)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read(8192)
            except OSError:
                continue
            fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            orig = unquote(fields.get("Path", ""))
            if not orig:
                continue
            orig = orig if os.path.isabs(orig) else os.path.join(top, orig)
            if os.path.abspath(orig) != want:
                continue
            try:
                when = datetime.strptime(fields.get("DeletionDate", ""), "%Y-%m-%dT%H:%M:%S").timestamp()
            except ValueError:
                when = 0
            if when < deleted_after - 120:
                continue
            item = os.path.join(trash, "files", n[:-len(".trashinfo")])
            if os.path.lexists(item) and (best is None or when > best[0]):
                best = (when, item, p)
    if not best:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.move(best[1], path)
    try:
        os.remove(best[2])
    except OSError:
        pass
    return True


def _mac_trashes(path: str) -> list[str]:
    out = [os.path.expanduser("~/.Trash")]
    top = _mount_point(path)
    if top != "/":
        out.append(os.path.join(top, ".Trashes", str(os.getuid())))
    return out


def _restore_mac(path: str, size: int, mtime_ns: int, trashes: list[str] | None = None) -> bool:
    """The Trash keeps no list we can read: find the file by name, size and modified time."""
    stem, ext = os.path.splitext(os.path.basename(path))
    for t in trashes if trashes is not None else _mac_trashes(path):
        try:
            names = os.listdir(t)
        except OSError:
            continue
        for n in sorted(names, key=lambda x: (x != stem + ext, x)):       # the plain name first
            if not (n.startswith(stem) and n.endswith(ext)):
                continue
            p = os.path.join(t, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size == size and abs(st.st_mtime_ns - mtime_ns) < 2_000_000_000:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.move(p, path)
                return True
    return False


def restore_system(path: str, size: int, mtime_ns: int, deleted_at: float) -> bool:
    if os.path.lexists(path):
        return False
    try:
        if os.name == "nt":
            return _restore_windows(path, size, deleted_at)
        if sys.platform == "darwin":
            return _restore_mac(path, size, mtime_ns)
        return _restore_freedesktop(path, deleted_at)
    except OSError:
        return False


# ---------------------------------------------------------------- delete + undo
def _row(db: Database, fid: int) -> dict | None:
    r = db.cat.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
    return dict(r) if r else None


def delete_files(db: Database, scanner, ids: list[int], how: str | None = None, extra: dict | None = None) -> dict:
    """Delete catalog files (and their sidecars) to the system trash or Kurator's holding folder.
    The catalog entries leave with their tags parked by content hash; the operation keeps
    everything needed to put them back (Ctrl+Z)."""
    how = how or mode(db)
    if how not in MODES:
        raise OpError("Delete to the system trash or to Kurator's trash folder")
    ids = list(dict.fromkeys(int(i) for i in ids))
    done, failed = [], []
    batch_dirs: dict[str, str] = {}
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    now = time.time()
    with scanner.fs_lock:
        for fid in ids:
            row = _row(db, fid)
            if row is None:
                failed.append({"id": fid, "error": "not in catalog"})
                continue
            path = row["path"]
            if not os.path.isfile(path):
                failed.append({"id": fid, "path": path, "error": "missing on disk"})
                continue
            st = os.stat(path)
            entry = {"id": fid, "path": path, "how": how, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "at": now,
                     "row": row, "tags": [list(r) for r in db.cat.execute(
                         "SELECT tag_id, source, confidence, added FROM file_tags WHERE file_id = ?", (fid,))],
                     "info": [list(r) for r in db.cat.execute(
                         "SELECT source, author, licence, updated FROM file_info WHERE file_id = ?", (fid,))],
                     "held": None, "sidecars": []}
            try:
                side = fileops._sidecars(path)
                if how == "system" and not no_recycle_bin(path):
                    to_system_trash(path)
                    for sc in side:
                        s = os.stat(sc)
                        try:
                            to_system_trash(sc)
                            entry["sidecars"].append([sc, None, s.st_size, s.st_mtime_ns])
                        except OSError:
                            pass
                else:
                    entry["how"] = "kurator"
                    root = holding_root(db, path)
                    if root not in batch_dirs:
                        batch_dirs[root] = os.path.join(root, stamp)
                        os.makedirs(batch_dirs[root], exist_ok=True)
                        _remember_root(db, root)
                    held = fileops._free_name(os.path.join(batch_dirs[root], os.path.basename(path)), set())
                    fileops.move_file(path, held)
                    entry["held"] = held
                    for sc in side:
                        sh = fileops._free_name(fileops._sidecar_target(path, sc, held), set())
                        try:
                            fileops.move_file(sc, sh)
                            entry["sidecars"].append([sc, sh, 0, 0])
                        except OSError:
                            pass
            except Exception as e:  # noqa: BLE001 - report per file, go on with the rest
                failed.append({"id": fid, "path": path, "error": f"{type(e).__name__}: {e}"})
                continue
            with db.write_lock:
                c = db.cat
                c.execute("BEGIN")
                db._orphan_tags_where(c, "f.id = ?", (fid,))   # a restore from the trash gets them back
                c.execute("DELETE FROM files WHERE id = ?", (fid,))
                c.execute("COMMIT")
            done.append(entry)
    for root, d in batch_dirs.items():   # what is where, should the catalog be lost
        try:
            with open(os.path.join(d, "kurator-trash.json"), "w", encoding="utf-8") as f:
                json.dump([{"from": e["path"], "held": e["held"]} for e in done if e["held"] and e["held"].startswith(d)],
                          f, ensure_ascii=False, indent=1)
        except OSError:
            pass
    op_id = None
    if done:
        data = {"entries": done, **(extra or {})}
        with db.write_lock:
            op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                   (time.time(), "delete", json.dumps(data))).lastrowid
    scanner._bump()
    return {"op_id": op_id, "deleted": len(done), "failed": failed, "mode": how,
            "held_instead": sum(1 for e in done if how == "system" and e["held"]),   # no Recycle Bin on that drive
            "ids": [e["id"] for e in done]}


def _put_back_row(db: Database, e: dict) -> int:
    """The catalog entry as it was (same id when free), with its tags and info."""
    c = db.cat
    row = dict(e["row"])
    cur = c.execute("SELECT id FROM files WHERE path = ?", (e["path"],)).fetchone()
    cols = [r[1] for r in c.execute("PRAGMA table_info(files)")]
    with db.write_lock:
        c.execute("BEGIN")
        try:
            if cur:                                  # the scan found it first: keep that row
                fid = cur[0]
            else:
                if c.execute("SELECT 1 FROM files WHERE id = ?", (row["id"],)).fetchone():
                    row.pop("id")
                if not c.execute("SELECT 1 FROM roots WHERE id = ?", (row.get("root_id"),)).fetchone():
                    r = db.root_for(e["path"], None)
                    if r is None:
                        c.execute("ROLLBACK")
                        return 0
                    row["root_id"] = r["id"]
                if row.get("stack_id") and not c.execute("SELECT 1 FROM stacks WHERE id = ?", (row["stack_id"],)).fetchone():
                    row["stack_id"] = None
                try:
                    st = os.stat(e["path"])
                    row["size"], row["mtime_ns"] = st.st_size, st.st_mtime_ns
                except OSError:
                    pass
                keys = [k for k in row if k in cols]
                fid = c.execute(f"INSERT INTO files({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                                [row[k] for k in keys]).lastrowid
            for tag_id, source, conf, added in e.get("tags", []):
                if c.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) VALUES (?, ?, ?, ?, ?)",
                              (fid, tag_id, source, conf, added))
                    if row.get("hash"):
                        c.execute("DELETE FROM orphan_tags WHERE hash = ? AND tag_id = ?", (row["hash"], tag_id))
            for source, author, licence, updated in e.get("info", []):
                c.execute("INSERT OR IGNORE INTO file_info(file_id, source, author, licence, updated) VALUES (?, ?, ?, ?, ?)",
                          (fid, source, author, licence, updated))
                if row.get("hash"):
                    c.execute("DELETE FROM orphan_info WHERE hash = ?", (row["hash"],))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return fid


def undo_delete(db: Database, scanner, data: dict) -> dict:
    restored, failed = 0, []
    with scanner.fs_lock:
        for e in reversed(data.get("entries", [])):
            path = e["path"]
            try:
                if os.path.lexists(path):
                    raise OpError("a file of that name is there again")
                if e["held"]:
                    if not os.path.isfile(e["held"]):
                        raise OpError("no longer in Kurator's trash (emptied?)")
                    fileops.move_file(e["held"], path)
                elif not restore_system(path, e["size"], e["mtime_ns"], e["at"]):
                    raise OpError("not found in the system trash - restore it there; its tags come back by themselves")
                for orig, held, size, mt in e.get("sidecars", []):
                    if os.path.lexists(orig):
                        continue
                    try:
                        if held:
                            fileops.move_file(held, orig)
                        else:
                            restore_system(orig, size, mt, e["at"])
                    except OSError:
                        pass
                _put_back_row(db, e)
                restored += 1
            except Exception as ex:  # noqa: BLE001
                failed.append({"path": path, "error": str(ex)})
    for kept, tag_id in data.get("merged", []):   # tags the kept duplicates got from the removed ones
        with db.write_lock:
            db.cat.execute("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?", (kept, tag_id))
    scanner._bump()
    return {"restored": restored, "failed": failed}
