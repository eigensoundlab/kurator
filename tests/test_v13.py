"""v0.13: meshopt and Draco glTF, binary USD (in a separate process), hole-free simplification,
turntables, cutting regions out of sounds, Godot resources from sprite sheets and atlases."""

import importlib.util
import os
import struct
import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from PIL import Image

import compressed_samples as C
from kurator.db import Database
from kurator.mesh3d import Mesh, MeshError, native, read_mesh, render, texture_summary, thin
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
HAS_DRACO = importlib.util.find_spec("DracoPy") is not None
HAS_USD = importlib.util.find_spec("pxr") is not None


def _index(tmp_path, lib):
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 120:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return db, sc, ids


def _red_blue(m):
    img = render(m, 128)
    w, h = img.size
    left, right = img.getpixel((int(w * 0.33), h // 2)), img.getpixel((int(w * 0.62), h // 2))
    return left[0] > 100 and left[2] < 60 and right[2] > 100 and right[0] < 60


def test_meshopt_gltf(tmp_path):
    p = tmp_path / "mo.glb"
    p.write_bytes(C.meshopt_glb())
    m = read_mesh(p.read_bytes(), str(p), full=True)
    assert m.triangles == 2 and m.has_uv and _red_blue(m)
    assert np.allclose(np.sort(m.vertices[:, 0]), [-1, -1, 1, 1])


@pytest.mark.skipif(not HAS_DRACO, reason="DracoPy (optional 3D add-on) not installed")
def test_draco_gltf(tmp_path, monkeypatch):
    p = tmp_path / "dr.glb"
    p.write_bytes(C.draco_glb())
    m = read_mesh(p.read_bytes(), str(p), full=True)
    assert m.triangles == 2 and m.has_uv and _red_blue(m)
    monkeypatch.setattr(native, "available", lambda kind: False)
    with pytest.raises(MeshError, match="3D add-on"):
        read_mesh(p.read_bytes(), str(p))


def test_native_crash_stays_in_its_process(monkeypatch):
    monkeypatch.setattr(native, "available", lambda kind: True)
    monkeypatch.setattr(native, "_BOOT", "import os; os.abort()")
    with pytest.raises(MeshError):
        native.run("draco", payload=b"junk")
    monkeypatch.setattr(native, "_BOOT", "import sys; sys.stderr.write('ValueError: broken file\\n'); sys.exit(2)")
    with pytest.raises(MeshError, match="broken file"):
        native.run("draco", payload=b"junk")


@pytest.mark.skipif(not HAS_USD, reason="usd-core (optional 3D add-on) not installed")
def test_binary_usd_and_usdz(tmp_path):
    C.usd_files(str(tmp_path))
    for name in ("quad.usdc", "quad.usdz"):
        p = tmp_path / name
        m = read_mesh(p.read_bytes(), str(p))
        assert m.triangles == 2 and m.info["up_axis"] == "Z"
        assert np.allclose(m.vertices[:, 1].mean(), 5.0)          # parent transform, then Z up -> Y up
        assert texture_summary(m)["textured"] and _red_blue(m)
    usdz = read_mesh((tmp_path / "quad.usdz").read_bytes(), str(tmp_path / "quad.usdz"))
    assert usdz.materials[0].maps["base"].data is not None       # the texture inside the package


def _sphere(nu, nv):
    us, vs = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(0, 1, nv + 1))
    th, ph = us * 2 * np.pi, vs * np.pi
    P = np.stack([np.sin(ph) * np.cos(th), np.cos(ph), np.sin(ph) * np.sin(th)], -1).reshape(-1, 3).astype(np.float32)
    j, i = np.mgrid[0:nv, 0:nu]
    a = (j * (nu + 1) + i).ravel()
    F = np.concatenate([np.stack([a, a + nu + 1, a + 1], 1), np.stack([a + 1, a + nu + 1, a + nu + 2], 1)]).astype(np.int32)
    return Mesh(P, F)


def test_simplification_keeps_the_surface_closed():
    m = _sphere(400, 200)
    small = thin(m, 8000)
    assert 2000 < small.triangles <= 8000
    full = np.asarray(render(m, 128, supersample=1).convert("L"), np.int16)
    few = np.asarray(render(small, 128, supersample=1).convert("L"), np.int16)
    bg = np.asarray(render(Mesh(np.zeros((3, 3), np.float32) + [[9, 9, 9], [9, 9, 9.001], [9, 9.001, 9]], np.array([[0, 1, 2]], np.int32)), 128, supersample=1).convert("L"), np.int16)
    # pixels showing background where the full model has surface: none (dropping triangles left ~90 %)
    holes = ((np.abs(few - bg) < 3) & (np.abs(full - bg) >= 3)).mean()
    assert holes < 0.01


def test_turntable_strip(tmp_path, monkeypatch):
    monkeypatch.setenv("KURATOR_TURNTABLE", "1")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "mo.glb").write_bytes(C.meshopt_glb())
    db, sc, ids = _index(tmp_path, lib)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    item = c.get(f"/api/items/{ids['mo.glb']}").json()
    assert item["meta"]["turntable"] == 12
    r = c.get(f"/t/{ids['mo.glb']}", params={"s": 1})
    im = Image.open(__import__("io").BytesIO(r.content))
    assert r.status_code == 200 and im.size == (12 * 160, 160)


@pytest.fixture()
def sound(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    t = np.arange(44100 * 2) / 44100
    sf.write(lib / "tone.wav", np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2, 1), 44100, subtype="PCM_16")
    db, sc, ids = _index(tmp_path, lib)
    return TestClient(create_app(db, sc, 8765), base_url=BASE), db, ids, lib


def test_region_export(sound):
    c, db, ids, lib = sound
    r = c.post(f"/api/audio/{ids['tone.wav']}/export", headers=H,
               json={"start": 0.5, "end": 1.0, "fade_in": 0.01, "fade_out": 0.01, "normalize": -1}).json()
    assert os.path.basename(r["file"]).startswith("tone_0m00_50s") and r["id"]
    info = sf.info(r["file"])
    assert info.samplerate == 44100 and info.channels == 2 and info.subtype == "PCM_16"
    assert abs(info.frames - 22050) <= 1
    a, _ = sf.read(r["file"])
    assert abs(np.abs(a).max() - 10 ** (-1 / 20)) < 0.01 and np.abs(a[:5]).max() < 0.01   # normalised, faded in
    r2 = c.post(f"/api/audio/{ids['tone.wav']}/export", headers=H, json={"start": 0, "end": 0.25, "format": "flac", "name": "hit"}).json()
    assert r2["file"].endswith("hit.flac") and sf.info(r2["file"]).format == "FLAC"
    again = c.post(f"/api/audio/{ids['tone.wav']}/export", headers=H, json={"start": 0, "end": 0.25, "format": "flac", "name": "hit"}).json()
    assert again["file"].endswith("hit_2.flac")                                            # never overwrites
    assert c.post(f"/api/audio/{ids['tone.wav']}/export", headers=H, json={"start": 1, "end": 1}).status_code == 400
    assert c.post(f"/api/audio/{ids['tone.wav']}/export", headers=H, json={"start": 0, "end": 1, "dest": "/no/such/dir"}).status_code == 400


def test_godot_resources(tmp_path):
    lib = tmp_path / "lib"
    (lib / "game").mkdir(parents=True)
    (lib / "game" / "project.godot").write_text("config_version=5\n")
    out = lib / "game" / "art"
    out.mkdir()
    frames = [Image.new("RGBA", (32, 32), (k * 60, 100, 200, 255)) for k in range(4)]
    frames[0].save(lib / "walk.gif", save_all=True, append_images=frames[1:], duration=80, loop=0)
    coin = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    coin.paste((250, 220, 0, 255), (16, 8, 48, 56))
    coin.save(lib / "coin.png")
    db, sc, ids = _index(tmp_path, lib)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    r = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["walk.gif"]], "mode": "frames", "padding": 0, "godot": True,
                                                     "fps": 8, "dest": str(out), "name": "walk"}).json()
    tres = [f for f in r["files"] if f.endswith(".tres")]
    assert [os.path.basename(f) for f in tres] == ["walk_frames.tres"]
    text = open(tres[0]).read()
    assert text.startswith('[gd_resource type="SpriteFrames" load_steps=6 format=3]')
    assert 'path="res://art/walk.png"' in text and '"name": &"walk"' in text and '"speed": 8' in text
    assert text.count('[sub_resource type="AtlasTexture"') == 4 and "region = Rect2(32, 0, 32, 32)" in text
    r = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["coin.png"]], "padding": 0, "godot": True,
                                                     "dest": str(tmp_path), "name": "items"}).json()
    sprite = [f for f in r["files"] if f.endswith("coin.tres")][0]
    text = open(sprite).read()
    assert 'path="../items.png"' in text and "region = Rect2(0, 0, 32, 48)" in text and "margin = Rect2(16, 8, 32, 16)" in text
