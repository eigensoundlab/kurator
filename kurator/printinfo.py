"""Desktop publishing and print: colour profiles, CMYK inks, soft proofing, PDF print facts.

* Pictures with an embedded ICC profile (CMYK, Adobe RGB, ProPhoto, Gray...) are converted to sRGB
  for previews with LittleCMS (in Pillow), so they look as they will print or as Photoshop shows
  them, not over-saturated or washed out.
* inks(): the C, M, Y, K plates of a CMYK picture - or, with a CMYK proofing profile chosen in the
  settings, of an RGB picture as it would be separated - plus a gamut mask for soft proofing.
* pdf_info(): page boxes (media, trim, bleed), PDF/X conformance, output intent, spot colours and
  fonts of a PDF / Illustrator file.
"""

from __future__ import annotations

import glob
import io
import os
import re
import zlib

import numpy as np
from PIL import Image, ImageCms

MAX_INK_SIDE = 1600
_SRGB = None


def _srgb():
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.createProfile("sRGB")
    return _SRGB


def icc_profile(data: bytes | None):
    if not data:
        return None
    try:
        return ImageCms.ImageCmsProfile(io.BytesIO(data))
    except (OSError, ImageCms.PyCMSError, TypeError, ValueError):
        return None


def describe(prof) -> str:
    try:
        return (ImageCms.getProfileDescription(prof) or "").strip()[:120]
    except (ImageCms.PyCMSError, TypeError, AttributeError):
        return ""


def colour_space(prof) -> str:
    try:
        return prof.profile.xcolor_space.strip()
    except AttributeError:
        return ""


def to_srgb(img: Image.Image) -> tuple[Image.Image, dict]:
    """The picture in sRGB through its embedded profile (alpha kept). Returns (image, facts)."""
    facts: dict = {"mode": img.mode}
    prof = icc_profile(img.info.get("icc_profile"))
    if prof is not None:
        facts["profile"] = describe(prof)
    if img.mode not in ("CMYK", "RGB", "RGBA", "L", "LA") or prof is None:
        return img, facts
    if facts.get("profile", "").lower().startswith("srgb"):
        return img, facts
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
    base = img.convert("RGB" if img.mode == "RGBA" else "L") if alpha is not None else img
    try:
        out = ImageCms.profileToProfile(base, prof, _srgb(), renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")
    except (ImageCms.PyCMSError, OSError, ValueError):
        return img, facts   # an odd profile: keep Pillow's own conversion
    if out is None:
        return img, facts
    facts["converted"] = True
    if alpha is not None:
        out.putalpha(alpha)
    return out, facts


# ---------------------------------------------------------------- CMYK proofing profiles
PROFILE_DIRS = [
    r"C:\Windows\System32\spool\drivers\color",
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Common Files\Adobe\Color\Profiles"),
    os.path.expandvars(r"%PROGRAMFILES%\Common Files\Adobe\Color\Profiles"),
    "/Library/ColorSync/Profiles", "/Library/Application Support/Adobe/Color/Profiles",
    os.path.expanduser("~/Library/ColorSync/Profiles"), "/System/Library/ColorSync/Profiles",
    "/usr/share/color/icc", "/usr/local/share/color/icc", os.path.expanduser("~/.local/share/color/icc"),
    os.path.expanduser("~/.color/icc"),
]


def cmyk_profiles(extra_dirs: list[str] | None = None) -> list[dict]:
    """CMYK output profiles installed on this computer (Adobe, ECI/FOGRA, printer drivers...)."""
    out, seen = [], set()
    for d in (extra_dirs or []) + PROFILE_DIRS:
        if not d or "%" in d or not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, "**", "*.ic[cm]"), recursive=True)[:2000]:
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                continue
            seen.add(key)
            try:
                if os.path.getsize(p) > 20 * 1024 * 1024:
                    continue
                with open(p, "rb") as f:
                    prof = icc_profile(f.read())
            except OSError:
                continue
            if prof is not None and colour_space(prof) == "CMYK":
                out.append({"path": p, "name": describe(prof) or os.path.basename(p)})
    out.sort(key=lambda x: x["name"].lower())
    return out


def _open_profile(path: str | None):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        prof = icc_profile(f.read())
    return prof if prof is not None and colour_space(prof) == "CMYK" else None


# ---------------------------------------------------------------- inks
def inks(path: str, proof_path: str | None = None, max_side: int = MAX_INK_SIDE) -> dict:
    """{'cmyk': (h, w, 4) uint8 ink amounts, 'gamut': (h, w) bool or None, 'source': 'file' | 'proof', ...}"""
    img = Image.open(path)
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    if img.format == "JPEG":
        img.draft(img.mode, (max_side, max_side))
    img.load()
    icc = img.info.get("icc_profile")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if img.mode == "CMYK":
        prof = icc_profile(icc)
        return {"cmyk": np.asarray(img, np.uint8), "gamut": None, "source": "file",
                "profile": describe(prof) if prof is not None else ""}
    proof = _open_profile(proof_path)
    if proof is None:
        raise ValueError("Ink views need a CMYK picture, or a CMYK proofing profile in ⚙ Settings for RGB pictures")
    src = icc_profile(icc) or _srgb()
    rgb = img.convert("RGB")
    try:
        orig = rgb if src is _srgb() else ImageCms.profileToProfile(rgb, src, _srgb(), outputMode="RGB")
        cmyk = ImageCms.profileToProfile(rgb, src, proof, renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="CMYK")
        back = ImageCms.profileToProfile(cmyk, proof, _srgb(), renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB")
        # gamut: colours that come back different from a colorimetric trip through the printer's space
        rc = ImageCms.profileToProfile(rgb, src, proof, renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="CMYK")
        rc_back = ImageCms.profileToProfile(rc, proof, _srgb(), renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB")
        to_lab = ImageCms.buildTransform(_srgb(), ImageCms.createProfile("LAB"), "RGB", "LAB")
        a = _lab(ImageCms.applyTransform(orig, to_lab))
        b = _lab(ImageCms.applyTransform(rc_back, to_lab))
    except (ImageCms.PyCMSError, OSError, ValueError) as e:
        raise ValueError(f"the proofing profile can't be used: {e}") from None
    delta_e = np.sqrt(((a - b) ** 2).sum(-1))
    return {"cmyk": np.asarray(cmyk, np.uint8), "gamut": delta_e > 6.0, "source": "proof",
            "profile": describe(proof), "proof": back}


def _lab(img: Image.Image) -> np.ndarray:
    """Pillow's LAB image (L 0..255, a/b signed bytes) as float L*a*b*."""
    raw = np.asarray(img, np.uint8)
    out = np.empty(raw.shape, np.float32)
    out[..., 0] = raw[..., 0] * (100 / 255)
    out[..., 1:] = raw[..., 1:].view(np.int8)
    return out


def proof_image(path: str, proof_path: str | None, max_side: int = MAX_INK_SIDE) -> Image.Image:
    r = inks(path, proof_path, max_side)
    if r.get("proof") is not None:
        return r["proof"]
    raise ValueError("this picture needs no proof (it is CMYK already)")


# ---------------------------------------------------------------- PDF
_SEP = re.compile(rb"/Separation\s*/([^\s/\[\]<>()]+)")
_DEVN = re.compile(rb"/DeviceN\s*\[([^\]]{1,2000})\]")
_FONT = re.compile(rb"/BaseFont\s*/([^\s/\[\]<>()]+)")
_STREAM = re.compile(rb"<<([^>]{0,500}?/FlateDecode[^>]{0,500}?)>>\s*stream\r?\n", re.S)
NOT_INK = {"All", "None", "Cyan", "Magenta", "Yellow", "Black"}


def _name(b: bytes) -> str:
    s = b.decode("latin-1")
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)


def _scan(blob: bytes, spots: set, fonts: set) -> None:
    for m in _SEP.finditer(blob):
        spots.add(_name(m.group(1)))
    for m in _DEVN.finditer(blob):
        for n in re.findall(rb"/([^\s/\[\]<>()]+)", m.group(1)):
            spots.add(_name(n))
    for m in _FONT.finditer(blob):
        fonts.add(_name(m.group(1)))


def pdf_info(data: bytes, page) -> dict:
    """Print facts from the raw file and from pdfium's first page."""
    out: dict = {}
    head = data[:16]
    m = re.match(rb"%PDF-(\d\.\d)", head)
    if m:
        out["pdf_version"] = m.group(1).decode()
    pdfx = re.search(rb"GTS_PDFXVersion\s*(?:>|\()\s*(PDF/X-[0-9a-zA-Z:]+)", data[:4_000_000]) or \
        re.search(rb"<pdfxid:GTS_PDFXVersion>(PDF/X-[^<]+)<", data[:8_000_000])
    if pdfx:
        out["pdfx"] = pdfx.group(1).decode("latin-1")
    if b"/OutputIntents" in data[:8_000_000] or b"/OutputIntents" in data[-2_000_000:]:
        out["output_intent"] = True
        oi = re.search(rb"/OutputConditionIdentifier\s*\(([^)]{1,80})\)", data)
        if oi:
            out["output_condition"] = oi.group(1).decode("latin-1")
    spots: set = set()
    fonts: set = set()
    _scan(data, spots, fonts)
    budget = 64 * 1024 * 1024   # also look inside compressed object streams (not page content)
    for sm in _STREAM.finditer(data):
        d = sm.group(1)
        if b"/ObjStm" not in d:
            continue
        try:
            raw = zlib.decompressobj().decompress(data[sm.end(): sm.end() + 8 * 1024 * 1024], 16 * 1024 * 1024)
        except zlib.error:
            continue
        budget -= len(raw)
        _scan(raw, spots, fonts)
        if budget <= 0:
            break
    spots = {s for s in spots if s not in NOT_INK}
    if spots:
        out["spot_colours"] = sorted(spots)[:40]
    if fonts:
        clean = sorted({re.sub(r"^[A-Z]{6}\+", "", f) for f in fonts})
        out["fonts"] = clean[:60]
        out["fonts_subset"] = any(re.match(r"^[A-Z]{6}\+", f) for f in fonts)
    try:
        mm = 25.4 / 72
        crop = page.get_cropbox()
        boxes = {"media": page.get_mediabox(), "crop": crop}
        for name, fn in (("trim", page.get_trimbox), ("bleed", page.get_bleedbox)):
            try:
                b = fn(fallback_ok=False)
            except TypeError:
                b = None
            if b is not None:
                boxes[name] = b
        out["boxes_mm"] = {k: [round((b[2] - b[0]) * mm, 1), round((b[3] - b[1]) * mm, 1)] for k, b in boxes.items()}
        # relative to the rendered area (the crop box), y from the top: for drawing over the preview
        cw, ch = crop[2] - crop[0], crop[3] - crop[1]
        if cw > 0 and ch > 0:
            out["boxes_rel"] = {k: [round((b[0] - crop[0]) / cw, 5), round((crop[3] - b[3]) / ch, 5),
                                    round((b[2] - b[0]) / cw, 5), round((b[3] - b[1]) / ch, 5)]
                                for k, b in boxes.items() if k in ("trim", "bleed", "media")}
        if "trim" in boxes and "bleed" in boxes:
            t, b = boxes["trim"], boxes["bleed"]
            out["bleed_mm"] = round(min(t[0] - b[0], t[1] - b[1], b[2] - t[2], b[3] - t[3]) * mm, 1)
    except Exception:
        pass
    return out
