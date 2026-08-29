"""P10.2 loop mechanisms: focus versioning (mint/supersede/propose/accept/decline, registry
mirror sync), step reactions feeding guidance, needs-judgment derivation (each kind, and empty on
a clean project), the evidence-opened gate log, and residue excluding codes a finding has already
claimed — plus the API surface for all of it (session, journal, needs-judgment, focus, steps,
residue). All offline."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from masshine import jobs, projects, store, synthesize
from masshine.api import app
from masshine.db import project_db


@pytest.fixture
def conn(tmp_path):
    c = project_db(tmp_path / "test.db")
    yield c
    c.close()


def _seed_doc(c, doc_id: str = "doc-a", n_sents: int = 3):
    sents = [f"Sentence {i} of {doc_id}." for i in range(n_sents)]
    text = " ".join(sents)
    c.execute("INSERT INTO document (id, text, filename) VALUES (?,?,?)",
             (doc_id, text, f"{doc_id}.txt"))
    c.execute("INSERT INTO section (id, doc_id, gist, char_start, char_end) "
             "VALUES ('S1', ?, 'gist', 0, ?)", (doc_id, len(text)))
    pos = 0
    for i, sent in enumerate(sents):
        start = text.index(sent, pos)
        end = start + len(sent)
        pos = end
        c.execute("INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
                 "VALUES (?,?,?,?,?)", (f"S1.{i:03d}", doc_id, "S1", start, end))
    c.commit()


def _seed_code(c, cid: str, doc_id: str, sid: str, label: str = "Code"):
    c.execute("INSERT INTO code (id, origin_doc_id, label, definition, code_type, evidence_ids, "
             "model_rationale, coder) VALUES (?,?,?,?,?,?,?,?)",
             (cid, doc_id, label, "def", "semantic", json.dumps([f"{doc_id}#{sid}"]), "", "standard"))
    c.commit()


def _project(tmp_path, monkeypatch, name: str = "Test") -> str:
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return projects.create_project(name)["id"]


# ---- focus versioning (store-level) --------------------------------------------------------

def test_mint_focus_version_supersedes_previous_active(conn):
    v1 = store.mint_focus_version(conn, "first focus", "researcher")
    v2 = store.mint_focus_version(conn, "second focus", "researcher")
    assert store.active_focus(conn)["text"] == "second focus"
    history = store.focus_history(conn)
    assert [h["status"] for h in history] == ["superseded", "active"]
    assert v1["n"] == 1 and v2["n"] == 2


def test_propose_focus_does_not_touch_active(conn):
    store.mint_focus_version(conn, "the focus", "researcher")
    store.propose_focus(conn, "a sharper focus", "the material keeps pointing here")
    assert store.active_focus(conn)["text"] == "the focus"
    assert store.pending_focus_proposal(conn)["text"] == "a sharper focus"


def test_propose_focus_clears_earlier_pending_proposal(conn):
    store.propose_focus(conn, "first proposal", "r1")
    store.propose_focus(conn, "second proposal", "r2")
    assert store.pending_focus_proposal(conn)["text"] == "second proposal"


def test_accept_focus_proposal_promotes_it_to_active(conn):
    store.mint_focus_version(conn, "old focus", "researcher")
    store.propose_focus(conn, "new focus", "why")
    n = store.pending_focus_proposal(conn)["n"]
    version = store.accept_focus_proposal(conn, n)
    assert version["status"] == "active" and version["text"] == "new focus"
    assert store.active_focus(conn)["text"] == "new focus"
    assert store.pending_focus_proposal(conn) is None


def test_accept_focus_proposal_unknown_n_returns_none(conn):
    assert store.accept_focus_proposal(conn, 999) is None


def test_decline_focus_proposal(conn):
    store.propose_focus(conn, "declined idea", "why")
    n = store.pending_focus_proposal(conn)["n"]
    assert store.decline_focus_proposal(conn, n) is True
    assert store.pending_focus_proposal(conn) is None
    assert not store.decline_focus_proposal(conn, n)   # already resolved


# ---- focus versioning via the API: registry mirror stays in sync -------------------------------

def test_post_focus_mints_active_version_and_syncs_registry(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/focus", json={"text": "Does migration change kin ties?"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert projects.get_project(pid)["research_question"] == "Does migration change kin ties?"

    r2 = client.post(f"/projects/{pid}/focus", json={"text": "A sharper question"})
    assert r2.status_code == 200
    assert projects.get_project(pid)["research_question"] == "A sharper question"

    conn = project_db(projects.project_db_path(pid))
    try:
        history = store.focus_history(conn)
    finally:
        conn.close()
    assert [h["status"] for h in history] == ["superseded", "active"]


def test_post_focus_rejects_empty_text(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/focus", json={"text": "   "})
    assert r.status_code == 400


def test_focus_proposal_accept_endpoint_syncs_registry(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    conn = project_db(projects.project_db_path(pid))
    try:
        store.propose_focus(conn, "proposed focus", "the data keeps drifting here")
        n = store.pending_focus_proposal(conn)["n"]
    finally:
        conn.close()
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/focus/proposal/{n}", json={"action": "accept"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert projects.get_project(pid)["research_question"] == "proposed focus"


def test_focus_proposal_decline_endpoint_does_not_touch_registry(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    conn = project_db(projects.project_db_path(pid))
    try:
        store.propose_focus(conn, "proposed focus", "why")
        n = store.pending_focus_proposal(conn)["n"]
    finally:
        conn.close()
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/focus/proposal/{n}", json={"action": "decline"})
    assert r.status_code == 200
    assert projects.get_project(pid)["research_question"] is None


def test_focus_proposal_unknown_n_404s(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/focus/proposal/999", json={"action": "accept"})
    assert r.status_code == 404


# ---- read_work sources the active focus over the registry fallback ------------------------------

def test_read_work_prefers_active_focus_over_registry(tmp_path, monkeypatch):
    from masshine import read
    pid = _project(tmp_path, monkeypatch, "RQ Test")
    conn_reg = projects._registry()
    conn_reg.execute("UPDATE project SET research_question=? WHERE id=?",
                     ("registry question", pid))
    conn_reg.commit()
    conn_reg.close()
    conn = project_db(projects.project_db_path(pid))
    try:
        store.mint_focus_version(conn, "focus-version question", "researcher")
        conn.execute(
            "INSERT INTO document (id, text, filename, status, created_at) VALUES "
            "('doc-a','x','doc-a.txt','ingested','2026-01-01T00:00:00Z')")
        conn.commit()
    finally:
        conn.close()
    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(research_question), ([], [], [], {}))[1])
    jobs.read_work(pid)(lambda **_: None)
    assert seen == ["focus-version question"]


def test_read_work_falls_back_to_registry_when_no_focus_minted(tmp_path, monkeypatch):
    from masshine import read
    pid = _project(tmp_path, monkeypatch, "RQ Test")
    conn_reg = projects._registry()
    conn_reg.execute("UPDATE project SET research_question=? WHERE id=?",
                     ("registry question", pid))
    conn_reg.commit()
    conn_reg.close()
    conn = project_db(projects.project_db_path(pid))
    try:
        conn.execute(
            "INSERT INTO document (id, text, filename, status, created_at) VALUES "
            "('doc-a','x','doc-a.txt','ingested','2026-01-01T00:00:00Z')")
        conn.commit()
    finally:
        conn.close()
    seen = []
    monkeypatch.setattr(read, "read_document",
                        lambda conn, doc_id, span, research_question=None:
                        (seen.append(research_question), ([], [], [], {}))[1])
    jobs.read_work(pid)(lambda **_: None)
    assert seen == ["registry question"]


# ---- step reactions persist and feed guidance ---------------------------------------------------

def test_react_to_step_persists_and_appears_in_guidance(conn):
    _seed_doc(conn)
    step = store.insert_step(conn, "standard", "doc-a", "pattern",
                             {"statement": "A recurring pattern.", "sids": ["doc-a#S1.000"],
                              "code_ids": [], "weakest_sids": [], "finding_id": None})
    reacted = store.react_to_step(conn, step["id"], "challenge", note="I don't buy this")
    assert reacted["reaction"] == "challenge" and reacted["reaction_note"] == "I don't buy this"
    guidance = store.compile_guidance(conn, "doc-a")
    assert "challenged the walkthrough step" in guidance
    assert "A recurring pattern." in guidance and "I don't buy this" in guidance


def test_react_reframe_rewrites_statement_and_keeps_original(conn):
    _seed_doc(conn)
    step = store.insert_step(conn, "standard", "doc-a", "pattern",
                             {"statement": "original wording", "sids": ["doc-a#S1.000"],
                              "code_ids": [], "weakest_sids": [], "finding_id": None})
    reacted = store.react_to_step(conn, step["id"], "reframe", statement="my sharper wording")
    assert reacted["statement"] == "my sharper wording"
    row = store.steps_for_doc(conn, "standard", "doc-a")[0]
    assert row["statement"] == "my sharper wording"
    raw = conn.execute("SELECT payload FROM step WHERE id=?", (step["id"],)).fetchone()[0]
    assert json.loads(raw)["original_statement"] == "original wording"


def test_react_to_step_recomputes_finding_stance_immediately(conn):
    _seed_doc(conn)
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001"],
                                            "key_evidence_sentence_ids": [], "tensions": []})
    store.recompute_finding_state(conn, "standard", "T01")
    step = store.insert_step(conn, "standard", "doc-a", "pattern",
                             {"statement": "s", "sids": ["doc-a#S1.000"], "code_ids": [],
                              "weakest_sids": [], "finding_id": "T01"})
    store.react_to_step(conn, step["id"], "challenge", note="strains under scrutiny")
    stance = conn.execute(
        "SELECT stance FROM finding_state WHERE theme_id='T01'").fetchone()[0]
    assert stance == "challenge"


def test_react_to_unknown_step_returns_none(conn):
    assert store.react_to_step(conn, "ST00000000", "agree") is None


def test_adopted_reframe_appears_in_guidance(conn):
    _seed_doc(conn)
    store.insert_step(conn, "standard", "doc-a", "residue",
                      {"note": "an odd passage", "sids": ["doc-a#S1.000"], "code_ids": [],
                       "reframe_offer": "read this as institutional distrust, not a grudge"})
    store.adopt_reframe(conn, "standard", 0)
    guidance = store.compile_guidance(conn, "doc-a")
    assert "institutional distrust" in guidance


# ---- needs-judgment derivation --------------------------------------------------------------

def test_needs_judgment_empty_on_clean_project(conn):
    assert store.needs_judgment_payload(conn, "standard") == []


def test_needs_judgment_unanswered_checkback(conn):
    _seed_doc(conn)
    store.insert_step(conn, "standard", "doc-a", "checkback",
                      {"steer": "the researcher's steer", "target": "T01",
                       "supports": {"text": "x", "sids": []}, "strains": {"text": "", "sids": []},
                       "not_found": {"text": ""}, "proposal": ""})
    items = store.needs_judgment_payload(conn, "standard")
    assert [i["kind"] for i in items] == ["checkback"]


def test_needs_judgment_answered_checkback_excluded(conn):
    _seed_doc(conn)
    step = store.insert_step(conn, "standard", "doc-a", "checkback",
                             {"steer": "s", "target": "T01",
                              "supports": {"text": "x", "sids": []},
                              "strains": {"text": "", "sids": []}, "not_found": {"text": ""},
                              "proposal": ""})
    store.react_to_step(conn, step["id"], "agree")
    assert store.needs_judgment_payload(conn, "standard") == []


def test_needs_judgment_challenged_finding(conn):
    _seed_doc(conn)
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001"],
                                            "key_evidence_sentence_ids": [], "tensions": []})
    store.recompute_finding_state(conn, "standard", "T01")
    conn.execute("UPDATE finding_state SET stance='challenge' WHERE theme_id='T01'")
    conn.commit()
    items = store.needs_judgment_payload(conn, "standard")
    assert [i["kind"] for i in items] == ["strained_finding"]


def test_needs_judgment_thin_finding(conn):
    _seed_doc(conn, "doc-a")
    _seed_doc(conn, "doc-b")
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    _seed_code(conn, "C0002", "doc-b", "S1.000")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001", "C0002"],
                                            "key_evidence_sentence_ids": [], "tensions": []})
    store.recompute_finding_state(conn, "standard", "T01")   # 2 docs, 2 codes -> under the floor
    items = store.needs_judgment_payload(conn, "standard")
    assert [i["kind"] for i in items] == ["thin_finding"]


def test_needs_judgment_single_case_finding_is_not_an_exception(conn):
    """A single-case finding is expected early in a project — not itself an exception."""
    _seed_doc(conn)
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001"],
                                            "key_evidence_sentence_ids": [], "tensions": []})
    store.recompute_finding_state(conn, "standard", "T01")
    assert store.needs_judgment_payload(conn, "standard") == []


def test_needs_judgment_pending_focus_proposal(conn):
    store.propose_focus(conn, "sharper focus", "why")
    items = store.needs_judgment_payload(conn, "standard")
    assert [i["kind"] for i in items] == ["focus_proposal"]


def test_needs_judgment_residue_with_unresolved_reframe_offer(conn):
    _seed_doc(conn)
    store.insert_step(conn, "standard", "doc-a", "residue",
                      {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [],
                       "reframe_offer": "a possible reframe"})
    items = store.needs_judgment_payload(conn, "standard")
    assert [i["kind"] for i in items] == ["residue"]


def test_needs_judgment_residue_without_reframe_offer_is_not_an_exception(conn):
    _seed_doc(conn)
    store.insert_step(conn, "standard", "doc-a", "residue",
                      {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [], "reframe_offer": ""})
    assert store.needs_judgment_payload(conn, "standard") == []


def test_needs_judgment_adopted_residue_no_longer_an_exception(conn):
    _seed_doc(conn)
    store.insert_step(conn, "standard", "doc-a", "residue",
                      {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [],
                       "reframe_offer": "a possible reframe"})
    store.adopt_reframe(conn, "standard", 0)
    assert store.needs_judgment_payload(conn, "standard") == []


def test_needs_judgment_endpoint_empty_on_clean_project(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "NJ Test")
    client = TestClient(app)
    r = client.get(f"/projects/{pid}/needs-judgment")
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_needs_judgment_endpoint_includes_audio_review(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "NJ Test")
    uploads = projects.uploads_dir(pid)
    (uploads / "interview.asr.json").write_text(json.dumps({"segments": [], "roles": {}}))
    (uploads / "interview.txt").write_text("PHILLIPS: hi.\n")
    client = TestClient(app)
    r = client.get(f"/projects/{pid}/needs-judgment")
    body = r.json()
    assert body["count"] == 1 and body["items"][0]["kind"] == "audio_review"


# ---- evidence-opened gate log --------------------------------------------------------------

def test_mark_evidence_opened_accumulates_idempotently(conn):
    _seed_doc(conn)
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001"],
                                            "key_evidence_sentence_ids": ["doc-a#S1.000"],
                                            "tensions": []})
    store.recompute_finding_state(conn, "standard", "T01")
    assert store.mark_evidence_opened(conn, "T01", "doc-a#S1.000") == ["doc-a#S1.000"]
    assert store.mark_evidence_opened(conn, "T01", "doc-a#S1.000") == ["doc-a#S1.000"]  # idempotent
    assert (store.mark_evidence_opened(conn, "T01", "doc-a#S1.001")
           == ["doc-a#S1.000", "doc-a#S1.001"])


def test_mark_evidence_opened_unknown_finding_returns_none(conn):
    assert store.mark_evidence_opened(conn, "T99", "doc-a#S1.000") is None


def test_evidence_opened_endpoint(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "EO Test")
    conn = project_db(projects.project_db_path(pid))
    try:
        _seed_doc(conn)
        _seed_code(conn, "C0001", "doc-a", "S1.000")
        store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                                "supporting_code_ids": ["C0001"],
                                                "key_evidence_sentence_ids": ["doc-a#S1.000"],
                                                "tensions": []})
        store.recompute_finding_state(conn, "standard", "T01")
    finally:
        conn.close()
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/findings/T01/evidence-opened", json={"sid": "doc-a#S1.000"})
    assert r.status_code == 200
    assert r.json()["opened_evidence"] == ["doc-a#S1.000"]

    r2 = client.get(f"/projects/{pid}/journal")
    findings = {f["id"]: f for f in r2.json()["findings"]}
    assert findings["T01"]["evidence_opened_count"] == 1
    assert findings["T01"]["evidence_total"] == 1


def test_evidence_opened_endpoint_unknown_finding_404s(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "EO Test")
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/findings/T99/evidence-opened", json={"sid": "doc-a#S1.000"})
    assert r.status_code == 404


# ---- residue excludes codes already claimed by a finding ----------------------------------------

def test_residue_candidates_exclude_codes_claimed_by_a_finding(conn):
    _seed_doc(conn)
    _seed_code(conn, "C0001", "doc-a", "S1.000", label="Claimed")
    _seed_code(conn, "C0002", "doc-a", "S1.001", label="Unclaimed")
    store.upsert_finding(conn, "standard", {"id": "", "label": "F", "central_concept": "c",
                                            "supporting_code_ids": ["C0001"],
                                            "key_evidence_sentence_ids": [], "tensions": []})
    doc_codes = synthesize._codes_touching(conn, "doc-a")
    prior = store.findings_for_mode(conn, "standard")
    claimed = {cid for f in prior for cid in f.get("supporting_code_ids", [])}
    unclaimed = [c for c in doc_codes if c["id"] not in claimed]
    assert [c["id"] for c in unclaimed] == ["C0002"]


# ---- session / journal / steps / residue API surface --------------------------------------------

def test_session_endpoint_404_for_unknown_doc(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "Session Test")
    client = TestClient(app)
    r = client.get(f"/projects/{pid}/session/doc-a")
    assert r.status_code == 404


def test_session_endpoint_excludes_residue_includes_checkback(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "Session Test")
    conn = project_db(projects.project_db_path(pid))
    try:
        _seed_doc(conn)
        store.insert_step(conn, "standard", "doc-a", "pattern",
                          {"statement": "p", "sids": ["doc-a#S1.000"], "code_ids": [],
                           "weakest_sids": [], "finding_id": None})
        store.insert_step(conn, "standard", "doc-a", "checkback",
                          {"steer": "s", "target": "T01", "supports": {"text": "x", "sids": []},
                           "strains": {"text": "", "sids": []}, "not_found": {"text": ""},
                           "proposal": ""})
        store.insert_step(conn, "standard", "doc-a", "residue",
                          {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [],
                           "reframe_offer": ""})
    finally:
        conn.close()
    client = TestClient(app)
    r = client.get(f"/projects/{pid}/session/doc-a")
    body = r.json()
    assert body["n_steps"] == 2
    assert [s["kind"] for s in body["steps"]] == ["pattern", "checkback"]
    assert body["steps"][1]["checkback"]["target"] == "T01"


def test_step_react_endpoint(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "React Test")
    conn = project_db(projects.project_db_path(pid))
    try:
        _seed_doc(conn)
        step = store.insert_step(conn, "standard", "doc-a", "pattern",
                                 {"statement": "p", "sids": ["doc-a#S1.000"], "code_ids": [],
                                  "weakest_sids": [], "finding_id": None})
    finally:
        conn.close()
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/steps/{step['id']}/react", json={"reaction": "park"})
    assert r.status_code == 200 and r.json()["step"]["reaction"] == "park"

    r2 = client.post(f"/projects/{pid}/steps/{step['id']}/react", json={"reaction": "bogus"})
    assert r2.status_code == 400

    r3 = client.post(f"/projects/{pid}/steps/ST00000000/react", json={"reaction": "agree"})
    assert r3.status_code == 404


def test_residue_reframe_endpoint(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "Residue Test")
    conn = project_db(projects.project_db_path(pid))
    try:
        _seed_doc(conn)
        store.insert_step(conn, "standard", "doc-a", "residue",
                          {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [],
                           "reframe_offer": "reframe idea"})
    finally:
        conn.close()
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/residue/0/reframe")
    assert r.status_code == 200 and r.json()["step"]["reaction"] == "reframe"

    r2 = client.post(f"/projects/{pid}/residue/5/reframe")
    assert r2.status_code == 404


def test_journal_endpoint_shape_on_clean_project(tmp_path, monkeypatch):
    pid = _project(tmp_path, monkeypatch, "Journal Test")
    client = TestClient(app)
    body = client.get(f"/projects/{pid}/journal").json()
    assert body["focus"] == {"active": None, "history": [], "proposal": None}
    assert body["story"] == {"n": 0, "paras": [], "versions": []}
    assert body["findings"] == [] and body["residue"] == [] and body["history"] == []
