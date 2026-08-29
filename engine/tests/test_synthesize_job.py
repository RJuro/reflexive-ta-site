"""jobs.synthesize_work (P10.2): checkpoint discipline (own kind "synthesize", skip already-
synthesized docs, `mode` threaded through), the API's job plumbing, and a full run against the
seeded fixture project with a canned model response. `synthesize.synthesize_document`'s own
validators are covered in isolation in tests/test_synthesize.py — this file tests jobs.py's
orchestration and the real DB path end to end. All offline."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import llm
from masshine import jobs, projects, seed, store, synthesize
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return projects.create_project("Synthesize Job Test")["id"]


def _seed_doc(pid: str, doc_id: str = "doc-a", n_sections: int = 2, sents_per_section: int = 3):
    """A synthetic document plus one code touching it — enough for the orchestration tests below,
    which stub synthesize.synthesize_document itself and never touch the LLM (mirrors
    test_read_job.py's synthetic-document fixture)."""
    conn = project_db(projects.project_db_path(pid))
    try:
        pos = 0
        chunks, sec_rows, sent_rows = [], [], []
        for i in range(1, n_sections + 1):
            sec_id = f"S{i}"
            sec_start = pos
            for j in range(sents_per_section):
                sent = f"This is sentence {j} of section {i} in {doc_id}. "
                chunks.append(sent)
                start = pos
                pos += len(sent)
                sent_rows.append((f"{sec_id}.{j:03d}", doc_id, sec_id, start, pos))
            sec_rows.append((sec_id, doc_id, f"section {i} gist", sec_start, pos))
        conn.execute(
            "INSERT INTO document (id, text, filename, status, created_at, kind) "
            "VALUES (?,?,?,?,?,?)",
            (doc_id, "".join(chunks), f"{doc_id}.txt", "read", _now(), "transcript"))
        conn.executemany(
            "INSERT INTO section (id, doc_id, gist, char_start, char_end) VALUES (?,?,?,?,?)",
            sec_rows)
        conn.executemany(
            "INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
            "VALUES (?,?,?,?,?)", sent_rows)
        conn.execute(
            "INSERT INTO code (id, origin_doc_id, label, definition, code_type, evidence_ids, "
            "model_rationale, coder) VALUES (?,?,?,?,?,?,?,?)",
            ("C0001", doc_id, "A code", "def", "semantic", f'["{doc_id}#S1.000"]', "", "standard"))
        conn.commit()
    finally:
        conn.close()


def _empty_result() -> dict:
    return {"findings": [], "checkbacks": [], "residue": [], "steps": [], "intro": [],
           "story": [], "focus_proposal": None}


# ---- checkpoint discipline ----------------------------------------------------------------------

def test_skips_docs_already_synthesized_this_checkpoint(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    calls = []

    def fake(conn, doc_id, mode):
        calls.append(doc_id)
        return _empty_result()

    monkeypatch.setattr(synthesize, "synthesize_document", fake)
    r1 = jobs.synthesize_work(pid)(lambda **_: None)
    assert calls == ["doc-a"]
    assert set(r1["docs"]) == {"doc-a"}

    r2 = jobs.synthesize_work(pid)(lambda **_: None)
    assert calls == ["doc-a"]        # no second call — already synthesized
    assert r2["docs"] == {}


def test_mode_defaults_to_standard(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    seen = []
    monkeypatch.setattr(synthesize, "synthesize_document",
                        lambda conn, doc_id, mode: (seen.append(mode), _empty_result())[1])
    jobs.synthesize_work(pid)(lambda **_: None)
    assert seen == ["standard"]


def test_mode_param_threaded_through(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    seen = []
    monkeypatch.setattr(synthesize, "synthesize_document",
                        lambda conn, doc_id, mode: (seen.append(mode), _empty_result())[1])
    jobs.synthesize_work(pid, mode="panel")(lambda **_: None)
    assert seen == ["panel"]


def test_synthesize_and_read_checkpoints_are_independent_files(pid, monkeypatch):
    """The own-checkpoint-kind requirement: a project that has already been READ (checkpoint
    "read") must still have every document eligible for SYNTHESIZE (checkpoint "synthesize")."""
    _seed_doc(pid, "doc-a")
    assert not projects.checkpoint_path(pid, "synthesize").exists()
    monkeypatch.setattr(synthesize, "synthesize_document",
                        lambda conn, doc_id, mode: _empty_result())
    jobs.synthesize_work(pid)(lambda **_: None)
    assert projects.checkpoint_path(pid, "synthesize").exists()
    assert projects.checkpoint_path(pid, "synthesize") != projects.checkpoint_path(pid, "read")


# ---- API surface ---------------------------------------------------------------------------

def test_post_synthesize_endpoint_returns_job_id(pid, monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append(jid))
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/synthesize", json={})
    assert r.status_code == 200
    assert r.json()["job_id"] in submitted


def test_post_synthesize_endpoint_rejects_bad_mode(pid):
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/synthesize", json={"mode": "bogus"})
    assert r.status_code == 400


# ---- full run against the seeded fixture, canned model response --------------------------------

def _canned_response(doc_id: str, sids: list[str], code_id: str) -> dict:
    return {
        "findings": [{"id": "", "label": "A finding", "central_concept": "Something recurring",
                     "supporting_code_ids": [code_id],
                     "key_evidence_sentence_ids": [sids[0]], "tensions": []}],
        "steps": [{"kind": "pattern", "statement": "A strong pattern in this document.",
                  "sids": [sids[0]], "code_ids": [code_id], "weakest_sids": [sids[-1]],
                  "finding_id": None}],
        "checkbacks": [],
        "residue": [],
        "intro": [{"para": "This document opens with a familiar arrival.", "sids": [sids[0]]}],
        "story": [{"para": "The story so far follows one migration.", "sids": [sids[0]]}],
        "focus_proposal": None,
    }


def test_full_synthesize_run_against_seeded_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    pid = seed.import_cache(FIXTURES / "project_2interview.json", "Synthesize Fixture Test")
    conn = project_db(projects.project_db_path(pid))
    try:
        # seed.import_cache also replays the LEGACY sequential theorist into theme_v2
        # mode='standard' (0 LLM calls) — clear it so this test exercises SYNTHESIZE against a
        # clean finding set (see synthesize.py's module docstring: the two pipelines must not
        # share live theme_v2 data under the same mode).
        conn.execute("DELETE FROM theme_v2 WHERE mode='standard'")
        conn.execute("DELETE FROM theme_step WHERE mode='standard'")
        conn.commit()
        doc_ids = [r[0] for r in conn.execute("SELECT id FROM document ORDER BY created_at, id")]
        sids_by_doc = {d: [r[0] for r in conn.execute(
            "SELECT id FROM sentence WHERE doc_id=? ORDER BY char_start LIMIT 3", (d,))]
            for d in doc_ids}
        code_by_doc = {d: conn.execute(
            "SELECT id FROM code WHERE origin_doc_id=? LIMIT 1", (d,)).fetchone()[0]
            for d in doc_ids}
    finally:
        conn.close()
    assert len(doc_ids) == 2   # sanity: the fixture really is the 2-interview project

    def fake_chat(system, user, **kw):
        doc_id = next(d for d in doc_ids if f"document {d}" in user)
        return _canned_response(doc_id, sids_by_doc[doc_id], code_by_doc[doc_id])

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    result = jobs.synthesize_work(pid)(lambda **_: None)
    assert set(result["docs"]) == set(doc_ids)

    conn = project_db(projects.project_db_path(pid))
    try:
        findings = store.findings_journal_payload(conn, "standard")
        assert len(findings) == len(doc_ids)   # each canned response mints its own new finding
        assert all(f["standing"] in ("firm", "single-case", "thin") for f in findings)
        assert all(f["supporting_code_ids"] for f in findings)
        for d in doc_ids:
            assert store.get_intro(conn, d)
        story = store.latest_story(conn)
        assert story["paras"] and story["n"] == len(doc_ids)   # one new version per document
        steps = [s for d in doc_ids for s in store.steps_for_doc(conn, "standard", d)]
        assert len(steps) == len(doc_ids)
        assert all(s["kind"] == "pattern" for s in steps)
    finally:
        conn.close()
