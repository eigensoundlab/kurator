"""Textured test models: a quad whose texture is red on the left (u < 0.5) and blue on the right,
in glTF (embedded maps), OBJ (+ .mtl, one map missing) and binary FBX (UVs, material, texture)."""

from __future__ import annotations

import io
import json
import os
import struct

import numpy as np
from PIL import Image

from model_samples import _fbx_node

QUAD_V = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)   # facing +Z
QUAD_UV = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)                   # glTF: v down
QUAD_F = [[0, 1, 2], [0, 2, 3]]


def red_blue(size=(8, 8)) -> Image.Image:
    im = Image.new("RGB", size, (220, 20, 20))
    im.paste((20, 40, 220), (size[0] // 2, 0, size[0], size[1]))
    return im


def _png(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def glb() -> bytes:
    base, normal = _png(red_blue()), _png(Image.new("RGB", (4, 4), (128, 128, 255)))
    bufs = [QUAD_V.tobytes(), QUAD_UV.tobytes(), np.array(QUAD_F, np.uint16).tobytes(), base, normal]
    blob, views = b"", []
    for b in bufs:
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(b)})
        blob += b + b"\0" * (-len(b) % 4)
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"name": "Paint", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
                       "normalTexture": {"index": 1}}],
        "textures": [{"source": 0}, {"source": 1}],
        "images": [{"bufferView": 3, "mimeType": "image/png", "name": "paint_albedo"},
                   {"bufferView": 4, "mimeType": "image/png", "name": "paint_normal"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"}],
        "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def obj_files(folder: str) -> None:
    os.makedirs(os.path.join(folder, "Textures"), exist_ok=True)
    red_blue().save(os.path.join(folder, "Textures", "Board_Albedo.png"))
    lines = ["mtllib board.mtl", "o Board"] + [f"v {x} {y} {z}" for x, y, z in QUAD_V]
    lines += [f"vt {u} {1 - v}" for u, v in QUAD_UV]                     # OBJ: v up
    lines += ["vn 0 0 1", "usemtl Board", "f 1/1/1 2/2/1 3/3/1 4/4/1"]
    open(os.path.join(folder, "board.obj"), "w").write("\n".join(lines) + "\n")
    # an absolute path from another computer (found by name), a missing bump map, options before a file
    open(os.path.join(folder, "board.mtl"), "w").write(
        "newmtl Board\nKd 1 1 1\nmap_Kd -s 1 1 1 C:\\Users\\artist\\Desktop\\board_albedo.PNG\n"
        "map_Bump -bm 0.5 board_normal.png\n")


def fbx() -> bytes:
    pvi = [0, 1, 2, ~3]
    uv = QUAD_UV.copy()
    uv[:, 1] = 1 - uv[:, 1]                                                # FBX: v up
    P = lambda *a: ("P", [("S", a[0]), ("S", a[1]), ("S", a[2]), ("S", a[3])] + list(a[4:]), [])  # noqa: E731
    content = _png(red_blue())
    nodes = [
        ("GlobalSettings", [], [("Properties70", [], [P("UpAxis", "int", "Integer", "", ("I", 1))])]),
        ("Objects", [], [
            ("Geometry", [("L", 100), ("S", "Quad\x00\x01Geometry"), ("S", "Mesh")], [
                ("Vertices", [("d", QUAD_V.astype(np.float64).reshape(-1))], []),
                ("PolygonVertexIndex", [("i", pvi)], []),
                ("LayerElementNormal", [("I", 0)], [
                    ("MappingInformationType", [("S", "ByPolygonVertex")], []),
                    ("ReferenceInformationType", [("S", "Direct")], []),
                    ("Normals", [("d", [0, 0, 1] * 4)], [])]),
                ("LayerElementUV", [("I", 0)], [
                    ("MappingInformationType", [("S", "ByPolygonVertex")], []),
                    ("ReferenceInformationType", [("S", "IndexToDirect")], []),
                    ("UV", [("d", uv.astype(np.float64).reshape(-1))], []),
                    ("UVIndex", [("i", [0, 1, 2, 3])], [])]),
                ("LayerElementMaterial", [("I", 0)], [
                    ("MappingInformationType", [("S", "AllSame")], []),
                    ("Materials", [("i", [0])], [])]),
            ]),
            ("Model", [("L", 200), ("S", "Quad\x00\x01Model"), ("S", "Mesh")], []),
            ("Material", [("L", 300), ("S", "Wood\x00\x01Material"), ("S", "")], []),
            ("Texture", [("L", 400), ("S", "WoodTex\x00\x01Texture"), ("S", "")],
             [("RelativeFilename", [("S", "D:\\proj\\wood_albedo.png")], [])]),
            ("Video", [("L", 500), ("S", "WoodTex\x00\x01Video"), ("S", "Clip")],
             [("Content", [("R", content)], [])]),
            ("Texture", [("L", 401), ("S", "WoodN\x00\x01Texture"), ("S", "")],
             [("RelativeFilename", [("S", "wood_normal.png")], [])]),
        ]),
        ("Connections", [], [
            ("C", [("S", "OO"), ("L", 100), ("L", 200)], []), ("C", [("S", "OO"), ("L", 200), ("L", 0)], []),
            ("C", [("S", "OO"), ("L", 300), ("L", 200)], []),
            ("C", [("S", "OP"), ("L", 400), ("L", 300), ("S", "DiffuseColor")], []),
            ("C", [("S", "OO"), ("L", 500), ("L", 400)], []),
            ("C", [("S", "OP"), ("L", 401), ("L", 300), ("S", "NormalMap")], []),
        ]),
    ]
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400)
    for n in nodes:
        out += _fbx_node(*n, len(out), False)
    return out + b"\0" * 13 + b"\0" * 160
