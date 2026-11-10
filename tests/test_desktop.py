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


def test_install_kurator_from_the_app():
    """The start page may install Kurator; the Python it downloads is pinned with checksums for every build;
    the installers take that Python; Kurator's page knows a Kurator the app installed."""
    d = os.path.join(ROOT, "desktop", "src-tauri")
    caps = json.load(open(os.path.join(d, "capabilities", "splash.json"), encoding="utf-8"))
    build = open(os.path.join(d, "build.rs"), encoding="utf-8").read()
    assert "allow-install-kurator" in caps["permissions"] and '"install_kurator"' in build
    rt = json.load(open(os.path.join(d, "python-runtime.json"), encoding="utf-8"))
    assert re.fullmatch(r"3\.14\.\d+", rt["version"])
    for target in ("x86_64-pc-windows-msvc", "x86_64-apple-darwin", "aarch64-apple-darwin", "x86_64-unknown-linux-gnu"):
        a = rt["assets"][target]
        assert a["url"].startswith(f"https://github.com/astral-sh/python-build-standalone/releases/download/{rt['release']}/")
        assert f"cpython-{rt['version']}%2B{rt['release']}-{target}-install_only_stripped.tar.gz" in a["url"]
        assert re.fullmatch(r"[0-9a-f]{64}", a["sha256"])
    sh = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    bat = open(os.path.join(ROOT, "install.bat"), encoding="utf-8").read()
    assert '${KURATOR_PYTHON:+"$KURATOR_PYTHON"}' in sh and 'set "PY="%KURATOR_PYTHON%""' in bat
    from kurator import updates
    assert updates.install_kind()["app"] is False                             # a clone: its own update script
