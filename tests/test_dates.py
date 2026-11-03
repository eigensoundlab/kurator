import os
from datetime import datetime

from kurator import dates


def ts(*a):
    return datetime(*a).timestamp()


def test_exif_datetime():
    assert dates.parse_exif_datetime("2019:04:12 15:30:00") == ts(2019, 4, 12, 15, 30)
    assert dates.parse_exif_datetime(b"2019:04:12 15:30:00\x00") == ts(2019, 4, 12, 15, 30)
    assert dates.parse_exif_datetime("0000:00:00 00:00:00") is None
    assert dates.parse_exif_datetime("garbage") is None
    assert dates.parse_exif_datetime("1970:01:01 00:00:00") is None  # before MIN_YEAR


def test_filename_dates():
    cases = {
        "2019-04-12 dragon sketch.jpg": ts(2019, 4, 12),
        "IMG_20190412_153012.jpg": ts(2019, 4, 12, 15, 30, 12),
        "Screenshot 2021-11-03 at 09.15.22.png": ts(2021, 11, 3, 9, 15, 22),
        "sketch_2018_07_01.png": ts(2018, 7, 1),
        "PXL_20230105_101010123.jpg": ts(2023, 1, 5),
    }
    for name, expected in cases.items():
        got = dates.parse_filename_date(name)
        assert got is not None, name
        assert datetime.fromtimestamp(got).date() == datetime.fromtimestamp(expected).date(), name
    for name in ["dragon_1234.png", "00123-2019041.png", "v20991340.png", "12345-1679321261.png"]:
        assert dates.parse_filename_date(name) is None, name


def test_iso_and_xmp():
    assert dates.parse_iso_datetime("2018-06-01T10:20:30") == ts(2018, 6, 1, 10, 20, 30)
    assert dates.parse_iso_datetime("2018-06-01T10:20:30Z") is not None
    assert dates.parse_iso_datetime("2018-06-01T10:20:30+0200") is not None
    xmp = '<x:xmpmeta><rdf:Description xmp:CreateDate="2017-03-04T05:06:07+01:00"/></x:xmpmeta>'
    assert dates.parse_xmp_date(xmp) is not None
    assert dates.parse_xmp_date("<xmp:CreateDate>2016:02:03 04:05:06</xmp:CreateDate>") == ts(2016, 2, 3, 4, 5, 6)


def test_resolve_priority(tmp_path):
    f = tmp_path / "2015-01-01 thing.png"
    f.write_bytes(b"x")
    st = os.stat(f)
    t, src = dates.resolve_date({"exif-original": ts(2010, 1, 1), "png": ts(2011, 1, 1)}, str(f), st)
    assert src == "exif-original" and t == ts(2010, 1, 1)
    t, src = dates.resolve_date({"png": ts(2011, 1, 1)}, str(f), st)
    assert src == "png"
    t, src = dates.resolve_date({}, str(f), st)
    assert src == "filename" and t == ts(2015, 1, 1)
    g = tmp_path / "plain.png"
    g.write_bytes(b"x")
    os.utime(g, (ts(2012, 5, 5), ts(2012, 5, 5)))
    t, src = dates.resolve_date({}, str(g), os.stat(g))
    assert src in ("file-modified", "file-created")
