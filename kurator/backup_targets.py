"""Backup targets. One small interface, so cloud targets (rclone: Google Drive, OneDrive, NAS...)
can be added next to local folders without touching the planner or the runner.

Every target is addressed with paths relative to its root, always written with "/".
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time

from .fileops import set_creation_time

CHUNK = 4 * 1024 * 1024
META_DIR = "_kurator"            # manifest + catalog snapshot live here
ARCHIVE_DIR = "_kurator_archive"  # replaced / removed files, by date


class TargetError(OSError):
    pass


def _hash_path(path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalTarget:
    """A folder on a local disk, external drive, NAS share or a synced cloud folder."""

    kind = "local"

    def __init__(self, target_id: int, location: str, reserve: int = 0):
        self.id = target_id
        self.root = os.path.normpath(os.path.abspath(os.path.expanduser(location)))
        self.reserve = reserve

    # -- info ----------------------------------------------------------------
    def label(self) -> str:
        return self.root

    def prefetch(self) -> None:
        """Cloud targets list everything once per plan; local stat calls are cheap."""

    def available(self) -> bool:
        """The root (or its nearest existing parent on the same drive) is reachable."""
        return self._existing_parent() is not None

    def _existing_parent(self) -> str | None:
        d = self.root
        while not os.path.isdir(d):
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
        return d

    def space(self) -> dict:
        """{'total', 'used', 'free'} in bytes, or None values if unknown."""
        p = self._existing_parent()
        if p is None:
            return {"total": None, "used": None, "free": None}
        u = shutil.disk_usage(p)
        return {"total": u.total, "used": u.used, "free": u.free}

    def _abs(self, rel: str) -> str:
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise TargetError(f"Invalid path in backup: {rel}")
        return os.path.join(self.root, *parts)

    def exists(self, rel: str) -> bool:
        return os.path.isfile(self._abs(rel))

    # -- writing ---------------------------------------------------------------
    def put(self, src: str, rel: str, verify: bool = True, birth: float | None = None) -> str:
        """Copy src into the target. Returns the content hash of what was copied.
        Writes to a temporary name first, so an interrupted copy never looks complete."""
        dst = self._abs(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".kurator-part"
        h = hashlib.blake2b(digest_size=16)
        st = os.stat(src)
        try:
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                for chunk in iter(lambda: fi.read(CHUNK), b""):
                    h.update(chunk)
                    fo.write(chunk)
                fo.flush()
                os.fsync(fo.fileno())
            digest = h.hexdigest()
            if verify and _hash_path(tmp) != digest:
                raise TargetError(f"Verification failed for {rel}")
            shutil.copystat(src, tmp)  # modified/accessed time, permission bits
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        b = birth if birth is not None else getattr(st, "st_birthtime", None)
        if b and os.name == "nt":
            set_creation_time(dst, b)
        return digest

    def put_bytes(self, data: bytes, rel: str) -> None:
        dst = self._abs(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".kurator-part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)

    def put_file_plain(self, src: str, rel: str) -> None:
        """Copy small companion files (sidecars, catalog snapshot) without hashing."""
        dst = self._abs(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".kurator-part"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)

    def move(self, rel_from: str, rel_to: str) -> None:
        """Rename inside the target (relocate after re-tagging, archive)."""
        a, b = self._abs(rel_from), self._abs(rel_to)
        if not os.path.isfile(a):
            raise TargetError(f"Missing in backup: {rel_from}")
        if os.path.lexists(b):
            raise TargetError(f"Already exists in backup: {rel_to}")
        os.makedirs(os.path.dirname(b), exist_ok=True)
        os.rename(a, b)
        for suffix in (".xmp",):
            if os.path.isfile(a + suffix) and not os.path.lexists(b + suffix):
                os.rename(a + suffix, b + suffix)
        self._prune_empty(os.path.dirname(a))

    def _prune_empty(self, d: str) -> None:
        while os.path.normcase(d) != os.path.normcase(self.root) and d.startswith(self.root):
            try:
                os.rmdir(d)  # only succeeds when empty
            except OSError:
                return
            d = os.path.dirname(d)

    # -- reading (restore) -------------------------------------------------------
    def get(self, rel: str, dst: str, mtime_ns: int | None = None, birth: float | None = None,
            expect_hash: str | None = None) -> None:
        src = self._abs(rel)
        if not os.path.isfile(src):
            raise TargetError(f"Missing in backup: {rel}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".kurator-part"
        h = hashlib.blake2b(digest_size=16)
        try:
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                for chunk in iter(lambda: fi.read(CHUNK), b""):
                    h.update(chunk)
                    fo.write(chunk)
            if expect_hash and h.hexdigest() != expect_hash:
                raise TargetError(f"Backup copy of {rel} does not match its recorded checksum")
            shutil.copystat(src, tmp)
            if mtime_ns:
                os.utime(tmp, ns=(mtime_ns, mtime_ns))
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        if birth and os.name == "nt":
            set_creation_time(dst, birth)


class RcloneTarget:
    """A folder on any rclone remote: Google Drive, OneDrive, Dropbox, pCloud, B2, SFTP/NAS...
    Same interface as LocalTarget. Uploads are verified with the provider's checksum (MD5 on
    Google Drive) when it offers one, otherwise by size."""

    kind = "rclone"

    def __init__(self, target_id: int, location: str, reserve: int = 0):
        from .rclone import split_location, shared
        self.id = target_id
        self.remote, self.path = split_location(location)
        self.fs = f"{self.remote}:{self.path}"
        self.reserve = reserve
        self.rc = shared()
        self._listing: dict | None = None
        self._avail: tuple[float, bool] | None = None

    def label(self) -> str:
        return self.fs

    def _rel(self, rel: str) -> str:
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise TargetError(f"Invalid path in backup: {rel}")
        return "/".join(parts)

    def available(self) -> bool:
        if self._avail and time.time() - self._avail[0] < 30:
            return self._avail[1]
        try:
            self.rc.call("operations/fsinfo", timeout=30, fs=f"{self.remote}:")
            ok = True
        except Exception:
            ok = False
        self._avail = (time.time(), ok)
        return ok

    def space(self) -> dict:
        try:
            a = self.rc.call("operations/about", timeout=45, fs=f"{self.remote}:")
            return {"total": a.get("total"), "used": a.get("used"), "free": a.get("free")}
        except Exception:
            return {"total": None, "used": None, "free": None}  # provider has no quota info

    def prefetch(self) -> None:
        """List the whole backup folder once (sizes + checksums) - one API walk, not one per file."""
        try:
            r = self.rc.call("operations/list", fs=self.fs, remote="",
                             opt={"recurse": True, "filesOnly": True, "showHash": True, "hashTypes": ["md5"]})
            self._listing = {i["Path"]: i for i in r.get("list") or []}
        except Exception as e:
            if "not found" in str(e).lower():
                self._listing = {}
            else:
                raise TargetError(f"Can't list {self.fs}: {e}") from None

    def exists(self, rel: str) -> bool:
        rel = self._rel(rel)
        if self._listing is not None:
            return rel in self._listing
        return self._stat(rel) is not None

    def _stat(self, rel: str) -> dict | None:
        try:
            return self.rc.call("operations/stat", fs=self.fs, remote=rel,
                                opt={"showHash": True, "hashTypes": ["md5"]}).get("item")
        except Exception as e:
            if "not found" in str(e).lower():
                return None
            raise

    def put(self, src: str, rel: str, verify: bool = True, birth: float | None = None) -> str:
        rel = self._rel(rel)
        b2, md5 = hashlib.blake2b(digest_size=16), hashlib.md5(usedforsecurity=False)
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                b2.update(chunk)
                md5.update(chunk)
        size = os.path.getsize(src)
        self.rc.call("operations/copyfile", srcFs=os.path.dirname(os.path.abspath(src)), srcRemote=os.path.basename(src),
                     dstFs=self.fs, dstRemote=rel)
        if verify:
            item = self._stat(rel)
            if item is None:
                raise TargetError(f"Upload of {rel} is not visible on {self.remote}")
            remote_md5 = (item.get("Hashes") or {}).get("md5")
            if item.get("Size") != size or (remote_md5 and remote_md5 != md5.hexdigest()):
                raise TargetError(f"Verification failed for {rel} on {self.remote}")
        if self._listing is not None:
            self._listing[rel] = {"Path": rel, "Size": size}
        return b2.hexdigest()

    def put_file_plain(self, src: str, rel: str) -> None:
        self.rc.call("operations/copyfile", srcFs=os.path.dirname(os.path.abspath(src)), srcRemote=os.path.basename(src),
                     dstFs=self.fs, dstRemote=self._rel(rel))

    def put_bytes(self, data: bytes, rel: str) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, os.path.basename(rel) or "data")
            with open(p, "wb") as f:
                f.write(data)
            self.put_file_plain(p, rel)

    def move(self, rel_from: str, rel_to: str) -> None:
        a, b = self._rel(rel_from), self._rel(rel_to)
        if not self.exists(a):
            raise TargetError(f"Missing in backup: {rel_from}")
        if self._stat(b) is not None:
            raise TargetError(f"Already exists in backup: {rel_to}")
        self.rc.call("operations/movefile", srcFs=self.fs, srcRemote=a, dstFs=self.fs, dstRemote=b)  # server-side
        if self._stat(a + ".xmp") is not None:
            try:
                self.rc.call("operations/movefile", srcFs=self.fs, srcRemote=a + ".xmp", dstFs=self.fs, dstRemote=b + ".xmp")
            except Exception:
                pass
        if self._listing is not None:
            self._listing[b] = self._listing.pop(a, {"Path": b})

    def get(self, rel: str, dst: str, mtime_ns: int | None = None, birth: float | None = None,
            expect_hash: str | None = None) -> None:
        rel = self._rel(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp_name = os.path.basename(dst) + ".kurator-part"
        tmp = os.path.join(os.path.dirname(dst), tmp_name)
        try:
            self.rc.call("operations/copyfile", srcFs=self.fs, srcRemote=rel, dstFs=os.path.dirname(dst), dstRemote=tmp_name)
            if expect_hash and _hash_path(tmp) != expect_hash:
                raise TargetError(f"Backup copy of {rel} does not match its recorded checksum")
            if mtime_ns:
                os.utime(tmp, ns=(mtime_ns, mtime_ns))
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        if birth and os.name == "nt":
            set_creation_time(dst, birth)


def make_target(row):
    kind = row["kind"] if "kind" in row.keys() else "local"
    if kind == "local":
        return LocalTarget(row["id"], row["location"], row["reserve"])
    if kind == "rclone":
        return RcloneTarget(row["id"], row["location"], row["reserve"])
    raise TargetError(f"Unknown target type '{kind}'")
