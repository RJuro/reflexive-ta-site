"""reconcile (merge application) and reconcile_into (incremental id stability).

The model only returns id GROUPINGS; Python applies them. These tests pin that application:
the kept code's label/definition/type wins, evidence unions and dedupes, dropped members fall
back to their own group, a failed call degrades (or raises), and — crucially — established code
ids are never renumbered or reused when a new document folds in.
"""
import sqlite3

import pytest

import llm
import masshine as m


def _code(label, ev, ctype="semantic"):
    return {"label": label, "definition": f"def:{label}", "code_type": ctype, "evidence": list(ev)}


def _stub_groups(monkeypatch, groups):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"groups": groups})


# ---- reconcile: applying the model's groupings --------------------------------------------------

def test_merge_keeps_representative_and_unions_evidence(monkeypatch):
    codes = [_code("A", ["d#S1.000"]), _code("B", ["d#S1.001", "d#S1.000"]), _code("C", ["d#S2.000"])]
    _stub_groups(monkeypatch, [{"keep": "t0", "members": ["t0", "t1"]}])
    out = m.reconcile(codes)
    merged = [c for c in out if c["label"] == "A"]
    passthrough = [c for c in out if c["label"] == "C"]
    assert len(merged) == 1 and len(passthrough) == 1  # A∪B collapsed; C untouched
    assert merged[0]["label"] == "A"  # keep-code's label wins, not B's
    assert merged[0]["evidence"] == ["d#S1.000", "d#S1.001"]  # union, deduped, order preserved


def test_unknown_keep_falls_back_to_first_member(monkeypatch):
    codes = [_code("A", ["d#S1.000"]), _code("B", ["d#S1.001"])]
    _stub_groups(monkeypatch, [{"keep": "tX", "members": ["t0", "t1"]}])
    out = m.reconcile(codes)
    assert len(out) == 1 and out[0]["label"] == "A"


def test_dropped_member_survives_as_own_code(monkeypatch):
    codes = [_code("A", ["d#S1.000"]), _code("B", ["d#S1.001"])]
    _stub_groups(monkeypatch, [{"keep": "t0", "members": ["t0"]}])  # model never mentions t1
    out = m.reconcile(codes)
    assert {c["label"] for c in out} == {"A", "B"}


def test_empty_codes_short_circuits_without_calling_model():
    # no monkeypatch → the autouse guard would fire if reconcile called the model
    assert m.reconcile([]) == []


def test_failed_call_degrades_to_unmerged(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr(llm, "chat_json", boom)
    codes = [_code("A", ["d#S1.000", "d#S1.000"]), _code("B", ["d#S1.001"])]
    out = m.reconcile(codes)  # raise_on_fail defaults False
    assert {c["label"] for c in out} == {"A", "B"}
    assert [c for c in out if c["label"] == "A"][0]["evidence"] == ["d#S1.000"]  # still deduped


def test_failed_call_raises_when_requested(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr(llm, "chat_json", boom)
    with pytest.raises(RuntimeError):
        m.reconcile([_code("A", ["d#S1.000"])], raise_on_fail=True)


# ---- reconcile_into: incremental codebook, stable ids -------------------------------------------

def _seed(conn, run, codes):
    """Seed the project codebook with fresh monotonic ids (bumps meta code_seq honestly)."""
    return m._write_codebook(conn, run, [(None, c) for c in codes])


def test_new_duplicate_absorbs_into_existing_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "t")
    _seed(conn, run, [_code("A", ["d1#S1.000"]), _code("B", ["d1#S1.001"])])  # C0001, C0002
    # tagged temp ids: t0=C0001(A), t1=C0002(B), t2=None(new). Merge the new one into A.
    _stub_groups(monkeypatch, [{"members": ["t0", "t2"]}])
    cb = m.reconcile_into(conn, run, [_code("A-restated", ["d2#S3.000"])])
    assert set(cb) == {"C0001", "C0002"}  # no new id minted
    assert cb["C0001"]["label"] == "A"  # established definition wins over the restatement
    assert cb["C0001"]["evidence"] == ["d1#S1.000", "d2#S3.000"]  # evidence unions across docs


def test_genuinely_new_code_gets_fresh_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "t")
    _seed(conn, run, [_code("A", ["d1#S1.000"]), _code("B", ["d1#S1.001"])])  # C0001, C0002
    _stub_groups(monkeypatch, [])  # model merges nothing
    cb = m.reconcile_into(conn, run, [_code("C", ["d2#S3.000"])])
    assert set(cb) == {"C0001", "C0002", "C0003"}
    assert cb["C0003"]["label"] == "C"


def test_two_existing_merge_lower_id_survives_and_ids_never_reused(monkeypatch):
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "t")
    _seed(conn, run, [_code("A", ["d1#S1.000"]), _code("B", ["d1#S1.001"]),
                      _code("C", ["d1#S1.002"])])  # C0001, C0002, C0003
    # merge existing C0002 + C0003; the new code stays separate (no group)
    _stub_groups(monkeypatch, [{"members": ["t1", "t2"]}])
    cb = m.reconcile_into(conn, run, [_code("D", ["d2#S3.000"])])
    assert "C0003" not in cb  # absorbed
    assert cb["C0002"]["label"] == "B"  # lower id survives
    assert set(cb["C0002"]["evidence"]) == {"d1#S1.001", "d1#S1.002"}
    assert "C0004" in cb and cb["C0004"]["label"] == "D"  # fresh id skips the retired C0003
