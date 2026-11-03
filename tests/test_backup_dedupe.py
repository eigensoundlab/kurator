"""Backups store identical files once; the copies point to it, and every case stays correct."""

import json
import os
import shutil
import time

import pytest

from kurator import backup, tags
from kurator.db import Database
from kurator.scanner import Scanner
from test_backup import noise, run_job, wait_scan


def rescan(sc):
    sc.request_scan()
    wait_scan(sc)


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "Art"
    for d in ("a", "b", "c"):
        (lib / d).mkdir(parents=True)
    noise(lib / "a" / "dragon.png", 1)
    shutil.copy(lib / "a" / "dragon.png", lib / "b" / "dragon.png")         # same content, 3 places
    shutil.copy(lib / "a" / "dragon.png", lib / "c" / "dragon copy.png")
    noise(lib / "a" / "other.png", 2)
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    rescan(sc)
    return db, sc, lib, tmp_path, backup.Jobs(db, sc)


def stored(tmp):
    return sorted(p.relative_to(tmp / "bk").as_posix() for p in (tmp / "bk").rglob("*.png") if "_kurator" not in str(p))


def rel(p, lib):
    return os.path.relpath(p, lib).replace("\\", "/")


def test_identical_files_stored_once_and_restored_everywhere(env):
    db, sc, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "B", "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    p = backup.plan(db, s["id"])
    assert p["counts"]["new"] == 2 and p["counts"]["same_as"] == 2
    assert p["bytes"] == os.path.getsize(lib / "a" / "dragon.png") + os.path.getsize(lib / "a" / "other.png")
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert stored(tmp) == ["Art/a/dragon.png", "Art/a/other.png"]
    rows = {rel(r["src_path"], lib): r["dest"] for r in db.cat.execute("SELECT * FROM backup_files")}
    assert rows["b/dragon.png"] == rows["a/dragon.png"] == rows["c/dragon copy.png"]
    manifest = [json.loads(ln) for ln in (tmp / "bk" / "_kurator" / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 4
    # second run: nothing to do
    p = backup.plan(db, s["id"])
    assert p["counts"]["new"] == 0 and p["counts"]["same_as"] == 2 and not [o for o in p["ops"] if o["op"] != "meta"]
    # lose two of the copies: restore brings all of them back from the one stored file
    os.remove(lib / "b" / "dragon.png")
    os.remove(lib / "c" / "dragon copy.png")
    run_job(jobs, lambda: jobs.start_restore(s["id"], "missing", None))
    assert (lib / "b" / "dragon.png").read_bytes() == (lib / "a" / "dragon.png").read_bytes()
    assert (lib / "c" / "dragon copy.png").exists()


def test_editing_the_owner_keeps_the_others_safe(env):
    db, sc, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "B", "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    old = (lib / "b" / "dragon.png").read_bytes()
    time.sleep(0.05)
    noise(lib / "a" / "dragon.png", 99)                     # the file that owns the stored copy is edited
    rescan(sc)
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert (tmp / "bk" / "Art" / "a" / "dragon.png").read_bytes() == (lib / "a" / "dragon.png").read_bytes()
    assert (tmp / "bk" / "Art" / "b" / "dragon.png").read_bytes() == old    # the others got their own copy
    r = {rel(x["src_path"], lib): x["dest"] for x in db.cat.execute("SELECT * FROM backup_files")}
    assert r["c/dragon copy.png"] == r["b/dragon.png"] == "Art/b/dragon.png"
    p = backup.plan(db, s["id"])
    assert not [o for o in p["ops"] if o["op"] not in ("meta",)]


def test_editing_a_copy_and_retagging(env):
    db, sc, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "T", "layout": "tags", "tag_template": "{tagpath}",
                             "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    dragons = tags.create_tag(db, "Dragons")["id"]
    ids = {rel(r[1], lib): r[0] for r in db.cat.execute("SELECT id, path FROM files")}
    tags.apply(db, [ids["a/dragon.png"], ids["b/dragon.png"], ids["c/dragon copy.png"]], dragons, "add")
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert stored(tmp) == ["Dragons/dragon.png", "_untagged/Art/a/other.png"]
    # re-tag: the stored copy moves, every file pointing to it follows
    monsters = tags.create_tag(db, "Monsters")["id"]
    allids = [ids["a/dragon.png"], ids["b/dragon.png"], ids["c/dragon copy.png"]]
    tags.apply(db, allids, dragons, "remove")
    tags.apply(db, allids, monsters, "add")
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert stored(tmp) == ["Monsters/dragon.png", "_untagged/Art/a/other.png"]
    assert {r[0] for r in db.cat.execute("SELECT dest FROM backup_files WHERE dest LIKE '%dragon%'")} == {"Monsters/dragon.png"}
    # one copy is edited: it gets its own stored file, the shared one is untouched
    time.sleep(0.05)
    noise(lib / "c" / "dragon copy.png", 7)
    rescan(sc)
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert len(stored(tmp)) == 3
    assert (tmp / "bk" / "Monsters" / "dragon.png").read_bytes() == (lib / "a" / "dragon.png").read_bytes()


def test_mirror_keeps_a_copy_others_still_need(env):
    db, sc, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "M", "mode": "mirror", "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    os.remove(lib / "a" / "dragon.png")                     # the owner leaves the library
    rescan(sc)
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    # the stored copy isn't archived - b/ and c/ still need it; it moves to b/'s place instead
    assert stored(tmp) == ["Art/a/other.png", "Art/b/dragon.png"]
    r = {rel(x["src_path"], lib): x["dest"] for x in db.cat.execute("SELECT * FROM backup_files")}
    assert r == {"a/other.png": "Art/a/other.png", "b/dragon.png": "Art/b/dragon.png", "c/dragon copy.png": "Art/b/dragon.png"}


def test_dedupe_can_be_switched_off(env):
    db, sc, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "B", "dedupe": False, "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    p = backup.plan(db, s["id"])
    assert p["counts"]["new"] == 4 and p["counts"]["same_as"] == 0
    assert Database  # (imported for the fixture's type)
    assert Scanner


def test_duplicates_view(env):
    from fastapi.testclient import TestClient
    from kurator.server import create_app
    db, sc, lib, tmp, jobs = env
    c = TestClient(create_app(db, sc, 8765), base_url="http://127.0.0.1:8765")
    it = c.get("/api/items", params={"dupes": 1}).json()
    assert sorted(it["n"]) == ["dragon copy.png", "dragon.png", "dragon.png"]
    assert c.get("/api/tags").json()["duplicates"] == 3
    d = c.get(f"/api/items/{it['ids'][0]}").json()
    assert len(d["twins"]) == 2
