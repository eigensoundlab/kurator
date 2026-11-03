"""Tags, re-linking, browsing, pins, moves with undo, animation flag."""

import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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
def env(tmp_path):
    lib = tmp_path / "lib"
    (lib / "inbox").mkdir(parents=True)
    (lib / "sorted").mkdir()
    for i in range(6):
        Image.new("RGB", (40 + i, 40), (i * 40, 0, 0)).save(lib / "inbox" / f"img{i}.png")
        os.utime(lib / "inbox" / f"img{i}.png", (1500000000 + i * 86400 * 400,) * 2)
    (lib / "inbox" / "img0.png.xmp").write_text("<x/>")
    # animated GIF + WebP + APNG
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "green", "blue")]
    frames[0].save(lib / "inbox" / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    frames[0].save(lib / "inbox" / "anim.webp", save_all=True, append_images=frames[1:], duration=100, loop=0)
    frames[0].save(lib / "inbox" / "anim.png", save_all=True, append_images=frames[1:], duration=100, loop=0)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    Image.new("RGB", (20, 20)).save(outside / "o1.jpg")
    Image.new("RGB", (20, 20)).save(outside / "sub" / "deep.jpg")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait(sc)
    client = TestClient(create_app(db, sc, 8765), base_url=BASE)
    return client, db, sc, lib, outside


def ids_by_name(c, **q):
    it = c.get("/api/items", params=q).json()
    return dict(zip(it["n"], it["ids"])), it


def test_animation_flag(env):
    c, *_ = env
    names, it = ids_by_name(c)
    flags = dict(zip(it["n"], it["f"]))
    assert flags["anim.gif"] == 1 and flags["anim.webp"] == 1 and flags["anim.png"] == 1
    assert flags["img1.png"] == 0


def test_tags_apply_toggle_filter_hierarchy(env):
    c, db, *_ = env
    t = c.post("/api/tags", json={"name": " Creatures / Dragons "}, headers=H).json()
    assert t["name"] == "Creatures/Dragons" and t["hotkey"] == 1
    tags = {x["name"]: x for x in c.get("/api/tags").json()["tags"]}
    assert "Creatures" in tags  # parent created automatically
    names, _ = ids_by_name(c)
    a, b = names["img1.png"], names["img2.png"]
    r = c.post(f"/api/tags/{t['id']}/apply", json={"ids": [a]}, headers=H).json()
    assert r["added"] == [a]
    r = c.post(f"/api/tags/{t['id']}/apply", json={"ids": [a, b]}, headers=H).json()  # not all have it -> add
    assert r["added"] == [b]
    # filtering by the parent includes children
    _, it = ids_by_name(c, tag=str(tags["Creatures"]["id"]))
    assert sorted(it["ids"]) == sorted([a, b])
    _, it = ids_by_name(c, untagged=1)
    assert a not in it["ids"] and it["count"] == 9 - 2 + 0  # 6 png + 3 anim - 2 tagged
    r = c.post(f"/api/tags/{t['id']}/apply", json={"ids": [a, b]}, headers=H).json()  # all have it -> remove
    assert sorted(r["removed"]) == sorted([a, b])
    # rename parent renames children
    c.patch(f"/api/tags/{tags['Creatures']['id']}", json={"name": "Monsters"}, headers=H)
    assert any(x["name"] == "Monsters/Dragons" for x in c.get("/api/tags").json()["tags"])
    # hotkey moves between tags
    t2 = c.post("/api/tags", json={"name": "Icons", "hotkey": 1}, headers=H).json()
    assert t2["hotkey"] == 1
    assert c.get(f"/api/items/{a}").status_code == 200


def test_tags_follow_files_moved_outside_the_app(env):
    c, db, sc, lib, _ = env
    t = c.post("/api/tags", json={"name": "Items"}, headers=H).json()
    names, _ = ids_by_name(c)
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [names["img3.png"]]}, headers=H)
    shutil.move(str(lib / "inbox" / "img3.png"), str(lib / "sorted" / "renamed.png"))
    sc.request_scan(); wait(sc)
    names, it = ids_by_name(c, tag=str(t["id"]))
    assert list(names) == ["renamed.png"]


def test_browse_outside_folder_and_promote(env):
    c, db, sc, lib, outside = env
    lst = c.get("/api/fs/list", params={"path": str(outside)}).json()
    assert lst["images"] == 1 and lst["dirs"][0]["name"] == "sub" and lst["library"] is None
    r = c.post("/api/browse", json={"path": str(outside)}, headers=H).json()
    assert r["library"] is None
    names, _ = ids_by_name(c, folder=str(outside))
    assert list(names) == ["o1.jpg"]  # shallow: subfolder not included
    all_names, _ = ids_by_name(c)
    assert "o1.jpg" not in all_names  # browsed folders are not part of "All library items"
    folders = [f["path"] for f in c.get("/api/folders").json()]
    assert str(outside) not in folders  # browse folders are not part of the library tree
    # inside the library it's just a filter
    assert c.post("/api/browse", json={"path": str(lib / "inbox")}, headers=H).json()["library"] == str(lib)
    # promote: add as library folder -> absorbs the browse root, now recursive
    c.post("/api/roots", json={"path": str(outside)}, headers=H)
    wait(sc); time.sleep(0.2); wait(sc)
    names, _ = ids_by_name(c, folder=str(outside))
    assert set(names) == {"o1.jpg", "deep.jpg"}
    assert [r["kind"] for r in db.roots() if r["path"] == str(outside)] == ["library"]


def test_pins(env):
    c, *_ , lib, _ = env
    r = c.put("/api/pins", json={"paths": [str(lib / "sorted"), str(lib / "sorted"), " "]}, headers=H).json()
    assert r == [str(lib / "sorted")]
    assert c.get("/api/pins").json() == r


def test_move_plan_execute_undo(env):
    c, db, sc, lib, outside = env
    names, _ = ids_by_name(c)
    t = c.post("/api/tags", json={"name": "Keep"}, headers=H).json()
    ids = [names["img0.png"], names["img1.png"]]
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": ids}, headers=H)
    (lib / "sorted" / "img1.png").write_bytes(b"existing file - must not be overwritten")
    mtime_before = os.stat(lib / "inbox" / "img0.png").st_mtime_ns

    # destination outside the library is refused
    r = c.post("/api/move/plan", json={"ids": ids, "dest": str(outside)}, headers=H)
    assert r.status_code == 400

    plan = c.post("/api/move/plan", json={"ids": ids, "dest": str(lib / "sorted")}, headers=H).json()
    assert plan["count"] == 2
    tos = {os.path.basename(m["to"]) for m in plan["moves"]}
    assert tos == {"img0.png", "img1 (2).png"}

    res = c.post("/api/move", json={"ids": ids, "dest": str(lib / "sorted")}, headers=H).json()
    assert res["moved"] == 2 and not res["failed"]
    assert (lib / "sorted" / "img0.png").exists() and not (lib / "inbox" / "img0.png").exists()
    assert (lib / "sorted" / "img0.png.xmp").exists()  # sidecar moved along
    assert (lib / "sorted" / "img1.png").read_bytes().startswith(b"existing")  # untouched
    assert os.stat(lib / "sorted" / "img0.png").st_mtime_ns == mtime_before  # timestamp kept
    # catalog row updated in place: same id, tags kept
    d = c.get(f"/api/items/{ids[0]}").json()
    assert d["path"] == str(lib / "sorted" / "img0.png") and d["tags"] == [t["id"]]

    # a rescan doesn't lose anything
    sc.request_scan(); wait(sc)
    assert c.get(f"/api/items/{ids[0]}").json()["tags"] == [t["id"]]

    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["moved"] == 2
    assert (lib / "inbox" / "img0.png").exists() and (lib / "inbox" / "img0.png.xmp").exists()
    assert (lib / "inbox" / "img1.png").exists()
    assert c.post(f"/api/operations/{res['op_id']}/undo", headers=H).status_code == 400


def test_move_by_year_with_date_prefix(env):
    c, db, sc, lib, _ = env
    names, _ = ids_by_name(c)
    ids = [names["img2.png"]]
    res = c.post("/api/move", json={"ids": ids, "dest": str(lib / "sorted"), "by_year": True, "date_prefix": True},
                 headers=H).json()
    assert res["moved"] == 1
    d = c.get(f"/api/items/{ids[0]}").json()
    year = time.strftime("%Y", time.localtime(d["date_ts"]))
    day = time.strftime("%Y-%m-%d", time.localtime(d["date_ts"]))
    assert d["path"] == str(lib / "sorted" / year / f"{day}_img2.png")


def test_cross_device_move_verifies_and_keeps_times(tmp_path, monkeypatch):
    from kurator import fileops
    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(300000))
    os.utime(src, (1500000000, 1500000000))
    monkeypatch.setattr(fileops, "_same_device", lambda *a: False)  # force the copy path
    dst = tmp_path / "x" / "a.bin"
    assert fileops.move_file(str(src), str(dst)) == "copy"
    assert not src.exists() and dst.exists()
    assert int(os.stat(dst).st_mtime) == 1500000000
