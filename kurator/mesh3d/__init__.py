"""3D models: reading meshes from common formats, and rendering preview images.

Pure Python + numpy, no 3D library needed. Readers return a Mesh in a Y-up coordinate system.
Formats whose geometry can't be read (Maya, 3ds Max, Cinema 4D...) still get thumbnails from
their embedded previews or from Windows; see kurator/handlers/model3d.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MAX_TRIANGLES = 3_000_000   # bigger meshes are thinned out for previews


class MeshError(ValueError):
    pass


@dataclass
class Mesh:
    vertices: np.ndarray                 # (n, 3) float32, Y up
    faces: np.ndarray                    # (m, 3) int32
    colors: np.ndarray | None = None     # (m, 3) uint8 per face, optional
    info: dict = field(default_factory=dict)   # format-specific facts for the side panel
    dates: dict = field(default_factory=dict)  # embedded dates, e.g. {"document": ts}

    @property
    def triangles(self) -> int:
        return int(len(self.faces))


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
    return Mesh(mesh.vertices, mesh.faces[::step], mesh.colors[::step] if mesh.colors is not None else None,
                mesh.info, mesh.dates)


from .formats import READERS, read_mesh  # noqa: E402,F401
from .render import render  # noqa: E402,F401
