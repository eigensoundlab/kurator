"""3D models: rendered previews of the geometry, Blender's own thumbnails, embedded previews.

* Geometry formats (glTF/GLB, OBJ, FBX, STL, PLY, DAE, 3MF, 3DS, OFF, USDA/USDZ, VOX) are rendered
  by kurator.mesh3d and can be turned around in the 3D viewer.
* .blend files carry the thumbnail Blender saved (also zstd/gzip-compressed files, Blender 2.5-5.x).
* 3MF files from slicers carry a thumbnail; it is preferred over our own render.
* Formats nobody can read without the application (Maya, 3ds Max, Cinema 4D, ZBrush, Alembic,
  Rhino, SketchUp...) get the Windows thumbnail or an embedded image if there is one, else a card.
"""

from __future__ import annotations

import gzip
import io
import os
import struct

import numpy as np
from PIL import Image

from . import Handler, Loaded, register
from .common import placeholder, shell_thumbnail

MAX_BLEND = 1024 * 1024 * 1024


def _fmt(ext: str) -> str:
    return ext.lstrip(".").upper()


def _fmt_size(v: np.ndarray, unit: str | None) -> list:
    if not len(v):
        return []
    ext = v.max(axis=0) - v.min(axis=0)
    return [float(f"{x:.4g}") for x in ext]


def load_mesh_file(data: bytes, path: str, target: int | None) -> Loaded:
    from ..mesh3d import MeshError, read_mesh, render, texture_summary
    from ..mesh3d.formats import threemf_thumbnail
    ext = os.path.splitext(path)[1].lower()
    meta: dict = {"format": _fmt(ext), "model": {}}
    img = None
    if ext == ".3mf":
        img = threemf_thumbnail(data)
        if img is not None:
            meta["preview"] = "embedded"
    try:
        mesh = read_mesh(data, path)
    except MeshError as e:
        meta["model"]["note"] = str(e)
        if str(e).startswith("not a 3D model"):
            meta["placeholder"] = True
            meta["not_model"] = True
            return Loaded(placeholder(ext, "program code, not 3D"), meta, {}, None)
        if img is None:
            img = shell_thumbnail(path)
            if img is not None:
                meta["preview"] = "windows"
        if img is None:
            meta["placeholder"] = True
            img = placeholder(ext, "no preview")
        return Loaded(img, meta, {}, None)
    used = np.unique(mesh.faces) if len(mesh.faces) else np.zeros(0, np.int64)
    model = {"vertices": int(len(used)), "triangles": mesh.triangles,
             "dimensions": _fmt_size(mesh.vertices[used] if len(used) else mesh.vertices, mesh.info.get("unit"))}
    model.update(mesh.info)
    model.update(texture_summary(mesh))
    names = mesh.info.get("textures") if isinstance(mesh.info.get("textures"), list) else mesh.info.get("images")
    if not model.get("textured") and names:   # formats without material wiring here (3DS...): the names they list
        from ..mesh3d import resolve_texture
        maps = [{"role": "texture", "name": n.replace("\\", "/").rsplit("/", 1)[-1],
                 "found": resolve_texture(os.path.dirname(path), n) is not None, "embedded": False} for n in names[:64]]
        model.update({"textured": True, "maps": maps})
        missing = [m["name"] for m in maps if not m["found"]]
        if missing:
            model["missing_textures"] = missing
    model["uv"] = mesh.has_uv
    meta["model"] = model
    meta["mesh"] = True   # the 3D viewer can show it
    if img is None:
        img = render(mesh, 512)
        meta["preview"] = "rendered"
    return Loaded(img, meta, dict(mesh.dates), None)


# ---------------------------------------------------------------- Blender
def _read_all(f) -> bytes:
    out, total = [], 0
    while total < MAX_BLEND:
        b = f.read(min(16 * 1024 * 1024, MAX_BLEND - total))
        if not b:
            break
        out.append(b)
        total += len(b)
    return b"".join(out)


def _blend_bytes(data: bytes) -> tuple[bytes, str | None]:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        # Blender writes several zstd frames (seekable format): read across all of them, with a cap
        try:
            from compression import zstd   # Python 3.14+
            with zstd.ZstdFile(io.BytesIO(data)) as f:
                return _read_all(f), "zstd"
        except ImportError:
            import zstandard
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True) as r:
                return _read_all(r), "zstd"
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            return _read_all(g), "gzip"
    return data, None


def parse_blend(data: bytes) -> dict:
    """Header, block counts and the saved thumbnail of a .blend file (no Blender needed)."""
    raw, comp = _blend_bytes(data)
    if not raw.startswith(b"BLENDER"):
        raise ValueError("not a .blend file")
    out: dict = {"compression": comp}
    if raw[7:9].isdigit() and raw[9:10] == b"-":
        # Blender 5.0+: "BLENDER17-01v0500" (header size, file format version, endian, version)
        hsize = int(raw[7:9])
        endian = "<" if raw[12:13] == b"v" else ">"
        ver = raw[13:hsize].decode("ascii", "replace")
        out["blender"] = f"{int(ver[:-2])}.{int(ver[-2:])}"
        layouts = [("large", hsize)]
    else:
        ptr = 8 if raw[7:8] == b"-" else 4
        endian = "<" if raw[8:9] == b"v" else ">"
        ver = raw[9:12].decode("ascii", "replace")
        out["blender"] = f"{ver[0]}.{int(ver[1:])}"
        layouts = [(ptr, 12)]
    counts: dict[str, int] = {}
    thumb = None
    for layout, off in layouts:
        n = len(raw)
        guard = 0
        while off + 16 <= n and guard < 5_000_000:
            guard += 1
            code = raw[off:off + 4]
            if layout == "large":   # code, SDNAnr, old pointer (8), length (8), count (8)
                length = struct.unpack_from(endian + "q", raw, off + 16)[0]
                head = 32
            else:
                length = struct.unpack_from(endian + "i", raw, off + 4)[0]
                head = 20 if layout == 4 else 24   # code, len, old pointer, SDNAnr, count
            if length < 0 or off + head + length > n:
                break
            if code == b"ENDB":
                break
            if code == b"TEST" and thumb is None and length >= 8:
                w, h = struct.unpack_from(endian + "ii", raw, off + head)
                if 0 < w <= 2048 and 0 < h <= 2048 and length >= 8 + w * h * 4:
                    px = raw[off + head + 8: off + head + 8 + w * h * 4]
                    thumb = Image.frombytes("RGBA", (w, h), px).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if code[2:4] == b"\0\0" and code[:2].isalpha():
                k = code[:2].decode()
                counts[k] = counts.get(k, 0) + 1
            off += head + length
    if thumb is None:
        thumb = _find_test_block(raw[:16 * 1024 * 1024], endian)
    names = {"OB": "objects", "ME": "meshes", "MA": "materials", "IM": "images", "SC": "scenes",
             "AC": "actions", "CA": "cameras", "LA": "lights", "GR": "collections", "NT": "node groups"}
    for code, label in names.items():
        if counts.get(code):
            out[label] = counts[code]
    out["thumbnail"] = thumb
    return out


def _find_test_block(raw: bytes, endian: str) -> Image.Image | None:
    """Fallback for block layouts we don't know: look for the thumbnail block by its shape."""
    pos = raw.find(b"TEST")
    while pos >= 0:
        for head in (20, 24, 32):
            try:
                w, h = struct.unpack_from(endian + "ii", raw, pos + head)
            except struct.error:
                break
            if 16 <= w <= 2048 and 16 <= h <= 2048 and pos + head + 8 + w * h * 4 <= len(raw):
                px = raw[pos + head + 8: pos + head + 8 + w * h * 4]
                return Image.frombytes("RGBA", (w, h), px).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        pos = raw.find(b"TEST", pos + 4)
    return None


def load_blend(data: bytes, path: str, target: int | None) -> Loaded:
    ext = os.path.splitext(path)[1].lower()
    meta: dict = {"format": "BLEND"}
    try:
        info = parse_blend(data)
    except Exception as e:  # damaged or unknown variant: still list it
        info = {"thumbnail": None, "note": f"{type(e).__name__}: {e}"}
    img = info.pop("thumbnail", None)
    meta["model"] = {k: v for k, v in info.items() if v is not None}
    if ext.startswith(".blend") and ext != ".blend":
        meta["model"]["backup"] = True
    if img is not None:
        bg = Image.new("RGBA", img.size, (36, 40, 50, 255))
        img = Image.alpha_composite(bg, img.convert("RGBA")).convert("RGB")
        meta["preview"] = "embedded"
    else:
        img = shell_thumbnail(path)
        if img is not None:
            meta["preview"] = "windows"
        else:
            meta["placeholder"] = True
            img = placeholder(".blend", "no saved preview")
    return Loaded(img, meta, {}, None)


# ---------------------------------------------------------------- others
_PNG = b"\x89PNG\r\n\x1a\n"


def _embedded_png(data: bytes, limit: int = 64 * 1024 * 1024) -> Image.Image | None:
    """The largest PNG found inside the file (SketchUp keeps its preview this way)."""
    best = None
    pos = data.find(_PNG, 0, limit)
    tries = 0
    while pos >= 0 and tries < 16:
        tries += 1
        try:
            img = Image.open(io.BytesIO(data[pos:]))
            img.load()
            if best is None or img.width * img.height > best.width * best.height:
                best = img
        except Exception:
            pass
        pos = data.find(_PNG, pos + 8, limit)
    return best


def load_other(data: bytes, path: str, target: int | None) -> Loaded:
    ext = os.path.splitext(path)[1].lower()
    meta: dict = {"format": _fmt(ext), "model": {}}
    img = shell_thumbnail(path)
    if img is not None:
        meta["preview"] = "windows"
    elif ext == ".skp":
        img = _embedded_png(data)
        if img is not None:
            meta["preview"] = "embedded"
    if img is None:
        meta["placeholder"] = True
        img = placeholder(ext, "no preview")
    return Loaded(img, meta, {}, None)


MESH_FORMATS = (".glb", ".gltf", ".obj", ".fbx", ".stl", ".ply", ".dae", ".3mf", ".3ds", ".off",
                ".usda", ".usdz", ".vox")
OTHER_FORMATS = (".usd", ".usdc", ".ma", ".mb", ".max", ".c4d", ".ztl", ".zpr", ".abc", ".3dm", ".skp",
                 ".lwo", ".hip", ".spp", ".sbs", ".sbsar")

register(Handler("model", MESH_FORMATS, load_mesh_file, group="3d"))
register(Handler("model", (".blend", ".blend1", ".blend2"), load_blend, group="3d"))
register(Handler("model", OTHER_FORMATS, load_other, group="3d"))
