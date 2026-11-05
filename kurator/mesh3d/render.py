"""Software renderer for thumbnails: orthographic 3/4 view, flat shading, outlines, base-colour
textures sampled per pixel.

Vectorised z-buffer rasteriser in numpy: triangles are grouped by the size of their screen bounding
box and every pixel of the box is tested at once. A million triangles take about a second.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

from . import MAX_TRIANGLES, Mesh, MeshError, thin

BASE = np.array([182, 188, 200], np.float32)
BG_TOP, BG_BOTTOM = (40, 44, 54), (24, 27, 33)
SAMPLES_PER_CHUNK = 600_000   # pixel tests per numpy step (memory: ~40 bytes each)


def _rotation(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rx @ ry


def _background(w: int, h: int) -> np.ndarray:
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    top, bot = np.array(BG_TOP, np.float32), np.array(BG_BOTTOM, np.float32)
    return np.broadcast_to(top * (1 - t) + bot * t, (h, w, 3)).copy()


def render(mesh: Mesh, size: int = 512, supersample: int = 2, yaw: float = -35.0, pitch: float = 24.0) -> Image.Image:
    """Thumbnail of a mesh. If memory runs short, tries again with fewer triangles and pixels."""
    import gc
    try:
        return _render(thin(mesh, MAX_TRIANGLES), size, supersample, yaw, pitch)
    except MemoryError:
        gc.collect()
        return _render(thin(mesh, 150_000), size, 1, yaw, pitch, textures=False)


def _textures(mesh: Mesh) -> dict:
    out = {}
    for k, m in enumerate(mesh.materials[:64]):
        ref = m.maps.get("base")
        img = ref.open(512) if ref is not None else None
        if img is None:
            continue
        try:
            a = np.asarray(img.convert("RGB"), np.float32) / 255
        except Exception:
            continue
        out[k] = a * np.array(m.color[:3], np.float32)
    return out


def _render(mesh: Mesh, size: int, supersample: int, yaw: float, pitch: float, textures: bool = True) -> Image.Image:
    W = H = size * supersample
    V = mesh.vertices.astype(np.float32)
    F = mesh.faces
    if len(F) == 0:
        raise MeshError("no triangles")
    used = np.unique(F)
    lo, hi = V[used].min(axis=0), V[used].max(axis=0)
    P = (V - (lo + hi) / 2) @ _rotation(yaw, pitch).T.astype(np.float32)
    pu = P[used]
    ext = max(float((pu[:, 0].max() - pu[:, 0].min())), float((pu[:, 1].max() - pu[:, 1].min())), 1e-12)
    s = W * 0.84 / ext
    cx = (pu[:, 0].max() + pu[:, 0].min()) / 2
    cy = (pu[:, 1].max() + pu[:, 1].min()) / 2
    sx = (P[:, 0] - cx) * s + W / 2
    sy = -(P[:, 1] - cy) * s + H / 2
    depth = -P[:, 2] * s          # smaller = closer to the camera

    i0, i1, i2 = F[:, 0], F[:, 1], F[:, 2]
    # flat shading, two-sided (many models have inconsistent winding)
    n = np.cross(P[i1] - P[i0], P[i2] - P[i0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 0
    n = n[keep] / ln[keep, None]
    n[n[:, 2] < 0] *= -1
    key = np.array([-0.45, 0.75, 0.5], np.float32); key /= np.linalg.norm(key)
    rim = np.array([0.7, 0.2, -0.3], np.float32); rim /= np.linalg.norm(rim)
    light = 0.30 + 0.62 * np.clip(n @ key, 0, 1) + 0.16 * np.clip(n @ rim, 0, 1) + 0.10 * n[:, 2]
    base = mesh.colors[keep].astype(np.float32) if mesh.colors is not None else np.broadcast_to(BASE, (int(keep.sum()), 3))
    shade = np.clip(base * light[:, None], 0, 255).astype(np.uint8)
    tri = np.nonzero(keep)[0]

    x = np.stack([sx[i0[tri]], sx[i1[tri]], sx[i2[tri]]], 1)
    y = np.stack([sy[i0[tri]], sy[i1[tri]], sy[i2[tri]]], 1)
    z = np.stack([depth[i0[tri]], depth[i1[tri]], depth[i2[tri]]], 1)
    zbuf = np.full(W * H, np.inf, np.float32)
    ibuf = np.full(W * H, -1, np.int32)
    _rasterise(x, y, z, W, H, zbuf, ibuf)

    img = _background(W, H)
    hit = ibuf >= 0
    flat = img.reshape(-1, 3)
    flat[hit] = shade[ibuf[hit]]
    texs = _textures(mesh) if textures and mesh.has_uv and mesh.face_mat is not None and mesh.materials else {}
    if texs:
        _sample(mesh, texs, flat, hit, ibuf, tri, x, y, light[:, None] if light.ndim == 1 else light, W)
    # outlines where the depth jumps (silhouettes, overlapping parts) make shapes readable when small
    zb = np.where(hit, zbuf, np.nan).reshape(H, W)
    edge = np.zeros((H, W), bool)
    thr = 0.02 * W

    def jumps(a, b):
        return (np.abs(a - b) > thr) | (np.isnan(a) != np.isnan(b))

    jh = jumps(zb[:, 1:], zb[:, :-1])
    edge[:, 1:] |= jh
    edge[:, :-1] |= jh
    jv = jumps(zb[1:, :], zb[:-1, :])
    edge[1:, :] |= jv
    edge[:-1, :] |= jv
    edge &= hit.reshape(H, W)
    img[edge] *= 0.55
    out = Image.fromarray(img.astype(np.uint8), "RGB")
    if supersample > 1:
        out = out.resize((size, size), Image.Resampling.LANCZOS)
    return out.filter(ImageFilter.UnsharpMask(radius=1, percent=40, threshold=2))


def _sample(mesh, texs, flat, hit, ibuf, tri, x, y, light, W) -> None:
    """Replace the flat colour of pixels on textured materials by the texture at that point."""
    pix = np.nonzero(hit)[0]
    k = ibuf[pix]
    fid = tri[k]
    fm = mesh.face_mat[fid]
    for mi, tex in texs.items():
        sel = np.nonzero(fm == mi)[0]
        if not len(sel):
            continue
        kk, pp = k[sel], pix[sel]
        px = (pp % W).astype(np.float32) + 0.5
        py = (pp // W).astype(np.float32) + 0.5
        X, Y = x[kk], y[kk]
        area = (X[:, 1] - X[:, 0]) * (Y[:, 2] - Y[:, 0]) - (X[:, 2] - X[:, 0]) * (Y[:, 1] - Y[:, 0])
        area = np.where(np.abs(area) < 1e-12, 1e-12, area)
        w0 = ((X[:, 1] - px) * (Y[:, 2] - py) - (X[:, 2] - px) * (Y[:, 1] - py)) / area
        w1 = ((X[:, 2] - px) * (Y[:, 0] - py) - (X[:, 0] - px) * (Y[:, 2] - py)) / area
        w2 = 1 - w0 - w1
        uvs = mesh.tri_uv(fid[sel]).astype(np.float32)
        u = w0 * uvs[:, 0, 0] + w1 * uvs[:, 1, 0] + w2 * uvs[:, 2, 0]
        v = w0 * uvs[:, 0, 1] + w1 * uvs[:, 1, 1] + w2 * uvs[:, 2, 1]
        th, tw = tex.shape[:2]
        col = np.floor(np.mod(u, 1.0) * tw).astype(np.int64).clip(0, tw - 1)
        row = np.floor(np.mod(v, 1.0) * th).astype(np.int64).clip(0, th - 1)   # glTF convention: v down
        flat[pp] = np.clip(tex[row, col] * 255 * light[kk], 0, 255)


def _rasterise(x, y, z, W, H, zbuf, ibuf) -> None:
    minx = np.clip(np.floor(x.min(1)), 0, W - 1).astype(np.int32)
    maxx = np.clip(np.ceil(x.max(1)), 0, W - 1).astype(np.int32)
    miny = np.clip(np.floor(y.min(1)), 0, H - 1).astype(np.int32)
    maxy = np.clip(np.ceil(y.max(1)), 0, H - 1).astype(np.int32)
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    ok = (np.abs(area) > 1e-9) & (x.max(1) >= 0) & (x.min(1) < W) & (y.max(1) >= 0) & (y.min(1) < H)
    bw = maxx - minx + 1
    bh = maxy - miny + 1
    kw = np.left_shift(1, np.ceil(np.log2(np.maximum(bw, 1))).astype(np.int64))
    kh = np.left_shift(1, np.ceil(np.log2(np.maximum(bh, 1))).astype(np.int64))
    ids = np.nonzero(ok)[0]
    if not len(ids):
        return
    keys = kw[ids] * 100_000 + kh[ids]
    order = np.argsort(keys, kind="stable")
    ids, keys = ids[order], keys[order]
    starts = np.r_[0, np.nonzero(np.diff(keys))[0] + 1, len(ids)]
    for a, b in zip(starts[:-1], starts[1:]):
        gw, gh = int(kw[ids[a]]), int(kh[ids[a]])
        per = max(1, SAMPLES_PER_CHUNK // (gw * gh))
        for c in range(a, b, per):
            t = ids[c:min(b, c + per)]
            _chunk(t, gw, gh, x, y, z, area, minx, maxx, miny, maxy, W, zbuf, ibuf)


def _chunk(t, gw, gh, x, y, z, area, minx, maxx, miny, maxy, W, zbuf, ibuf) -> None:
    gx = minx[t][:, None, None] + np.arange(gw, dtype=np.int32)[None, None, :]
    gy = miny[t][:, None, None] + np.arange(gh, dtype=np.int32)[None, :, None]
    px = gx.astype(np.float32) + 0.5
    py = gy.astype(np.float32) + 0.5
    x0, x1, x2 = (x[t, k][:, None, None] for k in range(3))
    y0, y1, y2 = (y[t, k][:, None, None] for k in range(3))
    inv = (1.0 / area[t])[:, None, None]
    w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) * inv
    w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) * inv
    w2 = 1.0 - w0 - w1
    eps = -1e-7
    inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps) & (gx <= maxx[t][:, None, None]) & (gy <= maxy[t][:, None, None])
    if not inside.any():
        return
    zz = w0 * z[t, 0][:, None, None] + w1 * z[t, 1][:, None, None] + w2 * z[t, 2][:, None, None]
    tri = np.broadcast_to(t[:, None, None], inside.shape)[inside]
    pix = (gy * W + gx)
    pix = np.broadcast_to(pix, inside.shape)[inside]
    zz = zz[inside].astype(np.float32)
    o = np.lexsort((zz, pix))
    pix, zz, tri = pix[o], zz[o], tri[o]
    first = np.r_[True, pix[1:] != pix[:-1]]
    pix, zz, tri = pix[first], zz[first], tri[first]
    better = zz < zbuf[pix]
    zbuf[pix[better]] = zz[better]
    ibuf[pix[better]] = tri[better]
