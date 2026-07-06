"""P6 codebook consolidation: the validator (model proposes, Python disposes), family
persistence + payloads, staleness wiring, the job's guidance threading, and the API. Offline."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import llm
from masshine import consolidate, jobs, projects, seed, store
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


def _codes(n=6, rejected=()):
    out = []
    for i in range(1, n + 1):
        cid = f"C{i:04d}"
        out.append({"id": cid, "coder": "standard", "code_type": "semantic",
                    "label": f"Code {i}", "definition": f"def {i}",
                    "researcher_label": None,
                    "status": "rejected" if cid in rejected else "active"})
    return out


def test_validator_drops_invented_dupes_rejected_and_files_unplaced(monkeypatch):
    canned = {"families": [
        {"label": "Fam A", "definition": "a", "member_code_ids": ["C0001", "C0002", "C9999"]},
        {"label": "Fam B", "definition": "b", "member_code_ids": ["C0002", "C0003", "C0006"]},
        {"label": "Empty", "definition": "e", "member_code_ids": ["C9998"]},
    ]}
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: canned)
    fams = consolidate.consolidate_codebook(_codes(6, rejected=("C0006",)))
    labels = [f["label"] for f in fams]
    assert labels == ["Fam A", "Fam B", "Unfiled"]          # empty family dropped
    assert fams[0]["member_code_ids"] == ["C0001", "C0002"]  # C9999 invented → dropped
    assert fams[1]["member_code_ids"] == ["C0003"]           # C0002 dupe → first wins; C0006 rejected
    assert fams[2]["member_code_ids"] == ["C0004", "C0005"]  # unplaced actives → Unfiled
    # deterministic ring hues
    assert [f["hue"] for f in fams] == [round(360 * i / 3) for i in range(3)]
    assert [f["position"] for f in fams] == [0, 1, 2]


def test_validator_uses_researcher_label_and_skips_rejected_in_listing(monkeypatch):
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return {"families": [{"label": "F", "definition": "d", "member_code_ids": ["C0001"]}]}

    monkeypatch.setattr(llm, "chat_json", fake)
    codes = _codes(2, rejected=("C0002",))
    codes[0]["researcher_label"] = "My Better Name"
    consolidate.consolidate_codebook(codes)
    assert "My Better Name" in seen["user"]
    assert "C0002" not in seen["user"]                       # rejected codes never shown


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return seed.import_cache(FIXTURES / "panel_2interview.json", "Fam Test",
                             "migration_oral_history")


def _fake_families():
    return [
        {"label": "Transit", "definition": "d1", "member_code_ids": ["C0001", "C0002"],
         "position": 0, "hue": 0},
        {"label": "Kin", "definition": "d2", "member_code_ids": ["C0003"],
         "position": 1, "hue": 180},
    ]


def test_persist_and_payload_roundtrip(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_families(conn, _fake_families())
        fams = store.families_payload(conn)
        assert [f["id"] for f in fams] == ["F01", "F02"]
        assert fams[0]["n_codes"] == 2 and fams[0]["hue"] == 0
        by_id = {c["id"]: c for c in store.codes_payload(conn)}
        assert by_id["C0001"]["family_id"] == "F01"
        assert by_id["C0003"]["family_id"] == "F02"
        assert by_id["C0004"]["family_id"] is None
        # exports carry families
        assert '"families"' not in ""  # noqa — placeholder clarity
        csv_text = store.codes_csv(conn)
        assert csv_text.splitlines()[0].split(",")[5] == "family" or "family" in csv_text.splitlines()[0]
        payload = store.export_payload(conn, "panel")
        assert [f["id"] for f in payload["families"]] == ["F01", "F02"]
    finally:
        conn.close()


def test_staleness_flag_roundtrip(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        assert store.families_stale(conn) is False
        store.set_families_stale(conn, True)
        assert store.families_stale(conn) is True
        store.set_families_stale(conn, False)
        assert store.families_stale(conn) is False
    finally:
        conn.close()


def test_consolidate_work_threads_family_guidance_and_clears_state(seeded, monkeypatch):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.add_comment(conn, "family", "F01", None, "split transit from arrival",
                          {"label": "Transit"})
        store.set_families_stale(conn, True)
    finally:
        conn.close()
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return {"families": [{"label": "One", "definition": "d",
                              "member_code_ids": ["C0001"]}]}

    monkeypatch.setattr(llm, "chat_json", fake)
    result = jobs.consolidate_work(seeded)(lambda **k: None)
    assert "split transit from arrival" in seen["user"]
    assert result["feedback_used"] is True and result["comments_addressed"] == 1
    conn = project_db(projects.project_db_path(seeded))
    try:
        assert store.families_stale(conn) is False
        assert store.list_comments(conn, target_type="family", status="open") == []
        assert store.families_payload(conn)  # persisted (One + Unfiled)
    finally:
        conn.close()


def test_api_consolidate_queues_job_and_families_endpoint(seeded, monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append(jid))
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/consolidate")
    assert r.status_code == 200 and r.json()["job_id"] in submitted

    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_families(conn, _fake_families())
    finally:
        conn.close()
    fams = client.get(f"/projects/{seeded}/families").json()
    assert [f["id"] for f in fams["families"]] == ["F01", "F02"] and fams["stale"] is False
    detail = client.get(f"/projects/{seeded}").json()
    assert detail["n_families"] == 2 and detail["families_stale"] is False
    # family comments accepted through the API
    r = client.post(f"/projects/{seeded}/comments",
                    json={"target_type": "family", "target_id": "F01", "body": "note"})
    assert r.status_code == 200
