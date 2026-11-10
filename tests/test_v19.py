"""v0.19: the desktop app's needs - drops with paths, file paths, folders in the file manager, Open with
(found and added programs), quitting when standard input closes - and the desktop app's own files."""

from __future__ import annotations

import io
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
    assert res == {"app": "paint", "files": 2, "layout": None}
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


# --------------------------------------------- after trying the desktop app (0.19.0)
def test_tagged_files_in_a_browsed_folder_show_in_the_tag(env):
    """A tag (or project) shows all its files - also those in a folder only browsed - as its count says."""
    c, db, sc, lib, tmp = env
    other = tmp / "elsewhere"
    other.mkdir()
    Image.new("RGB", (20, 10), "pink").save(other / "outside.png")
    c.post("/api/browse", json={"path": str(other)}, headers=H)
    wait(sc)
    out_id = c.get("/api/items", params={"folder": str(other)}).json()["ids"][0]
    in_id = ids_by_name(c)["shot0.png"]
    t = c.post("/api/tags", json={"name": "Eryxian Music"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [out_id, in_id], "op": "add"}, headers=H)
    assert sorted(c.get("/api/items", params={"tag": str(t["id"])}).json()["ids"]) == sorted([out_id, in_id])
    assert out_id not in c.get("/api/items").json()["ids"]                     # "All library items": the library only
    assert next(x for x in c.get("/api/tags").json()["tags"] if x["id"] == t["id"])["count"] == 2


def test_add_library_folder_that_doesnt_exist_yet(env):
    c, db, sc, lib, tmp = env
    new = tmp / "drops" / "Inbox"
    assert c.post("/api/roots", json={"path": str(new)}, headers=H).status_code == 400
    r = c.post("/api/roots", json={"path": str(new), "create": True}, headers=H).json()
    assert new.is_dir() and r["path"] == str(new)


def test_regenerate_previews(env):
    c, db, sc, lib, tmp = env
    ids = ids_by_name(c)
    i = ids["shot1.png"]
    h = db.cat.execute("SELECT hash FROM files WHERE id = ?", (i,)).fetchone()[0]
    assert c.get(f"/t/{i}", params={"s": 1600}).status_code == 200 and db.has_thumb(h, 1600)
    tver = c.get(f"/api/items/{i}").json().get("tver")
    assert c.post("/api/remake", json={"ids": [i, 99999]}, headers=H).json() == {"queued": 1}
    assert not db.has_thumb(h, 1600)
    wait(sc)
    assert db.cat.execute("SELECT status FROM files WHERE id = ?", (i,)).fetchone()[0] == 1 and db.has_thumb(h, 256)
    assert tver is None or c.get(f"/api/items/{i}").json().get("tver") != tver


def test_picture_as_png_for_the_clipboard(env, tmp_path):
    c, db, sc, lib, tmp = env
    i = ids_by_name(c)["shot1.png"]
    r = c.get(f"/api/items/{i}/png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    import io
    assert Image.open(io.BytesIO(r.content)).size == (41, 30)
    p = c.get(f"/api/items/{i}/png", params={"file": 1}).json()["path"]
    assert os.path.isfile(p) and Image.open(p).size == (41, 30)
    assert c.get("/api/items/424242/png").status_code == 404


def test_svg_page_or_everything_in_the_viewer(env):
    c, db, sc, lib, tmp = env
    (lib / "a" / "page.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
                                        '<rect x="10" y="10" width="50" height="50" fill="red"/><rect x="150" y="20" width="40" height="40" fill="blue"/></svg>')
    sc.request_scan()
    wait(sc)
    i = ids_by_name(c)["page.svg"]
    page = c.get(f"/f/{i}", params={"all": 0}).text
    everything = c.get(f"/f/{i}", params={"all": 1}).text
    assert 'viewBox="0 0 100 100"' in page and 'viewBox="0 0 100 100"' not in everything
    assert 'viewBox="0 0 190' in everything.replace("190.0", "190")


def test_opening_the_page_from_another_site(env):
    """The desktop app's start page (another origin) may open Kurator; nothing else cross-site."""
    c, *_ = env
    nav = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}
    assert c.get("/", headers=nav).status_code == 200
    assert c.get("/api/state", headers=nav).status_code == 403
    assert c.get("/", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors"}).status_code == 403


def test_slow_file_named_while_indexing():
    from kurator.scanner import Scanner as S

    class F:
        def __init__(self, run): self.run = run
        def running(self): return self.run

    s = S.__new__(S)
    s.state = {"slow": None}
    a, b = F(True), F(False)
    started = {a: time.time() - 30}
    s._note_slow({a: "/x/big.blend", b: "/x/small.png"}, started)
    assert s.state["slow"]["name"] == "big.blend" and s.state["slow"]["secs"] >= 30 and b not in started
    s._note_slow({b: "/x/small.png"}, {})
    assert s.state["slow"] is None


# ---------------------------------------- updates, REAPER tracks, layers, page errors
def test_update_check(env, monkeypatch):
    from kurator import __version__, updates
    c, db, sc, lib, tmp = env
    calls = []

    def fake(timeout=6):
        calls.append(1)
        return {"latest": "v99.0.0", "name": "Kurator 99", "url": "https://github.com/eigensoundlab/kurator/releases/tag/v99.0.0", "published": "2027-01-01"}
    monkeypatch.setattr(updates, "_fetch", fake)
    u = c.get("/api/update").json()
    assert u["newer"] and u["latest"] == "v99.0.0" and u["current"] == __version__ and not u["skipped"]
    c.get("/api/update")
    assert len(calls) == 1                                                     # once a day
    c.get("/api/update", params={"force": 1})
    assert len(calls) == 2
    c.post("/api/update/skip", json={"version": "v99.0.0"}, headers=H)
    assert c.get("/api/update").json()["skipped"]
    c.put("/api/settings", json={"update_check": False}, headers=H)
    assert c.get("/api/update").json() ["enabled"] is False and len(calls) == 2
    assert updates.version_tuple("v.0.19.0") == updates.version_tuple("0.19.0") == (0, 19, 0) < updates.version_tuple("v0.19.1")

    def offline(timeout=6):
        raise OSError("no network")
    monkeypatch.setattr(updates, "_fetch", offline)
    u = c.get("/api/update", params={"force": 1}).json()
    assert u["error"] and u["latest"] == "v99.0.0"                               # what we knew stays


def test_reaper_tracks_and_krita_layers(env, monkeypatch):
    import wave
    import zipfile
    from kurator import apps
    c, db, sc, lib, tmp = env
    for n, secs in (("gravel", 1.5), ("wind", 0.25)):
        with wave.open(str(lib / "a" / f"{n}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * int(8000 * secs))
    Image.new("RGBA", (30, 20), (255, 0, 0, 128)).save(lib / "a" / "top.png")
    sc.request_scan()
    wait(sc)
    ids = ids_by_name(c)
    reaper, krita = tmp / "reaper.exe", tmp / "krita.exe"
    reaper.write_text(""); krita.write_text("")
    monkeypatch.setattr(apps, "_find", lambda *a: None)
    saved = {a["name"]: a for a in c.put("/api/apps", json={"custom": [{"name": "REAPER", "path": str(reaper)}, {"name": "Krita", "path": str(krita)}], "hidden": []}, headers=H).json()["apps"]}
    assert saved["REAPER"]["layout"] == "tracks" and saved["Krita"]["layout"] == "layers"
    started = []
    monkeypatch.setattr(apps.subprocess, "Popen", lambda cmd, **k: started.append(cmd))
    r = c.post("/api/open-with", json={"app": saved["REAPER"]["id"], "ids": [ids["gravel.wav"], ids["wind.wav"]], "layout": "tracks"}, headers=H).json()
    assert r["layout"] == "tracks"
    rpp = started[-1][-1]
    text = open(rpp, encoding="utf-8").read()
    assert rpp.endswith(".rpp") and text.count("<TRACK") == 2 and 'NAME "gravel"' in text and "LENGTH 1.500000" in text
    assert f'FILE "{lib / "a" / "wind.wav"}"' in text and "<SOURCE WAVE" in text
    c.post("/api/open-with", json={"app": saved["Krita"]["id"], "ids": [ids["top.png"], ids["shot1.png"]], "layout": "layers"}, headers=H)
    ora = started[-1][-1]
    with zipfile.ZipFile(ora) as z:
        assert z.namelist()[0] == "mimetype" and z.read("mimetype") == b"image/openraster"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        stack = z.read("stack.xml").decode()
        assert '<image w="41" h="30"' in stack and stack.index('name="top"') < stack.index('name="shot1"')
        assert Image.open(io.BytesIO(z.read("data/layer0.png"))).size == (30, 20)
    assert c.post("/api/open-with", json={"app": saved["REAPER"]["id"], "ids": [ids["top.png"]], "layout": "layers"}, headers=H).status_code == 400


def test_page_errors_go_to_the_log(env, capsys):
    c, *_ = env
    assert c.post("/api/client-log", json={"text": "status: TypeError: x is undefined"}, headers=H).json()["ok"]
    assert "[page] status: TypeError" in capsys.readouterr().err
