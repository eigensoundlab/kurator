"""v0.19: the desktop app's needs - drops with paths, file paths, folders in the file manager, Open with
(found and added programs), quitting when standard input closes - and the desktop app's own files."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import apps
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def wait(sc, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        st = sc.status()
        if st["phase"] == "idle" and st["finished"]:
            return
        assert st["phase"] != "error", st
        time.sleep(0.05)
    raise TimeoutError


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    (lib / "a").mkdir(parents=True)
    for i, c in enumerate(("red", "green")):
        Image.new("RGB", (40 + i, 30), c).save(lib / "a" / f"shot{i}.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait(sc)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    return c, db, sc, lib, tmp_path


def ids_by_name(c):
    it = c.get("/api/items").json()
    return dict(zip(it["n"], it["ids"]))


# ------------------------------------------------------------ drops with paths
def test_drop_paths_classify_copy_and_undo(env):
    c, db, sc, lib, tmp = env
    ids = ids_by_name(c)
    out = tmp / "outside"
    out.mkdir()
    (out / "copy_of_shot1.png").write_bytes((lib / "a" / "shot1.png").read_bytes())
    twin = out / "shot1.png"                                                    # same name, size, time: a copy
    twin.write_bytes((lib / "a" / "shot1.png").read_bytes())
    st = os.stat(lib / "a" / "shot1.png")
    os.utime(twin, ns=(st.st_atime_ns, st.st_mtime_ns))
    Image.new("RGB", (12, 10), "orange").save(out / "new.png")
    os.utime(out / "new.png", (1600000000, 1600000000))
    (out / "notes.txt").write_text("hi")
    Image.new("RGB", (9, 9), "blue").save(lib / "a" / "fresh.png")              # in the library, not indexed yet
    (tmp / "folder_out").mkdir()
    paths = [str(lib / "a" / "shot0.png"), str(twin), str(out / "copy_of_shot1.png"), str(out / "new.png"),
             str(out / "notes.txt"), str(out / "gone.png"), str(lib / "a" / "fresh.png"), str(lib / "a"), str(tmp / "folder_out")]
    r = c.post("/api/drop/paths", json={"paths": paths}, headers=H).json()
    by = {os.path.basename(f["path"]): f for f in r["files"]}
    assert by["shot0.png"]["ids"] == [ids["shot0.png"]]                         # by path
    assert by["shot1.png"]["ids"] == [ids["shot1.png"]]                         # a copy elsewhere: the library's
    assert by["copy_of_shot1.png"]["ids"] == [] and by["new.png"]["ids"] == []
    assert by["fresh.png"]["ids"] and by["fresh.png"].get("new") and r["indexing"]
    assert {os.path.basename(s["path"]): s["error"] for s in r["skipped"]} == {"notes.txt": "not a kind of file Kurator shows", "gone.png": "not found"}
    assert [(os.path.basename(f["path"]), f["in_library"]) for f in r["folders"]] == [("a", True), ("folder_out", False)]
    wait(sc)
    assert c.get(f"/api/items/{by['fresh.png']['ids'][0]}").json()["width"] == 9
    # new files copied in from where they are: their own name, their time, a catalog row
    dest = lib / "inbox"
    got = c.post("/api/drop/copy", json={"batch": "p1", "dest": str(dest), "path": str(out / "new.png")}, headers=H).json()
    assert got["path"] == str(dest / "new.png") and got["id"] and (out / "new.png").exists()
    assert os.stat(got["path"]).st_mtime == 1600000000
    again = c.post("/api/drop/copy", json={"batch": "p1", "dest": str(dest), "path": str(out / "new.png")}, headers=H).json()
    assert again["path"] == str(dest / "new (2).png")
    assert c.post("/api/drop/copy", json={"batch": "p1", "dest": str(dest), "path": str(out / "gone.png")}, headers=H).status_code == 400
    assert c.post("/api/drop/copy", json={"batch": "p1", "dest": "", "path": str(out / "new.png")}, headers=H).status_code == 400
    assert not [n for n in os.listdir(dest) if n.endswith(".part")]
    done = c.post("/api/drop/done", json={"batch": "p1"}, headers=H).json()
    assert done["received"] == 2 and done["indexed"] == 2
    wait(sc)
    assert c.post(f"/api/operations/{done['op_id']}/undo", headers=H).json()["removed"] == 2
    assert not (dest / "new.png").exists() and (out / "new.png").exists()       # the original stays


def test_paths_and_reveal_folder(env, monkeypatch):
    c, db, sc, lib, tmp = env
    ids = ids_by_name(c)
    r = c.post("/api/paths", json={"ids": [ids["shot1.png"], 999999, ids["shot0.png"]]}, headers=H).json()
    assert r["paths"] == [str(lib / "a" / "shot1.png"), str(lib / "a" / "shot0.png")]
    started = []
    monkeypatch.setattr("kurator.fileops.subprocess.Popen", lambda cmd, *a, **k: started.append(cmd))
    assert c.post("/api/reveal-folder", json={"path": str(lib / "a")}, headers=H).json()["ok"]
    assert started and started[0][-1] == str(lib / "a")
    assert c.post("/api/reveal-folder", json={"path": str(tmp / "nope")}, headers=H).status_code == 400


# ------------------------------------------------------------------ open with
def test_open_with_programs(env, monkeypatch):
    c, db, sc, lib, tmp = env
    ids = ids_by_name(c)
    monkeypatch.setattr(apps, "KNOWN", [("krita", "Krita", ("image",), None, [], [], [])])
    monkeypatch.setattr(apps, "_find", lambda *a: None)                       # this computer's own programs aside
    r = c.get("/api/apps").json()
    assert r["apps"] == [] and r["ext_kind"][".png"] == "image" and r["ext_kind"][".wav"] == "audio"
    prog = tmp / "paint.exe"
    prog.write_text("")
    for bad in ({"name": "x", "path": "relative/paint.exe"}, {"name": "x", "path": str(tmp / "missing.exe")}, {"name": "x", "path": str(tmp)}):
        assert c.put("/api/apps", json={"custom": [bad], "hidden": []}, headers=H).status_code == 400
    saved = c.put("/api/apps", json={"custom": [{"name": "", "path": f'"{prog}"', "kinds": ["image", "nope"]}], "hidden": ["krita", "made-up"]},
                  headers=H).json()["apps"]
    assert [(a["name"], a["kinds"], a["custom"]) for a in saved] == [("paint", ["image"], True)]
    assert db.get_setting("open_with")["hidden"] == ["krita"]
    started = []
    monkeypatch.setattr(apps.subprocess, "Popen", lambda cmd, **k: started.append((cmd, k)))
    res = c.post("/api/open-with", json={"app": saved[0]["id"], "ids": [ids["shot0.png"], ids["shot1.png"]]}, headers=H).json()
    assert res == {"app": "paint", "files": 2}
    cmd, kw = started[0]
    assert cmd == [str(prog), str(lib / "a" / "shot0.png"), str(lib / "a" / "shot1.png")]
    assert kw["stdin"] == subprocess.DEVNULL
    assert c.post("/api/open-with", json={"app": "c-unknown", "ids": [ids["shot0.png"]]}, headers=H).status_code == 400
    assert c.post("/api/open-with", json={"app": saved[0]["id"], "ids": [424242]}, headers=H).status_code == 400
    # a found program can be hidden, and is then offered no more (the page filters on "hidden")
    monkeypatch.setattr(apps, "_find", lambda *a: str(prog))
    found = {a["id"]: a for a in c.get("/api/apps").json()["apps"]}
    assert found["krita"]["hidden"] and not found["krita"]["custom"]


def test_open_with_helpers(monkeypatch):
    assert apps._newest(["/x/Blender 4.9/blender", "/x/Blender 4.10/blender", "/x/Blender 3.6/blender"]) == "/x/Blender 4.10/blender"
    assert apps._newest([]) is None
    monkeypatch.setattr(apps.sys, "platform", "darwin")
    assert apps.command("/Applications/Krita.app", ["/a.png"]) == ["open", "-a", "/Applications/Krita.app", "/a.png"]
    monkeypatch.setattr(apps.sys, "platform", "linux")
    assert apps.command("/usr/bin/krita", ["/a.png"]) == ["/usr/bin/krita", "/a.png"]


def test_page_may_use_the_desktop_ipc(env):
    c, *_ = env
    csp = c.get("/").headers["content-security-policy"]
    assert "connect-src 'self' ipc: http://ipc.localhost" in csp


# ------------------------------------------------- quitting with the desktop app
def test_quits_when_stdin_closes(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = subprocess.Popen([sys.executable, "-m", "kurator", "--no-browser", "--no-ai", "--no-scan", "--no-watch", "--port", str(port),
                          "--data-dir", str(tmp_path / "data"), "--quit-on-stdin-close"],
                         cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        t = time.time()
        up = False
        while time.time() - t < 60 and p.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    up = True
                    break
            except OSError:
                time.sleep(0.2)
        assert up, p.stdout.read().decode(errors="replace") if p.poll() is not None else "no server"
        p.stdin.close()
        assert p.wait(timeout=30) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
