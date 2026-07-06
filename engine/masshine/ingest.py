"""Ingest layer: structure (LLM 1-shot) + mechanical sentence index (spaCy).

The LLM only returns line numbers; the system maps them to exact char offsets and resolves
verbatim text from source (P1: never regenerate). Coding comes later.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import spacy

from . import llm
from .config import PROMPTS

# ponytail: rule-based sentence splitter, no model download. Sentence splitting is
# format-independent — only document STRUCTURE needs the LLM.
_NLP = spacy.blank("en")
_NLP.add_pipe("sentencizer")


def _slug(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")


def _line_offsets(raw: str) -> list[int]:
    """offs[L-1] = char offset of the start of 1-based line L; offs[-1] = len(raw)."""
    offs, pos = [0], 0
    for line in raw.splitlines(keepends=True):
        pos += len(line)
        offs.append(pos)
    return offs


def _numbered(raw: str) -> str:
    return "".join(f"{i:04d}| {ln}"
                   for i, ln in enumerate(raw.splitlines(keepends=True), start=1))


def structure(raw: str) -> list[dict]:
    """LLM 1-shot → sections (gist + line range + char range)."""
    system = (PROMPTS / "structure.prompt").read_text(encoding="utf-8")
    data = llm.chat_json(system, _numbered(raw), label="structure")
    offs = _line_offsets(raw)
    n_lines = len(offs) - 1
    sections = []
    for i, s in enumerate(data.get("sections", []), start=1):
        a = max(1, min(int(s["start_line"]), n_lines))
        b = max(a, min(int(s["end_line"]), n_lines))
        sections.append({
            "id": f"S{i}", "gist": str(s.get("gist", "")).strip(),
            "start_line": a, "end_line": b,
            "char_start": offs[a - 1], "char_end": offs[b],
        })
    return sections


def sentence_index(raw: str, sections: list[dict]) -> list[dict]:
    """spaCy-split sentences within each section. char offsets index `raw` exactly."""
    sents = []
    for sec in sections:
        k = 0
        for sent in _NLP(raw[sec["char_start"]:sec["char_end"]]).sents:
            if not sent.text.strip():
                continue
            sents.append({
                "id": f"{sec['id']}.{k:03d}", "section_id": sec["id"],
                "char_start": sec["char_start"] + sent.start_char,
                "char_end": sec["char_start"] + sent.end_char,
            })
            k += 1
    return sents


def ingest(conn: sqlite3.Connection, run_id: str, path: Path) -> tuple[str, list[dict], list[dict]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    doc_id = _slug(path)
    sections = structure(raw)
    sents = sentence_index(raw, sections)
    conn.execute("DELETE FROM sentence WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM section WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM document WHERE id = ?", (doc_id,))
    conn.execute(
        "INSERT INTO document (id, run_id, path, text, text_hash, char_len) VALUES (?,?,?,?,?,?)",
        (doc_id, run_id, str(path), raw, hashlib.sha256(raw.encode()).hexdigest()[:16], len(raw)),
    )
    conn.executemany(
        "INSERT INTO section (id, doc_id, gist, start_line, end_line, char_start, char_end) "
        "VALUES (:id,:doc_id,:gist,:start_line,:end_line,:char_start,:char_end)",
        [{**s, "doc_id": doc_id} for s in sections],
    )
    conn.executemany(
        "INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
        "VALUES (:id,:doc_id,:section_id,:char_start,:char_end)",
        [{**s, "doc_id": doc_id} for s in sents],
    )
    conn.commit()
    return doc_id, sections, sents
