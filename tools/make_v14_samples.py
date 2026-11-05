"""Sample library for tools/ui_check_v14.py: a CMYK print file with a profile, an RGB picture, a PDF
with trim/bleed boxes and a spot colour, a Unity project with sprites, KTX2 textures.

    python tools/make_v14_samples.py FOLDER
"""

import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from icc_samples import cmyk_profile  # noqa: E402
from test_v14 import _pdf, bc1_solid, make_ktx2  # noqa: E402

if __name__ == "__main__":
    from pathlib import Path
    root = Path(sys.argv[1])
    printdir, unity, tex = root / "print", root / "Game", root / "textures"
    for d in (printdir, unity / "Assets" / "Sprites", unity / "ProjectSettings", tex):
        d.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:300, 0:400]
    c = (x / 399 * 255).astype(np.uint8)
    m = (y / 299 * 255).astype(np.uint8)
    yy = np.full_like(c, 120)
    k = np.where((x - 200) ** 2 + (y - 150) ** 2 < 60 ** 2, 200, 0).astype(np.uint8)
    Image.fromarray(np.dstack([c, m, yy, k]), "CMYK").save(root / "print" / "poster_cmyk.jpg", "JPEG", quality=92,
                                                          icc_profile=cmyk_profile(), dpi=(300, 300))
    rgb = np.dstack([x / 399 * 255, 255 - y / 299 * 255, np.full_like(x, 255)]).astype(np.uint8)
    Image.fromarray(rgb).save(root / "print" / "screen_rgb.png", dpi=(72, 72))
    _pdf(printdir / "flyer.pdf")
    (root / "test_cmyk.icc").write_bytes(cmyk_profile())
    art = unity / "Assets" / "Sprites"
    Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(art / "hero.png")
    Image.new("RGB", (32, 32), (128, 128, 255)).save(art / "hero_normal.png")
    (tex / "brick_bc1.ktx2").write_bytes(make_ktx2(131, 64, 64, bc1_solid(64, 64, (180, 70, 50))))
    (tex / "wood_basis.ktx2").write_bytes(make_ktx2(0, 64, 64, b"\0" * 64, dfd_model=166))
