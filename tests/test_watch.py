"""Live folder watching: only changed folders are re-checked; tags survive edits and moves."""

import os
import shutil
import time

from PIL import Image

from kurator import tags
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.watcher import Watcher


def wait_for(cond, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def names(db):
    return {r[0] for r in db.cat.execute("SELECT name FROM files")}


def test_watcher_picks_up_changes(tmp_path):
    lib = tmp_path / "lib"
    (lib / "a").mkdir(parents=True)
    Image.new("RGB", (20, 20), "red").save(lib / "a" / "one.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    assert wait_for(lambda: sc.status()["phase"] == "idle" and sc.status()["finished"])
    w = Watcher(db, sc)
    w.start()
    try:
        time.sleep(0.5)
        # new file
        Image.new("RGB", (20, 20), "blue").save(lib / "a" / "two.png")
        assert wait_for(lambda: "two.png" in names(db))
        assert wait_for(lambda: db.cat.execute("SELECT status FROM files WHERE name='two.png'").fetchone()[0] == 1)

        # tag it, then edit the file in place: same catalog row, new content hash, tag kept
        fid, h1 = db.cat.execute("SELECT id, hash FROM files WHERE name='two.png'").fetchone()
        t = tags.create_tag(db, "Creatures")
        tags.apply(db, [fid], t["id"], "add")
        time.sleep(1.1)
        Image.new("RGB", (30, 30), "green").save(lib / "a" / "two.png")
        assert wait_for(lambda: (db.cat.execute("SELECT hash FROM files WHERE id=?", (fid,)).fetchone() or [h1])[0] != h1)
        assert db.cat.execute("SELECT COUNT(*) FROM file_tags WHERE file_id=?", (fid,)).fetchone()[0] == 1

        # move a whole folder outside Kurator: rows follow, tag re-attached by content
        shutil.move(str(lib / "a"), str(lib / "b"))
        assert wait_for(lambda: db.cat.execute(
            "SELECT COUNT(*) FROM files WHERE dir = ?", (str(lib / "b"),)).fetchone()[0] == 2)
        assert wait_for(lambda: db.cat.execute(
            "SELECT COUNT(*) FROM file_tags ft JOIN files f ON f.id = ft.file_id WHERE f.name='two.png'").fetchone()[0] == 1)

        # delete
        os.remove(lib / "b" / "one.png")
        assert wait_for(lambda: "one.png" not in names(db))
        assert w.status()["updates"] >= 3
    finally:
        w.stop()
        sc.stop()


def test_scoped_walk_leaves_other_folders_alone(tmp_path):
    lib = tmp_path / "lib"
    for d in ("x", "y"):
        (lib / d).mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(lib / d / f"{d}.png")
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.walk_root(rid, str(lib))
    os.remove(lib / "y" / "y.png")
    sc.walk_folder(rid, str(lib / "x"), recursive=False)
    assert names(db) == {"x.png", "y.png"}  # y not re-checked yet
    sc.walk_folder(rid, str(lib / "y"), recursive=False)
    assert names(db) == {"x.png"}
