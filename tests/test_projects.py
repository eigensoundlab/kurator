"""Projects as a top filter, with live sources: tags, folders and other projects."""

import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import projects, rules, tags
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    for rel in ("a/1.png", "a/2.png", "b/3.png", "c/4.png", "5.png"):
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(p)
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.walk_root(rid, str(lib))
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    t = {n: tags.create_tag(db, n, facet=f)["id"] for n, f in
         (("Creatures", "subject"), ("Game A", "project"), ("DLC", "project"), ("Other", "project"))}
    tags.apply(db, [ids["1.png"]], t["Creatures"], "add")
    tags.apply(db, [ids["3.png"]], t["Game A"], "add")
    tags.apply(db, [ids["5.png"]], t["DLC"], "add")
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    return db, sc, lib, rid, ids, t, c


def names(c, **params):
    return sorted(c.get("/api/items", params=params).json()["n"])


def test_sources_make_a_live_project(env):
    db, sc, lib, rid, ids, t, c = env
    ga = t["Game A"]
    assert names(c, project=ga) == ["3.png"]                      # only what was added by hand
    r = c.put(f"/api/projects/{ga}/sources", headers=H, json={"sources": [
        {"kind": "tag", "ref": t["Creatures"]}, {"kind": "folder", "ref": str(lib / "c")}, {"kind": "project", "ref": t["DLC"]}]})
    assert r.status_code == 200 and [s["kind"] for s in r.json()] == ["tag", "folder", "project"]
    assert names(c, project=ga) == ["1.png", "3.png", "4.png", "5.png"]
    # inside the project, other filters narrow it down
    assert names(c, project=ga, folder=str(lib / "c")) == ["4.png"]
    assert names(c, project=ga, tag=str(t["Creatures"])) == ["1.png"]
    # a tag filter on the project (Ctrl+click), exclusion (Alt+click) and "No project" use the sources too
    assert names(c, tag=str(ga)) == ["1.png", "3.png", "4.png", "5.png"]
    assert names(c, exclude=str(ga)) == ["2.png"]
    assert names(c, missing="project") == ["2.png"]
    # live: a new file in the folder, a newly tagged creature
    Image.new("RGB", (8, 8)).save(lib / "c" / "6.png")
    sc.walk_root(rid, str(lib))
    ids6 = db.cat.execute("SELECT id FROM files WHERE name = '6.png'").fetchone()[0]
    tags.apply(db, [ids["2.png"]], t["Creatures"], "add")
    assert names(c, project=ga) == ["1.png", "2.png", "3.png", "4.png", "5.png", "6.png"]
    # "add from library" marks members
    out = c.get("/api/items", params={"member_of": ga}).json()
    assert dict(zip(out["n"], out["m"])) == {n: 1 for n in out["n"]} and ids6 in out["ids"]


def test_counts_folders_and_overview(env):
    db, sc, lib, rid, ids, t, c = env
    ga = t["Game A"]
    projects.set_sources(db, ga, [{"kind": "tag", "ref": t["Creatures"]}])
    tg = {x["name"]: x for x in c.get("/api/tags", params={"project": ga}).json()["tags"]}
    assert tg["Creatures"]["count"] == 1 and tg["DLC"]["count"] == 0
    whole = {x["name"]: x for x in c.get("/api/tags").json()["tags"]}
    assert whole["Game A"]["count"] == 2 and whole["Game A"]["live"] is True   # 3.png by hand + 1.png via Creatures
    folders = {os.path.basename(n["path"]): n["total"] for n in c.get("/api/folders", params={"project": ga}).json()}
    assert folders["a"] == 1 and folders["b"] == 1 and "c" not in folders
    ov = {p["name"]: p for p in c.get("/api/projects").json()}
    assert ov["Game A"]["count"] == 2 and ov["Game A"]["by_hand"] == 1
    assert ov["Game A"]["sources"][0]["label"].endswith("Creatures") and ov["Game A"]["sources"][0]["count"] == 1


def test_no_cycles_and_only_projects(env):
    db, sc, lib, rid, ids, t, c = env
    assert c.put(f"/api/projects/{t['Game A']}/sources", headers=H, json={"sources": [{"kind": "project", "ref": t["DLC"]}]}).status_code == 200
    r = c.put(f"/api/projects/{t['DLC']}/sources", headers=H, json={"sources": [{"kind": "project", "ref": t["Game A"]}]})
    assert r.status_code == 400 and "circle" in r.json()["detail"]
    assert c.put(f"/api/projects/{t['Creatures']}/sources", headers=H, json={"sources": []}).status_code == 400
    assert c.put(f"/api/projects/{t['Game A']}/sources", headers=H, json={"sources": [{"kind": "project", "ref": t["Game A"]}]}).status_code == 400


def test_rules_and_backups_see_live_members(env, tmp_path):
    db, sc, lib, rid, ids, t, c = env
    projects.set_sources(db, t["Game A"], [{"kind": "folder", "ref": str(lib / "c")}])
    rules.set_rules(db, [{"tag_id": t["Game A"], "dest": str(tmp_path / "out" / "{project}"), "date_prefix": False}])
    entries, skipped = rules.plan_entries(db, [ids["4.png"], ids["2.png"]])
    assert [e["id"] for e in entries] == [ids["4.png"]] and entries[0]["dir"] == str(tmp_path / "out" / "Game A")
    assert [s_["id"] for s_ in skipped] == [ids["2.png"]]
    from kurator import backup
    s = backup.save_set(db, {"name": "P", "layout": "tags", "tag_template": "{project}/{tagpath}",
                             "targets": [{"location": str(tmp_path / "bk"), "reserve_gb": 0}]})
    files = {f["name"]: f for f in backup._scope_files(db, backup._set_row(db, s["id"]))}
    assert files["4.png"]["groups"]["project"] == ["Game A"]
