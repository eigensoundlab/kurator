"""v0.18: copy to a folder (with undo), resampled copies, hotkeys per project."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

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
    Image.new("RGB", (400, 200), (200, 30, 30)).save(lib / "inbox" / "wide.png")
    os.utime(lib / "inbox" / "wide.png", (1500000000, 1500000000))
    (lib / "inbox" / "wide.png.xmp").write_text("<x:xmpmeta/>")
    (lib / "inbox" / "wide.png.meta").write_text("guid: 0123456789abcdef")    # Unity: never copied
    alpha = Image.new("RGBA", (120, 300), (0, 0, 255, 0))
    alpha.paste((0, 0, 255, 255), (20, 20, 100, 280))
    alpha.save(lib / "inbox" / "tall.png")
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    ex = Image.Exif()
    ex[0x0132] = "2011:02:03 04:05:06"
    ex.get_ifd(0x8769)[0x9003] = "2011:02:03 04:05:06"
    Image.new("RGB", (640, 480), (10, 120, 40)).save(lib / "inbox" / "photo.jpg", quality=90, icc_profile=srgb, exif=ex.tobytes())
    sprite = Image.new("RGB", (4, 4), (0, 0, 0))
    sprite.putpixel((1, 1), (255, 255, 255))
    sprite.save(lib / "inbox" / "sprite.png")
    frames = [Image.new("RGB", (64, 32), c) for c in ("red", "green", "blue")]
    frames[0].save(lib / "inbox" / "anim.gif", save_all=True, append_images=frames[1:], duration=[80, 120, 160], loop=0)
    Image.new("RGB", (30, 30), "white").save(lib / "inbox" / "small.jpg")
    outside = tmp_path / "outside"
    outside.mkdir()
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait(sc)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    it = c.get("/api/items").json()
    return c, db, sc, lib, outside, dict(zip(it["n"], it["ids"]))


# ---------------------------------------------------------------- copy
def test_copy_into_library_keeps_tags_dates_and_undoes(env):
    c, db, sc, lib, outside, ids = env
    t = c.post("/api/tags", json={"name": "Keep"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [ids["wide.png"]]}, headers=H)
    c.post("/api/info", json={"ids": [ids["wide.png"]], "author": "Ada", "licence": "CC-BY-4.0"}, headers=H)
    src = lib / "inbox" / "wide.png"
    (lib / "sorted" / "wide.png").write_bytes(b"not a picture - must not be overwritten")

    plan = c.post("/api/copy/plan", json={"ids": [ids["wide.png"]], "dest": str(lib / "sorted")}, headers=H).json()
    assert plan["count"] == 1 and plan["in_library"] and os.path.basename(plan["copies"][0]["to"]) == "wide (2).png"
    res = c.post("/api/copy", json={"ids": [ids["wide.png"]], "dest": str(lib / "sorted")}, headers=H).json()
    assert res["copied"] == 1 and res["indexed"] == 1 and not res["failed"] and res["op_id"]
    dst = lib / "sorted" / "wide (2).png"
    assert src.exists() and dst.read_bytes() == src.read_bytes()                      # the original stays
    assert (lib / "sorted" / "wide.png").read_bytes().startswith(b"not a picture")      # untouched
    assert os.stat(dst).st_mtime_ns == os.stat(src).st_mtime_ns                        # date kept
    assert (lib / "sorted" / "wide (2).png.xmp").exists()                              # XMP sidecar copied
    assert not (lib / "sorted" / "wide (2).png.meta").exists()                         # Unity GUID is not
    new = res["new_ids"][0]
    d = c.get(f"/api/items/{new}").json()
    orig = c.get(f"/api/items/{ids['wide.png']}").json()
    assert d["path"] == str(dst) and d["tags"] == [t["id"]] and d["hash"] == orig["hash"] and d["width"] == 400
    assert d["info"]["author"] == "Ada" and d["info"]["licence"] == "CC-BY-4.0"
    assert db.cat.execute("SELECT status FROM files WHERE id = ?", (new,)).fetchone()[0] == 1   # nothing to index
    sc.request_scan(); wait(sc)                                                        # a rescan keeps it as it is
    assert c.get(f"/api/items/{new}").json()["tags"] == [t["id"]]

    # copy into the same folder: a duplicate next to it; without tags
    r2 = c.post("/api/copy", json={"ids": [ids["wide.png"]], "dest": str(lib / "inbox"), "keep_tags": False}, headers=H).json()
    dup = lib / "inbox" / "wide (2).png"
    assert r2["copied"] == 1 and dup.exists() and c.get(f"/api/items/{r2['new_ids'][0]}").json()["tags"] == []

    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["removed"] == 1 and not dst.exists() and not (lib / "sorted" / "wide (2).png.xmp").exists()
    assert src.exists() and (lib / "inbox" / "wide.png.xmp").exists()
    assert c.get(f"/api/items/{new}").status_code == 404
    assert c.post(f"/api/operations/{res['op_id']}/undo", headers=H).status_code == 400
    # a copy changed since is kept
    dup.write_bytes(dup.read_bytes() + b"edited")
    u2 = c.post(f"/api/operations/{r2['op_id']}/undo", headers=H).json()
    assert u2["removed"] == 0 and u2["kept"][0]["reason"] == "changed since it was copied" and dup.exists()


def test_copy_outside_library_by_year(env):
    c, db, sc, lib, outside, ids = env
    sel = [ids["wide.png"], ids["photo.jpg"]]
    assert c.post("/api/copy/plan", json={"ids": sel, "dest": "  "}, headers=H).status_code == 400
    plan = c.post("/api/copy/plan", json={"ids": sel, "dest": str(outside / "client"), "by_year": True}, headers=H).json()
    assert plan["count"] == 2 and not plan["in_library"]
    res = c.post("/api/copy", json={"ids": sel, "dest": str(outside / "client"), "by_year": True}, headers=H).json()
    assert res["copied"] == 2 and res["indexed"] == 0 and res["new_ids"] == []
    year = time.strftime("%Y", time.localtime(1500000000))
    assert (outside / "client" / year / "wide.png").exists()
    assert db.cat.execute("SELECT COUNT(*) FROM files").fetchone()[0] == len(ids)        # catalog unchanged
    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["removed"] == 2 and not (outside / "client" / year / "wide.png").exists()


def test_copy_into_skipped_folder_is_not_catalogued(env):
    c, db, sc, lib, outside, ids = env
    res = c.post("/api/copy", json={"ids": [ids["wide.png"]], "dest": str(lib / ".hidden")}, headers=H).json()
    assert res["copied"] == 1 and res["indexed"] == 0 and (lib / ".hidden" / "wide.png").exists()


# ---------------------------------------------------------------- resample
def _run(c, sc, body):
    r = c.post("/api/resample/run", json=body, headers=H)
    assert r.status_code == 200, r.text
    t = time.time()
    while c.get("/api/resample/job").json()["running"]:
        assert time.time() - t < 60
        time.sleep(0.05)
    wait(sc)
    return c.get("/api/resample/job").json()


def test_resample_sizes_and_formats(env):
    c, db, sc, lib, outside, ids = env
    from kurator import resample
    assert resample.target_size(400, 200, "percent", 50) == (200, 100)
    assert resample.target_size(400, 200, "long", 100) == (100, 50)
    assert resample.target_size(400, 200, "height", 50) == (100, 50)
    assert resample.target_size(400, 200, "fit", 100, 100) == (100, 50)
    assert resample.target_size(400, 200, "exact", 64, 64) == (64, 64)

    t = c.post("/api/tags", json={"name": "Hero"}, headers=H).json()
    c.post(f"/api/tags/{t['id']}/apply", json={"ids": [ids["photo.jpg"], ids["tall.png"]]}, headers=H)
    sel = [ids[n] for n in ("wide.png", "tall.png", "photo.jpg", "small.jpg")]
    body = {"ids": sel, "mode": "long", "value": 100}
    plan = c.post("/api/resample/plan", json=body, headers=H).json()
    assert plan["count"] == 3 and plan["skipped"] == {"already smaller than that": 1}
    names = {os.path.basename(i["to"]): i["new"] for i in plan["items"]}
    assert names == {"wide_100x50.png": [100, 50], "tall_40x100.png": [40, 100], "photo_100x75.jpg": [100, 75]}

    job = _run(c, sc, body)
    res = job["result"]
    assert res["written"] == 3 and res["failed"] == 0 and len(res["new_ids"]) == 3 and res["op_id"]
    with Image.open(lib / "inbox" / "photo_100x75.jpg") as im:
        assert im.size == (100, 75) and im.info.get("icc_profile")                          # colour profile kept
        assert im.getexif().get_ifd(0x8769).get(0x9003) == "2011:02:03 04:05:06"           # EXIF date kept
    assert os.stat(lib / "inbox" / "photo_100x75.jpg").st_mtime_ns == os.stat(lib / "inbox" / "photo.jpg").st_mtime_ns
    with Image.open(lib / "inbox" / "tall_40x100.png") as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0                             # transparency kept
    # indexed at once, with the tags
    items = c.get("/api/items").json()
    by = dict(zip(items["n"], items["ids"]))
    d = c.get(f"/api/items/{by['photo_100x75.jpg']}").json()
    assert d["tags"] == [t["id"]] and d["width"] == 100 and d["height"] == 75
    assert time.strftime("%Y", time.localtime(d["date_ts"])) == "2011"
    assert (lib / "inbox" / "photo.jpg").exists() and Image.open(lib / "inbox" / "photo.jpg").size == (640, 480)

    # undo deletes the copies
    u = c.post(f"/api/operations/{res['op_id']}/undo", headers=H).json()
    assert u["removed"] == 3 and not (lib / "inbox" / "photo_100x75.jpg").exists()
    assert c.get(f"/api/items/{by['photo_100x75.jpg']}").status_code == 404


def test_resample_pixel_art_animation_and_folders(env, tmp_path):
    c, db, sc, lib, outside, ids = env
    # pixel art: nearest neighbour, 300 %, into a subfolder next to the original
    _run(c, sc, {"ids": [ids["sprite.png"]], "mode": "percent", "value": 300, "enlarge": True, "filter": "nearest",
                 "where": "subfolder", "folder": "x3", "suffix": "@{pct}"})
    out = lib / "inbox" / "x3" / "sprite@300.png"
    with Image.open(out) as im:
        assert im.size == (12, 12) and im.getpixel((3, 3)) == (255, 255, 255) and im.getpixel((5, 5)) == (255, 255, 255)
        assert im.getpixel((2, 2)) == (0, 0, 0) and im.getpixel((6, 6)) == (0, 0, 0)        # hard edges
    # enlarge is off by default
    plan = c.post("/api/resample/plan", json={"ids": [ids["sprite.png"]], "mode": "percent", "value": 200}, headers=H).json()
    assert plan["count"] == 0

    # animated GIF keeps its frames and timing; RGBA -> JPEG is flattened onto white, into one folder outside
    _run(c, sc, {"ids": [ids["anim.gif"]], "mode": "width", "value": 32})
    with Image.open(lib / "inbox" / "anim_32x16.gif") as im:
        assert im.size == (32, 16) and im.n_frames == 3
        im.seek(2)
        assert im.info["duration"] == 160 and im.convert("RGB").getpixel((5, 5))[2] > 200
    job = _run(c, sc, {"ids": [ids["tall.png"]], "mode": "exact", "value": 64, "value2": 64, "format": "jpg",
                       "quality": 85, "where": "folder", "folder": str(outside / "web"), "suffix": ""})
    with Image.open(outside / "web" / "tall.jpg") as im:
        assert im.format == "JPEG" and im.size == (64, 64) and min(im.getpixel((1, 1))) > 240   # white, not black
    assert job["result"]["new_ids"] == []                                                 # outside: not catalogued

    # bad requests
    for bad in ({"mode": "fit", "value": 100}, {"mode": "nope", "value": 1}, {"mode": "long", "value": 0},
                {"mode": "long", "value": 100, "where": "folder", "folder": ""}, {"mode": "long", "value": 100, "format": "bmp"}):
        assert c.post("/api/resample/plan", json={"ids": [ids["wide.png"]], **bad}, headers=H).status_code == 400


# ---------------------------------------------------------------- hotkeys per project
def test_project_hotkeys(env):
    c, db, sc, lib, outside, ids = env
    game = c.post("/api/tags", json={"name": "Game A", "facet": "project", "auto_hotkey": False}, headers=H).json()
    hero = c.post("/api/tags", json={"name": "Hero", "auto_hotkey": False}, headers=H).json()
    tree = c.post("/api/tags", json={"name": "Tree", "auto_hotkey": False}, headers=H).json()
    plain = c.post("/api/tags", json={"name": "Plain"}, headers=H).json()
    url = f"/api/projects/{game['id']}/hotkeys"
    r = c.put(url, json={"hotkeys": {"1": hero["id"], "2": tree["id"], "3": None}}, headers=H).json()
    assert r == {"1": hero["id"], "2": tree["id"]}
    # one key per tag: the later number wins
    assert c.put(url, json={"hotkeys": {"1": hero["id"], "4": hero["id"]}}, headers=H).json() == {"4": hero["id"]}
    p = next(x for x in c.get("/api/projects").json() if x["id"] == game["id"])
    assert p["hotkeys"] == {"4": hero["id"]}
    for bad in ({"0": hero["id"]}, {"x": hero["id"]}, {"1": 99999}):
        assert c.put(url, json={"hotkeys": bad}, headers=H).status_code == 400
    assert c.put(f"/api/projects/{plain['id']}/hotkeys", json={"hotkeys": {}}, headers=H).status_code == 400   # not a project
    # deleting the tag (or the project) takes its keys with it
    c.delete(f"/api/tags/{hero['id']}", headers=H)
    assert next(x for x in c.get("/api/projects").json() if x["id"] == game["id"])["hotkeys"] == {}
    c.put(url, json={"hotkeys": {"2": tree["id"]}}, headers=H)
    c.delete(f"/api/tags/{game['id']}", headers=H)
    assert db.cat.execute("SELECT COUNT(*) FROM project_hotkeys").fetchone()[0] == 0


def test_migration_from_schema_11(tmp_path):
    db = Database(str(tmp_path / "d"))
    db.cat.execute("DROP TABLE project_hotkeys")
    db.cat.execute("UPDATE settings SET value = '11' WHERE key = 'schema_version'")
    db2 = Database(str(tmp_path / "d"))
    assert db2.cat.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()[0] == "12"
    assert [r[1] for r in db2.cat.execute("PRAGMA table_info(project_hotkeys)")] == ["project_id", "hotkey", "tag_id"]
