"""3D models: every reader, previews, Blender thumbnails, the mesh API; tag exclusion; quick quit."""

import json
import os
import struct
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import model_samples as ms
from kurator import tags
from kurator.db import Database
from kurator.handlers import handler_for
from kurator.handlers.model3d import parse_blend
from kurator.mesh3d import Mesh, read_mesh, render
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    return str(d), ms.write_all(str(d))


def test_every_reader_finds_the_geometry(samples):
    folder, expected = samples
    for name, tris in expected.items():
        if tris == 0:
            continue
        path = os.path.join(folder, name)
        m = read_mesh(open(path, "rb").read(), path)
        if tris > 0:
            assert m.triangles == tris, name
        else:
            assert m.triangles > 0, name
        used = np.unique(m.faces)
        ext = m.vertices[used].max(0) - m.vertices[used].min(0)
        if name != "voxels.vox":
            # the house is 2 wide, 3.5 tall (Y up after conversion), 2 deep, whatever the file's up axis
            assert np.allclose(ext, [2, 3.5, 2], atol=1e-4), (name, ext)


def test_transforms_and_details(samples):
    folder, _ = samples
    rd = lambda n: read_mesh(open(os.path.join(folder, n), "rb").read(), os.path.join(folder, n))  # noqa: E731
    glb = rd("scene.glb")
    assert glb.vertices[:, 1].max() == pytest.approx(2.5)        # pyramid moved up by its node
    assert glb.info["generator"] == "Kurator tests" and glb.info["materials"] == 2
    # cube faces green (embedded texture), pyramid blue (colour factor)
    cols = {tuple(c) for c in glb.colors}
    assert any(c[1] > 150 and c[0] < 100 for c in cols) and any(c[2] > 150 and c[0] < 100 for c in cols)
    obj = rd("cube.obj")
    red = obj.colors[:12]
    assert (red[:, 0] > 150).all() and (obj.colors[12:, 2] > 150).all()   # usemtl order kept
    assert obj.info["textures"] == ["textures/red_albedo.png"]
    fbx = rd("shape74.fbx")
    assert fbx.vertices[:, 1].min() == pytest.approx(9.0)        # Lcl Translation (0, 10, 0)
    assert fbx.info["textures"] == ["shape_albedo.png"] and fbx.info["creator"].startswith("Kurator")
    assert fbx.dates["document"] == pytest.approx(time.mktime((2021, 5, 4, 10, 30, 0, 0, 0, -1)))
    dae = rd("shape.dae")
    assert dae.vertices[:, 1].min() == pytest.approx(2.0)        # translate 0 0 3 in Z-up = +3 up
    tm = rd("print.3mf")
    assert tm.vertices[:, 0].min() == pytest.approx(9.0) and tm.info["unit"] == "millimeter"
    vox = rd("voxels.vox")
    assert vox.info["voxels"] == 35 and vox.colors is not None


def test_previews_and_metadata(samples):
    folder, expected = samples
    for name in expected:
        path = os.path.join(folder, name)
        h = handler_for(os.path.splitext(name)[1])
        assert h is not None and h.group == "3d" and h.kind == "model", name
        loaded = h.load(open(path, "rb").read(), path, 512)
        assert loaded.image is not None and min(loaded.image.size) >= 16, name
    load = lambda n: handler_for(os.path.splitext(n)[1]).load(open(os.path.join(folder, n), "rb").read(), os.path.join(folder, n), 512)  # noqa: E731
    assert load("print_thumb.3mf").meta["preview"] == "embedded"
    assert load("scene.glb").meta["preview"] == "rendered" and load("scene.glb").meta["model"]["triangles"] == 18
    assert load("house.skp").meta["preview"] == "embedded"
    assert load("rig.ma").meta["placeholder"] is True


@pytest.mark.parametrize("name,version,comp", [("scene.blend", "4.5", None), ("scene_zstd.blend", "4.5", "zstd"),
                                               ("scene_gzip.blend", "4.5", "gzip"), ("scene_v5.blend", "5.0", None)])
def test_blend_thumbnails(samples, name, version, comp):
    folder, _ = samples
    info = parse_blend(open(os.path.join(folder, name), "rb").read())
    assert info["blender"] == version and info["compression"] == comp
    assert info["objects"] == 2 and info["meshes"] == 1 and info["materials"] == 1
    th = info["thumbnail"]
    assert th.size == (32, 32)
    assert th.getpixel((0, 0))[:3] == (0, 255, 0)          # rows flipped the right way up
    assert th.getpixel((16, 16))[:3] == (255, 160, 0)


def test_render_big_mesh_quickly():
    n = 300
    u, v = np.meshgrid(np.linspace(0, np.pi, n), np.linspace(0, 2 * np.pi, n), indexing="ij")
    verts = np.stack([np.sin(u) * np.cos(v), np.cos(u), np.sin(u) * np.sin(v)], -1).reshape(-1, 3).astype(np.float32)
    i, j = np.meshgrid(np.arange(n - 1), np.arange(n - 1), indexing="ij")
    a, b, c, d = i * n + j, (i + 1) * n + j, (i + 1) * n + j + 1, i * n + j + 1
    faces = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)]).astype(np.int32)
    t = time.time()
    img = render(Mesh(verts, faces), 256)
    assert time.time() - t < 20
    px = np.asarray(img)
    assert px[128, 128].mean() > 80          # sphere in the middle
    assert px[5, 5].mean() < 60              # background in the corner


def test_mesh_api_and_index(samples, tmp_path):
    folder, expected = samples
    db = Database(str(tmp_path / "data"))
    db.add_root(folder, "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 120:
        time.sleep(0.1)
    rows = {r["name"]: dict(r) for r in db.cat.execute("SELECT * FROM files")}
    assert set(expected) <= set(rows)
    bad = {n: r["error"] for n, r in rows.items() if r["status"] != 1}
    assert not bad, bad
    assert rows["scene.glb"]["kind"] == "model" and rows["scene.glb"]["width"] is None
    assert rows["shape74.fbx"]["date_source"] == "document"
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    st = c.get("/api/state").json()["types"]
    assert "3d" in {g["key"] for g in st["groups"]} and "glb" in st["meshes"] and "blend" not in st["meshes"]
    r = c.get(f"/api/mesh/{rows['scene.glb']['id']}")
    body = r.content
    assert body[:4] == b"KMSH"
    hl = struct.unpack_from("<I", body, 4)[0]
    head = json.loads(body[8:8 + hl])
    assert head["triangles"] == 18 and head["vertices"] == 13 and head["colors"] is True
    assert len(body) == 8 + hl + 13 * 12 + 18 * 12 + 40   # 13*3 colour bytes padded to 40
    assert c.get(f"/api/mesh/{rows['scene.blend']['id']}").status_code == 415
    item = c.get(f"/api/items/{rows['print.3mf']['id']}").json()
    assert item["meta"]["model"]["triangles"] == 18 and item["meta"]["model"]["dimensions"] == [2.0, 3.5, 2.0]


def test_exclude_filter(tmp_path):
    from PIL import Image
    lib = tmp_path / "lib"
    lib.mkdir()
    for n in ("a", "b", "c"):
        Image.new("RGB", (8, 8), "red").save(lib / f"{n}.png")
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.walk_root(rid, str(lib))
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    game = tags.create_tag(db, "Game A", facet="project")["id"]
    tags.apply(db, [ids["a.png"], ids["b.png"]], game, "add")
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    names = lambda **p: sorted(c.get("/api/items", params=p).json()["n"])  # noqa: E731
    assert names(exclude=str(game)) == ["c.png"]
    assert names(tag=str(game)) == ["a.png", "b.png"]


def test_scanner_stops_quickly(tmp_path):
    """Quitting during indexing must not wait for the whole queue (kurator.bat took minutes to close)."""
    from PIL import Image
    lib = tmp_path / "lib"
    lib.mkdir()
    for i in range(400):
        Image.new("RGB", (900, 700), (i % 255, 80, 120)).save(lib / f"{i}.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=2)
    sc.request_scan()
    t = time.time()
    while sc.status()["processed"] < 5 and time.time() - t < 60:
        time.sleep(0.05)
    t = time.time()
    sc.stop(timeout=10)
    assert time.time() - t < 8
    pending = db.cat.execute("SELECT COUNT(*) FROM files WHERE status = 0").fetchone()[0]
    assert pending > 0   # left for the next start


@pytest.mark.skipif(os.name == "nt", reason="the stand-in 'blender' is a script with a POSIX #! line")
def test_blend_3d_view_through_blender(samples, tmp_path, monkeypatch):
    """With Blender installed, .blend files open in the 3D viewer (converted once to a cached GLB).
    A stand-in 'blender' checks that scripts in the file can't run (--disable-autoexec)."""
    import stat
    import sys
    folder, _ = samples
    glb = os.path.join(folder, "scene.glb")
    fake = tmp_path / "blender"
    log = tmp_path / "calls.txt"
    fake.write_text(f"""#!{sys.executable}
import shutil, sys
a = sys.argv[1:]
open({str(log)!r}, "a").write(" ".join(a[:4]) + "\\n")
assert "--disable-autoexec" in a and "--factory-startup" in a and "--background" in a
assert a.index("--disable-autoexec") < a.index("--python")
shutil.copy({glb!r}, a[a.index("--") + 1])
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KURATOR_BLENDER", str(fake))
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(folder, "library")
    sc = Scanner(db, workers=1)
    sc.walk_root(rid, folder)
    from kurator.processing import process_file
    db.store_results([process_file(r[0], (256,)) for r in db.cat.execute("SELECT path FROM files WHERE name = 'scene.blend'")])
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    assert "blend" in c.get("/api/state").json()["types"]["meshes"]
    bid = db.cat.execute("SELECT id FROM files WHERE name = 'scene.blend'").fetchone()[0]
    for _ in range(2):
        body = c.get(f"/api/mesh/{bid}").content
        assert body[:4] == b"KMSH"
    assert len(log.read_text().splitlines()) == 1          # converted once, then cached
    monkeypatch.setenv("KURATOR_BLENDER", "off")
    c2 = TestClient(create_app(db, sc, 8765), base_url=BASE)
    assert "blend" not in c2.get("/api/state").json()["types"]["meshes"]
