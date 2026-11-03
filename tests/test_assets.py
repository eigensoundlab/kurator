"""Texture sets, model companions, and exporting a project into a game folder."""

import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import model_samples as ms
from kurator import assets, projects, tags
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.mark.parametrize("name,expected", [
    ("rock_albedo.png", ("rock", "color")), ("Rock_01_4K_Normal.png", ("rock_01", "normal")),
    ("rock_roughness_2k.jpg", ("rock", "roughness")), ("crate-AO.tga", ("crate", "ao")),
    ("sword_BaseColor.png", ("sword", "color")), ("wall_ORM.png", ("wall", "packed")),
    ("portrait.png", None), ("notes_normal.txt", None),
])
def test_map_roles(name, expected):
    assert assets.map_role(name) == expected


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    m = lib / "props"
    (m / "textures").mkdir(parents=True)
    o, mtl = ms.obj()
    (m / "cube.obj").write_bytes(o)
    (m / "test.mtl").write_bytes(mtl)
    Image.new("RGB", (8, 8), "red").save(m / "textures" / "red_albedo.png")
    (m / "shape.fbx").write_bytes(ms.fbx_binary())
    Image.new("RGB", (8, 8), "blue").save(m / "textures" / "shape_albedo.png")
    tex = lib / "textures" / "rock"
    tex.mkdir(parents=True)
    for role in ("albedo", "normal_4k", "roughness"):
        Image.new("RGB", (8, 8), "gray").save(tex / f"rock_{role}.png")
    Image.new("RGB", (8, 8), "green").save(lib / "Hero Portrait.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 60:
        time.sleep(0.05)
    ids = {os.path.relpath(r[1], lib).replace("\\", "/"): r[0] for r in db.cat.execute("SELECT id, path FROM files")}
    game = tags.create_tag(db, "Game A", facet="project")["id"]
    projects.set_sources(db, game, [{"kind": "folder", "ref": str(lib / "props")}])
    tags.apply(db, [ids["textures/rock/rock_albedo.png"], ids["Hero Portrait.png"]], game, "add")
    return db, sc, lib, tmp_path, ids, game


def test_related(env):
    db, sc, lib, tmp, ids, game = env
    r = assets.related(db, ids["textures/rock/rock_normal_4k.png"])
    assert [m["role"] for m in r["set"]["maps"]] == ["color", "normal", "roughness"]
    r = assets.related(db, ids["props/cube.obj"])
    assert {u["name"] for u in r["uses"]} == {"test.mtl", os.path.join("textures", "red_albedo.png")}
    r = assets.related(db, ids["props/shape.fbx"])
    assert [u["name"] for u in r["uses"]] == [os.path.join("textures", "shape_albedo.png")]   # named inside the FBX


def test_export_unity_and_godot(env):
    db, sc, lib, tmp, ids, game = env
    out = tmp / "MyGame"
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    p = c.post("/api/export/plan", headers=H, json={"project": game, "dest": str(out), "preset": "unity"}).json()
    rels = sorted(e["rel"] for e in p["examples"])
    assert "Assets/Game A/Models/cube/cube.obj" in rels
    assert "Assets/Game A/Models/cube/test.mtl" in rels
    assert "Assets/Game A/Models/cube/textures/red_albedo.png" in rels      # same relative path as next to the model
    assert "Assets/Game A/Models/shape/textures/shape_albedo.png" in rels
    assert "Assets/Game A/Textures/rock_albedo.png" in rels and "Assets/Game A/Sprites/Hero Portrait.png" in rels
    assert "Assets/Game A/Textures/red_albedo.png" not in rels      # travels with its model only
    c.post("/api/export/run", headers=H, json={"project": game, "dest": str(out), "preset": "unity"})
    t = time.time()
    while c.get("/api/export/job").json()["running"] and time.time() - t < 30:
        time.sleep(0.05)
    job = c.get("/api/export/job").json()
    assert job["result"]["copied"] == p["copy"] and not job["errors"]
    assert (out / "Assets/Game A/Models/cube/textures/red_albedo.png").read_bytes() == (lib / "props/textures/red_albedo.png").read_bytes()
    assert (out / "Assets/Game A/kurator_export.json").exists()
    again = c.post("/api/export/plan", headers=H, json={"project": game, "dest": str(out), "preset": "unity"}).json()
    assert again["copy"] == 0 and again["unchanged"] == p["files"]
    g = c.post("/api/export/plan", headers=H, json={"project": game, "dest": str(out), "preset": "godot"}).json()
    assert "assets/game_a/sprites/hero_portrait.png" in {e["rel"] for e in g["examples"]}
    bad = c.post("/api/export/plan", headers=H, json={"project": game, "dest": str(lib / "x"), "preset": "unity"})
    assert bad.status_code == 400 and "library" in bad.json()["detail"]
