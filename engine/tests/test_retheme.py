"""Extending themes to a newly-coded document (bug: coding a 3rd doc into an already-themed
project left the journey with no primary action). code_work must flag themes_stale when it
codes NEW documents into a project whose mode already has themes — but it must NOT clear
theme_steps, since theme_work's raw_cache lets already-themed docs replay for free and only the
new doc calls the model. All offline (autouse guard in conftest fails any un-stubbed llm call)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import llm
from conftest import FIXTURES
from masshine import jobs, projects, runner, seed, store
from masshine.api import app
from masshine.db import project_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """2 docs, panel-coded and themed (fixture: panel_2interview.json)."""
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    pid = seed.import_cache(FIXTURES / "panel_2interview.json", "Retheme Demo",
                            "migration_oral_history")
    return pid


def _seed_third_doc(pid: str, doc_id: str = "doc-c"):
    """Insert a 3rd document's document/section/sentence rows directly into the project DB —
    minimal shape (see tests/test_feedback.py::_seed_doc) — so code_work sees an uncoded doc."""
    conn = project_db(projects.project_db_path(pid))
    try:
        text = "NEW: A third narrator describes crossing the border alone at night."
        conn.execute(
            "INSERT INTO document (id, text, filename, status, created_at, kind) "
            "VALUES (?,?,?,?,?,?)", (doc_id, text, "third.txt", "ingested", _now(), "transcript"))
        conn.execute("INSERT INTO section (id, doc_id, gist, char_start, char_end) "
                     "VALUES ('S1', ?, 'border crossing', 0, ?)", (doc_id, len(text)))
        conn.execute("INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
                     "VALUES ('S1.001', ?, 'S1', 0, ?)", (doc_id, len(text)))
        conn.commit()
    finally:
        conn.close()


def test_coding_new_doc_sets_stale_and_preserves_replay_cache(seeded, monkeypatch):
    pid = seeded
    _seed_third_doc(pid)

    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"codes": []})

    conn = project_db(projects.project_db_path(pid))
    try:
        assert store.themes_stale(conn, "panel") is False  # control: not stale before coding
    finally:
        conn.close()

    result = jobs.code_work(pid, "panel")(lambda **_: None)
    assert result["new_docs"] == ["doc-c"]

    conn = project_db(projects.project_db_path(pid))
    try:
        assert store.themes_stale(conn, "panel") is True
    finally:
        conn.close()

    cp = projects.checkpoint_path(pid, "panel")
    state = runner.load_checkpoint(cp)
    # both ORIGINAL docs' theme steps survive untouched — proving the resume cache was not cleared
    order = state["order"]
    original_docs = [d for d in order if d != "doc-c"]
    assert len(original_docs) == 2
    for d in original_docs:
        assert d in state["theme_steps"]


def test_get_project_reports_n_themed_docs(seeded):
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}")
    assert r.status_code == 200
    d = r.json()
    assert d["n_themed_docs"] == 2


def test_get_project_n_themed_docs_lags_after_new_doc_coded(seeded, monkeypatch):
    pid = seeded
    _seed_third_doc(pid)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"codes": []})
    jobs.code_work(pid, "panel")(lambda **_: None)

    client = TestClient(app)
    r = client.get(f"/projects/{pid}")
    d = r.json()
    assert d["themes_stale"] is True
    assert d["n_themed_docs"] == 2  # theme walk hasn't run for doc-c yet
    coded_docs = sum(1 for doc in d["documents"] if (doc.get("status") or "").startswith("coded"))
    assert coded_docs == 3
    assert coded_docs - d["n_themed_docs"] == 1  # exactly one new source to extend themes to


def test_coding_when_no_themes_exist_does_not_set_stale(tmp_path, monkeypatch):
    """Control: a project with no themes yet must not have the stale flag flipped by coding —
    stale means 'themes exist but under-cover the corpus', which isn't true before any theme run."""
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    proj = projects.create_project("No Themes Yet", "migration_oral_history")
    pid = proj["id"]
    _seed_third_doc(pid, "doc-only")

    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"codes": []})
    jobs.code_work(pid, "panel")(lambda **_: None)

    conn = project_db(projects.project_db_path(pid))
    try:
        assert store.themes_stale(conn, "panel") is False
    finally:
        conn.close()
