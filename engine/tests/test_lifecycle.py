"""P2.5/P2.6 — project and document lifecycle (F3): rename/archive/delete a project; rename/
delete a document with the full invalidation cascade (codes, evidence, checkpoints, theme
steps). All offline — projects here are created directly or seeded from the cache fixture
(0 LLM calls, per the autouse guard)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from masshine import projects, runner, seed, store
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", d)
    return d


# ---- P2.5: project registry lifecycle --------------------------------------------------------

def test_project_lifecycle_roundtrip(data_dir):
    client = TestClient(app)
    p = client.post("/projects", json={"name": "Scratch"}).json()
    pid = p["id"]
    assert p["archived"] is False

    # rename
    r = client.patch(f"/projects/{pid}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert client.get(f"/projects/{pid}").json()["project"]["name"] == "Renamed"

    # archive: disappears from the default list, stays reachable directly, reappears with ?archived=1
    r = client.patch(f"/projects/{pid}", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    assert pid not in {x["id"] for x in client.get("/projects").json()}
    assert pid in {x["id"] for x in client.get("/projects?archived=1").json()}
    assert client.get(f"/projects/{pid}").json()["project"]["archived"] is True

    # unarchive
    client.patch(f"/projects/{pid}", json={"archived": False})
    assert pid in {x["id"] for x in client.get("/projects").json()}

    # delete: registry row + project dir gone, 404 afterward
    proj_dir = projects.project_dir(pid)
    assert proj_dir.exists()
    r = client.delete(f"/projects/{pid}")
    assert r.status_code == 200
    assert not proj_dir.exists()
    assert client.get(f"/projects/{pid}").status_code == 404
    assert client.patch(f"/projects/{pid}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/projects/{pid}").status_code == 404


def test_project_patch_rejects_empty_name(data_dir):
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Scratch"}).json()["id"]
    r = client.patch(f"/projects/{pid}", json={"name": "   "})
    assert r.status_code == 400


def test_unknown_project_404s_on_lifecycle_routes(data_dir):
    client = TestClient(app)
    assert client.patch("/projects/Pnope", json={"name": "x"}).status_code == 404
    assert client.delete("/projects/Pnope").status_code == 404


# ---- P2.6: document lifecycle -----------------------------------------------------------------

@pytest.fixture
def seeded(data_dir):
    pid = seed.import_cache(FIXTURES / "panel_2interview.json", "Seeded",
                            "migration_oral_history")
    return pid


def test_document_patch_title_roundtrips(seeded):
    client = TestClient(app)
    doc_id = client.get(f"/projects/{seeded}").json()["documents"][0]["doc_id"]
    r = client.patch(f"/projects/{seeded}/documents/{doc_id}", json={"title": "New Title"})
    assert r.status_code == 200
    docs = client.get(f"/projects/{seeded}").json()["documents"]
    assert next(d for d in docs if d["doc_id"] == doc_id)["title"] == "New Title"
    rd = client.get(f"/projects/{seeded}/documents/{doc_id}").json()
    assert rd["title"] == "New Title"


def test_document_patch_rejects_empty_title(seeded):
    client = TestClient(app)
    doc_id = client.get(f"/projects/{seeded}").json()["documents"][0]["doc_id"]
    assert client.patch(f"/projects/{seeded}/documents/{doc_id}",
                        json={"title": "  "}).status_code == 400


def test_document_delete_unknown_404s(seeded):
    client = TestClient(app)
    assert client.delete(f"/projects/{seeded}/documents/DOESNOTEXIST").status_code == 404


def test_document_delete_cascades_codes_checkpoints_and_stale_flag(seeded):
    client = TestClient(app)
    before = client.get(f"/projects/{seeded}").json()
    docs = before["documents"]
    assert len(docs) == 2
    doc_id = docs[0]["doc_id"]
    other_id = docs[1]["doc_id"]

    codes_before = client.get(f"/projects/{seeded}/codes").json()
    origin_here = [c for c in codes_before if c["origin_doc_id"] == doc_id]
    assert origin_here  # sanity: this doc actually originated codes

    # cross-doc evidence check: find (or fabricate awareness of) codes referencing this doc's
    # sentences from the OTHER doc's origin, if any exist in the fixture — not required, but if
    # present after delete they must have this doc's evidence stripped, not merely orphaned.
    cross_ref_ids_before = {
        c["id"] for c in codes_before
        if c["origin_doc_id"] != doc_id
        and any(e.startswith(f"{doc_id}#") for e in c["evidence"])
    }

    assert not store.themes_stale(project_db(projects.project_db_path(seeded)), "panel")

    r = client.delete(f"/projects/{seeded}/documents/{doc_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["codes_deleted"] == len(origin_here)

    # document list shrinks
    after = client.get(f"/projects/{seeded}").json()
    assert len(after["documents"]) == 1
    assert after["documents"][0]["doc_id"] == other_id

    # codes originating on the deleted doc are gone
    codes_after = client.get(f"/projects/{seeded}/codes").json()
    assert all(c["origin_doc_id"] != doc_id for c in codes_after)
    assert all(not any(e.startswith(f"{doc_id}#") for e in c["evidence"]) for c in codes_after)
    # codes that had cross-references stripped survive with the reference gone, not orphaned
    for cid in cross_ref_ids_before:
        remaining = next((c for c in codes_after if c["id"] == cid), None)
        if remaining:
            assert not any(e.startswith(f"{doc_id}#") for e in remaining["evidence"])

    # theme steps cleared, stale flag set for BOTH modes
    conn = project_db(projects.project_db_path(seeded))
    try:
        assert conn.execute("SELECT COUNT(*) FROM theme_step WHERE mode='standard'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM theme_step WHERE mode='panel'").fetchone()[0] == 0
        assert store.themes_stale(conn, "standard") is True
        assert store.themes_stale(conn, "panel") is True
    finally:
        conn.close()

    # checkpoints no longer reference the deleted doc
    for mode in ("standard", "panel"):
        cp = projects.checkpoint_path(seeded, mode)
        if cp.exists():
            state = runner.load_checkpoint(cp)
            assert doc_id not in state.get("docs", {})
            assert doc_id not in state.get("order", [])
            assert state.get("theme_steps", {}) == {}

    # comments/memos targeting the doc are gone (add one before delete would be better, but the
    # fixture doesn't seed any — assert the delete path doesn't error when there are none, and
    # exercise the explicit target_type='document' comment/memo path directly against the DB)
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.add_comment(conn, "document", other_id, other_id, "note on the doc itself")
        store.set_memo(conn, "document", other_id, "memo on the doc itself")
    finally:
        conn.close()
    r2 = client.delete(f"/projects/{seeded}/documents/{other_id}")
    assert r2.status_code == 200
    conn = project_db(projects.project_db_path(seeded))
    try:
        assert store.list_comments(conn) == []
        assert store.list_memos(conn) == []
    finally:
        conn.close()
