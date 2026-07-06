"""Ingest-layer pure functions: slug, line offsets, numbering, spaCy sentence indexing, and
structure() line-range clamping (with a mocked LLM). All offsets must index the source exactly
(P1: text is resolved from offsets, never regenerated)."""
import sqlite3
from pathlib import Path

import llm
import masshine as m
from masshine.db import init_db, new_run

RAW = (
    "\tPHILLIPS:\tThis is an interview. It has two sentences here.\n"
    "\tGRANDE:\tI was born in a small village. We left in the spring.\n"
    "\tPHILLIPS:\tAnd then what happened next?\n"
)


def test_slug_normalizes_filename():
    assert m._slug(Path("DP-40 GRANDE, M.txt")) == "dp-40-grande-m"
    assert m._slug(Path("EI-845 RODWIN.txt")) == "ei-845-rodwin"


def test_line_offsets_bounds_and_monotonic():
    offs = m._line_offsets(RAW)
    assert offs[0] == 0
    assert offs[-1] == len(RAW)
    assert offs == sorted(offs)
    # offs[L-1] is the char index where 1-based line L starts
    assert RAW[offs[1]:].startswith("\tGRANDE:")


def test_numbered_prefixes_each_line():
    numbered = m._numbered(RAW)
    lines = numbered.splitlines()
    assert lines[0].startswith("0001| ")
    assert lines[1].startswith("0002| ")
    assert lines[2].startswith("0003| ")


def test_sentence_index_offsets_resolve_to_source():
    sections = [{"id": "S1", "gist": "g", "start_line": 1, "end_line": 3,
                 "char_start": 0, "char_end": len(RAW)}]
    sents = m.sentence_index(RAW, sections)
    assert sents, "expected at least one sentence"
    for s in sents:
        assert 0 <= s["char_start"] < s["char_end"] <= len(RAW)
        assert RAW[s["char_start"]:s["char_end"]].strip()  # non-empty verbatim slice
        assert s["section_id"] == "S1"


def test_structure_clamps_out_of_range_lines(monkeypatch):
    n_lines = len(RAW.splitlines())
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "sections": [{"gist": "whole", "start_line": -5, "end_line": 9999}]})
    title, summary, secs = m.structure(RAW)
    assert title is None and summary is None  # old-shape LLM response: front-matter omitted
    assert len(secs) == 1
    s = secs[0]
    assert s["start_line"] == 1
    assert s["end_line"] == n_lines
    assert s["char_start"] == 0
    assert s["char_end"] == len(RAW)
    assert s["gist"] == "whole"


def test_structure_reads_title_and_summary_when_present(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "title": "  Grande — Yugoslavia to Ellis Island  ",
        "summary": "  Covers the journey and arrival.  ",
        "sections": [{"gist": "whole", "start_line": 1, "end_line": len(RAW.splitlines())}]})
    title, summary, secs = m.structure(RAW)
    assert title == "Grande — Yugoslavia to Ellis Island"
    assert summary == "Covers the journey and arrival."
    assert len(secs) == 1


def test_ingest_writes_title_and_summary_to_document_row(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "title": "Grande — Yugoslavia to Ellis Island, 1920",
        "summary": "Covers the journey and arrival.",
        "sections": [{"gist": "whole", "start_line": 1, "end_line": len(RAW.splitlines())}]})
    p = tmp_path / "grande.txt"
    p.write_text(RAW, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    run = new_run(conn, "test")
    doc_id, secs, sents = m.ingest(conn, run, p)
    row = conn.execute("SELECT title, summary FROM document WHERE id=?", (doc_id,)).fetchone()
    assert row == ("Grande — Yugoslavia to Ellis Island, 1920", "Covers the journey and arrival.")


def test_ingest_leaves_title_and_summary_null_when_model_omits_them(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "sections": [{"gist": "whole", "start_line": 1, "end_line": len(RAW.splitlines())}]})
    p = tmp_path / "grande.txt"
    p.write_text(RAW, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    run = new_run(conn, "test")
    doc_id, secs, sents = m.ingest(conn, run, p)
    row = conn.execute("SELECT title, summary FROM document WHERE id=?", (doc_id,)).fetchone()
    assert row == (None, None)


def test_ingest_reads_cp1252_source_without_mojibake(tmp_path, monkeypatch):
    """P1.1 end-to-end: a cp1252 upload (curly apostrophe at 0x92) must not bake a replacement
    char into document.text — the exact bug in DP-5 JOHNSON / NPS-101 KEMPF."""
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "sections": [{"gist": "whole", "start_line": 1, "end_line": 1}]})
    p = tmp_path / "cp1252.txt"
    p.write_bytes(b"PHILLIPS:\tLet\x92s start the interview.\n")
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    run = new_run(conn, "test")
    doc_id, secs, sents = m.ingest(conn, run, p)
    text = conn.execute("SELECT text FROM document WHERE id=?", (doc_id,)).fetchone()[0]
    assert "�" not in text  # no replacement-char mojibake
    assert "Let’s start" in text
