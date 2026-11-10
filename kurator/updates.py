"""Is there a newer Kurator? Asks GitHub for the latest release, at most once a day.

This is the one request Kurator makes on its own; ⚙ Settings → Updates turns it off. Nothing is
sent but the request itself (GitHub sees your IP address, as when you open the page).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from . import __version__
from .db import Database

REPO = "eigensoundlab/kurator"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DAY = 86400


def version_tuple(v: str | None) -> tuple:
    """'v0.19.0', '0.19.0', 'v.0.19.0' -> (0, 19, 0)."""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def enabled(db: Database) -> bool:
    return bool(db.get_setting("update_check", True))


def _fetch(timeout: float = 6) -> dict:
    req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Kurator/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - a fixed https URL
        data = json.loads(r.read(512 * 1024))
    return {"latest": data.get("tag_name"), "name": data.get("name"), "url": data.get("html_url"),
            "published": data.get("published_at")}


def install_kind() -> dict:
    """How this Kurator gets updated: a git clone (update.bat / update.sh pulls the new code) or not."""
    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = "update.bat" if os.name == "nt" else "update.sh"
    return {"git": os.path.isdir(os.path.join(home, ".git")), "script": script,
            "script_found": os.path.isfile(os.path.join(home, script)), "folder": home,
            "app": os.path.isfile(os.path.join(home, ".kurator-app"))}   # installed by the desktop app: it updates it


def check(db: Database, force: bool = False) -> dict:
    """{current, latest, newer, url, ...}; asks GitHub when the last answer is a day old (or force)."""
    out = {"enabled": enabled(db), "current": __version__, **install_kind()}
    if not out["enabled"] and not force:
        return out
    info = db.get_setting("update_info", {}) or {}
    if force or time.time() - info.get("checked", 0) > DAY:
        try:
            info = {**_fetch(), "checked": time.time()}
        except Exception as e:   # offline, rate limit...: try again tomorrow, keep what we knew
            info = {**info, "checked": time.time(), "error": str(e)[:200]}
        db.set_setting("update_info", info)
    latest = info.get("latest")
    out.update(latest=latest, name=info.get("name"), url=info.get("url"), published=info.get("published"),
               error=info.get("error"), checked=info.get("checked"),
               newer=bool(latest) and version_tuple(latest) > version_tuple(__version__),
               skipped=bool(latest) and db.get_setting("update_skip") == latest)
    return out
