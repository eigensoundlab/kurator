"""Geometry for the 3D viewer in one binary blob ('KMSH' version 2).

Layout: b'KMSH', u32 JSON length, JSON (padded to 4 bytes), then
  positions  float32 × 3 per vertex
  normals    int8 × 4 per vertex (x, y, z, 0), from the file or smoothed
  uv         float32 × 2 per vertex (only when the header says "uv": true), glTF convention (v down)
  colors     uint8 × 4 per vertex (material colour for untextured parts)
  indices    uint32 × 3 per triangle, sorted by material; header "groups" lists the ranges.
Header "materials" describes each material with its maps; map textures are fetched separately
(/api/mesh/{id}/tex/{k}) so the geometry shows up before the textures arrive.
"""

from __future__ import annotations

import json

import numpy as np

from . import VIEW_TRIANGLES, Mesh, thin


def _smooth_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])   # area-weighted
    n = np.zeros((len(v), 3), np.float64)
    for k in range(3):
        for c in range(3):
            n[:, c] += np.bincount(f[:, k], weights=fn[:, c], minlength=len(v))
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return (n / np.where(ln > 0, ln, 1)).astype(np.float32)


def _i8(n: np.ndarray) -> np.ndarray:
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.where(ln > 0, ln, 1)
    out = np.zeros((len(n), 4), np.int8)
    out[:, :3] = np.clip(np.round(n * 127), -127, 127).astype(np.int8)
    return out


def pack(mesh: Mesh) -> tuple[bytes, list]:
    """(body, textures): textures[k] is the TexRef the header's map {"tex": k} refers to."""
    m = thin(mesh, VIEW_TRIANGLES)
    v = np.ascontiguousarray(m.vertices, np.float32)
    f = np.ascontiguousarray(m.faces, np.int64)
    nmat = len(m.materials)
    fm = m.face_mat if m.face_mat is not None else np.full(len(f), -1, np.int32)
    fm = np.where((fm >= 0) & (fm < nmat), fm, -1).astype(np.int32)
    order = np.argsort(fm, kind="stable")
    f, fm = f[order], fm[order]
    corner_uv = m.corner_uv[order] if m.corner_uv is not None else None
    corner_n = m.corner_normals[order] if m.corner_normals is not None else None
    face_col = m.colors[order] if m.colors is not None else None

    normals_from = "file" if (m.normals is not None or corner_n is not None) else "smooth"
    if corner_uv is not None or corner_n is not None:
        # per-corner data: every triangle gets its own three vertices
        pos = v[f].reshape(-1, 3)
        if corner_n is not None:
            nrm = corner_n.reshape(-1, 3)
        else:
            nrm = (m.normals if m.normals is not None else _smooth_normals(v, f))[f].reshape(-1, 3)
        uv = corner_uv.reshape(-1, 2) if corner_uv is not None else (m.uv[f].reshape(-1, 2) if m.uv is not None else None)
        col = np.repeat(face_col, 3, axis=0) if face_col is not None else None
        idx = np.arange(len(pos), dtype=np.uint32).reshape(-1, 3)
    else:
        pos = v
        nrm = m.normals if m.normals is not None else _smooth_normals(v, f)
        uv = m.uv
        col = None
        if face_col is not None:
            col = np.full((len(v), 3), 190, np.uint8)
            col[f.reshape(-1)] = np.repeat(face_col, 3, axis=0)
        idx = f.astype(np.uint32)
    rgba = np.full((len(pos), 4), 255, np.uint8)
    if col is not None:
        rgba[:, :3] = col
    else:
        rgba[:, :3] = 190

    groups = []
    if len(fm):
        cuts = np.r_[0, np.nonzero(np.diff(fm))[0] + 1, len(fm)]
        groups = [{"start": int(a), "count": int(b - a), "material": int(fm[a])} for a, b in zip(cuts[:-1], cuts[1:])]
    textures: list = []
    tex_ids: dict = {}
    mats = []
    for mt in m.materials:
        maps = {}
        for role, ref in mt.maps.items():
            entry = {"name": (ref.name or "").replace("\\", "/").rsplit("/", 1)[-1] or "embedded", "found": ref.found,
                     "embedded": ref.data is not None}
            if ref.found:
                key = ref.path or id(ref)
                if key not in tex_ids:
                    tex_ids[key] = len(textures)
                    textures.append((ref, role))
                entry["tex"] = tex_ids[key]
            maps[role] = entry
        mats.append({"name": mt.name, "color": [round(float(c), 4) for c in mt.color],
                     "emissive": [round(float(c), 4) for c in mt.emissive], "maps": maps})
    used = np.unique(f) if len(f) else np.zeros(0, np.int64)
    lo = v[used].min(axis=0) if len(used) else np.zeros(3)
    hi = v[used].max(axis=0) if len(used) else np.zeros(3)
    head = json.dumps({"version": 2, "vertices": len(pos), "triangles": len(idx), "uv": uv is not None,
                       "colors": face_col is not None, "normals": normals_from, "groups": groups, "materials": mats,
                       "textures": len(textures), "thinned": len(m.faces) < len(mesh.faces),
                       "min": [float(x) for x in lo], "max": [float(x) for x in hi]}).encode()
    head += b" " * (-len(head) % 4)
    parts = [b"KMSH", len(head).to_bytes(4, "little"), head, np.ascontiguousarray(pos, np.float32).tobytes(),
             _i8(np.asarray(nrm, np.float32)).tobytes()]
    if uv is not None:
        parts.append(np.ascontiguousarray(uv, np.float32).tobytes())
    parts += [rgba.tobytes(), np.ascontiguousarray(idx, np.uint32).tobytes()]
    return b"".join(parts), textures


def texture_bytes(ref, role: str, max_side: int = 2048) -> tuple[bytes, str] | None:
    """A map texture for the browser: WebP (lossless for normal maps, which show artefacts easily)."""
    import io
    img = ref.open(max_side)
    if img is None:
        return None
    try:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if ("A" in img.getbands() or img.mode == "P") else "RGB")
        buf = io.BytesIO()
        if role == "normal":
            img.save(buf, "WEBP", lossless=True, method=1)
        else:
            img.save(buf, "WEBP", quality=90, method=3)
        return buf.getvalue(), "image/webp"
    except Exception:
        return None
