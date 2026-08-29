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
    """P8a: a merged code gets status 'merged' + merged_into (its researcher_label/rename is
    still whatever it was — a merge does not imply a rename). The SURVIVOR's evidence becomes
    the order-preserving de-duplicated union of its own evidence plus every code merged into it
    (following chains — see revisions_map). This reshapes the researcher-facing codebook only;
    friction/theming read the raw `code` table directly and are untouched (documented at the top
    of the merge sections below)."""
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
    rows = {r[0]: r for r in conn.execute(q, args)}
    out = []
    for cid, r in rows.items():
        c = _code_row(r)
        rev = revs.get(c["id"], {})
        merged_into = rev.get("merged_into")
        if merged_into:
            c["status"] = "merged"
        else:
            c["status"] = "rejected" if rev.get("rejected") else "active"
        c["merged_into"] = merged_into
        # a merge does not itself rename the absorbed code — new_label still reflects any actual
        # rename revision, independent of whether this code also got merged into a survivor.
        c["researcher_label"] = rev.get("new_label")
        out.append(c)
    # survivor evidence union: gather every code whose merge chain resolves to this survivor,
    # in ascending code-id order (deterministic and stable), then append their evidence in that
    # order, de-duplicating while preserving first-seen order.
    absorbed_by_survivor: dict[str, list[str]] = {}
    for cid, rev in revs.items():
        if rev.get("merged_into"):
            absorbed_by_survivor.setdefault(rev["merged_into"], []).append(cid)
    if absorbed_by_survivor:
        # need the evidence of absorbed codes even if they were filtered out of `rows` by
        # coder/doc_id — evidence union should be complete regardless of the query's own filter.
        all_evidence: dict[str, list[str]] = {}
        for cid, r in rows.items():
            all_evidence[cid] = json.loads(r[5])
        missing = [cid for absorbed in absorbed_by_survivor.values() for cid in absorbed
                   if cid not in all_evidence]
        if missing:
            for r in conn.execute(
                    f"SELECT id, evidence_ids FROM code WHERE id IN "
                    f"({','.join('?' * len(missing))})", missing):
                all_evidence[r[0]] = json.loads(r[1])
        for c in out:
            if c["id"] in absorbed_by_survivor:
                seen = list(c["evidence"])
                seen_set = set(seen)
                for absorbed_id in sorted(absorbed_by_survivor[c["id"]]):
                    for ev in all_evidence.get(absorbed_id, []):
                        if ev not in seen_set:
                            seen.append(ev)
                            seen_set.add(ev)
                c["evidence"] = seen
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
    consolidate.consolidate_codebook's output: each already carries position/hue, `rationale`
    (P7: the "why" behind the cluster — defaults to "" when a family predates that field), and
    validated member_code_ids."""
    conn.execute("DELETE FROM code_family")
    conn.execute("UPDATE code SET family_id=NULL")
    now = _now()
    for fam in families:
        fid = f"F{fam['position'] + 1:02d}"
        conn.execute(
            "INSERT INTO code_family (id, label, definition, hue, position, created_at, "
            "rationale) VALUES (?,?,?,?,?,?,?)",
            (fid, fam["label"], fam["definition"], fam["hue"], fam["position"], now,
             fam.get("rationale", "")))
        for cid in fam["member_code_ids"]:
            conn.execute("UPDATE code SET family_id=? WHERE id=?", (fid, cid))
    conn.commit()


def families_payload(conn: sqlite3.Connection) -> list[dict]:
    """Families ordered by ring position, each with n_codes = count of non-rejected,
    non-merged members and n_sources = count of distinct origin docs among those active members
    (derived, no schema change — >1 signals a family that was aggregated across sources).
    `rationale` falls back to "" for families persisted before schema v8. P8a: a merged code is
    no longer part of the family's visible count (like rejected) — it lives on only as evidence
    folded into its survivor."""
    revs = revisions_map(conn)
    out = []
    for r in conn.execute(
            "SELECT id, label, definition, hue, position, rationale FROM code_family "
            "ORDER BY position"):
        fid = r[0]
        rows = conn.execute(
            "SELECT id, origin_doc_id FROM code WHERE family_id=?", (fid,)).fetchall()
        active = [(cid, doc_id) for cid, doc_id in rows
                  if not revs.get(cid, {}).get("rejected") and not revs.get(cid, {}).get("merged_into")]
        n_codes = len(active)
        n_sources = len({doc_id for _, doc_id in active})
        out.append({"id": fid, "label": r[1], "definition": r[2], "hue": r[3], "position": r[4],
                    "rationale": r[5] or "", "n_codes": n_codes, "n_sources": n_sources})
    return out


def set_families_stale(conn: sqlite3.Connection, flag: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('families_stale', ?)",
                 (1 if flag else 0,))
    conn.commit()


def families_stale(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key='families_stale'").fetchone()
    return bool(row and row[0])


# ---- merge proposals (P8a: the compress pass's review queue) -------------------------------------
# The compress job proposes within-family merge groups; nothing is applied automatically — each
# proposal sits PENDING until a researcher accepts or dismisses it (api.py's accept/dismiss
# endpoints). A new compress run replaces the pending set wholesale (accepted/dismissed history
# is kept for audit, never deleted here).

def persist_merge_proposals(conn: sqlite3.Connection, proposals: list[dict]) -> None:
    """Replace all PENDING proposals with a fresh batch from a compress run. Accepted/dismissed
    rows from earlier runs are left alone — they're the audit trail, not scratch space."""
    conn.execute("DELETE FROM merge_proposal WHERE status='pending'")
    now = _now()
    for p in proposals:
        pid = "MP" + uuid.uuid4().hex[:8]
        conn.execute(
            "INSERT INTO merge_proposal (id, family_id, survivor_id, absorbed_ids, merged_label, "
            "rationale, status, created_at) VALUES (?,?,?,?,?,?, 'pending', ?)",
            (pid, p.get("family_id"), p["survivor_id"], json.dumps(p["absorbed_ids"]),
             p.get("merged_label") or None, p.get("rationale", ""), now))
    conn.commit()


def merge_proposals_payload(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    q = ("SELECT id, family_id, survivor_id, absorbed_ids, merged_label, rationale, status, "
         "created_at FROM merge_proposal")
    args: list = []
    if status:
        q += " WHERE status=?"; args.append(status)
    q += " ORDER BY created_at, id"
    return [{"id": r[0], "family_id": r[1], "survivor_id": r[2],
             "absorbed_ids": json.loads(r[3] or "[]"), "merged_label": r[4],
             "rationale": r[5] or "", "status": r[6], "created_at": r[7]}
            for r in conn.execute(q, args)]


def set_proposal_status(conn: sqlite3.Connection, pid_: str, status: str) -> bool:
    cur = conn.execute("UPDATE merge_proposal SET status=? WHERE id=?", (status, pid_))
    conn.commit()
    return cur.rowcount > 0


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
    # researcher theme revisions are keyed by (mode, theme_id): once the catalogue is replaced,
    # revisions pointing at ids that no longer exist are orphans — drop them so the rebuild
    # warning doesn't fire forever and a future run reusing an old id can't inherit a stale edit.
    ids = [t["id"] for t in themes]
    if ids:
        q = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM theme_revision WHERE mode=? AND theme_id NOT IN ({q})",
                     (mode, *ids))
    else:
        conn.execute("DELETE FROM theme_revision WHERE mode=?", (mode,))
    conn.commit()


def themes_payload(conn: sqlite3.Connection, mode: str) -> dict:
    """P8b: each theme gains researcher_label/researcher_claim (override wins when set), status
    ("active" | "merged" | "demoted"), and merged_into. A merge target's supporting_code_ids /
    key_evidence_sentence_ids / tensions / subthemes become the de-duplicated, order-preserving
    union of its own plus every theme merged into it (chain-followed); paradigm_provenance is
    summed key-wise across the union. Merged/demoted themes stay IN the returned list (so
    export/audit see them) but carry `status` so callers can filter them out by default — same
    shape as codes_payload's rejected/merged codes."""
    revs = theme_revisions_map(conn, mode)
    rows = {}
    for r in conn.execute(
            "SELECT id, central_concept, coverage, claim_scope, falsified_if, payload "
            "FROM theme_v2 WHERE mode=? ORDER BY id", (mode,)):
        t = {"id": r[0], "central_concept": r[1], "coverage": r[2], "claim_scope": r[3],
             "falsified_if": r[4]}
        t.update(json.loads(r[5]))
        rows[t["id"]] = t
    out = []
    for tid, t in rows.items():
        rev = revs.get(tid, {})
        merged_into = rev.get("merged_into")
        if merged_into:
            t["status"] = "merged"
        elif rev.get("demoted"):
            t["status"] = "demoted"
        else:
            t["status"] = "active"
        t["merged_into"] = merged_into
        t["researcher_label"] = rev.get("researcher_label")
        t["researcher_claim"] = rev.get("researcher_claim")
        out.append(t)
    # survivor union: gather every theme whose merge chain resolves to this survivor, in
    # ascending theme-id order (deterministic), unioning list fields de-duplicated/order-preserving
    # and summing paradigm_provenance key-wise.
    absorbed_by_survivor: dict[str, list[str]] = {}
    for tid, rev in revs.items():
        if rev.get("merged_into"):
            absorbed_by_survivor.setdefault(rev["merged_into"], []).append(tid)
    if absorbed_by_survivor:
        for t in out:
            absorbed = absorbed_by_survivor.get(t["id"])
            if not absorbed:
                continue
            for field in ("supporting_code_ids", "key_evidence_sentence_ids", "tensions"):
                seen = list(t.get(field, []))
                seen_set = set(seen)
                for absorbed_id in sorted(absorbed):
                    for v in rows.get(absorbed_id, {}).get(field, []):
                        if v not in seen_set:
                            seen.append(v)
                            seen_set.add(v)
                t[field] = seen
            sub_seen = list(t.get("subthemes", []))
            existing_claims = {s.get("claim") for s in sub_seen}
            for absorbed_id in sorted(absorbed):
                for s in rows.get(absorbed_id, {}).get("subthemes", []):
                    if s.get("claim") not in existing_claims:
                        sub_seen.append(s)
                        existing_claims.add(s.get("claim"))
            t["subthemes"] = sub_seen
            prov = dict(t.get("paradigm_provenance") or {})
            has_prov = "paradigm_provenance" in t
            for absorbed_id in sorted(absorbed):
                aprov = rows.get(absorbed_id, {}).get("paradigm_provenance")
                if aprov:
                    has_prov = True
                    for k, v in aprov.items():
                        prov[k] = prov.get(k, 0) + v
            if has_prov:
                t["paradigm_provenance"] = prov
    snaps = [{"doc_id": r[0], "themes": json.loads(r[1])} for r in conn.execute(
        "SELECT doc_id, snapshot FROM theme_step WHERE mode=? ORDER BY position", (mode,))]
    return {"mode": mode, "themes": out, "snapshots": snaps,
            "stale": themes_stale(conn, mode)}


# ---- theme authority (P8b) ---------------------------------------------------------------------
# Same audit-trail philosophy as code revisions (add_revision/revisions_map): the theme_v2 row
# itself is never mutated by a researcher edit — every relabel/reclaim/merge/demote/restore is an
# appended row in `theme_revision`, folded into current state at read time by
# theme_revisions_map/themes_payload. Ids are stable across extend-themes (a walk reuses a prior
# theme's id), so overrides survive it; only a FULL rebuild replaces theme_v2 wholesale and orphans
# them (the frontend warns before that — see `n_theme_revisions` on get_project).

def add_theme_revision(conn: sqlite3.Connection, mode: str, theme_id: str, action: str,
                       value: str | None = None) -> dict:
    row = conn.execute(
        "SELECT central_concept, coverage, claim_scope, payload FROM theme_v2 "
        "WHERE mode=? AND id=?", (mode, theme_id)).fetchone()
    ctx = {}
    if row:
        payload = json.loads(row[3] or "{}")
        ctx = {"central_concept": row[0], "coverage": row[1], "claim_scope": row[2],
              "label": payload.get("label", ""),
              "supporting_code_ids": payload.get("supporting_code_ids", [])}
    conn.execute(
        "INSERT INTO theme_revision (mode, theme_id, action, value, context, created_at) "
        "VALUES (?,?,?,?,?,?)", (mode, theme_id, action, value, json.dumps(ctx), _now()))
    conn.commit()
    return {"mode": mode, "theme_id": theme_id, "action": action, "value": value, "context": ctx}


def theme_revisions_map(conn: sqlite3.Connection, mode: str, resolve_chains: bool = True) -> dict:
    """Fold the theme_revision log into current per-theme state for one mode: latest 'relabel' ->
    researcher_label; latest 'reclaim' -> researcher_claim; 'merge' (value = target theme id) ->
    merged_into (chain-followed at read time, depth-capped like code merges); 'demote' ->
    demoted:True; a later 'restore' clears both merged_into and demoted (and does not touch
    researcher_label/researcher_claim — a relabel survives a merge/demote/restore cycle, mirroring
    how a code's rename survives its own merge/restore).

    `resolve_chains=False` returns the raw one-hop state (used internally by cycle validation)."""
    out: dict[str, dict] = {}
    for theme_id, action, value in conn.execute(
            "SELECT theme_id, action, value FROM theme_revision WHERE mode=? ORDER BY id",
            (mode,)):
        st = out.setdefault(theme_id, {"researcher_label": None, "researcher_claim": None,
                                       "merged_into": None, "demoted": False})
        if action == "relabel":
            st["researcher_label"] = value
        elif action == "reclaim":
            st["researcher_claim"] = value
        elif action == "merge":
            st["merged_into"] = value
        elif action == "demote":
            st["demoted"] = True
        elif action == "restore":
            st["merged_into"] = None
            st["demoted"] = False
    if resolve_chains:
        for theme_id, st in out.items():
            target = st.get("merged_into")
            if not target:
                continue
            seen = {theme_id}
            depth = 0
            while target in out and out[target].get("merged_into") and depth < 10:
                if target in seen:  # defensive: a cycle slipped through API validation
                    break
                seen.add(target)
                target = out[target]["merged_into"]
                depth += 1
            st["merged_into"] = target
    return out


def n_theme_revisions(conn: sqlite3.Connection, mode: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM theme_revision WHERE mode=?", (mode,)).fetchone()[0]


def demote_theme(conn: sqlite3.Connection, mode: str, theme_id: str) -> dict:
    """Demote a theme to memo material: appends a 'demote' revision AND writes/appends a memo
    (target_type='theme', target=theme_id) preserving its label + central_concept + supporting
    ids, so nothing is lost. A plain merge does NOT write a memo — only demote does (the merged
    theme's content lives on via the survivor's union instead)."""
    row = conn.execute(
        "SELECT central_concept, payload FROM theme_v2 WHERE mode=? AND id=?",
        (mode, theme_id)).fetchone()
    label, central_concept, supporting = theme_id, "", []
    if row:
        central_concept = row[0]
        payload = json.loads(row[1] or "{}")
        label = payload.get("label") or theme_id
        supporting = payload.get("supporting_code_ids", [])
    date = _now()[:10]
    body_lines = [f"Demoted from theme on {date}: {label}."]
    if central_concept:
        body_lines.append(central_concept)
    if supporting:
        body_lines.append("Supporting codes: " + ", ".join(supporting))
    existing = next((m for m in list_memos(conn, target_type="theme")
                     if m["target_id"] == theme_id), None)
    body = "\n\n".join(body_lines)
    if existing and existing.get("body"):
        body = existing["body"] + "\n\n" + body
    set_memo(conn, "theme", theme_id, body, {"label": label, "central_concept": central_concept})
    return add_theme_revision(conn, mode, theme_id, "demote")


# ---- P10.2: findings (theme_v2 rows minted incrementally by SYNTHESIZE) ------------------------
# Findings are NOT a new table (contract §2's "do not invent a second findings table" /
# MASSHINE.md §10's "theme authority (as finding editing)") — one finding IS one theme_v2 row,
# read and merge/relabel/demote/restore-able through the EXACT SAME machinery P8b already built
# (themes_payload, theme_revisions_map, add_theme_revision, compile_guidance's theme branch). The
# one difference from the legacy sequential theorist (themes.theorize_walk / persist_themes above)
# is persistence shape: that pass replaces a mode's WHOLE theme_v2 set on every run (a full
# re-walk); SYNTHESIZE runs ONE DOCUMENT AT A TIME (jobs.synthesize_work, mirroring
# jobs.read_work's per-doc checkpoint), so it needs to UPSERT one finding at a time instead —
# `upsert_finding` below, never `persist_themes`, from here on for this mode.
#
# ponytail: a project that runs the OLD /code+/themes pipeline and the NEW /read+/synthesize
# pipeline against the SAME `mode` string will corrupt each other's theme_v2 rows (persist_themes
# wholesale-deletes a mode; /themes/{id}/revise also targets whichever mode is passed). This is
# an operational constraint (pick one pipeline per project), not a runtime guard — add one (e.g.
# a `pipeline` column on the project registry) if a real project ever needs both.

def _next_finding_id(conn: sqlite3.Connection, mode: str) -> str:
    """A monotonic per-mode T-sequence, never reused or renumbered — same discipline as
    reconcile._next_code_id, keyed by `finding_seq:{mode}` in the shared `meta` table."""
    key = f"finding_seq:{mode}"
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    n = (row[0] if row else 0) + 1
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, n))
    return f"T{n:02d}"


def finding_row(conn: sqlite3.Connection, mode: str, finding_id: str) -> dict | None:
    """One finding straight off theme_v2 (payload merged in) — no theme_revision folding, no
    finding_state join. Used by synthesize.py's per-document resolve step, which needs the
    CURRENT persisted support to accumulate against (see synthesize._resolve_finding)."""
    row = conn.execute(
        "SELECT id, central_concept, coverage, claim_scope, falsified_if, payload "
        "FROM theme_v2 WHERE mode=? AND id=?", (mode, finding_id)).fetchone()
    if not row:
        return None
    f = {"id": row[0], "central_concept": row[1], "coverage": row[2], "claim_scope": row[3],
         "falsified_if": row[4]}
    f.update(json.loads(row[5] or "{}"))
    return f


def findings_for_mode(conn: sqlite3.Connection, mode: str) -> list[dict]:
    """Every finding currently persisted for `mode`, in id order — SYNTHESIZE's "current
    findings" prompt input (contract §3)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM theme_v2 WHERE mode=? ORDER BY id", (mode,))]
    return [f for f in (finding_row(conn, mode, fid) for fid in ids) if f]


def upsert_finding(conn: sqlite3.Connection, mode: str, finding: dict) -> str:
    """Write one already-validated finding (see synthesize._resolve_finding): `finding["id"]`
    empty mints a fresh one; non-empty UPDATES that row in place — no wholesale delete (the thing
    persist_themes does, and this must not, since SYNTHESIZE runs one document at a time).
    Returns the finding's final id."""
    fid = finding["id"] or _next_finding_id(conn, mode)
    payload = {k: v for k, v in finding.items() if k not in ("id", "central_concept")}
    conn.execute(
        "INSERT INTO theme_v2 (id, run_id, mode, central_concept, coverage, claim_scope, "
        "falsified_if, payload) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mode, id) DO UPDATE SET central_concept=excluded.central_concept, "
        "payload=excluded.payload",
        (fid, "", mode, finding.get("central_concept", ""), "", "", "", json.dumps(payload)))
    conn.commit()
    return fid


def findings_journal_payload(conn: sqlite3.Connection, mode: str) -> list[dict]:
    """The Journal's finding cards (contract §4): persisted support plus the computed
    standing/stance/opened-evidence gate state from `finding_state` (computed at SYNTHESIZE time
    by recompute_finding_state, never derived live here). `evidence_total`/`evidence_opened_count`
    are counted against the finding's OWN curated anchors (`key_evidence_sentence_ids`) — the
    set the evidence gate (R6, data-session-spec §4) actually asks the researcher to open."""
    states = {r[0]: r[1:] for r in conn.execute(
        "SELECT theme_id, standing, standing_note, stance, opened_evidence "
        "FROM finding_state WHERE mode=?", (mode,))}
    out = []
    for f in findings_for_mode(conn, mode):
        standing, standing_note, stance, opened_json = states.get(f["id"], (None, "", None, "[]"))
        opened = json.loads(opened_json or "[]")
        kev = f.get("key_evidence_sentence_ids", [])
        out.append({
            "id": f["id"], "label": f.get("label", ""), "central_concept": f["central_concept"],
            "standing": standing, "standing_note": standing_note, "stance": stance,
            "supporting_code_ids": f.get("supporting_code_ids", []),
            "key_evidence_sentence_ids": kev, "opened_evidence": opened,
            "evidence_opened_count": len(set(opened) & set(kev)), "evidence_total": len(kev),
        })
    return out


# ---- P10.2: finding_state — the computed standing, the rolled-up stance, the evidence gate ------

STANDING_MIN_DOCS = 2
STANDING_MIN_CODES = 3


def compute_standing(n_docs: int, n_codes: int) -> tuple[str, str]:
    """PURE — the evidential fact the contract forbids asking the model for (§3, §5.1): `firm`
    needs BOTH >= STANDING_MIN_DOCS documents and >= STANDING_MIN_CODES supporting codes; exactly
    one document is always `single-case` regardless of code count (there is no cross-case claim
    to grade yet); anything else (>=2 docs but under the code floor, or the degenerate zero-docs
    case) is `thin`."""
    if n_docs >= STANDING_MIN_DOCS and n_codes >= STANDING_MIN_CODES:
        return "firm", f"recurs in {n_docs} interviews · {n_codes} supporting codes"
    if n_docs == 1:
        return ("single-case",
                f"seen in 1 interview so far · {n_codes} supporting code{'' if n_codes == 1 else 's'}")
    if n_docs == 0:
        return "thin", "no grounded supporting evidence"
    return "thin", (f"recurs in {n_docs} interviews but only {n_codes} supporting code"
                    f"{'' if n_codes == 1 else 's'} — under the firm floor of {STANDING_MIN_CODES}")


def _finding_stance(conn: sqlite3.Connection, mode: str, finding_id: str) -> str | None:
    """The most recent walkthrough reaction on any step naming this finding as its target — a
    plain step carries it as payload["finding_id"], a checkback as payload["target"]. Folded in
    Python (per-project step counts are small; no JSON1 dependency needed)."""
    latest_at, latest_reaction = None, None
    for created_at, kind, payload_json, reaction in conn.execute(
            "SELECT created_at, kind, payload, reaction FROM step "
            "WHERE mode=? AND reaction IS NOT NULL", (mode,)):
        payload = json.loads(payload_json or "{}")
        target = payload.get("target") if kind == "checkback" else payload.get("finding_id")
        if target == finding_id and (latest_at is None or created_at >= latest_at):
            latest_at, latest_reaction = created_at, reaction
    return latest_reaction


def recompute_finding_state(conn: sqlite3.Connection, mode: str, finding_id: str) -> dict:
    """Recompute `standing`/`standing_note`/`stance` for one finding from its CURRENT persisted
    support (contract §5.1: standing is computed, never trusted from the model or accepted from a
    researcher endpoint) — called after every SYNTHESIZE document and after every step reaction
    that targets a finding. `opened_evidence` is preserved untouched (this never writes the
    evidence gate log)."""
    f = finding_row(conn, mode, finding_id)
    sup = f.get("supporting_code_ids", []) if f else []
    codes = {c["id"]: c for c in codes_payload(conn)}
    docs = {ev.split("#", 1)[0] for cid in sup for ev in codes.get(cid, {}).get("evidence", [])}
    standing, note = compute_standing(len(docs), len(sup))
    stance = _finding_stance(conn, mode, finding_id)
    existing = conn.execute(
        "SELECT opened_evidence FROM finding_state WHERE theme_id=?", (finding_id,)).fetchone()
    opened = existing[0] if existing else "[]"
    now = _now()
    conn.execute(
        "INSERT INTO finding_state (theme_id, mode, standing, standing_note, stance, "
        "opened_evidence, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(theme_id) DO UPDATE SET mode=excluded.mode, standing=excluded.standing, "
        "standing_note=excluded.standing_note, stance=excluded.stance, updated_at=excluded.updated_at",
        (finding_id, mode, standing, note, stance, opened, now))
    conn.commit()
    return {"theme_id": finding_id, "mode": mode, "standing": standing, "standing_note": note,
            "stance": stance, "opened_evidence": json.loads(opened), "updated_at": now}


def mark_evidence_opened(conn: sqlite3.Connection, finding_id: str, sid: str) -> list[str] | None:
    """Append one sid to a finding's opened-evidence gate log (idempotent — the same sid twice is
    a no-op). None if the finding has no finding_state row yet (caller 404s) — in practice every
    persisted finding gets one via recompute_finding_state the moment it first exists."""
    row = conn.execute(
        "SELECT opened_evidence FROM finding_state WHERE theme_id=?", (finding_id,)).fetchone()
    if not row:
        return None
    opened = json.loads(row[0] or "[]")
    if sid not in opened:
        opened.append(sid)
        conn.execute("UPDATE finding_state SET opened_evidence=?, updated_at=? WHERE theme_id=?",
                     (json.dumps(opened), _now(), finding_id))
        conn.commit()
    return opened


# ---- P10.2: focus versioning (contract §2, §4) --------------------------------------------------
# The research question as a versioned object. Registry `project.research_question` (a DIFFERENT
# sqlite file — projects.py's cross-project registry, not this project db) stays a cached mirror
# of whichever row is 'active'; syncing that mirror is the CALLER's job (api.py, which already
# imports `projects`) — this module only ever touches the project-db connection it's handed.

def _next_focus_n(conn: sqlite3.Connection) -> int:
    return (conn.execute("SELECT COALESCE(MAX(n), 0) FROM focus_version").fetchone()[0] or 0) + 1


def mint_focus_version(conn: sqlite3.Connection, text: str, author: str, rationale: str = "",
                       status: str = "active") -> dict:
    """A fresh focus_version row. `status='active'` (the default — a researcher edit) supersedes
    whatever was active before, so exactly one row is ever 'active'. `status='proposed'` (an
    assistant suggestion) does NOT touch the active row — see propose_focus, which clears any
    earlier pending proposal first."""
    if status == "active":
        conn.execute("UPDATE focus_version SET status='superseded' WHERE status='active'")
    n = _next_focus_n(conn)
    now = _now()
    conn.execute(
        "INSERT INTO focus_version (n, text, author, status, rationale, created_at) "
        "VALUES (?,?,?,?,?,?)", (n, text, author, status, rationale, now))
    conn.commit()
    return {"n": n, "text": text, "author": author, "status": status, "rationale": rationale,
            "created_at": now}


def propose_focus(conn: sqlite3.Connection, text: str, rationale: str) -> dict:
    """SYNTHESIZE's optional focus_proposal (contract §3) → a 'proposed' row. Any earlier
    still-pending proposal is superseded first — only the latest machine suggestion stands."""
    conn.execute("UPDATE focus_version SET status='superseded' WHERE status='proposed'")
    return mint_focus_version(conn, text, "assistant", rationale, status="proposed")


def active_focus(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT n, text, author, rationale, created_at FROM focus_version "
        "WHERE status='active' ORDER BY n DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"n": row[0], "text": row[1], "author": row[2], "rationale": row[3],
            "created_at": row[4]}


def focus_history(conn: sqlite3.Connection) -> list[dict]:
    """The adopted-focus lineage (active + superseded rows only — a declined or still-pending
    proposal never WAS the focus, so it stays out of history and lives under `proposal` instead —
    see journal_payload)."""
    return [{"n": r[0], "text": r[1], "author": r[2], "status": r[3], "rationale": r[4],
            "created_at": r[5]}
            for r in conn.execute(
                "SELECT n, text, author, status, rationale, created_at FROM focus_version "
                "WHERE status IN ('active','superseded') ORDER BY n")]


def pending_focus_proposal(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT n, text, rationale, created_at FROM focus_version "
        "WHERE status='proposed' ORDER BY n DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"n": row[0], "text": row[1], "rationale": row[2], "created_at": row[3]}


def accept_focus_proposal(conn: sqlite3.Connection, n: int) -> dict | None:
    """Promote a pending proposal to the active focus. None if `n` isn't a pending proposal
    (caller 404s)."""
    row = conn.execute(
        "SELECT text, rationale FROM focus_version WHERE n=? AND status='proposed'", (n,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE focus_version SET status='superseded' WHERE status='active'")
    conn.execute("UPDATE focus_version SET status='active' WHERE n=?", (n,))
    conn.commit()
    return {"n": n, "text": row[0], "author": "assistant", "rationale": row[1], "status": "active"}


def decline_focus_proposal(conn: sqlite3.Connection, n: int) -> bool:
    cur = conn.execute(
        "UPDATE focus_version SET status='declined' WHERE n=? AND status='proposed'", (n,))
    conn.commit()
    return cur.rowcount > 0


# ---- P10.2: walkthrough steps, checkbacks, and residue — all `step` rows ------------------------
# Distinguished only by `kind` (contract §2: no separate residue table). `payload`'s shape depends
# on kind: pattern/tension/uncertainty/delta/declined carry {"statement","sids","code_ids",
# "weakest_sids","finding_id"}; checkback carries {"steer","target","supports":{"text","sids"},
# "strains":{"text","sids"},"not_found":{"text"},"proposal"}; residue carries {"note","sids",
# "code_ids","reframe_offer"}. `_step_view` flattens any of the three into ONE shape (the
# contract's session response) so a caller never branches on kind to find `statement`/`sids`/
# `finding_id` — a checkback additionally carries its raw shape back under `checkback`.

_STEP_COLS = ("id", "mode", "doc_id", "position", "kind", "payload", "reaction",
             "reaction_note", "created_at")


def _step_row(r) -> dict:
    d = dict(zip(_STEP_COLS, r))
    d["payload"] = json.loads(d["payload"] or "{}")
    return d


def _step_view(row: dict) -> dict:
    p, kind = row["payload"], row["kind"]
    if kind == "checkback":
        sids = list(p.get("supports", {}).get("sids", [])) + list(p.get("strains", {}).get("sids", []))
        flat = {"statement": p.get("steer", ""), "sids": sids, "code_ids": [],
               "weakest_sids": [], "finding_id": p.get("target"), "checkback": p}
    elif kind == "residue":
        flat = {"statement": p.get("note", ""), "sids": p.get("sids", []),
               "code_ids": p.get("code_ids", []), "weakest_sids": [], "finding_id": None,
               "reframe_offer": p.get("reframe_offer", "")}
    else:
        flat = {k: p.get(k) for k in ("statement", "sids", "code_ids", "weakest_sids", "finding_id")}
    return {"id": row["id"], "doc_id": row["doc_id"], "kind": kind,
            "reaction": row["reaction"], "reaction_note": row["reaction_note"], **flat}


def insert_step(conn: sqlite3.Connection, mode: str, doc_id: str, kind: str, payload: dict) -> dict:
    sid = "ST" + uuid.uuid4().hex[:8]
    position = (conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM step WHERE doc_id=?",
        (doc_id,)).fetchone()[0] or -1) + 1
    now = _now()
    conn.execute(
        "INSERT INTO step (id, mode, doc_id, position, kind, payload, reaction, reaction_note, "
        "created_at) VALUES (?,?,?,?,?,?,NULL,NULL,?)",
        (sid, mode, doc_id, position, kind, json.dumps(payload), now))
    conn.commit()
    return _step_view(_step_row(
        (sid, mode, doc_id, position, kind, json.dumps(payload), None, None, now)))


def steps_for_doc(conn: sqlite3.Connection, mode: str, doc_id: str,
                  include_residue: bool = False) -> list[dict]:
    """The per-document walkthrough (contract's GET /session/{doc_id}): every kind except
    `residue` in position order — residue's home is the Journal's "doesn't fit yet" (MASSHINE.md
    §2's mechanism table), not the paced per-document sequence. `include_residue=True` for a
    caller that wants the raw rows regardless (there is none yet; kept for symmetry/tests)."""
    q = f"SELECT {','.join(_STEP_COLS)} FROM step WHERE mode=? AND doc_id=?"
    args = [mode, doc_id]
    if not include_residue:
        q += " AND kind != 'residue'"
    q += " ORDER BY position"
    return [_step_view(_step_row(r)) for r in conn.execute(q, args)]


def residue_items(conn: sqlite3.Connection, mode: str) -> list[dict]:
    """Every residue step, project-wide, oldest first — the stable ordering
    POST /residue/{idx}/reframe's `idx` indexes into (contract §4)."""
    rows = conn.execute(
        f"SELECT {','.join(_STEP_COLS)} FROM step WHERE mode=? AND kind='residue' "
        "ORDER BY created_at, id", (mode,)).fetchall()
    return [_step_view(_step_row(r)) for r in rows]


def adopt_reframe(conn: sqlite3.Connection, mode: str, idx: int) -> dict | None:
    """Turn residue entry `idx`'s reframe offer into standing guidance (contract §5.3: never
    auto-resolved — only this explicit researcher action does it). Marks the step's reaction
    'reframe' so compile_guidance's adopted-reframe line picks it up on the next read/synthesis.
    None if `idx` is out of range (caller 404s).

    ponytail: `idx` is a POSITION in residue_items' stable ordering, not a durable id — fine as
    long as nothing inserts a new residue row between a GET /journal and the matching POST here
    (a single-researcher, single-request UI flow never race). A concurrent SYNTHESIZE run adding
    residue mid-review could shift indices; a step-id-based endpoint would be sturdier. Upgrade
    path if that ever bites: switch the route to take a step_id, same as /steps/{step_id}/react."""
    rows = conn.execute(
        "SELECT id FROM step WHERE mode=? AND kind='residue' ORDER BY created_at, id",
        (mode,)).fetchall()
    if idx < 0 or idx >= len(rows):
        return None
    step_id = rows[idx][0]
    conn.execute("UPDATE step SET reaction='reframe' WHERE id=?", (step_id,))
    conn.commit()
    row = conn.execute(f"SELECT {','.join(_STEP_COLS)} FROM step WHERE id=?", (step_id,)).fetchone()
    return _step_view(_step_row(row))


def react_to_step(conn: sqlite3.Connection, step_id: str, reaction: str,
                  note: str | None = None, statement: str | None = None) -> dict | None:
    """Record a walkthrough reaction (contract §4 — agree/challenge/reframe/park). `reframe` with
    a `statement` rewrites the step's own text — RESEARCHER WORDING WINS, the original is kept
    under payload["original_statement"] (data-session-spec §3). If the step targets a finding (a
    plain step's "finding_id", or a checkback's "target"), that finding's `stance` is recomputed
    immediately — the researcher shouldn't have to wait for the next SYNTHESIZE run to see their
    own reaction reflected in the Journal. None if `step_id` doesn't exist."""
    row = conn.execute(f"SELECT {','.join(_STEP_COLS)} FROM step WHERE id=?", (step_id,)).fetchone()
    if not row:
        return None
    d = _step_row(row)
    payload = d["payload"]
    if reaction == "reframe" and statement:
        key = "note" if d["kind"] == "residue" else "steer" if d["kind"] == "checkback" else "statement"
        payload.setdefault("original_statement", payload.get(key, ""))
        payload[key] = statement
    conn.execute("UPDATE step SET reaction=?, reaction_note=?, payload=? WHERE id=?",
                 (reaction, note, json.dumps(payload), step_id))
    conn.commit()
    d["reaction"], d["reaction_note"], d["payload"] = reaction, note, payload
    view = _step_view(d)
    fid = view.get("finding_id")
    if fid and conn.execute(
            "SELECT 1 FROM theme_v2 WHERE mode=? AND id=?", (d["mode"], fid)).fetchone():
        recompute_finding_state(conn, d["mode"], fid)
    return view


# ---- P10.2: story-so-far + document intros -------------------------------------------------------

def add_story_version(conn: sqlite3.Connection, paragraphs: list[dict]) -> dict:
    n = (conn.execute("SELECT COALESCE(MAX(n), 0) FROM story_version").fetchone()[0] or 0) + 1
    now = _now()
    conn.execute("INSERT INTO story_version (n, text, created_at) VALUES (?,?,?)",
                (n, json.dumps(paragraphs), now))
    conn.commit()
    return {"n": n, "paras": paragraphs, "created_at": now}


def latest_story(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT n, text FROM story_version ORDER BY n DESC LIMIT 1").fetchone()
    return {"n": row[0], "paras": json.loads(row[1])} if row else {"n": 0, "paras": []}


def story_version_ns(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT n FROM story_version ORDER BY n")]


def set_intro(conn: sqlite3.Connection, doc_id: str, paragraphs: list[dict]) -> None:
    conn.execute(
        "INSERT INTO intro (doc_id, text, created_at) VALUES (?,?,?) "
        "ON CONFLICT(doc_id) DO UPDATE SET text=excluded.text, created_at=excluded.created_at",
        (doc_id, json.dumps(paragraphs), _now()))
    conn.commit()


def get_intro(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    row = conn.execute("SELECT text FROM intro WHERE doc_id=?", (doc_id,)).fetchone()
    return json.loads(row[0]) if row else []


# ---- P10.2: the three read surfaces (contract §4) ------------------------------------------------

def session_payload(conn: sqlite3.Connection, mode: str, doc_id: str) -> dict | None:
    row = conn.execute("SELECT id, title, filename FROM document WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return None
    title = (row[1] or "").strip() or row[2]
    steps = steps_for_doc(conn, mode, doc_id)
    return {"intro": get_intro(conn, doc_id), "steps": steps, "n_steps": len(steps),
            "doc": {"id": row[0], "title": title}}


def journal_payload(conn: sqlite3.Connection, mode: str) -> dict:
    story = latest_story(conn)
    residue = [{"note": r["statement"], "sids": r["sids"], "code_ids": r["code_ids"],
               "reframe_offer": r["reframe_offer"]} for r in residue_items(conn, mode)]
    return {
        "focus": {"active": active_focus(conn), "history": focus_history(conn),
                 "proposal": pending_focus_proposal(conn)},
        "story": {"n": story["n"], "paras": story["paras"], "versions": story_version_ns(conn)},
        "findings": findings_journal_payload(conn, mode),
        "residue": residue,
        "memos": list_memos(conn),
        "history": [],   # project git-history timeline (data-session-spec §7) — not built in P10.2
    }


def needs_judgment_payload(conn: sqlite3.Connection, mode: str) -> list[dict]:
    """Exceptions only (contract §5.4) — never clerical "N codes to review" busywork. Audio
    awaiting review is NOT computed here (it needs the project's upload directory, a filesystem
    concern this module doesn't otherwise touch) — api.py's endpoint appends those items itself."""
    items = []
    for r in conn.execute(
            f"SELECT {','.join(_STEP_COLS)} FROM step WHERE mode=? AND kind='checkback' "
            "AND reaction IS NULL ORDER BY created_at, id", (mode,)):
        v = _step_view(_step_row(r))
        cb = v["checkback"]
        items.append({"kind": "checkback", "title": f'Check-back on {v["finding_id"] or "a finding"}',
                     "detail": cb.get("steer", ""), "target_type": "step", "target_id": v["id"],
                     "action_hint": "review the check-back against the material"})
    for f in findings_journal_payload(conn, mode):
        if f["stance"] == "challenge":
            items.append({"kind": "strained_finding",
                         "title": f["label"] or f["central_concept"],
                         "detail": f["standing_note"], "target_type": "finding",
                         "target_id": f["id"], "action_hint": "revisit the challenged finding"})
        elif f["standing"] == "thin":
            items.append({"kind": "thin_finding", "title": f["label"] or f["central_concept"],
                         "detail": f["standing_note"], "target_type": "finding",
                         "target_id": f["id"], "action_hint": "weigh the thin finding"})
    proposal = pending_focus_proposal(conn)
    if proposal:
        items.append({"kind": "focus_proposal", "title": "A refined research focus is proposed",
                     "detail": proposal["text"], "target_type": "focus",
                     "target_id": str(proposal["n"]),
                     "action_hint": "accept or decline the proposal"})
    for idx, r in enumerate(residue_items(conn, mode)):
        if r["reaction"] is None and r.get("reframe_offer"):
            items.append({"kind": "residue", "title": "Material doesn't fit yet",
                         "detail": r["reframe_offer"], "target_type": "residue",
                         "target_id": str(idx), "action_hint": "adopt or leave the reframe offer"})
    return items


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


def revisions_map(conn: sqlite3.Connection, resolve_chains: bool = True) -> dict:
    """Fold the revision log into the current per-code state: latest rename wins; rejected is
    true unless a later 'restore' lifts it. P8a: 'merge' sets merged_into to the SURVIVOR code id
    (stored in the `new_label` column — reused, documented in db.py's revision table comment); a
    later 'restore' un-merges by clearing BOTH rejected and merged_into.

    A merge chain (A merged into B, B later merged into C) is followed at read time — capped at
    depth 10 against pathological cycles — so evidence/guidance always lands on the FINAL
    survivor rather than an intermediate one. `resolve_chains=False` returns the raw one-hop
    state (used internally by cycle validation, which must see the unresolved graph)."""
    out: dict[str, dict] = {}
    for code_id, action, new_label in conn.execute(
            "SELECT code_id, action, new_label FROM revision ORDER BY id"):
        st = out.setdefault(code_id, {"rejected": False, "new_label": None, "merged_into": None})
        if action == "rename":
            st["new_label"] = new_label
        elif action == "reject":
            st["rejected"] = True
        elif action == "restore":
            st["rejected"] = False
            st["merged_into"] = None
        elif action == "merge":
            st["merged_into"] = new_label
    if resolve_chains:
        for code_id, st in out.items():
            target = st.get("merged_into")
            if not target:
                continue
            seen = {code_id}
            depth = 0
            while target in out and out[target].get("merged_into") and depth < 10:
                if target in seen:  # defensive: a cycle slipped through API validation
                    break
                seen.add(target)
                target = out[target]["merged_into"]
                depth += 1
            st["merged_into"] = target
    return out


def open_comment_counts(conn: sqlite3.Connection) -> dict:
    """{doc_id: n} for open doc-scoped comments, plus '_project' for project-level ones."""
    out: dict[str, int] = {}
    for doc_id, n in conn.execute(
            "SELECT COALESCE(doc_id, '_project'), COUNT(*) FROM comment "
            "WHERE status='open' GROUP BY COALESCE(doc_id, '_project')"):
        out[doc_id] = n
    return out


def compile_guidance(conn: sqlite3.Connection, doc_id: str | None = None,
                     mode: str | None = None) -> str:
    """Compile the researcher's open feedback into the plain-text block a re-run's prompts carry.
    doc_id given → coding guidance for that document (sentence/code/document comments + revisions
    on that doc's codes). doc_id None → project-level theme guidance (theme comments + a summary
    of all code revisions, since themes read the whole codebook). `mode` ("standard" | "panel"),
    given alongside doc_id=None, also folds in P8b theme_revision lines — a rebuild-with-feedback
    must respect an earlier relabel/demote/merge, not silently re-propose over it."""
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
            if st.get("merged_into"):
                survivor_label, _ = labels.get(
                    st["merged_into"],
                    (ctxs.get(st["merged_into"], {}).get("label", st["merged_into"]), None))
                lines.append(f'- The researcher MERGED "{label}" into "{survivor_label}" — '
                             f"treat them as one concept.")
            elif st["rejected"]:
                lines.append(f'- The researcher REJECTED the code "{label}" — do not '
                             f"re-propose this interpretation.")
            elif st["new_label"]:
                lines.append(f'- The researcher renamed "{label}" to "{st["new_label"]}" — '
                             f"use the new name and its implied focus.")
    if not doc_id and mode:
        theme_revs = theme_revisions_map(conn, mode)
        if theme_revs:
            theme_labels = {r[0]: json.loads(r[1] or "{}").get("label", r[0])
                            for r in conn.execute(
                                "SELECT id, payload FROM theme_v2 WHERE mode=?", (mode,))}
            ctxs = {r[0]: json.loads(r[1] or "{}") for r in conn.execute(
                "SELECT theme_id, context FROM theme_revision WHERE mode=? ORDER BY id",
                (mode,))}
            def _current_label(tid, st):
                # the researcher's latest relabel if any, else the machine label — a theme that
                # was renamed and THEN merged/demoted should read by its latest name everywhere
                # EXCEPT the relabel line itself, which needs the OLD name to say what changed.
                return (st.get("researcher_label") or theme_labels.get(tid)
                       or ctxs.get(tid, {}).get("label", tid))
            for theme_id, st in theme_revs.items():
                lbl = _current_label(theme_id, st)
                if st.get("merged_into"):
                    survivor_st = theme_revs.get(st["merged_into"], {})
                    survivor_lbl = _current_label(st["merged_into"], survivor_st)
                    lines.append(f'- The researcher MERGED the theme "{lbl}" into '
                                 f'"{survivor_lbl}" — treat them as one theme.')
                elif st.get("demoted"):
                    lines.append(f'- The researcher DEMOTED the theme "{lbl}" — do not '
                                 f're-propose it as a top-level theme.')
                elif st.get("researcher_label"):
                    old_lbl = theme_labels.get(theme_id) or ctxs.get(theme_id, {}).get("label", theme_id)
                    lines.append(f'- The researcher renamed the theme "{old_lbl}" to '
                                 f'"{st["researcher_label"]}" — use the new name.')
    return "\n".join(lines + _p10_2_guidance_lines(conn))


def _p10_2_guidance_lines(conn: sqlite3.Connection) -> list[str]:
    """P10.2's additions to every guidance call, doc-scoped or project-scoped alike (contract
    §5.5): the active focus and whether it changed, parked declines to re-offer (READ's own
    out-of-scope memos — "each run", unconditionally, per contract §5), adopted reframes, and
    standing walkthrough step reactions. Read WITHOUT a mode filter — READ's own guidance call
    (jobs.read_work → compile_guidance(conn, doc_id)) carries no mode at all, and in practice a
    project only ever runs SYNTHESIZE under one mode's id-space anyway (see the findings
    section's ponytail note above)."""
    lines: list[str] = []
    focus = active_focus(conn)
    if focus:
        lines.append(f'- The current research focus is: "{focus["text"]}".')
    history = focus_history(conn)
    if len(history) > 1:
        prev, cur = history[-2], history[-1]
        rationale = f' — {cur["rationale"]}' if cur.get("rationale") else ""
        lines.append(f'- The research focus changed from "{prev["text"]}" to "{cur["text"]}"'
                     f'{rationale}. Reconsider anything declined under the earlier focus.')
    for m in list_memos(conn, target_type="document"):
        if m.get("author") == "assistant" and m.get("body"):
            lines.append(f"- Material was declined as out of scope on {m['target_id']}: "
                         f"{m['body']}")
    verbs = {"agree": "agreed with", "challenge": "challenged", "reframe": "reframed",
            "park": "parked"}
    for row in conn.execute(
            f"SELECT {','.join(_STEP_COLS)} FROM step WHERE reaction IS NOT NULL "
            "ORDER BY created_at"):
        v = _step_view(_step_row(row))
        if v["kind"] == "residue" and v["reaction"] == "reframe":
            lines.append('- An earlier "doesn\'t fit yet" passage was reframed by the '
                         f'researcher: {v.get("reframe_offer") or v["statement"]} — treat this '
                         "as new analytic direction.")
            continue
        verb = verbs.get(v["reaction"], v["reaction"])
        line = f'- The researcher {verb} the walkthrough step "{v["statement"]}"'
        if v["reaction_note"]:
            line += f": {v['reaction_note']}"
        lines.append(line + ".")
    return lines


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


def _model_manifest(conn: sqlite3.Connection, model_id: str | None) -> dict:
    """P10.1c provenance: which model this project is CONFIGURED to run under right now
    (model_id — the project's own default, or None for the server env default) and what that
    actually resolves to, plus every distinct model any run row in this project's history
    actually used — so a project coded partly under one model and partly under another stays
    honest even though `model`/`model_id` above only describe the CURRENT setting."""
    from . import llm, models
    entry = models.resolve(model_id) if model_id else None
    with llm.use_model(entry):
        resolved_model = llm.model()
    rows = conn.execute("SELECT DISTINCT model FROM run WHERE model IS NOT NULL").fetchall()
    return {"model_id": model_id, "model": resolved_model,
           "models_used": sorted(r[0] for r in rows)}


def export_payload(conn: sqlite3.Connection, mode: str, model_id: str | None = None) -> dict:
    codes = codes_payload(conn)
    for c in codes:
        c["evidence"] = [{"id": e, "quote": _safe_quote(conn, e)} for e in c["evidence"]]
    th = themes_payload(conn, mode)
    return {
        "exported_at": _now(),
        "mode": mode,
        "manifest": _model_manifest(conn, model_id),
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
    w.writerow(["id", "label", "central_concept", "coverage", "claim_scope", "provenance",
                "n_supporting", "supporting_code_ids", "tensions", "subthemes",
                "key_evidence", "falsified_if", "researcher_memo"])
    for t in themes_payload(conn, mode)["themes"]:
        prov = t.get("paradigm_provenance") or {}
        w.writerow([
            t["id"], t.get("label", ""), t["central_concept"], t.get("coverage", ""),
            t.get("claim_scope", ""),
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
        heading = t.get("label") or t["central_concept"]
        out.append(f"### {t['id']} — {heading}")
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
    active = [c for c in codes if c["status"] == "active"]
    rejected = [c for c in codes if c["status"] == "rejected"]
    merged = [c for c in codes if c["status"] == "merged"]
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
    if merged:
        out.append("### Merged codes")
        out.append("")
        for c in merged:
            lbl = c.get("researcher_label") or c["label"]
            survivor = by_id.get(c["merged_into"])
            survivor_lbl = (survivor.get("researcher_label") or survivor["label"]) if survivor \
                else c["merged_into"]
            out.append(f"- {lbl} → merged into **{survivor_lbl}**")
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
