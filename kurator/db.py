"""SQLite catalog + packed thumbnail store.

catalog.db  - roots, files (path, dates, metadata...). Small, back it up together with sidecars.
thumbs.db   - WebP thumbnails as blobs keyed by (content hash, size). Disposable cache:
              one file instead of 100k tiny files (much faster on Windows/NTFS), and
              moved/renamed/duplicate files reuse the same thumbnails.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA_VERSION = 8

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    dir TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'image',
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    hash TEXT,
    width INTEGER,
    height INTEGER,
    color INTEGER,
    alpha INTEGER,
    date_ts REAL,
    date_source TEXT,
    meta TEXT,
    status INTEGER NOT NULL DEFAULT 0,   -- 0 pending, 1 done, 2 error
    error TEXT,
    seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS files_dir ON files(dir, name);
CREATE INDEX IF NOT EXISTS files_date ON files(date_ts);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE INDEX IF NOT EXISTS files_root ON files(root_id);
"""

# Each step brings the catalog from version i to i+1. Never edit a released step - add a new one.
MIGRATIONS = {
    1: [
        "ALTER TABLE roots ADD COLUMN kind TEXT NOT NULL DEFAULT 'library'",   # 'library' | 'browse'
        "ALTER TABLE roots ADD COLUMN last_used REAL",
        "ALTER TABLE files ADD COLUMN frames INTEGER",
        "UPDATE files SET frames = json_extract(meta, '$.frames') WHERE meta LIKE '%\"frames\"%'",
        """CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- hierarchy with '/', e.g. Creatures/Dragons
            color TEXT,
            hotkey INTEGER UNIQUE,                      -- 1..9
            created REAL
        )""",
        """CREATE TABLE file_tags (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            source TEXT NOT NULL DEFAULT 'manual',      -- 'manual' | 'ai'
            confidence REAL,
            added REAL,
            PRIMARY KEY (file_id, tag_id)
        ) WITHOUT ROWID""",
        "CREATE INDEX file_tags_tag ON file_tags(tag_id)",
        # Tags of files that vanished (moved/renamed outside Kurator). Re-attached by content hash.
        """CREATE TABLE orphan_tags (
            hash TEXT NOT NULL,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            source TEXT, confidence REAL, added REAL, orphaned REAL,
            PRIMARY KEY (hash, tag_id)
        ) WITHOUT ROWID""",
        """CREATE TABLE operations (
            id INTEGER PRIMARY KEY,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,
            data TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0
        )""",
    ],
    2: [
        # A backup set = what to back up (scope), how to lay it out, and where (one or more targets).
        """CREATE TABLE backup_sets (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            layout TEXT NOT NULL DEFAULT 'original',   -- 'original' | 'tags'
            tag_template TEXT NOT NULL DEFAULT '{tagpath}',
            policy TEXT NOT NULL DEFAULT 'fill',       -- 'fill' (in order) | 'free' (most free space)
            mode TEXT NOT NULL DEFAULT 'copy',         -- 'copy' (keep everything) | 'mirror' (archive removed)
            verify INTEGER NOT NULL DEFAULT 1,
            scope TEXT NOT NULL DEFAULT '{}',          -- JSON: {} = whole library, {"tags": [..]}, {"folders": [..]}
            created REAL, last_run REAL, last_result TEXT
        )""",
        """CREATE TABLE backup_targets (
            id INTEGER PRIMARY KEY,
            set_id INTEGER NOT NULL REFERENCES backup_sets(id) ON DELETE CASCADE,
            kind TEXT NOT NULL DEFAULT 'local',        -- 'local' now, 'rclone' next
            location TEXT NOT NULL,
            reserve INTEGER NOT NULL DEFAULT 1073741824,  -- bytes always left free
            position INTEGER NOT NULL DEFAULT 0
        )""",
        # Where each library file lives in a backup set. Not tied to files(id) by a foreign key:
        # entries stay when a file leaves the library, so we always know what the backup holds.
        """CREATE TABLE backup_files (
            set_id INTEGER NOT NULL REFERENCES backup_sets(id) ON DELETE CASCADE,
            file_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL REFERENCES backup_targets(id) ON DELETE CASCADE,
            dest TEXT NOT NULL,          -- path relative to the target
            src_path TEXT NOT NULL,      -- where the original was (for restore)
            hash TEXT, size INTEGER, mtime_ns INTEGER, birth REAL,
            date_ts REAL, date_source TEXT, tags TEXT,
            backed_up REAL,
            PRIMARY KEY (set_id, file_id)
        )""",
        "CREATE UNIQUE INDEX backup_files_dest ON backup_files(target_id, dest)",
        "CREATE INDEX backup_files_hash ON backup_files(set_id, hash)",
    ],
    3: [
        # Tag groups ("facets"): subject / project / status / source. Children follow their parent.
        "ALTER TABLE tags ADD COLUMN facet TEXT NOT NULL DEFAULT 'subject'",
        "ALTER TABLE tags ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX tags_facet ON tags(facet)",
    ],
    4: [
        # AI: what a tag looks like, in words (used by image-text models), e.g. "a robot or android"
        "ALTER TABLE tags ADD COLUMN ai_prompt TEXT",
        # A tag pool ("profile"): the AI may only choose from these tags.
        """CREATE TABLE ai_profiles (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            tag_ids TEXT NOT NULL,                  -- JSON list
            mode TEXT NOT NULL DEFAULT 'single',    -- 'single' (one of the pool) | 'multi'
            method TEXT NOT NULL DEFAULT 'zeroshot',-- 'zeroshot' | 'trained' | 'vlm'
            threshold REAL NOT NULL DEFAULT 0.5,
            vlm_model TEXT, captions INTEGER NOT NULL DEFAULT 0,
            trained TEXT,                           -- JSON: classifier weights + stats
            created REAL, last_run REAL, last_result TEXT
        )""",
        # Pending AI suggestions - not tags until you accept them.
        """CREATE TABLE suggestions (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            score REAL, profile_id INTEGER, method TEXT, created REAL,
            PRIMARY KEY (file_id, tag_id)
        ) WITHOUT ROWID""",
        "CREATE INDEX suggestions_tag ON suggestions(tag_id)",
        # Rejected suggestions are never suggested again and teach the classifier what a tag is NOT.
        """CREATE TABLE rejections (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            created REAL,
            PRIMARY KEY (file_id, tag_id)
        ) WITHOUT ROWID""",
        """CREATE TABLE captions (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            model TEXT NOT NULL, text TEXT NOT NULL, created REAL,
            PRIMARY KEY (file_id, model)
        ) WITHOUT ROWID""",
    ],
    5: [
        # Projects that include things automatically: a tag, a folder or another project (live).
        """CREATE TABLE project_sources (
            id INTEGER PRIMARY KEY,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,          -- 'tag' | 'folder' | 'project'
            ref TEXT NOT NULL,           -- tag id, or folder path
            created REAL
        )""",
        "CREATE INDEX project_sources_tag ON project_sources(tag_id)",
        # Thumbnail version: bumped when previews are made again for unchanged files (e.g. ffmpeg
        # was installed later), so browsers don't keep showing the old cached picture.
        "ALTER TABLE files ADD COLUMN tver INTEGER NOT NULL DEFAULT 0",
    ],
    6: [
        # Backups store identical files once: several catalog files may point to one backed-up copy.
        "DROP INDEX backup_files_dest",
        "CREATE INDEX backup_files_dest ON backup_files(target_id, dest)",
        "ALTER TABLE backup_sets ADD COLUMN dedupe INTEGER NOT NULL DEFAULT 1",
        # Saved views ("smart folders"): a name and the filter state of the grid
        """CREATE TABLE saved_views (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            query TEXT NOT NULL,          -- JSON: the grid's filters
            position INTEGER NOT NULL DEFAULT 0,
            created REAL
        )""",
    ],
    7: [
        # Perceptual hash (dHash, 64 bit) for finding similar - not identical - images
        "ALTER TABLE files ADD COLUMN phash INTEGER",
        # Version stacks: sketch -> lineart -> colour shown as one item
        "ALTER TABLE files ADD COLUMN stack_id INTEGER",
        "CREATE INDEX files_stack ON files(stack_id) WHERE stack_id IS NOT NULL",
        """CREATE TABLE stacks (
            id INTEGER PRIMARY KEY,
            cover_id INTEGER,            -- the file shown for the stack (default: the newest)
            created REAL
        )""",
    ],
}

TAG_COLORS = ["#e5484d", "#f76b15", "#ffc53d", "#46a758", "#12a594", "#0090ff", "#6e56cf", "#d6409f",
              "#ad7f58", "#8d8d8d"]

THUMB_SCHEMA = """
CREATE TABLE IF NOT EXISTS thumbs (
    hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (hash, size)
) WITHOUT ROWID;
"""


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA cache_size=-65536")  # 64 MB page cache
    con.execute("PRAGMA mmap_size=268435456")
    return con


class Database:
    """Thread-local read connections + one serialized writer."""

    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.catalog_path = os.path.join(data_dir, "catalog.db")
        self.thumbs_path = os.path.join(data_dir, "thumbs.db")
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.write_lock:
            c = self.cat
            c.executescript(CATALOG_SCHEMA)
            c.execute("INSERT OR IGNORE INTO settings VALUES ('schema_version', '1')")
            self._migrate(c)
            self.thm.executescript(THUMB_SCHEMA)

    def _migrate(self, c: sqlite3.Connection) -> None:
        ver = int(c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()[0])
        while ver < SCHEMA_VERSION:
            c.execute("BEGIN")
            try:
                for stmt in MIGRATIONS[ver]:
                    c.execute(stmt)
                ver += 1
                c.execute("UPDATE settings SET value=? WHERE key='schema_version'", (str(ver),))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    # -- settings ----------------------------------------------------------
    def get_setting(self, key: str, default=None):
        row = self.cat.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_setting(self, key: str, value) -> None:
        with self.write_lock:
            self.cat.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # -- connections -------------------------------------------------------
    @property
    def cat(self) -> sqlite3.Connection:
        con = getattr(self._local, "cat", None)
        if con is None:
            con = self._local.cat = _connect(self.catalog_path)
            con.row_factory = sqlite3.Row
        return con

    @property
    def thm(self) -> sqlite3.Connection:
        con = getattr(self._local, "thm", None)
        if con is None:
            con = self._local.thm = _connect(self.thumbs_path)
        return con

    # -- roots -------------------------------------------------------------
    def roots(self, kind: str | None = None) -> list[dict]:
        rows = self.cat.execute(
            "SELECT r.id, r.path, r.kind, r.added_at, r.last_used, COUNT(f.id) AS files FROM roots r "
            "LEFT JOIN files f ON f.root_id = r.id "
            + ("WHERE r.kind = ? " if kind else "") + "GROUP BY r.id ORDER BY r.path",
            (kind,) if kind else (),
        ).fetchall()
        return [dict(r) for r in rows]

    def root_for(self, path: str, kind: str | None = "library") -> dict | None:
        """The root that contains path (deepest match), or None."""
        best = None
        for r in self.roots(kind):
            rp = r["path"]
            if path == rp or path.startswith(rp.rstrip(os.sep) + os.sep):
                if best is None or len(rp) > len(best["path"]):
                    best = r
        return best

    def add_root(self, path: str, kind: str = "library") -> int:
        """Add a library (recursive) or browse (single folder) root.

        A new library root absorbs roots inside it: their catalog rows (and tags) are kept
        and simply re-parented, so nothing is re-indexed.
        """
        now = time.time()
        with self.write_lock:
            c = self.cat
            c.execute("BEGIN")
            try:
                row = c.execute("SELECT id, kind FROM roots WHERE path = ?", (path,)).fetchone()
                if row:
                    rid = row[0]
                    if kind == "library" and row[1] != "library":
                        c.execute("UPDATE roots SET kind='library', added_at=? WHERE id=?", (now, rid))
                    c.execute("UPDATE roots SET last_used=? WHERE id=?", (now, rid))
                else:
                    rid = c.execute("INSERT INTO roots(path, kind, added_at, last_used) VALUES (?, ?, ?, ?)",
                                    (path, kind, now, now)).lastrowid
                if kind == "library":
                    lo, hi = path.rstrip(os.sep) + os.sep, path.rstrip(os.sep) + chr(ord(os.sep) + 1)
                    inner = [r[0] for r in c.execute(
                        "SELECT id FROM roots WHERE id != ? AND path >= ? AND path < ?", (rid, lo, hi))]
                    if inner:
                        q = ",".join("?" * len(inner))
                        c.execute(f"UPDATE files SET root_id = ? WHERE root_id IN ({q})", (rid, *inner))
                        c.execute(f"DELETE FROM roots WHERE id IN ({q})", inner)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return rid

    def touch_root(self, root_id: int) -> None:
        with self.write_lock:
            self.cat.execute("UPDATE roots SET last_used=? WHERE id=?", (time.time(), root_id))

    def remove_root(self, root_id: int) -> None:
        """Removes the folder from the catalog only. Files on disk are never touched.
        Tags are parked by content hash, so re-adding the folder brings them back."""
        with self.write_lock:
            c = self.cat
            c.execute("BEGIN")
            self._orphan_tags_where(c, "f.root_id = ?", (root_id,))
            c.execute("DELETE FROM roots WHERE id = ?", (root_id,))
            c.execute("COMMIT")

    def remake_previews(self, where: str, args: tuple = ()) -> int:
        """Process matching files again (their previews could be better now) on the next scan."""
        with self.write_lock:
            n = self.cat.execute(f"UPDATE files SET status = 0, tver = tver + 1 WHERE status = 1 AND ({where})", args).rowcount
            self.cat.commit()
        return n

    def cleanup(self, browse_days: float = 14, orphan_days: float = 180) -> None:
        """Forget browse folders not visited for a while and very old orphaned tags."""
        now = time.time()
        for r in self.roots("browse"):
            if (r["last_used"] or 0) < now - browse_days * 86400:
                self.remove_root(r["id"])
        with self.write_lock:
            self.cat.execute("DELETE FROM orphan_tags WHERE orphaned < ?", (now - orphan_days * 86400,))

    # -- tags --------------------------------------------------------------
    @staticmethod
    def _orphan_tags_where(c, where: str, args: tuple) -> None:
        c.execute(
            "INSERT OR IGNORE INTO orphan_tags(hash, tag_id, source, confidence, added, orphaned) "
            "SELECT f.hash, ft.tag_id, ft.source, ft.confidence, ft.added, ? FROM file_tags ft "
            f"JOIN files f ON f.id = ft.file_id WHERE f.hash IS NOT NULL AND {where}",
            (time.time(), *args),
        )

    def relink_orphans(self, hashes: list[str]) -> int:
        """Re-attach parked tags to files with the same content (moved/renamed outside the app)."""
        c = self.cat
        if not hashes or c.execute("SELECT 1 FROM orphan_tags LIMIT 1").fetchone() is None:
            return 0
        n = 0
        with self.write_lock:
            for i in range(0, len(hashes), 500):
                chunk = hashes[i:i + 500]
                q = ",".join("?" * len(chunk))
                c.execute("BEGIN")
                n += c.execute(
                    "INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) "
                    "SELECT f.id, o.tag_id, o.source, o.confidence, o.added FROM orphan_tags o "
                    f"JOIN files f ON f.hash = o.hash WHERE o.hash IN ({q})", chunk).rowcount
                c.execute(f"DELETE FROM orphan_tags WHERE hash IN ({q})", chunk)
                c.execute("COMMIT")
        return n

    # -- thumbnails --------------------------------------------------------
    def get_thumb(self, h: str, size: int) -> bytes | None:
        row = self.thm.execute("SELECT data FROM thumbs WHERE hash = ? AND size = ?", (h, size)).fetchone()
        return row[0] if row else None

    def has_thumb(self, h: str, size: int) -> bool:
        return self.thm.execute("SELECT 1 FROM thumbs WHERE hash = ? AND size = ?", (h, size)).fetchone() is not None

    def put_thumbs(self, h: str, thumbs: dict[int, bytes]) -> None:
        if not thumbs:
            return
        with self.write_lock:
            self.thm.executemany(
                "INSERT OR REPLACE INTO thumbs(hash, size, data) VALUES (?, ?, ?)",
                [(h, s, d) for s, d in thumbs.items()],
            )

    # -- processing results ------------------------------------------------
    def store_results(self, results: list[dict]) -> None:
        """Write a batch of processing results in one transaction per database."""
        ok, bad, thumbs = [], [], []
        for r in results:
            if "error" in r:
                bad.append((r["error"][:500], r["path"]))
                continue
            ok.append((
                r["hash"], r.get("width"), r.get("height"), r.get("color"), int(bool(r.get("alpha"))),
                (r.get("meta") or {}).get("frames"),
                r["date_ts"], r["date_source"], json.dumps(r.get("meta") or {}, ensure_ascii=False, default=str),
                r.get("kind", "image"), r["size"], r["mtime_ns"], r.get("phash"), r["path"],
            ))
            for s, d in (r.get("thumbs") or {}).items():
                thumbs.append((r["hash"], s, d))
        with self.write_lock:
            if thumbs:
                self.thm.execute("BEGIN")
                self.thm.executemany("INSERT OR REPLACE INTO thumbs(hash, size, data) VALUES (?, ?, ?)", thumbs)
                self.thm.execute("COMMIT")
            c = self.cat
            c.execute("BEGIN")
            c.executemany(
                "UPDATE files SET hash=?, width=?, height=?, color=?, alpha=?, frames=?, date_ts=?, date_source=?, meta=?, "
                "kind=?, size=?, mtime_ns=?, phash=?, status=1, error=NULL WHERE path=?",
                ok,
            )
            c.executemany("UPDATE files SET status=2, error=? WHERE path=?", bad)
            c.execute("COMMIT")
        self.relink_orphans([o[0] for o in ok])
