"""Recording sessions from Ballistic Foley Lab: zips that arrive in a library folder are unpacked and tagged.

On a phone, Ballistic Foley Lab's Record view saves a session as one zip: the takes as WAV (with bext,
iXML and XMP inside), a .xmp sidecar each, and ballistic-session.json - the plan and, per file, its shot,
take, tags, notes and place. Saved into a folder the computer syncs (Syncthing, iCloud Drive, Google
Drive, OneDrive) that is in the library, it is noticed by the folder watcher or the next scan, and:

  * unpacked into a new folder next to it, named like the zip - only the files the list names, by their
    plain names, never over anything;
  * each take gets the date it was recorded as its modified time, and its keywords as tags in the right
    groups (subjects, project, status, source), read from its sidecar and its own XMP;
  * takes from a plan are counted against their shots (plans.py), and a plan Kurator does not know is
    added;
  * the zip goes to the system trash. Where that is not possible it stays, and is not unpacked again:
    each session is remembered by the hash of its list.

Any other zip is left alone. Settings → Recording sessions turns unpacking off.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
import zipfile
from datetime import datetime

from . import plans, xmp
from .db import Database

MANIFEST = "ballistic-session.json"
FORMAT = "ballistic-session"
MAX_FILES = 2000
MAX_FILE = 2 << 30          # one take: 2 GB
MAX_TOTAL = 16 << 30        # a session: 16 GB
_inbox = {"n": 0, "last": None, "sounds": 0, "folder": None}
_lock = threading.Lock()


def enabled(db: Database) -> bool:
    return bool(db.get_setting("sessions_unpack", True))


def _manifest(z: zipfile.ZipFile) -> tuple[dict, bytes] | None:
    try:
        info = z.getinfo(MANIFEST)
    except KeyError:
        return None
    if info.file_size > 4 << 20:
        return None
    raw = z.read(info)
    try:
        m = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(m, dict) or m.get("format") != FORMAT or m.get("version") != 1 or not isinstance(m.get("files"), list):
        return None
    return m, raw


def is_session(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return _manifest(z) is not None
    except (OSError, zipfile.BadZipFile):
        return False


def _plain(name) -> str | None:
    """A file name the list may name: no folders, no tricks, .wav or .xmp."""
    if not isinstance(name, str) or not name or name != os.path.basename(name) or "/" in name or "\\" in name:
        return None
    if name in (".", "..") or name.startswith(".") or "\x00" in name:
        return None
    return name if name.lower().endswith((".wav", ".wav.xmp", ".xmp")) else None


def _free(folder: str) -> str:
    out, k = folder, 2
    while os.path.exists(out):
        out = f"{folder} {k}"
        k += 1
    return out


def _ts(iso) -> float | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _done(db: Database) -> dict:
    v = db.get_setting("sessions_done", {})
    return v if isinstance(v, dict) else {}


def unpack(db: Database, scanner, path: str) -> dict | None:
    """Unpack one session zip; None when it is not one, or was unpacked before."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None                                   # not a zip, or still being written: the next event retries
    with z:
        got = _manifest(z)
        if got is None:
            return None
        m, raw = got
        key = hashlib.sha256(raw).hexdigest()[:32]
        done = _done(db)
        if key in done:
            return None
        names = {i.filename: i for i in z.infolist()}
        wanted: list[tuple[str, zipfile.ZipInfo, dict]] = []
        total = 0
        for e in m["files"][:MAX_FILES]:
            if not isinstance(e, dict):
                continue
            for k in ("file", "sidecar"):
                n = _plain(e.get(k))
                if n and n in names and names[n].file_size <= MAX_FILE:
                    total += names[n].file_size
                    wanted.append((n, names[n], e))
        if not wanted or total > MAX_TOTAL:
            return None
        folder = _free(os.path.splitext(path)[0])
        os.makedirs(folder)
        written: dict[str, dict] = {}
        for n, info, e in wanted:
            dest = os.path.join(folder, n)
            if os.path.exists(dest):
                continue
            tmp = dest + ".part"
            with z.open(info) as src, open(tmp, "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
            os.replace(tmp, dest)
            if n.lower().endswith(".wav"):
                written[dest] = e
        # the recording date as the modified time, so every app sees when it was made
        for dest, e in written.items():
            ts = _ts(e.get("date"))
            if ts:
                try:
                    os.utime(dest, (ts, ts))
                    for side in (dest + ".xmp",):
                        if os.path.exists(side):
                            os.utime(side, (ts, ts))
                except OSError:
                    pass
        try:
            with open(os.path.join(folder, MANIFEST), "wb") as f:
                f.write(raw)
        except OSError:
            pass
    root = db.root_for(os.path.normpath(folder), "library")
    ids: dict[str, int] = {}
    if root is not None:
        scanner.walk_folder(root["id"], folder, False)
        q = ",".join("?" * len(written))
        if written:
            for fid, p in db.cat.execute(f"SELECT id, path FROM files WHERE path IN ({q})", list(written)).fetchall():
                ids[p] = fid
        if ids:
            xmp.import_keywords(db, list(ids.values()))
        scanner.request_process()
    # takes against their plan's shots; a plan from the phone joins the plans here
    plan = m.get("plan") if isinstance(m.get("plan"), dict) else None
    if plan:
        try:
            if plans.get(db, str(plan.get("id"))) is None:
                plans.save(db, plan)
        except plans.PlanError:
            plan = None
    by_plan: dict[str, dict[str, list[int]]] = {}
    for dest, e in written.items():
        if dest in ids and e.get("plan") and e.get("shot"):
            by_plan.setdefault(str(e["plan"]), {}).setdefault(str(e["shot"]), []).append(ids[dest])
    for pid, by_shot in by_plan.items():
        plans.add_takes(db, pid, by_shot)
    # the zip itself: to the trash, remembered either way
    trashed = False
    try:
        from .trash import to_system_trash
        to_system_trash(path)
        trashed = True
    except Exception:  # noqa: BLE001 - no Send2Trash, a removable drive...: it stays, and is not unpacked again
        pass
    done[key] = {"zip": os.path.basename(path), "folder": folder, "files": len(written), "at": time.time(), "trashed": trashed}
    if len(done) > 2000:
        for k in sorted(done, key=lambda k: done[k].get("at", 0))[: len(done) - 2000]:
            done.pop(k)
    db.set_setting("sessions_done", done)
    with _lock:
        _inbox.update(n=_inbox["n"] + 1, last=os.path.basename(folder), sounds=len(written), folder=folder)
    return {"folder": folder, "files": len(written), "tagged": len(ids), "trashed": trashed,
            "plan": plan.get("name") if plan else None}


class Unpacker:
    """Zips seen by the scanner or the folder watcher are checked here, one at a time, off their thread."""

    def __init__(self, db: Database, scanner):
        self.db = db
        self.scanner = scanner
        self._q: queue.Queue[str] = queue.Queue()
        self._seen: dict[str, tuple[int, int]] = {}    # path -> (size, mtime_ns) already looked at
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    def offer(self, paths: list[str]) -> None:
        if not enabled(self.db):
            return
        for p in paths:
            if self.db.root_for(os.path.normpath(p), "library") is None:
                continue                                  # a folder only browsed: nothing is unpacked there
            try:
                st = os.stat(p)
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            if self._seen.get(p) == sig:
                continue
            self._seen[p] = sig
            self._q.put(p)
        if not self._q.empty() and not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name="kurator-sessions", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                p = self._q.get(timeout=1)
            except queue.Empty:
                return
            try:
                if os.path.isfile(p) and is_session(p):
                    time.sleep(0.5)                       # a sync tool may still be finishing it
                    unpack(self.db, self.scanner, p)
            except Exception as e:  # noqa: BLE001 - one bad zip must not stop the next
                self.last_error = f"{os.path.basename(p)}: {type(e).__name__}: {e}"
                self._seen.pop(p, None)


def inbox() -> dict:
    with _lock:
        return dict(_inbox)
