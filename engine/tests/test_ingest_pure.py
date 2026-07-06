"""Ingest-layer pure functions: slug, line offsets, numbering, spaCy sentence indexing, and
structure() line-range clamping (with a mocked LLM). All offsets must index the source exactly
(P1: text is resolved from offsets, never regenerated)."""
from pathlib import Path

import llm
import masshine as m

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
    secs = m.structure(RAW)
    assert len(secs) == 1
    s = secs[0]
    assert s["start_line"] == 1
    assert s["end_line"] == n_lines
    assert s["char_start"] == 0
    assert s["char_end"] == len(RAW)
    assert s["gist"] == "whole"
