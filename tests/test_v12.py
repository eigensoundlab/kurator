"""v0.12: textured 3D models (glTF, OBJ, FBX), texture detection, memory safety, program-code
.obj files, SVG sizes and "show all objects", game projects and repositories, 2D companion maps."""

import json
import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import textured_samples as T
from kurator import maps2d, processing, projectdirs
from kurator.db import Database
from kurator.handlers.vector import load_svg, svg_for_view
from kurator.mesh3d import read_mesh, render, texture_summary
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


def _index(tmp_path, lib):
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 90:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return db, sc, ids


@pytest.fixture()
def models(tmp_path):
    lib = tmp_path / "lib"
    (lib / "models").mkdir(parents=True)
    (lib / "models" / "quad.glb").write_bytes(T.glb())
    (lib / "models" / "quad.fbx").write_bytes(T.fbx())
    T.obj_files(str(lib / "models"))
    (lib / "models" / "plain.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\nf 1 x 3\n")
    (lib / "models" / "compiled.obj").write_bytes(b"\x64\x86\x05\x00" + bytes(range(256)) * 8)
    db, sc, ids = _index(tmp_path, lib)
    return TestClient(create_app(db, sc, 8765), base_url=BASE), db, ids, lib


def _left_right(img: Image.Image):
    """Colours left and right of the middle of a thumbnail of the textured quad."""
    w, h = img.size
    return img.getpixel((int(w * 0.33), h // 2)), img.getpixel((int(w * 0.62), h // 2))


@pytest.mark.parametrize("name", ["quad.glb", "board.obj", "quad.fbx"])
def test_textures_are_found_and_sampled(models, name):
    _, _, _, lib = models
    p = str(lib / "models" / name)
    m = read_mesh(open(p, "rb").read(), p, full=True)
    assert m.has_uv and m.face_mat is not None and m.materials[0].maps["base"].found
    left, right = _left_right(render(m, 128))
    assert left[0] > 100 and left[2] < 60 and right[2] > 100 and right[0] < 60   # red | blue, not mirrored
    assert m.normals is not None or m.corner_normals is not None or name == "quad.glb"
    s = texture_summary(m)
    assert s["textured"] and {x["role"] for x in s["maps"]} >= {"base", "normal"}
    if name == "board.obj":   # absolute path from another PC, found by name in Textures/; the bump map is missing
        # (on case-insensitive disks the path may keep the case written in the .mtl)
        assert m.materials[0].maps["base"].path.lower().endswith(os.path.join("textures", "board_albedo.png"))
        assert s["missing_textures"] == ["board_normal.png"]
    if name == "quad.fbx":    # texture embedded in the FBX (Video content); the normal map is missing
        assert m.materials[0].maps["base"].data is not None and s["missing_textures"] == ["wood_normal.png"]


def test_viewer_mesh_and_texture_api(models):
    c, _, ids, _ = models
    import struct
    body = c.get(f"/api/mesh/{ids['quad.glb']}").content
    hl = struct.unpack_from("<I", body, 4)[0]
    head = json.loads(body[8:8 + hl])
    assert head["version"] == 2 and head["uv"] and head["textures"] == 2
    assert head["groups"] == [{"start": 0, "count": 2, "material": 0}]
    mat = head["materials"][0]
    assert mat["name"] == "Paint" and mat["maps"]["base"]["tex"] == 0 and mat["maps"]["normal"]["embedded"]
    # positions, normals, uv, colours, indices
    assert len(body) == 8 + hl + 4 * 12 + 4 * 4 + 4 * 8 + 4 * 4 + 2 * 12
    t = c.get(f"/api/mesh/{ids['quad.glb']}/tex/0")
    assert t.headers["content-type"] == "image/webp" and Image.open(__import__("io").BytesIO(t.content)).size == (8, 8)
    assert c.get(f"/api/mesh/{ids['quad.glb']}/tex/7").status_code == 404
    obj = c.get(f"/api/mesh/{ids['board.obj']}").content
    hl = struct.unpack_from("<I", obj, 4)[0]
    head = json.loads(obj[8:8 + hl])
    assert head["normals"] == "file" and head["vertices"] == 6       # per-corner: 2 triangles x 3
    assert head["materials"][0]["maps"]["normal"] == {"name": "board_normal.png", "found": False, "embedded": False}


def test_model_meta_filters_and_program_code(models):
    c, db, ids, _ = models
    meta = {n: c.get(f"/api/items/{i}").json()["meta"] for n, i in ids.items() if n.endswith((".obj", ".glb", ".fbx"))}
    assert meta["quad.glb"]["model"]["textured"] is True and meta["quad.glb"]["model"]["uv"] is True
    assert meta["plain.obj"]["model"]["textured"] is False and "1 damaged" in meta["plain.obj"]["model"]["note"]
    assert meta["compiled.obj"]["not_model"] and meta["compiled.obj"]["placeholder"]
    names = lambda **q: {n for n, i in ids.items() if i in c.get("/api/items", params=q).json()["ids"]}  # noqa: E731
    assert names(tex="yes") == {"quad.glb", "board.obj", "quad.fbx"}
    assert names(tex="no") == {"plain.obj"}
    assert names(tex="missing") == {"board.obj", "quad.fbx"}


def test_render_survives_memory_pressure(models, monkeypatch):
    import importlib
    R = importlib.import_module("kurator.mesh3d.render")
    _, _, _, lib = models
    p = str(lib / "models" / "quad.glb")
    m = read_mesh(open(p, "rb").read(), p)
    calls = []
    real = R._render

    def flaky(mesh, size, ss, yaw, pitch, textures=True):
        calls.append(ss)
        if len(calls) == 1:
            raise MemoryError("Unable to allocate 3.81 MiB")
        return real(mesh, size, ss, yaw, pitch, textures)
    monkeypatch.setattr(R, "_render", flaky)
    assert R.render(m, 64).size == (64, 64) and calls == [2, 1]   # second try: smaller


def test_memory_errors_are_retried(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (8, 8), "red").save(lib / "a.png")
    real = processing.process_file

    def oom(path, sizes):
        return {"path": path, "error": "MemoryError: not enough free memory"}
    import kurator.scanner as S
    monkeypatch.setattr(S, "process_file", oom)
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 30:
        time.sleep(0.05)
    assert db.cat.execute("SELECT status FROM files").fetchone()[0] == 2
    monkeypatch.setattr(S, "process_file", real)
    sc.request_scan()
    t = time.time()
    time.sleep(0.3)
    while not (sc.status()["phase"] == "idle") and time.time() - t < 30:
        time.sleep(0.05)
    time.sleep(0.3)
    assert db.cat.execute("SELECT status FROM files").fetchone()[0] == 1


def test_heavy_jobs_setting(monkeypatch):
    assert processing.heavy_jobs(16) == 3 and processing.heavy_jobs(2) == 1
    monkeypatch.setenv("KURATOR_HEAVY_JOBS", "5")
    assert processing.heavy_jobs(2) == 5


SVG_MM = '<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297"><rect x="10" y="10" width="100" height="50" fill="red"/>{extra}</svg>'


def test_svg_sizes_in_mm_and_without_size(monkeypatch):
    monkeypatch.setenv("KURATOR_SVG_ALL", "0")
    r = load_svg(SVG_MM.format(extra="").encode(), "inkscape.svg", 256)   # Inkscape writes mm
    assert r.size == (794, 1123) and r.image.getpixel((30, 30))[:3] == (255, 0, 0)
    zero = b'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"><circle cx="500" cy="500" r="40" fill="blue"/></svg>'
    r = load_svg(zero, "zero.svg", 256)
    assert abs(r.size[0] - 82) <= 2 and "no size" in r.meta["note"]
    assert r.image.getpixel((r.image.width // 2, r.image.height // 2))[:3] == (0, 0, 255)
    fixed = svg_for_view(zero)
    assert b'viewBox="45' in fixed and b'width="0"' not in fixed
    empty = load_svg(b'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"/>', "e.svg", 256)
    assert empty.meta.get("placeholder")


def test_svg_show_all_objects(monkeypatch):
    data = SVG_MM.format(extra='<rect x="400" y="10" width="40" height="40" fill="blue"/>').encode()
    monkeypatch.setenv("KURATOR_SVG_ALL", "0")
    assert "outside_page" not in load_svg(data, "a.svg", 256).meta and svg_for_view(data) is None
    monkeypatch.setenv("KURATOR_SVG_ALL", "1")
    r = load_svg(data, "a.svg", 256)
    assert r.meta["outside_page"] and abs(r.size[0] - 440 * 793.7 / 210) < 3   # the page grew to the right (px at 96 dpi)
    assert b"viewBox" in svg_for_view(data)
    assert svg_for_view(SVG_MM.format(extra="").encode()) is None   # everything on the page: the original


def test_project_folders_are_left_alone(tmp_path):
    lib = tmp_path / "lib"
    unity = lib / "Game"
    for d in ("Assets/Art", "ProjectSettings", "Library/Artifacts", "Temp", "Packages/com.me.art"):
        (unity / d).mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(unity / "Assets/Art/hero.png")
    (unity / "Assets/Art/hero.png.meta").write_text("guid: 1234\n")
    Image.new("RGB", (8, 8), "red").save(unity / "Library/Artifacts/cache.png")
    Image.new("RGB", (8, 8), "red").save(unity / "Temp/tmp.png")
    Image.new("RGB", (8, 8), "red").save(unity / "Packages/com.me.art/icon.png")
    code = lib / "tool"
    (code / "obj/Debug").mkdir(parents=True)
    (code / "res").mkdir()
    (code / "tool.csproj").write_text("<Project/>")
    Image.new("RGB", (8, 8), "red").save(code / "obj/Debug/gen.png")
    Image.new("RGB", (8, 8), "red").save(code / "res/logo.png")
    venv = lib / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n")
    Image.new("RGB", (8, 8), "red").save(venv / "lib/x.png")
    db, sc, ids = _index(tmp_path, lib)
    assert set(ids) == {"hero.png", "icon.png", "logo.png"}
    assert projectdirs.engine_root(str(unity / "Assets/Art/hero.png"))[0] == "Unity"
    assert sc.walk_folder(1, str(unity / "Library/Artifacts"), False) is None
    assert set(r[0] for r in db.cat.execute("SELECT name FROM files")) == {"hero.png", "icon.png", "logo.png"}
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    c.post("/api/tags", json={"name": "Hero"}, headers=H)
    r = c.post("/api/xmp/write", json={"ids": [ids["hero.png"]], "include_untagged": True}, headers=H).json()
    assert r["written"] == 0 and r["skipped_project"] == 1 and not (unity / "Assets/Art/hero.png.xmp").exists()
    dest = unity / "Assets/Characters"
    dest.mkdir()
    res = c.post("/api/move", json={"ids": [ids["hero.png"]], "dest": str(dest)}, headers=H).json()
    assert res["moved"] == 1 and (dest / "hero.png.meta").read_text() == "guid: 1234\n"   # the GUID travels along
    assert not (unity / "Assets/Art/hero.png.meta").exists()


@pytest.fixture()
def sprites(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    base = Image.new("RGBA", (40, 30), (200, 100, 50, 255))
    base.save(lib / "hero.png")
    for suf in ("normal", "glow", "ao", "depth", "sky", "v2", "final"):
        base.save(lib / f"hero_{suf}.png")
    base.resize((20, 15)).save(lib / "hero_n.png")             # half size, same shape: a map too
    base.resize((30, 30)).save(lib / "hero_e.png")             # another shape: not a map
    base.save(lib / "tree_a.png")
    base.save(lib / "tree_b.png")
    base.save(lib / "rock_albedo.png")
    base.save(lib / "rock_nrm.png")
    db, sc, ids = _index(tmp_path, lib)
    return db, sc, ids


def test_2d_companion_maps(sprites):
    db, sc, ids = sprites
    g = maps2d.group(db, ids["hero.png"])
    roles = {m["name"]: m["role"] for m in g["maps"]}
    assert roles == {"hero_normal.png": "normal", "hero_n.png": "normal", "hero_glow.png": "emissive",
                     "hero_ao.png": "ao", "hero_depth.png": "depth", "hero_sky.png": "background"}
    assert g["main"]["name"] == "hero.png" and g["role"] == "base"
    other = maps2d.group(db, ids["hero_glow.png"])
    assert other["main"]["id"] == ids["hero.png"] and other["role"] == "emissive"
    assert maps2d.group(db, ids["hero_v2.png"]) is None and maps2d.group(db, ids["tree_a.png"]) is None
    rock = maps2d.group(db, ids["rock_nrm.png"])
    assert rock["main"]["name"] == "rock_albedo.png" and rock["maps"][0]["role"] == "normal"
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    r = c.get(f"/api/items/{ids['hero.png']}/maps2d").json()
    assert r["main"]["url"] == f"/f/{ids['hero.png']}" and len(r["maps"]) == 6
    assert c.get(f"/api/items/{ids['tree_a.png']}/maps2d").json() == {"maps": []}
