"""The researcher-feedback layer (schema v3/v4): comments, memos, revisions, and the guidance
block that carries them into the model's prompts on a re-run. All offline."""
from __future__ import annotations

import sqlite3

import pytest

import llm
from masshine import coding, store, themes
from masshine.db import project_db


@pytest.fixture
def conn(tmp_path):
    c = project_db(tmp_path / "test.db")
    yield c
    c.close()


def _seed_doc(c: sqlite3.Connection, doc_id: str = "doc-a"):
    """One document, one section, two sentences — enough for prompt-threading tests."""
    text = "GRANDE: Our clothes were stolen in Trieste. A fellow bought new ones in London."
    c.execute("INSERT INTO document (id, text) VALUES (?,?)", (doc_id, text))
    c.execute("INSERT INTO section (id, doc_id, gist, char_start, char_end) "
              "VALUES ('S1', ?, 'transit', 0, ?)", (doc_id, len(text)))
    c.execute("INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
              "VALUES ('S1.001', ?, 'S1', 0, 44)", (doc_id,))
    c.execute("INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
              "VALUES ('S1.002', ?, 'S1', 45, ?)", (doc_id, len(text)))
    c.commit()


def _seed_code(c: sqlite3.Connection, cid: str = "C0001", doc_id: str = "doc-a",
               label: str = "Material dispossession in transit"):
    c.execute("INSERT INTO code (id, origin_doc_id, label, definition, code_type, evidence_ids, "
              "model_rationale, coder) VALUES (?,?,?,?,?,?,?,?)",
              (cid, doc_id, label, "def", "semantic", f'["{doc_id}#S1.001"]', "", "standard"))
    c.commit()


# ---- comments -------------------------------------------------------------------------------

def test_comment_crud_roundtrip(conn):
    c = store.add_comment(conn, "sentence", "S1.001", "doc-a", "read as gendered labor",
                          {"quote": "Our clothes were stolen"})
    assert c["status"] == "open"
    got = store.list_comments(conn, doc_id="doc-a")
    assert len(got) == 1 and got[0]["body"] == "read as gendered labor"
    assert got[0]["context"]["quote"] == "Our clothes were stolen"

    # edit re-opens; status-only patch does not touch the body
    assert store.set_comment_status(conn, c["id"], "addressed")
    assert store.update_comment(conn, c["id"], body="sharper: kin-broker networks")
    got = store.list_comments(conn)[0]
    assert got["body"] == "sharper: kin-broker networks" and got["status"] == "open"

    assert store.delete_comment(conn, c["id"])
    assert store.list_comments(conn) == []
    assert not store.delete_comment(conn, c["id"])  # second delete: gone


def test_open_comment_counts_scopes_by_doc(conn):
    store.add_comment(conn, "sentence", "S1.001", "doc-a", "a")
    store.add_comment(conn, "code", "C0001", "doc-a", "b")
    store.add_comment(conn, "theme", "T01", None, "c")
    counts = store.open_comment_counts(conn)
    assert counts == {"doc-a": 2, "_project": 1}


# ---- memos ----------------------------------------------------------------------------------

def test_memo_upsert_and_delete(conn):
    store.set_memo(conn, "code", "C0001", "first draft", {"label": "X"})
    store.set_memo(conn, "code", "C0001", "revised analytic memo")
    memos = store.list_memos(conn, target_type="code")
    assert len(memos) == 1 and memos[0]["body"] == "revised analytic memo"
    store.set_memo(conn, "code", "C0001", "   ")  # empty body deletes
    assert store.list_memos(conn) == []


def test_memos_never_enter_guidance(conn):
    _seed_code(conn)
    store.set_memo(conn, "code", "C0001", "private analytic writing")
    assert "private analytic writing" not in store.compile_guidance(conn, "doc-a")


# ---- revisions ------------------------------------------------------------------------------

def test_revision_folding_and_codes_payload(conn):
    _seed_code(conn)
    store.add_revision(conn, "C0001", "rename", "Kin-broker provisioning")
    store.add_revision(conn, "C0001", "reject")
    payload = store.codes_payload(conn)
    assert payload[0]["status"] == "rejected"
    assert payload[0]["researcher_label"] == "Kin-broker provisioning"
    store.add_revision(conn, "C0001", "restore")
    assert store.codes_payload(conn)[0]["status"] == "active"


# ---- guidance compilation --------------------------------------------------------------------

def test_compile_guidance_scoping(conn):
    _seed_code(conn, "C0001", "doc-a")
    _seed_code(conn, "C0002", "doc-b", label="Other doc's code")
    store.add_comment(conn, "sentence", "S1.001", "doc-a", "gendered labor here",
                      {"quote": "Our clothes"})
    store.add_comment(conn, "theme", "T01", None, "split this theme", {"claim": "Household absorbs"})
    store.add_revision(conn, "C0001", "reject")
    store.add_revision(conn, "C0002", "rename", "Renamed elsewhere")

    g_doc = store.compile_guidance(conn, "doc-a")
    assert "gendered labor here" in g_doc and "S1.001" in g_doc
    assert "REJECTED" in g_doc and "Material dispossession" in g_doc
    assert "split this theme" not in g_doc          # theme feedback is project-level
    assert "Renamed elsewhere" not in g_doc         # other doc's revision filtered out

    g_theme = store.compile_guidance(conn)
    assert "split this theme" in g_theme and "Household absorbs" in g_theme
    assert "gendered labor here" not in g_theme     # doc feedback stays with the doc
    assert "Renamed elsewhere" in g_theme           # all revisions summarized for the walk


def test_mark_feedback_addressed_scopes(conn):
    store.add_comment(conn, "sentence", "S1.001", "doc-a", "a")
    store.add_comment(conn, "theme", "T01", None, "b")
    assert store.mark_feedback_addressed(conn, doc_id="doc-a") == 1
    assert store.open_comment_counts(conn) == {"_project": 1}
    assert store.mark_feedback_addressed(conn, target_type="theme") == 1
    assert store.open_comment_counts(conn) == {}


# ---- prompt threading -------------------------------------------------------------------------

def test_coder_prompt_carries_guidance(conn, monkeypatch):
    _seed_doc(conn)
    seen = []

    def fake_chat(system, user, **kw):
        seen.append(user)
        return {"codes": []}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    coding.code_sections(conn, "doc-a", guidance="- On sentence S1.001: gendered labor")
    assert seen and coding.GUIDANCE_HEADER in seen[0]
    assert "gendered labor" in seen[0]
    # and without guidance the block is absent
    seen.clear()
    coding.code_sections(conn, "doc-a")
    assert coding.GUIDANCE_HEADER not in seen[0]


def test_theorist_prompt_carries_guidance(monkeypatch):
    seen = []

    def fake_chat(system, user, **kw):
        seen.append(user)
        return {"themes": []}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    themes.theorize_walk(
        ["doc-a"], {"doc-a": [("C0001", {"code_type": "semantic", "label": "x",
                                         "definition": "d", "evidence": ["doc-a#S1.001"]})]},
        {"C0001": {"evidence": ["doc-a#S1.001"]}}, {"doc-a": "[S1.001] text"},
        {"doc-a": {"S1.001"}}, guidance="- On the theme \"X\": split it")
    assert seen and themes.THEME_GUIDANCE_HEADER in seen[0] and "split it" in seen[0]


# ---- staleness flag ---------------------------------------------------------------------------

def test_themes_stale_flag(conn):
    assert store.themes_stale(conn, "panel") is False
    store.set_themes_stale(conn, "panel", True)
    assert store.themes_stale(conn, "panel") is True
    assert store.themes_stale(conn, "standard") is False
    store.set_themes_stale(conn, "panel", False)
    assert store.themes_stale(conn, "panel") is False
