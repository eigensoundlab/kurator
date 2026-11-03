"""Small 3D files in every supported format, written from scratch (no 3D library needed).

The test object is a red-ish cube (12 triangles) with a blue pyramid on top (6 triangles), so
every geometry reader should find 18 triangles (OBJ/PLY/OFF use quads for the cube: still 18).
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import struct
import zipfile
import zlib

import numpy as np
from PIL import Image

CUBE_V = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                   [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], np.float32)
CUBE_Q = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 4, 7, 3]]
PYR_V = np.array([[-1, 1, -1], [1, 1, -1], [1, 1, 1], [-1, 1, 1], [0, 2.5, 0]], np.float32)  # Y up
PYR_T = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4], [0, 2, 1], [0, 3, 2]]
TRIS = 18


def quads_to_tris(q):
    return [[a, b, c] for a, b, c, d in q] + [[a, c, d] for a, b, c, d in q]


def all_tris():
    v = np.concatenate([CUBE_V, PYR_V])
    f = np.array(quads_to_tris(CUBE_Q) + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T], np.int64)
    return v, f


def zup(v):  # Y-up -> Z-up (for formats that are Z up)
    return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)


# ---------------------------------------------------------------- writers
def stl_binary() -> bytes:
    v, f = all_tris()
    v = zup(v)
    out = io.BytesIO()
    out.write(b"Kurator test cube".ljust(80, b"\0"))
    out.write(struct.pack("<I", len(f)))
    for t in f:
        out.write(struct.pack("<3f", 0, 0, 0))
        for i in t:
            out.write(struct.pack("<3f", *v[i]))
        out.write(b"\0\0")
    return out.getvalue()


def stl_ascii() -> bytes:
    v, f = all_tris()
    v = zup(v)
    lines = ["solid test"]
    for t in f:
        lines += ["facet normal 0 0 0", " outer loop"] + [f"  vertex {v[i][0]} {v[i][1]} {v[i][2]}" for i in t] + [" endloop", "endfacet"]
    return ("\n".join(lines + ["endsolid test"]) + "\n").encode()


def obj() -> tuple[bytes, bytes]:
    lines = ["# test", "mtllib test.mtl", "o Cube"] + [f"v {x} {y} {z}" for x, y, z in CUBE_V]
    lines += ["usemtl Red"] + ["f " + " ".join(f"{i + 1}/1/1" for i in q) for q in CUBE_Q]
    lines += ["o Pyramid"] + [f"v {x} {y} {z}" for x, y, z in PYR_V] + ["usemtl Blue"]
    lines += ["f " + " ".join(str(i - 5) for i in t) for t in PYR_T]   # negative (relative) indices
    mtl = "newmtl Red\nKd 0.8 0.1 0.1\nmap_Kd textures/red_albedo.png\nnewmtl Blue\nKd 0.1 0.2 0.9\n"
    return ("\n".join(lines) + "\n").encode(), mtl.encode()


def ply_binary() -> bytes:
    v = np.concatenate([CUBE_V, PYR_V])
    faces = CUBE_Q + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T]
    head = (f"ply\nformat binary_little_endian 1.0\ncomment test\nelement vertex {len(v)}\n"
            "property float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\n"
            f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n").encode()
    body = b"".join(struct.pack("<3f3B", *p, 200, 60, 40) for p in v)
    body += b"".join(struct.pack("<B", len(f)) + struct.pack(f"<{len(f)}i", *f) for f in faces)
    return head + body


def ply_ascii() -> bytes:
    v, f = all_tris()
    head = f"ply\nformat ascii 1.0\nelement vertex {len(v)}\nproperty float x\nproperty float y\nproperty float z\nelement face {len(f)}\nproperty list uchar int vertex_indices\nend_header\n"
    return (head + "".join(f"{a} {b} {c}\n" for a, b, c in v) + "".join(f"3 {a} {b} {c}\n" for a, b, c in f)).encode()


def off() -> bytes:
    v = np.concatenate([CUBE_V, PYR_V])
    faces = CUBE_Q + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T]
    return (f"OFF\n{len(v)} {len(faces)} 0\n" + "".join(f"{a} {b} {c}\n" for a, b, c in v)
            + "".join(f"{len(f)} " + " ".join(map(str, f)) + "\n" for f in faces)).encode()


def _gltf_doc(external: bool):
    pv = PYR_V - np.array([0, 1, 0], np.float32)   # pyramid geometry at the origin, moved up by its node
    bufs = [CUBE_V.tobytes(), np.array(quads_to_tris(CUBE_Q), np.uint16).tobytes(),
            pv.tobytes(), np.array(PYR_T, np.uint32).tobytes()]
    blob, views, off_ = b"", [], 0
    for b in bufs:
        views.append({"buffer": 0, "byteOffset": off_, "byteLength": len(b)})
        blob += b + b"\0" * (-len(b) % 4)
        off_ = len(blob)
    # an embedded 2x2 texture (green) on the cube material
    png = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 200, 40)).save(png, "PNG")
    png = png.getvalue()
    views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(png)})
    blob += png + b"\0" * (-len(png) % 4)
    doc = {
        "asset": {"version": "2.0", "generator": "Kurator tests"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "root", "children": [1, 2]}, {"mesh": 0}, {"mesh": 1, "translation": [0, 1, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]},
                   {"primitives": [{"attributes": {"POSITION": 2}, "indices": 3, "material": 1}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                      {"pbrMetallicRoughness": {"baseColorFactor": [0.1, 0.2, 0.9, 1]}}],
        "textures": [{"source": 0}], "images": [{"bufferView": 4, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-1, -1, -1], "max": [1, 1, 1]},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 5, "type": "VEC3", "min": [-1, 0, -1], "max": [1, 1.5, 1]},
            {"bufferView": 3, "componentType": 5125, "count": 18, "type": "SCALAR"}],
        "bufferViews": views,
        "animations": [],
        "buffers": [{"byteLength": len(blob)} if not external else {"byteLength": len(blob), "uri": "scene.bin"}],
    }
    return doc, blob


def glb() -> bytes:
    doc, blob = _gltf_doc(False)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def gltf_external() -> tuple[bytes, bytes]:
    doc, blob = _gltf_doc(True)
    return json.dumps(doc, indent=1).encode(), blob


# FBX binary ---------------------------------------------------------
def _fbx_prop(p) -> bytes:
    kind, val = p
    if kind == "I":
        return b"I" + struct.pack("<i", val)
    if kind == "L":
        return b"L" + struct.pack("<q", val)
    if kind == "D":
        return b"D" + struct.pack("<d", val)
    if kind == "S":
        b = val.encode() if isinstance(val, str) else val
        return b"S" + struct.pack("<I", len(b)) + b
    if kind in ("d", "i"):
        arr = np.asarray(val, "<f8" if kind == "d" else "<i4")
        raw = zlib.compress(arr.tobytes())
        return kind.encode() + struct.pack("<III", len(arr), 1, len(raw)) + raw
    raise ValueError(kind)


def _fbx_node(name, props, children, offset, wide) -> bytes:
    hdr = 25 if wide else 13
    pb = b"".join(_fbx_prop(p) for p in props)
    start = offset + hdr + len(name) + len(pb)
    kids = b""
    for c in children:
        kids += _fbx_node(*c, start + len(kids), wide)
    if children:
        kids += b"\0" * hdr
    end = start + len(kids)
    fmt = "<QQQB" if wide else "<IIIB"
    return struct.pack(fmt, end, len(props), len(pb), len(name)) + name.encode() + pb + kids


def fbx_binary(version: int = 7400) -> bytes:
    wide = version >= 7500
    v = np.concatenate([CUBE_V, PYR_V])
    pvi = []
    for q in CUBE_Q:
        pvi += q[:-1] + [~q[-1]]
    for t in PYR_T:
        pvi += [t[0] + 8, t[1] + 8, ~(t[2] + 8)]
    P = lambda *a: ("P", [("S", a[0]), ("S", a[1]), ("S", a[2]), ("S", a[3])] + list(a[4:]), [])  # noqa: E731
    nodes = [
        ("FBXHeaderExtension", [], [("CreationTimeStamp", [], [(k, [("I", val)], []) for k, val in
                                    (("Year", 2021), ("Month", 5), ("Day", 4), ("Hour", 10), ("Minute", 30), ("Second", 0))])]),
        ("Creator", [("S", "Kurator test FBX writer")], []),
        ("GlobalSettings", [], [("Properties70", [], [P("UpAxis", "int", "Integer", "", ("I", 1)),
                                                      P("UnitScaleFactor", "double", "Number", "", ("D", 1.0))])]),
        ("Objects", [], [
            ("Geometry", [("L", 100), ("S", "Shape\x00\x01Geometry"), ("S", "Mesh")],
             [("Vertices", [("d", v.reshape(-1))], []), ("PolygonVertexIndex", [("i", pvi)], [])]),
            ("Model", [("L", 200), ("S", "Shape\x00\x01Model"), ("S", "Mesh")],
             [("Properties70", [], [P("Lcl Translation", "Lcl Translation", "", "A", ("D", 0.0), ("D", 10.0), ("D", 0.0))])]),
            ("Material", [("L", 300), ("S", "Mat\x00\x01Material"), ("S", "")], []),
            ("Texture", [("L", 400), ("S", "Tex\x00\x01Texture"), ("S", "")], [("RelativeFilename", [("S", "textures\\shape_albedo.png")], [])]),
        ]),
        ("Connections", [], [("C", [("S", "OO"), ("L", 100), ("L", 200)], []), ("C", [("S", "OO"), ("L", 200), ("L", 0)], [])]),
    ]
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    for n in nodes:
        out += _fbx_node(*n, len(out), wide)
    out += b"\0" * (25 if wide else 13)
    return out + b"\0" * 160   # footer (not read)


def fbx_ascii() -> bytes:
    v, f = all_tris()
    pvi = []
    for t in f:
        pvi += [int(t[0]), int(t[1]), ~int(t[2])]
    return (f'; FBX 7.4.0 project file\nObjects:  {{\n Geometry: 1, "Geometry::", "Mesh" {{\n'
            f'  Vertices: *{v.size} {{\n   a: {",".join(str(float(x)) for x in v.reshape(-1))}\n  }}\n'
            f'  PolygonVertexIndex: *{len(pvi)} {{\n   a: {",".join(map(str, pvi))}\n  }}\n }}\n}}\n').encode()


def dae() -> bytes:
    v = zup(np.concatenate([CUBE_V, PYR_V]))
    f = np.array(quads_to_tris(CUBE_Q) + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T])
    return f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <asset><contributor><authoring_tool>Kurator tests</authoring_tool></contributor>
  <created>2020-02-03T04:05:06Z</created><up_axis>Z_UP</up_axis></asset>
 <library_geometries><geometry id="g1"><mesh>
  <source id="g1-pos"><float_array id="g1-arr" count="{v.size}">{" ".join(map(str, v.reshape(-1)))}</float_array></source>
  <vertices id="g1-v"><input semantic="POSITION" source="#g1-pos"/></vertices>
  <triangles count="{len(f)}"><input semantic="VERTEX" source="#g1-v" offset="0"/><p>{" ".join(map(str, f.reshape(-1)))}</p></triangles>
 </mesh></geometry></library_geometries>
 <library_visual_scenes><visual_scene id="s"><node id="n"><translate>0 0 3</translate>
  <instance_geometry url="#g1"/></node></visual_scene></library_visual_scenes>
</COLLADA>'''.encode()


def threemf(thumbnail: bool) -> bytes:
    v = zup(np.concatenate([CUBE_V, PYR_V]))
    f = np.array(quads_to_tris(CUBE_Q) + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T])
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             '<metadata name="Application">Kurator tests</metadata><metadata name="CreationDate">2022-07-08</metadata>\n'
             '<resources><object id="1" type="model"><mesh><vertices>'
             + "".join(f'<vertex x="{a}" y="{b}" z="{c}"/>' for a, b, c in v)
             + "</vertices><triangles>" + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
             + '</triangles></mesh></object></resources>\n<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></build></model>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("3D/3dmodel.model", model)
        if thumbnail:
            png = io.BytesIO()
            Image.new("RGB", (64, 64), (250, 120, 0)).save(png, "PNG")
            z.writestr("Metadata/thumbnail.png", png.getvalue())
    return buf.getvalue()


def three_ds() -> bytes:
    v = zup(np.concatenate([CUBE_V, PYR_V])).astype("<f4")
    f = np.array(quads_to_tris(CUBE_Q) + [[a + 8, b + 8, c + 8] for a, b, c in PYR_T])

    def chunk(cid, body):
        return struct.pack("<HI", cid, 6 + len(body)) + body
    verts = chunk(0x4110, struct.pack("<H", len(v)) + v.tobytes())
    faces = chunk(0x4120, struct.pack("<H", len(f)) + b"".join(struct.pack("<4H", a, b, c, 0) for a, b, c in f))
    obj = chunk(0x4000, b"Shape\0" + chunk(0x4100, verts + faces))
    mat = chunk(0xAFFF, chunk(0xA200, chunk(0xA300, b"brick.jpg\0")))
    return chunk(0x4D4D, chunk(0x3D3D, mat + obj))


def usda() -> bytes:
    v, f = all_tris()
    pts = ", ".join(f"({a}, {b}, {c})" for a, b, c in v)
    return (f'#usda 1.0\n(\n    upAxis = "Y"\n)\n\ndef Xform "root"\n{{\n    def Mesh "shape"\n    {{\n'
            f'        int[] faceVertexCounts = [{", ".join("3" for _ in f)}]\n'
            f'        int[] faceVertexIndices = [{", ".join(str(int(i)) for i in f.reshape(-1))}]\n'
            f'        point3f[] points = [{pts}]\n    }}\n}}\n').encode()


def vox() -> bytes:
    def chunk(cid, content=b"", children=b""):
        return cid + struct.pack("<ii", len(content), len(children)) + content + children

    def dct(d):
        out = struct.pack("<i", len(d))
        for k, v in d.items():
            out += struct.pack("<i", len(k)) + k.encode() + struct.pack("<i", len(v)) + v.encode()
        return out
    xyzi = [(x, y, z, 1) for x in range(4) for y in range(4) for z in range(2)]   # 4x4x2 slab, colour 1
    xyzi2 = [(1, 1, z, 2) for z in range(3)]                                     # a 1x1x3 post, colour 2
    pal = bytearray(1024)
    pal[0:4] = bytes((220, 40, 40, 255))
    pal[4:8] = bytes((40, 80, 230, 255))
    models = (chunk(b"SIZE", struct.pack("<iii", 4, 4, 2)) + chunk(b"XYZI", struct.pack("<i", len(xyzi)) + bytes(b for v in xyzi for b in v))
              + chunk(b"SIZE", struct.pack("<iii", 3, 3, 3)) + chunk(b"XYZI", struct.pack("<i", len(xyzi2)) + bytes(b for v in xyzi2 for b in v)))
    scene = (chunk(b"nTRN", struct.pack("<i", 0) + dct({}) + struct.pack("<iiii", 1, -1, -1, 1) + dct({}))
             + chunk(b"nGRP", struct.pack("<i", 1) + dct({}) + struct.pack("<iii", 2, 2, 4))
             + chunk(b"nTRN", struct.pack("<i", 2) + dct({}) + struct.pack("<iiii", 3, -1, 0, 1) + dct({"_t": "0 0 1"}))
             + chunk(b"nSHP", struct.pack("<i", 3) + dct({}) + struct.pack("<ii", 1, 0) + dct({}))
             + chunk(b"nTRN", struct.pack("<i", 4) + dct({}) + struct.pack("<iiii", 5, -1, 0, 1) + dct({"_t": "0 0 4"}))
             + chunk(b"nSHP", struct.pack("<i", 5) + dct({}) + struct.pack("<ii", 1, 1) + dct({})))
    return b"VOX " + struct.pack("<i", 150) + chunk(b"MAIN", b"", models + scene + chunk(b"RGBA", bytes(pal)))


# Blender ------------------------------------------------------------
def blend(variant: str = "plain") -> bytes:
    """A minimal .blend: header, a few ID blocks, the thumbnail (TEST) block, ENDB."""
    w = h = 32
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            if 8 <= x < 24 and 8 <= y < 24:
                img.putpixel((x, y), (255, 160, 0, 255))
    img.putpixel((0, 0), (0, 255, 0, 255))   # top-left marker: checks the vertical flip
    px = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()   # Blender stores rows bottom-up
    test = struct.pack("<ii", w, h) + px
    blocks = [(b"REND", b"\0" * 72), (b"TEST", test), (b"GLOB", b"\0" * 64),
              (b"OB\0\0", b"\0" * 100), (b"OB\0\0", b"\0" * 100), (b"ME\0\0", b"\0" * 50), (b"MA\0\0", b"\0" * 40)]
    if variant == "v5":
        out = b"BLENDER17-01v0500"
        for code, body in blocks + [(b"ENDB", b"")]:
            out += code + struct.pack("<iQqq", 0, 0x1000, len(body), 1) + body
    else:
        out = b"BLENDER-v405"
        for code, body in blocks + [(b"ENDB", b"")]:
            out += code + struct.pack("<iQii", len(body), 0x1000, 0, 1) + body
    if variant == "gzip":
        return gzip.compress(out)
    if variant == "zstd":
        try:
            from compression import zstd
            frames = [zstd.compress(out[:500]), zstd.compress(out[500:])]
        except ImportError:
            import zstandard
            c = zstandard.ZstdCompressor()
            frames = [c.compress(out[:500]), c.compress(out[500:])]   # two frames, like Blender
        return b"".join(frames)
    return out


def skp_with_png() -> bytes:
    png = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 130, 200)).save(png, "PNG")
    return b"\xff\xfe\xff\x0eSketchUp Model" + b"\0" * 300 + png.getvalue() + b"\0" * 200


def write_all(folder: str) -> dict[str, int]:
    """Write every sample; returns {file name: expected triangles (0 = no geometry)}."""
    import os
    os.makedirs(folder, exist_ok=True)
    exp = {}

    def w(name, data, tris):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        exp[name] = tris

    w("cube_bin.stl", stl_binary(), TRIS)
    w("cube_ascii.stl", stl_ascii(), TRIS)
    o, m = obj()
    w("cube.obj", o, TRIS)
    with open(os.path.join(folder, "test.mtl"), "wb") as f:
        f.write(m)
    w("cube_bin.ply", ply_binary(), TRIS)
    w("cube_ascii.ply", ply_ascii(), TRIS)
    w("cube.off", off(), TRIS)
    w("scene.glb", glb(), TRIS)
    g, b = gltf_external()
    w("scene.gltf", g, TRIS)
    with open(os.path.join(folder, "scene.bin"), "wb") as f:
        f.write(b)
    w("shape74.fbx", fbx_binary(7400), TRIS)
    w("shape75.fbx", fbx_binary(7500), TRIS)
    w("shape_ascii.fbx", fbx_ascii(), TRIS)
    w("shape.dae", dae(), TRIS)
    w("print.3mf", threemf(False), TRIS)
    w("print_thumb.3mf", threemf(True), TRIS)
    w("shape.3ds", three_ds(), TRIS)
    w("shape.usda", usda(), TRIS)
    w("voxels.vox", vox(), -1)    # triangle count depends on exposed faces
    w("scene.blend", blend("plain"), 0)
    w("scene_zstd.blend", blend("zstd"), 0)
    w("scene_gzip.blend", blend("gzip"), 0)
    w("scene_v5.blend", blend("v5"), 0)
    w("scene.blend1", blend("plain"), 0)
    w("house.skp", skp_with_png(), 0)
    w("rig.ma", b"//Maya ASCII 2024 scene\nrequires maya \"2024\";\n", 0)
    return exp


if __name__ == "__main__":
    import sys
    print(write_all(sys.argv[1]))
    _ = base64  # keep import for ad-hoc experiments
