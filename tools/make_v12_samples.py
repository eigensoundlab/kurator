"""Sample library for tools/ui_check_v12.py: a brick ball (GLB with base colour and normal map),
textured quads in GLB/OBJ/FBX, a program-code .obj, and a 2D shield with normal, glow, AO, depth and
sky maps.

    python tools/make_v12_samples.py FOLDER
"""

import io
import json
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
import textured_samples as T  # noqa: E402


def png(a) -> bytes:
    b = io.BytesIO()
    Image.fromarray(a).save(b, "PNG")
    return b.getvalue()


def normal_from_height(h, k):
    dx = np.roll(h, -1, 1) - np.roll(h, 1, 1)
    dy = np.roll(h, -1, 0) - np.roll(h, 1, 0)
    n = np.stack([-dx * k, dy * k, np.ones_like(h)], -1)
    return ((n / np.linalg.norm(n, axis=-1, keepdims=True) * 0.5 + 0.5) * 255).astype(np.uint8)


def brick_ball(path):
    nu, nv = 64, 32
    us, vs = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(0, 1, nv + 1))
    th, ph = us * 2 * np.pi, vs * np.pi
    P = np.stack([np.sin(ph) * np.cos(th), np.cos(ph), np.sin(ph) * np.sin(th)], -1).reshape(-1, 3).astype(np.float32)
    UV = np.stack([us, vs], -1).reshape(-1, 2).astype(np.float32)
    F = np.array([[a, a + nu + 1, a + 1] for j in range(nv) for i in range(nu) for a in [j * (nu + 1) + i]]
                 + [[a + 1, a + nu + 1, a + nu + 2] for j in range(nv) for i in range(nu) for a in [j * (nu + 1) + i]], np.uint32)
    W, H = 512, 256
    y, x = np.mgrid[0:H, 0:W]
    mortar = ((y % 32) < 3) | (((x + (y // 32 % 2) * 32) % 64) < 3)
    base = np.zeros((H, W, 3), np.uint8)
    base[:] = (170, 70, 50)
    base[mortar] = (200, 200, 190)
    h = np.asarray(Image.fromarray(np.where(mortar, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), np.float32) / 255
    bufs = [P.tobytes(), P.tobytes(), UV.tobytes(), F.tobytes(), png(base), png(normal_from_height(h, 3))]
    blob, views = b"", []
    for b in bufs:
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(b)})
        blob += b + b"\0" * (-len(b) % 4)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3, "material": 0}]}],
           "materials": [{"name": "Bricks", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}, "normalTexture": {"index": 1}}],
           "textures": [{"source": 0}, {"source": 1}],
           "images": [{"bufferView": 4, "mimeType": "image/png", "name": "bricks_albedo"},
                      {"bufferView": 5, "mimeType": "image/png", "name": "bricks_normal"}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(P), "type": "VEC3", "min": [-1, -1, -1], "max": [1, 1, 1]},
                         {"bufferView": 1, "componentType": 5126, "count": len(P), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5126, "count": len(P), "type": "VEC2"},
                         {"bufferView": 3, "componentType": 5125, "count": F.size, "type": "SCALAR"}],
           "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    open(path, "wb").write(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def shield(d):
    W, H = 400, 300
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((x - W / 2) / 150) ** 2 + ((y - H / 2) / 120) ** 2)
    inside = r < 1
    gem = np.zeros_like(x)
    for gx, gy in ((W / 2 - 60, H / 2), (W / 2, H / 2 - 20), (W / 2 + 60, H / 2)):
        gem = np.maximum(gem, np.clip(1 - np.hypot(x - gx, y - gy) / 18, 0, 1))
    height = (np.clip(1 - r, 0, 1) ** 0.5 * 0.6 + np.where((r > 0.82) & (r < 0.95), 0.4, 0) + gem * 0.5) * inside
    base = np.zeros((H, W, 4), np.uint8)
    base[..., 0] = 120 + 60 * (r < 0.82)
    base[..., 1] = 90 + 30 * (r < 0.82)
    base[..., 2] = 60
    base[..., :3][gem > 0] = (40, 200, 230)
    base[..., 3] = inside * 255
    Image.fromarray(base).save(f"{d}/hero.png")
    hb = np.asarray(Image.fromarray((height * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255
    Image.fromarray((hb * 255).astype(np.uint8)).save(f"{d}/hero_depth.png")
    Image.fromarray(normal_from_height(hb, 8)).save(f"{d}/hero_normal.png")
    glow = np.zeros((H, W, 3), np.uint8)
    glow[gem > 0.2] = (60, 230, 255)
    Image.fromarray(glow).save(f"{d}/hero_glow.png")
    Image.fromarray((np.clip(1 - 0.6 * np.exp(-((r - 0.82) / 0.05) ** 2), 0, 1) * 255).astype(np.uint8)).save(f"{d}/hero_ao.png")
    sky = np.zeros((H, W, 3), np.uint8)
    sky[..., 0] = (30 + y / H * 80).astype(np.uint8)
    sky[..., 1] = (40 + y / H * 60).astype(np.uint8)
    sky[..., 2] = 120
    Image.fromarray(sky).save(f"{d}/hero_sky.png")
    Image.fromarray(base).save(f"{d}/hero_v2.png")


if __name__ == "__main__":
    root = sys.argv[1]
    models, sprites = os.path.join(root, "models"), os.path.join(root, "sprites")
    os.makedirs(models, exist_ok=True)
    os.makedirs(sprites, exist_ok=True)
    brick_ball(os.path.join(models, "bricks_ball.glb"))
    open(os.path.join(models, "quad.glb"), "wb").write(T.glb())
    open(os.path.join(models, "quad.fbx"), "wb").write(T.fbx())
    T.obj_files(models)
    open(os.path.join(models, "compiled.obj"), "wb").write(b"\x64\x86\x05\x00" + bytes(range(256)) * 4)
    shield(sprites)
