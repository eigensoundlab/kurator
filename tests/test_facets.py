"""Tag groups: projects, single-choice status, filters, sidecars, backup/rule tokens."""

import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import backup, rules, tags, xmp
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "Art"
    lib.mkdir()
    for n in ("a", "b", "c"):
        Image.new("RGB", (8, 8)).save(lib / f"{n}.png")
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    Scanner(db, workers=1).walk_root(rid, str(lib))
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return db, lib, tmp_path, ids, TestClient(create_app(db, Scanner(db), 8765), base_url=BASE)


def test_projects_status_and_filters(env):
    db, lib, tmp, ids, c = env
    game = c.post("/api/tags", json={"name": "Game A", "facet": "project"}, headers=H).json()
    assert game["facet"] == "project"
    with pytest.raises(tags.TagError):  # sub-tags stay in their parent's group
        tags.create_tag(db, "Game A/Level 1", facet="subject")
    lvl = tags.create_tag(db, "Game A/Level 1")
    assert lvl["facet"] == "project"
    icons = tags.create_tag(db, "Icons")
    wip = c.post("/api/tags", json={"name": "WIP", "facet": "status"}, headers=H).json()
    final = c.post("/api/tags", json={"name": "final", "facet": "status"}, headers=H).json()
    c.post(f"/api/tags/{wip['id']}/apply", json={"ids": [ids["a.png"], ids["b.png"]]}, headers=H)
    r = c.post(f"/api/tags/{final['id']}/apply", json={"ids": [ids["a.png"]], "op": "add"}, headers=H).json()
    assert r["also_removed"] == [{"tag_id": wip["id"], "ids": [ids["a.png"]]}]  # one status per image
    tags.apply(db, [ids["a.png"]], game["id"], "add")
    tags.apply(db, [ids["a.png"], ids["c.png"]], icons["id"], "add")

    it = c.get("/api/items", params={"missing": "project"}).json()
    assert sorted(it["n"]) == ["b.png", "c.png"]
    it = c.get("/api/items", params={"tag": f"{game['id']},{icons['id']}"}).json()  # project AND subject
    assert it["n"] == ["a.png"]
    t = c.get("/api/tags").json()
    assert t["missing"]["project"] == 2 and t["missing"]["status"] == 1 and len(t["facets"]) == 4
    c.patch(f"/api/tags/{game['id']}", json={"archived": True}, headers=H)
    assert tags.get_tag(db, game["id"])["archived"] == 1


def test_sidecar_roots_and_import(env):
    db, lib, tmp, ids, c = env
    p = tags.create_tag(db, "Game A", facet="project")
    s = tags.create_tag(db, "concept", facet="status")
    tags.apply(db, [ids["a.png"]], p["id"], "add")
    tags.apply(db, [ids["a.png"]], s["id"], "add")
    xmp.write_sidecars(db, [ids["a.png"]])
    side = (lib / "a.png.xmp").read_text(encoding="utf-8")
    assert "Projects|Game A" in side and "Status|concept" in side
    # a second catalog imports them back into the right groups
    db2 = Database(str(tmp / "data2"))
    rid = db2.add_root(str(lib), "library")
    Scanner(db2, workers=1).walk_root(rid, str(lib))
    fid = db2.cat.execute("SELECT id FROM files WHERE name='a.png'").fetchone()[0]
    xmp.import_keywords(db2, [fid])
    got = {r[0]: r[1] for r in db2.cat.execute("SELECT name, facet FROM tags")}
    assert got == {"Game A": "project", "concept": "status"}


def test_project_tokens_in_backup_and_rules(env):
    db, lib, tmp, ids, c = env
    p = tags.create_tag(db, "Game A", facet="project")
    sub = tags.create_tag(db, "Icons")
    tags.apply(db, [ids["a.png"]], p["id"], "add")
    tags.apply(db, [ids["a.png"], ids["b.png"]], sub["id"], "add")
    s = backup.save_set(db, {"name": "P", "layout": "tags", "tag_template": "{project}/{tagpath}",
                             "targets": [{"location": str(tmp / "bk"), "reserve_gb": 0}]})
    jobs = backup.Jobs(db)
    jobs.start_backup(s["id"])
    while jobs.status()["running"]:
        time.sleep(0.05)
    assert not jobs.status()["errors"]
    assert (tmp / "bk" / "Game A" / "Icons" / "a.png").exists()
    assert (tmp / "bk" / "_no-project" / "Icons" / "b.png").exists()
    assert (tmp / "bk" / "_untagged" / "Art" / "c.png").exists()
    # move rules can use {project} too
    rules.set_rules(db, [{"tag_id": sub["id"], "dest": str(lib / "{project}" / "{tag}")}])
    entries, _ = rules.plan_entries(db, [ids["a.png"], ids["b.png"]])
    dirs = {e["id"]: e["dir"] for e in entries}
    assert dirs[ids["a.png"]] == str(lib / "Game A" / "Icons") and dirs[ids["b.png"]] == str(lib / "Icons")
