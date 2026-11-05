"""3D models: reading meshes from common formats, and rendering preview images.

Pure Python + numpy, no 3D library needed. Readers return a Mesh in a Y-up coordinate system.
Formats whose geometry can't be read (Maya, 3ds Max, Cinema 4D...) still get thumbnails from
their embedded previews or from Windows; see kurator/handlers/model3d.py.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import numpy as np

MAX_TRIANGLES = 1_200_000   # bigger meshes are thinned out for thumbnails (memory)
VIEW_TRIANGLES = 1_500_000  # ... and for the 3D viewer
TEXTURE_EXTS = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".webp", ".dds", ".psd", ".exr", ".hdr", ".ktx2")
ROLES = ("base", "normal", "emissive", "occlusion", "metal_rough", "roughness", "metallic", "opacity", "specular", "height")


class MeshError(ValueError):
    pass


@dataclass
class TexRef:
    """A texture a material uses: a file on disk, bytes inside the model, or a name that wasn't found."""
    name: str                      # as written in the model (may be an absolute path from another PC)
    path: str | None = None        # the file found on disk
    data: bytes | None = None      # embedded (GLB, data: URI, FBX video content)

    @property
    def found(self) -> bool:
        return self.path is not None or self.data is not None

    def open(self, max_side: int | None = None):
        """PIL image or None (unknown format, unreadable)."""
        from PIL import Image
        try:
            if self.data is not None:
                img = Image.open(io.BytesIO(self.data))
            elif self.path is not None:
                if os.path.getsize(self.path) > 512 * 1024 * 1024:
                    return None
                img = Image.open(self.path)
            else:
                return None
            if max_side:
                img.draft("RGB", (max_side, max_side))
                img.load()
                if max(img.size) > max_side:
                    img.thumbnail((max_side, max_side))
            return img
        except Exception:
            return None


@dataclass
class Material:
    name: str = ""
    color: tuple = (0.72, 0.74, 0.78, 1.0)     # base colour factor, sRGB 0..1 (+ alpha)
    emissive: tuple = (0.0, 0.0, 0.0)
    maps: dict = field(default_factory=dict)  # role -> TexRef (roles: ROLES)


@dataclass
class Mesh:
    vertices: np.ndarray                 # (n, 3) float32, Y up
    faces: np.ndarray                    # (m, 3) int32
    colors: np.ndarray | None = None     # (m, 3) uint8 per face, optional
    info: dict = field(default_factory=dict)   # format-specific facts for the side panel
    dates: dict = field(default_factory=dict)  # embedded dates, e.g. {"document": ts}
    uv: np.ndarray | None = None         # (n, 2) per vertex ...
    corner_uv: np.ndarray | None = None  # ... or (m, 3, 2) per triangle corner (OBJ, FBX)
    face_mat: np.ndarray | None = None   # (m,) material index per triangle, -1 = none
    materials: list = field(default_factory=list)   # [Material]
    normals: np.ndarray | None = None          # (n, 3) per vertex, from the file (only when read for the viewer)
    corner_normals: np.ndarray | None = None   # (m, 3, 3) per triangle corner

    @property
    def triangles(self) -> int:
        return int(len(self.faces))

    @property
    def has_uv(self) -> bool:
        return self.uv is not None or self.corner_uv is not None

    def tri_uv(self, tri: np.ndarray) -> np.ndarray | None:
        """(k, 3, 2) texture coordinates of the given triangles."""
        if self.corner_uv is not None:
            return self.corner_uv[tri]
        if self.uv is not None:
            return self.uv[self.faces[tri]]
        return None


def resolve_texture(base_dir: str, ref: str) -> str | None:
    """Find a texture a model refers to. Artists' files often hold absolute paths from their own PC
    or the wrong extension, so look next to the model and in usual texture folders too."""
    from urllib.parse import unquote
    ref = unquote((ref or "").strip().strip('"')).replace("\\", "/")
    if not ref or ref.startswith("data:"):
        return None
    base_dir = os.path.abspath(base_dir)
    name = ref.rsplit("/", 1)[-1]
    cands = []
    if not os.path.isabs(ref) and not (len(ref) > 1 and ref[1] == ":"):
        cands.append(os.path.normpath(os.path.join(base_dir, ref)))
    else:
        cands.append(ref)
    dirs = [base_dir] + [os.path.join(base_dir, d) for d in ("textures", "Textures", "tex", "Tex", "maps", "Maps", "materials", "Materials")]
    parent = os.path.dirname(base_dir)
    dirs += [os.path.join(parent, d) for d in ("textures", "Textures", "tex", "maps", "Materials")]
    cands += [os.path.join(d, name) for d in dirs]
    for c in cands:
        if os.path.isfile(c):
            return c
    stem = os.path.splitext(name)[0].lower()
    for d in dirs:   # case differences and another extension (.tga in the model, .png on disk)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        exact = [n for n in names if n.lower() == name.lower()]
        if exact:
            return os.path.join(d, exact[0])
        other = [n for n in names if os.path.splitext(n)[0].lower() == stem and os.path.splitext(n)[1].lower() in TEXTURE_EXTS]
        if other:
            return os.path.join(d, sorted(other)[0])
    return None


def texture_summary(mesh: "Mesh") -> dict:
    """What the side panel shows: the maps the materials use, and the ones that are missing."""
    maps, missing = [], []
    seen = set()
    for m in mesh.materials:
        for role, ref in m.maps.items():
            key = (role, ref.path or ref.name)
            if key in seen:
                continue
            seen.add(key)
            name = os.path.basename(ref.path) if ref.path else (ref.name.replace("\\", "/").rsplit("/", 1)[-1] or "embedded")
            maps.append({"role": role, "name": name, "found": ref.found, "embedded": ref.data is not None})
            if not ref.found:
                missing.append(name)
    out = {"textured": bool(maps)}
    if maps:
        out["maps"] = maps[:64]
    if missing:
        out["missing_textures"] = sorted(set(missing))[:32]
    return out


def merge(parts: list[tuple[np.ndarray, np.ndarray, np.ndarray | None]]) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Concatenate (vertices, faces, face_colors) parts into one mesh."""
    vs, fs, cs, off = [], [], [], 0
    any_color = any(p[2] is not None for p in parts)
    for v, f, c in parts:
        if len(v) == 0 or len(f) == 0:
            continue
        vs.append(np.asarray(v, np.float32))
        fs.append(np.asarray(f, np.int64) + off)
        if any_color:
            cs.append(np.asarray(c, np.uint8) if c is not None else np.full((len(f), 3), (180, 186, 198), np.uint8))
        off += len(v)
    if not vs:
        raise MeshError("no geometry found")
    v = np.concatenate(vs)
    f = np.concatenate(fs)
    c = np.concatenate(cs) if any_color else None
    ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
    if not ok.all():
        f, c = f[ok], (c[ok] if c is not None else None)
    return v, f.astype(np.int32), c


def z_up_to_y_up(v: np.ndarray) -> np.ndarray:
    out = np.empty_like(v)
    out[:, 0], out[:, 1], out[:, 2] = v[:, 0], v[:, 2], -v[:, 1]
    return out


def thin(mesh: Mesh, limit: int) -> Mesh:
    """Keep every k-th triangle so that at most `limit` remain (previews / the 3D viewer)."""
    m = len(mesh.faces)
    if m <= limit:
        return mesh
    step = int(np.ceil(m / limit))
    sl = slice(None, None, step)
    return Mesh(mesh.vertices, mesh.faces[sl], mesh.colors[sl] if mesh.colors is not None else None,
                mesh.info, mesh.dates, mesh.uv, mesh.corner_uv[sl] if mesh.corner_uv is not None else None,
                mesh.face_mat[sl] if mesh.face_mat is not None else None, mesh.materials, mesh.normals,
                mesh.corner_normals[sl] if mesh.corner_normals is not None else None)


def material_colors(mesh: Mesh) -> None:
    """Per-face colours from the materials (base colour times the average of its texture) for
    renderers that don't sample textures."""
    if not mesh.materials or mesh.face_mat is None:
        return
    pal = []
    for m in mesh.materials:
        c = np.array(m.color[:3], np.float32)
        ref = m.maps.get("base")
        img = ref.open(64) if ref is not None else None
        if img is not None:
            try:
                avg = np.array(img.convert("RGB").resize((1, 1), 4).getpixel((0, 0)), np.float32) / 255
                c = c * avg
            except Exception:
                pass
        pal.append(np.clip(c * 255, 0, 255).astype(np.uint8))
    pal.append(np.array((180, 186, 198), np.uint8))
    pal = np.array(pal, np.uint8)
    fm = np.where((mesh.face_mat >= 0) & (mesh.face_mat < len(mesh.materials)), mesh.face_mat, len(mesh.materials))
    mesh.colors = pal[fm]


from .formats import READERS, read_mesh  # noqa: E402,F401
from .render import render  # noqa: E402,F401
