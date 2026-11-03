"""rclone integration - cloud storage without handling cloud credentials in Kurator.

rclone (https://rclone.org, MIT licence) talks to Google Drive, OneDrive, Dropbox, pCloud, B2,
SFTP/NAS and ~70 more. Your logins live in rclone's own config file (optionally encrypted); Kurator
only refers to a remote by name, e.g. "gdrive:Kurator Backup".

Kurator starts ONE background rclone ("rcd") bound to 127.0.0.1 on a free port, protected by a
random user/password, and drives it over its JSON API. One process, many parallel transfers.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-. ]{0,40}$")
# Global flags for the daemon: stop cleanly at Google's daily upload limit, retry flaky networks.
DAEMON_FLAGS = ["--drive-stop-on-upload-limit", "--retries", "3", "--low-level-retries", "10"]


class RcloneError(OSError):
    pass


def _no_window() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


class Rclone:
    def __init__(self, db=None, log_dir: str | None = None):
        self.db = db
        self.log_dir = log_dir
        self._proc: subprocess.Popen | None = None
        self._url = ""
        self._auth = ""
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
        self.connecting: dict = {}

    # -- discovery -----------------------------------------------------------------
    def path(self) -> str | None:
        configured = self.db.get_setting("rclone_path", None) if self.db else None
        env = os.environ.get("KURATOR_RCLONE")
        for cand in (env, configured):
            if cand and os.path.isfile(cand):
                return cand
        return shutil.which("rclone")

    def _run(self, *args, timeout: float = 60) -> str:
        exe = self.path()
        if not exe:
            raise RcloneError("rclone is not installed")
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, **_no_window())
        if r.returncode != 0:
            raise RcloneError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else f"rclone exited with {r.returncode}")
        return r.stdout

    def info(self) -> dict:
        exe = self.path()
        if not exe:
            return {"installed": False, "remotes": [], "connecting": self.connecting}
        try:
            version = self._run("version", timeout=20).splitlines()[0].strip()
        except Exception as e:
            return {"installed": False, "error": str(e), "remotes": [], "connecting": self.connecting}
        return {"installed": True, "path": exe, "version": version, "remotes": self.remotes(), "connecting": self.connecting}

    def remotes(self) -> list[dict]:
        """[{'name': 'gdrive', 'type': 'drive'}] - read without touching any credentials."""
        try:
            out = self._run("listremotes", "--long", timeout=20)
        except Exception:
            return []
        res = []
        for line in out.splitlines():
            if ":" in line:
                name, typ = line.split(":", 1)
                res.append({"name": name.strip(), "type": typ.strip()})
        return res

    # -- connecting a cloud account ------------------------------------------------
    def connect_google_drive(self, name: str) -> None:
        """Create a Google Drive remote. rclone opens the Google sign-in page in your browser.
        scope=drive.file: Kurator/rclone can only see files it created - not the rest of your Drive."""
        name = name.strip()
        if not NAME_RE.match(name) or ":" in name:
            raise RcloneError("Use a simple name like gdrive or gdrive-work (letters, numbers, - and _)")
        if any(r["name"] == name for r in self.remotes()):
            raise RcloneError(f'A remote called "{name}" already exists')
        if self.connecting.get("running"):
            raise RcloneError("Already connecting an account - finish the sign-in in your browser first")
        self.connecting = {"running": True, "name": name, "started": time.time(), "error": None, "done": False}

        def work():
            try:
                self._run("config", "create", name, "drive", "scope=drive.file", timeout=900)
                if not any(r["name"] == name for r in self.remotes()):
                    raise RcloneError("Sign-in did not complete")
                self.connecting.update(done=True)
            except Exception as e:
                self.connecting.update(error=str(e))
            finally:
                self.connecting.update(running=False)

        threading.Thread(target=work, name="rclone-connect", daemon=True).start()

    # -- daemon --------------------------------------------------------------------
    def _ensure(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            exe = self.path()
            if not exe:
                raise RcloneError("rclone is not installed - see https://rclone.org/downloads/ (Windows: winget install Rclone.Rclone)")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            user, pw = "kurator", secrets.token_urlsafe(24)
            log = None
            if self.log_dir:
                os.makedirs(self.log_dir, exist_ok=True)
                log = open(os.path.join(self.log_dir, "rclone.log"), "ab")
            args = [exe, "rcd", "--rc-addr", f"127.0.0.1:{port}", "--rc-user", user, "--rc-pass", pw, *DAEMON_FLAGS]
            if os.environ.get("RCLONE_CONFIG"):
                args += ["--config", os.environ["RCLONE_CONFIG"]]
            self._proc = subprocess.Popen(args, stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, **_no_window())
            self._url = f"http://127.0.0.1:{port}/"
            self._auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
            deadline = time.time() + 15
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    raise RcloneError("rclone could not start (see rclone.log in Kurator's data folder)")
                try:
                    self._post("core/version", {}, timeout=2)
                    return
                except (urllib.error.URLError, ConnectionError, OSError):
                    time.sleep(0.15)
            raise RcloneError("rclone did not respond")

    def _post(self, method: str, params: dict, timeout: float = 3600) -> dict:
        req = urllib.request.Request(self._url + method, data=json.dumps(params).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": self._auth})
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
                msg = body.get("error") or str(e)
            except ValueError:
                msg = str(e)
            raise RcloneError(msg) from None

    def call(self, method: str, timeout: float = 3600, **params) -> dict:
        self._ensure()
        return self._post(method, params, timeout=timeout)

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._post("core/quit", {}, timeout=3)
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None


_shared: Rclone | None = None


def shared(db=None, log_dir: str | None = None) -> Rclone:
    """One rclone daemon per Kurator process."""
    global _shared
    if _shared is None:
        _shared = Rclone(db, log_dir)
    elif db is not None and _shared.db is None:
        _shared.db, _shared.log_dir = db, log_dir
    return _shared


def split_location(location: str) -> tuple[str, str]:
    """'gdrive:Art backup/2026' -> ('gdrive', 'Art backup/2026')"""
    if ":" not in location:
        raise RcloneError("Cloud locations look like remote:folder, e.g. gdrive:Kurator Backup")
    name, path = location.split(":", 1)
    if not name or not NAME_RE.match(name):
        raise RcloneError(f"Invalid remote name: {name!r}")
    path = path.strip()
    return name, (path.rstrip("/") or path)


if __name__ == "__main__":  # tiny manual check: python -m kurator.rclone
    print(json.dumps(shared().info(), indent=2))
    sys.exit(0)
