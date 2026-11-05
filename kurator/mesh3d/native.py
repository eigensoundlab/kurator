"""Decoders with native code - Draco (DracoPy) and binary USD (Pixar's usd-core) - run in a separate,
short-lived Python process. They are optional (install.bat 3d), and a malformed file that crashes
native code can then only end that process, never Kurator.

The child gets its input on stdin (or a file path), writes an .npz with the arrays to stdout and a
message to stderr when it fails.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys

import numpy as np

from . import MeshError

TIMEOUT = 180
MODULES = {"draco": "DracoPy", "usd": "pxr"}
_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
         "from kurator.mesh3d.native import child; child(sys.argv[2], sys.argv[3])")


def available(kind: str) -> bool:
    try:
        return importlib.util.find_spec(MODULES[kind]) is not None
    except (ValueError, ImportError):
        return False


def run(kind: str, payload: bytes | None = None, path: str | None = None, timeout: int = TIMEOUT) -> dict:
    """Arrays decoded by the child process (+ 'info' dict). Raises MeshError."""
    if not available(kind):
        raise MeshError(f"needs the optional 3D add-on ({MODULES[kind]}): run install.bat 3d / ./install.sh 3d")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cmd = [sys.executable, "-I", "-c", _BOOT, root, kind, path or "-"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(cmd, input=payload or b"", capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise MeshError(f"{MODULES[kind]} took longer than {timeout} s") from None
    if p.returncode != 0:
        msg = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MeshError(msg[-1][:300] if msg else f"{MODULES[kind]} stopped (code {p.returncode})")
    with np.load(io.BytesIO(p.stdout), allow_pickle=False) as z:
        out = {k: z[k] for k in z.files}
    if "info" in out:
        out["info"] = json.loads(bytes(out["info"]).decode("utf-8"))
    return out


# ---------------------------------------------------------------- in the child process
def _emit(arrays: dict, info: dict | None = None) -> None:
    if info is not None:
        arrays["info"] = np.frombuffer(json.dumps(info).encode("utf-8"), np.uint8)
    buf = io.BytesIO()
    np.savez(buf, **{k: v for k, v in arrays.items() if v is not None})
    sys.stdout.buffer.write(buf.getvalue())
    sys.stdout.flush()


def _draco(data: bytes) -> None:
    import DracoPy
    m = DracoPy.decode(data)
    pts = np.asarray(m.points, np.float32).reshape(-1, 3)
    faces = np.asarray(getattr(m, "faces", None) if getattr(m, "faces", None) is not None else [], np.int64).reshape(-1, 3)
    uv = getattr(m, "tex_coord", None)
    nrm = getattr(m, "normals", None)
    _emit({"points": pts, "faces": faces,
           "uv": np.asarray(uv, np.float32).reshape(-1, 2) if uv is not None and len(uv) == len(pts) else None,
           "normals": np.asarray(nrm, np.float32).reshape(-1, 3) if nrm is not None and len(nrm) == len(pts) else None})


def _usd(path: str) -> None:
    """Meshes of a USD stage in world space (Y up), their UVs and base-colour textures."""
    from pxr import Gf, Usd, UsdGeom, UsdShade
    stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError("USD stage could not be opened")
    up = UsdGeom.GetStageUpAxis(stage)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    vs, fs, uvs, fms, off = [], [], [], [], 0
    mats: list[dict] = []
    mat_index: dict = {}
    any_uv = False
    n_mesh = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        if UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        mesh = UsdGeom.Mesh(prim)
        pts = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        idx = mesh.GetFaceVertexIndicesAttr().Get()
        if not pts or not counts or not idx:
            continue
        n_mesh += 1
        v = np.asarray(pts, np.float64).reshape(-1, 3)
        M = np.asarray(cache.GetLocalToWorldTransform(prim), np.float64).reshape(4, 4)
        v = np.c_[v, np.ones(len(v))] @ M
        v = v[:, :3]
        counts = np.asarray(counts, np.int64)
        idx = np.asarray(idx, np.int64)
        ntri = np.maximum(counts - 2, 0)
        starts = np.r_[0, np.cumsum(counts)[:-1]]
        poly = np.repeat(np.arange(len(counts)), ntri)
        k = np.arange(int(ntri.sum())) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
        s = starts[poly]
        corners = np.stack([s, s + k, s + k + 1], 1)
        f = idx[corners]
        uv = None
        api = UsdGeom.PrimvarsAPI(prim)
        for name in ("st", "st0", "UVMap", "uv"):
            pv = api.GetPrimvar(name)
            if pv and pv.HasValue():
                vals = pv.ComputeFlattened()
                if vals is None:
                    continue
                arr = np.asarray(vals, np.float32).reshape(-1, 2)
                interp = pv.GetInterpolation()
                if interp == UsdGeom.Tokens.faceVarying and len(arr) >= len(idx):
                    uv = arr[corners]
                elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying) and len(arr) >= len(v):
                    uv = arr[f]
                if uv is not None:
                    uv = uv.copy()
                    uv[..., 1] = 1 - uv[..., 1]   # to the glTF convention (v down)
                    break
        mat_id = -1
        bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        if bound:
            key = str(bound.GetPath())
            if key not in mat_index:
                mat_index[key] = len(mats)
                mats.append(_usd_material(bound, path))
            mat_id = mat_index[key]
        vs.append(v.astype(np.float32))
        fs.append(f + off)
        uvs.append(uv)
        any_uv |= uv is not None
        fms.append(np.full(len(f), mat_id, np.int32))
        off += len(v)
    if not vs:
        raise ValueError("no mesh geometry in this USD file")
    v = np.concatenate(vs)
    if up == "Z":
        v = np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1)
    out = {"points": v, "faces": np.concatenate(fs), "face_mat": np.concatenate(fms)}
    if any_uv:
        out["corner_uv"] = np.concatenate([u if u is not None else np.zeros((len(ff), 3, 2), np.float32) for u, ff in zip(uvs, fs)])
    meters = UsdGeom.GetStageMetersPerUnit(stage)
    info = {"meshes": n_mesh, "materials": len(mats), "up_axis": str(up), "materials_list": mats,
            "unit": {1.0: "m", 0.01: "cm", 0.001: "mm"}.get(round(meters, 6), f"{meters:g} m")}
    _emit(out, info)


def _usd_material(material, path: str) -> dict:
    """Base colour (factor or texture) of a UsdPreviewSurface material."""
    from pxr import UsdShade
    out = {"name": material.GetPrim().GetName(), "color": [0.72, 0.74, 0.78, 1.0], "maps": {}}
    surf = material.ComputeSurfaceSource()[0]
    if not surf:
        return out
    roles = {"diffuseColor": "base", "normal": "normal", "emissiveColor": "emissive", "occlusion": "occlusion",
             "roughness": "roughness", "metallic": "metallic", "opacity": "opacity"}
    for inp in surf.GetInputs():
        role = roles.get(inp.GetBaseName())
        if not role:
            continue
        src = inp.GetConnectedSource()
        if src and src[0]:
            shader = UsdShade.Shader(src[0].GetPrim())
            fi = shader.GetInput("file")
            if fi:
                asset = fi.Get()
                if asset is not None:
                    resolved = getattr(asset, "resolvedPath", "") or ""
                    out["maps"][role] = {"name": asset.path, "resolved": resolved}
        elif role == "base":
            val = inp.Get()
            if val is not None:
                out["color"] = [max(0.0, min(1.0, float(c))) ** (1 / 2.2) for c in val][:3] + [1.0]
    return out


def child(kind: str, path: str) -> None:
    try:
        if kind == "draco":
            _draco(sys.stdin.buffer.read())
        elif kind == "usd":
            _usd(path)
        else:
            raise ValueError(f"unknown decoder {kind}")
    except Exception as e:   # one clean line for the parent
        sys.stderr.write(f"{type(e).__name__}: {e}\n")
        sys.exit(2)
