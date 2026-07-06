"""Project-DB read/write helpers shared by jobs (write) and the API (read). Bridges the engine's
in-memory artifacts and the per-project schema-v2/v3 database + JSON checkpoint.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .db import resolve
from .friction import friction as _friction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- documents ----------------------------------------------------------------------------------

def doc_entry(conn: sqlite3.Connection, doc_id: str, filename: str) -> dict:
    """Checkpoint/reading shape for one doc, resolved from the DB index (sections + sentences+text)."""
    secs = conn.execute(
        "SELECT id, gist, start_line, end_line FROM section WHERE doc_id=? ORDER BY char_start",
        (doc_id,)).fetchall()
    sents = [{"id": sid, "section_id": sec, "text": resolve(conn, doc_id, sid)}
             for sid, sec in conn.execute(
                 "SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                 (doc_id,)).fetchall()]
    return {"name": filename,
            "sections": [{"id": r[0], "gist": r[1], "start_line": r[2], "end_line": r[3]}
                         for r in secs],
            "sentences": sents}


def document_list(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, filename, status, created_at, kind FROM document "
        "ORDER BY created_at, id").fetchall()
    out = []
    for doc_id, filename, status, created, kind in rows:
        ns = conn.execute("SELECT COUNT(*) FROM section WHERE doc_id=?", (doc_id,)).fetchone()[0]
        nt = conn.execute("SELECT COUNT(*) FROM sentence WHERE doc_id=?", (doc_id,)).fetchone()[0]
        out.append({"doc_id": doc_id, "filename": filename, "status": status,
                    "created_at": created, "kind": kind or "transcript",
                    "n_sections": ns, "n_sentences": nt})
    return out


def reading_payload(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute("SELECT id, filename FROM document WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return None
    secs = conn.execute(
        "SELECT id, gist FROM section WHERE doc_id=? ORDER BY char_start", (doc_id,)).fetchall()
    by_sec: dict[str, list] = {}
    for sid, sec, cs, ce in conn.execute(
            "SELECT id, section_id, char_start, char_end FROM sentence WHERE doc_id=? "
            "ORDER BY char_start", (doc_id,)):
        by_sec.setdefault(sec, []).append(
            {"id": sid, "text": resolve(conn, doc_id, sid), "char_start": cs, "char_end": ce})
    return {"id": row[0], "filename": row[1],
            "sections": [{"id": s[0], "gist": s[1], "sentences": by_sec.get(s[0], [])}
                         for s in secs]}


# ---- codes --------------------------------------------------------------------------------------

def _code_row(r) -> dict:
    return {"id": r[0], "coder": r[1], "label": r[2], "definition": r[3], "code_type": r[4],
            "evidence": json.loads(r[5]), "model_rationale": r[6], "origin_doc_id": r[7]}


def persist_panel_codes(conn: sqlite3.Connection, run_id: str, order: list[str],
                        docs: dict, coders: dict) -> None:
    """Write panel codes to the `code` table with the `coder` column and canonical Cxxxx ids —
    assigned in the SAME order theorize_panel_sequential enumerates (doc order × lens order × code),
    so theme supporting_code_ids match /codes ids."""
    conn.execute("DELETE FROM code")
    rows, i = [], 0
    for doc_id in order:
        panel = docs[doc_id].get("panel", {})
        for lens in coders:  # canonical lens order == panel dict insertion order
            for c in panel.get(lens, []):
                i += 1
                rows.append((f"C{i:04d}", doc_id, run_id, c["label"], c["definition"],
                             c["code_type"], json.dumps(c["evidence"]),
                             c.get("model_rationale", ""), lens))
    conn.executemany(
        "INSERT INTO code (id, origin_doc_id, run_id, label, definition, code_type, "
        "evidence_ids, model_rationale, coder) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def codes_payload(conn: sqlite3.Connection, coder: str | None = None,
                  doc_id: str | None = None) -> list[dict]:
    q = ("SELECT id, coder, label, definition, code_type, evidence_ids, model_rationale, "
         "origin_doc_id FROM code")
    where, args = [], []
    if coder:
        where.append("coder = ?"); args.append(coder)
    if doc_id:
        where.append("origin_doc_id = ?"); args.append(doc_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id"
    revs = revisions_map(conn)
    out = []
    for r in conn.execute(q, args):
        c = _code_row(r)
        rev = revs.get(c["id"], {})
        c["status"] = "rejected" if rev.get("rejected") else "active"
        c["researcher_label"] = rev.get("new_label")
        out.append(c)
    return out


def panel_by_doc_from_db(conn: sqlite3.Connection, doc_id: str) -> dict:
    """Rebuild {lens: [codes]} for one doc from the DB (for friction)."""
    panel: dict[str, list] = {}
    for r in conn.execute(
            "SELECT id, coder, label, definition, code_type, evidence_ids, model_rationale, "
            "origin_doc_id FROM code WHERE origin_doc_id=? ORDER BY id", (doc_id,)):
        panel.setdefault(r[1], []).append(_code_row(r))
    return panel


def friction_payload(conn: sqlite3.Connection, doc_id: str) -> dict:
    """Live friction for one doc as a flat list (interpretive first), each entry carrying the
    divergent sentence's verbatim text and per-lens readings — the exact shape the comparison view
    consumes. interpretive = 2+ lenses on the sentence; attentional = a subset of lenses coded it."""
    panel = panel_by_doc_from_db(conn, doc_id)
    fr = _friction(panel)
    n = len(fr["coders"])
    items = []
    for ev, cm in fr["by_sent"].items():
        if len(cm) < 2 and len(cm) >= n:
            continue  # coded by everyone and only... (n==1 edge) — nothing divergent
        kind = "interpretive" if len(cm) >= 2 else "attentional"
        if kind == "attentional" and len(cm) >= n:
            continue
        d, sid = ev.split("#", 1)
        try:
            text = resolve(conn, d, sid)
        except Exception:
            text = ev
        readings = {co: [{"label": c["label"], "type": c["code_type"]} for c in cs]
                    for co, cs in cm.items()}
        items.append({"sid": sid, "text": text, "kind": kind, "readings": readings,
                      "n_coders": len(cm)})
    items.sort(key=lambda x: (x["kind"] != "interpretive", -x["n_coders"]))
    return {"coverage": fr["coverage"], "coders": fr["coders"], "friction": items}


# ---- themes -------------------------------------------------------------------------------------

def persist_themes(conn: sqlite3.Connection, mode: str, themes: list[dict],
                   snaps: list[tuple]) -> None:
    conn.execute("DELETE FROM theme_v2 WHERE mode=?", (mode,))
    conn.execute("DELETE FROM theme_step WHERE mode=?", (mode,))
    for t in themes:
        payload = {k: v for k, v in t.items()
                   if k not in ("id", "central_concept", "coverage", "claim_scope", "falsified_if")}
        conn.execute(
            "INSERT INTO theme_v2 (id, run_id, mode, central_concept, coverage, claim_scope, "
            "falsified_if, payload) VALUES (?,?,?,?,?,?,?,?)",
            (t["id"], "", mode, t["central_concept"], t.get("coverage", ""),
             t.get("claim_scope", ""), t.get("falsified_if", ""), json.dumps(payload)))
    for pos, (doc_id, snap) in enumerate(snaps):
        conn.execute(
            "INSERT OR REPLACE INTO theme_step (mode, doc_id, position, raw, snapshot) "
            "VALUES (?,?,?,?,?)", (mode, doc_id, pos, "", json.dumps(snap)))
    conn.commit()


def themes_payload(conn: sqlite3.Connection, mode: str) -> dict:
    themes = []
    for r in conn.execute(
            "SELECT id, central_concept, coverage, claim_scope, falsified_if, payload "
            "FROM theme_v2 WHERE mode=? ORDER BY id", (mode,)):
        t = {"id": r[0], "central_concept": r[1], "coverage": r[2], "claim_scope": r[3],
             "falsified_if": r[4]}
        t.update(json.loads(r[5]))
        themes.append(t)
    snaps = [{"doc_id": r[0], "themes": json.loads(r[1])} for r in conn.execute(
        "SELECT doc_id, snapshot FROM theme_step WHERE mode=? ORDER BY position", (mode,))]
    return {"mode": mode, "themes": themes, "snapshots": snaps,
            "stale": themes_stale(conn, mode)}


def code_counts(conn: sqlite3.Connection) -> dict:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT coder, COUNT(*) FROM code GROUP BY coder")}


# ---- researcher feedback (schema v3) --------------------------------------------------------
# Comments and revisions are the researcher's voice in the loop. They are stored with a JSON
# `context` snapshot (label / quote / lens at write time) so their meaning survives the id churn
# a recode causes, and they compile into a plain-text guidance block the model reads on re-runs.

def add_comment(conn: sqlite3.Connection, target_type: str, target_id: str,
                doc_id: str | None, body: str, context: dict | None = None) -> dict:
    cid = "N" + uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO comment (id, target_type, target_id, doc_id, body, context, status, "
        "created_at) VALUES (?,?,?,?,?,?, 'open', ?)",
        (cid, target_type, target_id, doc_id, body, json.dumps(context or {}), _now()))
    conn.commit()
    return {"id": cid, "target_type": target_type, "target_id": target_id, "doc_id": doc_id,
            "body": body, "context": context or {}, "status": "open"}


def list_comments(conn: sqlite3.Connection, doc_id: str | None = None,
                  target_type: str | None = None, status: str | None = None) -> list[dict]:
    q = ("SELECT id, target_type, target_id, doc_id, body, context, status, created_at "
         "FROM comment")
    where, args = [], []
    if doc_id:
        where.append("doc_id = ?"); args.append(doc_id)
    if target_type:
        where.append("target_type = ?"); args.append(target_type)
    if status:
        where.append("status = ?"); args.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at"
    return [{"id": r[0], "target_type": r[1], "target_id": r[2], "doc_id": r[3], "body": r[4],
             "context": json.loads(r[5] or "{}"), "status": r[6], "created_at": r[7]}
            for r in conn.execute(q, args)]


def set_comment_status(conn: sqlite3.Connection, cid: str, status: str) -> bool:
    cur = conn.execute("UPDATE comment SET status=? WHERE id=?", (status, cid))
    conn.commit()
    return cur.rowcount > 0


def update_comment(conn: sqlite3.Connection, cid: str, body: str | None = None,
                   status: str | None = None) -> bool:
    """Edit a comment's text and/or status. Editing the text re-opens it — changed words
    haven't been seen by the model yet."""
    sets, args = [], []
    if body is not None:
        sets.append("body=?"); args.append(body)
        sets.append("status='open'")
    if status is not None and body is None:
        sets.append("status=?"); args.append(status)
    if not sets:
        return False
    args.append(cid)
    cur = conn.execute(f"UPDATE comment SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()
    return cur.rowcount > 0


def delete_comment(conn: sqlite3.Connection, cid: str) -> bool:
    cur = conn.execute("DELETE FROM comment WHERE id=?", (cid,))
    conn.commit()
    return cur.rowcount > 0


# ---- memos (researcher's analytic writing — persisted, NEVER sent to the model) --------------

def set_memo(conn: sqlite3.Connection, target_type: str, target_id: str, body: str,
             context: dict | None = None) -> dict:
    """Upsert the memo for one target; an empty body deletes it (a memo is a living document,
    not a thread)."""
    if not body.strip():
        conn.execute("DELETE FROM memo WHERE target_type=? AND target_id=?",
                     (target_type, target_id))
        conn.commit()
        return {"target_type": target_type, "target_id": target_id, "body": ""}
    conn.execute(
        "INSERT INTO memo (target_type, target_id, body, context, updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(target_type, target_id) "
        "DO UPDATE SET body=excluded.body, context=excluded.context, "
        "updated_at=excluded.updated_at",
        (target_type, target_id, body, json.dumps(context or {}), _now()))
    conn.commit()
    return {"target_type": target_type, "target_id": target_id, "body": body}


def list_memos(conn: sqlite3.Connection, target_type: str | None = None) -> list[dict]:
    q = "SELECT target_type, target_id, body, context, updated_at FROM memo"
    args: list = []
    if target_type:
        q += " WHERE target_type=?"; args.append(target_type)
    return [{"target_type": r[0], "target_id": r[1], "body": r[2],
             "context": json.loads(r[3] or "{}"), "updated_at": r[4]}
            for r in conn.execute(q, args)]


def add_revision(conn: sqlite3.Connection, code_id: str, action: str,
                 new_label: str | None = None) -> dict:
    row = conn.execute("SELECT label, definition, code_type, coder FROM code WHERE id=?",
                       (code_id,)).fetchone()
    ctx = ({"label": row[0], "definition": row[1], "code_type": row[2], "coder": row[3]}
           if row else {})
    conn.execute(
        "INSERT INTO revision (code_id, action, new_label, context, created_at) "
        "VALUES (?,?,?,?,?)", (code_id, action, new_label, json.dumps(ctx), _now()))
    conn.commit()
    return {"code_id": code_id, "action": action, "new_label": new_label, "context": ctx}


def revisions_map(conn: sqlite3.Connection) -> dict:
    """Fold the revision log into the current per-code state: latest rename wins; rejected is
    true unless a later 'restore' lifts it."""
    out: dict[str, dict] = {}
    for code_id, action, new_label in conn.execute(
            "SELECT code_id, action, new_label FROM revision ORDER BY id"):
        st = out.setdefault(code_id, {"rejected": False, "new_label": None})
        if action == "rename":
            st["new_label"] = new_label
        elif action == "reject":
            st["rejected"] = True
        elif action == "restore":
            st["rejected"] = False
    return out


def open_comment_counts(conn: sqlite3.Connection) -> dict:
    """{doc_id: n} for open doc-scoped comments, plus '_project' for project-level ones."""
    out: dict[str, int] = {}
    for doc_id, n in conn.execute(
            "SELECT COALESCE(doc_id, '_project'), COUNT(*) FROM comment "
            "WHERE status='open' GROUP BY COALESCE(doc_id, '_project')"):
        out[doc_id] = n
    return out


def compile_guidance(conn: sqlite3.Connection, doc_id: str | None = None) -> str:
    """Compile the researcher's open feedback into the plain-text block a re-run's prompts carry.
    doc_id given → coding guidance for that document (sentence/code/document comments + revisions
    on that doc's codes). doc_id None → project-level theme guidance (theme comments + a summary
    of all code revisions, since themes read the whole codebook)."""
    lines: list[str] = []
    if doc_id:
        wanted = ("sentence", "code", "document")
        comments = [c for c in list_comments(conn, status="open")
                    if c["doc_id"] == doc_id and c["target_type"] in wanted]
    else:
        comments = [c for c in list_comments(conn, target_type="theme", status="open")]
    for c in comments:
        ctx = c["context"]
        if c["target_type"] == "sentence":
            quote = ctx.get("quote", "")
            where = f'sentence {c["target_id"]}' + (f' ("{quote}")' if quote else "")
        elif c["target_type"] == "code":
            where = f'the code "{ctx.get("label", c["target_id"])}"'
        elif c["target_type"] == "theme":
            where = f'the theme "{ctx.get("claim", c["target_id"])}"'
        else:
            where = "this document"
        lines.append(f"- On {where}: {c['body']}")
    revs = revisions_map(conn)
    if revs:
        labels = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT id, label, origin_doc_id FROM code")}
        ctxs = {r[0]: json.loads(r[1] or "{}") for r in conn.execute(
            "SELECT code_id, context FROM revision ORDER BY id")}
        for code_id, st in revs.items():
            label, origin = labels.get(code_id, (ctxs.get(code_id, {}).get("label", code_id), None))
            if doc_id and origin is not None and origin != doc_id:
                continue
            if st["rejected"]:
                lines.append(f'- The researcher REJECTED the code "{label}" — do not '
                             f"re-propose this interpretation.")
            elif st["new_label"]:
                lines.append(f'- The researcher renamed "{label}" to "{st["new_label"]}" — '
                             f"use the new name and its implied focus.")
    return "\n".join(lines)


def mark_feedback_addressed(conn: sqlite3.Connection, doc_id: str | None = None,
                            target_type: str | None = None) -> int:
    """Flip open comments to 'addressed' after a re-run consumed them (scoped like compile)."""
    if doc_id:
        cur = conn.execute(
            "UPDATE comment SET status='addressed' WHERE status='open' AND doc_id=? "
            "AND target_type IN ('sentence','code','document')", (doc_id,))
    elif target_type:
        cur = conn.execute(
            "UPDATE comment SET status='addressed' WHERE status='open' AND target_type=?",
            (target_type,))
    else:
        cur = conn.execute("UPDATE comment SET status='addressed' WHERE status='open'")
    conn.commit()
    return cur.rowcount


def set_themes_stale(conn: sqlite3.Connection, mode: str, stale: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (f"themes_stale:{mode}", 1 if stale else 0))
    conn.commit()


def themes_stale(conn: sqlite3.Connection, mode: str) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key=?",
                       (f"themes_stale:{mode}",)).fetchone()
    return bool(row and row[0])
