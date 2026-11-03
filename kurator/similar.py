"""Similar images: groups of pictures that look nearly the same (resized, re-saved, slightly edited,
exported twice...), found with a 64-bit difference hash (dHash) of each thumbnail.

Hashes are made while indexing; older catalogs are filled in from the thumbnail cache in the
background. Grouping uses band buckets (no all-pairs comparison), so it stays fast for 100k images.
"""

from __future__ import annotations

import io
import threading

from PIL import Image

from .db import Database
from .processing import dhash

LEVELS = {"strict": 4, "normal": 8, "loose": 12}
_BANDS = [(s, 16) for s in (0, 16, 32, 48)] + [(s, 16) for s in (8, 24, 40)]   # 16-bit slices, overlapping
_MAX_BUCKET = 400   # flat or blank images all share a hash: don't compare those with each other


def _u(h: int) -> int:
    return h & 0xFFFFFFFFFFFFFFFF


def groups(items: list[tuple[int, int]], dist: int) -> list[list[int]]:
    """items: (file id, signed dHash). Returns groups (2+ ids) whose members are within `dist` bits
    of another member, biggest groups first."""
    hs = {fid: _u(h) for fid, h in items if h}   # 0 = no usable picture
    parent = {fid: fid for fid in hs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for shift, width in _BANDS:
        mask = (1 << width) - 1
        buckets: dict[int, list[int]] = {}
        for fid, h in hs.items():
            buckets.setdefault((h >> shift) & mask, []).append(fid)
        for ids in buckets.values():
            if len(ids) < 2 or len(ids) > _MAX_BUCKET:
                continue
            for i, a in enumerate(ids):
                ha = hs[a]
                for b in ids[i + 1:]:
                    if (ha ^ hs[b]).bit_count() <= dist:
                        ra, rb = find(a), find(b)
                        if ra != rb:
                            parent[rb] = ra
    out: dict[int, list[int]] = {}
    for fid in hs:
        out.setdefault(find(fid), []).append(fid)
    return sorted((g for g in out.values() if len(g) > 1), key=lambda g: (-len(g), min(g)))


class Backfill:
    """Makes the missing hashes from cached thumbnails, in the background."""

    def __init__(self, db: Database):
        self.db = db
        self.running = False
        self.done = 0
        self._lock = threading.Lock()

    def pending(self) -> int:
        return self.db.cat.execute(
            "SELECT COUNT(*) FROM files WHERE status = 1 AND phash IS NULL AND hash IS NOT NULL AND kind IN ('image', 'video') "
            "AND (meta IS NULL OR json_extract(meta, '$.placeholder') IS NULL)").fetchone()[0]

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            if not self.pending():
                return False
            self.running = True
            self.done = 0
        threading.Thread(target=self._run, name="kurator-phash", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            while True:
                rows = self.db.cat.execute(
                    "SELECT id, hash FROM files WHERE status = 1 AND phash IS NULL AND hash IS NOT NULL AND kind IN ('image', 'video') "
                    "AND (meta IS NULL OR json_extract(meta, '$.placeholder') IS NULL) LIMIT 500").fetchall()
                if not rows:
                    break
                upd = []
                for fid, h in rows:
                    data = self.db.get_thumb(h, 256)
                    v = 0   # no thumbnail: mark as done with a neutral value so we don't retry forever
                    if data:
                        try:
                            v = dhash(Image.open(io.BytesIO(data)))
                        except Exception:
                            pass
                    upd.append((v, fid))
                with self.db.write_lock:
                    self.db.cat.executemany("UPDATE files SET phash = ? WHERE id = ?", upd)
                self.done += len(upd)
        finally:
            self.running = False
