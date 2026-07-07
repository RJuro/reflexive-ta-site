"""P8b: theme authority — theme_revision folding (relabel/reclaim/merge/demote/restore),
themes_payload override application (label/claim win, merge unions, provenance sums, hidden by
default), demote-writes-memo, compile_guidance theme lines, and the revise-theme API's
validation. Offline (autouse fixture in conftest blocks any live LLM call)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from masshine import projects, seed, store
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


@pytest.fixture
def conn(tmp_path):
    c = project_db(tmp_path / "test.db")
    yield c
    c.close()


def _seed_theme(c, tid="T01", mode="standard", central_concept="Claim one.",
                label="Label One", supporting=None, tensions=None, subthemes=None,
                key_evidence=None, provenance=None, coverage="1 of 2",
                claim_scope="single-case", falsified_if=""):
    """Insert one theme_v2 row with a realistic payload shape (label/subthemes/provenance/anchors
    all ride in the JSON payload column, per store.persist_themes)."""
    import json
    payload = {
        "label": label,
        "subthemes": subthemes or [],
        "supporting_code_ids": supporting or [],
        "key_evidence_sentence_ids": key_evidence or [],
        "tensions": tensions or [],
    }
    if provenance is not None:
        payload["paradigm_provenance"] = provenance
    c.execute(
        "INSERT INTO theme_v2 (id, run_id, mode, central_concept, coverage, claim_scope, "
        "falsified_if, payload) VALUES (?,?,?,?,?,?,?,?)",
        (tid, "", mode, central_concept, coverage, claim_scope, falsified_if,
         json.dumps(payload)))
    c.commit()


# ---- theme_revisions_map folding --------------------------------------------------------------

def test_relabel_latest_wins(conn):
    _seed_theme(conn, "T01")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "First rename")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "Second rename")
    revs = store.theme_revisions_map(conn, "standard")
    assert revs["T01"]["researcher_label"] == "Second rename"


def test_reclaim_latest_wins(conn):
    _seed_theme(conn, "T01")
    store.add_theme_revision(conn, "standard", "T01", "reclaim", "First claim edit")
    store.add_theme_revision(conn, "standard", "T01", "reclaim", "Second claim edit")
    revs = store.theme_revisions_map(conn, "standard")
    assert revs["T01"]["researcher_claim"] == "Second claim edit"


def test_merge_chain_resolves_to_final_survivor(conn):
    _seed_theme(conn, "T01")
    _seed_theme(conn, "T02")
    _seed_theme(conn, "T03")
    store.add_theme_revision(conn, "standard", "T01", "merge", "T02")  # T01 -> T02
    store.add_theme_revision(conn, "standard", "T02", "merge", "T03")  # T02 (& T01) -> T03
    revs = store.theme_revisions_map(conn, "standard")
    assert revs["T01"]["merged_into"] == "T03"
    assert revs["T02"]["merged_into"] == "T03"
    assert revs.get("T03", {}).get("merged_into") is None


def test_restore_clears_merged_and_demoted(conn):
    _seed_theme(conn, "T01")
    _seed_theme(conn, "T02")
    store.add_theme_revision(conn, "standard", "T01", "merge", "T02")
    store.add_theme_revision(conn, "standard", "T01", "restore")
    revs = store.theme_revisions_map(conn, "standard")
    assert revs["T01"].get("merged_into") is None
    assert not revs["T01"].get("demoted")

    store.add_theme_revision(conn, "standard", "T01", "demote")
    store.add_theme_revision(conn, "standard", "T01", "restore")
    revs = store.theme_revisions_map(conn, "standard")
    assert not revs["T01"].get("demoted")


def test_demote_flags(conn):
    _seed_theme(conn, "T01")
    store.add_theme_revision(conn, "standard", "T01", "demote")
    revs = store.theme_revisions_map(conn, "standard")
    assert revs["T01"]["demoted"] is True


def test_revisions_scoped_by_mode(conn):
    """Theme ids are per-mode (standard vs panel); a revision on one mode must not leak into the
    other's folding, mirroring how theme_v2 rows are scoped."""
    _seed_theme(conn, "T01", mode="standard")
    _seed_theme(conn, "T01", mode="panel")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "Standard-only rename")
    revs_std = store.theme_revisions_map(conn, "standard")
    revs_panel = store.theme_revisions_map(conn, "panel")
    assert revs_std["T01"]["researcher_label"] == "Standard-only rename"
    assert "T01" not in revs_panel or revs_panel["T01"].get("researcher_label") is None


# ---- themes_payload: overrides + merge union + hidden ------------------------------------------

def test_themes_payload_applies_relabel_and_reclaim(conn):
    _seed_theme(conn, "T01", label="Machine Label", central_concept="Machine claim.")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "Researcher Label")
    store.add_theme_revision(conn, "standard", "T01", "reclaim", "Researcher claim.")
    payload = store.themes_payload(conn, "standard")
    t = payload["themes"][0]
    assert t["researcher_label"] == "Researcher Label"
    assert t["researcher_claim"] == "Researcher claim."
    assert t["status"] == "active"


def test_themes_payload_merge_unions_support_anchors_tensions_and_sums_provenance(conn):
    _seed_theme(conn, "T01", supporting=["C0001"], key_evidence=["doc#S1.001"],
               tensions=["C0005"], provenance={"standard": 1})
    _seed_theme(conn, "T02", supporting=["C0001", "C0002"], key_evidence=["doc#S1.002"],
               tensions=["C0006"], provenance={"standard": 1, "critical": 1})
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    payload = store.themes_payload(conn, "standard")
    by_id = {t["id"]: t for t in payload["themes"]}
    survivor = by_id["T01"]
    # order-preserving de-duplicated union
    assert survivor["supporting_code_ids"] == ["C0001", "C0002"]
    assert survivor["key_evidence_sentence_ids"] == ["doc#S1.001", "doc#S1.002"]
    assert survivor["tensions"] == ["C0005", "C0006"]
    assert survivor["paradigm_provenance"] == {"standard": 2, "critical": 1}
    assert by_id["T02"]["status"] == "merged"
    assert by_id["T02"]["merged_into"] == "T01"


def test_themes_payload_merge_union_dedupes_overlapping_evidence(conn):
    _seed_theme(conn, "T01", supporting=["C0001", "C0002"])
    _seed_theme(conn, "T02", supporting=["C0002", "C0003"])
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    payload = store.themes_payload(conn, "standard")
    survivor = next(t for t in payload["themes"] if t["id"] == "T01")
    assert survivor["supporting_code_ids"] == ["C0001", "C0002", "C0003"]


def test_demoted_theme_flagged(conn):
    _seed_theme(conn, "T01")
    store.add_theme_revision(conn, "standard", "T01", "demote")
    payload = store.themes_payload(conn, "standard")
    t = payload["themes"][0]
    assert t["status"] == "demoted"


def test_active_themes_list_excludes_merged_and_demoted_by_default(conn):
    """default themes_payload()["themes"] behavior mirrors codes_payload — merged/demoted rows
    stay in the returned list (so audit/export can see them) but carry status so the UI can
    filter; a helper is used for the UI's default active-only view."""
    _seed_theme(conn, "T01")
    _seed_theme(conn, "T02")
    _seed_theme(conn, "T03")
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    store.add_theme_revision(conn, "standard", "T03", "demote")
    payload = store.themes_payload(conn, "standard")
    active = [t for t in payload["themes"] if t["status"] == "active"]
    assert {t["id"] for t in active} == {"T01"}


def test_restore_after_merge_unwinds_union(conn):
    _seed_theme(conn, "T01", supporting=["C0001"])
    _seed_theme(conn, "T02", supporting=["C0002"])
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    store.add_theme_revision(conn, "standard", "T02", "restore")
    payload = store.themes_payload(conn, "standard")
    by_id = {t["id"]: t for t in payload["themes"]}
    assert by_id["T01"]["supporting_code_ids"] == ["C0001"]
    assert by_id["T02"]["status"] == "active"


# ---- demote writes a memo --------------------------------------------------------------------

def test_demote_writes_memo_with_theme_content(conn):
    _seed_theme(conn, "T01", label="Fading Recall", central_concept="In this account, memory fades.",
               supporting=["C0001", "C0002"])
    store.demote_theme(conn, "standard", "T01")
    memos = store.list_memos(conn, target_type="theme")
    assert len(memos) == 1
    m = memos[0]
    assert m["target_id"] == "T01"
    assert "Fading Recall" in m["body"]
    assert "In this account, memory fades." in m["body"]
    assert "C0001" in m["body"] and "C0002" in m["body"]
    assert "Demoted from theme" in m["body"]


def test_demote_theme_also_sets_demoted_status(conn):
    _seed_theme(conn, "T01")
    store.demote_theme(conn, "standard", "T01")
    payload = store.themes_payload(conn, "standard")
    assert payload["themes"][0]["status"] == "demoted"


def test_merge_does_not_write_a_memo(conn):
    _seed_theme(conn, "T01")
    _seed_theme(conn, "T02")
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    assert store.list_memos(conn, target_type="theme") == []


# ---- compile_guidance theme branch -------------------------------------------------------------

def test_compile_guidance_carries_relabel_demote_merge_lines(conn):
    _seed_theme(conn, "T01", label="Old Label", central_concept="Old claim.")
    _seed_theme(conn, "T02", label="Absorbed Theme", central_concept="Absorbed claim.")
    _seed_theme(conn, "T03", label="Doomed Theme", central_concept="Doomed claim.")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "New Label")
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    store.demote_theme(conn, "standard", "T03")
    guidance = store.compile_guidance(conn, mode="standard")
    assert "New Label" in guidance and "Old Label" in guidance
    assert "renamed" in guidance.lower()
    assert "merged" in guidance.lower() and "Absorbed Theme" in guidance
    assert "demoted" in guidance.lower() and "Doomed Theme" in guidance
    assert "do not re-propose it as a top-level theme" in guidance.lower() or \
        "do not re-propose" in guidance.lower()


def test_compile_guidance_merge_line_uses_current_relabel_not_stale_machine_label(conn):
    """A theme renamed and THEN merged should be named by its latest researcher label in the
    MERGED line specifically (the separate 'renamed' line is expected to still mention the old
    machine label once, to say what changed — but the MERGED line must use the current name on
    both sides, not resurrect the stale machine label)."""
    _seed_theme(conn, "T01", label="Machine Survivor Label")
    _seed_theme(conn, "T02", label="Machine Absorbed Label")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "Renamed Survivor")
    store.add_theme_revision(conn, "standard", "T02", "relabel", "Renamed Absorbed")
    store.add_theme_revision(conn, "standard", "T02", "merge", "T01")
    guidance = store.compile_guidance(conn, mode="standard")
    merged_line = next(l for l in guidance.splitlines() if l.startswith("- The researcher MERGED"))
    assert "Renamed Absorbed" in merged_line and "Renamed Survivor" in merged_line
    assert "Machine Absorbed Label" not in merged_line
    assert "Machine Survivor Label" not in merged_line


def test_compile_guidance_theme_lines_scoped_by_mode(conn):
    _seed_theme(conn, "T01", mode="standard", label="Std Label")
    _seed_theme(conn, "T01", mode="panel", label="Panel Label")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "Std renamed")
    guidance_std = store.compile_guidance(conn, mode="standard")
    guidance_panel = store.compile_guidance(conn, mode="panel")
    assert "Std renamed" in guidance_std
    assert "Std renamed" not in guidance_panel


# ---- n_theme_revisions --------------------------------------------------------------------------

def test_n_theme_revisions_counts_by_mode(conn):
    _seed_theme(conn, "T01", mode="standard")
    _seed_theme(conn, "T01", mode="panel")
    store.add_theme_revision(conn, "standard", "T01", "relabel", "x")
    store.add_theme_revision(conn, "standard", "T01", "reclaim", "y")
    store.add_theme_revision(conn, "panel", "T01", "relabel", "z")
    assert store.n_theme_revisions(conn, "standard") == 2
    assert store.n_theme_revisions(conn, "panel") == 1


# =================================================================================================
# API-level tests
# =================================================================================================

@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return seed.import_cache(FIXTURES / "panel_2interview.json", "Theme Authority Test",
                             "migration_oral_history")


def _theme_ids(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM theme_v2 WHERE mode='panel' ORDER BY id")]
    finally:
        conn.close()
    return ids


def test_api_relabel_roundtrip(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "relabel", "value": "My new label", "mode": "panel"})
    assert r.status_code == 200
    themes = {t["id"]: t for t in client.get(f"/projects/{seeded}/themes?mode=panel").json()["themes"]}
    assert themes[tids[0]]["researcher_label"] == "My new label"


def test_api_reclaim_roundtrip(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "reclaim", "value": "Sharper claim.", "mode": "panel"})
    assert r.status_code == 200
    themes = {t["id"]: t for t in client.get(f"/projects/{seeded}/themes?mode=panel").json()["themes"]}
    assert themes[tids[0]]["researcher_claim"] == "Sharper claim."


def test_api_merge_union_and_hidden(seeded):
    tids = _theme_ids(seeded)
    survivor, absorbed = tids[0], tids[1]
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{absorbed}/revise",
                    json={"action": "merge", "value": survivor, "mode": "panel"})
    assert r.status_code == 200
    themes = {t["id"]: t for t in client.get(f"/projects/{seeded}/themes?mode=panel").json()["themes"]}
    assert themes[absorbed]["status"] == "merged"
    assert themes[absorbed]["merged_into"] == survivor
    # survivor absorbed the merged theme's supporting codes (union, not replacement)
    assert set(themes[absorbed]["supporting_code_ids"]) <= set(themes[survivor]["supporting_code_ids"])


def test_api_demote_writes_memo(seeded):
    tids = _theme_ids(seeded)
    target = tids[0]
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{target}/revise",
                    json={"action": "demote", "mode": "panel"})
    assert r.status_code == 200
    memos = client.get(f"/projects/{seeded}/memos?target_type=theme").json()
    assert any(m["target_id"] == target for m in memos)
    themes = {t["id"]: t for t in client.get(f"/projects/{seeded}/themes?mode=panel").json()["themes"]}
    assert themes[target]["status"] == "demoted"


def test_api_restore_after_demote(seeded):
    tids = _theme_ids(seeded)
    target = tids[0]
    client = TestClient(app)
    client.post(f"/projects/{seeded}/themes/{target}/revise",
               json={"action": "demote", "mode": "panel"})
    r = client.post(f"/projects/{seeded}/themes/{target}/revise",
                    json={"action": "restore", "mode": "panel"})
    assert r.status_code == 200
    themes = {t["id"]: t for t in client.get(f"/projects/{seeded}/themes?mode=panel").json()["themes"]}
    assert themes[target]["status"] == "active"


def test_api_bad_action_400(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "bogus", "mode": "panel"})
    assert r.status_code == 400


def test_api_bad_mode_400(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "relabel", "value": "x", "mode": "nonsense"})
    assert r.status_code == 400


def test_api_revise_unknown_theme_404(seeded):
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/T999/revise",
                    json={"action": "relabel", "value": "x", "mode": "panel"})
    assert r.status_code == 404


def test_api_merge_into_self_400(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "merge", "value": tids[0], "mode": "panel"})
    assert r.status_code == 400


def test_api_merge_into_missing_theme_400(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
                    json={"action": "merge", "value": "T999", "mode": "panel"})
    assert r.status_code == 400


def test_api_merge_into_already_merged_theme_400(seeded):
    tids = _theme_ids(seeded)
    a, b, c = tids[0], tids[1], tids[2]
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{b}/revise",
                    json={"action": "merge", "value": a, "mode": "panel"})
    assert r.status_code == 200
    # now try to merge c into b, which is itself already merged
    r = client.post(f"/projects/{seeded}/themes/{c}/revise",
                    json={"action": "merge", "value": b, "mode": "panel"})
    assert r.status_code == 400


def test_api_merge_cycle_rejected(seeded):
    tids = _theme_ids(seeded)
    a, b = tids[0], tids[1]
    client = TestClient(app)
    r = client.post(f"/projects/{seeded}/themes/{b}/revise",
                    json={"action": "merge", "value": a, "mode": "panel"})
    assert r.status_code == 200
    # a is not (yet) merged, so merging a into b should fail: b is itself merged into a
    r = client.post(f"/projects/{seeded}/themes/{a}/revise",
                    json={"action": "merge", "value": b, "mode": "panel"})
    assert r.status_code == 400


def test_get_project_reports_n_theme_revisions(seeded):
    tids = _theme_ids(seeded)
    client = TestClient(app)
    client.post(f"/projects/{seeded}/themes/{tids[0]}/revise",
               json={"action": "relabel", "value": "x", "mode": "panel"})
    detail = client.get(f"/projects/{seeded}").json()
    assert detail["n_theme_revisions"] == 1


def test_persist_themes_prunes_orphaned_revisions_but_keeps_surviving_ids(conn):
    """Rebuilds replace the catalogue: revisions keyed to vanished ids must die with it,
    revisions keyed to ids that survive (extend-themes) must live."""
    store.add_theme_revision(conn, "panel", "T01", "relabel", "Kept label")
    store.add_theme_revision(conn, "panel", "T99", "relabel", "Orphan label")
    new_set = [{"id": "T01", "central_concept": "still here", "supporting_code_ids": ["C0001"]}]
    store.persist_themes(conn, "panel", new_set, [])
    revs = store.theme_revisions_map(conn, "panel")
    assert "T01" in revs and revs["T01"]["researcher_label"] == "Kept label"
    assert "T99" not in revs
