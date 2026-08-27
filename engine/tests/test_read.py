"""READ (P10.1a): span slicing, the Python validators (grounding drops, cap enforcement, invalid
reuse/decline dropping), the codebook block's researcher-first ordering, and persistence (new ids
continue the shared C-sequence, reuse unions evidence, one decline memo per doc). All offline —
the autouse `_no_live_llm` guard proves nothing here reaches a live model.
"""
from __future__ import annotations

import json
import math
import sqlite3

import pytest

import llm
from masshine import projects, read, seed, store
from masshine.db import project_db
from conftest import FIXTURES


# ---- span slicing (pure) -----------------------------------------------------------------------

def test_slices_for_span_doc_is_one_call():
    counts = [("S1", 5), ("S2", 5), ("S3", 5)]
    assert read.slices_for_span(counts, "doc") == [["S1", "S2", "S3"]]


def test_slices_for_span_halves_splits_at_nearest_boundary():
    # cumulative sentence counts: 2, 7, 9, 10 — midpoint 5 is closest after S2 (cum=7, diff 2)
    # vs after S1 (cum=2, diff 3) — so the split lands after S2.
    counts = [("S1", 2), ("S2", 5), ("S3", 2), ("S4", 1)]
    assert read.slices_for_span(counts, "halves") == [["S1", "S2"], ["S3", "S4"]]


def test_slices_for_span_halves_never_splits_a_section_or_empties_a_side():
    # a single lopsided section can't push the split past the ends
    counts = [("S1", 100), ("S2", 1)]
    halves = read.slices_for_span(counts, "halves")
    assert halves == [["S1"], ["S2"]]
    assert all(halves)  # neither side empty


def test_slices_for_span_halves_single_section():
    assert read.slices_for_span([("S1", 5)], "halves") == [["S1"]]


def test_slices_for_span_groups_of_three():
    counts = [(f"S{i}", 1) for i in range(1, 8)]  # 7 sections
    groups = read.slices_for_span(counts, "groups")
    assert groups == [["S1", "S2", "S3"], ["S4", "S5", "S6"], ["S7"]]


def test_slices_for_span_sections_one_call_each():
    counts = [("S1", 1), ("S2", 1)]
    assert read.slices_for_span(counts, "sections") == [["S1"], ["S2"]]


def test_slices_for_span_empty_document():
    assert read.slices_for_span([], "doc") == []
    assert read.slices_for_span([], "halves") == []
    assert read.slices_for_span([], "groups") == []


def test_slices_for_span_unknown_span_raises():
    with pytest.raises(ValueError):
        read.slices_for_span([("S1", 1)], "chapters")


# ---- decile math (pure) --------------------------------------------------------------------

def test_decile_buckets_spreads_by_position():
    # doc_len=100: positions 5, 25, 95 land in deciles 0, 2, 9
    assert read.decile_buckets([5, 25, 95], 100) == [1, 0, 1, 0, 0, 0, 0, 0, 0, 1]


def test_decile_buckets_empty_doc_len_is_all_zero():
    assert read.decile_buckets([1, 2, 3], 0) == [0] * 10


def test_first30_share():
    assert read.first30_share([5, 5, 5, 0, 0, 0, 0, 0, 0, 5]) == pytest.approx(0.75)
    assert read.first30_share([0] * 10) == 0.0


# ---- validators: grounding drops (P1) ----------------------------------------------------------

def test_parse_codes_drops_ungrounded_evidence_and_qualifies_the_rest():
    valid = {"S1.000", "S1.001"}
    items = [
        {"label": "A", "definition": "d", "code_type": "semantic",
         "evidence_sentence_ids": ["S1.000", "S9.999"], "uncertainty": ""},
        {"label": "B", "definition": "d", "code_type": "latent",
         "evidence_sentence_ids": ["S9.999"]},  # fully ungrounded -> dropped entirely
    ]
    codes, dropped = read._parse_codes(items, valid, "doc-a")
    assert dropped == 2  # one bad id from A, one from B
    assert len(codes) == 1
    assert codes[0]["evidence"] == ["doc-a#S1.000"]
    assert codes[0]["code_type"] == "semantic"


def test_parse_codes_uncertainty_carried_no_rationale_field():
    valid = {"S1.000"}
    items = [{"label": "A", "definition": "d", "code_type": "semantic",
             "evidence_sentence_ids": ["S1.000"], "uncertainty": "translation ambiguous"}]
    codes, _ = read._parse_codes(items, valid, "doc-a")
    assert codes[0]["uncertainty"] == "translation ambiguous"
    assert "rationale" not in codes[0] and "model_rationale" not in codes[0]


def test_parse_reuses_drops_invalid_code_id_and_ungrounded():
    valid = {"S1.000"}
    codebook_ids = {"C0001"}
    items = [
        {"code_id": "C0001", "evidence_sentence_ids": ["S1.000"]},          # kept
        {"code_id": "C9999", "evidence_sentence_ids": ["S1.000"]},          # invented id -> dropped
        {"code_id": "C0001", "evidence_sentence_ids": ["S9.999"]},          # ungrounded -> dropped
    ]
    reuses, dropped = read._parse_reuses(items, valid, "doc-a", codebook_ids)
    assert dropped == 2
    assert reuses == [{"code_id": "C0001", "evidence": ["doc-a#S1.000"]}]


def test_parse_out_of_scope_grounds_and_drops():
    valid = {"S1.000", "S1.001"}
    items = [
        {"sentence_ids": ["S1.000", "S9.999"], "reason": "off-topic"},
        {"sentence_ids": ["S9.999"], "reason": "gone"},   # zero grounded -> dropped
    ]
    decl, dropped = read._parse_out_of_scope(items, valid, "doc-a")
    assert dropped == 1
    assert decl == [{"sentence_ids": ["doc-a#S1.000"], "reason": "off-topic"}]


# ---- cap enforcement, exercised through read_document (multi-call span) -----------------------

@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    return seed.import_cache(FIXTURES / "project_2interview.json", "Read Test")


@pytest.fixture
def conn(seeded):
    c = project_db(projects.project_db_path(seeded))
    yield c
    c.close()


DOC = "dp-40-grande-m"


def test_read_document_enforces_cap_across_calls(conn, monkeypatch):
    """Two calls (span='halves'), each proposing 20 fresh codes -> 40 total, capped to 25."""
    def fake_chat(system, user, **kw):
        # cite the first sentence id actually present anywhere in this call's transcript block
        sid = [ln.split("]", 1)[0][1:] for ln in user.splitlines() if ln.startswith("[")][0]
        return {"codes": [{"label": f"L{i}", "definition": "d", "code_type": "semantic",
                           "evidence_sentence_ids": [sid], "uncertainty": ""}
                          for i in range(20)]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    codes, reuses, declines, drop = read.read_document(conn, DOC, span="halves")
    assert len(codes) == read.READ_CODE_CAP == 25
    assert drop["over_cap"] == 40 - 25


def test_read_document_span_doc_makes_one_call(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat_json",
                        lambda s, u, **kw: (calls.append(1), {"codes": []})[1])
    read.read_document(conn, DOC, span="doc")
    assert len(calls) == 1


def test_read_document_span_halves_makes_two_calls(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat_json",
                        lambda s, u, **kw: (calls.append(1), {"codes": []})[1])
    read.read_document(conn, DOC, span="halves")
    assert len(calls) == 2


def test_read_document_span_groups_makes_ceil_n_over_3_calls(conn, monkeypatch):
    n_sections = conn.execute(
        "SELECT COUNT(*) FROM section WHERE doc_id=?", (DOC,)).fetchone()[0]
    calls = []
    monkeypatch.setattr(llm, "chat_json",
                        lambda s, u, **kw: (calls.append(1), {"codes": []})[1])
    read.read_document(conn, DOC, span="groups")
    assert len(calls) == math.ceil(n_sections / read.GROUP_SIZE)


def test_read_document_span_sections_makes_one_call_per_section(conn, monkeypatch):
    n_sections = conn.execute(
        "SELECT COUNT(*) FROM section WHERE doc_id=?", (DOC,)).fetchone()[0]
    calls = []
    monkeypatch.setattr(llm, "chat_json",
                        lambda s, u, **kw: (calls.append(1), {"codes": []})[1])
    read.read_document(conn, DOC, span="sections")
    assert len(calls) == n_sections


def test_read_document_always_sees_full_codebook_every_call(conn, monkeypatch):
    """Reuse-before-mint needs the WHOLE codebook on every slice, not just its neighbors — the
    seeded project already has 136 active codes; every call's user message should carry the
    PROJECT CODEBOOK block."""
    seen_users = []
    monkeypatch.setattr(llm, "chat_json",
                        lambda s, u, **kw: (seen_users.append(u), {"codes": []})[1])
    read.read_document(conn, DOC, span="sections")
    assert seen_users  # at least one call happened
    assert all("PROJECT CODEBOOK" in u for u in seen_users)


# ---- codebook block: researcher codes first, empty -> "" --------------------------------------

def test_codebook_block_researcher_codes_listed_first():
    codes = [
        {"id": "C0001", "coder": "standard", "code_type": "semantic", "label": "Machine code",
         "definition": "d1", "status": "active", "researcher_label": None},
        {"id": "C0002", "coder": "researcher", "code_type": "semantic", "label": "Human code",
         "definition": "d2", "status": "active", "researcher_label": None},
    ]
    block = read.codebook_block(codes)
    assert block.index("Human code") < block.index("Machine code")
    assert "researcher's own codes" in block


def test_codebook_block_skips_rejected_and_merged():
    codes = [
        {"id": "C0001", "coder": "standard", "code_type": "semantic", "label": "Rejected",
         "definition": "d", "status": "rejected", "researcher_label": None},
        {"id": "C0002", "coder": "standard", "code_type": "semantic", "label": "Merged",
         "definition": "d", "status": "merged", "researcher_label": None},
    ]
    assert read.codebook_block(codes) == ""


def test_build_user_message_omits_codebook_block_when_empty(conn):
    msg = read.build_user_message(conn, DOC, "irrelevant raw text", ["S1"], [])
    assert "PROJECT CODEBOOK" not in msg


def test_build_user_message_includes_research_question_line(conn):
    msg = read.build_user_message(conn, DOC, "x", ["S1"], [], research_question="Does X cause Y?")
    assert "RESEARCH QUESTION: Does X cause Y?" in msg


# ---- persistence: no LLM, pure disposal ---------------------------------------------------------

def test_persist_read_new_codes_continue_the_shared_c_sequence(conn):
    before_max = max(int(c["id"][1:]) for c in store.codes_payload(conn))
    new_codes = [{"label": "New one", "definition": "d", "code_type": "semantic",
                 "evidence": [f"{DOC}#S1.000"], "uncertainty": ""}]
    digest = read.persist_read(conn, "R-test", DOC, new_codes, [], [])
    assert digest == {"n_new": 1, "n_reused": 0, "n_declined": 0}
    payload = store.codes_payload(conn)
    ids = [int(c["id"][1:]) for c in payload]
    assert max(ids) == before_max + 1
    new_row = next(c for c in payload if int(c["id"][1:]) == before_max + 1)
    assert new_row["coder"] == "standard" and new_row["label"] == "New one"
    assert new_row["origin_doc_id"] == DOC


def test_persist_read_reuse_unions_evidence_without_duplicates(conn):
    target = store.codes_payload(conn)[0]
    cid = target["id"]
    original_evidence = list(target["evidence"])
    new_sid = f"{DOC}#S1.000"
    reuse = [{"code_id": cid, "evidence": [new_sid] + original_evidence[:1]}]  # incl. a dupe
    read.persist_read(conn, "R-test", DOC, [], reuse, [])
    row = conn.execute("SELECT evidence_ids FROM code WHERE id=?", (cid,)).fetchone()
    evidence = json.loads(row[0])
    assert evidence[:len(original_evidence)] == original_evidence  # old evidence untouched, in order
    assert evidence.count(new_sid) == 1  # no duplicate even though it was already added once
    assert new_sid in evidence


def test_persist_read_ignores_reuse_of_a_code_that_no_longer_exists(conn):
    # defensive path: a codebook drift between validation and persistence must not crash
    digest = read.persist_read(conn, "R-test", DOC, [], [{"code_id": "C9999", "evidence": ["x"]}], [])
    assert digest["n_reused"] == 1  # counted as attempted; persistence just no-ops for it


def test_persist_read_writes_one_decline_memo_authored_by_assistant(conn):
    declines = [{"sentence_ids": [f"{DOC}#S1.000", f"{DOC}#S1.001"], "reason": "off-topic chatter"}]
    read.persist_read(conn, "R-test", DOC, [], [], declines)
    memos = store.list_memos(conn, target_type="document")
    assert len(memos) == 1
    m = memos[0]
    assert m["target_id"] == DOC and m["author"] == "assistant"
    assert "off-topic chatter" in m["body"] and "S1.000" in m["body"]


def test_persist_read_no_declines_leaves_existing_memo_alone(conn):
    store.set_memo(conn, "document", DOC, "earlier run's declines", author="assistant")
    read.persist_read(conn, "R-test", DOC, [], [], [])
    memos = store.list_memos(conn, target_type="document")
    assert len(memos) == 1 and memos[0]["body"] == "earlier run's declines"


# ---- citation_deciles / first30_share against a real (seeded) doc -----------------------------

def test_citation_deciles_counts_real_evidence(conn):
    """The seeded project's codes already cite this doc throughout — deciles should sum to the
    total number of citations into this doc across the whole codebook."""
    deciles = read.citation_deciles(conn, DOC)
    prefix = f"{DOC}#"
    expected = sum(1 for c in store.codes_payload(conn) for e in c["evidence"] if e.startswith(prefix))
    assert sum(deciles) == expected
    assert expected > 0  # sanity: the fixture really does cite this doc
