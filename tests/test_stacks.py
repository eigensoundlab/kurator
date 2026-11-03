"""Version stacks: shown as one item (the newest on top), by-name grouping, filters, unstack."""

import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import stacks, tags
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.mark.parametrize("a,b", [("hero_sketch.png", "hero_lineart.png"), ("hero_v01.png", "hero_v12.psd"),
                                 ("Hero (1).jpg", "hero copy.jpg"), ("dragon-final.png", "dragon_wip_2.png")])
def test_version_key(a, b):
    assert stacks.version_key(a) == stacks.version_key(b)


def test_version_key_keeps_different_pictures_apart():
    assert stacks.version_key("hero.png") != stacks.version_key("heroine.png")
    assert stacks.version_key("sword_01.png") != stacks.version_key("shield_01.png")


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for k, n in enumerate(["hero_sketch.png", "hero_lineart.png", "hero_color.png", "dragon_v1.png", "dragon_v2.png", "tree.png"]):
        Image.new("RGB", (16, 16), (40 * k, 10, 10)).save(lib / n)
        os.utime(lib / n, (1600000000 + k * 1000, 1600000000 + k * 1000))
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 60:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return db, ids, TestClient(create_app(db, sc, 8765), base_url=BASE)


def test_stacks_in_the_grid(env):
    db, ids, c = env
    names = lambda **p: c.get("/api/items", params=p).json()  # noqa: E731
    assert c.post("/api/stacks", headers=H, json={"ids": list(ids.values()), "by_name": True}).json()["stacks"] == 2
    out = names()
    assert sorted(out["n"]) == ["dragon_v2.png", "hero_color.png", "tree.png"]            # the newest of each on top
    assert dict(zip(out["n"], out["k"])) == {"dragon_v2.png": 2, "hero_color.png": 3, "tree.png": 0}
    assert len(names(stacks="expand")["n"]) == 6
    d = c.get(f"/api/items/{ids['hero_color.png']}").json()["stack"]
    assert [m["name"] for m in d["members"]] == ["hero_sketch.png", "hero_lineart.png", "hero_color.png"]
    assert names(stack=d["id"])["n"] == ["hero_sketch.png", "hero_lineart.png", "hero_color.png"]
    # a filter that matches only an older version shows that version for the stack
    t = tags.create_tag(db, "Ink")["id"]
    tags.apply(db, [ids["hero_lineart.png"]], t, "add")
    assert names(tag=str(t))["n"] == ["hero_lineart.png"]
    # choose another cover; unstack
    c.post("/api/stacks/cover", headers=H, json={"id": ids["hero_sketch.png"]})
    assert "hero_sketch.png" in names()["n"]
    c.post("/api/stacks/unstack", headers=H, json={"ids": [ids["hero_sketch.png"]]})
    assert len(names()["n"]) == 5
    assert c.post("/api/stacks", headers=H, json={"ids": [ids["tree.png"]]}).status_code == 400
