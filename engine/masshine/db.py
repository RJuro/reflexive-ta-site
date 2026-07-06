"""Persistence primitives: schema, run rows, per-document JSON export, verbatim resolution."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .config import EXPORT_DIR


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS run (id TEXT PRIMARY KEY, created_at TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS document (
            id TEXT PRIMARY KEY, run_id TEXT, path TEXT, text TEXT, text_hash TEXT, char_len INTEGER
        );
        CREATE TABLE IF NOT EXISTS section (
            id TEXT, doc_id TEXT, gist TEXT,
            start_line INTEGER, end_line INTEGER, char_start INTEGER, char_end INTEGER,
            PRIMARY KEY (doc_id, id)
        );
        CREATE TABLE IF NOT EXISTS sentence (
            id TEXT, doc_id TEXT, section_id TEXT, char_start INTEGER, char_end INTEGER,
            PRIMARY KEY (doc_id, id)
        );
        CREATE TABLE IF NOT EXISTS code (
            id TEXT PRIMARY KEY, origin_doc_id TEXT, run_id TEXT,
            label TEXT, definition TEXT, code_type TEXT,
            evidence_ids TEXT, model_rationale TEXT
        );
        CREATE TABLE IF NOT EXISTS theme (
            id TEXT PRIMARY KEY, run_id TEXT, central_concept TEXT,
            supporting_code_ids TEXT, contradicting_code_ids TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
        """
    )
    conn.commit()


def new_run(conn: sqlite3.Connection, note: str = "") -> str:
    run_id = "R" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    conn.execute("INSERT INTO run (id, created_at, note) VALUES (?,?,?)",
                 (run_id, datetime.now(timezone.utc).isoformat(), note))
    conn.commit()
    return run_id


def export_json(conn: sqlite3.Connection, doc_id: str):
    """Artifact: sections with their sentences nested (sub-hierarchy)."""
    doc = conn.execute(
        "SELECT id, run_id, text_hash, char_len FROM document WHERE id = ?", (doc_id,)
    ).fetchone()
    secs = conn.execute(
        "SELECT id, gist, start_line, end_line, char_start, char_end FROM section "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    sents = conn.execute(
        "SELECT id, section_id, char_start, char_end FROM sentence "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    by_section: dict[str, list] = {}
    for r in sents:
        by_section.setdefault(r[1], []).append(
            {"id": r[0], "char_start": r[2], "char_end": r[3]}
        )
    # codes are project-level (evidence is doc-qualified); include the whole codebook
    codes = conn.execute(
        "SELECT id, origin_doc_id, label, definition, code_type, evidence_ids, model_rationale "
        "FROM code ORDER BY id"
    ).fetchall()
    themes = conn.execute(
        "SELECT id, central_concept, supporting_code_ids, contradicting_code_ids "
        "FROM theme ORDER BY id"
    ).fetchall()
    EXPORT_DIR.mkdir(exist_ok=True)
    payload = {
        "document": {"id": doc[0], "run_id": doc[1], "text_hash": doc[2], "char_len": doc[3]},
        "sections": [
            {"id": r[0], "gist": r[1], "start_line": r[2], "end_line": r[3],
             "char_start": r[4], "char_end": r[5], "sentences": by_section.get(r[0], [])}
            for r in secs
        ],
        "codebook": [
            {"id": c[0], "origin_doc_id": c[1], "label": c[2], "definition": c[3],
             "code_type": c[4], "evidence_sentence_ids": json.loads(c[5]),
             "model_rationale": c[6]}
            for c in codes
        ],
        "themes": [
            {"id": t[0], "central_concept": t[1],
             "supporting_code_ids": json.loads(t[2]),
             "contradicting_code_ids": json.loads(t[3])}
            for t in themes
        ],
    }
    out = EXPORT_DIR / f"{doc_id}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out


def resolve(conn: sqlite3.Connection, doc_id: str, sentence_id: str) -> str:
    """Pull verbatim text for a sentence ID from the index (P1: never regenerate)."""
    text = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    cs, ce = conn.execute(
        "SELECT char_start, char_end FROM sentence WHERE doc_id = ? AND id = ?",
        (doc_id, sentence_id),
    ).fetchone()
    return text[cs:ce]


def resolve_ev(conn: sqlite3.Connection, qualified: str) -> str:
    """Resolve a doc-qualified evidence id 'doc_id#sentence_id' to verbatim text."""
    doc_id, sentence_id = qualified.split("#", 1)
    return resolve(conn, doc_id, sentence_id)


# ---- schema v2/v3: per-project database (Phase 3 / feedback loop) ----------------------------
# One SQLite DB per project. The v1 tables above are unchanged; v2 adds durable columns/tables so
# panel codes (per lens) and the rich sequential themes persist in the DB, not only in JSON caches.
# v3 adds the researcher-feedback layer: free-text comments on sentences/codes/themes and code
# revisions (rename / reject). Both compile into a guidance block the model sees on re-runs.
# v4 adds researcher memos (analytic writing — persisted, never sent to the model) and a source
# `kind` on documents (interview / field notes / focus group / document / other).

SCHEMA_VERSION = 4


def init_project_db(conn: sqlite3.Connection) -> None:
    """Create/upgrade a project database to schema v4 (WAL, busy_timeout). Idempotent."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)  # v1 tables (run, document, section, sentence, code, theme, meta)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS theme_v2 (
            id TEXT, run_id TEXT, mode TEXT,
            central_concept TEXT, coverage TEXT, claim_scope TEXT, falsified_if TEXT,
            payload TEXT,                       -- JSON: subthemes, supporting/tensions, provenance, anchors
            PRIMARY KEY (mode, id)
        );
        CREATE TABLE IF NOT EXISTS theme_step (
            mode TEXT, doc_id TEXT, position INTEGER, raw TEXT, snapshot TEXT,
            PRIMARY KEY (mode, doc_id)
        );
        CREATE TABLE IF NOT EXISTS comment (
            id TEXT PRIMARY KEY,
            target_type TEXT,                   -- 'sentence' | 'code' | 'theme' | 'document'
            target_id TEXT,
            doc_id TEXT,                        -- NULL for project-level targets (themes)
            body TEXT,
            context TEXT,                       -- JSON snapshot (label/quote/lens) — survives id churn on recode
            status TEXT DEFAULT 'open',         -- 'open' | 'addressed' | 'dismissed'
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS revision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id TEXT,
            action TEXT,                        -- 'rename' | 'reject' | 'restore'
            new_label TEXT,
            context TEXT,                       -- JSON snapshot of the code at revision time
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS memo (
            target_type TEXT,                   -- 'code' | 'theme' | 'document' | 'project'
            target_id TEXT,
            body TEXT,
            context TEXT,                       -- JSON snapshot (label/claim) — survives id churn
            updated_at TEXT,
            PRIMARY KEY (target_type, target_id)
        );
        """
    )
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Forward-only column adds (no-op once at v2). Hosts future ALTERs."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(document)")}
    if "filename" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN filename TEXT")
    if "status" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN status TEXT DEFAULT 'ingested'")
    if "created_at" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN created_at TEXT")
    if "kind" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN kind TEXT DEFAULT 'transcript'")
    code_cols = {r[1] for r in conn.execute("PRAGMA table_info(code)")}
    if "coder" not in code_cols:
        conn.execute("ALTER TABLE code ADD COLUMN coder TEXT NOT NULL DEFAULT 'standard'")


def project_db(path) -> sqlite3.Connection:
    """Open (and v2-init) a project database at `path`."""
    conn = sqlite3.connect(str(path))
    init_project_db(conn)
    return conn
