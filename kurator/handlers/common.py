"""Shared helpers for handlers: embedded previews, placeholder cards, Windows shell thumbnails, macOS converters."""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw, ImageFont

MAX_MEMBER = 256 * 1024 * 1024
_XMP_THUMB = re.compile(rb"<xmpGImg:image>([^<]{100,})</xmpGImg:image>|xmpGImg:image=\"([^\"]{100,})\"")


def xmp_thumbnail(data: bytes) -> Image.Image | None:
    """Most Adobe files (InDesign, Illustrator, EPS, PSD...) embed a small JPEG preview in their XMP."""
    best = None
    for m in _XMP_THUMB.finditer(data):
        raw = (m.group(1) or m.group(2)).replace(b"&#xA;", b"").replace(b"&#xD;", b"")
        try:
            img = Image.open(io.BytesIO(base64.b64decode(re.sub(rb"\s", b"", raw))))
            img.load()
        except Exception:
            continue
        if best is None or img.width * img.height > best.width * best.height:
            best = img
    return best


def zip_image(data: bytes, names: list[str] | None = None, prefix: str | None = None) -> Image.Image | None:
    """Preview image stored inside a ZIP-based file (Procreate, CorelDRAW X4+, OpenRaster...)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    with z:
        cands = []
        for info in z.infolist():
            n = info.filename
            if (names and n in names) or (prefix and n.lower().startswith(prefix.lower())):
                if info.file_size <= MAX_MEMBER:
                    cands.append(info)
        cands.sort(key=lambda i: (names.index(i.filename) if names and i.filename in names else 99, -i.file_size))
        for info in cands:
            try:
                img = Image.open(io.BytesIO(z.read(info)))
                img.load()
                return img
            except Exception:
                continue
    return None


def placeholder(ext: str, note: str = "") -> Image.Image:
    """A neutral card for files without a usable preview - they can still be tagged, moved, backed up."""
    img = Image.new("RGB", (512, 512), (36, 40, 50))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((136, 96, 376, 416), radius=18, outline=(110, 120, 140), width=6)
    d.polygon([(316, 96), (376, 156), (316, 156)], fill=(110, 120, 140))
    label = ext.lstrip(".").upper()[:7]
    try:
        font = ImageFont.load_default(size={1: 72, 2: 72, 3: 72, 4: 64, 5: 52, 6: 44}.get(len(label), 38))
        small = ImageFont.load_default(size=26)
    except TypeError:  # very old Pillow
        font = small = ImageFont.load_default()
    d.text((256, 280), label, fill=(200, 210, 230), font=font, anchor="mm")
    if note:
        d.text((256, 460), note[:40], fill=(140, 150, 170), font=small, anchor="mm")
    return img


# ---------------------------------------------------------------- macOS
# macOS opens HEIC and PDF itself. When the packages Kurator uses for them have no build for this Python or
# macOS version (see tools/install_extras.py), these built-in tools stand in.
def mac_has(tool: str) -> bool:
    return sys.platform == "darwin" and shutil.which(tool) is not None


def mac_convert(path: str, timeout: float = 60) -> Image.Image | None:
    """A picture macOS can open (HEIC, HEIF...) as JPEG made by sips, with its metadata (dates).
    macOS only; None elsewhere or when sips can't."""
    if not mac_has("sips"):
        return None
    fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="kurator-sips-")
    os.close(fd)
    try:
        r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "best", path, "--out", tmp],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           timeout=timeout, check=False)
        if r.returncode or not os.path.getsize(tmp):
            return None
        with Image.open(tmp) as im:
            im.load()
            return im.copy()          # info (EXIF: the dates) comes along
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def quicklook_thumbnail(path: str, size: int = 1024, timeout: float = 60) -> Image.Image | None:
    """The thumbnail Finder shows (Quick Look, qlmanage): for PDFs the first page. macOS only."""
    if not mac_has("qlmanage"):
        return None
    with tempfile.TemporaryDirectory(prefix="kurator-ql-") as out:
        try:
            subprocess.run(["qlmanage", "-t", "-s", str(int(size)), "-o", out, path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        made = [f for f in os.listdir(out) if f.lower().endswith(".png")]
        if not made:
            return None
        try:
            with Image.open(os.path.join(out, made[0])) as im:
                im.load()
                return im.copy()
        except OSError:
            return None


# ---------------------------------------------------------------- Windows
def shell_thumbnail(path: str, size: int = 512) -> Image.Image | None:
    """Ask Windows for the thumbnail Explorer shows - works for any format whose application installs
    a thumbnail handler (Affinity, CorelDRAW, Office...). Windows only; returns None elsewhere."""
    if os.name != "nt":
        return None
    try:
        return _shell_thumbnail_win(path, size)
    except Exception:
        return None


def _shell_thumbnail_win(path: str, size: int) -> Image.Image | None:
    import ctypes
    from ctypes import POINTER, Structure, byref, c_void_p, wintypes

    ole32, shell32, gdi32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.gdi32

    class GUID(Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def guid(s: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(s), byref(g))
        return g

    class SIZE(Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAP(Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", c_void_p)]

    class BITMAPINFOHEADER(Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    ole32.CoInitializeEx(None, 0x2)  # apartment-threaded; harmless if already initialised
    iid = guid("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")  # IShellItemImageFactory
    factory = c_void_p()
    hr = shell32.SHCreateItemFromParsingName(ctypes.c_wchar_p(os.path.abspath(path)), None, byref(iid), byref(factory))
    if hr != 0 or not factory:
        return None
    vtbl = ctypes.cast(factory, POINTER(POINTER(c_void_p))).contents
    GetImage = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, SIZE, ctypes.c_int, POINTER(wintypes.HBITMAP))(vtbl[3])
    Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[2])
    hbmp = wintypes.HBITMAP()
    try:
        # SIIGBF_THUMBNAILONLY (0x08): never return the generic file-type icon
        GetImage(factory, SIZE(size, size), 0x08, byref(hbmp))
    except OSError:
        Release(factory)
        return None
    Release(factory)
    if not hbmp:
        return None
    try:
        bm = BITMAP()
        gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = ctypes.windll.user32.GetDC(None)
        gdi32.GetDIBits(hdc, hbmp, 0, h, buf, byref(bih), 0)
        ctypes.windll.user32.ReleaseDC(None, hdc)
        return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGBA")
    finally:
        gdi32.DeleteObject(hbmp)
