"""Unity sprite import settings (.meta files next to the textures).

* For sprite sheets and atlases saved into a Unity project: a new .meta with Sprite Mode = Multiple
  and the sprite rectangles (trimmed sprites keep their original pivot), so Unity slices it on import.
* For pictures already in a Unity project: set texture type Sprite, pixels per unit, filter (Point
  for pixel art), compression and mip maps. Existing .meta files keep their GUID (every reference
  in scenes and prefabs points at it) and all other settings; the old files are kept in Kurator's
  data folder so the change can be undone. Normal and mask maps named like the picture
  (hero_normal.png, hero_mask.png) are linked as the sprite's secondary textures (_NormalMap,
  _MaskMap) for 2D lights.

Unity notices the changed .meta files and re-imports the textures when its window gets focus.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid

FILTER = {"point": 0, "bilinear": 1, "trilinear": 2}
COMPRESSION = {"none": 0, "normal": 1, "high": 2, "low": 3}
TEX_SIZES = [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]


class UnityError(ValueError):
    pass


def new_guid() -> str:
    return uuid.uuid4().hex


def _internal_id(name: str) -> int:
    v = int.from_bytes(hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest(), "little") & 0x7FFFFFFFFFFFFFFF
    return v or 21300000


def _max_size(w: int, h: int) -> int:
    m = max(w, h)
    return next((s for s in TEX_SIZES if s >= m), 16384)


def sprite_entries(sprites, height: int, mode: str) -> list[dict]:
    """Unity rectangles (origin bottom left) and pivots for atlas.Sprite objects."""
    out, seen = [], set()
    for s in sprites:
        name = os.path.splitext(s.name)[0]
        base, k = name, 2
        while name in seen:
            name = f"{base}_{k}"
            k += 1
        seen.add(name)
        w, h = s.img.size
        y = height - s.y - h
        pivot, alignment = (0.5, 0.5), 0
        if mode == "atlas" and (s.off_x or s.off_y or (w, h) != (s.src_w, s.src_h)):
            bottom = s.src_h - s.off_y - h   # keep the pivot at the centre of the untrimmed picture
            pivot = (round((s.src_w / 2 - s.off_x) / w, 6), round((s.src_h / 2 - bottom) / h, 6))
            alignment = 9
        out.append({"name": name, "x": s.x, "y": y, "w": w, "h": h, "pivot": pivot, "alignment": alignment})
    return out


def texture_meta(guid: str, width: int, height: int, *, sprite_mode: int = 1, ppu: float = 100, filter_mode: int = 1,
                 compression: int = 1, mipmaps: bool = False, sprites: list[dict] | None = None,
                 texture_type: int = 8, secondary: list[tuple[str, str]] | None = None) -> str:
    """A complete TextureImporter .meta (Unity 2021.3 and newer read it and fill in anything new)."""
    lines = ["fileFormatVersion: 2", f"guid: {guid}", "TextureImporter:", "  internalIDToNameTable: []",
             "  externalObjects: {}", "  serializedVersion: 13", "  mipmaps:", "    mipMapMode: 0",
             f"    enableMipMap: {1 if mipmaps else 0}", f"    sRGBTexture: {0 if texture_type == 1 else 1}", "    linearTexture: 0",
             "    fadeOut: 0", "    borderMipMap: 0", "    mipMapsPreserveCoverage: 0", "    alphaTestReferenceValue: 0.5",
             "    mipMapFadeDistanceStart: 1", "    mipMapFadeDistanceEnd: 3", "  bumpmap:", "    convertToNormalMap: 0",
             "    externalNormalMap: 0", "    heightScale: 0.25", "    normalMapFilter: 0", "    flipGreenChannel: 0",
             "  isReadable: 0", "  streamingMipmaps: 0", "  streamingMipmapsPriority: 0", "  vTOnly: 0",
             "  ignoreMipmapLimit: 0", "  grayScaleToAlpha: 0", "  generateCubemap: 6", "  cubemapConvolution: 0",
             "  seamlessCubemap: 0", "  textureFormat: 1", "  maxTextureSize: 2048", "  textureSettings:",
             "    serializedVersion: 2", f"    filterMode: {filter_mode}", "    aniso: 1", "    mipBias: 0",
             "    wrapU: 1", "    wrapV: 1", "    wrapW: 1", "  nPOTScale: 0", "  lightmap: 0", "  compressionQuality: 50",
             f"  spriteMode: {sprite_mode}", "  spriteExtrude: 1", "  spriteMeshType: 1", "  alignment: 0",
             "  spritePivot: {x: 0.5, y: 0.5}", f"  spritePixelsToUnits: {ppu:g}", "  spriteBorder: {x: 0, y: 0, z: 0, w: 0}",
             "  spriteGenerateFallbackPhysicsShape: 1", "  alphaUsage: 1", f"  alphaIsTransparency: {1 if texture_type == 8 else 0}",
             "  spriteTessellationDetail: -1", f"  textureType: {texture_type}", "  textureShape: 1",
             "  singleChannelComponent: 0", "  flipbookRows: 1", "  flipbookColumns: 1", "  maxTextureSizeSet: 0",
             "  compressionQualitySet: 0", "  textureFormatSet: 0", "  ignorePngGamma: 0", "  applyGammaDecoding: 0",
             "  swizzle: 50462976", "  cookieLightType: 0", "  platformSettings:", "  - serializedVersion: 4",
             "    buildTarget: DefaultTexturePlatform", f"    maxTextureSize: {_max_size(width, height)}", "    resizeAlgorithm: 0",
             "    textureFormat: -1", f"    textureCompression: {compression}", "    compressionQuality: 50",
             "    crunchedCompression: 0", "    allowsAlphaSplitting: 0", "    overridden: 0", "    ignorePlatformSupport: 0",
             "    androidETC2FallbackOverride: 0", "    forceMaximumCompressionQuality_BC6H_BC7: 0",
             "  spriteSheet:", "    serializedVersion: 2"]
    ids = {}
    if sprites:
        lines.append("    sprites:")
        for sp in sprites:
            iid = _internal_id(sp["name"])
            ids[sp["name"]] = iid
            lines += ["    - serializedVersion: 2", f"      name: {_yaml_name(sp['name'])}", "      rect:", "        serializedVersion: 2",
                      f"        x: {sp['x']}", f"        y: {sp['y']}", f"        width: {sp['w']}", f"        height: {sp['h']}",
                      f"      alignment: {sp['alignment']}", f"      pivot: {{x: {sp['pivot'][0]:g}, y: {sp['pivot'][1]:g}}}",
                      "      border: {x: 0, y: 0, z: 0, w: 0}", "      customData: ", "      outline: []", "      physicsShape: []",
                      "      tessellationDetail: -1", "      bones: []", f"      spriteID: {new_guid()}", f"      internalID: {iid}",
                      "      vertices: []", "      indices: ", "      edges: []", "      weights: []"]
    else:
        lines.append("    sprites: []")
    lines += ["    outline: []", "    customData: ", "    physicsShape: []", "    bones: []",
              f"    spriteID: {new_guid() if sprite_mode == 1 else ''}".rstrip(), "    internalID: 0", "    vertices: []",
              "    indices: ", "    edges: []", "    weights: []"]
    lines += _secondary_lines(secondary)
    lines += ["    spriteCustomMetadata:", "      entries: []"]
    if ids:
        lines.append("    nameFileIdTable:")
        lines += [f"      {_yaml_name(n)}: {i}" for n, i in ids.items()]
    else:
        lines.append("    nameFileIdTable: {}")
    lines += ["  mipmapLimitGroupName: ", "  pSDRemoveMatte: 0", "  userData: ", "  assetBundleName: ", "  assetBundleVariant: "]
    return "\n".join(lines) + "\n"


def _secondary_lines(secondary) -> list[str]:
    if not secondary:
        return ["    secondaryTextures: []"]
    out = ["    secondaryTextures:"]
    for name, guid in secondary:
        out += [f"    - name: {name}", f"      texture: {{fileID: 2800000, guid: {guid}, type: 3}}"]
    return out


def _yaml_name(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9_\-. ]+", s) and not s[0].isspace() else json.dumps(s)


def read_guid(meta_path: str) -> str | None:
    try:
        with open(meta_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"guid:\s*([0-9a-f]{32})", line)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def write_sheet_meta(png: str, width: int, height: int, sprites, mode: str, ppu: float = 100,
                     filter_mode: str = "bilinear") -> str:
    """The .meta for a sprite sheet / atlas just saved (keeps the GUID if the PNG was there before)."""
    meta = png + ".meta"
    guid = read_guid(meta) or new_guid()
    text = texture_meta(guid, width, height, sprite_mode=2, ppu=ppu, filter_mode=FILTER.get(filter_mode, 1),
                        compression=0 if filter_mode == "point" else 1, sprites=sprite_entries(sprites, height, mode))
    with open(meta, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return meta


# ---------------------------------------------------------------- settings for existing textures
_SET = {   # setting -> (regex for the line, replacement template)
    "textureType": (r"^(  textureType: )-?\d+", r"\g<1>{v}"),
    "spriteMode": (r"^(  spriteMode: )\d+", r"\g<1>{v}"),
    "ppu": (r"^(  spritePixelsToUnits: )[-0-9.e]+", r"\g<1>{v}"),
    "alphaIsTransparency": (r"^(  alphaIsTransparency: )\d", r"\g<1>{v}"),
    "filterMode": (r"^(    filterMode: )-?\d+", r"\g<1>{v}"),
    "enableMipMap": (r"^(    enableMipMap: )\d", r"\g<1>{v}"),
    "textureCompression": (r"^(    textureCompression: )\d", r"\g<1>{v}"),
}


def edit_meta(text: str, values: dict) -> str:
    for key, v in values.items():
        rx, rep = _SET[key]
        text, n = re.subn(rx, rep.format(v=v), text, flags=re.M)
        if not n and key in ("textureType", "spriteMode", "ppu", "alphaIsTransparency"):
            name = {"ppu": "spritePixelsToUnits"}.get(key, key)
            text = re.sub(r"^(TextureImporter:\n)", rf"\g<1>  {name}: {v}\n", text, count=1, flags=re.M)
    return text


def set_secondary(text: str, secondary: list[tuple[str, str]]) -> str:
    block = "\n".join(_secondary_lines(secondary)) + "\n"
    rx = re.compile(r"^    secondaryTextures:.*?\n(?=    [^\s-]|  \S|\S|\Z)", re.M | re.S)
    if rx.search(text):
        return rx.sub(block, text, count=1)
    if re.search(r"^  spriteSheet:\n", text, re.M):
        return re.sub(r"^(  spriteSheet:\n(?:    serializedVersion: \d+\n)?)", lambda m: m.group(1) + block, text, count=1, flags=re.M)
    return text


def apply_settings(files: list[dict], data_dir: str, *, sprite: bool = True, ppu: float = 100, filter_mode: str = "bilinear",
                   compression: str = "normal", mipmaps: bool = False, link_maps: bool = True) -> dict:
    """files: [{"path", "width", "height", "maps": {"normal": path, "mask": path}}]. Returns a summary
    with the backup id for undo."""
    backup_id = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    bdir = os.path.join(data_dir, "unity-meta-backup", backup_id)
    changed, created, skipped = [], [], []
    manifest = []
    values = {"filterMode": FILTER.get(filter_mode, 1), "enableMipMap": int(mipmaps),
              "textureCompression": COMPRESSION.get(compression, 1)}
    if sprite:
        values.update({"textureType": 8, "spriteMode": 1, "ppu": f"{float(ppu):g}", "alphaIsTransparency": 1})

    def guid_for_map(path: str, normal: bool) -> str | None:
        meta = path + ".meta"
        g = read_guid(meta)
        if g:
            return g
        g = new_guid()
        with open(meta, "w", encoding="utf-8", newline="\n") as f:
            f.write(texture_meta(g, 1024, 1024, sprite_mode=0, texture_type=1 if normal else 0, filter_mode=values["filterMode"],
                                 compression=values["textureCompression"]))
        created.append(meta)
        manifest.append({"meta": meta, "backup": None})
        return g

    for f in files:
        path = f["path"]
        meta = path + ".meta"
        secondary = []
        if link_maps and sprite:
            for role, slot in (("normal", "_NormalMap"), ("mask", "_MaskMap")):
                mp = (f.get("maps") or {}).get(role)
                if mp and os.path.isfile(mp):
                    g = guid_for_map(mp, role == "normal")
                    if g:
                        secondary.append((slot, g))
        if os.path.isfile(meta):
            with open(meta, encoding="utf-8", errors="replace") as fh:
                old = fh.read()
            if "TextureImporter:" not in old:
                skipped.append(path)
                continue
            new = edit_meta(old, values)
            if secondary:
                new = set_secondary(new, secondary)
            if new != old:
                os.makedirs(bdir, exist_ok=True)
                keep = os.path.join(bdir, f"{len(manifest):05d}.meta")
                shutil.copy2(meta, keep)
                with open(meta, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
                manifest.append({"meta": meta, "backup": keep})
                changed.append(path)
        else:
            text = texture_meta(new_guid(), f.get("width") or 1024, f.get("height") or 1024, sprite_mode=1 if sprite else 0,
                                ppu=float(ppu), filter_mode=values["filterMode"], compression=values["textureCompression"],
                                mipmaps=mipmaps, texture_type=8 if sprite else 0, secondary=secondary)
            with open(meta, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            manifest.append({"meta": meta, "backup": None})
            created.append(path)
    if manifest:
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    return {"changed": len(changed), "created": len(created), "skipped": skipped[:50], "undo": backup_id if manifest else None}


def undo(data_dir: str, backup_id: str) -> int:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+", backup_id or ""):
        raise UnityError("unknown change")
    bdir = os.path.join(data_dir, "unity-meta-backup", backup_id)
    try:
        with open(os.path.join(bdir, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
    except OSError:
        raise UnityError("this change can't be undone any more") from None
    n = 0
    for e in reversed(manifest):
        if e["backup"]:
            shutil.copy2(e["backup"], e["meta"])
        elif os.path.isfile(e["meta"]):
            os.remove(e["meta"])
        n += 1
    shutil.rmtree(bdir, ignore_errors=True)
    return n
