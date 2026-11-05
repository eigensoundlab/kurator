"""Compressed glTF and binary USD test files: the textured quad of textured_samples (red left,
blue right) as meshopt-compressed GLB (written by a minimal encoder below), as Draco GLB (DracoPy),
and as USD crate / USDZ with a texture inside (Pixar's usd-core)."""

from __future__ import annotations

import io
import json
import os
import struct

import numpy as np

from textured_samples import QUAD_F, QUAD_UV, QUAD_V, _png, red_blue


# ---------------------------------------------------------------- meshopt (minimal encoder)
def _zigzag8(d: np.ndarray) -> np.ndarray:
    d = d.astype(np.int16)
    return (((d << 1) ^ (d >> 15)) & 0xFF).astype(np.uint8)


def mo_vertices(raw: bytes, stride: int) -> bytes:
    """Vertex codec v0 using only 'raw' groups (mode 3): valid, if not small."""
    a = np.frombuffer(raw, np.uint8).reshape(-1, stride)
    count = len(a)
    bsize = min((8192 // stride) & ~15, 256)
    out = bytearray(b"\xa0")
    last = np.zeros(stride, np.uint8)   # baseline: zeros, stored in the tail
    for s in range(0, count, bsize):
        blk = a[s:s + bsize]
        n = len(blk)
        aligned = (n + 15) & ~15
        groups = aligned // 16
        for k in range(stride):
            col = blk[:, k].astype(np.int16)
            prev = np.r_[last[k], blk[:-1, k]].astype(np.int16)
            enc = np.zeros(aligned, np.uint8)
            enc[:n] = _zigzag8((col - prev + 128) % 256 - 128)
            out += bytes([0xFF] * ((groups + 3) // 4))
            out += enc.tobytes()
            last[k] = blk[-1, k]
    out += bytes(max(stride, 32) - stride) + bytes(stride)   # tail: padding, then the zero baseline
    return bytes(out)


def _vbyte(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def mo_sequence(idx) -> bytes:
    out, last = bytearray(b"\xd1"), 0
    for i in idx:
        d = int(i) - last
        z = (d << 1) ^ (d >> 31)
        out += _vbyte((z << 1) | 0)
        last = int(i)
    return bytes(out) + b"\0\0\0\0"


def _glb(doc, blob: bytes) -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def meshopt_glb() -> bytes:
    pos, uv = QUAD_V.tobytes(), QUAD_UV.tobytes()
    idx = np.array(QUAD_F, np.uint32).reshape(-1)
    png = _png(red_blue())
    parts = [mo_vertices(pos, 12), mo_vertices(uv, 8), mo_sequence(idx), png]
    blob, offs = b"", []
    for p in parts:
        offs.append(len(blob))
        blob += p + b"\0" * (-len(p) % 4)

    def mo(k, stride, count, mode):
        return {"buffer": 1, "byteLength": 4 * count * 0 + stride * count, "byteStride": stride if mode == "ATTRIBUTES" else None,
                "extensions": {"EXT_meshopt_compression": {"buffer": 0, "byteOffset": offs[k], "byteLength": len(parts[k]),
                                                           "byteStride": stride, "count": count, "mode": mode}}}
    views = [mo(0, 12, 4, "ATTRIBUTES"), mo(1, 8, 4, "ATTRIBUTES"), mo(2, 4, 6, "INDICES"),
             {"buffer": 0, "byteOffset": offs[3], "byteLength": len(png)}]
    for v in views:
        if v.get("byteStride") is None:
            v.pop("byteStride", None)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "extensionsUsed": ["EXT_meshopt_compression"], "extensionsRequired": ["EXT_meshopt_compression"],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
           "textures": [{"source": 0}], "images": [{"bufferView": 3, "mimeType": "image/png"}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]},
                         {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": 6, "type": "SCALAR"}],
           "bufferViews": views,
           "buffers": [{"byteLength": len(blob)}, {"byteLength": 64, "extensions": {"EXT_meshopt_compression": {"fallback": True}}}]}
    return _glb(doc, blob)


# ---------------------------------------------------------------- Draco
def draco_glb() -> bytes:
    import DracoPy
    enc = DracoPy.encode(QUAD_V.astype(np.float64), np.array(QUAD_F, np.uint32), tex_coord=QUAD_UV.astype(np.float64),
                         preserve_order=True, quantization_bits=16, tex_coord_quantization_bits=16)
    png = _png(red_blue())
    blob = enc + b"\0" * (-len(enc) % 4)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(enc)},
             {"buffer": 0, "byteOffset": len(blob), "byteLength": len(png)}]
    blob += png
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "extensionsUsed": ["KHR_draco_mesh_compression"], "extensionsRequired": ["KHR_draco_mesh_compression"],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0,
                                       "extensions": {"KHR_draco_mesh_compression": {"bufferView": 0, "attributes": {"POSITION": 0, "TEXCOORD_0": 1}}}}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
           "textures": [{"source": 0}], "images": [{"bufferView": 1, "mimeType": "image/png"}],
           "accessors": [{"componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]},
                         {"componentType": 5126, "count": 4, "type": "VEC2"},
                         {"componentType": 5125, "count": 6, "type": "SCALAR"}],
           "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    return _glb(doc, blob)


# ---------------------------------------------------------------- USD
def usd_files(folder: str) -> None:
    """quad.usdc (Z up, moved by a parent transform, texture next to it) and quad.usdz (texture inside)."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils
    os.makedirs(os.path.join(folder, "tex"), exist_ok=True)
    red_blue().save(os.path.join(folder, "tex", "quad_albedo.png"))
    path = os.path.join(folder, "quad.usdc")
    st = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(st, "/root").AddTranslateOp().Set(Gf.Vec3d(0, 0, 5))
    m = UsdGeom.Mesh.Define(st, "/root/quad")
    # the quad facing -Y in Z-up space, i.e. +Z (towards the viewer) once turned Y-up
    m.CreatePointsAttr([(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)])
    m.CreateFaceVertexCountsAttr([4])
    m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    pv = UsdGeom.PrimvarsAPI(m.GetPrim()).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
    pv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    mat = UsdShade.Material.Define(st, "/root/mat")
    sh = UsdShade.Shader.Define(st, "/root/mat/surface")
    sh.CreateIdAttr("UsdPreviewSurface")
    tex = UsdShade.Shader.Define(st, "/root/mat/tex")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("tex/quad_albedo.png")
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    st.GetRootLayer().Save()
    UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(path), os.path.join(folder, "quad.usdz"))
