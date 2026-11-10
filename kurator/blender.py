"""Blender, when it is installed: turn .blend files into glTF for the 3D viewer.

Blender runs in the background with its factory settings and with Python auto-run switched off
(--disable-autoexec), so scripts or drivers stored in a .blend file never run. The result is cached
by content hash, so each file is converted once.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import threading

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# Runs inside Blender. Export options differ between versions: try the full set, then the basics.
EXPORT = r"""
import sys, bpy
out = sys.argv[sys.argv.index("--") + 1]
full = dict(filepath=out, export_format="GLB", export_apply=True, use_visible=True, export_animations=False,
            export_cameras=False, export_lights=False, export_extras=False, export_draco_mesh_compression_enable=False)
try:
    bpy.ops.export_scene.gltf(**full)
except TypeError:
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
"""


# Runs inside Blender: what the materials are made of (image textures, procedural nodes, plain values) and
# what the glTF preview keeps of them, plus the meshes (as exported: modifiers applied), UV maps, lights,
# camera and world. Read-only: nothing is saved.
REPORT = r"""
import sys, os, json, bpy
out = sys.argv[sys.argv.index("--") + 1]
PROCEDURAL = {"TEX_NOISE", "TEX_VORONOI", "TEX_WAVE", "TEX_MAGIC", "TEX_MUSGRAVE", "TEX_GRADIENT", "TEX_CHECKER", "TEX_BRICK",
              "TEX_WHITE_NOISE", "TEX_SKY", "TEX_POINTDENSITY", "TEX_GABOR", "AMBIENT_OCCLUSION", "BEVEL", "WIREFRAME",
              "LAYER_WEIGHT", "FRESNEL", "TEX_IES", "SCRIPT"}
PASS = {"NORMAL_MAP", "SEPRGB", "SEPARATE_COLOR", "SEPXYZ", "SEPARATE_XYZ", "REROUTE", "GAMMA", "BUMP"}
INPUTS = [("Base Color", "base_color"), ("Metallic", "metallic"), ("Roughness", "roughness"), ("Normal", "normal"),
          ("Emission Color", "emission"), ("Emission", "emission"), ("Alpha", "alpha"), ("Transmission Weight", "transmission"),
          ("Transmission", "transmission"), ("Subsurface Weight", "subsurface"), ("Coat Weight", "coat"), ("Sheen Weight", "sheen")]

def image(img):
    path = bpy.path.abspath(img.filepath, library=img.library) if img.filepath else ""
    packed = bool(img.packed_file)
    missing = img.source == "FILE" and not packed and not (path and os.path.isfile(path))
    try:
        size = [int(img.size[0]), int(img.size[1])] if not missing else [0, 0]
    except Exception:
        size = [0, 0]
    return {"name": img.name, "file": os.path.basename(path) if path else "", "size": size, "packed": packed,
            "missing": missing, "colorspace": img.colorspace_settings.name, "source": img.source}

def upstream(socket, seen=None, depth=0):
    seen = seen if seen is not None else set()
    nodes = []
    if depth > 40:
        return nodes
    for link in socket.links:
        n = link.from_node
        if n.name in seen:
            continue
        seen.add(n.name)
        nodes.append(n)
        if n.type == "GROUP" and n.node_tree:
            nodes += [m for m in n.node_tree.nodes if m.type not in ("GROUP_INPUT", "GROUP_OUTPUT")]
        for s in n.inputs:
            if n.type == "TEX_IMAGE" and s.name == "Vector":
                continue                     # how the image is placed, not what it is
            nodes += upstream(s, seen, depth + 1)
    return nodes

def describe(socket):
    if not socket.is_linked:
        v = socket.default_value
        try:
            v = [round(float(x), 3) for x in v][:4]
        except TypeError:
            v = round(float(v), 3)
        return {"kind": "value", "value": v}
    nodes = upstream(socket)
    imgs = [image(n.image) for n in nodes if n.type == "TEX_IMAGE" and n.image]
    proc = sorted({n.bl_label or n.type for n in nodes if n.type in PROCEDURAL})
    attrs = [n for n in nodes if n.type in ("ATTRIBUTE", "VERTEX_COLOR")]
    other = sorted({n.bl_label or n.type for n in nodes if n.type not in PROCEDURAL | PASS | {"TEX_IMAGE", "TEX_COORD", "UVMAP", "MAPPING", "ATTRIBUTE", "VERTEX_COLOR", "GROUP"}})
    kind = "procedural" if proc and not imgs else "texture" if imgs and not proc else "mixed" if imgs else "vertex colours" if attrs else "nodes"
    return {"kind": kind, "images": imgs, "procedural": proc, "adjust": other}

users = {}
for o in bpy.data.objects:
    for slot in getattr(o, "material_slots", []):
        if slot.material:
            users.setdefault(slot.material.name, set()).add(o.name)
mats = []
for m in bpy.data.materials:
    if m.name not in users:
        continue
    r = {"name": m.name, "objects": len(users[m.name]), "nodes": bool(m.use_nodes and m.node_tree), "inputs": {}, "shader": None,
         "blend": getattr(m, "blend_method", None) or getattr(m, "surface_render_method", None), "backface_culling": bool(m.use_backface_culling)}
    if r["nodes"]:
        out_node = next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
            or next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        surf = out_node.inputs["Surface"].links[0].from_node if out_node and out_node.inputs["Surface"].is_linked else None
        r["shader"] = surf.bl_label if surf else None
        bsdf = surf if surf and surf.type == "BSDF_PRINCIPLED" else next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        r["principled"] = bool(bsdf)
        if bsdf:
            for name, key in INPUTS:
                s = bsdf.inputs.get(name)
                if s is None or key in r["inputs"]:
                    continue
                d = describe(s)
                if d["kind"] == "value":     # off: not worth a line
                    if key == "normal":
                        continue
                    if key == "emission":
                        st = bsdf.inputs.get("Emission Strength")
                        if (st is not None and not st.is_linked and st.default_value == 0) or not any(d["value"][:3]):
                            continue
                    if key in ("transmission", "subsurface", "coat", "sheen") and not d["value"]:
                        continue
                r["inputs"][key] = d
        elif surf:
            d = describe(out_node.inputs["Surface"])
            r["inputs"]["surface"] = d
    else:
        c = list(m.diffuse_color)
        r["inputs"]["base_color"] = {"kind": "value", "value": [round(x, 3) for x in c]}
    mats.append(r)

dg = bpy.context.evaluated_depsgraph_get()
meshes, tris, verts = [], 0, 0
for o in bpy.context.scene.objects:
    if o.type != "MESH" or not o.visible_get():
        continue
    e = o.evaluated_get(dg)
    try:
        me = e.to_mesh()
    except Exception:
        continue
    me.calc_loop_triangles()
    t, v = len(me.loop_triangles), len(me.vertices)
    meshes.append({"name": o.name, "triangles": t, "vertices": v, "uv_maps": [u.name for u in me.uv_layers],
                   "colors": len(getattr(me, "color_attributes", [])), "materials": len([s for s in o.material_slots if s.material]),
                   "modifiers": [md.type.title().replace("_", " ") for md in o.modifiers if md.show_render]})
    tris += t; verts += v
    e.to_mesh_clear()
sc = bpy.context.scene
world = sc.world
env = None
if world and world.use_nodes and world.node_tree:
    env = next((n.image.name for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT" and n.image), None)
images = [image(i) for i in bpy.data.images if i.users and i.source in ("FILE", "SEQUENCE", "MOVIE", "TILED")]
report = {"blender": bpy.app.version_string, "materials": mats, "meshes": meshes, "triangles": tris, "vertices": verts,
          "objects": len(sc.objects), "camera": sc.camera.name if sc.camera else None,
          "lights": len([o for o in sc.objects if o.type == "LIGHT"]), "world": world.name if world else None, "environment": env,
          "images": images, "missing_images": [i["name"] for i in images if i["missing"]],
          "armatures": len([o for o in sc.objects if o.type == "ARMATURE"]), "actions": len(bpy.data.actions),
          "engine": sc.render.engine}
with open(out, "w", encoding="utf-8") as f:
    json.dump(report, f)
"""

# Runs inside Blender: a still picture of the file. The scene's camera when it has one (else a three-quarter
# view of everything visible); the file's own lights and world, or a neutral studio when it has none.
RENDER = r"""
import sys, math, bpy, mathutils
args = sys.argv[sys.argv.index("--") + 1:]
out, engine, width, own_camera = args[0], args[1], int(args[2]), args[3] == "1"
sc = bpy.context.scene
engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
if engine == "cycles":
    sc.render.engine = "CYCLES"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == kind]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == kind
                    sc.cycles.device = "GPU"
                    break
            except TypeError:
                continue
    except Exception:
        pass
    sc.cycles.samples = 96
    sc.cycles.use_adaptive_sampling = True
    try:
        sc.cycles.use_denoising = True
    except Exception:
        pass
else:
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    try:
        sc.eevee.taa_render_samples = 48
    except Exception:
        pass
KINDS = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"}
objs = [o for o in sc.objects if o.type in KINDS and o.visible_get() and not o.hide_render]
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
if not pts:
    raise SystemExit("nothing visible to render")
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
c = (lo + hi) / 2
r = max((hi - lo).length / 2, 1e-4)
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = width, width * 3 // 4, 100
if not (own_camera and sc.camera):
    cam = bpy.data.objects.new("KuratorCamera", bpy.data.cameras.new("KuratorCamera"))
    sc.collection.objects.link(cam)
    cam.data.lens = 50
    cam.data.sensor_fit = "HORIZONTAL"
    fov = min(cam.data.angle_x, cam.data.angle_y)
    dist = r / math.sin(fov / 2) * 1.08
    az, el = math.radians(35), math.radians(22)
    d = mathutils.Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = c + d * dist
    cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    # closer, until the nearest edge of what is visible reaches the frame (a sphere around it is too far)
    inv = cam.matrix_world.inverted()
    tx = math.tan(cam.data.angle_x / 2) * 0.94
    ty = math.tan(cam.data.angle_y / 2) * 0.94
    step = min(-q.z - max(abs(q.x) / tx, abs(q.y) / ty) for q in (inv @ p for p in pts))
    if step > 0:
        cam.location = cam.location + (c - cam.location).normalized() * step
    cam.data.clip_start, cam.data.clip_end = max(dist / 1000, 1e-4), dist + r * 4
    sc.camera = cam
lit = any(o.type == "LIGHT" and o.visible_get() and not o.hide_render for o in sc.objects)
world = sc.world
env = bool(world and world.use_nodes and world.node_tree and any(n.type in ("TEX_ENVIRONMENT", "TEX_SKY") for n in world.node_tree.nodes))
if not lit and not env:
    # a neutral studio: three sun lights and a dim grey world, on a transparent background
    for name, energy, az, el in (("Key", 3.2, 40, 45), ("Fill", 1.0, -70, 15), ("Rim", 2.4, 170, 35)):
        l = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
        l.data.energy = energy
        l.data.angle = math.radians(8)
        sc.collection.objects.link(l)
        a, e = math.radians(az), math.radians(el)
        l.rotation_euler = (-mathutils.Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))).to_track_quat("-Z", "Y").to_euler()
    w = bpy.data.worlds.new("KuratorStudio")
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.18, 0.18, 0.18, 1)
    bg.inputs["Strength"].default_value = 0.6
    sc.world = w
    sc.render.film_transparent = True
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGBA"
sc.render.filepath = out
try:
    bpy.ops.render.render(write_still=True)
except RuntimeError:
    if sc.render.engine != "CYCLES" or not sc.cycles.use_denoising:
        raise
    sc.cycles.use_denoising = False          # a Blender built without the denoiser
    bpy.ops.render.render(write_still=True)
"""


class BlenderError(RuntimeError):
    pass


def find() -> str | None:
    """Blender's executable: KURATOR_BLENDER, the PATH, or the usual install folders."""
    env = os.environ.get("KURATOR_BLENDER")
    if env == "off":
        return None
    if env and os.path.isfile(env):
        return env
    exe = shutil.which("blender")
    if exe:
        return exe
    cands: list[str] = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
            cands += glob.glob(os.path.join(base, "Steam", "steamapps", "common", "Blender", "blender.exe"))
    elif os.uname().sysname == "Darwin":
        cands += ["/Applications/Blender.app/Contents/MacOS/Blender"]
    else:
        cands += ["/snap/bin/blender", "/usr/local/bin/blender", "/opt/blender/blender"]
    cands = [c for c in cands if os.path.isfile(c)]

    def version(p: str) -> tuple:
        m = re.search(r"(\d+)\.(\d+)", p)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    return max(cands, key=version) if cands else None


def _run(exe: str, src: str, script_text: str, script_name: str, cache_dir: str, args: list[str], timeout: float) -> str:
    """Blender in the background on src with a script; returns what it printed (for errors)."""
    script = os.path.join(cache_dir, script_name)   # a file: multi-line arguments are fragile on Windows
    with open(script, "w", encoding="utf-8") as f:
        f.write(script_text)
    cmd = [exe, "--background", "--factory-startup", "--disable-autoexec", src, "--python-exit-code", "3",
           "--python", script, "--", *args]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=timeout, creationflags=_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired:
        raise BlenderError("Blender took too long") from None
    except OSError as e:
        raise BlenderError(f"Could not start Blender: {e}") from None
    return (r.stdout or b"").decode("utf-8", "replace")


def _tail(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip() and not ln.startswith(("Blender ", "Read blend", "Fra:"))]
    return " / ".join(lines[-3:])


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def report(src: str, h: str, cache_dir: str, timeout: float = 180) -> dict:
    """What the .blend holds: materials (textured, procedural, values - and what the preview keeps),
    meshes as exported, UV maps, lights, camera, world, images (missing ones). Cached by content hash."""
    exe = find()
    if not exe:
        raise BlenderError("Install Blender to look into .blend files (or set KURATOR_BLENDER to blender.exe)")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{h}.report.json")
    with _lock(h + ".report"):
        if not os.path.isfile(out):
            tmp = out + ".part"
            log = _run(exe, src, REPORT, "_kurator_report.py", cache_dir, [tmp], timeout)
            if not os.path.isfile(tmp):
                raise BlenderError("Blender could not read this file" + (": " + _tail(log) if _tail(log) else ""))
            os.replace(tmp, out)
        with open(out, encoding="utf-8") as f:
            return json.load(f)


RENDER_WIDTH = 1280


def render(src: str, h: str, cache_dir: str, engine: str = "eevee", own_camera: bool = True,
           timeout: float = 900) -> tuple[str, str]:
    """A still picture of the .blend, made by Blender: (PNG path, engine used). EEVEE needs a graphics
    card; without one (or if it fails) Cycles renders instead. Cached by content hash and engine."""
    exe = find()
    if not exe:
        raise BlenderError("Install Blender to render .blend files (or set KURATOR_BLENDER to blender.exe)")
    if engine not in ("eevee", "cycles"):
        raise BlenderError("Render with eevee or cycles")
    os.makedirs(cache_dir, exist_ok=True)
    cam = "cam" if own_camera else "auto"
    for eng in ([engine] if engine == "cycles" else ["eevee", "cycles"]):
        out = os.path.join(cache_dir, f"{h}.{eng}.{cam}.png")
        with _lock(f"{h}.{eng}.{cam}"):
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                return out, eng
            tmp = os.path.join(cache_dir, f"{h}.{eng}.{cam}.part.png")
            log = _run(exe, src, RENDER, "_kurator_render.py", cache_dir,
                       [tmp, eng, str(RENDER_WIDTH), "1" if own_camera else "0"], timeout)
            if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, out)
                return out, eng
    raise BlenderError("Blender could not render this file" + (": " + _tail(log) if _tail(log) else ""))


def to_glb(src: str, h: str, cache_dir: str, timeout: float = 300) -> str:
    """Convert src (.blend) to a cached .glb named after its content hash; returns the .glb path."""
    exe = find()
    if not exe:
        raise BlenderError("Install Blender to view .blend files in 3D (or set KURATOR_BLENDER to blender.exe)")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{h}.glb")
    with _locks_guard:
        lock = _locks.setdefault(h, threading.Lock())
    with lock:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        tmp = out + ".part.glb"
        script = os.path.join(cache_dir, "_kurator_export.py")   # a file: multi-line arguments are fragile on Windows
        with open(script, "w", encoding="utf-8") as f:
            f.write(EXPORT)
        args = [exe, "--background", "--factory-startup", "--disable-autoexec", src, "--python", script, "--", tmp]
        try:
            r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               timeout=timeout, creationflags=_NO_WINDOW, check=False)
        except subprocess.TimeoutExpired:
            raise BlenderError("Blender took too long to convert this file") from None
        except OSError as e:
            raise BlenderError(f"Could not start Blender: {e}") from None
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            raise BlenderError("Blender could not export this file" + (": " + " / ".join(tail) if tail else ""))
        os.replace(tmp, out)
        return out
