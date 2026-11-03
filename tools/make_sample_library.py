"""Generate a messy test library: mixed formats, metadata, dates, nested folders.

    python tools/make_sample_library.py OUT_DIR [--count 3000]
"""

from __future__ import annotations

import argparse
import io
import os
import random
import time
import zipfile
from datetime import datetime

from PIL import Image, ImageDraw, PngImagePlugin

FOLDERS = ["old sketches", "old sketches/2019", "Downloads", "Desktop/new", "wip", "wip/creatures",
           "export", "export/icons", "refs", "phone", "misc/stuff/deep/folder"]
SUBJECTS = ["creature", "sword", "icon", "emblem", "character", "potion", "armor", "dragon", "knight"]


def draw(subject: str, size: tuple[int, int], alpha: bool, rng: random.Random) -> Image.Image:
    mode = "RGBA" if alpha else "RGB"
    bg = (0, 0, 0, 0) if alpha else tuple(rng.randint(150, 255) for _ in range(3))
    img = Image.new(mode, size, bg)
    d = ImageDraw.Draw(img)
    w, h = size
    col = tuple(rng.randint(0, 200) for _ in range(3))
    for _ in range(rng.randint(3, 12)):
        x0, y0 = rng.randint(0, w - 1), rng.randint(0, h - 1)
        x1, y1 = rng.randint(x0, w), rng.randint(y0, h)
        shape = rng.choice(["ellipse", "rectangle", "line"])
        fill = tuple(min(255, c + rng.randint(0, 55)) for c in col)
        if shape == "line":
            d.line((x0, y0, x1, y1), fill=fill + ((255,) if alpha else ()), width=max(1, w // 60))
        else:
            getattr(d, shape)((x0, y0, x1, y1), outline=fill + ((255,) if alpha else ()), width=max(1, w // 80))
    d.text((8, 8), subject, fill=(0, 0, 0) + ((255,) if alpha else ()))
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    os.makedirs(a.out, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(a.out, f), exist_ok=True)

    t_start = datetime(2015, 1, 1).timestamp()
    t_end = time.time() - 86400
    for i in range(a.count):
        subject = rng.choice(SUBJECTS)
        folder = os.path.join(a.out, rng.choice(FOLDERS))
        ts = rng.uniform(t_start, t_end)
        dt = datetime.fromtimestamp(ts)
        kind = rng.choices(["jpg_exif", "png_ai", "png_alpha", "jfif", "named", "plain_jpg", "webp"],
                           weights=[25, 10, 15, 8, 12, 25, 5])[0]
        big = rng.random() < 0.01
        size = (6000, 4000) if big else (rng.choice([400, 800, 1200, 1600]), rng.choice([400, 800, 1200, 1600]))
        if kind == "png_alpha":
            size = (rng.choice([64, 128, 256, 512]),) * 2
        img = draw(subject, size, kind == "png_alpha", rng)
        if kind == "jpg_exif":
            exif = Image.Exif()
            exif[0x0110] = rng.choice(["Pixel 7", "iPhone 13", "EOS 80D"])
            exif[0x010F] = "TestMaker"
            exif.get_ifd(0x8769)[36867] = dt.strftime("%Y:%m:%d %H:%M:%S")
            path = os.path.join(folder, f"IMG_{i:05d}.jpg")
            img.save(path, "JPEG", quality=85, exif=exif)
        elif kind == "png_ai":
            info = PngImagePlugin.PngInfo()
            info.add_text("parameters", f"{subject}, fantasy concept art, detailed\nNegative prompt: blurry\n"
                                        f"Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: {rng.randint(0, 2**31)}, "
                                        f"Size: {size[0]}x{size[1]}, Model: sdxl_base")
            info.add_text("Creation Time", dt.strftime("%Y-%m-%dT%H:%M:%S"))
            path = os.path.join(folder, f"{rng.randint(0, 99999):05d}-{rng.randint(0, 2**31)}.png")
            img.save(path, "PNG", pnginfo=info)
        elif kind == "png_alpha":
            path = os.path.join(folder, f"{subject}_icon_{i}.png")
            img.save(path, "PNG")
        elif kind == "jfif":
            path = os.path.join(folder, f"{subject} ref {i}.jfif")
            img.save(path, "JPEG", quality=80)
        elif kind == "named":
            path = os.path.join(folder, f"{dt:%Y-%m-%d} {subject} sketch {i}.jpg")
            img.save(path, "JPEG", quality=80)
        elif kind == "webp":
            path = os.path.join(folder, f"{subject}_{i}.webp")
            img.save(path, "WEBP", quality=80)
        else:
            path = os.path.join(folder, f"{subject}_{i}.jpg")
            img.save(path, "JPEG", quality=80)
        os.utime(path, (ts, ts))

    # Special cases
    special = os.path.join(a.out, "special")
    os.makedirs(special, exist_ok=True)
    with open(os.path.join(special, "broken.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0 not really a jpeg")
    Image.new("I;16", (300, 200), 30000).save(os.path.join(special, "16bit.png"))
    Image.new("CMYK", (300, 200), (0, 80, 80, 0)).save(os.path.join(special, "cmyk.jpg"))
    rot = draw("rotated", (600, 300), False, rng)
    exif = Image.Exif(); exif[274] = 6
    rot.save(os.path.join(special, "rotated_exif6.jpg"), exif=exif)
    # Krita file
    buf = io.BytesIO(); draw("krita", (800, 600), True, rng).save(buf, "PNG")
    with zipfile.ZipFile(os.path.join(special, "painting.kra"), "w") as z:
        z.writestr("mimetype", "application/x-krita")
        z.writestr("mergedimage.png", buf.getvalue())
        z.writestr("documentinfo.xml", "<document-info><about><title>Dragon WIP</title>"
                                       "<creation-date>2018-06-01T10:20:30</creation-date></about></document-info>")
    print(f"generated {a.count} images (+ specials) in {a.out}")


if __name__ == "__main__":
    main()
