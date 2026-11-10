""".blend files: what the materials are made of (and what the 3D preview keeps of them), and pictures
rendered by Blender. With Blender installed the real thing; the server's part also without it."""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import blender
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
MAKE = r"""
import bpy, sys
d = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
cube.modifiers.new("Bevel", "BEVEL").width = 0.1
m = bpy.data.materials.new("Painted"); m.use_nodes = True
nt = m.node_tree; b = nt.nodes["Principled BSDF"]
t = nt.nodes.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(d + "/checker.png")
nt.links.new(t.outputs["Color"], b.inputs["Base Color"])
ramp = nt.nodes.new("ShaderNodeValToRGB"); nt.links.new(t.outputs["Color"], ramp.inputs["Fac"]); nt.links.new(ramp.outputs["Color"], b.inputs["Roughness"])
b.inputs["Metallic"].default_value = 0.8
cube.data.materials.append(m)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.8, location=(2.5, 0, 0))
s = bpy.context.object
m2 = bpy.data.materials.new("Rock"); m2.use_nodes = True
nt = m2.node_tree; b = nt.nodes["Principled BSDF"]
n = nt.nodes.new("ShaderNodeTexNoise"); nt.links.new(n.outputs["Color"], b.inputs["Base Color"])
s.data.materials.append(m2)
bpy.ops.mesh.primitive_plane_add(size=6, location=(1, 0, -1))
p = bpy.context.object
m3 = bpy.data.materials.new("Floor"); m3.use_nodes = True
nt = m3.node_tree; b = nt.nodes["Principled BSDF"]
t3 = nt.nodes.new("ShaderNodeTexImage")
img = bpy.data.images.new("floor_albedo", 4, 4); img.source = "FILE"; img.filepath = "//textures/floor_albedo.png"
t3.image = img
nt.links.new(t3.outputs["Color"], b.inputs["Base Color"])
p.data.materials.append(m3)
bpy.ops.wm.save_as_mainfile(filepath=d + "/scene.blend")
"""


@pytest.fixture(scope="module")
def blend_file(tmp_path_factory):
    exe = blender.find()
    if not exe:
        pytest.skip("Blender is not installed")
    d = tmp_path_factory.mktemp("blend")
    Image.new("RGB", (64, 32), "orange").save(d / "checker.png")
    (d / "make.py").write_text(MAKE)
    subprocess.run([exe, "--background", "--factory-startup", "--python", str(d / "make.py"), "--", str(d)],
                   capture_output=True, timeout=300, check=False)
    assert (d / "scene.blend").exists()
    return d


def test_material_report(blend_file):
    r = blender.report(str(blend_file / "scene.blend"), "h1", str(blend_file / "cache"))
    mats = {m["name"]: m for m in r["materials"]}
    assert set(mats) == {"Painted", "Rock", "Floor"}
    p = mats["Painted"]["inputs"]
    assert p["base_color"]["kind"] == "texture" and p["base_color"]["images"][0]["size"] == [64, 32]
    assert p["roughness"]["kind"] == "texture" and p["roughness"]["adjust"] == ["Color Ramp"]   # shown plain
    assert p["metallic"] == {"kind": "value", "value": 0.8}
    assert "normal" not in p and "emission" not in p                                          # unused: left out
    assert mats["Rock"]["inputs"]["base_color"] == {"kind": "procedural", "images": [], "procedural": ["Noise Texture"], "adjust": []}
    assert mats["Floor"]["inputs"]["base_color"]["images"][0]["missing"] and r["missing_images"] == ["floor_albedo"]
    cube = next(m for m in r["meshes"] if m["name"] == "Cube")
    assert cube["modifiers"] == ["Bevel"] and cube["triangles"] > 12 and cube["uv_maps"] == ["UVMap"]   # bevelled, as exported
    assert r["camera"] is None and r["lights"] == 0
    assert os.path.isfile(blend_file / "cache" / "h1.report.json")                            # once per file version


def test_render(blend_file):
    png, used = blender.render(str(blend_file / "scene.blend"), "h1", str(blend_file / "cache"), "eevee")
    assert used in ("eevee", "cycles")                                                       # no graphics card: Cycles
    im = Image.open(png)
    assert im.size == (blender.RENDER_WIDTH, blender.RENDER_WIDTH * 3 // 4) and im.mode == "RGBA"
    alpha = im.getchannel("A")
    assert alpha.getextrema() == (0, 255)                                                    # the scene on a clear background
    box = alpha.getbbox()
    assert box[2] - box[0] > im.size[0] * 0.6                                                # framed: fills the picture
    t = time.time()
    assert blender.render(str(blend_file / "scene.blend"), "h1", str(blend_file / "cache"), "eevee")[0] == png
    assert time.time() - t < 1                                                               # made once


# ------------------------------------------------------------- the server's part (no Blender needed)
@pytest.fixture()
def env(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ship.blend").write_bytes(b"BLENDER-v300" + b"\0" * 100)
    Image.new("RGB", (8, 8)).save(lib / "pic.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    monkeypatch.setattr(blender, "find", lambda: "/usr/bin/blender-fake")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while time.time() - t < 60 and not (sc.status()["phase"] == "idle" and sc.status()["finished"]):
        time.sleep(0.05)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    it = c.get("/api/items").json()
    return c, dict(zip(it["n"], it["ids"])), tmp_path


def test_endpoints(env, monkeypatch):
    c, ids, tmp = env
    assert c.get(f"/api/items/{ids['pic.png']}/blend/report").status_code == 415
    seen = []

    def fake_report(src, h, cache):
        seen.append((os.path.basename(src), h))
        return {"materials": [], "meshes": [], "triangles": 0}
    monkeypatch.setattr(blender, "report", fake_report)
    assert c.get(f"/api/items/{ids['ship.blend']}/blend/report").json()["triangles"] == 0
    assert seen[0][0] == "ship.blend" and seen[0][1]
    png = tmp / "r.png"
    Image.new("RGBA", (4, 3)).save(png)
    monkeypatch.setattr(blender, "render", lambda src, h, cache, engine, cam: (str(png), "cycles"))
    r = c.get(f"/api/items/{ids['ship.blend']}/blend/render?engine=eevee&camera=0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-kurator-engine"] == "cycles"

    def broken(*a, **k):
        raise blender.BlenderError("Blender could not render this file: nothing visible to render")
    monkeypatch.setattr(blender, "render", broken)
    r = c.get(f"/api/items/{ids['ship.blend']}/blend/render")
    assert r.status_code == 422 and "nothing visible" in r.json()["detail"]
    monkeypatch.setattr(blender, "find", lambda: None)
    assert c.get(f"/api/items/{ids['ship.blend']}/blend/report").status_code == 415
