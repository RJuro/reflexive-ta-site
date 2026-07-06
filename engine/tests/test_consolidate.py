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


def _multi_source_codes(n_per_doc=25, docs=("docA", "docB")):
    """>40 active codes total, spread across `docs` — puts consolidate_codebook on the
    map-reduce path (2+ sources)."""
    out = []
    i = 0
    for doc_id in docs:
        for _ in range(n_per_doc):
            i += 1
            cid = f"C{i:04d}"
            out.append({"id": cid, "coder": "standard", "code_type": "semantic",
                        "label": f"Code {i}", "definition": f"def {i}",
                        "researcher_label": None, "status": "active",
                        "origin_doc_id": doc_id})
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


def test_small_project_fast_path_uses_one_call(monkeypatch):
    """<=40 active codes across 2 docs still takes the single-call path (only n_sources<=1 or
    <=SMALL_PROJECT_CODE_LIMIT codes trigger it — 2 docs alone shouldn't force map-reduce)."""
    codes = _multi_source_codes(n_per_doc=10, docs=("docA", "docB"))  # 20 total, 2 docs
    assert len({c["origin_doc_id"] for c in codes}) == 2
    calls = []

    def fake(system, user, **kw):
        calls.append(kw.get("label"))
        return {"families": [{"label": "F", "definition": "d",
                              "member_code_ids": [c["id"] for c in codes]}]}

    monkeypatch.setattr(llm, "chat_json", fake)
    fams = consolidate.consolidate_codebook(codes)
    assert calls == ["consolidate"]                          # exactly one call, the old prompt
    assert len(fams) == 1 and fams[0]["label"] == "F"


def test_multi_source_orchestration_calls_and_transitive_membership(monkeypatch):
    codes = _multi_source_codes(n_per_doc=25, docs=("docA", "docB"))  # 50 total, > limit
    by_doc = {"docA": [c["id"] for c in codes if c["origin_doc_id"] == "docA"],
              "docB": [c["id"] for c in codes if c["origin_doc_id"] == "docB"]}
    calls = []

    def fake(system, user, **kw):
        label = kw.get("label")
        calls.append(label)
        if label == "consolidate:src:docA":
            ids = by_doc["docA"]
            return {"families": [
                {"label": "A1", "definition": "a1", "member_code_ids": ids[:15]},
                {"label": "A2", "definition": "a2", "member_code_ids": ids[15:]},
            ]}
        if label == "consolidate:src:docB":
            ids = by_doc["docB"]
            return {"families": [
                {"label": "B1", "definition": "b1", "member_code_ids": ids},
            ]}
        assert label == "consolidate:aggregate"
        # SF01/SF02 from docA (processed first, sorted order), SF03 from docB
        return {"families": [
            {"label": "Merged", "definition": "m", "member_family_ids": ["SF01", "SF03"]},
            {"label": "Solo", "definition": "s", "member_family_ids": ["SF02"]},
        ]}

    monkeypatch.setattr(llm, "chat_json", fake)
    doc_titles = {"docA": "Interview A", "docB": "Interview B"}
    fams = consolidate.consolidate_codebook(codes, doc_titles=doc_titles)

    assert calls == ["consolidate:src:docA", "consolidate:src:docB", "consolidate:aggregate"]
    assert [f["label"] for f in fams] == ["Merged", "Solo"]
    # transitive membership: Merged = SF01 (docA[:15]) + SF03 (all of docB)
    merged = next(f for f in fams if f["label"] == "Merged")
    assert set(merged["member_code_ids"]) == set(by_doc["docA"][:15]) | set(by_doc["docB"])
    solo = next(f for f in fams if f["label"] == "Solo")
    assert set(solo["member_code_ids"]) == set(by_doc["docA"][15:])
    # ring hues from the aggregate's order
    assert [f["hue"] for f in fams] == [round(360 * i / 2) for i in range(2)]
    assert [f["position"] for f in fams] == [0, 1]


def test_aggregate_validation_invented_dropped_unplaced_to_unfiled_dupe_first_wins(monkeypatch):
    codes = _multi_source_codes(n_per_doc=25, docs=("docA", "docB"))
    by_doc = {"docA": [c["id"] for c in codes if c["origin_doc_id"] == "docA"],
              "docB": [c["id"] for c in codes if c["origin_doc_id"] == "docB"]}

    def fake(system, user, **kw):
        label = kw.get("label")
        if label == "consolidate:src:docA":
            ids = by_doc["docA"]
            return {"families": [
                {"label": "A1", "definition": "a1", "member_code_ids": ids[:15]},   # -> SF01
                {"label": "A2", "definition": "a2", "member_code_ids": ids[15:]},   # -> SF02
            ]}
        if label == "consolidate:src:docB":
            ids = by_doc["docB"]
            return {"families": [
                {"label": "B1", "definition": "b1", "member_code_ids": ids},        # -> SF03
            ]}
        assert label == "consolidate:aggregate"
        return {"families": [
            # SF99 invented -> dropped; SF01 kept
            {"label": "Fam1", "definition": "d1", "member_family_ids": ["SF01", "SF99"]},
            # SF01 dupe-claimed here -> first wins (Fam1 keeps it, this drops it) but SF02 kept
            {"label": "Fam2", "definition": "d2", "member_family_ids": ["SF01", "SF02"]},
            # SF03 never claimed by anything -> its codes land in Unfiled
        ]}

    monkeypatch.setattr(llm, "chat_json", fake)
    fams = consolidate.consolidate_codebook(codes)

    labels = [f["label"] for f in fams]
    assert labels == ["Fam1", "Fam2", "Unfiled"]
    fam1 = next(f for f in fams if f["label"] == "Fam1")
    fam2 = next(f for f in fams if f["label"] == "Fam2")
    unfiled = next(f for f in fams if f["label"] == "Unfiled")
    assert set(fam1["member_code_ids"]) == set(by_doc["docA"][:15])       # SF01 only (SF99 dropped)
    assert set(fam2["member_code_ids"]) == set(by_doc["docA"][15:])       # SF02 only (SF01 dupe dropped)
    assert set(unfiled["member_code_ids"]) == set(by_doc["docB"])         # SF03 unclaimed -> Unfiled


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
        assert fams[0]["n_sources"] == 1 and fams[1]["n_sources"] == 1
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


def test_families_payload_reports_n_sources_for_multi_source_family(seeded):
    """C0001 originates on doc 1 (dp-40-grande-m), C0260 on doc 2 (ei-845-rodwin) — a family
    spanning both should report n_sources == 2, confirming the aggregated-across-sources case."""
    conn = project_db(projects.project_db_path(seeded))
    try:
        multi = [{"label": "Cross-case", "definition": "d",
                  "member_code_ids": ["C0001", "C0260"], "position": 0, "hue": 0}]
        store.persist_families(conn, multi)
        fams = store.families_payload(conn)
        assert len(fams) == 1
        assert fams[0]["n_codes"] == 2
        assert fams[0]["n_sources"] == 2
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
    # seeded is 421 codes / 2 docs — above the small-project fast path, so this is now
    # multi-source: 2 per-source calls + 1 aggregate call, guidance only on the last.
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.add_comment(conn, "family", "F01", None, "split transit from arrival",
                          {"label": "Transit"})
        store.set_families_stale(conn, True)
    finally:
        conn.close()
    calls = []

    def fake(system, user, **kw):
        calls.append({"user": user, "label": kw.get("label")})
        if kw.get("label", "").startswith("consolidate:src:"):
            return {"families": [{"label": "One", "definition": "d",
                                  "member_code_ids": ["C0001"]}]}
        return {"families": [{"label": "Agg", "definition": "d",
                              "member_family_ids": ["SF01", "SF02"]}]}

    monkeypatch.setattr(llm, "chat_json", fake)
    result = jobs.consolidate_work(seeded)(lambda **k: None)
    assert len(calls) == 3
    assert "split transit from arrival" not in calls[0]["user"]
    assert "split transit from arrival" not in calls[1]["user"]
    assert "split transit from arrival" in calls[2]["user"]   # guidance only on the aggregate call
    assert result["feedback_used"] is True and result["comments_addressed"] == 1
    conn = project_db(projects.project_db_path(seeded))
    try:
        assert store.families_stale(conn) is False
        assert store.list_comments(conn, target_type="family", status="open") == []
        assert store.families_payload(conn)  # persisted (Agg + Unfiled)
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
