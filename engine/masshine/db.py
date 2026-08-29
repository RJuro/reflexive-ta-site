"""Persistence primitives: schema, run rows, per-document JSON export, verbatim resolution."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import llm
from .config import EXPORT_DIR


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS run (
            id TEXT PRIMARY KEY, created_at TEXT, note TEXT, model TEXT
        );
        CREATE TABLE IF NOT EXISTS document (
            id TEXT PRIMARY KEY, run_id TEXT, path TEXT, text TEXT, text_hash TEXT, char_len INTEGER,
            title TEXT, summary TEXT
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
    """P10.1c: every run row auto-captures llm.model() — the ACTIVE resolved model (an active
    jobs.py `use_model()` override if one is set, else today's env default) — so a project coded
    partly under one model and partly under another stays honest run-by-run (see
    store.export_payload's manifest)."""
    run_id = "R" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    conn.execute("INSERT INTO run (id, created_at, note, model) VALUES (?,?,?,?)",
                 (run_id, datetime.now(timezone.utc).isoformat(), note, llm.model()))
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
# v5 adds LLM-authored document front-matter (`title`, `summary` — nullable; the structure()
# call already reads the whole transcript, so these ride the same LLM call at ~0 extra cost).
# v6 adds identity-lite authorship: `comment.author`, `memo.author` (nullable — a coauthor's
# display name, asked once client-side and stamped locally; no accounts, no auth change).
# v7 adds the codebook consolidation pass (P6): `code_family` (one row per family, with the
# deterministic ring-position hue) and `code.family_id` (nullable — set only for codes a
# consolidation run placed).
# v8 adds hierarchy discipline (P7): `code_family.rationale` (nullable — the one-sentence
# "why these codes belong together" the consolidation pass now returns per family).
# v9 adds researcher-driven code collapse (P8a): revision.action gains 'merge' (folded in
# store.revisions_map — no schema change needed for that, `new_label` already holds arbitrary
# text and is reused to hold the survivor code id for a merge action) and a new `merge_proposal`
# table holding the compress pass's pending review queue (one row per proposed merge group).
# v10 adds theme authority (P8b): a `theme_revision` table — the same audit-trail pattern as
# `revision`/`revisions_map`, but for theme_v2 rows (relabel/reclaim/merge/demote/restore).
# Themes don't get a rename/reject/merge column added to theme_v2 itself; like codes, the
# override is folded in at read time (see store.theme_revisions_map / themes_payload).
# v11 adds per-run model provenance (P10.1c): `run.model` (nullable — the resolved model string
# new_run() auto-captured from llm.model() when the run started), so a project coded partly under
# one researcher-selected model and partly under another stays honest in the export manifest.
# v12 adds the P10.2 loop machinery (design/P10.2-CONTRACT.md §2): `focus_version` (the research
# question as a versioned, researcher/assistant-authored object — the registry's
# `project.research_question` column stays a cached mirror of whichever row is 'active', so
# existing readers of that column keep working untouched); `finding_state` (per-finding computed
# `standing`/researcher `stance`/opened-evidence gate log — keyed by theme_id ALONE, not
# (mode, theme_id), on the working assumption that one project only ever runs SYNTHESIZE under
# one mode's id-space at a time — see synthesize.py's module docstring); `step` (walkthrough
# steps AND checkbacks AND residue notes — all three are just `step.kind` values, so no separate
# residue table exists, matching the contract's 5-table list); `story_version` (the project
# narrative, versioned per document position); `intro` (one per-document introduction, keyed by
# doc_id). Findings themselves are NOT a new table — SYNTHESIZE writes them into the EXISTING
# `theme_v2`/`theme_step` tables (same id discipline, same theme_revision authority machinery —
# see synthesize.py), so a "finding" and a P8b "theme" are the same row from here on.

SCHEMA_VERSION = 12


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
            action TEXT,                        -- 'rename' | 'reject' | 'restore' | 'merge'
            new_label TEXT,                     -- 'merge': reused to hold the SURVIVOR code id
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
        CREATE TABLE IF NOT EXISTS code_family (
            id TEXT, label TEXT, definition TEXT, hue INTEGER, position INTEGER, created_at TEXT,
            rationale TEXT,
            PRIMARY KEY (id)
        );
        CREATE TABLE IF NOT EXISTS merge_proposal (
            id TEXT PRIMARY KEY,
            family_id TEXT,                      -- NULL for the no-family batch
            survivor_id TEXT,
            absorbed_ids TEXT,                   -- JSON list of code ids
            merged_label TEXT,                   -- optional better label for the survivor
            rationale TEXT,
            status TEXT DEFAULT 'pending',       -- 'pending' | 'accepted' | 'dismissed'
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS theme_revision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT,
            theme_id TEXT,
            action TEXT,                         -- 'relabel' | 'reclaim' | 'merge' | 'demote' | 'restore'
            value TEXT,                          -- 'relabel': new label; 'reclaim': new claim;
                                                  -- 'merge': target theme id
            context TEXT,                        -- JSON snapshot of the theme at revision time
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS focus_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            n INTEGER,
            text TEXT,
            author TEXT,                         -- 'researcher' | 'assistant'
            status TEXT,                         -- 'active' | 'superseded' | 'proposed' | 'declined'
            rationale TEXT,
            created_at TEXT
        );
        -- ponytail: theme_id ALONE is the PK here (per contract §2), not (mode, theme_id) —
        -- exactly one mode's SYNTHESIZE finding-state can live safely per project. Two modes
        -- both minting fresh ids from "T01" (each mode has its own sequence — see
        -- store._next_finding_id) WOULD collide here. Upgrade path if a project ever needs
        -- SYNTHESIZE findings in both modes at once: make this PK (mode, theme_id).
        CREATE TABLE IF NOT EXISTS finding_state (
            theme_id TEXT PRIMARY KEY,
            mode TEXT,
            standing TEXT,
            standing_note TEXT,
            stance TEXT,
            opened_evidence TEXT,                -- JSON list of sid — a gate-visit log, append-only
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS step (
            id TEXT PRIMARY KEY,
            mode TEXT,
            doc_id TEXT,
            position INTEGER,
            kind TEXT,                           -- pattern|tension|uncertainty|delta|declined|checkback|residue
            payload TEXT,                        -- JSON — shape depends on kind, see synthesize.py
            reaction TEXT,                       -- 'agree' | 'challenge' | 'reframe' | 'park' | NULL
            reaction_note TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS story_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            n INTEGER,
            text TEXT,                           -- JSON [{para, sids[]}]
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS intro (
            doc_id TEXT PRIMARY KEY,
            text TEXT,                           -- JSON [{para, sids[]}]
            created_at TEXT
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
    if "title" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN title TEXT")
    if "summary" not in cols:
        conn.execute("ALTER TABLE document ADD COLUMN summary TEXT")
    code_cols = {r[1] for r in conn.execute("PRAGMA table_info(code)")}
    if "coder" not in code_cols:
        conn.execute("ALTER TABLE code ADD COLUMN coder TEXT NOT NULL DEFAULT 'standard'")
    comment_cols = {r[1] for r in conn.execute("PRAGMA table_info(comment)")}
    if "author" not in comment_cols:
        conn.execute("ALTER TABLE comment ADD COLUMN author TEXT")
    memo_cols = {r[1] for r in conn.execute("PRAGMA table_info(memo)")}
    if "author" not in memo_cols:
        conn.execute("ALTER TABLE memo ADD COLUMN author TEXT")
    if "family_id" not in code_cols:
        conn.execute("ALTER TABLE code ADD COLUMN family_id TEXT")
    family_cols = {r[1] for r in conn.execute("PRAGMA table_info(code_family)")}
    if "rationale" not in family_cols:
        conn.execute("ALTER TABLE code_family ADD COLUMN rationale TEXT")
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(run)")}
    if "model" not in run_cols:
        conn.execute("ALTER TABLE run ADD COLUMN model TEXT")


def project_db(path) -> sqlite3.Connection:
    """Open (and v2-init) a project database at `path`."""
    conn = sqlite3.connect(str(path))
    init_project_db(conn)
    return conn
