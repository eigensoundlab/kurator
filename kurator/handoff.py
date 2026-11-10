"""Hand several files to a program in one piece: sounds as the tracks of a REAPER project, pictures as
the layers of one OpenRaster file (.ora - Krita, GIMP and MyPaint open it with its layers).

The files are written to Kurator's cache (handoff/, kept a week). Save the project or picture from
the program where you want it.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from xml.sax.saxutils import quoteattr

from PIL import Image

KEEP_DAYS = 7


def folder(data_dir: str) -> str:
    d = os.path.join(data_dir, "cache", "handoff")
    os.makedirs(d, exist_ok=True)
    now = time.time()
    for n in os.listdir(d):   # last week's hand-offs go
        p = os.path.join(d, n)
        try:
            if now - os.path.getmtime(p) > KEEP_DAYS * 86400:
                os.remove(p)
        except OSError:
            pass
    return d


def _name(stem: str, n: int, what: str, ext: str) -> str:
    return f"Kurator {time.strftime('%Y-%m-%d %H%M%S')} - {stem}{f' +{n - 1}' if n > 1 else ''} {what}{ext}"


# ----------------------------------------------------------------- REAPER
_SOURCE = {".mp3": "MP3", ".ogg": "VORBIS", ".oga": "VORBIS", ".opus": "OPUS", ".flac": "FLAC"}


def _rpp_str(s: str) -> str:
    """A string in a REAPER project: quoted with ", ' or ` - whichever the string doesn't contain."""
    for q in ('"', "'", "`"):
        if q not in s:
            return f"{q}{s}{q}"
    return '"' + s.replace('"', "'") + '"'


def reaper_project(sounds: list[tuple[str, float | None]], out_dir: str) -> str:
    """One track per sound, each starting at 0; returns the .rpp path."""
    lines = [f'<REAPER_PROJECT 0.1 "7.0/Kurator" {int(time.time())}', "  TEMPO 120 4 4"]
    for path, duration in sounds:
        name = os.path.basename(path)
        kind = _SOURCE.get(os.path.splitext(path)[1].lower(), "WAVE")
        lines += ["  <TRACK", f"    NAME {_rpp_str(os.path.splitext(name)[0])}", "    <ITEM", "      POSITION 0",
                  f"      LENGTH {max(float(duration or 0), 0.001) if duration else 60:.6f}", "      LOOP 0",
                  f"      NAME {_rpp_str(name)}", f"      <SOURCE {kind}", f"        FILE {_rpp_str(path)}", "      >",
                  "    >", "  >"]
    lines.append(">")
    stem = os.path.splitext(os.path.basename(sounds[0][0]))[0]
    out = os.path.join(out_dir, _name(stem, len(sounds), "tracks", ".rpp"))
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return out


# ------------------------------------------------------------ OpenRaster
def openraster(paths: list[str], out_dir: str, max_side: int = 8192) -> str:
    """The pictures as layers of one .ora, the first on top, at the top left of a canvas as large as
    the largest; returns the path."""
    from .processing import render_png
    layers = []
    for p in paths:
        png = render_png(p, max_side)
        with Image.open(io.BytesIO(png)) as im:
            layers.append((os.path.splitext(os.path.basename(p))[0], png, im.size))
    w = max(s[0] for _, _, s in layers)
    h = max(s[1] for _, _, s in layers)
    stack = "".join(f'<layer name={quoteattr(n)} src="data/layer{i}.png" x="0" y="0" opacity="1.0" visibility="visible"/>'
                    for i, (n, _, _) in enumerate(layers))
    xml = f"<?xml version='1.0' encoding='UTF-8'?>\n<image w=\"{w}\" h=\"{h}\" version=\"0.0.3\"><stack>{stack}</stack></image>\n"
    stem = layers[0][0]
    out = os.path.join(out_dir, _name(stem, len(layers), "layers", ".ora"))
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", compress_type=zipfile.ZIP_STORED)   # first, stored
        z.writestr("stack.xml", xml, compress_type=zipfile.ZIP_DEFLATED)
        for i, (_, png, _) in enumerate(layers):
            z.writestr(f"data/layer{i}.png", png, compress_type=zipfile.ZIP_STORED)
        # the picture as a whole, for programs that show it before opening (bottom layer first)
        merged = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if w * h * len(layers) <= 400_000_000:
            for _, png, _ in reversed(layers):
                with Image.open(io.BytesIO(png)) as im:
                    merged.alpha_composite(im.convert("RGBA"))
        thumb = merged.copy()
        thumb.thumbnail((256, 256))
        for name, im in (("mergedimage.png", merged), ("Thumbnails/thumbnail.png", thumb)):
            buf = io.BytesIO()
            im.save(buf, "PNG", compress_level=1)
            z.writestr(name, buf.getvalue(), compress_type=zipfile.ZIP_STORED)
    return out
