"""Mesh readers. Each takes (data, path) and returns a Mesh (Y up).

Supported geometry: glTF/GLB, OBJ, FBX (binary and ASCII), STL, PLY, COLLADA (.dae), 3MF, 3DS,
OFF, USDA (and .usdz containing USDA), MagicaVoxel .vox.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import struct
import zipfile
import zlib
from datetime import datetime

import numpy as np

from . import Material, Mesh, MeshError, TexRef, material_colors, merge, resolve_texture, z_up_to_y_up

MAX_EXTERNAL = 512 * 1024 * 1024


def _fan(counts: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Triangulate polygons (vertex counts + flat index list) as fans, keeping polygon order
    (so per-polygon data like materials can be repeated with np.repeat(x, counts - 2))."""
    counts = np.asarray(counts, np.int64)
    idx = np.asarray(idx, np.int64)
    if len(counts) and (counts == 3).all():
        return idx[: 3 * len(counts)].reshape(-1, 3)
    ntri = np.maximum(counts - 2, 0)
    if not ntri.sum():
        return np.zeros((0, 3), np.int64)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    poly = np.repeat(np.arange(len(counts)), ntri)
    k = np.arange(int(ntri.sum())) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
    s = starts[poly]
    return np.stack([idx[s], idx[s + k], idx[s + k + 1]], 1)


def _no_dtd(data: bytes) -> None:
    head = data[:4096]
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data[:65536]:
        raise MeshError("XML with a DTD is not read (safety)")


# ---------------------------------------------------------------- STL
def read_stl(data: bytes, path: str) -> Mesh:
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 == len(data) or (not data[:5].lower() == b"solid" and 84 + n * 50 <= len(data)):
            rec = np.frombuffer(data, np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), n, 84)
            v = rec["v"].reshape(-1, 3)
            colors = None
            attr = rec["a"]
            if (attr & 0x8000).any():   # VisCAM/SolidView colour convention: 15-bit RGB, bit 15 = valid
                r = ((attr >> 10) & 31) * 255 // 31
                g = ((attr >> 5) & 31) * 255 // 31
                b = (attr & 31) * 255 // 31
                colors = np.stack([r, g, b], 1).astype(np.uint8)
                colors[(attr & 0x8000) == 0] = (180, 186, 198)
            f = np.arange(len(v), dtype=np.int32).reshape(-1, 3)
            name = data[:80].split(b"\0")[0].decode("latin-1", "replace").strip()
            return Mesh(z_up_to_y_up(v), f, colors, {"encoding": "binary", **({"header": name} if name else {})})
    nums = re.findall(rb"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", data)
    if not nums:
        raise MeshError("no STL facets")
    v = np.array(nums, dtype=np.float32)
    v = v[: len(v) // 3 * 3]
    return Mesh(z_up_to_y_up(v), np.arange(len(v), dtype=np.int32).reshape(-1, 3), None, {"encoding": "ASCII"})


# ---------------------------------------------------------------- OBJ
def _floats(lines: list[bytes], n: int) -> np.ndarray:
    try:
        return np.array([ln.split()[1:n + 1] for ln in lines], dtype=np.float32)
    except ValueError:   # a damaged line somewhere: keep what can be read
        out = []
        for ln in lines:
            try:
                vals = [float(t) for t in ln.split()[1:n + 1]]
            except ValueError:
                vals = []
            out.append((vals + [0.0] * n)[:n])
        return np.array(out, dtype=np.float32).reshape(-1, n)


def read_obj(data: bytes, path: str, full: bool = False) -> Mesh:
    head = data[:8192]
    if b"\0" in head or head[:2] in (b"\x64\x86", b"\x4c\x01", b"\x64\xaa") or head[:4] == b"\x00\x00\xff\xff":
        raise MeshError("not a 3D model: this .obj is compiled program code (a build file), not a Wavefront OBJ")
    verts: list[bytes] = []
    tverts: list[bytes] = []
    nverts: list[bytes] = []
    counts: list[int] = []
    idx: list[int] = []
    tidx: list[int] = []
    nidx: list[int] = []
    face_mat: list[int] = []
    mats: dict[str, int] = {}
    cur = -1
    objects = 0
    mtllibs = []
    bad = 0
    for line in data.split(b"\n"):
        if line[:2] == b"v ":
            verts.append(line)
        elif line[:3] == b"vt ":
            tverts.append(line)
        elif line[:3] == b"vn ":
            if full:
                nverts.append(line)
        elif line[:2] == b"f ":
            nv, nt, nn = len(verts), len(tverts), len(nverts)
            fi, ti, ni = [], [], []
            try:
                for p in line.split()[1:]:
                    a = p.split(b"/")
                    if not a[0]:
                        continue
                    k = int(a[0])
                    fi.append(k - 1 if k > 0 else nv + k)
                    t = int(a[1]) if len(a) > 1 and a[1] else 0
                    ti.append(t - 1 if t > 0 else (nt + t if t < 0 else -1))
                    if full:
                        q = int(a[2]) if len(a) > 2 and a[2] else 0
                        ni.append(q - 1 if q > 0 else (nn + q if q < 0 else -1))
            except ValueError:
                bad += 1
                continue
            if len(fi) >= 3:
                counts.append(len(fi))
                idx.extend(fi)
                tidx.extend(ti)
                nidx.extend(ni)
                face_mat.append(cur)
        elif line[:7] == b"usemtl ":
            cur = mats.setdefault(line[7:].strip().decode("utf-8", "replace"), len(mats))
        elif line[:2] in (b"o ", b"g "):
            objects += 1
        elif line[:7] == b"mtllib ":
            mtllibs.append(line[7:].strip().decode("utf-8", "replace"))
    if not verts or not counts:
        raise MeshError("no OBJ faces")
    v = _floats(verts, 3)
    counts_a = np.array(counts)
    corners = _fan(counts_a, np.arange(len(idx)))          # positions in the flat corner lists
    f = np.array(idx, np.int64)[corners]
    fm = np.repeat(np.array(face_mat), counts_a - 2)
    info = {"objects": max(objects, 1), "materials": len(mats)}
    if bad:
        info["note"] = f"{bad} damaged face lines skipped"
    corner_uv = None
    if tverts:
        vt = _floats(tverts, 2)
        vt[:, 1] = 1 - vt[:, 1]                              # to the glTF convention (v down)
        ti = np.array(tidx, np.int64)[corners]
        ok = (ti >= 0) & (ti < len(vt))
        if ok.any():
            corner_uv = np.where(ok[..., None], vt[np.clip(ti, 0, len(vt) - 1)], 0).astype(np.float32)
    corner_n = None
    if nverts and len(nidx) == len(idx):
        vn = _floats(nverts, 3)
        ni_ = np.array(nidx, np.int64)[corners]
        okn = (ni_ >= 0) & (ni_ < len(vn))
        if okn.all():
            corner_n = vn[ni_].astype(np.float32)
    names = sorted(mats, key=mats.get)
    materials = [Material(name=n) for n in names]
    base = os.path.dirname(path)
    for lib in mtllibs:
        mp = resolve_texture(base, lib)   # .mtl files are found the same way as textures
        if mp and os.path.getsize(mp) < 8 * 1024 * 1024:
            with open(mp, "rb") as fh:
                defs = _read_mtl(fh.read(), os.path.dirname(mp))
            for m in materials:
                if m.name in defs:
                    d = defs[m.name]
                    m.color, m.maps, m.emissive = d.color, d.maps, d.emissive
    if mtllibs:
        info["material_files"] = mtllibs
    tex = sorted({r.name for m in materials for r in m.maps.values()})
    if tex:
        info["textures"] = tex
    ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
    if not ok.all():
        f, fm = f[ok], fm[ok]
        corner_uv = corner_uv[ok] if corner_uv is not None else None
        corner_n = corner_n[ok] if corner_n is not None else None
    mesh = Mesh(v, f.astype(np.int32), None, info, corner_uv=corner_uv,
                face_mat=fm.astype(np.int32) if mats else None, materials=materials, corner_normals=corner_n)
    material_colors(mesh)
    return mesh


_MTL_ROLES = {"map_kd": "base", "map_ke": "emissive", "map_d": "opacity", "map_ks": "specular", "map_pr": "roughness",
              "map_pm": "metallic", "map_ka": "occlusion", "norm": "normal", "map_bump": "normal", "bump": "normal",
              "disp": "height", "map_disp": "height"}


def _mtl_file(rest: str) -> str:
    """The file name of a map line, without options like -bm 1.0 or -s 1 1 1."""
    toks = rest.split()
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("-") and not out:
            i += 1
            while i < len(toks) and re.fullmatch(r"[-+0-9.eE]+|on|off", toks[i]) and i < len(toks) - 1:
                i += 1
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def _read_mtl(data: bytes, base: str) -> dict:
    out: dict[str, Material] = {}
    cur = None
    for line in data.decode("utf-8", "replace").splitlines():
        p = line.strip().split(None, 1)
        if not p:
            continue
        key = p[0].lower()
        if key == "newmtl" and len(p) > 1:
            cur = out.setdefault(p[1].strip(), Material(name=p[1].strip()))
        elif cur is None or len(p) < 2:
            continue
        elif key in ("kd", "ke"):
            try:
                c = tuple(max(0.0, min(1.0, float(t))) for t in p[1].split()[:3])
            except ValueError:
                continue
            if key == "kd":
                cur.color = tuple(x ** (1 / 2.2) for x in c) + (cur.color[3],)
            else:
                cur.emissive = c
        elif key == "d":
            try:
                cur.color = cur.color[:3] + (float(p[1].split()[0]),)
            except ValueError:
                pass
        elif key in _MTL_ROLES:
            name = _mtl_file(p[1])
            role = _MTL_ROLES[key]
            if role == "normal" and key in ("bump", "map_bump") and not re.search(r"norm|nrm|nml", name, re.I):
                role = "height"
            if name and role not in cur.maps:
                cur.maps[role] = TexRef(name, resolve_texture(base, name))
    return out


# ---------------------------------------------------------------- PLY
_PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
              "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
              "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def read_ply(data: bytes, path: str) -> Mesh:
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        raise MeshError("not a PLY file")
    header = data[:end].decode("ascii", "replace").splitlines()
    body = data[data.index(b"\n", end) + 1:]
    fmt = "ascii"
    elements: list[dict] = []
    for ln in header:
        p = ln.split()
        if not p:
            continue
        if p[0] == "format":
            fmt = p[1]
        elif p[0] == "element":
            elements.append({"name": p[1], "count": int(p[2]), "props": []})
        elif p[0] == "property" and elements:
            if p[1] == "list":
                elements[-1]["props"].append(("list", p[4], _PLY_TYPES[p[2]], _PLY_TYPES[p[3]]))
            else:
                elements[-1]["props"].append(("scalar", p[2], _PLY_TYPES[p[1]]))
    v = f = colors = None
    vcol = None
    if fmt == "ascii":
        tokens = body.split()
        pos = 0
        for el in elements:
            if el["name"] == "vertex" and all(pr[0] == "scalar" for pr in el["props"]):
                k = len(el["props"])
                arr = np.array(tokens[pos:pos + k * el["count"]], dtype=np.float64).reshape(-1, k)
                pos += k * el["count"]
                names = [pr[1] for pr in el["props"]]
                v = arr[:, [names.index("x"), names.index("y"), names.index("z")]].astype(np.float32)
                if {"red", "green", "blue"} <= set(names):
                    vcol = arr[:, [names.index("red"), names.index("green"), names.index("blue")]]
            elif el["name"] == "face":
                counts, idx = [], []
                for _ in range(el["count"]):
                    for pr in el["props"]:
                        if pr[0] == "list":
                            n = int(tokens[pos]); pos += 1
                            vals = [int(t) for t in tokens[pos:pos + n]]; pos += n
                            if pr[1] in ("vertex_indices", "vertex_index"):
                                counts.append(n); idx.extend(vals)
                        else:
                            pos += 1
                f = _fan(np.array(counts), np.array(idx))
            else:
                for _ in range(el["count"]):
                    for pr in el["props"]:
                        if pr[0] == "list":
                            n = int(tokens[pos]); pos += 1 + n
                        else:
                            pos += 1
    else:
        endian = "<" if "little" in fmt else ">"
        off = 0
        for el in elements:
            scalars = all(pr[0] == "scalar" for pr in el["props"])
            if scalars:
                dt = np.dtype([(pr[1], endian + pr[2]) for pr in el["props"]])
                arr = np.frombuffer(body, dt, el["count"], off)
                off += dt.itemsize * el["count"]
                if el["name"] == "vertex":
                    v = np.stack([arr["x"], arr["y"], arr["z"]], 1).astype(np.float32)
                    if all(c in arr.dtype.names for c in ("red", "green", "blue")):
                        vcol = np.stack([arr["red"], arr["green"], arr["blue"]], 1)
                continue
            # lists: fast path when every face is a triangle and the list is the only property
            if len(el["props"]) == 1 and el["props"][0][0] == "list":
                _, name, ct, it = el["props"][0]
                dt = np.dtype([("n", endian + ct), ("i", endian + it, 3)])
                try:
                    arr = np.frombuffer(body, dt, el["count"], off)
                    if (arr["n"] == 3).all():
                        off += dt.itemsize * el["count"]
                        if el["name"] == "face":
                            f = arr["i"].astype(np.int64)
                        continue
                except ValueError:
                    pass
            counts, idx = [], []
            for _ in range(el["count"]):
                for pr in el["props"]:
                    if pr[0] == "list":
                        cs = np.dtype(endian + pr[2]).itemsize
                        n = int(np.frombuffer(body, endian + pr[2], 1, off)[0]); off += cs
                        isz = np.dtype(endian + pr[3]).itemsize
                        vals = np.frombuffer(body, endian + pr[3], n, off); off += isz * n
                        if pr[1] in ("vertex_indices", "vertex_index"):
                            counts.append(n); idx.extend(vals.tolist())
                    else:
                        off += np.dtype(pr[2]).itemsize
            if el["name"] == "face":
                f = _fan(np.array(counts), np.array(idx))
    if v is None or f is None or not len(f):
        raise MeshError("PLY without faces (point clouds are not previewed)")
    f = np.asarray(f, np.int64)
    if vcol is not None:
        vc = np.asarray(vcol, np.float64)
        if vc.max() <= 1.0:
            vc = vc * 255
        colors = vc[f].mean(axis=1).clip(0, 255).astype(np.uint8)
    # scanned data is usually Z up (photogrammetry, CAD exports); a "y up" mesh is far more common in
    # game assets - guess by which axis is flattest at the bottom is unreliable, keep the file's axes
    return Mesh(v, f.astype(np.int32), colors, {"encoding": fmt, "vertices": int(len(v))})


# ---------------------------------------------------------------- OFF
def read_off(data: bytes, path: str) -> Mesh:
    lines = [ln.split(b"#")[0].strip() for ln in data.split(b"\n")]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].endswith(b"OFF"):
        raise MeshError("not an OFF file")
    first = lines[0][3:].split() or lines[1].split()
    start = 1 if lines[0][3:].split() else 2
    nv, nf = int(first[0]), int(first[1])
    v = np.array([ln.split()[:3] for ln in lines[start:start + nv]], dtype=np.float32)
    counts, idx, cols = [], [], []
    for ln in lines[start + nv:start + nv + nf]:
        p = ln.split()
        n = int(p[0])
        counts.append(n)
        idx.extend(int(t) for t in p[1:1 + n])
        if len(p) >= 4 + n:
            c = [float(t) for t in p[1 + n:4 + n]]
            cols.append([x * 255 if max(c) <= 1 else x for x in c])
    f = _fan(np.array(counts), np.array(idx))
    colors = None
    if cols and len(cols) == len(counts):
        colors = np.repeat(np.array(cols), np.array(counts) - 2, axis=0).clip(0, 255).astype(np.uint8)
    return Mesh(v, f.astype(np.int32), colors, {})


# ---------------------------------------------------------------- glTF / GLB
_GL_DTYPE = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_GL_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _quat_mat(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def read_gltf(data: bytes, path: str, full: bool = False) -> Mesh:
    bin_chunk = None
    if data[:4] == b"glTF":
        length = struct.unpack_from("<I", data, 8)[0]
        off = 12
        doc = None
        while off + 8 <= min(length, len(data)):
            clen, ctype = struct.unpack_from("<II", data, off)
            chunk = data[off + 8: off + 8 + clen]
            if ctype == 0x4E4F534A:
                doc = json.loads(chunk)
            elif ctype == 0x004E4942:
                bin_chunk = chunk
            off += 8 + clen + (-clen % 4)
        if doc is None:
            raise MeshError("GLB without JSON")
    else:
        doc = json.loads(data)
    base = os.path.dirname(path)
    buffers: list[bytes | None] = []
    for i, b in enumerate(doc.get("buffers", [])):
        uri = b.get("uri")
        if uri is None:
            buffers.append(bin_chunk)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            from urllib.parse import unquote
            p = os.path.normpath(os.path.join(base, unquote(uri)))
            inside = os.path.commonpath([os.path.abspath(p), os.path.abspath(base)]) == os.path.abspath(base)
            if inside and os.path.isfile(p) and os.path.getsize(p) <= MAX_EXTERNAL:   # only next to the model
                with open(p, "rb") as fh:
                    buffers.append(fh.read())
            else:
                buffers.append(None)
    views = doc.get("bufferViews", [])
    accs = doc.get("accessors", [])

    decoded: dict[int, bytes] = {}

    def view_bytes(vi: int) -> tuple[bytes, int]:
        """(buffer, offset) of a bufferView, decompressing meshopt views on first use."""
        bv = views[vi]
        mo = (bv.get("extensions") or {})
        mo = mo.get("EXT_meshopt_compression") or mo.get("KHR_meshopt_compression")
        if mo is None:
            buf = buffers[bv["buffer"]]
            if buf is None:
                raise MeshError("external .bin file is missing")
            return buf, bv.get("byteOffset", 0)
        if vi not in decoded:
            from .meshopt import MeshoptError, decode
            src = buffers[mo["buffer"]]
            if src is None:
                raise MeshError("external .bin file is missing")
            o = mo.get("byteOffset", 0)
            try:
                decoded[vi] = decode(bytes(src[o: o + mo["byteLength"]]), mo["count"], mo["byteStride"],
                                     mo.get("mode", "ATTRIBUTES"), mo.get("filter", "NONE"))
            except MeshoptError as e:
                raise MeshError(str(e)) from None
        return decoded[vi], 0

    def view_data(vi: int) -> bytes:
        buf, o = view_bytes(vi)
        n = views[vi].get("byteLength", 0) if vi not in decoded else len(decoded[vi])
        return bytes(buf[o: o + n])

    def accessor(i: int) -> np.ndarray:
        a = accs[i]
        if "bufferView" not in a:
            raise MeshError("sparse/empty accessor")
        bv = views[a["bufferView"]]
        buf, voff = view_bytes(a["bufferView"])
        dt = np.dtype(_GL_DTYPE[a["componentType"]])
        n = _GL_N[a["type"]]
        count = a["count"]
        start = voff + a.get("byteOffset", 0)
        stride = bv.get("byteStride") or dt.itemsize * n
        if stride == dt.itemsize * n:
            arr = np.frombuffer(buf, dt, count * n, start).reshape(count, n)
        else:
            raw = np.frombuffer(buf, np.uint8, stride * (count - 1) + dt.itemsize * n, start)
            arr = np.lib.stride_tricks.as_strided(raw, (count, n * dt.itemsize), (stride, 1)).copy().view(dt).reshape(count, n)
        if a.get("normalized") and dt.kind in "iu":
            arr = arr.astype(np.float32) / np.iinfo(dt).max
        return arr

    used_ext = doc.get("extensionsUsed", [])
    from . import native
    compressed = [e for e in used_ext if e == "KHR_draco_mesh_compression" and not native.available("draco")]
    mats = doc.get("materials", [])
    materials = _gltf_materials(doc, buffers, views, mats, base)

    parts = []
    errors: list[str] = []
    meshes = doc.get("meshes", [])

    def add_mesh(mi: int, M: np.ndarray) -> None:
        for prim in meshes[mi].get("primitives", []):
            if prim.get("mode", 4) != 4 or "POSITION" not in prim.get("attributes", {}):
                continue
            if "extensions" in prim and set(prim["extensions"]) & set(compressed):
                continue
            draco = (prim.get("extensions") or {}).get("KHR_draco_mesh_compression")
            try:
                if draco is not None:
                    v, f, uv_d, n_d = _draco_prim(draco, view_data)
                else:
                    v = accessor(prim["attributes"]["POSITION"]).astype(np.float32)[:, :3]
                    f = accessor(prim["indices"]).reshape(-1) if "indices" in prim else np.arange(len(v))
            except MeshError as e:
                if draco is not None:
                    errors.append(str(e))
                continue
            except (KeyError, ValueError, IndexError):
                continue
            f = f[: len(f) // 3 * 3].reshape(-1, 3).astype(np.int32)
            v = v @ M[:3, :3].T.astype(np.float32) + M[:3, 3].astype(np.float32)
            uv = None
            if draco is not None:
                uv = uv_d
            elif "TEXCOORD_0" in prim["attributes"]:
                try:
                    uv = accessor(prim["attributes"]["TEXCOORD_0"]).astype(np.float32)[:, :2]
                    if len(uv) != len(v):
                        uv = None
                except (MeshError, KeyError, ValueError, IndexError):
                    uv = None
            nrm = n_d @ M[:3, :3].T.astype(np.float32) if draco is not None and full and n_d is not None else None
            if nrm is None and draco is None and full and "NORMAL" in prim["attributes"]:
                try:
                    nrm = accessor(prim["attributes"]["NORMAL"]).astype(np.float32)[:, :3] @ M[:3, :3].T.astype(np.float32)
                    if len(nrm) != len(v):
                        nrm = None
                except (MeshError, KeyError, ValueError, IndexError):
                    nrm = None
            mi_ = prim.get("material")
            parts.append((v, f, uv, mi_ if mi_ is not None and mi_ < len(materials) else -1, nrm))

    nodes = doc.get("nodes", [])

    def local(n: dict) -> np.ndarray:
        if "matrix" in n:
            return np.array(n["matrix"], np.float64).reshape(4, 4).T
        M = np.eye(4)
        S = np.diag(n.get("scale", [1, 1, 1]) + [1])
        R = np.eye(4)
        R[:3, :3] = _quat_mat(n.get("rotation", [0, 0, 0, 1]))
        T = np.eye(4)
        T[:3, 3] = n.get("translation", [0, 0, 0])
        return T @ R @ S @ M

    seen = set()

    def walk(i: int, parent: np.ndarray, depth: int = 0) -> None:
        if depth > 64 or i in seen or i >= len(nodes):
            return
        seen.add(i)
        n = nodes[i]
        M = parent @ local(n)
        if "mesh" in n:
            add_mesh(n["mesh"], M)
        for c in n.get("children", []):
            walk(c, M, depth + 1)

    scenes = doc.get("scenes", [])
    roots = scenes[doc.get("scene", 0)].get("nodes", []) if scenes else []
    if not roots and nodes:   # no scene: every node that isn't somebody's child
        kids = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in kids]
    for r in roots:
        walk(r, np.eye(4))
    if not parts:
        for mi in range(len(meshes)):
            add_mesh(mi, np.eye(4))
    asset = doc.get("asset", {})
    info = {k: v for k, v in {
        "generator": asset.get("generator"), "gltf": asset.get("version"),
        "meshes": len(meshes), "materials": len(mats), "textures": len(doc.get("textures", [])),
        "animations": len(doc.get("animations", [])), "skins": len(doc.get("skins", [])),
        "extensions": ", ".join(used_ext) if used_ext else None,
        "images": [im.get("uri") for im in doc.get("images", []) if im.get("uri") and not im["uri"].startswith("data:")] or None,
    }.items() if v not in (None, 0, "")}
    if not parts:
        if compressed:
            raise MeshError("Draco-compressed geometry: install the optional 3D add-on (install.bat 3d / ./install.sh 3d)")
        if errors:
            raise MeshError(errors[0])
        raise MeshError("no triangle geometry")
    vs, fs, uvs, fms, ns, off = [], [], [], [], [], 0
    any_uv = any(p[2] is not None for p in parts)
    all_n = full and all(p[4] is not None for p in parts)
    for v, f, uv, mi_, nrm in parts:
        if not len(v) or not len(f):
            continue
        ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
        f = f[ok]
        vs.append(v)
        fs.append(f + off)
        if any_uv:
            uvs.append(uv if uv is not None else np.zeros((len(v), 2), np.float32))
        fms.append(np.full(len(f), mi_, np.int32))
        if all_n:
            ns.append(nrm)
        off += len(v)
    if not vs:
        raise MeshError("no triangle geometry")
    mesh = Mesh(np.concatenate(vs), np.concatenate(fs).astype(np.int32), None, info,
                uv=np.concatenate(uvs) if any_uv else None, face_mat=np.concatenate(fms), materials=materials,
                normals=np.concatenate(ns) if all_n and ns else None)
    material_colors(mesh)
    return mesh


def _draco_prim(ext: dict, view_data):
    """Positions, triangles, UVs and normals of a Draco-compressed primitive, decoded in a separate
    process (DracoPy is native code)."""
    from . import native
    r = native.run("draco", payload=view_data(ext["bufferView"]))
    return (r["points"].astype(np.float32), r["faces"].astype(np.int64).reshape(-1), r.get("uv"), r.get("normals"))


def _gltf_materials(doc, buffers, views, mats, base: str) -> list:
    """Materials with their texture maps (embedded images or files next to the model)."""
    textures, images = doc.get("textures", []), doc.get("images", [])
    refs: dict[int, TexRef] = {}

    def ref(info) -> TexRef | None:
        t = (info or {}).get("index")
        if t is None or t >= len(textures):
            return None
        tex = textures[t]
        src = tex.get("source")
        for ext in ("KHR_texture_basisu", "EXT_texture_webp", "MSFT_texture_dds"):
            src = (tex.get("extensions") or {}).get(ext, {}).get("source", src) if src is None else src
        if src is None or src >= len(images):
            return None
        if src not in refs:
            im = images[src]
            r = TexRef(im.get("name") or im.get("uri") or f"image {src}")
            try:
                if "bufferView" in im:
                    bv = views[im["bufferView"]]
                    buf = buffers[bv["buffer"]]
                    if buf is not None:
                        o = bv.get("byteOffset", 0)
                        r.data = bytes(buf[o: o + bv["byteLength"]])
                elif im.get("uri", "").startswith("data:"):
                    r.data = base64.b64decode(im["uri"].split(",", 1)[1])
                elif im.get("uri"):
                    r.name = im["uri"]
                    r.path = resolve_texture(base, im["uri"])
            except (KeyError, IndexError, ValueError, TypeError):
                pass
            refs[src] = r
        return refs[src]

    out = []
    for m in mats:
        pbr = m.get("pbrMetallicRoughness") or {}
        f = pbr.get("baseColorFactor", [1, 1, 1, 1])
        mat = Material(name=m.get("name", ""), color=tuple(max(0.0, min(1.0, c)) ** (1 / 2.2) for c in f[:3]) + (float(f[3]) if len(f) > 3 else 1.0,),
                       emissive=tuple(m.get("emissiveFactor", [0, 0, 0])[:3]))
        for role, info in (("base", pbr.get("baseColorTexture")), ("metal_rough", pbr.get("metallicRoughnessTexture")),
                           ("normal", m.get("normalTexture")), ("occlusion", m.get("occlusionTexture")),
                           ("emissive", m.get("emissiveTexture"))):
            r = ref(info)
            if r is not None:
                mat.maps[role] = r
        if "base" not in mat.maps and pbr.get("baseColorFactor") is None:
            mat.color = (0.72, 0.74, 0.78, 1.0)   # no colour at all: the neutral grey of untextured previews
        out.append(mat)
    return out


# ---------------------------------------------------------------- FBX
_FBX_MAGIC = b"Kaydara FBX Binary  \x00"


class _Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def all(self, name):
        return [c for c in self.children if c.name == name]


def _fbx_read_props(data: bytes, off: int, n: int) -> tuple[list, int]:
    props = []
    for _ in range(n):
        t = chr(data[off]); off += 1
        if t in "YCIFDL":
            fmt = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}[t]
            props.append(struct.unpack_from(fmt, data, off)[0]); off += struct.calcsize(fmt)
        elif t in "fdlibc":
            length, enc, clen = struct.unpack_from("<III", data, off); off += 12
            dt = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "<?", "c": "u1"}[t]
            props.append(("array", dt, length, enc, data[off:off + clen])); off += clen
        elif t in "SR":
            ln = struct.unpack_from("<I", data, off)[0]; off += 4
            raw = data[off:off + ln]; off += ln
            props.append(raw.decode("utf-8", "replace") if t == "S" else raw)
        else:
            raise MeshError(f"unknown FBX property type {t!r}")
    return props, off


def _fbx_array(p) -> np.ndarray:
    _, dt, length, enc, raw = p
    if enc == 1:   # never inflate more than the array claims to hold (zip bombs)
        need = length * np.dtype(dt).itemsize
        raw = zlib.decompressobj().decompress(raw, need)
        if len(raw) < need:
            raise MeshError("damaged FBX array")
    return np.frombuffer(raw, dt, length)


def _fbx_parse(data: bytes) -> tuple[list[_Node], int]:
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    hdr = 25 if wide else 13

    def nodes(off: int, end: int, depth: int) -> tuple[list[_Node], int]:
        out = []
        while off + hdr <= end:
            if wide:
                end_off, nprops, _plen = struct.unpack_from("<QQQ", data, off)
            else:
                end_off, nprops, _plen = struct.unpack_from("<III", data, off)
            if end_off == 0:
                return out, off + hdr
            nlen = data[off + hdr - 1]
            name = data[off + hdr: off + hdr + nlen].decode("ascii", "replace")
            p = off + hdr + nlen
            props, p = _fbx_read_props(data, p, nprops)
            children = []
            if p < end_off and depth < 64:
                children, _ = nodes(p, end_off, depth + 1)
            out.append(_Node(name, props, children))
            if end_off <= off:
                raise MeshError("corrupt FBX")
            off = end_off
        return out, off

    top, _ = nodes(27, len(data), 0)
    return top, version


def _euler(deg) -> np.ndarray:
    x, y, z = (math.radians(a) for a in deg)
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def _p70(node: _Node | None) -> dict:
    out = {}
    if node is None:
        return out
    pn = node.find("Properties70") or node.find("Properties60")
    if pn is None:
        return out
    for p in pn.children:
        if p.props and isinstance(p.props[0], str):
            out[p.props[0]] = p.props[4:] if len(p.props) > 4 else p.props[1:]
    return out


def read_fbx(data: bytes, path: str, full: bool = False) -> Mesh:
    if not data.startswith(_FBX_MAGIC):
        return _read_fbx_ascii(data)
    top, version = _fbx_parse(data)
    root = {n.name: n for n in top}
    info: dict = {"fbx": f"{version // 1000}.{version % 1000 // 100}"}
    dates: dict = {}
    hx = root.get("FBXHeaderExtension")
    if hx:
        cts = hx.find("CreationTimeStamp")
        if cts:
            vals = {c.name: c.props[0] for c in cts.children if c.props}
            try:
                dates["document"] = datetime(vals["Year"], vals["Month"], vals["Day"], vals.get("Hour", 0),
                                             vals.get("Minute", 0), vals.get("Second", 0)).timestamp()
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
    creator = root.get("Creator")
    if creator and creator.props:
        info["creator"] = creator.props[0]
    gs = _p70(root.get("GlobalSettings"))
    up = int(gs.get("UpAxis", [1])[0]) if gs.get("UpAxis") else 1
    unit = float(gs.get("UnitScaleFactor", [1.0])[0]) if gs.get("UnitScaleFactor") else 1.0
    info["unit"] = {1.0: "cm", 100.0: "m", 0.1: "mm", 2.54: "inch"}.get(round(unit, 4), f"{unit:g} cm")
    objs = root.get("Objects")
    if objs is None:
        raise MeshError("FBX without objects")
    geoms, models, mat_nodes, tex_nodes, videos, anims = {}, {}, {}, {}, {}, 0
    for o in objs.children:
        oid = o.props[0] if o.props else None
        if o.name == "Geometry":
            geoms[oid] = o
        elif o.name == "Model":
            models[oid] = o
        elif o.name == "Material":
            mat_nodes[oid] = o
        elif o.name == "Texture":
            tex_nodes[oid] = o
        elif o.name == "Video":
            videos[oid] = o
        elif o.name == "AnimationStack":
            anims += 1
    parent: dict = {}
    geom_model: dict = {}
    model_mats: dict = {}       # model -> [material ids] in connection order (= the material index)
    mat_tex: dict = {}          # material -> [(property, texture id)]
    tex_video: dict = {}
    conns = root.get("Connections")
    for c in (conns.children if conns else []):
        if len(c.props) < 3:
            continue
        child, par = c.props[1], c.props[2]
        if c.props[0] == "OO":
            if child in geoms and par in models:
                geom_model.setdefault(child, par)
            elif child in models:
                parent[child] = par
            elif child in mat_nodes and par in models:
                model_mats.setdefault(par, []).append(child)
            elif child in videos and par in tex_nodes:
                tex_video[par] = child
            elif child in tex_nodes and par in mat_nodes:
                mat_tex.setdefault(par, []).append(("", child))
        elif c.props[0] == "OP" and child in tex_nodes and par in mat_nodes:
            mat_tex.setdefault(par, []).append((str(c.props[3]) if len(c.props) > 3 else "", child))

    base = os.path.dirname(path)
    textures = []
    for t in tex_nodes.values():   # every texture the file names (export takes them along), used or not
        fn = t.find("RelativeFilename") or t.find("FileName")
        if fn and fn.props and fn.props[0]:
            textures.append(fn.props[0].replace("\\", "/").rsplit("/", 1)[-1])

    def texref(tid) -> TexRef | None:
        t = tex_nodes.get(tid)
        if t is None:
            return None
        fn = t.find("RelativeFilename") or t.find("FileName")
        name = fn.props[0] if fn and fn.props and fn.props[0] else ""
        vid = videos.get(tex_video.get(tid))
        data_ = None
        if vid is not None:
            content = vid.find("Content")
            if content and content.props and isinstance(content.props[0], bytes) and len(content.props[0]) > 16:
                data_ = content.props[0]
            if not name:
                fn = vid.find("RelativeFilename") or vid.find("Filename")
                name = fn.props[0] if fn and fn.props else ""
        return TexRef(name or "embedded", None if data_ else resolve_texture(base, name), data_)

    mat_index: dict = {}
    materials: list[Material] = []

    def material(mid) -> int:
        if mid in mat_index:
            return mat_index[mid]
        node = mat_nodes[mid]
        p = _p70(node)
        m = Material(name=_fbx_name(node))
        dc = p.get("DiffuseColor") or p.get("Diffuse")
        if dc and len(dc) >= 3:
            try:
                m.color = tuple(max(0.0, min(1.0, float(x))) ** (1 / 2.2) for x in dc[:3]) + (1.0,)
            except (TypeError, ValueError):
                pass
        for prop, tid in mat_tex.get(mid, []):
            role = _FBX_ROLES.get(prop.split("|")[-1].lower().replace(" ", ""), "base" if not prop else None)
            r = texref(tid)
            if role is None or r is None:
                continue
            if role == "normal" and "bump" in prop.lower() and not re.search(r"norm|nrm|nml", r.name, re.I):
                role = "height"
            m.maps.setdefault(role, r)
        if "base" in m.maps and not (dc and len(dc) >= 3):
            m.color = (1.0, 1.0, 1.0, 1.0)
        mat_index[mid] = len(materials)
        materials.append(m)
        return mat_index[mid]

    def local(mid) -> np.ndarray:
        p = _p70(models.get(mid))
        M = np.eye(4)
        t = [float(x) for x in p.get("Lcl Translation", [0, 0, 0])[:3]]
        r = [float(x) for x in p.get("Lcl Rotation", [0, 0, 0])[:3]]
        pre = [float(x) for x in p.get("PreRotation", [0, 0, 0])[:3]]
        sc = [float(x) for x in p.get("Lcl Scaling", [1, 1, 1])[:3]]
        M[:3, :3] = _euler(pre) @ _euler(r) @ np.diag(sc)
        M[:3, 3] = t
        return M

    def world(mid, depth=0) -> np.ndarray:
        if mid not in models or depth > 64:
            return np.eye(4)
        return world(parent.get(mid), depth + 1) @ local(mid)

    vs, fs, uvs, fms, nrms, off = [], [], [], [], [], 0
    any_uv = False
    for gid, g in geoms.items():
        vn, pn = g.find("Vertices"), g.find("PolygonVertexIndex")
        if vn is None or pn is None or not vn.props or not pn.props:
            continue
        v = _fbx_array(vn.props[0]).astype(np.float64).reshape(-1, 3)
        pvi = _fbx_array(pn.props[0]).astype(np.int64)
        ends = pvi < 0
        idx = np.where(ends, ~pvi, pvi)
        end_pos = np.nonzero(ends)[0]
        if not len(end_pos):
            continue
        counts = np.diff(np.r_[-1, end_pos])
        corners = _fan(counts, np.arange(end_pos[-1] + 1))
        f = idx[corners]
        ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
        mid_model = geom_model.get(gid)
        M = world(mid_model)
        v = v @ M[:3, :3].T + M[:3, 3]
        uv = _fbx_uv(g, idx, corners, len(v))
        nrm = _fbx_layer(g, "LayerElementNormal", "Normals", "NormalsIndex", 3, idx, corners, len(v)) if full else None
        if nrm is not None:
            nrm = nrm @ M[:3, :3].T
        mlist = [material(m) for m in model_mats.get(mid_model, []) if m in mat_nodes]
        fm = np.full(len(f), mlist[0] if mlist else -1, np.int32)
        lm = g.find("LayerElementMaterial")
        if lm is not None and mlist:
            mn = lm.find("Materials")
            mapping = (lm.find("MappingInformationType").props[0] if lm.find("MappingInformationType") else "")
            if mn is not None and mn.props and mapping == "ByPolygon":
                per_poly = _fbx_array(mn.props[0]).astype(np.int64)
                if len(per_poly) >= len(counts):
                    pm = np.repeat(per_poly[:len(counts)], np.maximum(counts - 2, 0))
                    lut = np.array(mlist + [-1], np.int32)
                    fm = lut[np.clip(pm, -1, len(mlist) - 1)]
        vs.append(v.astype(np.float32))
        fs.append(f[ok] + off)
        fms.append(fm[ok])
        uvs.append(uv[ok] if uv is not None else None)
        nrms.append(nrm[ok].astype(np.float32) if nrm is not None else None)
        any_uv |= uv is not None
        off += len(v)
    if not vs:
        raise MeshError("no mesh geometry in FBX")
    v = np.concatenate(vs)
    f = np.concatenate(fs).astype(np.int32)
    corner_uv = None
    if any_uv:
        corner_uv = np.concatenate([u if u is not None else np.zeros((len(ff), 3, 2), np.float32) for u, ff in zip(uvs, fs)])
    corner_n = np.concatenate(nrms) if nrms and all(n is not None for n in nrms) else None
    if up == 2:
        v = z_up_to_y_up(v)
        if corner_n is not None:
            corner_n = z_up_to_y_up(corner_n.reshape(-1, 3)).reshape(-1, 3, 3)
    info.update({"meshes": len(vs), "models": len(models), "materials": len(mat_nodes), "animations": anims})
    if textures:
        info["textures"] = sorted(set(textures))
    mesh = Mesh(v, f, None, {k: x for k, x in info.items() if x not in (0, None, "")}, dates,
                corner_uv=corner_uv, face_mat=np.concatenate(fms) if materials else None, materials=materials,
                corner_normals=corner_n)
    material_colors(mesh)
    return mesh


_FBX_ROLES = {"diffusecolor": "base", "diffuse": "base", "basecolor": "base", "color": "base", "maya|basecolor": "base",
              "normalmap": "normal", "bump": "normal", "normalcamera": "normal", "normal": "normal",
              "emissivecolor": "emissive", "emissive": "emissive", "emissionColor".lower(): "emissive",
              "specularcolor": "specular", "specularfactor": "specular", "specular": "specular",
              "shininessexponent": "roughness", "specularroughness": "roughness", "roughness": "roughness",
              "reflectionfactor": "metallic", "metalness": "metallic", "metallic": "metallic",
              "transparentcolor": "opacity", "transparencyfactor": "opacity", "opacity": "opacity",
              "ambientcolor": "occlusion", "ambientocclusion": "occlusion", "displacementcolor": "height"}


def _fbx_name(node) -> str:
    n = node.props[1] if len(node.props) > 1 and isinstance(node.props[1], str) else ""
    return n.split("\x00\x01")[0].split("::")[-1]


def _fbx_uv(g, idx: np.ndarray, corners: np.ndarray, nverts: int) -> np.ndarray | None:
    """Texture coordinates of each triangle corner (glTF convention, v down), or None."""
    uv = _fbx_layer(g, "LayerElementUV", "UV", "UVIndex", 2, idx, corners, nverts)
    if uv is not None:
        uv[..., 1] = 1 - uv[..., 1]
    return uv


def _fbx_layer(g, layer: str, values: str, index: str, n: int, idx: np.ndarray, corners: np.ndarray, nverts: int):
    """Per-corner values of a geometry layer (UVs, normals), shape (triangles, 3, n), or None."""
    le = g.find(layer)
    if le is None:
        return None
    un, ui = le.find(values), le.find(index)
    mt = le.find("MappingInformationType")
    rt = le.find("ReferenceInformationType")
    if un is None or not un.props:
        return None
    mapping = mt.props[0] if mt and mt.props else "ByPolygonVertex"
    ref = rt.props[0] if rt and rt.props else "Direct"
    uv = _fbx_array(un.props[0]).astype(np.float32)
    uv = uv[: len(uv) // n * n].reshape(-1, n).copy()
    if ref in ("IndexToDirect", "Index") and ui is not None and ui.props:
        uidx = _fbx_array(ui.props[0]).astype(np.int64)
    else:
        uidx = None
    if mapping == "ByPolygonVertex":
        k = uidx[corners] if uidx is not None and len(uidx) > corners.max() else corners
    elif mapping in ("ByVertice", "ByVertex", "ByControlPoint"):
        k = idx[corners]
        if uidx is not None and len(uidx) >= nverts:
            k = uidx[k]
    else:
        return None
    k = np.clip(k, 0, len(uv) - 1) if len(uv) else None
    return uv[k] if k is not None else None


def _read_fbx_ascii(data: bytes) -> Mesh:
    text = data.decode("utf-8", "replace")
    if "FBX" not in text[:2000]:
        raise MeshError("not an FBX file")
    vs = re.findall(r"Vertices:\s*\*\d+\s*\{\s*a:\s*([^}]*)\}", text)
    ps = re.findall(r"PolygonVertexIndex:\s*\*\d+\s*\{\s*a:\s*([^}]*)\}", text)
    parts = []
    for vt, pt in zip(vs, ps):
        v = np.array(vt.replace("\n", "").split(","), dtype=np.float64).reshape(-1, 3)
        pvi = np.array(pt.replace("\n", "").split(","), dtype=np.int64)
        ends = pvi < 0
        idx = np.where(ends, ~pvi, pvi)
        counts = np.diff(np.r_[-1, np.nonzero(ends)[0]])
        parts.append((v.astype(np.float32), _fan(counts, idx), None))
    if not parts:
        raise MeshError("no mesh geometry in FBX")
    v, f, c = merge(parts)
    m = re.search(r'"UpAxis",\s*"int",\s*"Integer",\s*"",\s*(\d)', text)
    if m and m.group(1) == "2":
        v = z_up_to_y_up(v)
    return Mesh(v, f, c, {"encoding": "ASCII", "meshes": len(parts)})


# ---------------------------------------------------------------- COLLADA
def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def read_dae(data: bytes, path: str) -> Mesh:
    import xml.etree.ElementTree as ET
    _no_dtd(data)
    root = _strip_ns(ET.fromstring(data))
    up = (root.findtext("asset/up_axis") or "Y_UP").strip()
    info = {"up_axis": up}
    tool = root.findtext("asset/contributor/authoring_tool")
    if tool:
        info["generator"] = tool.strip()
    created = root.findtext("asset/created")
    dates = {}
    if created:
        try:
            dates["document"] = datetime.fromisoformat(created.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    geoms = {}
    for g in root.iter("geometry"):
        mesh = g.find("mesh")
        if mesh is None:
            continue
        sources = {s.get("id"): s for s in mesh.findall("source")}
        verts = mesh.find("vertices")
        pos = None
        if verts is not None:
            for inp in verts.findall("input"):
                if inp.get("semantic") == "POSITION":
                    src = sources.get(inp.get("source", "").lstrip("#"))
                    if src is not None and src.find("float_array") is not None:
                        pos = np.array((src.find("float_array").text or "").split(), dtype=np.float64).reshape(-1, 3)
        if pos is None:
            continue
        faces = []
        for prim in list(mesh):
            if prim.tag not in ("triangles", "polylist", "polygons"):
                continue
            inputs = prim.findall("input")
            if not inputs:
                continue
            stride = max(int(i.get("offset", 0)) for i in inputs) + 1
            voff = next((int(i.get("offset", 0)) for i in inputs if i.get("semantic") == "VERTEX"), 0)
            if prim.tag == "polygons":
                for p in prim.findall("p"):
                    a = np.array((p.text or "").split(), dtype=np.int64)[voff::stride]
                    faces.append(_fan(np.array([len(a)]), a))
                continue
            p = prim.find("p")
            if p is None or not (p.text or "").strip():
                continue
            a = np.array(p.text.split(), dtype=np.int64)[voff::stride]
            if prim.tag == "triangles":
                faces.append(a[: len(a) // 3 * 3].reshape(-1, 3))
            else:
                vc = np.array((prim.findtext("vcount") or "").split(), dtype=np.int64)
                faces.append(_fan(vc, a))
        if faces:
            geoms[g.get("id")] = (pos, np.concatenate(faces))
    parts = []

    def node_matrix(n) -> np.ndarray:
        M = np.eye(4)
        for t in list(n):
            vals = np.array((t.text or "").split(), dtype=np.float64)
            T = np.eye(4)
            if t.tag == "matrix" and len(vals) == 16:
                T = vals.reshape(4, 4)
            elif t.tag == "translate" and len(vals) == 3:
                T[:3, 3] = vals
            elif t.tag == "scale" and len(vals) == 3:
                T = np.diag(list(vals) + [1])
            elif t.tag == "rotate" and len(vals) == 4:
                ax = vals[:3] / (np.linalg.norm(vals[:3]) or 1)
                a = math.radians(vals[3])
                K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
                T[:3, :3] = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K
            else:
                continue
            M = M @ T
        return M

    def walk(n, P, depth=0):
        if depth > 64:
            return
        M = P @ node_matrix(n)
        for ig in n.findall("instance_geometry"):
            g = geoms.get(ig.get("url", "").lstrip("#"))
            if g:
                v = g[0] @ M[:3, :3].T + M[:3, 3]
                parts.append((v.astype(np.float32), g[1], None))
        for c in n.findall("node"):
            walk(c, M, depth + 1)

    for vs in root.iter("visual_scene"):
        for n in vs.findall("node"):
            walk(n, np.eye(4))
    if not parts:
        parts = [(g[0].astype(np.float32), g[1], None) for g in geoms.values()]
    v, f, c = merge(parts)
    if up == "Z_UP":
        v = z_up_to_y_up(v)
    elif up == "X_UP":
        v = v[:, [1, 0, 2]] * np.array([-1, 1, 1], np.float32)
    info["meshes"] = len(geoms)
    return Mesh(v, f, c, info, dates)


# ---------------------------------------------------------------- 3MF
_3MF_ATTR = re.compile(rb'(\w+)="([^"]*)"')


def threemf_thumbnail(data: bytes):
    """3MF files from slicers and CAD usually carry a rendered thumbnail."""
    from PIL import Image
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    with z:
        names = {n.lower(): n for n in z.namelist()}
        cands = [n for key, n in names.items() if key.startswith("metadata/") and key.endswith((".png", ".jpg", ".jpeg"))]
        cands.sort(key=lambda n: (0 if "thumbnail" in n.lower() and "small" not in n.lower() else 1, -z.getinfo(n).file_size))
        for n in cands:
            try:
                img = Image.open(io.BytesIO(z.read(n)))
                img.load()
                return img
            except Exception:
                continue
    return None


def read_3mf(data: bytes, path: str) -> Mesh:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise MeshError("not a 3MF (zip) file") from e
    with z:
        models = [n for n in z.namelist() if n.lower().endswith(".model")]
        if not models:
            raise MeshError("3MF without a model")
        texts = {}
        for n in models:
            if z.getinfo(n).file_size > MAX_EXTERNAL:
                continue
            texts["/" + n.lstrip("/")] = z.read(n)
    main = next((k for k in texts if k.lower() == "/3d/3dmodel.model"), next(iter(texts)))
    info: dict = {}
    dates: dict = {}
    objects: dict = {}
    for name, txt in texts.items():
        _no_dtd(txt)
        if name == main:
            m = re.search(rb'<model[^>]*\sunit="(\w+)"', txt)
            info["unit"] = (m.group(1).decode() if m else "millimeter")
            for key, val in re.findall(rb'<metadata\s+name="([^"]+)"[^>]*>([^<]*)</metadata>', txt):
                k = key.decode("utf-8", "replace").split(":")[-1]
                v = val.decode("utf-8", "replace").strip()
                if k in ("Application", "Title", "Designer", "Copyright", "LicenseTerms") and v:
                    info[k.lower()] = v
                elif k == "CreationDate" and v:
                    try:
                        dates["document"] = datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        pass
        for om in re.finditer(rb"<object\s([^>]*)>(.*?)</object>", txt, re.S):
            attrs = dict(_3MF_ATTR.findall(om.group(1)))
            oid = (name, attrs.get(b"id", b"").decode())
            body = om.group(2)
            vb = re.search(rb"<vertices>(.*?)</vertices>", body, re.S)
            tb = re.search(rb"<triangles>(.*?)</triangles>", body, re.S)
            comps = []
            for cm in re.finditer(rb"<component\s([^>]*?)/?>", body):
                ca = dict((k.split(b":")[-1], v) for k, v in _3MF_ATTR.findall(cm.group(1)))
                comps.append(((ca.get(b"path", name.encode()).decode() or name), ca.get(b"objectid", b"").decode(),
                              _3mf_matrix(ca.get(b"transform"))))
            if vb and tb:
                v = np.array(re.findall(rb'x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"', vb.group(1)), dtype=np.float64)
                f = np.array(re.findall(rb'v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"', tb.group(1)), dtype=np.int64)
                objects[oid] = ("mesh", v.reshape(-1, 3), f.reshape(-1, 3))
            elif comps:
                objects[oid] = ("comps", comps, None)
    parts = []

    def place(oid, M, depth=0):
        o = objects.get(oid)
        if o is None or depth > 32:
            return
        if o[0] == "mesh":
            parts.append(((o[1] @ M[:3, :3].T + M[:3, 3]).astype(np.float32), o[2], None))
        else:
            for p, cid, T in o[1]:
                place(("/" + p.lstrip("/"), cid), M @ T, depth + 1)

    mtxt = texts[main]
    items = re.findall(rb"<item\s([^>]*?)/?>", mtxt)
    for it in items:
        a = dict((k.split(b":")[-1], v) for k, v in _3MF_ATTR.findall(it))
        place((("/" + a[b"path"].decode().lstrip("/")) if b"path" in a else main, a.get(b"objectid", b"").decode()),
              _3mf_matrix(a.get(b"transform")))
    if not parts:
        for k, o in objects.items():
            if o[0] == "mesh":
                place(k, np.eye(4))
    v, f, c = merge(parts)
    info["objects"] = len(items) or len(objects)
    return Mesh(z_up_to_y_up(v), f, c, info, dates)


def _3mf_matrix(s: bytes | None) -> np.ndarray:
    M = np.eye(4)
    if s:
        vals = [float(x) for x in s.split()]
        if len(vals) == 12:
            M[:3, :3] = np.array(vals[:9]).reshape(3, 3).T
            M[:3, 3] = vals[9:]
    return M


# ---------------------------------------------------------------- 3DS
def read_3ds(data: bytes, path: str) -> Mesh:
    if len(data) < 6 or struct.unpack_from("<H", data, 0)[0] != 0x4D4D:
        raise MeshError("not a 3DS file")
    parts, names, textures = [], [], []
    mats = 0

    def walk(off: int, end: int, depth: int, cur: dict | None):
        nonlocal mats
        while off + 6 <= end and depth < 16:
            cid, ln = struct.unpack_from("<HI", data, off)
            if ln < 6 or off + ln > end:
                break
            body, stop = off + 6, off + ln
            if cid in (0x4D4D, 0x3D3D, 0x4100, 0xAFFF, 0xA200):
                if cid == 0xAFFF:
                    mats += 1
                walk(body, stop, depth + 1, cur)
            elif cid == 0x4000:
                z = data.index(b"\0", body, stop)
                names.append(data[body:z].decode("latin-1"))
                obj = {"v": None, "f": None}
                walk(z + 1, stop, depth + 1, obj)
                if obj["v"] is not None and obj["f"] is not None:
                    parts.append((obj["v"], obj["f"], None))
            elif cid == 0x4110 and cur is not None:
                n = struct.unpack_from("<H", data, body)[0]
                cur["v"] = np.frombuffer(data, "<f4", n * 3, body + 2).reshape(-1, 3)
            elif cid == 0x4120 and cur is not None:
                n = struct.unpack_from("<H", data, body)[0]
                cur["f"] = np.frombuffer(data, "<u2", n * 4, body + 2).reshape(-1, 4)[:, :3].astype(np.int64)
            elif cid == 0xA300:
                z = data.find(b"\0", body, stop)
                textures.append(data[body:z if z > 0 else stop].decode("latin-1"))
            off = stop

    walk(0, len(data), 0, None)
    v, f, c = merge(parts)
    info = {"objects": len(names), "materials": mats}
    if textures:
        info["textures"] = sorted(set(textures))
    return Mesh(z_up_to_y_up(v), f, c, info)


# ---------------------------------------------------------------- USD (ASCII)
def read_usd(data: bytes, path: str) -> Mesh:
    """Any USD (text, binary crate, USDZ): through Pixar's library when the 3D add-on is installed,
    else the built-in reader for text USD."""
    from . import native
    if native.available("usd") and os.path.isfile(path):
        return _usd_native(path)
    return read_usda(data, path)


def _usdz_member(package: str, inner: str) -> bytes | None:
    try:
        with zipfile.ZipFile(package) as z:
            info = z.getinfo(inner)
            if info.file_size > 256 * 1024 * 1024:
                return None
            return z.read(inner)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def _usd_native(path: str) -> Mesh:
    from . import native
    r = native.run("usd", path=path)
    info = r.get("info", {})
    base = os.path.dirname(path)
    materials = []
    for m in info.pop("materials_list", []):
        mat = Material(name=m.get("name", ""), color=tuple(m.get("color", (0.72, 0.74, 0.78, 1.0))))
        for role, t in (m.get("maps") or {}).items():
            resolved, name = t.get("resolved") or "", t.get("name") or ""
            ref = TexRef(name)
            if "[" in resolved and resolved.endswith("]"):   # inside a .usdz package
                pkg, inner = resolved[:-1].split("[", 1)
                ref.data = _usdz_member(pkg, inner)
            elif resolved and os.path.isfile(resolved):
                ref.path = resolved
            else:
                ref.path = resolve_texture(base, name)
            mat.maps[role] = ref
        if "base" in mat.maps and mat.maps["base"].found:
            mat.color = (1.0, 1.0, 1.0, 1.0)
        materials.append(mat)
    fm = r.get("face_mat")
    mesh = Mesh(r["points"].astype(np.float32), r["faces"].astype(np.int32), None,
                {k: v for k, v in info.items() if v not in (None, 0, "")}, corner_uv=r.get("corner_uv"),
                face_mat=fm.astype(np.int32) if fm is not None and materials else None, materials=materials)
    material_colors(mesh)
    return mesh


def read_usda(data: bytes, path: str) -> Mesh:
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise MeshError("bad USDZ") from e
        with z:
            name = next((n for n in z.namelist() if n.lower().endswith(".usda")), None)
            if not name:
                raise MeshError("USDZ with binary USD (.usdc): install the optional 3D add-on (install.bat 3d)")
            data = z.read(name)
    if data[:8] == b"PXR-USDC":
        raise MeshError("binary USD (.usdc): install the optional 3D add-on (install.bat 3d)")
    text = data.decode("utf-8", "replace")
    up = re.search(r'upAxis\s*=\s*"(\w)"', text)
    parts = []
    for m in re.finditer(r"def\s+Mesh\s+\"[^\"]*\"[^{]*\{(.*?)\n\s*\}", text, re.S):
        body = m.group(1)
        pts = re.search(r"points\s*=\s*\[(.*?)\]", body, re.S)
        cnt = re.search(r"faceVertexCounts\s*=\s*\[(.*?)\]", body, re.S)
        idx = re.search(r"faceVertexIndices\s*=\s*\[(.*?)\]", body, re.S)
        if not (pts and cnt and idx):
            continue
        v = np.array(re.findall(r"[-+0-9.eE]+", pts.group(1)), dtype=np.float32).reshape(-1, 3)
        f = _fan(np.array(cnt.group(1).replace(",", " ").split(), dtype=np.int64),
                 np.array(idx.group(1).replace(",", " ").split(), dtype=np.int64))
        parts.append((v, f, None))
    v, f, c = merge(parts)
    if up and up.group(1) == "Z":
        v = z_up_to_y_up(v)
    return Mesh(v, f, c, {"meshes": len(parts), "up_axis": up.group(1) if up else "Y"})


# ---------------------------------------------------------------- MagicaVoxel
def read_vox(data: bytes, path: str) -> Mesh:
    if data[:4] != b"VOX ":
        raise MeshError("not a VOX file")
    models: list[tuple] = []
    size = None
    palette = None
    nodes: dict = {}
    off = 8

    def read_dict(o):
        n = struct.unpack_from("<i", data, o)[0]; o += 4
        d = {}
        for _ in range(n):
            ln = struct.unpack_from("<i", data, o)[0]; o += 4
            k = data[o:o + ln].decode("utf-8", "replace"); o += ln
            ln = struct.unpack_from("<i", data, o)[0]; o += 4
            d[k] = data[o:o + ln].decode("utf-8", "replace"); o += ln
        return d, o

    while off + 12 <= len(data):
        cid = data[off:off + 4]
        csize, _children = struct.unpack_from("<ii", data, off + 4)
        body = off + 12
        if cid == b"SIZE":
            size = struct.unpack_from("<iii", data, body)
        elif cid == b"XYZI":
            n = struct.unpack_from("<i", data, body)[0]
            xyzi = np.frombuffer(data, np.uint8, n * 4, body + 4).reshape(-1, 4)
            models.append((size, xyzi))
        elif cid == b"RGBA":
            palette = np.frombuffer(data, np.uint8, 1024, body).reshape(256, 4)[:, :3]
        elif cid in (b"nTRN", b"nGRP", b"nSHP"):
            try:
                nid = struct.unpack_from("<i", data, body)[0]
                _, o = read_dict(body + 4)
                if cid == b"nTRN":
                    child = struct.unpack_from("<i", data, o)[0]
                    nframes = struct.unpack_from("<i", data, o + 12)[0]
                    t = (0, 0, 0)
                    if nframes:
                        fr, _ = read_dict(o + 16)
                        if "_t" in fr:
                            t = tuple(int(x) for x in fr["_t"].split())
                    nodes[nid] = ("T", child, t)
                elif cid == b"nGRP":
                    n = struct.unpack_from("<i", data, o)[0]
                    nodes[nid] = ("G", list(struct.unpack_from(f"<{n}i", data, o + 4)), None)
                else:
                    n = struct.unpack_from("<i", data, o)[0]
                    mids = []
                    o += 4
                    for _ in range(n):
                        mids.append(struct.unpack_from("<i", data, o)[0])
                        _, o = read_dict(o + 4)
                    nodes[nid] = ("S", mids, None)
            except struct.error:
                pass
        off = body + csize   # children (only MAIN has any) follow directly: read them in sequence
    if not models:
        raise MeshError("no voxels")
    pal = palette if palette is not None else np.tile(np.array([[200, 200, 200]], np.uint8), (256, 1))
    placed: list[tuple[int, tuple]] = []

    def walk(nid, t, depth=0):
        node = nodes.get(nid)
        if node is None or depth > 64:
            return
        if node[0] == "T":
            walk(node[1], tuple(a + b for a, b in zip(t, node[2])), depth + 1)
        elif node[0] == "G":
            for c in node[1]:
                walk(c, t, depth + 1)
        else:
            for m in node[1]:
                placed.append((m, t))

    if nodes:
        walk(0, (0, 0, 0))
    if not placed:
        placed = [(0, (0, 0, 0))]
    parts = []
    total = 0
    for mi, t in placed:
        if mi >= len(models):
            continue
        sz, xyzi = models[mi]
        total += len(xyzi)
        v, f, c = _voxel_faces(xyzi, sz, pal)
        if len(f):
            shift = np.array(t, np.float32) - np.floor(np.array(sz, np.float32) / 2)
            parts.append((v + shift, f, c))
    v, f, c = merge(parts)
    return Mesh(z_up_to_y_up(v), f, c, {"models": len(models), "voxels": total})


def _voxel_faces(xyzi: np.ndarray, size, pal) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sx, sy, sz = (int(s) for s in size)
    occ = np.zeros((sx + 2, sy + 2, sz + 2), bool)
    x, y, z = (xyzi[:, k].astype(np.int64) for k in range(3))
    ok = (x < sx) & (y < sy) & (z < sz)
    x, y, z, ci = x[ok], y[ok], z[ok], xyzi[ok, 3].astype(np.int64)
    occ[x + 1, y + 1, z + 1] = True
    col = pal[np.clip(ci - 1, 0, 255)]
    corners = {  # (normal offset, 4 corners of the face)
        (1, 0, 0): [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
        (-1, 0, 0): [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
        (0, 1, 0): [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
        (0, -1, 0): [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
        (0, 0, 1): [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
        (0, 0, -1): [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
    }
    vs, fs, cs, off = [], [], [], 0
    for (dx, dy, dz), quad in corners.items():
        exposed = ~occ[x + 1 + dx, y + 1 + dy, z + 1 + dz]
        n = int(exposed.sum())
        if not n:
            continue
        base = np.stack([x[exposed], y[exposed], z[exposed]], 1).astype(np.float32)
        q = np.array(quad, np.float32)
        v = (base[:, None, :] + q[None, :, :]).reshape(-1, 3)
        k = off + np.arange(n)[:, None] * 4
        f = np.concatenate([k + np.array([0, 1, 2]), k + np.array([0, 2, 3])])
        c = np.concatenate([col[exposed], col[exposed]])
        vs.append(v); fs.append(f); cs.append(c)
        off += 4 * n
    if not vs:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64), np.zeros((0, 3), np.uint8)
    return np.concatenate(vs), np.concatenate(fs), np.concatenate(cs)


READERS = {
    ".stl": read_stl, ".obj": read_obj, ".ply": read_ply, ".off": read_off,
    ".glb": read_gltf, ".gltf": read_gltf, ".fbx": read_fbx, ".dae": read_dae,
    ".3mf": read_3mf, ".3ds": read_3ds, ".usda": read_usd, ".usdz": read_usd, ".usd": read_usd, ".usdc": read_usd, ".vox": read_vox,
}


_FULL = (".obj", ".glb", ".gltf", ".fbx")


def read_mesh(data: bytes, path: str, full: bool = False) -> Mesh:
    """full: also read vertex normals (the 3D viewer); thumbnails don't need them."""
    ext = os.path.splitext(path)[1].lower()
    reader = READERS.get(ext)
    if reader is None:
        raise MeshError(f"no mesh reader for {ext}")
    try:
        return reader(data, path, full=True) if full and ext in _FULL else reader(data, path)
    except MeshError:
        raise
    except (ValueError, IndexError, KeyError, struct.error, zlib.error, TypeError, OverflowError) as e:
        raise MeshError(f"could not read {ext[1:].upper()}: {type(e).__name__}: {e}") from e
