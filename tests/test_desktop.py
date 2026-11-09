"""The desktop app's files (desktop/): versions that agree, the app id, icons, and what the page may use."""

from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_desktop_app_files_agree():
    d = os.path.join(ROOT, "desktop")
    conf = json.load(open(os.path.join(d, "src-tauri", "tauri.conf.json"), encoding="utf-8"))
    cargo = open(os.path.join(d, "src-tauri", "Cargo.toml"), encoding="utf-8").read()
    pkg = json.load(open(os.path.join(d, "package.json"), encoding="utf-8"))
    assert conf["identifier"] == "com.eigensound.kurator"
    assert re.search(r'^version = "([^"]+)"', cargo, re.M).group(1) == conf["version"] == pkg["version"]
    for icon in conf["bundle"]["icon"]:
        assert os.path.isfile(os.path.join(d, "src-tauri", icon)), icon
    caps = json.load(open(os.path.join(d, "src-tauri", "capabilities", "splash.json"), encoding="utf-8"))
    assert "remote" not in caps                                               # the page's rights are added at run time
    main = open(os.path.join(d, "src-tauri", "src", "main.rs"), encoding="utf-8").read()
    assert '"--quit-on-stdin-close"' in main and 'remote(format!("http://127.0.0.1:{port}/*"))' in main
