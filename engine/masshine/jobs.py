"""Background job execution (Phase 3). A single-worker thread pool serializes the LLM-heavy runs
(coding already saturates the provider at CONCURRENCY=8 internally), while job rows in the registry
give pollable status independent of the HTTP request. Resumability comes from the same per-project
JSON checkpoint the CLI uses: re-POSTing a code/theme job resumes from where it stopped.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import packs, projects, runner, store
from .coding import code_document, code_sections_panel
from .compress import compress_batches, propose_merges
from .consolidate import consolidate_codebook
from .db import new_run, project_db
from .ingest import ingest
from .reconcile import reconcile_project
from .themes import (theorize_panel_sequential, theorize_project_sequential,
                     transcript_block_from_sentences)

_EXECUTOR = ThreadPoolExecutor(max_workers=1)  # serialize LLM-heavy jobs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit(job_id: str, work) -> None:
    _EXECUTOR.submit(_run, job_id, work)


def _run(job_id: str, work) -> None:
    projects.update_job(job_id, status="running", started_at=_now())

    def progress(**p):
        projects.update_job(job_id, progress=p)

    try:
        result = work(progress)
        projects.update_job(job_id, status="done", result=result or {}, finished_at=_now())
    except Exception as e:
        import traceback
        traceback.print_exc()
        projects.update_job(job_id, status="failed",
                            error=f"{type(e).__name__}: {e}", finished_at=_now())


# ---- work builders (each returns work(progress) -> result dict) ----------------------------------

def ingest_work(pid: str, upload_path: Path, kind: str = "transcript"):
    def work(progress):
        progress(stage="structure", message=f"structuring {upload_path.name}")
        conn = project_db(projects.project_db_path(pid))
        try:
            run = new_run(conn, "ingest")
            doc_id, secs, sents = ingest(conn, run, upload_path)
            conn.execute("UPDATE document SET filename=?, status='ingested', created_at=?, "
                         "kind=? WHERE id=?", (upload_path.name, _now(), kind, doc_id))
            conn.commit()
        finally:
            conn.close()
        return {"doc_id": doc_id, "sections": len(secs), "sentences": len(sents)}
    return work


def code_work(pid: str, mode: str, recode: bool = False):
    def work(progress):
        cp = projects.checkpoint_path(pid, mode)
        state = runner.load_checkpoint(cp, recode=recode)
        conn = project_db(projects.project_db_path(pid))
        try:
            run = new_run(conn, f"code:{mode}")
            rows = conn.execute(
                "SELECT id, filename FROM document ORDER BY created_at, id").fetchall()
            order = [r[0] for r in rows]
            names = {r[0]: r[1] for r in rows}
            state["order"] = order
            docs = state.setdefault("docs", {})
            proj = projects.get_project(pid)
            coders = packs.panel_coders(proj["pack_id"]) if mode == "panel" else None
            total = len(order)
            new_docs = []  # doc ids coded THIS run (not already in the checkpoint before we started)
            for idx, doc_id in enumerate(order, 1):
                if doc_id in docs:
                    continue
                new_docs.append(doc_id)
                progress(stage="coding", doc_id=doc_id, done=idx - 1, total=total,
                         message=f"coding {names[doc_id]}")
                entry = store.doc_entry(conn, doc_id, names[doc_id])
                if mode == "panel":
                    panel, nfail = code_sections_panel(conn, doc_id, coders)
                    if nfail:
                        raise RuntimeError(f"{names[doc_id]}: {nfail} lens-section(s) failed")
                    entry["panel"] = panel
                else:
                    ds, dropped, nfail = code_document(conn, run, doc_id)
                    if nfail:
                        raise RuntimeError(f"{names[doc_id]}: {nfail} section(s) failed")
                    entry["codes"] = ds
                docs[doc_id] = entry
                conn.execute("UPDATE document SET status=? WHERE id=?", (f"coded:{mode}", doc_id))
                conn.commit()
                state.pop("project_codebook", None)  # new codes → reconcile is stale
                runner.save_checkpoint(cp, state)
            # persist to the code table
            if mode == "panel":
                store.persist_panel_codes(conn, run, order, docs, coders)
            elif not state.get("project_codebook"):
                progress(stage="reconcile", done=total, total=total, message="reconciling codebook")
                state["project_codebook"] = reconcile_project(
                    conn, run, [docs[d]["codes"] for d in order])
                runner.save_checkpoint(cp, state)
            # a code-table rewrite drops family_id (P6) — flag families stale if any exist
            if conn.execute("SELECT 1 FROM code_family LIMIT 1").fetchone():
                store.set_families_stale(conn, True)
            counts = store.code_counts(conn)
            # A newly-coded doc under an existing theme set silently under-covers the corpus —
            # only recode_work set the stale flag before, so freshly coded NEW documents never
            # surfaced a "bring this into the themes" action. theme_work's raw_cache replay makes
            # extending themes to the new doc(s) free for the already-themed ones (see theme_work).
            if new_docs:
                n_themes = conn.execute(
                    "SELECT COUNT(*) FROM theme_v2 WHERE mode=?", (mode,)).fetchone()[0]
                if n_themes:
                    store.set_themes_stale(conn, mode, True)
        finally:
            conn.close()
        return {"mode": mode, "docs": len(order), "code_counts": counts, "new_docs": new_docs}
    return work


def recode_work(pid: str, doc_id: str, mode: str):
    """Re-code ONE document with the researcher's open feedback compiled into the prompts.
    Theme steps that embedded the old codes go stale: in panel mode every step from this doc's
    position onward (earlier docs' code ids are unchanged — enumeration is doc-major); in standard
    mode ALL steps (the project-wide re-reconcile can reshuffle any id). The stale flag tells the
    UI to offer a theme rebuild; comments are flipped to 'addressed' only after success."""
    def work(progress):
        cp = projects.checkpoint_path(pid, mode)
        state = runner.load_checkpoint(cp)
        conn = project_db(projects.project_db_path(pid))
        try:
            guidance = store.compile_guidance(conn, doc_id) or None
            run = new_run(conn, f"recode:{mode}:{doc_id}")
            rows = conn.execute(
                "SELECT id, filename FROM document ORDER BY created_at, id").fetchall()
            order = [r[0] for r in rows]
            names = dict(rows)
            if doc_id not in order:
                raise RuntimeError(f"no document {doc_id}")
            before_labels = store.doc_code_labels(conn, doc_id)  # P4.11: snapshot before popping
            state["order"] = order
            docs = state.setdefault("docs", {})
            docs.pop(doc_id, None)
            stale_from = 0 if mode != "panel" else order.index(doc_id)
            steps = state.get("theme_steps", {})
            for d in order[stale_from:]:
                steps.pop(d, None)
            state.pop("project_codebook", None)
            proj = projects.get_project(pid)
            coders = packs.panel_coders(proj["pack_id"]) if mode == "panel" else None
            progress(stage="recoding", doc_id=doc_id, done=0, total=1,
                     message=f"recoding {names[doc_id]} with researcher feedback")
            entry = store.doc_entry(conn, doc_id, names[doc_id])
            if mode == "panel":
                panel, nfail = code_sections_panel(conn, doc_id, coders, guidance=guidance)
                if nfail:
                    raise RuntimeError(f"{names[doc_id]}: {nfail} lens-section(s) failed")
                entry["panel"] = panel
            else:
                ds, dropped, nfail = code_document(conn, run, doc_id, guidance=guidance)
                if nfail:
                    raise RuntimeError(f"{names[doc_id]}: {nfail} section(s) failed")
                entry["codes"] = ds
            docs[doc_id] = entry
            conn.execute("UPDATE document SET status=? WHERE id=?", (f"coded:{mode}", doc_id))
            conn.commit()
            runner.save_checkpoint(cp, state)
            if mode == "panel":
                store.persist_panel_codes(conn, run, order, docs, coders)
            else:
                progress(stage="reconcile", done=1, total=1, message="reconciling codebook")
                state["project_codebook"] = reconcile_project(
                    conn, run, [docs[d]["codes"] for d in order])
                runner.save_checkpoint(cp, state)
            conn.execute("DELETE FROM theme_step WHERE mode=? AND position>=?",
                         (mode, stale_from))
            conn.commit()
            store.set_themes_stale(conn, mode, True)
            # a code-table rewrite drops family_id (P6) — flag families stale if any exist
            if conn.execute("SELECT 1 FROM code_family LIMIT 1").fetchone():
                store.set_families_stale(conn, True)
            n_addr = store.mark_feedback_addressed(conn, doc_id=doc_id)
            counts = store.code_counts(conn)
            after_labels = store.doc_code_labels(conn, doc_id)  # P4.11: snapshot after persisting
            diff = store.diff_code_labels(before_labels, after_labels)
        finally:
            conn.close()
        return {"mode": mode, "doc_id": doc_id, "feedback_used": bool(guidance),
                "comments_addressed": n_addr, "notes_applied": n_addr, "code_counts": counts,
                "diff": diff}
    return work


def theme_work(pid: str, mode: str, feedback: bool = False):
    def work(progress):
        cp = projects.checkpoint_path(pid, mode)
        state = runner.load_checkpoint(cp)
        if not state or not state.get("docs"):
            raise RuntimeError("nothing coded yet — run coding before theming")
        order = state["order"]
        docs = state["docs"]
        conn = project_db(projects.project_db_path(pid))
        try:
            guidance = None
            if feedback:
                guidance = store.compile_guidance(conn, mode=mode) or None
                if guidance:  # every step must hear the feedback → full re-walk, no replay
                    state["theme_steps"] = {}
            transcripts = {d: transcript_block_from_sentences(docs[d]["sentences"],
                                                              docs[d]["sections"]) for d in order}
            valid = {d: {s["id"] for s in docs[d]["sentences"]} for d in order}
            theme_steps = state.setdefault("theme_steps", {})
            # a walk that replays NOTHING is a full rebuild: every prior theme id is re-minted,
            # so researcher theme revisions cannot be trusted to point at the same themes —
            # clear them wholesale after persist (extends keep their prefix and their revisions;
            # persist_themes additionally prunes orphaned ids on every run)
            full_rebuild = not theme_steps
            ctr = [0]

            def save_raw(doc_id, raw):
                theme_steps[doc_id] = raw
                runner.save_checkpoint(cp, state)
                ctr[0] += 1
                progress(stage="theming", doc_id=doc_id, done=ctr[0], total=len(order))

            if mode == "panel":
                panel_by_doc = {d: docs[d]["panel"] for d in order}
                themes, codebook, origin, snaps, fails = theorize_panel_sequential(
                    order, panel_by_doc, transcripts, valid,
                    raw_cache=theme_steps, save_raw=save_raw, guidance=guidance)
            else:
                themes, codebook, snaps, fails = theorize_project_sequential(
                    order, state["project_codebook"], transcripts, valid,
                    raw_cache=theme_steps, save_raw=save_raw, guidance=guidance)
            store.persist_themes(conn, mode, themes, snaps)
            if full_rebuild:
                conn.execute("DELETE FROM theme_revision WHERE mode=?", (mode,))
                conn.commit()
            store.set_themes_stale(conn, mode, False)
            if guidance:
                store.mark_feedback_addressed(conn, target_type="theme")
        finally:
            conn.close()
        return {"mode": mode, "themes": len(themes), "failures": fails,
                "feedback_used": bool(guidance)}
    return work


def consolidate_work(pid: str):
    """P6: group the whole codebook into 8–15 code families. Small/single-source projects get
    one LLM call; larger multi-source projects use a hierarchical map-reduce (per-source
    families, then one aggregation) — see consolidate.consolidate_codebook. Reads open family
    comments as guidance, persists families + hues, clears the staleness flag, and marks those
    comments addressed — the same shape as theme_work's feedback handling."""
    def work(progress):
        conn = project_db(projects.project_db_path(pid))
        try:
            codes = store.codes_payload(conn)
            guidance = store.compile_family_guidance(conn) or None
            doc_titles = {r[0]: (r[1] or "").strip() or r[2]
                          for r in conn.execute("SELECT id, title, filename FROM document")}
            progress(stage="consolidate", message="grouping the codebook into families")
            families = consolidate_codebook(codes, guidance=guidance, doc_titles=doc_titles,
                                             progress=progress)
            store.persist_families(conn, families)
            store.set_families_stale(conn, False)
            n_addr = store.mark_feedback_addressed(conn, target_type="family")
            unfiled = next((f for f in families if f["label"] == "Unfiled"), None)
        finally:
            conn.close()
        return {"families": len(families), "unfiled": len(unfiled["member_code_ids"]) if unfiled else 0,
                "comments_addressed": n_addr, "feedback_used": bool(guidance)}
    return work


def compress_work(pid: str):
    """P8a: the actual codebook COLLAPSE. One LLM call per family (>= COMPRESS_MIN_FAMILY_CODES
    active codes) proposes within-family merge groups; Python validates; the whole batch of
    proposals REPLACES any still-pending proposals from an earlier compress run (accepted/
    dismissed history is untouched — persist_merge_proposals only clears 'pending' rows). Nothing
    is merged here — this only fills the review queue the researcher acts on."""
    def work(progress):
        conn = project_db(projects.project_db_path(pid))
        try:
            codes = store.codes_payload(conn)
            families = store.families_payload(conn)
            progress(stage="compress", message="scanning families for redundant codes")
            families_scanned = len(compress_batches(codes, families))
            proposals = propose_merges(codes, families, progress=progress)
            store.persist_merge_proposals(conn, proposals)
        finally:
            conn.close()
        return {"proposals": len(proposals), "families_scanned": families_scanned}
    return work
