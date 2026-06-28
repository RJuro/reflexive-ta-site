"""MASSHINE backend — step 1 skeleton. Thin FastAPI over the section structure.
Run:  uv run uvicorn app:app --reload
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import masshine as m

app = FastAPI(title="MASSHINE engine", version="0.0.0")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(m.DB_PATH)
    m.init_db(conn)
    return conn


class IngestRequest(BaseModel):
    path: str
    note: str = ""


@app.post("/documents")
def ingest_document(req: IngestRequest):
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"no such file: {path}")
    conn = _conn()
    run_id = m.new_run(conn, note=req.note)
    doc_id, sections, sents = m.ingest(conn, run_id, path)
    return {"doc_id": doc_id, "run_id": run_id,
            "sections": len(sections), "sentences": len(sents)}


@app.get("/documents/{doc_id}/sections")
def list_sections(doc_id: str):
    """Sections with their sentences nested (sub-hierarchy), text resolved by offset."""
    conn = _conn()
    row = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no such document: {doc_id}")
    raw = row[0]
    secs = conn.execute(
        "SELECT id, gist, start_line, end_line, char_start, char_end FROM section "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    sents = conn.execute(
        "SELECT id, section_id, char_start, char_end FROM sentence "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    kids: dict[str, list] = {}
    for r in sents:
        kids.setdefault(r[1], []).append(
            {"id": r[0], "char_start": r[2], "char_end": r[3], "text": raw[r[2]:r[3]]}
        )
    return [{"id": r[0], "gist": r[1], "start_line": r[2], "end_line": r[3],
             "char_start": r[4], "char_end": r[5], "text": raw[r[4]:r[5]],
             "sentences": kids.get(r[0], [])} for r in secs]


@app.get("/documents/{doc_id}/export")
def export_document(doc_id: str):
    conn = _conn()
    if conn.execute("SELECT 1 FROM document WHERE id = ?", (doc_id,)).fetchone() is None:
        raise HTTPException(404, f"no such document: {doc_id}")
    return {"exported": str(m.export_json(conn, doc_id))}
