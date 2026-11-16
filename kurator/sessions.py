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

More takes of a session (a later zip with the same session id) go into the folder of its first zip,
while that folder is still in the library: takes already there are skipped, a different file with a
taken name gets a free one ("Take 1 2.wav").

On the same computer Ballistic Foley Lab can hand a session straight to Kurator: it opens Kurator's
/receive page and posts the zip to it (window to window, as it does with SFX Workbench), and that page
- Kurator's own origin - uploads it here. receive() unpacks it into the folder chosen in Settings
(default: "Ballistic Foley Lab" in the first library folder). Only pages from the Ballistic Foley Lab
address in Settings get an answer from /receive.

Kurator Capture (a phone app for sounds, photos and textures) writes the same kind of session with
kurator-session.json (format "kurator-session"): any file Kurator can show, each with its .xmp sidecar.
Its Google Drive backup keeps each session as a folder with the files and that list already in place;
when Google Drive for desktop syncs it into a library folder, adopt() files it where it is: the new
files get their tags from their sidecars and count against the plan. Nothing there is moved or changed.

Any other zip is left alone. Settings → Recording sessions turns unpacking off.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime

from . import plans, xmp
from .db import Database
from .handlers import handler_for

MANIFEST = "ballistic-session.json"
FORMAT = "ballistic-session"
# the lists a session can carry: Ballistic Foley Lab's and Kurator Capture's (same fields; Capture adds "kind")
MANIFESTS = {"ballistic-session.json": "ballistic-session", "kurator-session.json": "kurator-session"}
MAX_FILES = 2000
MAX_FILE = 2 << 30          # one take: 2 GB
MAX_TOTAL = 16 << 30        # a session: 16 GB
_inbox = {"n": 0, "last": None, "sounds": 0, "folder": None}
_lock = threading.Lock()
_unpacking = threading.Lock()   # the watcher's thread and a session sent from the page, one at a time
INBOX_NAME = "Ballistic Foley Lab"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class ReceiveError(ValueError):
    pass


def enabled(db: Database) -> bool:
    return bool(db.get_setting("sessions_unpack", True))


def _parse(raw: bytes, fmt: str) -> dict | None:
    try:
        m = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(m, dict) or m.get("format") != fmt or m.get("version") != 1 or not isinstance(m.get("files"), list):
        return None
    return m


def _manifest(z: zipfile.ZipFile) -> tuple[dict, bytes, str] | None:
    """The session's list in a zip: (list, its bytes, its file name), or None."""
    for name, fmt in MANIFESTS.items():
        try:
            info = z.getinfo(name)
        except KeyError:
            continue
        if info.file_size > 4 << 20:
            return None
        raw = z.read(info)
        m = _parse(raw, fmt)
        return (m, raw, name) if m is not None else None
    return None


def is_session(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return _manifest(z) is not None
    except (OSError, zipfile.BadZipFile):
        return False


def _media(name: str) -> bool:
    """A file Kurator shows (a sound, a picture...): what a session may bring besides sidecars."""
    return handler_for(os.path.splitext(name)[1].lower()) is not None and not name.lower().endswith(".xmp")


def _plain(name) -> str | None:
    """A file name the list may name: no folders, no tricks, a file Kurator shows or an .xmp sidecar."""
    if not isinstance(name, str) or not name or name != os.path.basename(name) or "/" in name or "\\" in name:
        return None
    if name in (".", "..") or name.startswith(".") or "\x00" in name:
        return None
    return name if name.lower().endswith(".xmp") or _media(name) else None


def _free(folder: str) -> str:
    out, k = folder, 2
    while os.path.exists(out):
        out = f"{folder} {k}"
        k += 1
    return out


def _free_wav(path: str) -> str:
    """A free name for a file ("Take 1 2.wav", "Bark 2.jpg"), with a free sidecar name too."""
    stem, ext = os.path.splitext(path)
    k = 2
    while True:
        out = f"{stem} {k}{ext}"
        if not os.path.exists(out) and not os.path.exists(out + ".xmp"):
            return out
        k += 1


def _same(path: str, z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    """Is the file at path the zip member, byte for byte?"""
    try:
        if os.path.getsize(path) != info.file_size:
            return False
        with open(path, "rb") as a, z.open(info) as b:
            while True:
                x, y = a.read(1 << 20), b.read(1 << 20)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def _copy_out(z: zipfile.ZipFile, info: zipfile.ZipInfo, dest: str) -> None:
    tmp = dest + ".part"
    with z.open(info) as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    os.replace(tmp, dest)


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


def _count(db: Database, items: list[tuple[str, str, int, str]]) -> None:
    """Takes against their shots: [(plan id, shot id, file id, capture id or "")]. A capture with an id
    (Kurator Capture gives each one) counts once, however many ways it arrived - a zip and a backup of
    the same session, say - while the file it first came as is still here."""
    caps = db.get_setting("plan_captures", {})
    caps = caps if isinstance(caps, dict) else {}
    by_plan: dict[str, dict[str, list[int]]] = {}
    for pid, sid, fid, cap in items:
        if cap:
            old = caps.get(cap)
            if old and old != fid and db.cat.execute("SELECT 1 FROM files WHERE id = ?", (old,)).fetchone():
                continue
            caps[cap] = fid
        by_plan.setdefault(pid, {}).setdefault(sid, []).append(fid)
    for pid, by_shot in by_plan.items():
        plans.add_takes(db, pid, by_shot)
    if len(caps) > 20000:
        caps = dict(list(caps.items())[-20000:])
    db.set_setting("plan_captures", caps)


def _cap(e: dict) -> str:
    c = e.get("capture")
    return c if isinstance(c, str) and 0 < len(c) <= 100 else ""


def _session_id(m: dict) -> str:
    sid = m.get("session")
    return sid if isinstance(sid, str) and 0 < len(sid) <= 100 else ""


def _earlier_folder(db: Database, done: dict, sid: str) -> str | None:
    """The folder an earlier zip of this session went into, while it is still there and in the library."""
    if not sid:
        return None
    for v in sorted(done.values(), key=lambda v: v.get("at", 0), reverse=True):
        f = v.get("folder")
        if v.get("session") == sid and isinstance(f, str) and os.path.isdir(f) and db.root_for(os.path.normpath(f), "library"):
            return f
    return None


def unpack(db: Database, scanner, path: str, into: str | None = None, discard: str | None = "trash") -> dict | None:
    """Unpack one session zip; None when it is not one, or was unpacked before.

    into: the folder to make the session's folder in (default: next to the zip). discard: what then
    happens to the zip - "trash" (the system trash), "delete" (a copy Kurator made) or None (it stays)."""
    with _unpacking:
        return _unpack(db, scanner, path, into, discard)


def _unpack(db: Database, scanner, path: str, into: str | None, discard: str | None) -> dict | None:
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None                                   # not a zip, or still being written: the next event retries
    with z:
        got = _manifest(z)
        if got is None:
            return None
        m, raw, list_name = got
        key = hashlib.sha256(raw).hexdigest()[:32]
        done = _done(db)
        if key in done:
            return None
        names = {i.filename: i for i in z.infolist()}
        wanted: list[tuple[str, zipfile.ZipInfo, zipfile.ZipInfo | None, dict]] = []   # take, its sidecar, its entry
        total = 0
        for e in m["files"][:MAX_FILES]:
            if not isinstance(e, dict):
                continue
            n = _plain(e.get("file"))
            if not (n and _media(n) and n in names and names[n].file_size <= MAX_FILE):
                continue
            sn = _plain(e.get("sidecar"))
            side = names.get(sn) if sn and sn.lower().endswith(".xmp") and names.get(sn) and names[sn].file_size <= MAX_FILE else None
            total += names[n].file_size + (side.file_size if side else 0)
            wanted.append((n, names[n], side, e))
        if not wanted or total > MAX_TOTAL:
            return None
        sid = _session_id(m)
        folder = _earlier_folder(db, done, sid)
        more = folder is not None
        if folder is None:                            # made with the first file written
            stem = os.path.splitext(os.path.basename(path))[0]
            folder = _free(os.path.join(into, stem) if into else os.path.splitext(path)[0])
        written: dict[str, dict] = {}
        skipped = 0
        seen_in: str | None = None                    # where skipped takes are, when not here
        homes: dict[str, str | None] = {}
        for n, info, side, e in wanted:
            # a take already in Kurator from the session it was recorded in (sent from Your sounds later)
            own = _session_id(e)
            if own and own != sid:
                if own not in homes:
                    homes[own] = _earlier_folder(db, done, own)
                home = homes[own]
                if home and home != folder and os.path.exists(os.path.join(home, n)) and _same(os.path.join(home, n), z, info):
                    skipped += 1
                    seen_in = home
                    continue
            dest = os.path.join(folder, n)
            if os.path.exists(dest):
                if _same(dest, z, info):
                    skipped += 1                      # this take came with an earlier zip of the session
                    continue
                dest = _free_wav(dest)
            elif side is not None and os.path.exists(dest + ".xmp"):
                dest = _free_wav(dest)
            os.makedirs(folder, exist_ok=True)
            _copy_out(z, info, dest)
            if side is not None:
                _copy_out(z, side, dest + ".xmp")
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
        made = os.path.isdir(folder)
        try:                                          # the latest list of the session (Kurator's own file)
            if not made:
                raise OSError("nothing new: no folder")
            tmp = os.path.join(folder, list_name + ".part")
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, os.path.join(folder, list_name))
        except OSError:
            pass
    root = db.root_for(os.path.normpath(folder), "library") if made else None
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
    # a texture's source photo goes with its texture: one take, not two
    _count(db, [(str(e["plan"]), str(e["shot"]), ids[dest], _cap(e)) for dest, e in written.items()
                if dest in ids and e.get("plan") and e.get("shot") and e.get("role") != "source"])
    # the zip itself: to the trash (or, Kurator's own copy, deleted), remembered either way
    trashed = False
    try:
        if discard == "trash":
            from .trash import to_system_trash
            to_system_trash(path)
            trashed = True
        elif discard == "delete":
            os.remove(path)
    except Exception:  # noqa: BLE001 - no Send2Trash, a removable drive...: it stays, and is not unpacked again
        pass
    done[key] = {"zip": os.path.basename(path), "folder": folder if made else "", "files": len(written), "at": time.time(),
                 "trashed": trashed, "session": sid}
    if len(done) > 2000:
        for k in sorted(done, key=lambda k: done[k].get("at", 0))[: len(done) - 2000]:
            done.pop(k)
    db.set_setting("sessions_done", done)
    shown = folder if made else (seen_in or folder)
    if written:
        with _lock:
            _inbox.update(n=_inbox["n"] + 1, last=os.path.basename(shown), sounds=len(written), folder=shown)
    return {"folder": shown, "files": len(written), "skipped": skipped, "more": more, "tagged": len(ids), "trashed": trashed,
            "plan": plan.get("name") if plan else None}


# ---------------------------------------------------------------- session folders already in place
def folder_list(folder: str) -> tuple[dict, str] | None:
    """The session list of a folder whose files are already there (a Kurator Capture backup): (list, path)."""
    for name, fmt in MANIFESTS.items():
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > 4 << 20:
                continue
            with open(path, "rb") as f:
                m = _parse(f.read(), fmt)
        except OSError:
            continue
        if m is not None:
            return m, path
    return None


def adopt(db: Database, scanner, folder: str) -> dict | None:
    """File a session's folder where it lies: each file it lists that is there and new here gets its tags
    from its sidecar and counts against the plan. Nothing is moved, renamed or changed - a backup folder
    synced from Google Drive stays exactly as the backup made it. None when there is nothing new."""
    with _unpacking:
        return _adopt(db, scanner, os.path.normpath(folder))


def _adopt(db: Database, scanner, folder: str) -> dict | None:
    got = folder_list(folder)
    root = db.root_for(folder, "library")
    if got is None or root is None:
        return None
    m, _ = got
    done = _done(db)
    if any(os.path.normpath(v.get("folder") or "") == folder for v in done.values()):
        return None                                   # a folder Kurator unpacked itself: already filed
    seen = db.get_setting("sessions_adopted", {})
    seen = seen if isinstance(seen, dict) else {}
    had = seen.get(folder) if isinstance(seen.get(folder), dict) else {}
    new: dict[str, dict] = {}
    for e in m["files"][:MAX_FILES]:
        n = _plain(e.get("file")) if isinstance(e, dict) else None
        if n and _media(n) and n not in had and os.path.isfile(os.path.join(folder, n)):
            new[os.path.join(folder, n)] = e
    if not new:
        return None
    scanner.walk_folder(root["id"], folder, False)
    q = ",".join("?" * len(new))
    ids = {p: fid for fid, p in db.cat.execute(f"SELECT id, path FROM files WHERE path IN ({q})", list(new)).fetchall()}
    if not ids:
        return None                                   # not scanned yet (still syncing): the next look files them
    xmp.import_keywords(db, list(ids.values()))
    scanner.request_process()
    plan = m.get("plan") if isinstance(m.get("plan"), dict) else None
    if plan:
        try:
            if plans.get(db, str(plan.get("id"))) is None:
                plans.save(db, plan)
        except plans.PlanError:
            plan = None
    _count(db, [(str(new[p]["plan"]), str(new[p]["shot"]), fid, _cap(new[p])) for p, fid in ids.items()
                if new[p].get("plan") and new[p].get("shot") and new[p].get("role") != "source"])
    now = time.time()
    had = {**had, **{os.path.basename(p): now for p in ids}}
    seen[folder] = had
    if len(seen) > 2000:
        for k in sorted(seen, key=lambda k: max(seen[k].values() or [0]))[: len(seen) - 2000]:
            seen.pop(k)
    db.set_setting("sessions_adopted", seen)
    with _lock:
        _inbox.update(n=_inbox["n"] + 1, last=os.path.basename(folder), sounds=len(ids), folder=folder)
    return {"folder": folder, "files": len(ids), "plan": plan.get("name") if plan else None}


# ---------------------------------------------------------------- straight from Ballistic Foley Lab
def receiving(db: Database) -> bool:
    return bool(db.get_setting("ballistic_receive", True))


def inbox_folder(db: Database) -> str | None:
    """Where sessions sent from Ballistic Foley Lab on this computer go: the folder in Settings while it
    is in the library, else "Ballistic Foley Lab" in the first library folder (None without one)."""
    p = db.get_setting("ballistic_inbox", None)
    if isinstance(p, str) and p and db.root_for(os.path.normpath(p), "library") is not None:
        return p
    roots = db.roots("library")
    return os.path.join(roots[0]["path"], INBOX_NAME) if roots else None


def zip_stem(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    if base.lower().endswith(".zip"):
        base = base[:-4]
    base = _UNSAFE.sub("-", base).strip().strip(".")[:100]
    return base or "Ballistic session"


def receive(db: Database, scanner, tmp: str, name: str) -> dict:
    """A session zip sent from the page (in `tmp`, outside the library): unpacked into the inbox folder."""
    if not receiving(db):
        raise ReceiveError("Kurator does not take sessions from Ballistic Foley Lab: switch it on in Settings → Recording sessions")
    folder = inbox_folder(db)
    if folder is None:
        raise ReceiveError("Add a library folder in Kurator first: the takes go into the library")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise ReceiveError(f"Can't use {folder}: {e.strerror or e}") from None
    with tempfile.TemporaryDirectory(prefix="kurator-session-") as d:
        p = os.path.join(d, zip_stem(name) + ".zip")   # the session folder is named after the zip
        shutil.move(tmp, p)
        if not is_session(p):
            raise ReceiveError("This is not a recording session from Ballistic Foley Lab")
        r = unpack(db, scanner, p, into=folder, discard="delete")
    if r is None:
        return {"folder": None, "files": 0, "skipped": 0, "already": True}
    return {**r, "already": False}


class Unpacker:
    """Zips and session lists seen by the scanner or the folder watcher are checked here, one at a time,
    off their thread."""

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
                if os.path.basename(p).lower() in MANIFESTS:
                    time.sleep(0.5)                       # the files of a backup may still be arriving
                    adopt(self.db, self.scanner, os.path.dirname(p))
                elif os.path.isfile(p) and is_session(p):
                    time.sleep(0.5)                       # a sync tool may still be finishing it
                    unpack(self.db, self.scanner, p)
            except Exception as e:  # noqa: BLE001 - one bad zip must not stop the next
                self.last_error = f"{os.path.basename(p)}: {type(e).__name__}: {e}"
                self._seen.pop(p, None)


def inbox() -> dict:
    with _lock:
        return dict(_inbox)
