"""Lead's integration guards for P10.2 — the two cross-cutting risks the build surfaced.

1. The focus-proposal verdict arrives under either field name (the contract wrote a bare
   {accept|decline}; the engine named it `action`, the frontend reached for `decision`).
2. The legacy theme walk must REFUSE to run once a synthesis exists for that mode, because
   findings share theme_v2 with it and persist_themes replaces a mode wholesale — the failure
   mode is silent destruction of researcher work, so it has to be a loud error.
"""
from __future__ import annotations

import pytest

from masshine import jobs, store
from masshine.api import FocusProposalReq
from masshine.db import project_db


@pytest.fixture
def conn(tmp_path):
    c = project_db(tmp_path / "test.db")
    yield c
    c.close()


@pytest.mark.parametrize("payload,expected", [
    ({"action": "accept"}, "accept"),
    ({"decision": "accept"}, "accept"),
    ({"action": "decline"}, "decline"),
    ({"decision": "DECLINE"}, "decline"),
    ({}, ""),
])
def test_focus_proposal_accepts_either_field_name(payload, expected):
    assert FocusProposalReq(**payload).verdict == expected


def test_legacy_theme_walk_refuses_after_synthesis(conn, monkeypatch, tmp_path):
    """A synthesized mode is off-limits to the old walk — and the message must say what to do."""
    store.insert_step(conn, mode="standard", doc_id="d1", kind="pattern",
                      payload={"statement": "x", "sids": []})
    conn.commit()

    monkeypatch.setattr(jobs.projects, "checkpoint_path", lambda pid, mode: tmp_path / "cp.json")
    monkeypatch.setattr(jobs.projects, "project_db_path", lambda pid: ":memory:")
    monkeypatch.setattr(jobs.runner, "load_checkpoint",
                        lambda cp, **kw: {"order": ["d1"], "docs": {"d1": {}}})
    monkeypatch.setattr(jobs, "project_db", lambda path: conn)

    with pytest.raises(RuntimeError, match="already has synthesized findings"):
        jobs.theme_work("P1", "standard")(lambda **kw: None)


def test_legacy_theme_walk_still_runs_for_an_unsynthesized_mode(conn, monkeypatch, tmp_path):
    """The guard is per-mode: a mode with no steps is untouched by it."""
    store.insert_step(conn, mode="panel", doc_id="d1", kind="pattern",
                      payload={"statement": "x", "sids": []})
    conn.commit()

    monkeypatch.setattr(jobs.projects, "checkpoint_path", lambda pid, mode: tmp_path / "cp.json")
    monkeypatch.setattr(jobs.projects, "project_db_path", lambda pid: ":memory:")
    monkeypatch.setattr(jobs.runner, "load_checkpoint",
                        lambda cp, **kw: {"order": ["d1"], "docs": {"d1": {}}})
    monkeypatch.setattr(jobs, "project_db", lambda path: conn)

    # gets PAST the guard and fails later on the empty stub doc — not with the guard's message
    with pytest.raises(Exception) as e:
        jobs.theme_work("P1", "standard")(lambda **kw: None)
    assert "already has synthesized findings" not in str(e.value)
