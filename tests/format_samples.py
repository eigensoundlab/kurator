"""Builds one small sample file per supported format (used by the tests and tools)."""

import base64
import gzip
import io
import math
import os
import sqlite3
import struct
import tempfile
import zipfile

from PIL import Image


def _png(color="red", size=(64, 48)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def _jpeg(color="blue", size=(80, 60)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def _xmp_with_thumb() -> bytes:
    b64 = base64.b64encode(_jpeg("green", (120, 90))).decode()
    b64 = "&#xA;".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    return (b'<x:xmpmeta><rdf:li><xmpGImg:width>120</xmpGImg:width><xmpGImg:image>' + b64.encode()
            + b'</xmpGImg:image></rdf:li></x:xmpmeta>')


def _wav_with_bext(path: str) -> None:
    import numpy as np
    import soundfile as sf
    t = np.linspace(0, 0.5, 22050, endpoint=False)
    sf.write(path, (0.5 * np.sin(2 * math.pi * 440 * t)).astype("float32"), 44100, subtype="PCM_16")
    data = open(path, "rb").read()
    body = bytearray(602)
    body[0:24] = b"Wooden door creak, slow".ljust(24, b"\x00")
    body[256:268] = b"Test Studio\x00"
    body[320:330] = b"2019-04-12"
    body[330:338] = b"15:30:00"
    chunk = b"bext" + struct.pack("<I", len(body)) + bytes(body)
    new = data[:12] + chunk + data[12:]
    new = new[:4] + struct.pack("<I", len(new) - 8) + new[8:]
    open(path, "wb").write(new)


def make_all(folder: str) -> dict:
    """Write samples into folder. Returns {filename: expected group}."""
    os.makedirs(folder, exist_ok=True)
    out = {}

    def w(name, data, group):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        out[name] = group

    img = Image.new("RGBA", (64, 48), (200, 50, 50, 255))
    for fmt, ext, group in (("TGA", "tga", "texture"), ("DDS", "dds", "texture"), ("ICO", "ico", "image"),
                            ("PCX", "pcx", "image"), ("SGI", "sgi", "image"), ("PPM", "ppm", "image"),
                            ("JPEG2000", "jp2", "image"), ("QOI", "qoi", "image")):
        b = io.BytesIO()
        try:
            (img if fmt in ("TGA", "DDS", "ICO", "QOI") else img.convert("RGB")).save(b, fmt)
        except Exception:
            continue  # this Pillow build can't write it - reading is what matters
        w(f"sample.{ext}", b.getvalue(), group)
    w("vector.svg", b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                    b'width="200" height="100" viewBox="0 0 200 100"><rect width="200" height="100" fill="#3a6"/>'
                    b'<circle cx="100" cy="50" r="40" fill="#fc0"/></svg>', "vector")
    w("vector.svgz", gzip.compress(open(os.path.join(folder, "vector.svg"), "rb").read()), "vector")
    b = io.BytesIO()
    Image.new("RGB", (300, 200), "purple").save(b, "PDF")
    w("doc.pdf", b.getvalue(), "document")
    w("art.ai", b.getvalue(), "vector")  # modern .ai = PDF-compatible
    # DOS EPS with an embedded TIFF preview
    tif = io.BytesIO()
    Image.new("RGB", (50, 40), "orange").save(tif, "TIFF")
    ps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 50 40\nshowpage\n"
    header = struct.pack("<4sIIIIIIH", b"\xc5\xd0\xd3\xc6", 30, len(ps), 0, 0, 30 + len(ps), len(tif.getvalue()), 0xFFFF)
    w("logo.eps", header + ps + tif.getvalue(), "vector")
    w("layout.indd", b"\x06\x06\xed\xf5" + b"\x00" * 64 + _xmp_with_thumb() + b"\x00" * 64, "document")
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("content/root.dat", b"x")
        b = io.BytesIO(); Image.new("RGB", (90, 60), "teal").save(b, "BMP")
        zf.writestr("metadata/thumbnails/thumbnail.bmp", b.getvalue())
    w("drawing.cdr", z.getvalue(), "vector")
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Document.archive", b"x")
        zf.writestr("QuickLook/Thumbnail.png", _png("pink", (100, 75)))
    w("painting.procreate", z.getvalue(), "work")
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("document.json", b"{}")
        zf.writestr("previews/preview.png", _png("navy", (120, 80)))
    w("ui.sketch", z.getvalue(), "vector")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.sqlite")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE CanvasPreview (ImageData BLOB, ImageWidth INT, ImageHeight INT)")
        con.execute("CREATE TABLE Canvas (CanvasWidth REAL, CanvasHeight REAL)")
        con.execute("INSERT INTO CanvasPreview VALUES (?, 110, 70)", (_png("olive", (110, 70)),))
        con.execute("INSERT INTO Canvas VALUES (2480, 3508)")
        con.commit(); con.close()
        db = open(p, "rb").read()
    w("comic.clip", b"CSFCHUNK" + b"\x00" * 16 + b"CHNKHead" + struct.pack(">Q", 8) + b"\x00" * 8
      + b"CHNKSQLi" + struct.pack(">Q", len(db)) + db + b"CHNKFoot" + struct.pack(">Q", 0), "work")
    w("photo.afphoto", os.urandom(2048), "work")
    # audio
    _wav_with_bext(os.path.join(folder, "DOORWood_Creak Slow_Test.wav"))
    out["DOORWood_Creak Slow_Test.wav"] = "audio"
    import numpy as np
    import soundfile as sf
    tone = (0.3 * np.sin(np.linspace(0, 2000, 30000))).astype("float32")
    for ext, fmt in (("flac", "FLAC"), ("ogg", "OGG"), ("aiff", "AIFF"), ("mp3", "MP3")):
        try:
            sf.write(os.path.join(folder, f"music.{ext}"), tone, 22050, format=fmt)
            out[f"music.{ext}"] = "audio"
        except Exception:
            pass
    return out


if __name__ == "__main__":
    import sys
    print(make_all(sys.argv[1]))
