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
        "SELECT id, filename, status, created_at, kind, title, summary FROM document "
        "ORDER BY created_at, id").fetchall()
    out = []
    for doc_id, filename, status, created, kind, title, summary in rows:
        ns = conn.execute("SELECT COUNT(*) FROM section WHERE doc_id=?", (doc_id,)).fetchone()[0]
        nt = conn.execute("SELECT COUNT(*) FROM sentence WHERE doc_id=?", (doc_id,)).fetchone()[0]
        out.append({"doc_id": doc_id, "filename": filename, "status": status,
                    "created_at": created, "kind": kind or "transcript",
                    "title": title, "summary": summary,
                    "n_sections": ns, "n_sentences": nt})
    return out


def reading_payload(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, filename, title, summary FROM document WHERE id=?", (doc_id,)).fetchone()
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
    return {"id": row[0], "filename": row[1], "title": row[2], "summary": row[3],
            "sections": [{"id": s[0], "gist": s[1], "sentences": by_sec.get(s[0], [])}
                         for s in secs]}


def rename_document(conn: sqlite3.Connection, doc_id: str, title: str) -> bool:
    """Human override of the LLM title — same philosophy as researcher_label on codes: the
    override always wins in docTitle()/document_list(), but nothing else about the document
    changes (sections/sentences/codes are untouched)."""
    cur = conn.execute("UPDATE document SET title=? WHERE id=?", (title, doc_id))
    conn.commit()
    return cur.rowcount > 0


def delete_document_rows(conn: sqlite3.Connection, doc_id: str) -> dict:
    """DB-side half of document deletion (P2.6/F3): drop the doc's own rows (document/section/
    sentence), drop codes that ORIGINATED on this doc, and for every remaining code strip any
    evidence entries that reference this doc — deleting the code outright if that empties its
    evidence (a code with zero grounded evidence is not a code). Also removes comments/memos
    that target this document. Checkpoint files and theme_step invalidation are NOT handled
    here (they live outside the project DB) — callers must also pop the doc from both mode
    checkpoints and clear theme_step, mirroring jobs.recode_work's invalidation dance.
    Returns counts for the caller/API response."""
    codes_deleted = 0
    codes_stripped = 0
    rows = conn.execute("SELECT id, evidence_ids, origin_doc_id FROM code").fetchall()
    for code_id, ev_json, origin_doc_id in rows:
        if origin_doc_id == doc_id:
            conn.execute("DELETE FROM code WHERE id=?", (code_id,))
            codes_deleted += 1
            continue
        evidence = json.loads(ev_json or "[]")
        kept = [e for e in evidence if not e.startswith(f"{doc_id}#")]
        if len(kept) != len(evidence):
            if kept:
                conn.execute("UPDATE code SET evidence_ids=? WHERE id=?",
                             (json.dumps(kept), code_id))
                codes_stripped += 1
            else:
                conn.execute("DELETE FROM code WHERE id=?", (code_id,))
                codes_deleted += 1
    conn.execute(
        "DELETE FROM comment WHERE doc_id=? OR (target_type='document' AND target_id=?)",
        (doc_id, doc_id))
    conn.execute("DELETE FROM memo WHERE target_type='document' AND target_id=?", (doc_id,))
    conn.execute("DELETE FROM sentence WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM section WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM document WHERE id=?", (doc_id,))
    conn.commit()
    return {"codes_deleted": codes_deleted, "codes_stripped": codes_stripped}


# ---- codes --------------------------------------------------------------------------------------

def _code_row(r) -> dict:
    return {"id": r[0], "coder": r[1], "label": r[2], "definition": r[3], "code_type": r[4],
            "evidence": json.loads(r[5]), "model_rationale": r[6], "origin_doc_id": r[7],
            "family_id": r[8] if len(r) > 8 else None}


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
         "origin_doc_id, family_id FROM code")
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
            "origin_doc_id, family_id FROM code WHERE origin_doc_id=? ORDER BY id", (doc_id,)):
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


# ---- code families (P6: codebook consolidation) --------------------------------------------------
# One consolidation pass groups the whole codebook into 8–15 families (consolidate.py proposes,
# validates); this is the persistence half. persist_panel_codes/`code` rewrites lose family_id —
# jobs.code_work/recode_work flag `families_stale` whenever that happens and any families exist.

def persist_families(conn: sqlite3.Connection, families: list[dict]) -> None:
    """Replace the family table wholesale and re-tag member codes' family_id. `families` is
    consolidate.consolidate_codebook's output: each already carries position/hue and validated
    member_code_ids."""
    conn.execute("DELETE FROM code_family")
    conn.execute("UPDATE code SET family_id=NULL")
    now = _now()
    for fam in families:
        fid = f"F{fam['position'] + 1:02d}"
        conn.execute(
            "INSERT INTO code_family (id, label, definition, hue, position, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (fid, fam["label"], fam["definition"], fam["hue"], fam["position"], now))
        for cid in fam["member_code_ids"]:
            conn.execute("UPDATE code SET family_id=? WHERE id=?", (fid, cid))
    conn.commit()


def families_payload(conn: sqlite3.Connection) -> list[dict]:
    """Families ordered by ring position, each with n_codes = count of non-rejected members
    and n_sources = count of distinct origin docs among those active members (derived, no
    schema change — >1 signals a family that was aggregated across sources)."""
    revs = revisions_map(conn)
    out = []
    for r in conn.execute(
            "SELECT id, label, definition, hue, position FROM code_family ORDER BY position"):
        fid = r[0]
        rows = conn.execute(
            "SELECT id, origin_doc_id FROM code WHERE family_id=?", (fid,)).fetchall()
        active = [(cid, doc_id) for cid, doc_id in rows if not revs.get(cid, {}).get("rejected")]
        n_codes = len(active)
        n_sources = len({doc_id for _, doc_id in active})
        out.append({"id": fid, "label": r[1], "definition": r[2], "hue": r[3], "position": r[4],
                    "n_codes": n_codes, "n_sources": n_sources})
    return out


def set_families_stale(conn: sqlite3.Connection, flag: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('families_stale', ?)",
                 (1 if flag else 0,))
    conn.commit()


def families_stale(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key='families_stale'").fetchone()
    return bool(row and row[0])


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


def doc_code_labels(conn: sqlite3.Connection, doc_id: str) -> list[tuple[str, str]]:
    """[(coder, label)] for every code currently originating on `doc_id` — a recode's before/after
    snapshot (P4.11). Label-based (not id-based): a recode assigns fresh Cxxxx ids, so ids churn
    even for a code whose meaning didn't change; (coder, label) pairs are the stable comparison."""
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT coder, label FROM code WHERE origin_doc_id=? ORDER BY id", (doc_id,))]


def diff_code_labels(before: list[tuple[str, str]], after: list[tuple[str, str]],
                     cap: int = 20) -> dict:
    """Set-diff two (coder,label) snapshots from doc_code_labels — the feedback loop's visible
    payoff after a re-code. Lists are capped at `cap` entries with a `more_n` overflow count each,
    so a big diff doesn't bloat the job row; `kept_n` is the size of the intersection."""
    before_set, after_set = set(before), set(after)
    new = sorted(after_set - before_set)
    dropped = sorted(before_set - after_set)
    kept_n = len(before_set & after_set)
    return {
        "new": [{"coder": c, "label": l} for c, l in new[:cap]],
        "new_more_n": max(0, len(new) - cap),
        "dropped": [{"coder": c, "label": l} for c, l in dropped[:cap]],
        "dropped_more_n": max(0, len(dropped) - cap),
        "kept_n": kept_n,
    }


# ---- researcher feedback (schema v3) --------------------------------------------------------
# Comments and revisions are the researcher's voice in the loop. They are stored with a JSON
# `context` snapshot (label / quote / lens at write time) so their meaning survives the id churn
# a recode causes, and they compile into a plain-text guidance block the model reads on re-runs.

def add_comment(conn: sqlite3.Connection, target_type: str, target_id: str,
                doc_id: str | None, body: str, context: dict | None = None,
                author: str | None = None) -> dict:
    cid = "N" + uuid.uuid4().hex[:8]
    created = _now()
    conn.execute(
        "INSERT INTO comment (id, target_type, target_id, doc_id, body, context, status, "
        "created_at, author) VALUES (?,?,?,?,?,?, 'open', ?, ?)",
        (cid, target_type, target_id, doc_id, body, json.dumps(context or {}), created, author))
    conn.commit()
    return {"id": cid, "target_type": target_type, "target_id": target_id, "doc_id": doc_id,
            "body": body, "context": context or {}, "status": "open", "created_at": created,
            "author": author}


def list_comments(conn: sqlite3.Connection, doc_id: str | None = None,
                  target_type: str | None = None, status: str | None = None) -> list[dict]:
    q = ("SELECT id, target_type, target_id, doc_id, body, context, status, created_at, author "
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
             "context": json.loads(r[5] or "{}"), "status": r[6], "created_at": r[7],
             "author": r[8]}
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
             context: dict | None = None, author: str | None = None) -> dict:
    """Upsert the memo for one target; an empty body deletes it (a memo is a living document,
    not a thread)."""
    if not body.strip():
        conn.execute("DELETE FROM memo WHERE target_type=? AND target_id=?",
                     (target_type, target_id))
        conn.commit()
        return {"target_type": target_type, "target_id": target_id, "body": ""}
    updated = _now()
    conn.execute(
        "INSERT INTO memo (target_type, target_id, body, context, updated_at, author) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(target_type, target_id) "
        "DO UPDATE SET body=excluded.body, context=excluded.context, "
        "updated_at=excluded.updated_at, author=excluded.author",
        (target_type, target_id, body, json.dumps(context or {}), updated, author))
    conn.commit()
    return {"target_type": target_type, "target_id": target_id, "body": body,
            "updated_at": updated, "author": author}


def list_memos(conn: sqlite3.Connection, target_type: str | None = None) -> list[dict]:
    q = "SELECT target_type, target_id, body, context, updated_at, author FROM memo"
    args: list = []
    if target_type:
        q += " WHERE target_type=?"; args.append(target_type)
    return [{"target_type": r[0], "target_id": r[1], "body": r[2],
             "context": json.loads(r[3] or "{}"), "updated_at": r[4], "author": r[5]}
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


def compile_family_guidance(conn: sqlite3.Connection) -> str:
    """Open comments on families (target_type='family') as plain-text guidance for a
    re-consolidation — same shape as compile_guidance's lines, kept separate because family
    comments are keyed by family label, not a code/theme/sentence context."""
    lines: list[str] = []
    for c in list_comments(conn, target_type="family", status="open"):
        ctx = c["context"]
        where = f'the family "{ctx.get("label", c["target_id"])}"'
        lines.append(f"- On {where}: {c['body']}")
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


# ---- export (v5) ------------------------------------------------------------------------------
# Self-contained exports: codes with researcher revisions applied and verbatim quotes resolved
# from the sentence index, themes with their full payload, plus memos and comments — everything
# a coauthor needs outside the app. JSON = archival/complete; CSV = flat, spreadsheet-ready.

def _safe_quote(conn: sqlite3.Connection, qualified: str) -> str:
    try:
        from .db import resolve_ev
        return " ".join(resolve_ev(conn, qualified).split())
    except Exception:
        return ""


def export_payload(conn: sqlite3.Connection, mode: str) -> dict:
    codes = codes_payload(conn)
    for c in codes:
        c["evidence"] = [{"id": e, "quote": _safe_quote(conn, e)} for e in c["evidence"]]
    th = themes_payload(conn, mode)
    return {
        "exported_at": _now(),
        "mode": mode,
        "documents": document_list(conn),
        "codes": codes,
        "themes": th["themes"],
        "themes_stale": th["stale"],
        "families": families_payload(conn),
        "memos": list_memos(conn),
        "comments": list_comments(conn),
    }


def _memo_map(conn: sqlite3.Connection, target_type: str) -> dict[str, str]:
    return {m["target_id"]: m["body"] for m in list_memos(conn, target_type=target_type)}


def codes_csv(conn: sqlite3.Connection) -> str:
    import csv
    import io
    memos = _memo_map(conn, "code")
    fam_labels = {f["id"]: f["label"] for f in families_payload(conn)}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "lens", "type", "status", "label", "machine_label", "definition",
                "origin_doc", "family", "n_evidence", "evidence_ids", "exemplar_quote",
                "model_rationale", "researcher_memo"])
    for c in codes_payload(conn):
        w.writerow([
            c["id"], c["coder"], c["code_type"], c["status"],
            c["researcher_label"] or c["label"], c["label"], c["definition"],
            c["origin_doc_id"], fam_labels.get(c.get("family_id"), ""),
            len(c["evidence"]), " ".join(c["evidence"]),
            _safe_quote(conn, c["evidence"][0]) if c["evidence"] else "",
            c["model_rationale"], memos.get(c["id"], ""),
        ])
    return out.getvalue()


def themes_csv(conn: sqlite3.Connection, mode: str) -> str:
    import csv
    import io
    memos = _memo_map(conn, "theme")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "central_concept", "coverage", "claim_scope", "provenance",
                "n_supporting", "supporting_code_ids", "tensions", "subthemes",
                "key_evidence", "falsified_if", "researcher_memo"])
    for t in themes_payload(conn, mode)["themes"]:
        prov = t.get("paradigm_provenance") or {}
        w.writerow([
            t["id"], t["central_concept"], t.get("coverage", ""), t.get("claim_scope", ""),
            "|".join(f"{k}:{v}" for k, v in prov.items()),
            len(t.get("supporting_code_ids", [])), " ".join(t.get("supporting_code_ids", [])),
            " ".join(t.get("tensions", [])),
            " | ".join(st.get("claim", "") for st in t.get("subthemes", [])),
            " ".join(t.get("key_evidence_sentence_ids", [])),
            t.get("falsified_if", ""), memos.get(t["id"], ""),
        ])
    return out.getvalue()


def _collapse(t: str) -> str:
    return " ".join((t or "").split())


def report_md(conn: sqlite3.Connection, proj: dict, mode: str) -> str:
    """Narrative Markdown report (P3.10/F8): title block, themes with anchor quotes resolved
    verbatim, a codebook appendix grouped by lens, and an open-notes appendix. Built fresh from
    the DB (not render_md.py's checkpoint-shaped renderers — those read in-memory run state;
    this reads the persisted project the way the other export endpoints do)."""
    docs = document_list(conn)
    codes = codes_payload(conn)
    by_id = {c["id"]: c for c in codes}
    th = themes_payload(conn, mode)
    memos_code = _memo_map(conn, "code")
    memos_theme = _memo_map(conn, "theme")
    generated = _now()[:10]

    out: list[str] = []
    out.append(f"# {proj['name']}")
    out.append("")
    out.append(f"*Generated {generated} · {proj.get('pack_id') or 'standard coding'} · "
               f"{len(docs)} source(s) · {len(codes)} codes · {len(th['themes'])} themes*")
    out.append("")
    out.append("## Sources")
    out.append("")
    for d in docs:
        title = (d.get("title") or "").strip() or d["filename"]
        out.append(f"- **{title}** — {d['n_sentences']} sentences, {d['n_sections']} sections")
        if d.get("summary"):
            out.append(f"  {_collapse(d['summary'])}")
    out.append("")

    out.append("## Themes")
    out.append("")
    if not th["themes"]:
        out.append("*No themes built yet.*")
        out.append("")
    for t in th["themes"]:
        out.append(f"### {t['id']} — {t['central_concept']}")
        out.append("")
        out.append(f"*{t.get('claim_scope', '')} · coverage {t.get('coverage', '')}*")
        out.append("")
        prov = t.get("paradigm_provenance") or {}
        if prov:
            out.append("**Provenance:** " + ", ".join(f"{k} {v}" for k, v in
                        sorted(prov.items(), key=lambda kv: -kv[1])))
            out.append("")
        if t.get("subthemes"):
            out.append("**Sub-themes:**")
            for st in t["subthemes"]:
                out.append(f"- {st.get('claim', '')}")
            out.append("")
        anchors = (t.get("key_evidence_sentence_ids") or [])[:5]
        if anchors:
            out.append("**Anchored in:**")
            for q in anchors:
                quote = _safe_quote(conn, q) or q
                out.append(f"- `{q}` — “{quote}”")
            out.append("")
        tensions = [by_id[c].get("researcher_label") or by_id[c]["label"]
                    for c in t.get("tensions", []) if c in by_id]
        if tensions:
            out.append("**Tensions:** " + "; ".join(tensions))
            out.append("")
        if t.get("falsified_if"):
            out.append(f"**Falsified if:** {t['falsified_if']}")
            out.append("")
        memo = memos_theme.get(t["id"])
        if memo:
            out.append(f"**Researcher memo:** {memo}")
            out.append("")

    out.append("## Codebook appendix")
    out.append("")
    active = [c for c in codes if c["status"] != "rejected"]
    rejected = [c for c in codes if c["status"] == "rejected"]
    by_lens: dict[str, list] = {}
    for c in active:
        by_lens.setdefault(c["coder"], []).append(c)
    for lens in by_lens:
        group = by_lens[lens]
        out.append(f"### {lens} · {len(group)} codes")
        out.append("")
        for c in group:
            lbl = c.get("researcher_label") or c["label"]
            out.append(f"- **{lbl}** ({c['code_type']}) — {c['definition']} "
                       f"[{len(c['evidence'])} evidence]")
            if c["evidence"]:
                out.append(f"  - exemplar: “{_safe_quote(conn, c['evidence'][0])}”")
            memo = memos_code.get(c["id"])
            if memo:
                out.append(f"  - researcher memo: {memo}")
        out.append("")
    if rejected:
        out.append("### Rejected codes")
        out.append("")
        for c in rejected:
            lbl = c.get("researcher_label") or c["label"]
            out.append(f"- ~~{lbl}~~ ({c['coder']} · {c['code_type']})")
        out.append("")

    notes = list_comments(conn, status="open")
    out.append("## Open notes appendix")
    out.append("")
    if not notes:
        out.append("*No open notes.*")
    else:
        for n in notes:
            who = f"{n['author']} · " if n.get("author") else ""
            ctx = n.get("context") or {}
            where = ctx.get("quote") or ctx.get("label") or ctx.get("claim") or n["target_id"]
            out.append(f"- ({n['target_type']}) {who}{n['body']} — _{_collapse(str(where))}_")
    out.append("")
    return "\n".join(out)


def set_themes_stale(conn: sqlite3.Connection, mode: str, stale: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (f"themes_stale:{mode}", 1 if stale else 0))
    conn.commit()


def themes_stale(conn: sqlite3.Connection, mode: str) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key=?",
                       (f"themes_stale:{mode}",)).fetchone()
    return bool(row and row[0])
