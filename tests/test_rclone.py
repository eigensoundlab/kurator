"""Cloud targets through a real rclone daemon (skipped if rclone isn't installed).

The cloud is simulated by an rclone remote of type 'local' - same API calls as Google Drive.
Point KURATOR_RCLONE at an rclone binary to run these tests.
"""

import os
import shutil
import time

import pytest
from PIL import Image

from kurator import backup, rclone, tags
from kurator.db import Database
from kurator.scanner import Scanner

RCLONE = os.environ.get("KURATOR_RCLONE") or shutil.which("rclone")
pytestmark = pytest.mark.skipif(not RCLONE, reason="rclone not installed")


def run_job(jobs, start):
    start()
    t = time.time()
    while jobs.status()["running"] and time.time() - t < 120:
        time.sleep(0.05)
    st = jobs.status()
    assert not st["errors"], st["errors"]
    return st["result"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[cloud]\ntype = local\n\n[cloud2]\ntype = local\n")
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    if RCLONE:
        monkeypatch.setenv("KURATOR_RCLONE", RCLONE)
    rclone._shared = None  # fresh daemon with this config
    lib = tmp_path / "Art"
    (lib / "a").mkdir(parents=True)
    for i in range(6):
        Image.frombytes("RGB", (60, 60), os.urandom(60 * 60 * 3)).save(lib / "a" / f"img{i}.png")
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    Scanner(db, workers=1).walk_root(rid, str(lib))
    from kurator.processing import process_file
    db.store_results([process_file(r[0]) for r in db.cat.execute("SELECT path FROM files")])
    yield db, lib, tmp_path, backup.Jobs(db)
    rclone.shared().stop()
    rclone._shared = None


def test_rclone_info_lists_remotes(env):
    info = rclone.shared().info()
    assert info["installed"] and {r["name"] for r in info["remotes"]} >= {"cloud", "cloud2"}
    with pytest.raises(rclone.RcloneError):
        rclone.shared().connect_google_drive("bad:name")


def test_cloud_backup_incremental_relocate_restore(env):
    db, lib, tmp, jobs = env
    remote_dir = tmp / "drive"
    with pytest.raises(backup.BackupError):  # unknown remote
        backup.save_set(db, {"name": "x", "targets": [{"kind": "rclone", "location": "nope:x"}]})
    s = backup.save_set(db, {"name": "Cloud", "layout": "tags",
                             "targets": [{"kind": "rclone", "location": f"cloud:{remote_dir}", "reserve_gb": 0}]})
    assert s["name"] == "Cloud"  # regression: the account name must not replace the set name
    p = backup.plan(db, s["id"])
    assert p["counts"]["new"] == 6 and p["targets"][0]["space"]["free"]  # quota via rclone about
    r = run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert r["copied"] == 6 and r["failed"] == 0
    assert len(list((remote_dir / "_untagged").rglob("*.png"))) == 6
    assert (remote_dir / "_kurator" / "manifest.jsonl").exists()
    assert backup.plan(db, s["id"])["counts"]["unchanged"] == 6

    # tag one image -> server-side move in the cloud, nothing uploaded
    fid = db.cat.execute("SELECT id FROM files WHERE name='img0.png'").fetchone()[0]
    t = tags.create_tag(db, "Icons")
    tags.apply(db, [fid], t["id"], "add")
    p = backup.plan(db, s["id"])
    assert p["counts"]["relocate"] == 1 and p["bytes"] == 0
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    assert (remote_dir / "Icons" / "img0.png").exists()

    # restore a lost original from the cloud, checksum-verified
    lost = lib / "a" / "img3.png"
    data = lost.read_bytes()
    os.remove(lost)
    r = run_job(jobs, lambda: jobs.start_restore(s["id"], "missing", None))
    assert r["restored"] == 1 and lost.read_bytes() == data


def test_spread_over_two_cloud_accounts(env):
    db, lib, tmp, jobs = env
    s = backup.save_set(db, {"name": "Two drives", "policy": "fill", "targets": [
        {"kind": "rclone", "location": f"cloud:{tmp / 'acc1'}", "reserve_gb": 0},
        {"kind": "rclone", "location": f"cloud2:{tmp / 'acc2'}", "reserve_gb": 0}]})
    p = backup.plan(db, s["id"])
    assert p["same_disk"] == []  # different accounts are different storage
    run_job(jobs, lambda: jobs.start_backup(s["id"]))
    n1 = len(list((tmp / "acc1").rglob("*.png")))
    n2 = len(list((tmp / "acc2").rglob("*.png")))
    assert n1 + n2 == 6
