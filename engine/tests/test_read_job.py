"""jobs.read_work (P10.1a): checkpoint discipline (own kind "read", skip already-read docs), span
resolution (param > MASSHINE_READ_SPAN env > "doc"), the coverage-gate fallback (front-loaded
citations force a re-read at span='sections', replacing the doc's codes), and the API endpoint.
`read.read_document` is monkeypatched throughout — this file tests jobs.py's orchestration, not
READ's own validators (see tests/test_read.py). All offline."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from masshine import jobs, projects, read, store
from masshine.api import app
from masshine.db import project_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return projects.create_project("Read Job Test")["id"]


def _seed_doc(pid: str, doc_id: str = "doc-a", n_sections: int = 6,
             sents_per_section: int = 3) -> None:
    """A synthetic multi-section, multi-sentence document with exact char offsets — minimal
    shape (mirrors tests/test_feedback.py::_seed_doc / test_retheme.py::_seed_third_doc);
    read_work only needs real section/sentence rows, not real transcript content."""
    conn = project_db(projects.project_db_path(pid))
    try:
        pos = 0
        chunks, sec_rows, sent_rows = [], [], []
        for i in range(1, n_sections + 1):
            sec_id = f"S{i}"
            sec_start = pos
            for j in range(sents_per_section):
                sent = f"This is sentence {j} of section {i}, a plain statement. "
                chunks.append(sent)
                start = pos
                pos += len(sent)
                sent_rows.append((f"{sec_id}.{j:03d}", doc_id, sec_id, start, pos))
            sec_rows.append((sec_id, doc_id, f"section {i} gist", sec_start, pos))
        conn.execute(
            "INSERT INTO document (id, text, filename, status, created_at, kind) "
            "VALUES (?,?,?,?,?,?)",
            (doc_id, "".join(chunks), f"{doc_id}.txt", "ingested", _now(), "transcript"))
        conn.executemany(
            "INSERT INTO section (id, doc_id, gist, char_start, char_end) VALUES (?,?,?,?,?)",
            sec_rows)
        conn.executemany(
            "INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
            "VALUES (?,?,?,?,?)", sent_rows)
        conn.commit()
    finally:
        conn.close()


def _empty_read(conn, doc_id, span, research_question=None):
    return [], [], [], {}


# ---- checkpoint discipline ----------------------------------------------------------------------

def test_skips_docs_already_read_this_checkpoint(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    calls = []

    def fake(conn, doc_id, span, research_question=None):
        calls.append(doc_id)
        return _empty_read(conn, doc_id, span)

    monkeypatch.setattr(read, "read_document", fake)
    r1 = jobs.read_work(pid)(lambda **_: None)
    assert calls == ["doc-a"]
    assert set(r1["docs"]) == {"doc-a"}

    r2 = jobs.read_work(pid)(lambda **_: None)
    assert calls == ["doc-a"]        # no second READ call — already read
    assert r2["docs"] == {}


# ---- span resolution: param > env > default ------------------------------------------------

def test_span_param_wins_over_env(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    monkeypatch.setenv("MASSHINE_READ_SPAN", "sections")
    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(span), _empty_read(conn, doc_id, span))[1])
    jobs.read_work(pid, span="doc")(lambda **_: None)
    assert seen == ["doc"]


def test_span_env_used_when_param_omitted(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    monkeypatch.setenv("MASSHINE_READ_SPAN", "groups")
    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(span), _empty_read(conn, doc_id, span))[1])
    jobs.read_work(pid)(lambda **_: None)
    assert seen == ["groups"]


def test_span_defaults_to_doc(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    monkeypatch.delenv("MASSHINE_READ_SPAN", raising=False)
    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(span), _empty_read(conn, doc_id, span))[1])
    jobs.read_work(pid)(lambda **_: None)
    assert seen == ["doc"]


def test_invalid_span_raises_before_any_work(pid):
    with pytest.raises(ValueError):
        jobs.read_work(pid, span="chapters")


# ---- coverage gate: front-loaded citations force a re-read at span='sections' -----------------

def test_coverage_gate_falls_back_to_sections_and_replaces_codes(pid, monkeypatch):
    _seed_doc(pid, "doc-a", n_sections=6, sents_per_section=3)
    calls = []

    def fake_read_document(conn, doc_id, span, research_question=None):
        calls.append(span)
        if span != "sections":
            # every citation packed into the very first sentence -> first30_share == 1.0
            return ([{"label": f"Front{i}", "definition": "d", "code_type": "semantic",
                     "evidence": [f"{doc_id}#S1.000"], "uncertainty": ""} for i in range(5)],
                    [], [], {})
        # one citation per section, spread across the whole document
        return ([{"label": f"Spread{i}", "definition": "d", "code_type": "semantic",
                 "evidence": [f"{doc_id}#S{i + 1}.000"], "uncertainty": ""} for i in range(6)],
                [], [], {})

    monkeypatch.setattr(read, "read_document", fake_read_document)
    result = jobs.read_work(pid)(lambda **_: None)

    assert calls == ["doc", "sections"]           # default span first, then the fallback
    digest = result["docs"]["doc-a"]
    assert digest["span_used"] == "sections"
    assert digest["coverage_fallback"] is True
    assert digest["first30_share"] < 0.45

    conn = project_db(projects.project_db_path(pid))
    try:
        labels = {c["label"] for c in store.codes_payload(conn)}
    finally:
        conn.close()
    assert labels == {f"Spread{i}" for i in range(6)}   # front-loaded attempt rolled back


def test_coverage_gate_does_not_fire_when_already_spread(pid, monkeypatch):
    _seed_doc(pid, "doc-a", n_sections=6, sents_per_section=3)
    calls = []

    def fake_read_document(conn, doc_id, span, research_question=None):
        calls.append(span)
        return ([{"label": f"Spread{i}", "definition": "d", "code_type": "semantic",
                 "evidence": [f"{doc_id}#S{i + 1}.000"], "uncertainty": ""} for i in range(6)],
                [], [], {})

    monkeypatch.setattr(read, "read_document", fake_read_document)
    result = jobs.read_work(pid)(lambda **_: None)
    assert calls == ["doc"]  # no fallback call
    assert result["docs"]["doc-a"]["coverage_fallback"] is False


def test_coverage_gate_does_not_recurse_when_span_is_already_sections(pid, monkeypatch):
    """A gate that fires against a span='sections' READ must not try to re-run at 'sections'
    again — there is no finer fallback."""
    _seed_doc(pid, "doc-a", n_sections=3, sents_per_section=2)
    calls = []

    def fake_read_document(conn, doc_id, span, research_question=None):
        calls.append(span)
        return ([{"label": "Front", "definition": "d", "code_type": "semantic",
                 "evidence": [f"{doc_id}#S1.000"], "uncertainty": ""}], [], [], {})

    monkeypatch.setattr(read, "read_document", fake_read_document)
    result = jobs.read_work(pid, span="sections")(lambda **_: None)
    assert calls == ["sections"]  # one call only
    assert result["docs"]["doc-a"]["coverage_fallback"] is False


# ---- research question passthrough ------------------------------------------------------------

def test_research_question_injected_from_project(pid, monkeypatch):
    _seed_doc(pid, "doc-a")
    conn_reg = projects._registry()
    conn_reg.execute("UPDATE project SET research_question=? WHERE id=?",
                     ("Does migration change kin obligation?", pid))
    conn_reg.commit()
    conn_reg.close()
    assert projects.get_project(pid)["research_question"] == "Does migration change kin obligation?"

    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(research_question), _empty_read(conn, doc_id, span))[1])
    jobs.read_work(pid)(lambda **_: None)
    assert seen == ["Does migration change kin obligation?"]


# ---- API surface ---------------------------------------------------------------------------

def test_post_read_endpoint_returns_job_id(pid, monkeypatch):
    """Same pattern as test_consolidate.py's API test: stub jobs.submit itself so the real
    background executor never runs — this tests the endpoint's job plumbing, not read_work."""
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append(jid))
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/read", json={})
    assert r.status_code == 200
    assert r.json()["job_id"] in submitted


def test_post_read_endpoint_rejects_bad_span(pid):
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/read", json={"span": "chapters"})
    assert r.status_code == 400
