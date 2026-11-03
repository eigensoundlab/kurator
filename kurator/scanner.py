"""Background indexing: walk library folders, then process new/changed files in parallel."""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

from . import dates
from .db import Database
from .handlers import handler_for
from .processing import GRID_SIZE, process_file

SKIP_DIRS = {"$recycle.bin", "system volume information", "__pycache__", "node_modules", ".git", ".svn"}
BATCH = 500


def _norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p))


class Scanner:
    def __init__(self, db: Database, workers: int | None = None):
        self.db = db
        self.workers = workers or max(1, (os.cpu_count() or 2) - 1)
        self._thread: threading.Thread | None = None
        self._again = threading.Event()          # a full rescan was requested while running
        self._pending_again = threading.Event()  # new pending files (from the watcher) while running
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Held while walking a folder and while Kurator moves files, so they never interleave.
        self.fs_lock = threading.RLock()
        self._last_scan_id = 0
        self.state = {"phase": "idle", "found": 0, "pending": 0, "processed": 0, "errors": 0,
                      "rate": 0.0, "started": None, "finished": None, "version": 0}

    # -- public ------------------------------------------------------------
    def request_scan(self) -> None:
        """Full rescan: walk every root (cheap for unchanged files) and process what's new/changed."""
        self._start(full=True)

    def request_process(self) -> None:
        """Only process files already marked pending (used by the folder watcher)."""
        self._start(full=False)

    def _start(self, full: bool) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                (self._again if full else self._pending_again).set()
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(full,), name="kurator-scan", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def status(self) -> dict:
        return dict(self.state)

    # -- internals ---------------------------------------------------------
    def _set(self, **kw) -> None:
        self.state.update(kw)

    def _bump(self) -> None:
        self.state["version"] += 1

    def _run(self, full: bool = True) -> None:
        try:
            while True:
                full = full or self._again.is_set()
                self._again.clear()
                self._pending_again.clear()
                self._set(processed=0, errors=0, rate=0.0, started=time.time(), finished=None)
                if full:
                    self._set(phase="walking", found=0)
                    for root in self.db.roots():
                        if self._stop.is_set():
                            return
                        if not os.path.isdir(root["path"]):
                            continue  # disk unplugged / folder gone: keep the catalog as it is
                        self._walk_root(root["id"], root["path"], recursive=root["kind"] == "library")
                    self._bump()
                self._process_pending()
                self._set(phase="idle", finished=time.time())
                self._bump()
                if self._stop.is_set() or not (self._again.is_set() or self._pending_again.is_set()):
                    break
                full = False
        except Exception as e:  # keep the app alive, surface the error
            self._set(phase="error", error=f"{type(e).__name__}: {e}")

    def walk_root(self, root_id: int, root: str, recursive: bool = True) -> None:
        """Public: bring the catalog of one root in sync with the disk (fast, no decoding)."""
        self._walk_root(root_id, root, recursive)
        self._bump()

    def _walk_root(self, root_id: int, root: str, recursive: bool = True) -> None:
        with self.fs_lock:
            self._walk_scope(root_id, _norm(root), recursive)

    def walk_folder(self, root_id: int, folder: str, recursive: bool) -> None:
        """Sync just one folder (or one subtree) of a root - used for live updates."""
        with self.fs_lock:
            self._walk_scope(root_id, _norm(folder), recursive)
        self._bump()

    @staticmethod
    def _scope_sql(top: str, recursive: bool) -> tuple[str, tuple]:
        if not recursive:
            return "dir = ?", (top,)
        base = top.rstrip(os.sep)
        return "(dir = ? OR (dir >= ? AND dir < ?))", (top, base + os.sep, base + chr(ord(os.sep) + 1))

    def _walk_scope(self, root_id: int, top: str, recursive: bool) -> None:
        """Compare one folder (and optionally everything below it) with the catalog.
        Unchanged files (same size + modification time) cost one stat call and nothing else."""
        db, cat = self.db, self.db.cat
        root = top
        # Unique, increasing marker for "seen in this walk" (two walks may start in the same ms).
        scan_id = max(int(time.time() * 1000) & 0x7FFFFFFF, self._last_scan_id + 1)
        self._last_scan_id = scan_id
        scope, scope_args = self._scope_sql(top, recursive)
        existing = {r[0]: (r[1], r[2]) for r in cat.execute(
            f"SELECT path, size, mtime_ns FROM files WHERE root_id = ? AND {scope}", (root_id, *scope_args))}
        data_dir = _norm(db.data_dir)
        new_rows, changed_rows, seen = [], [], []

        def flush():
            if not (new_rows or changed_rows or seen):
                return
            with db.write_lock:
                cat.execute("BEGIN")
                cat.executemany(
                    "INSERT OR IGNORE INTO files(root_id, path, dir, name, ext, kind, size, mtime_ns, "
                    "date_ts, date_source, status, seen) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)", new_rows)
                cat.executemany(
                    "UPDATE files SET size=?, mtime_ns=?, status=0, seen=? WHERE path=?", changed_rows)
                cat.executemany("UPDATE files SET seen=? WHERE path=?", [(scan_id, p) for p in seen])
                cat.execute("COMMIT")
            new_rows.clear(); changed_rows.clear(); seen.clear()
            self._bump()

        stack = [root]
        while stack:
            if self._stop.is_set():
                return
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not recursive:
                                continue
                            low = e.name.lower()
                            if low.startswith(".") or low in SKIP_DIRS or _norm(e.path) == data_dir:
                                continue
                            stack.append(e.path)
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        ext = os.path.splitext(e.name)[1].lower()
                        h = handler_for(ext)
                        if h is None:
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    path = _norm(e.path)
                    self.state["found"] += 1
                    prev = existing.get(path)
                    if prev is None:
                        ts, src = dates.resolve_date({}, e.name, st)
                        new_rows.append((root_id, path, os.path.dirname(path), e.name, ext, h.kind,
                                         st.st_size, st.st_mtime_ns, ts, src, scan_id))
                    elif prev != (st.st_size, st.st_mtime_ns):
                        changed_rows.append((st.st_size, st.st_mtime_ns, scan_id, path))
                    else:
                        seen.append(path)
                    if len(new_rows) + len(changed_rows) + len(seen) >= BATCH * 4:
                        flush()
        flush()
        if self._stop.is_set():
            return
        # Files that disappeared leave the catalog. Their tags are parked by content hash and come
        # back automatically if the file shows up elsewhere (moved/renamed outside Kurator).
        fscope = scope.replace("dir", "f.dir")
        with db.write_lock:
            cat.execute("BEGIN")
            db._orphan_tags_where(cat, f"f.root_id = ? AND f.seen != ? AND {fscope}", (root_id, scan_id, *scope_args))
            cat.execute(f"DELETE FROM files WHERE root_id = ? AND seen != ? AND {scope}", (root_id, scan_id, *scope_args))
            cat.execute("COMMIT")

    def _process_pending(self) -> None:
        cat = self.db.cat
        todo = [r[0] for r in cat.execute("SELECT path FROM files WHERE status = 0 ORDER BY dir, name")]
        self._set(phase="processing", pending=len(todo), processed=0)
        if not todo:
            return
        self._t0 = time.time()
        remaining = list(todo)
        try:
            # Processes: true parallelism for decoding/encoding. "spawn" behaves the same on every OS.
            with ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn")) as pool:
                remaining = self._run_pool(pool, remaining)
        except (BrokenProcessPool, OSError) as e:
            # e.g. a worker killed by the OS on a gigantic file, or process creation not allowed.
            self._set(warning=f"process pool failed ({type(e).__name__}); continuing in threads")
            remaining = getattr(self, "_leftover", remaining)
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                self._run_pool(pool, remaining)

    def _run_pool(self, pool, paths: list[str]) -> list[str]:
        """Process paths; returns the ones not finished (empty unless stopped)."""
        pending = list(reversed(paths))
        in_flight: dict = {}
        batch: list[dict] = []
        last_flush = time.time()

        def flush():
            nonlocal last_flush
            if batch:
                self.db.store_results(batch)
                batch.clear()
            last_flush = time.time()
            self._set(rate=round(self.state["processed"] / max(time.time() - self._t0, 1e-6), 1))
            self._bump()

        try:
            while pending or in_flight:
                if self._stop.is_set():
                    break
                while pending and len(in_flight) < self.workers * 4:
                    p = pending.pop()
                    in_flight[pool.submit(process_file, p, (GRID_SIZE,))] = p
                done, _ = wait(list(in_flight), timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    p = in_flight.pop(f)
                    try:
                        r = f.result()
                    except BrokenProcessPool:
                        pending.append(p)
                        raise
                    except Exception as e:
                        r = {"path": p, "error": f"worker: {type(e).__name__}: {e}"}
                    if "error" in r:
                        self.state["errors"] += 1
                    batch.append(r)
                    self.state["processed"] += 1
                if len(batch) >= 200 or (batch and time.time() - last_flush > 1.0):
                    flush()
        except BrokenProcessPool:
            flush()
            # put back everything that didn't finish and let the caller fall back
            self._leftover = pending + list(in_flight.values())
            raise BrokenProcessPool(f"{len(self._leftover)} files left") from None
        flush()
        return pending + list(in_flight.values())
