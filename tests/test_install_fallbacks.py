"""When a ready-made package is missing: the installers go on without it and say why
(tools/install_extras.py), and on a Mac, macOS's own tools stand in for HEIC (sips) and PDF (Quick Look)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import install_extras  # noqa: E402

from kurator import pdfpages  # noqa: E402
from kurator.handlers import common, image, vector  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="stand-in tools are shell scripts")


def test_extras_are_read_from_pyproject():
    got = install_extras.extras(["formats"])
    assert [install_extras.name_of(r) for _, r in got] == ["pi-heif", "pypdfium2"]
    assert all(r.startswith(("pi-heif>=", "pypdfium2>=")) for _, r in got)
    assert {install_extras.name_of(r) for _, r in install_extras.extras(["3d"])} >= {"dracopy", "usd-core"}
    with pytest.raises(SystemExit):
        install_extras.extras(["nope"])


def test_reasons():
    reason, fix = install_extras.why("pi-heif", py=(3, 15), mac=(10, 15))
    assert "Python 3.15" in reason and "Python 3.14" in fix and "fresh" in fix
    for py in ((3, 14), (3, 15)):     # the same build for every Python: on an older Mac the macOS version is why
        reason, fix = install_extras.why("pypdfium2", py=py, mac=(10, 15))
        assert "macOS 13 or newer" in reason and "10.15" in reason and not fix
    reason, fix = install_extras.why("pypdfium2", py=(3, 14), mac=(15, 1))
    assert "no ready-made pypdfium2" in reason and "later" in fix


def test_install_goes_on_and_says_what_is_missing(monkeypatch, capsys):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[-1].startswith("pi-heif"):
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Could not find a version that satisfies the requirement (from versions: none)\nERROR: No matching distribution found for pi-heif>=1.4.0")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(install_extras.subprocess, "run", fake_run)
    monkeypatch.setattr(install_extras.sys, "platform", "darwin")
    monkeypatch.setattr(install_extras, "why", lambda name: ("there is no ready-made pi-heif for Python 3.15 yet", "install Python 3.14 too"))
    assert install_extras.main(["formats"]) == 0
    assert len(calls) == 2 and all("--only-binary=:all:" in c for c in calls)   # one by one, never compiling
    out = capsys.readouterr().out
    assert "HEIC / HEIF pictures (iPhone photos): there is no ready-made pi-heif for Python 3.15 yet." in out
    assert "macOS decodes them itself" in out and "To get it: install Python 3.14 too." in out
    assert "PDF" not in out
    calls.clear()
    monkeypatch.setattr(install_extras.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert install_extras.main(["formats"]) == 0 and capsys.readouterr().out == ""


# ------------------------------------------------------------- macOS stand-ins (faked off a Mac)
@pytest.fixture()
def fake_mac(tmp_path, monkeypatch):
    """sips and qlmanage stand-ins on PATH, and a platform that says darwin."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "sips").write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        from PIL import Image
        a = sys.argv[1:]
        src, out = a[a.index("--out") - 1], a[a.index("--out") + 1]
        assert a[:4] == ["-s", "format", "jpeg", "-s"], a
        im = Image.open(src)
        ex = im.getexif()
        ex[0x0132] = "2021:06:05 10:20:30"
        im.convert("RGB").save(out, "JPEG", exif=ex)
        """))
    (bin_ / "qlmanage").write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import os, sys
        from PIL import Image
        a = sys.argv[1:]
        assert a[0] == "-t", a
        size, out, src = int(a[a.index("-s") + 1]), a[a.index("-o") + 1], a[-1]
        assert open(src, "rb").read(5) == b"%PDF-"
        Image.new("RGB", (size * 3 // 4, size), "white").save(os.path.join(out, os.path.basename(src) + ".png"))
        """))
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(common.sys, "platform", "darwin")
    return tmp_path


@posix_only
def test_heic_through_sips(fake_mac, monkeypatch):
    src = fake_mac / "IMG_0001.heic"
    Image.new("RGB", (40, 30), "teal").save(src, "PNG")       # the stand-in reads anything Pillow does
    img = common.mac_convert(str(src))
    assert img.size == (40, 30) and img.getexif()[0x0132] == "2021:06:05 10:20:30"
    monkeypatch.setattr(image, "_MAC_HEIF", True)
    got = image.load(src.read_bytes(), str(src), 256)
    assert got.meta["format"] == "HEIF" and got.image.size == (40, 30)
    assert not [f for f in os.listdir(os.environ.get("TMPDIR", "/tmp")) if f.startswith("kurator-sips-")]


@posix_only
def test_pdf_through_quicklook(fake_mac, monkeypatch):
    pdf = fake_mac / "flyer.pdf"
    Image.new("RGB", (100, 140), "red").save(pdf, "PDF")
    monkeypatch.setitem(sys.modules, "pypdfium2", None)              # not installed
    got = vector.load_pdf(pdf.read_bytes(), str(pdf), 512)
    assert got.meta == {"preview": "Quick Look", "format": "PDF"} and got.image.size == (576, 768)
    ai = fake_mac / "logo.ai"
    ai.write_bytes(pdf.read_bytes())
    assert vector.load_ai(ai.read_bytes(), str(ai), 256).meta["preview"] == "Quick Look"
    with pytest.raises(pdfpages.PageError, match="pypdfium2"):
        pdfpages.pages(str(pdf))


def test_without_a_mac_or_the_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(common.sys, "platform", "linux")
    assert common.mac_convert(str(tmp_path / "x.heic")) is None and common.quicklook_thumbnail(str(tmp_path / "x.pdf")) is None
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    pdf = tmp_path / "a.pdf"
    Image.new("RGB", (10, 10)).save(pdf, "PDF")
    with pytest.raises(OSError, match="pypdfium2"):
        vector.load_pdf(pdf.read_bytes(), str(pdf), 128)


# ------------------------------------------------------------- the real tools, on a Mac
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_real_macos_tools(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (64, 48), "orange").save(png)
    heic = tmp_path / "a.heic"
    r = subprocess.run(["sips", "-s", "format", "heic", str(png), "--out", str(heic)], capture_output=True, check=False)
    if r.returncode == 0 and heic.exists():
        img = common.mac_convert(str(heic))
        assert img is not None and img.size == (64, 48)
    pdf = tmp_path / "a.pdf"
    Image.new("RGB", (200, 280), "red").save(pdf, "PDF")
    th = common.quicklook_thumbnail(str(pdf), 256)
    assert th is not None and max(th.size) <= 256 and th.getpixel((th.width // 2, th.height // 2))[0] > 200
