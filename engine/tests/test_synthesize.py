"""SYNTHESIZE (P10.2): the Python validators (grounding drops, invented-id drops, the check-back
emptiness rule, standing never trusted from the model) and persistence (findings upsert into
theme_v2 rather than wholesale-replace, steps/checkbacks/residue as `step` rows, intro/story/
focus_proposal, finding_state recomputed for every finding). All offline — see
tests/test_synthesize_job.py for jobs.synthesize_work's orchestration and a full canned-response
run against the seeded fixture project.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from masshine import store, synthesize
from masshine.db import project_db


@pytest.fixture
def conn(tmp_path):
    c = project_db(tmp_path / "test.db")
    yield c
    c.close()


def _seed_doc(c: sqlite3.Connection, doc_id: str = "doc-a", n_sents: int = 4):
    """One section, `n_sents` one-sentence-per-id — enough for grounding tests."""
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


def _seed_code(c: sqlite3.Connection, cid: str, doc_id: str, sid: str, label: str = "Code",
              extra_evidence: list[str] | None = None):
    ev = [f"{doc_id}#{sid}"] + (extra_evidence or [])
    c.execute("INSERT INTO code (id, origin_doc_id, label, definition, code_type, evidence_ids, "
             "model_rationale, coder) VALUES (?,?,?,?,?,?,?,?)",
             (cid, doc_id, label, "def", "semantic", json.dumps(ev), "", "standard"))
    c.commit()


# ---- _resolve_finding / resolve_findings --------------------------------------------------------

def test_resolve_finding_new_finding_gets_empty_id_for_minting():
    item = {"label": "L", "central_concept": "C", "supporting_code_ids": ["C0001"],
           "key_evidence_sentence_ids": ["S1.000"]}
    resolved = synthesize._resolve_finding(item, "", None, {"C0001"}, {"S1.000"}, "doc-a")
    assert resolved["id"] == ""
    assert resolved["supporting_code_ids"] == ["C0001"]
    assert resolved["key_evidence_sentence_ids"] == ["doc-a#S1.000"]


def test_resolve_finding_drops_invented_code_ids_and_becomes_none_if_nothing_grounds():
    item = {"label": "L", "supporting_code_ids": ["C9999"], "key_evidence_sentence_ids": []}
    assert synthesize._resolve_finding(item, "", None, {"C0001"}, set(), "doc-a") is None


def test_resolve_finding_accumulates_against_prior_not_trusting_re_listing():
    """A revision that only mentions THIS document's new support must not lose the PRIOR
    document's support — Python unions, it never trusts the model to re-list its own history
    (themes._resolve_step_themes's established discipline, carried over)."""
    prior = {"id": "T01", "label": "L", "central_concept": "C",
            "supporting_code_ids": ["C0001"], "key_evidence_sentence_ids": ["doc-a#S1.000"],
            "tensions": []}
    item = {"id": "T01", "supporting_code_ids": ["C0002"], "key_evidence_sentence_ids": ["S2.000"]}
    resolved = synthesize._resolve_finding(item, "T01", prior, {"C0001", "C0002"}, {"S2.000"},
                                          "doc-b")
    assert resolved["supporting_code_ids"] == ["C0001", "C0002"]
    assert resolved["key_evidence_sentence_ids"] == ["doc-a#S1.000", "doc-b#S2.000"]


def test_resolve_finding_model_supplied_standing_is_ignored():
    item = {"label": "L", "central_concept": "C", "supporting_code_ids": ["C0001"],
           "key_evidence_sentence_ids": ["S1.000"], "standing": "firm",
           "standing_note": "trust me, it's firm"}
    resolved = synthesize._resolve_finding(item, "", None, {"C0001"}, {"S1.000"}, "doc-a")
    assert "standing" not in resolved and "standing_note" not in resolved


def test_resolve_findings_echoes_valid_prior_id_and_mints_fresh_for_invented_one():
    prior = [{"id": "T01", "label": "L", "central_concept": "C",
             "supporting_code_ids": ["C0001"], "key_evidence_sentence_ids": [], "tensions": []}]
    items = [
        {"id": "T01", "supporting_code_ids": ["C0002"]},   # a real prior id -> update
        {"id": "T99", "supporting_code_ids": ["C0003"]},   # invented id -> treated as new
    ]
    out = synthesize.resolve_findings(items, prior, {"C0001", "C0002", "C0003"}, set(), "doc-a")
    assert out[0]["id"] == "T01" and out[0]["supporting_code_ids"] == ["C0001", "C0002"]
    assert out[1]["id"] == ""   # empty -> caller (persist_synthesis) mints a fresh one


def test_resolve_findings_duplicate_echo_in_same_call_only_first_claims_the_id():
    prior = [{"id": "T01", "label": "L", "central_concept": "C",
             "supporting_code_ids": ["C0001"], "key_evidence_sentence_ids": [], "tensions": []}]
    items = [{"id": "T01", "supporting_code_ids": ["C0002"]},
            {"id": "T01", "supporting_code_ids": ["C0003"]}]
    out = synthesize.resolve_findings(items, prior, {"C0001", "C0002", "C0003"}, set(), "doc-a")
    assert out[0]["id"] == "T01" and out[0]["supporting_code_ids"] == ["C0001", "C0002"]
    assert out[1]["id"] == "" and out[1]["supporting_code_ids"] == ["C0003"]


# ---- _resolve_checkback: the empty-checkback validator (contract §5.2) -------------------------

def test_resolve_checkback_valid():
    item = {"steer": "steer text", "target": "T01",
           "supports": {"text": "supports", "sids": ["S1.000"]},
           "strains": {"text": "", "sids": []}, "not_found": {"text": ""}, "proposal": ""}
    cb = synthesize._resolve_checkback(item, {"T01"}, {"S1.000"}, "doc-a")
    assert cb["target"] == "T01"
    assert cb["supports"]["sids"] == ["doc-a#S1.000"]


def test_resolve_checkback_unknown_target_dropped():
    item = {"steer": "x", "target": "T99", "supports": {"text": "y", "sids": []}}
    assert synthesize._resolve_checkback(item, {"T01"}, set(), "doc-a") is None


def test_resolve_checkback_all_three_empty_is_invalid_and_dropped():
    item = {"steer": "x", "target": "T01", "supports": {"text": "", "sids": []},
           "strains": {"text": "", "sids": []}, "not_found": {"text": ""}}
    assert synthesize._resolve_checkback(item, {"T01"}, set(), "doc-a") is None


def test_resolve_checkback_not_found_alone_is_valid():
    """Reporting that the material has nothing to say about a steer is itself a valid, honest
    check-back — not_found's own text is enough, supports/strains may both be empty."""
    item = {"steer": "x", "target": "T01", "not_found": {"text": "nothing on this in this doc"}}
    cb = synthesize._resolve_checkback(item, {"T01"}, set(), "doc-a")
    assert cb is not None and cb["not_found"]["text"] == "nothing on this in this doc"


# ---- _resolve_residue ----------------------------------------------------------------------------

def test_resolve_residue_valid():
    item = {"note": "n", "sids": ["S1.000"], "code_ids": ["C0001"], "reframe_offer": "r"}
    r = synthesize._resolve_residue(item, {"C0001"}, {"S1.000"}, "doc-a")
    assert r == {"note": "n", "sids": ["doc-a#S1.000"], "code_ids": ["C0001"], "reframe_offer": "r"}


def test_resolve_residue_drops_when_nothing_grounds():
    item = {"note": "n", "sids": ["S9.999"], "code_ids": ["C9999"]}
    assert synthesize._resolve_residue(item, {"C0001"}, {"S1.000"}, "doc-a") is None


# ---- _resolve_steps --------------------------------------------------------------------------

def test_resolve_steps_valid_pattern():
    items = [{"kind": "pattern", "statement": "s", "sids": ["S1.000"], "code_ids": ["C0001"],
             "weakest_sids": ["S1.000"], "finding_id": "T01"}]
    out = synthesize._resolve_steps(items, {"C0001"}, {"S1.000"}, "doc-a", {"T01"})
    assert out[0]["sids"] == ["doc-a#S1.000"] and out[0]["finding_id"] == "T01"


def test_resolve_steps_unknown_kind_dropped():
    items = [{"kind": "bogus", "statement": "s", "sids": ["S1.000"]}]
    assert synthesize._resolve_steps(items, set(), {"S1.000"}, "doc-a", set()) == []


def test_resolve_steps_ungrounded_or_missing_statement_dropped():
    items = [{"kind": "pattern", "statement": "s", "sids": ["S9.999"]},   # ungrounded
            {"kind": "pattern", "statement": "", "sids": ["S1.000"]}]    # no statement
    assert synthesize._resolve_steps(items, set(), {"S1.000"}, "doc-a", set()) == []


def test_resolve_steps_unknown_finding_id_nulled_not_dropped():
    items = [{"kind": "pattern", "statement": "s", "sids": ["S1.000"], "finding_id": "T99"}]
    out = synthesize._resolve_steps(items, set(), {"S1.000"}, "doc-a", {"T01"})
    assert len(out) == 1 and out[0]["finding_id"] is None


# ---- _resolve_paragraphs (shared by intro + story) ---------------------------------------------

def test_resolve_paragraphs_drops_paragraph_whose_sids_all_fail():
    items = [{"para": "p1", "sids": ["S1.000"]}, {"para": "p2", "sids": ["S9.999"]}]
    out = synthesize._resolve_paragraphs(items, {"S1.000"}, "doc-a")
    assert len(out) == 1 and out[0]["para"] == "p1"


def test_resolve_paragraphs_accepts_already_qualified_cross_document_anchor():
    """The story-so-far can echo an anchor from an EARLIER document it cannot see this call — see
    the module docstring's rationale for `valid_qualified`."""
    items = [{"para": "carried forward", "sids": ["doc-b#S3.000"]}]
    out = synthesize._resolve_paragraphs(items, {"S1.000"}, "doc-a",
                                        valid_qualified={"doc-b#S3.000"})
    assert out == [{"para": "carried forward", "sids": ["doc-b#S3.000"]}]


def test_resolve_paragraphs_rejects_a_fabricated_qualified_anchor():
    items = [{"para": "p", "sids": ["doc-z#S1.000"]}]
    out = synthesize._resolve_paragraphs(items, {"S1.000"}, "doc-a",
                                        valid_qualified={"doc-b#S3.000"})
    assert out == []


# ---- _resolve_focus_proposal --------------------------------------------------------------------

def test_resolve_focus_proposal_none_and_empty_text_both_yield_none():
    assert synthesize._resolve_focus_proposal(None) is None
    assert synthesize._resolve_focus_proposal({"text": "   ", "rationale": "x"}) is None


def test_resolve_focus_proposal_valid():
    fp = synthesize._resolve_focus_proposal({"text": "new focus", "rationale": "why"})
    assert fp == {"text": "new focus", "rationale": "why"}


# ---- _codes_touching: evidence-based, not origin_doc_id-based -----------------------------------

def test_codes_touching_counts_a_reused_code_for_every_document_it_cites(conn):
    _seed_doc(conn, "doc-a")
    _seed_doc(conn, "doc-b")
    _seed_code(conn, "C0001", "doc-a", "S1.000", extra_evidence=["doc-b#S1.000"])
    assert [c["id"] for c in synthesize._codes_touching(conn, "doc-a")] == ["C0001"]
    assert [c["id"] for c in synthesize._codes_touching(conn, "doc-b")] == ["C0001"]


# ---- build_user_message: which blocks show up --------------------------------------------------

def test_build_user_message_includes_expected_blocks(conn):
    _seed_doc(conn, "doc-a")
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    doc_codes = synthesize._codes_touching(conn, "doc-a")
    msg = synthesize.build_user_message(conn, "doc-a", doc_codes, [], [], [], "")
    assert "TRANSCRIPT" in msg
    assert "THIS DOCUMENT'S CODES" in msg
    assert "CURRENT FINDINGS: none yet" in msg
    assert "STORY SO FAR: none yet" in msg


def test_build_user_message_omits_empty_optional_blocks(conn):
    _seed_doc(conn, "doc-a")
    msg = synthesize.build_user_message(conn, "doc-a", [], [], [], [], "")
    assert "THIS DOCUMENT'S CODES" not in msg
    assert "RESEARCHER GUIDANCE" not in msg
    assert "UNCLAIMED MATERIAL" not in msg


def test_build_user_message_includes_guidance_when_given(conn):
    _seed_doc(conn, "doc-a")
    msg = synthesize.build_user_message(conn, "doc-a", [], [], [], [], "- a steer")
    assert "RESEARCHER GUIDANCE" in msg and "- a steer" in msg


# ---- _guidance_block: doc-scoped + project-scoped guidance, deduplicated -----------------------

def test_guidance_block_empty_on_clean_project(conn):
    _seed_doc(conn, "doc-a")
    assert synthesize._guidance_block(conn, "doc-a", "standard") == ""


def test_guidance_block_dedupes_the_p10_2_tail(conn):
    """_guidance_block calls compile_guidance TWICE (doc-scoped + project-scoped); its shared
    P10.2 tail (focus/declines/reactions) would otherwise appear twice."""
    _seed_doc(conn, "doc-a")
    store.mint_focus_version(conn, "the focus", "researcher")
    block = synthesize._guidance_block(conn, "doc-a", "standard")
    assert block.count('The current research focus is: "the focus".') == 1


# ---- persistence: no LLM, pure disposal ----------------------------------------------------------

def test_persist_synthesis_upserts_finding_and_recomputes_state(conn):
    _seed_doc(conn, "doc-a")
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    data = {"findings": [{"id": "", "label": "L", "central_concept": "C",
                          "supporting_code_ids": ["C0001"],
                          "key_evidence_sentence_ids": ["doc-a#S1.000"], "tensions": []}],
           "checkbacks": [], "residue": [], "steps": [], "intro": [], "story": [],
           "focus_proposal": None}
    digest = synthesize.persist_synthesis(conn, "standard", "doc-a", data)
    assert digest["n_findings"] == 1
    findings = store.findings_journal_payload(conn, "standard")
    assert len(findings) == 1
    f = findings[0]
    assert f["standing"] == "single-case"
    assert f["evidence_total"] == 1 and f["evidence_opened_count"] == 0


def test_persist_synthesis_writes_steps_checkbacks_residue_as_step_rows_and_excludes_residue_from_session(conn):
    _seed_doc(conn, "doc-a")
    data = {"findings": [],
           "checkbacks": [{"steer": "s", "target": "T01",
                          "supports": {"text": "x", "sids": []},
                          "strains": {"text": "", "sids": []},
                          "not_found": {"text": ""}, "proposal": ""}],
           "residue": [{"note": "n", "sids": ["doc-a#S1.000"], "code_ids": [],
                       "reframe_offer": ""}],
           "steps": [{"kind": "pattern", "statement": "p", "sids": ["doc-a#S1.000"],
                     "code_ids": [], "weakest_sids": [], "finding_id": None}],
           "intro": [], "story": [], "focus_proposal": None}
    synthesize.persist_synthesis(conn, "standard", "doc-a", data)
    steps = store.steps_for_doc(conn, "standard", "doc-a")
    assert [s["kind"] for s in steps] == ["pattern", "checkback"]   # residue excluded from session
    residue = store.residue_items(conn, "standard")
    assert len(residue) == 1 and residue[0]["statement"] == "n"


def test_persist_synthesis_intro_story_and_focus_proposal(conn):
    _seed_doc(conn, "doc-a")
    data = {"findings": [], "checkbacks": [], "residue": [], "steps": [],
           "intro": [{"para": "p1", "sids": ["doc-a#S1.000"]}],
           "story": [{"para": "story para", "sids": ["doc-a#S1.000"]}],
           "focus_proposal": {"text": "new focus", "rationale": "why"}}
    synthesize.persist_synthesis(conn, "standard", "doc-a", data)
    assert store.get_intro(conn, "doc-a") == [{"para": "p1", "sids": ["doc-a#S1.000"]}]
    assert store.latest_story(conn)["paras"] == [{"para": "story para", "sids": ["doc-a#S1.000"]}]
    proposal = store.pending_focus_proposal(conn)
    assert proposal["text"] == "new focus" and proposal["rationale"] == "why"


def test_persist_synthesis_recomputes_every_finding_not_only_this_documents(conn):
    _seed_doc(conn, "doc-a")
    _seed_code(conn, "C0001", "doc-a", "S1.000")
    store.upsert_finding(conn, "standard",
                         {"id": "", "label": "Untouched", "central_concept": "",
                          "supporting_code_ids": ["C0001"],
                          "key_evidence_sentence_ids": [], "tensions": []})
    data = {"findings": [], "checkbacks": [], "residue": [], "steps": [], "intro": [],
           "story": [], "focus_proposal": None}
    synthesize.persist_synthesis(conn, "standard", "doc-a", data)
    states = {r[0] for r in conn.execute("SELECT theme_id FROM finding_state")}
    assert states == {"T01"}


# ---- store.compute_standing (pure) -----------------------------------------------------------

def test_compute_standing_firm_needs_both_thresholds():
    assert store.compute_standing(2, 3)[0] == "firm"
    assert store.compute_standing(3, 10)[0] == "firm"


def test_compute_standing_single_case_regardless_of_code_count():
    assert store.compute_standing(1, 1)[0] == "single-case"
    assert store.compute_standing(1, 10)[0] == "single-case"


def test_compute_standing_thin_when_docs_ok_but_under_the_code_floor():
    assert store.compute_standing(2, 2)[0] == "thin"


def test_compute_standing_thin_when_zero_docs():
    assert store.compute_standing(0, 0)[0] == "thin"
