"""v0.18, file tools: batch rename, delete (system trash or Kurator's trash folder) with undo,
duplicate cleanup, copies for delivery (clean, credit, zip), recent folders, quick destinations."""

from __future__ import annotations

import io
import os
import struct
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from kurator import trash
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


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
def env(tmp_path, monkeypatch):
    # the system trash of this test: a freedesktop trash inside tmp_path
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    if sys.platform.startswith("linux"):
        from send2trash import plat_other
        monkeypatch.setattr(plat_other, "XDG_DATA_HOME", os.fsencode(home / ".local" / "share"))
        monkeypatch.setattr(plat_other, "HOMETRASH_B", os.fsencode(home / ".local" / "share" / "Trash"))
    lib = tmp_path / "lib"
    (lib / "a").mkdir(parents=True)
    (lib / "b").mkdir()
    for i, c in enumerate(("red", "green", "blue")):
        Image.new("RGB", (40 + i, 30), c).save(lib / "a" / f"shot{i}.png")
        os.utime(lib / "a" / f"shot{i}.png", (1600000000 + i * 86400,) * 2)
    (lib / "a" / "shot0.png.xmp").write_text("<x/>")
    (lib / "a" / "shot0.png.meta").write_text("guid: abc")
    # exact duplicates: same bytes in a/ and b/
    Image.new("RGB", (50, 50), "purple").save(lib / "a" / "dup.png")
    (lib / "b" / "dup.png").write_bytes((lib / "a" / "dup.png").read_bytes())
    (lib / "b" / "dup copy.png").write_bytes((lib / "a" / "dup.png").read_bytes())
    os.utime(lib / "b" / "dup.png", (1500000000, 1500000000))
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait(sc)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    return c, db, sc, lib, tmp_path


def by_name(c):
    it = c.get("/api/items").json()
    out = {}
    for n, i in zip(it["n"], it["ids"]):
        d = os.path.basename(os.path.dirname(c.get(f"/api/items/{i}").json()["path"]))
        out[f"{d}/{n}"] = i
    return out


# ---------------------------------------------------------------- rename
def test_rename_template_counter_swap_and_undo(env):
    c, db, sc, lib, _ = env
    ids = by_name(c)
    t = c.post("/api/tags", json={"name": "Creatures/Dragons"}, headers=H).json()
    sel = [ids["a/shot2.png"], ids["a/shot0.png"], ids["a/shot1.png"]]          # grid order = counter order
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": sel}, headers=H)
    body = {"ids": sel, "template": "{date:%Y%m%d}_{tag}_{n}", "pad": 2, "start": 1}
    p = c.post("/api/rename/plan", json=body, headers=H).json()
    assert [os.path.basename(r["to"]) for r in p["renames"]] == ["20200915_Dragons_01.png", "20200913_Dragons_02.png", "20200914_Dragons_03.png"]
    res = c.post("/api/rename", json=body, headers=H).json()
    assert res["renamed"] == 3 and not res["failed"]
    assert (lib / "a" / "20200913_Dragons_02.png").exists() and not (lib / "a" / "shot0.png").exists()
    assert (lib / "a" / "20200913_Dragons_02.png.xmp").exists() and (lib / "a" / "20200913_Dragons_02.png.meta").exists()
    d = c.get(f"/api/items/{ids['a/shot0.png']}").json()                       # same row: tags kept
    assert d["name"] == "20200913_Dragons_02.png" and d["tags"] == [t["id"]]
    # renumber in place: names freed by the batch are reused, even swapped
    swap = [ids["a/shot1.png"], ids["a/shot0.png"]]
    r2 = c.post("/api/rename", json={"ids": swap, "template": "{n}", "start": 1}, headers=H).json()
    assert r2["renamed"] == 2
    c.post("/api/rename", json={"ids": swap[::-1], "template": "{n}", "start": 1}, headers=H)   # swap 1 <-> 2
    assert c.get(f"/api/items/{swap[0]}").json()["name"] == "2.png" and c.get(f"/api/items/{swap[1]}").json()["name"] == "1.png"
    # find / replace, case; undo the first rename
    p3 = c.post("/api/rename/plan", json={"ids": [ids["a/shot2.png"]], "template": "{name}", "find": "Dragons", "replace": "wyrm",
                                         "case": "upper"}, headers=H).json()
    assert os.path.basename(p3["renames"][0]["to"]) == "20200915_WYRM_01.png"
    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["renamed"] == 1                                                  # the others were renamed again since
    assert (lib / "a" / "shot2.png").exists() and (lib / "a" / "1.png").exists()
    for bad in ({"template": ""}, {"template": "{nope}"}, {"template": "{name}", "find": "(", "regex": True}, {"template": "{name}", "start": "x"}):
        assert c.post("/api/rename/plan", json={"ids": sel, **bad}, headers=H).status_code == 400


def test_rename_case_only(env):
    c, db, sc, lib, _ = env
    ids = by_name(c)
    res = c.post("/api/rename", json={"ids": [ids["a/shot1.png"]], "template": "{name}", "case": "upper"}, headers=H).json()
    assert res["renamed"] == 1 and "SHOT1.png" in os.listdir(lib / "a") and "shot1.png" not in os.listdir(lib / "a")


def test_rename_plan_on_a_case_insensitive_disk(env):
    """On Windows and macOS "SHOT2.png" and "shot2.png" are one file: the new name points to the file
    being renamed, so it is free. A case-sensitive disk (Linux) gets the same situation from a hard
    link. A name that points to another file is taken, on any disk."""
    c, db, sc, lib, _ = env
    ids = by_name(c)
    upper = lib / "a" / "SHOT2.png"
    if not upper.exists():                       # case-sensitive: make the other name point to the file
        os.link(lib / "a" / "shot2.png", upper)
    assert os.path.samefile(upper, lib / "a" / "shot2.png")
    p = c.post("/api/rename/plan", json={"ids": [ids["a/shot2.png"]], "template": "{name}", "case": "upper"}, headers=H).json()
    assert os.path.basename(p["renames"][0]["to"]) == "SHOT2.png" and not p["renames"][0]["clash"]
    p = c.post("/api/rename/plan", json={"ids": [ids["a/shot0.png"]], "template": "dup"}, headers=H).json()   # a/dup.png: another file
    assert os.path.basename(p["renames"][0]["to"]) == "dup (2).png" and p["renames"][0]["clash"]


# ---------------------------------------------------------------- delete
def test_delete_to_kurators_trash_and_undo(env):
    c, db, sc, lib, tmp = env
    ids = by_name(c)
    t = c.post("/api/tags", json={"name": "Keep"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [ids["a/shot0.png"]]}, headers=H)
    c.post("/api/info", json={"ids": [ids["a/shot0.png"]], "author": "Ada"}, headers=H)
    assert c.put("/api/settings", json={"delete_mode": "kurator", "trash_days": 7}, headers=H).json()["delete_mode"] == "kurator"
    assert c.put("/api/settings", json={"delete_mode": "nope"}, headers=H).status_code == 400
    res = c.post("/api/delete", json={"ids": [ids["a/shot0.png"], ids["a/shot1.png"]]}, headers=H).json()
    assert res["deleted"] == 2 and res["mode"] == "kurator"
    assert not (lib / "a" / "shot0.png").exists() and not (lib / "a" / "shot0.png.xmp").exists() and not (lib / "a" / "shot0.png.meta").exists()
    assert c.get(f"/api/items/{ids['a/shot0.png']}").status_code == 404
    st = c.get("/api/trash").json()
    assert st["files"] == 4 and st["roots"] == [os.path.join(str(tmp / "data"), "trash")]  # 2 pictures + 2 sidecars
    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["restored"] == 2 and not u["failed"]
    assert (lib / "a" / "shot0.png").exists() and (lib / "a" / "shot0.png.xmp").exists() and (lib / "a" / "shot0.png.meta").exists()
    d = c.get(f"/api/items/{ids['a/shot0.png']}").json()                      # same id, tags and info back
    assert d["tags"] == [t["id"]] and d["info"]["author"] == "Ada" and d["status"] == 1
    assert db.cat.execute("SELECT COUNT(*) FROM orphan_tags").fetchone()[0] == 0
    # emptying: a deleted file can't come back any more
    res2 = c.post("/api/delete", json={"ids": [ids["a/shot2.png"]]}, headers=H).json()
    assert c.post("/api/trash/empty", headers=H).json()["removed"] == 1
    u2 = c.post(f"/api/operations/{res2['op_id']}/undo", headers=H).json()
    assert u2["restored"] == 0 and "emptied" in u2["failed"][0]["error"]


def test_delete_old_batches_emptied(env):
    c, db, sc, lib, tmp = env
    ids = by_name(c)
    c.put("/api/settings", json={"delete_mode": "kurator", "trash_days": 3}, headers=H)
    c.post("/api/delete", json={"ids": [ids["a/shot2.png"]]}, headers=H)
    assert trash.auto_empty(db)["removed"] == 0
    batch = next(p for p in (tmp / "data" / "trash").iterdir())
    old = time.time() - 5 * 86400
    os.utime(batch / "kurator-trash.json", (old, old))
    assert trash.auto_empty(db)["removed"] == 1 and not batch.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the freedesktop trash (Linux)")
def test_delete_to_system_trash_and_undo(env):
    c, db, sc, lib, tmp = env
    ids = by_name(c)
    t = c.post("/api/tags", json={"name": "Keep"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [ids["a/shot0.png"]]}, headers=H)
    assert c.get("/api/settings").json()["delete_mode"] == "system"
    res = c.post("/api/delete", json={"ids": [ids["a/shot0.png"]]}, headers=H).json()
    assert res["deleted"] == 1 and res["mode"] == "system"
    tr = tmp / "home" / ".local" / "share" / "Trash"
    assert sorted(os.listdir(tr / "files")) == ["shot0.png", "shot0.png.meta", "shot0.png.xmp"]
    # restored by hand from the trash: the scan finds it and gives it its tags back (by content hash)
    (tr / "files" / "shot0.png").rename(lib / "a" / "shot0.png")
    (tr / "info" / "shot0.png.trashinfo").unlink()
    sc.request_scan(); wait(sc)
    back = by_name(c)["a/shot0.png"]
    assert c.get(f"/api/items/{back}").json()["tags"] == [t["id"]]
    # ... and Ctrl+Z from the trash
    res2 = c.post("/api/delete", json={"ids": [back]}, headers=H).json()
    u = c.post(f"/api/operations/{res2['op_id']}/undo", headers=H).json()
    assert u["restored"] == 1 and (lib / "a" / "shot0.png").exists()
    assert c.get(f"/api/items/{back}").json()["tags"] == [t["id"]]


def test_no_recycle_bin_drive_goes_to_kurators_trash(env, monkeypatch):
    c, db, sc, lib, tmp = env
    monkeypatch.setattr(trash, "no_recycle_bin", lambda path: True)      # e.g. a USB stick or a network share on Windows
    monkeypatch.setattr(trash, "to_system_trash", lambda path: (_ for _ in ()).throw(AssertionError("must not be used")))
    ids = by_name(c)
    res = c.post("/api/delete", json={"ids": [ids["a/shot1.png"]]}, headers=H).json()
    assert res["deleted"] == 1 and res["mode"] == "system" and res["held_instead"] == 1
    assert c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()["restored"] == 1 and (lib / "a" / "shot1.png").exists()


def test_restore_from_windows_recycle_bin_and_mac_trash(tmp_path):
    # Windows: $I index files (version 2: length-prefixed path) next to $R data files
    bin_ = tmp_path / "$Recycle.Bin" / "S-1-5-21-1"
    bin_.mkdir(parents=True)
    orig = tmp_path / "art" / "dragon.png"
    data = b"pixels" * 100
    (bin_ / "$RAB12CD.png").write_bytes(data)
    name = str(orig).encode("utf-16-le") + b"\0\0"
    ft = int(time.time() * 10_000_000) + 116444736000000000
    (bin_ / "$IAB12CD.png").write_bytes(struct.pack("<qqqi", 2, len(data), ft, len(name) // 2) + name)
    assert trash._read_index_file(str(bin_ / "$IAB12CD.png"))[0] == str(orig)
    assert not trash._restore_windows(str(orig), len(data) + 1, time.time() - 10, [str(bin_)])     # other size
    assert trash._restore_windows(str(orig), len(data), time.time() - 10, [str(bin_)])
    assert orig.read_bytes() == data and not (bin_ / "$IAB12CD.png").exists()
    # version 1 (Vista - 8.1): a fixed 520-byte field
    (bin_ / "$RXY.png").write_bytes(data)
    (bin_ / "$IXY.png").write_bytes(struct.pack("<qqq", 1, len(data), ft) + str(orig).encode("utf-16-le").ljust(520, b"\0"))
    orig.unlink()
    assert trash._restore_windows(str(orig), len(data), time.time() - 10, [str(bin_)]) and orig.exists()
    # macOS: found by name, size and time (the Trash renames clashes)
    mac = tmp_path / ".Trash"
    mac.mkdir()
    orig.rename(mac / "dragon 10.42.17.png")
    st = os.stat(mac / "dragon 10.42.17.png")
    assert trash._restore_mac(str(orig), st.st_size, st.st_mtime_ns, [str(mac)]) and orig.exists()


# ---------------------------------------------------------------- duplicates
def test_duplicates_keep_one_merge_tags_undo(env):
    c, db, sc, lib, _ = env
    c.put("/api/settings", json={"delete_mode": "kurator"}, headers=H)
    ids = by_name(c)
    t1 = c.post("/api/tags", json={"name": "Purple"}, headers=H).json()
    t2 = c.post("/api/tags", json={"name": "Final"}, headers=H).json()
    c.post(f"/api/tags/{t1['id']}/apply", json={"ids": [ids["b/dup copy.png"]]}, headers=H)
    c.post(f"/api/tags/{t2['id']}/apply", json={"ids": [ids["a/dup.png"], ids["b/dup copy.png"]]}, headers=H)
    view = c.get("/api/items", params={"dupes": 1}).json()["ids"]
    assert len(view) == 3
    p = c.post("/api/duplicates/plan", json={"ids": view, "keep": "tags"}, headers=H).json()
    assert p["count"] == 2 and p["groups"][0]["keep"]["id"] == ids["b/dup copy.png"]
    p = c.post("/api/duplicates/plan", json={"ids": view, "keep": "oldest"}, headers=H).json()
    assert p["groups"][0]["keep"]["id"] == ids["b/dup.png"]
    p = c.post("/api/duplicates/plan", json={"ids": view, "keep": "folder", "folder": str(lib / "a")}, headers=H).json()
    assert p["groups"][0]["keep"]["id"] == ids["a/dup.png"]
    # only files in the scope are removed: the copy that stays may be outside it
    p = c.post("/api/duplicates/plan", json={"ids": [ids["a/dup.png"]], "keep": "oldest"}, headers=H).json()
    assert p["count"] == 1 and p["groups"][0]["remove"][0]["id"] == ids["a/dup.png"] and not p["groups"][0]["keep"]["in_scope"]
    res = c.post("/api/duplicates/clean", json={"ids": view, "keep": "oldest"}, headers=H).json()
    assert res["deleted"] == 2 and res["merged"] == 2
    kept = c.get(f"/api/items/{ids['b/dup.png']}").json()
    assert sorted(kept["tags"]) == sorted([t1["id"], t2["id"]])
    assert not (lib / "a" / "dup.png").exists() and (lib / "b" / "dup.png").exists()
    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["restored"] == 2 and c.get(f"/api/items/{ids['b/dup.png']}").json()["tags"] == []
    assert sorted(c.get(f"/api/items/{ids['b/dup copy.png']}").json()["tags"]) == sorted([t1["id"], t2["id"]])
    assert c.post("/api/duplicates/plan", json={"ids": view, "keep": "folder"}, headers=H).status_code == 400


# ---------------------------------------------------------------- delivery
def _pictures(lib):
    ex = Image.Exif()
    ex[0x013B] = "Private Person"
    ex.get_ifd(0x8825)[1] = "N"
    ex.get_ifd(0x8825)[2] = (50.0, 5.0, 0.0)
    ex.get_ifd(0x8769)[0x9003] = "2021:05:06 07:08:09"
    Image.new("RGB", (60, 40), (10, 200, 10)).save(lib / "a" / "photo.jpg", exif=ex.tobytes(), quality=90)
    pi = PngImagePlugin.PngInfo()
    pi.add_text("parameters", "secret prompt, Steps: 30, Seed: 42")
    pi.add_text("workflow", '{"nodes": []}')
    Image.new("RGBA", (30, 30), (0, 0, 255, 128)).save(lib / "a" / "gen.png", pnginfo=pi, exif=ex.tobytes())
    Image.new("RGB", (30, 30), "white").save(lib / "a" / "plain.webp", exif=ex.tobytes())


def test_copy_for_delivery_clean_credit_and_zip(env):
    c, db, sc, lib, tmp = env
    _pictures(lib)
    sc.request_scan(); wait(sc)
    ids = by_name(c)
    sel = [ids["a/photo.jpg"], ids["a/gen.png"], ids["a/plain.webp"], ids["a/shot0.png"]]
    c.post("/api/info", json={"ids": sel, "author": "Ada Artist", "licence": "CC BY 4.0"}, headers=H)
    out = tmp / "delivery"
    res = c.post("/api/copy", json={"ids": sel, "dest": str(out), "clean": True, "credit": True}, headers=H).json()
    assert res["copied"] == 4 and res["cleaned"] == 4 and not res["failed"]
    with Image.open(out / "photo.jpg") as im:
        e = im.getexif()
        assert not e.get_ifd(0x8825) and e.get(0x013B) == "Ada Artist" and e.get(0x8298) == "CC BY 4.0"
        assert e.get_ifd(0x8769).get(0x9003) == "2021:05:06 07:08:09"                     # the date stays
    raw = (out / "gen.png").read_bytes()
    assert b"secret prompt" not in raw and b"workflow" not in raw and b"Ada Artist" in raw and b"creativecommons.org" in raw
    with Image.open(out / "gen.png") as im:
        assert im.getpixel((3, 3)) == (0, 0, 255, 128) and not im.getexif().get_ifd(0x8825)
    assert b"Ada Artist" in (out / "plain.webp").read_bytes()
    assert not (out / "shot0.png.xmp").exists()                                             # sidecars stay home
    assert os.stat(out / "photo.jpg").st_mtime_ns == os.stat(lib / "a" / "photo.jpg").st_mtime_ns
    assert b"secret prompt" in (lib / "a" / "gen.png").read_bytes()                         # originals untouched
    # the same as one zip; undo deletes it
    z = c.post("/api/copy", json={"ids": sel, "dest": str(out), "clean": True, "zip": "for client"}, headers=H).json()
    assert z["copied"] == 4 and z["zip"].endswith("for client.zip")
    with zipfile.ZipFile(z["zip"]) as zf:
        assert sorted(zf.namelist()) == ["gen.png", "photo.jpg", "plain.webp", "shot0.png"]
        assert b"secret prompt" not in zf.read("gen.png") and zf.getinfo("photo.jpg").compress_type == zipfile.ZIP_STORED
    assert c.post(f"/api/operations/{z['op_id']}/undo", headers=H).json()["removed"] == 1 and not os.path.exists(z["zip"])
    # into the library: a cleaned copy is indexed anew (other bytes), with the tags
    t = c.post("/api/tags", json={"name": "Hero"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [ids["a/gen.png"]]}, headers=H)
    r = c.post("/api/copy", json={"ids": [ids["a/gen.png"]], "dest": str(lib / "b"), "clean": True}, headers=H).json()
    wait(sc)
    d = c.get(f"/api/items/{r['new_ids'][0]}").json()
    assert d["tags"] == [t["id"]] and d["hash"] != c.get(f"/api/items/{ids['a/gen.png']}").json()["hash"]


# ---------------------------------------------------------------- folders, quick destinations
def test_recent_folders_mkdir_and_send_slots(env):
    c, db, sc, lib, tmp = env
    ids = by_name(c)
    c.post("/api/copy", json={"ids": [ids["a/shot1.png"]], "dest": str(tmp / "out1")}, headers=H)
    c.post("/api/move", json={"ids": [ids["a/shot2.png"]], "dest": str(lib / "b")}, headers=H)
    rec = [r["path"] for r in c.get("/api/folders/recent").json()["recent"]]
    assert rec == [str(lib / "b"), str(tmp / "out1")]
    c.post("/api/copy", json={"ids": [ids["a/shot1.png"]], "dest": str(tmp / "out1")}, headers=H)
    assert [r["path"] for r in c.get("/api/folders/recent").json()["recent"]][0] == str(tmp / "out1")
    assert c.post("/api/folders/recent/forget", json={"path": str(lib / "b")}, headers=H).json()["recent"][0]["path"] == str(tmp / "out1")
    made = c.post("/api/fs/mkdir", json={"parent": str(tmp), "name": "New: folder"}, headers=H).json()["path"]
    assert os.path.isdir(made) and os.path.basename(made) == "New_ folder"
    assert c.post("/api/fs/mkdir", json={"parent": str(tmp), "name": "  "}, headers=H).status_code == 400
    # quick destinations: global and per project
    proj = c.post("/api/tags", json={"name": "Game A", "facet": "project"}, headers=H).json()
    s = c.put("/api/send-slots", json={"scope": "global", "slots": {"1": {"path": str(tmp / "out1"), "action": "copy", "label": "Client"},
                                                                    "2": {"path": ""}}}, headers=H).json()
    assert s == {"global": {"1": {"path": str(tmp / "out1"), "action": "copy", "label": "Client"}}}
    s = c.put("/api/send-slots", json={"scope": str(proj["id"]), "slots": {"1": {"path": str(lib / "b"), "action": "move"}}}, headers=H).json()
    assert s[str(proj["id"])]["1"]["action"] == "move" and c.get("/api/send-slots").json() == s
    for bad in ({"scope": "global", "slots": {"0": {"path": "x"}}}, {"scope": "global", "slots": {"1": {"path": "x", "action": "zap"}}},
                {"scope": "99999", "slots": {}}):
        assert c.put("/api/send-slots", json=bad, headers=H).status_code == 400


def test_ui_files_are_revalidated(env):
    """After an update the browser must not keep running old scripts (heuristic caching)."""
    c = env[0]
    r = c.get("/static/js/sidebar.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    assert c.get("/static/js/sidebar.js", headers={"if-none-match": r.headers["etag"]}).status_code == 304
    assert c.get("/").headers["cache-control"] == "no-cache"


# ---------------------------------------------------------------- files dropped onto the page
def test_drop_match_receive_and_undo(env):
    c, db, sc, lib, tmp = env
    ids = by_name(c)
    st = os.stat(lib / "a" / "shot1.png")
    files = [{"name": "SHOT1.png", "size": st.st_size, "mtime": st.st_mtime_ns / 1e6 + 1500},   # case, FAT-like time
             {"name": "shot1.png", "size": st.st_size + 1, "mtime": st.st_mtime_ns / 1e6},       # other size
             {"name": "new.png", "size": 10, "mtime": 0}]
    assert c.post("/api/drop/match", json={"files": files}, headers=H).json()["ids"] == [[ids["a/shot1.png"]], [], []]
    # a new file into the library: its own name (never overwriting), its time, a catalog row to index
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), "orange").save(buf, "PNG")
    q = {"batch": "b1", "dest": str(lib / "b"), "name": "C:\\fake\\dup.png", "mtime": 1600000000000}
    r = c.post("/api/drop/file", params=q, content=buf.getvalue(), headers=H).json()
    assert r["path"] == str(lib / "b" / "dup (2).png") and r["id"]
    assert os.stat(r["path"]).st_mtime == 1600000000
    assert c.post("/api/drop/file", params={**q, "name": "notes.txt"}, content=b"hi", headers=H).status_code == 400
    out = c.post("/api/drop/file", params={**q, "dest": str(tmp / "elsewhere"), "name": "x.png"}, content=buf.getvalue(), headers=H).json()
    assert out["id"] is None and os.path.exists(out["path"])                                 # outside: only saved
    assert not [n for n in os.listdir(lib / "b") if n.endswith(".part")]
    done = c.post("/api/drop/done", json={"batch": "b1"}, headers=H).json()
    assert done["received"] == 2 and done["indexed"] == 1 and done["ids"] == [r["id"]]
    wait(sc)
    assert c.get(f"/api/items/{r['id']}").json()["width"] == 20
    assert c.post(f"/api/operations/{done['op_id']}/undo", headers=H).json()["removed"] == 2
    assert not os.path.exists(r["path"]) and not os.path.exists(out["path"])
    assert c.post("/api/drop/done", json={"batch": "nope"}, headers=H).json()["received"] == 0
