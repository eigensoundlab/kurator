"""Live updates: watch library folders and re-check only the folders where something changed.

New, edited, renamed, moved or deleted images show up in the catalog within a few seconds,
without rescanning the library. Events are collected for a short moment (debounce), so copying
1,000 files results in one small update, not 1,000.

Network shares and some file systems don't report changes; "Rescan" still works there.
"""

from __future__ import annotations

import os
import threading
import time

from .db import Database
from .handlers import handler_for

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # optional at runtime: the app works without live updates
    Observer = None
    FileSystemEventHandler = object

DEBOUNCE = 1.5   # seconds of quiet before applying changes
MAX_DELAY = 6.0  # apply at least this often during long copy operations


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "Watcher"):
        self.w = watcher

    def on_any_event(self, event):
        if event.event_type in ("opened", "closed_no_write"):
            return
        paths = [event.src_path]
        dest = getattr(event, "dest_path", "")
        if dest:
            paths.append(dest)
        for p in paths:
            p = os.fsdecode(p)
            if event.is_directory:
                if event.event_type in ("created", "deleted", "moved"):
                    self.w.mark(p, recursive=True)
                    self.w.mark(os.path.dirname(p), recursive=False)
            elif handler_for(os.path.splitext(p)[1]) is not None:
                self.w.mark(os.path.dirname(p), recursive=False)


class Watcher:
    def __init__(self, db: Database, scanner):
        self.db = db
        self.scanner = scanner
        self.enabled = Observer is not None
        self._observer = None
        self._watches: dict[str, object] = {}   # root path -> watch handle
        self._lock = threading.Lock()
        self._dirty: dict[str, bool] = {}       # folder -> recursive?
        self._first = 0.0
        self._timer: threading.Timer | None = None
        self.errors: dict[str, str] = {}
        self.updates = 0
        data_dir = os.path.normcase(os.path.abspath(db.data_dir))
        self._ignore = data_dir + os.sep

    def start(self) -> None:
        if not self.enabled:
            return
        self._observer = Observer()
        self._observer.daemon = True
        self._observer.start()
        self.sync()

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    def status(self) -> dict:
        return {"enabled": self.enabled and self._observer is not None, "watching": len(self._watches),
                "errors": self.errors, "updates": self.updates}

    def sync(self) -> None:
        """Watch exactly the current roots (call after roots are added or removed)."""
        if not self._observer:
            return
        want = {r["path"]: r["kind"] == "library" for r in self.db.roots() if os.path.isdir(r["path"])}
        with self._lock:
            for path in list(self._watches):
                if path not in want:
                    try:
                        self._observer.unschedule(self._watches.pop(path))
                    except Exception:
                        pass
            for path, recursive in want.items():
                if path in self._watches:
                    continue
                try:
                    self._watches[path] = self._observer.schedule(_Handler(self), path, recursive=recursive)
                    self.errors.pop(path, None)
                except Exception as e:  # e.g. Linux inotify watch limit, permissions, network share
                    self.errors[path] = f"{type(e).__name__}: {e}"

    # -- event collection ----------------------------------------------------
    def mark(self, folder: str, recursive: bool) -> None:
        folder = os.path.normpath(folder)
        if os.path.normcase(folder + os.sep).startswith(self._ignore):
            return
        with self._lock:
            self._dirty[folder] = self._dirty.get(folder, False) or recursive
            now = time.monotonic()
            if not self._first:
                self._first = now
            if self._timer:
                self._timer.cancel()
            delay = DEBOUNCE if now - self._first < MAX_DELAY else 0.05
            self._timer = threading.Timer(delay, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            dirty, self._dirty = self._dirty, {}
            self._first = 0.0
            self._timer = None
        if not dirty:
            return
        # A recursive entry covers everything below it.
        rec = sorted((d for d, r in dirty.items() if r), key=len)
        covered = []
        for d in rec:
            if not any(d == c or d.startswith(c.rstrip(os.sep) + os.sep) for c in covered):
                covered.append(d)
        jobs = [(d, True) for d in covered]
        for d, r in dirty.items():
            if not r and not any(d == c or d.startswith(c.rstrip(os.sep) + os.sep) for c in covered):
                jobs.append((d, False))
        changed = False
        for folder, recursive in jobs:
            root = self.db.root_for(folder, None)
            if root is None or not os.path.isdir(root["path"]):
                continue
            if root["kind"] != "library" and os.path.normcase(folder) != os.path.normcase(root["path"]):
                continue  # browse folders are shallow: ignore their subfolders
            try:
                self.scanner.walk_folder(root["id"], folder, recursive and root["kind"] == "library")
                changed = True
            except Exception as e:
                self.errors[folder] = f"{type(e).__name__}: {e}"
        if changed:
            self.updates += 1
            self.scanner.request_process()
