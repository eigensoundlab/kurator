"""Backups: layouts, incremental runs, re-tag relocation, spanning targets, archive, restore."""

import json
import os
import shutil
import time

import pytest
from PIL import Image

from kurator import backup, tags
from kurator.db import Database
from kurator.scanner import Scanner


def noise(path, seed, size=100):
    import random
    rnd = random.Random(seed)
    Image.frombytes("RGB", (size, size), bytes(rnd.randrange(256) for _ in range(size * size * 3))).save(path)


def wait_scan(sc):
    t = time.time()
    while time.time() - t < 30:
        st = sc.status()
        if st["phase"] == "idle" and st["finished"]:
            return
        time.sleep(0.05)
    raise TimeoutError


def run_job(jobs, start):
    start()
    t = time.time()
    while jobs.status()["running"] and time.time() - t < 60:
        time.sleep(0.05)
    st = jobs.status()
    assert not st["errors"], st["errors"]
    return st["result"]


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "Art"
    (lib / "Downloads" / "x").mkdir(parents=True)
    (lib / "Documents" / "y").mkdir(parents=True)
    noise(lib / "Downloads" / "x" / "sword.png", 1)
    noise(lib / "Documents" / "y" / "sword.png", 2)   # same name, other folder
    noise(lib / "Documents" / "y" / "dragon.png", 3)
    noise(lib / "Downloads" / "loose.png", 4)
    for p in lib.rglob("*.png"):
        os.utime(p, (1600000000, 1600000000))
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait_scan(sc)
    ids = {r[1]: r[0] for r in db.cat.execute("SELECT id, path FROM files")}
    return db, sc, lib, tmp_path, ids, backup.Jobs(db, sc)


def rows(db, set_id):
    return {r["src_path"]: dict(r) for r in db.cat.execute("SELECT * FROM backup_files WHERE set_id=?", (set_id,))}


def test_target_validation(env):
    db, sc, lib, tmp, ids, jobs = env
    with pytest.raises(backup.BackupError):
        backup.save_set(db, {"name": "x", "targets": [{"location": str(lib / "Downloads")}]})
    with pytest.raises(backup.BackupError):
        backup.save_set(db, {"name": "x", "targets": [{"location": str(tmp)}]})  # contains the library
    with pytest.raises(backup.BackupError):
        backup.save_set(db, {"name": "x", "targets": [{"location": str(tmp / "b")}, {"location": str(tmp / "b" / "c")}]})


def test_original_layout_incremental_and_archive(env):
    db, sc, lib, tmp, ids, jobs = env
    s = backup.save_set(db, {"name": "Disk", "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    p = backup.plan(db, s["id"])
    assert p["counts"]["new"] == 4 and p["enough_space"]
    r = run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert r["copied"] == 4 and r["failed"] == 0
    dst = tmp / "bk" / "Art" / "Downloads" / "x" / "sword.png"
    assert dst.read_bytes() == (lib / "Downloads" / "x" / "sword.png").read_bytes()
    assert int(os.stat(dst).st_mtime) == 1600000000  # dates kept
    man = [json.loads(x) for x in (tmp / "bk" / "_kurator" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(man) == 4 and all(m["blake2b_128"] for m in man)
    assert (tmp / "bk" / "_kurator" / "catalog.db").exists()
    assert backup.plan(db, s["id"])["counts"]["unchanged"] == 4  # incremental: nothing to do

    noise(lib / "Downloads" / "loose.png", 99)  # edit
    sc.request_scan(); wait_scan(sc)
    p = backup.plan(db, s["id"])
    assert p["counts"]["changed"] == 1 and p["counts"]["unchanged"] == 3
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    archived = list((tmp / "bk" / "_kurator_archive").rglob("loose.png"))
    assert len(archived) == 1  # old version kept
    assert backup.coverage(db)["not_backed_up"] == 0


def test_tag_layout_collisions_and_retag(env):
    db, sc, lib, tmp, ids, jobs = env
    eq = tags.create_tag(db, "Equipment/Weapons")
    cr = tags.create_tag(db, "Creatures")
    a, b = ids[str(lib / "Downloads" / "x" / "sword.png")], ids[str(lib / "Documents" / "y" / "sword.png")]
    tags.apply(db, [a, b], eq["id"], "add")
    d = ids[str(lib / "Documents" / "y" / "dragon.png")]
    tags.apply(db, [d], cr["id"], "add")
    s = backup.save_set(db, {"name": "Tags", "layout": "tags", "tag_template": "{tagpath}",
                             "targets": [{"location": str(tmp / "tb"), "reserve_gb": 0}]})
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    weapons = sorted(p.name for p in (tmp / "tb" / "Equipment" / "Weapons").iterdir())
    assert len(weapons) == 2 and "sword.png" in weapons and any(n.startswith("sword_") for n in weapons)
    assert (tmp / "tb" / "Creatures" / "dragon.png").exists()
    assert (tmp / "tb" / "_untagged" / "Art" / "Downloads" / "loose.png").exists()
    assert backup.plan(db, s["id"])["counts"]["unchanged"] == 4  # stable names, no endless renaming

    # re-tag: the backup copy is moved, not copied again
    tags.apply(db, [d], cr["id"], "remove")
    ic = tags.create_tag(db, "Icons")
    tags.apply(db, [d], ic["id"], "add")
    p = backup.plan(db, s["id"])
    assert p["counts"]["relocate"] == 1 and p["bytes"] == 0
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert (tmp / "tb" / "Icons" / "dragon.png").exists() and not (tmp / "tb" / "Creatures").exists()


def test_spanning_two_targets_and_space_check(env, monkeypatch):
    db, sc, lib, tmp, ids, jobs = env
    # Fixed free space: on a real (CI) disk it changes while the test runs, and the margin here is
    # a few KB, so the measured value would make the test flaky.
    free = 50 * 1024 ** 3
    real = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda p: real(p)._replace(free=free))
    one_file = 40000
    small = (free - one_file * 1.5) / 1024 ** 3  # first target only has room for ~1 file
    s = backup.save_set(db, {"name": "Span", "targets": [
        {"location": str(tmp / "d1"), "reserve_gb": small}, {"location": str(tmp / "d2"), "reserve_gb": 0}]})
    p = backup.plan(db, s["id"])
    per = {t["location"]: t["files"] for t in p["targets"]}
    assert per[str(tmp / "d1")] == 1 and per[str(tmp / "d2")] == 3
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert len(list((tmp / "d1").rglob("*.png"))) == 1 and len(list((tmp / "d2").rglob("*.png"))) == 3
    full = backup.save_set(db, {"name": "Full", "targets": [{"location": str(tmp / "d3"), "reserve_gb": free / 1024 ** 3}]})
    p = backup.plan(db, full["id"])
    assert not p["enough_space"] and len(p["skipped"]) == 4


def test_mirror_archive_relink_and_restore(env):
    db, sc, lib, tmp, ids, jobs = env
    s = backup.save_set(db, {"name": "M", "mode": "mirror", "targets": [{"location": str(tmp / "mb"), "reserve_gb": 0}]})
    run_job(jobs, lambda: jobs.start_backup(s["id"]))

    # moved outside Kurator: same content, new catalog id -> relinked and relocated, not re-copied
    shutil.move(str(lib / "Downloads" / "loose.png"), str(lib / "Documents" / "loose.png"))
    sc.request_scan(); wait_scan(sc)
    p = backup.plan(db, s["id"])
    assert p["counts"]["relink"] == 1 and p["counts"]["relocate"] == 1 and p["bytes"] == 0
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert (tmp / "mb" / "Art" / "Documents" / "loose.png").exists()

    # deleted from the library -> mirror mode archives it (never deletes)
    os.remove(lib / "Documents" / "y" / "dragon.png")
    sc.request_scan(); wait_scan(sc)
    p = backup.plan(db, s["id"])
    assert p["counts"]["archive"] == 1
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert list((tmp / "mb" / "_kurator_archive").rglob("dragon.png"))

    # restore missing originals
    lost = lib / "Downloads" / "x" / "sword.png"
    content = lost.read_bytes()
    os.remove(lost)
    rp = backup.plan_restore(db, s["id"], "missing", None)
    assert rp["count"] == 1 and rp["enough_space"]
    r = run_job(jobs, lambda: jobs.start_restore(s["id"], "missing", None))
    assert r["restored"] == 1 and lost.read_bytes() == content and int(os.stat(lost).st_mtime) == 1600000000

    # restore everything into a new folder (keeps the backup layout)
    r = run_job(jobs, lambda: jobs.start_restore(s["id"], "folder", str(tmp / "restored")))
    assert r["restored"] == 3 and (tmp / "restored" / "Art" / "Documents" / "loose.png").exists()
