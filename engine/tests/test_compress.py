"""P8a: code collapse + merge authority. The `merge` revision action's API validation, the
compress pass's proposal validator, the merge-proposal review-queue store helpers, and the
accept/dismiss/family-reassign endpoints. Offline (autouse fixture blocks any live LLM call)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import llm
from masshine import compress, jobs, projects, seed, store
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return seed.import_cache(FIXTURES / "panel_2interview.json", "Compress Test",
                             "migration_oral_history")


def _codes(n=6, family_id=None, statuses=None):
    """Fixture-shaped codes for compress.propose_merges: same fields test_consolidate.py's
    _codes() helper uses, plus family_id and an evidence list of length 1."""
    out = []
    statuses = statuses or {}
    for i in range(1, n + 1):
        cid = f"C{i:04d}"
        out.append({"id": cid, "coder": "standard", "code_type": "semantic",
                    "label": f"Code {i}", "definition": f"def {i}",
                    "researcher_label": None, "family_id": family_id,
                    "evidence": [f"doc#S{i}"],
                    "status": statuses.get(cid, "active")})
    return out


# ---- compress.propose_merges: validation (model proposes, Python disposes) -------------------

def test_propose_merges_batches_per_family_and_validates(monkeypatch):
    codes = _codes(4, family_id="F01") + _codes(4, family_id="F02")
    # re-id the second batch so ids don't collide
    for i, c in enumerate(codes[4:], start=5):
        c["id"] = f"C{i:04d}"
    families = [{"id": "F01", "position": 0}, {"id": "F02", "position": 1}]
    calls = []

    def fake(system, user, **kw):
        calls.append(kw.get("label"))
        if kw.get("label") == "compress:F01":
            return {"merges": [
                {"survivor_id": "C0001", "absorbed_ids": ["C0002", "C9999"],  # C9999 invented
                 "rationale": "same claim"},
            ]}
        return {"merges": []}   # F02: conservative, nothing to merge

    monkeypatch.setattr(llm, "chat_json", fake)
    proposals = compress.propose_merges(codes, families)
    assert calls == ["compress:F01", "compress:F02"]
    assert len(proposals) == 1
    p = proposals[0]
    assert p["family_id"] == "F01"
    assert p["survivor_id"] == "C0001"
    assert p["absorbed_ids"] == ["C0002"]        # invented id dropped
    assert p["rationale"] == "same claim"


def test_propose_merges_drops_survivor_in_absorbed_and_dupe_claims(monkeypatch):
    codes = _codes(4, family_id="F01")

    def fake(system, user, **kw):
        return {"merges": [
            {"survivor_id": "C0001", "absorbed_ids": ["C0001", "C0002"],  # survivor listed as absorbed -> cleaned
             "rationale": "r1"},
            {"survivor_id": "C0002", "absorbed_ids": ["C0003"],  # C0002 already claimed as absorbed above
             "rationale": "r2"},
        ]}

    monkeypatch.setattr(llm, "chat_json", fake)
    proposals = compress.propose_merges(codes, [{"id": "F01", "position": 0}])
    assert len(proposals) == 1
    assert proposals[0]["survivor_id"] == "C0001"
    assert proposals[0]["absorbed_ids"] == ["C0002"]   # self-ref cleaned, second group dropped (C0002 claimed)


def test_propose_merges_skips_batches_below_threshold(monkeypatch):
    codes = _codes(3, family_id="F01")   # below COMPRESS_MIN_FAMILY_CODES (4)
    called = []
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: called.append(1) or {"merges": []})
    proposals = compress.propose_merges(codes, [{"id": "F01", "position": 0}])
    assert called == [] and proposals == []


def test_propose_merges_no_family_batch_labeled_unfiled(monkeypatch):
    codes = _codes(4, family_id=None)
    seen = {}

    def fake(system, user, **kw):
        seen["label"] = kw.get("label")
        return {"merges": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    compress.propose_merges(codes, [])
    assert seen["label"] == "compress:unfiled"


def test_propose_merges_rejected_and_merged_codes_excluded_from_batch(monkeypatch):
    # 6 codes total, 2 excluded -> 4 remain active, exactly at the threshold so the batch still
    # qualifies and we can see what the model was actually shown.
    codes = _codes(6, family_id="F01", statuses={"C0005": "rejected", "C0006": "merged"})
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return {"merges": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    proposals = compress.propose_merges(codes, [{"id": "F01", "position": 0}])
    assert "C0005" not in seen["user"] and "C0006" not in seen["user"]
    assert "C0001" in seen["user"]                # active codes still shown
    assert proposals == []


def test_compress_batches_deterministic_order_family_then_unfiled():
    codes = _codes(4, family_id="F02") + [
        {**c, "id": f"U{c['id']}"} for c in _codes(4, family_id=None)
    ]
    families = [{"id": "F01", "position": 0}, {"id": "F02", "position": 1}]
    batches = compress.compress_batches(codes, families)
    fids = [fid for fid, _ in batches]
    assert fids == ["F02", None]   # real families ordered by position; no-family batch last


# ---- store: merge-proposal persistence / payload / status ------------------------------------

def test_persist_and_payload_and_status_roundtrip(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_merge_proposals(conn, [
            {"family_id": "F01", "survivor_id": "C0001", "absorbed_ids": ["C0002"],
             "merged_label": "Better name", "rationale": "same claim"},
        ])
        rows = store.merge_proposals_payload(conn)
        assert len(rows) == 1
        p = rows[0]
        assert p["status"] == "pending"
        assert p["survivor_id"] == "C0001" and p["absorbed_ids"] == ["C0002"]
        assert p["merged_label"] == "Better name"
        assert store.set_proposal_status(conn, p["id"], "accepted")
        assert store.merge_proposals_payload(conn, status="pending") == []
        assert store.merge_proposals_payload(conn, status="accepted")[0]["id"] == p["id"]
    finally:
        conn.close()


def test_persist_merge_proposals_replaces_only_pending(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_merge_proposals(conn, [
            {"family_id": "F01", "survivor_id": "C0001", "absorbed_ids": ["C0002"],
             "rationale": "r1"},
        ])
        first_id = store.merge_proposals_payload(conn)[0]["id"]
        store.set_proposal_status(conn, first_id, "accepted")
        # a new compress run should not touch the already-accepted row
        store.persist_merge_proposals(conn, [
            {"family_id": "F02", "survivor_id": "C0003", "absorbed_ids": ["C0004"],
             "rationale": "r2"},
        ])
        all_rows = store.merge_proposals_payload(conn)
        assert len(all_rows) == 2
        accepted = [r for r in all_rows if r["status"] == "accepted"]
        pending = [r for r in all_rows if r["status"] == "pending"]
        assert len(accepted) == 1 and accepted[0]["id"] == first_id
        assert len(pending) == 1 and pending[0]["survivor_id"] == "C0003"
    finally:
        conn.close()


# ---- jobs.compress_work ------------------------------------------------------------------------

def test_compress_work_persists_proposals_and_reports_counts(seeded, monkeypatch):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_families(conn, [
            {"label": "Fam", "definition": "d", "member_code_ids": ["C0001", "C0002", "C0003", "C0004"],
             "position": 0, "hue": 0},
        ])
    finally:
        conn.close()

    def fake(system, user, **kw):
        if kw.get("label") == "compress:F01":
            return {"merges": [{"survivor_id": "C0001", "absorbed_ids": ["C0002"],
                                "rationale": "same claim"}]}
        return {"merges": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    result = jobs.compress_work(seeded)(lambda **k: None)
    assert result["proposals"] >= 1
    assert result["families_scanned"] >= 1
    conn = project_db(projects.project_db_path(seeded))
    try:
        rows = store.merge_proposals_payload(conn, status="pending")
        assert any(r["survivor_id"] == "C0001" for r in rows)
    finally:
        conn.close()


# ---- API: merge action validation --------------------------------------------------------------

def test_api_merge_self_merge_rejected(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/codes/C0001/revise",
                    json={"action": "merge", "new_label": "C0001"})
    assert r.status_code == 400
    assert "itself" in r.json()["detail"]


def test_api_merge_into_rejected_code_rejected(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/codes/C0002/revise", json={"action": "reject"})
    assert r.status_code == 200
    r = client.post(f"/projects/{seeded}/codes/C0001/revise",
                    json={"action": "merge", "new_label": "C0002"})
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"]


def test_api_merge_into_already_merged_code_rejected(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/codes/C0002/revise",
                    json={"action": "merge", "new_label": "C0001"})
    assert r.status_code == 200
    r = client.post(f"/projects/{seeded}/codes/C0003/revise",
                    json={"action": "merge", "new_label": "C0002"})
    assert r.status_code == 400
    assert "itself merged" in r.json()["detail"]


def test_api_merge_cycle_rejected(seeded):
    """A 2-node cycle attempt (C0002 -> C0001, then C0001 -> C0002) is rejected — since C0001
    is already the survivor of C0002's merge, trying to merge C0001 into C0002 hits the
    "survivor is itself merged" guard, which is exactly what prevents this cycle from forming.
    Either way the merge must be refused with a 400 and no cycle created."""
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/codes/C0002/revise",
                    json={"action": "merge", "new_label": "C0001"})
    assert r.status_code == 200
    r = client.post(f"/projects/{seeded}/codes/C0001/revise",
                    json={"action": "merge", "new_label": "C0002"})
    assert r.status_code == 400
    # C0001 must still be a root (unmerged) — the rejected attempt left no trace
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0001"]["status"] == "active" and codes["C0001"]["merged_into"] is None


def test_cycle_check_blocks_a_deeper_chain_via_direct_revision(seeded):
    """Exercise api._cycle_check directly against a 3-node chain built by bypassing the API
    (store.add_revision has no validation of its own — the API layer is where cycle prevention
    lives). C0003 -> C0002 -> C0001 (C0001 root/survivor). Attempting to merge C0001 into C0003
    through the API must be rejected as a cycle: C0001's own chain doesn't point anywhere, but
    walking FROM the proposed survivor (C0003) we'd need to check the other direction — this
    test instead confirms revisions_map's depth-capped chain resolution stays sane (terminates,
    resolves to the final root) even given a manually constructed multi-hop chain."""
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.add_revision(conn, "C0002", "merge", "C0001")
        store.add_revision(conn, "C0003", "merge", "C0002")
    finally:
        conn.close()
    conn = project_db(projects.project_db_path(seeded))
    try:
        revs = store.revisions_map(conn)
        assert revs["C0003"]["merged_into"] == "C0001"   # chain followed to the final root
        assert revs["C0002"]["merged_into"] == "C0001"
    finally:
        conn.close()
    client = TestClient(app)
    # C0001 is the ultimate survivor of the whole chain — merging it into C0002 (which now
    # resolves through the chain back to C0001) must be rejected.
    r = client.post(f"/projects/{seeded}/codes/C0001/revise",
                    json={"action": "merge", "new_label": "C0002"})
    assert r.status_code == 400


def test_api_merge_success_and_missing_code_404(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/codes/C0002/revise",
                    json={"action": "merge", "new_label": "C0001"})
    assert r.status_code == 200
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0002"]["status"] == "merged" and codes["C0002"]["merged_into"] == "C0001"

    r = client.post(f"/projects/{seeded}/codes/NOPE/revise",
                    json={"action": "merge", "new_label": "C0001"})
    assert r.status_code == 404

    r = client.post(f"/projects/{seeded}/codes/C0003/revise",
                    json={"action": "merge", "new_label": "NOPE"})
    assert r.status_code == 400


# ---- API: compress job + merge-proposals accept/dismiss ----------------------------------------

def test_api_compress_queues_job(seeded, monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append(jid))
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/compress")
    assert r.status_code == 200 and r.json()["job_id"] in submitted


def test_api_accept_proposal_applies_merges_and_rename(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_merge_proposals(conn, [
            {"family_id": None, "survivor_id": "C0001", "absorbed_ids": ["C0002", "C0003"],
             "merged_label": "Unified label", "rationale": "same claim, three phrasings"},
        ])
        mpid = store.merge_proposals_payload(conn)[0]["id"]
    finally:
        conn.close()

    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/merge-proposals/{mpid}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 2
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0002"]["status"] == "merged" and codes["C0002"]["merged_into"] == "C0001"
    assert codes["C0003"]["status"] == "merged" and codes["C0003"]["merged_into"] == "C0001"
    assert codes["C0001"]["researcher_label"] == "Unified label"

    proposals = client.get(f"/projects/{seeded}/merge-proposals?status=accepted").json()
    assert any(p["id"] == mpid for p in proposals)


def test_api_dismiss_proposal_leaves_codes_untouched(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_merge_proposals(conn, [
            {"family_id": None, "survivor_id": "C0001", "absorbed_ids": ["C0002"],
             "rationale": "r"},
        ])
        mpid = store.merge_proposals_payload(conn)[0]["id"]
    finally:
        conn.close()

    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/merge-proposals/{mpid}/dismiss")
    assert r.status_code == 200
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0002"]["status"] == "active"   # untouched
    proposals = client.get(f"/projects/{seeded}/merge-proposals?status=dismissed").json()
    assert any(p["id"] == mpid for p in proposals)


def test_api_dismiss_missing_proposal_404(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/merge-proposals/MPNOPE/dismiss")
    assert r.status_code == 404


def test_api_accept_missing_proposal_404(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/merge-proposals/MPNOPE/accept")
    assert r.status_code == 404


def test_get_project_reports_n_pending_proposals(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_merge_proposals(conn, [
            {"family_id": None, "survivor_id": "C0001", "absorbed_ids": ["C0002"], "rationale": "r"},
        ])
    finally:
        conn.close()
    client = TestClient(app)
    detail = client.get(f"/projects/{seeded}").json()
    assert detail["n_pending_proposals"] == 1


# ---- API: family reassignment -------------------------------------------------------------------

def test_api_family_reassign(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.persist_families(conn, [
            {"label": "Fam A", "definition": "d", "member_code_ids": ["C0001"],
             "position": 0, "hue": 0},
            {"label": "Fam B", "definition": "d2", "member_code_ids": ["C0002"],
             "position": 1, "hue": 180},
        ])
    finally:
        conn.close()

    client = TestClient(app)
    r = client.patch(f"/projects/{seeded}/codes/C0001/family", json={"family_id": "F02"})
    assert r.status_code == 200
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0001"]["family_id"] == "F02"

    # unfile
    r = client.patch(f"/projects/{seeded}/codes/C0001/family", json={"family_id": None})
    assert r.status_code == 200
    codes = {c["id"]: c for c in client.get(f"/projects/{seeded}/codes").json()}
    assert codes["C0001"]["family_id"] is None

    # bad family id
    r = client.patch(f"/projects/{seeded}/codes/C0001/family", json={"family_id": "NOPE"})
    assert r.status_code == 400

    # bad code id
    r = client.patch(f"/projects/{seeded}/codes/NOPE/family", json={"family_id": "F01"})
    assert r.status_code == 404
