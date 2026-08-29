"""Background job execution (Phase 3). A single-worker thread pool serializes the LLM-heavy runs
(coding already saturates the provider at CONCURRENCY=8 internally), while job rows in the registry
give pollable status independent of the HTTP request. Resumability comes from the same per-project
JSON checkpoint the CLI uses: re-POSTing a code/theme job resumes from where it stopped.
"""
from __future__ import annotations

import functools
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import llm, models, packs, projects, read, runner, store, synthesize, transcribe
from .coding import code_document, code_sections_panel
from .compress import compress_batches, propose_merges
from .consolidate import consolidate_codebook
from .db import new_run, project_db
from .ingest import ingest
from .reconcile import reconcile_project
from .themes import (theorize_panel_sequential, theorize_project_sequential,
                     transcript_block_from_sentences)

READ_SPANS = ("doc", "halves", "groups", "sections")
COVERAGE_GATE_SHARE = 0.45  # first-3-deciles share above this re-runs the doc at span='sections'

_EXECUTOR = ThreadPoolExecutor(max_workers=1)  # serialize LLM-heavy jobs


# ---- researcher-selectable model resolution (P10.1c) ----------------------------------------
# Precedence: explicit job param > the project's own model_id default > the server env default
# (no override at all — llm.use_model(None) is a no-op, today's behavior unchanged).

def resolve_job_model(pid: str, model_id: str | None) -> dict | None:
    if not model_id:
        model_id = (projects.get_project(pid) or {}).get("model_id")
    return models.resolve(model_id) if model_id else None


def with_model(build):
    """Decorator for a `*_work(pid, ..., model_id=None)` builder: resolves the model ONCE here
    (job-build time, before the job ever runs) and wraps the returned work(progress) so the whole
    job body runs inside llm.use_model(entry) — entered FROM CODE THAT ALREADY RUNS ON THE
    EXECUTOR'S WORKER THREAD (work() only executes once _run() invokes it there), which is why no
    contextvars.copy_context() dance is needed: two sequential jobs on the single worker thread
    each set/reset their own ContextVar value and never see each other's (see llm.use_model).
    The wrapped callable carries `.model_id` (the resolved registry id, or None) so an endpoint
    can stash what actually ran into the job row's params."""
    @functools.wraps(build)
    def outer(pid, *args, model_id=None, **kwargs):
        entry = resolve_job_model(pid, model_id)
        inner = build(pid, *args, **kwargs)

        def work(progress):
            with llm.use_model(entry):
                return inner(progress)
        work.model_id = entry["id"] if entry else None
        return work
    return outer


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

@with_model
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


@with_model
def transcribe_work(pid: str, filename: str, auto_ingest: bool = True):
    """P10.1b — the audio path (data-session-spec.md §12): ASR -> role mapping -> canonical
    render -> (default) the SAME ingest path ingest_work uses, so an audio upload ends as a
    normal ingested document with zero ingest changes. `filename` names the audio file already
    sitting in this project's uploads dir (the /audio upload endpoint wrote it there) — this job
    saves nothing of the audio itself, only what it derives: `<stem>.txt` (the canonical
    transcript) and `<stem>.asr.json` (the segments+roles sidecar, timestamps preserved).

    auto_ingest=False stops after the sidecar write so the researcher can review
    (GET .../transcript) and optionally redraft (POST .../redraft + .../redraft/apply) BEFORE
    ingest freezes sentence offsets — see api.py's two-step flow and its 409 guard. Once ingested,
    `redraft_available` flips to False for real (not just as a documentation note) — a redraft
    after ingest would invalidate offsets the rest of the pipeline treats as load-bearing."""
    def work(progress):
        stem = Path(filename).stem
        audio_path = projects.uploads_dir(pid) / filename
        progress(stage="transcribe", message=f"transcribing {filename}")
        resp = transcribe.transcribe_audio(audio_path)
        segments = resp.get("segments", []) or []
        progress(stage="roles", message="mapping speaker roles")
        roles = transcribe.map_roles(segments)
        txt, sidecar = transcribe.render_transcript(segments, roles)
        txt_path = projects.uploads_dir(pid) / f"{stem}.txt"
        txt_path.write_text(txt, encoding="utf-8")
        (projects.uploads_dir(pid) / f"{stem}.asr.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
        usage = resp.get("usage") or {}
        result = {
            "stem": stem,
            "duration_seconds": usage.get("prompt_audio_seconds", 0),
            "n_segments": len(segments),
            "n_speakers": len({s.get("speaker_id") for s in segments}),
            "roles": roles,
            "usage": usage,                 # audio cost telemetry (ledger already has "asr")
            "redraft_available": not auto_ingest,
        }
        if auto_ingest:
            progress(stage="ingest", message=f"ingesting {txt_path.name}")
            result["ingest"] = ingest_work(pid, txt_path)(progress)  # kind defaults to "transcript"
        return result
    return work


@with_model
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


@with_model
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


def _delete_run_codes(conn, run_id: str, doc_id: str) -> None:
    """Undo one READ run's newly-minted codes for a doc before the coverage-gate re-run.
    ponytail: does not retract sids a `reuses` union added to an EXISTING code during the
    discarded attempt — those stayed grounded citations, just made under a coarser span, and
    are cheap to leave be. Upgrade path: track per-run added-sid deltas if double-counted reuse
    evidence ever proves to matter in practice."""
    conn.execute("DELETE FROM code WHERE origin_doc_id=? AND run_id=?", (doc_id, run_id))
    conn.commit()


def _read_one_doc(conn, run_id: str, doc_id: str, span: str, research_question: str | None,
                  progress) -> dict:
    """READ one document at `span`, persist, then the coverage gate: if the first 3 deciles of
    this doc's citations hold more than COVERAGE_GATE_SHARE of the total, the lost-in-the-middle
    guard fires — the just-persisted (newly-minted) codes for this doc+run are rolled back and
    the doc is re-read ONCE at span='sections' (the fine fallback), replacing the front-loaded
    output. Returns the per-doc digest for the job result."""
    codes, reuses, declines, drop = read.read_document(conn, doc_id, span, research_question)
    digest = read.persist_read(conn, run_id, doc_id, codes, reuses, declines)
    share = read.first30_share(read.citation_deciles(conn, doc_id))
    fallback = False
    if share > COVERAGE_GATE_SHARE and span != "sections":
        progress(stage="coverage-gate", doc_id=doc_id,
                 message=f"front-loaded citations ({share:.0%}) — re-reading at span=sections")
        _delete_run_codes(conn, run_id, doc_id)
        codes, reuses, declines, drop = read.read_document(
            conn, doc_id, "sections", research_question)
        digest = read.persist_read(conn, run_id, doc_id, codes, reuses, declines)
        share = read.first30_share(read.citation_deciles(conn, doc_id))
        span, fallback = "sections", True
    digest.update({"span_used": span, "first30_share": round(share, 3),
                  "coverage_fallback": fallback, "dropped": drop})
    return digest


@with_model
def read_work(pid: str, span: str | None = None):
    """READ each not-yet-read document (own checkpoint kind "read" — same skip-what's-done
    discipline as code_work, a separate file so it never collides with code_work's checkpoints)
    at the configured span: the `span` argument wins, then MASSHINE_READ_SPAN, then "doc". The
    sectioned coder+critic/panel path (code_work) is untouched — this is a parallel path, not a
    replacement."""
    span = span or os.environ.get("MASSHINE_READ_SPAN", "doc")
    if span not in READ_SPANS:
        raise ValueError(f"span must be one of {READ_SPANS}, got {span!r}")

    def work(progress):
        cp = projects.checkpoint_path(pid, "read")
        state = runner.load_checkpoint(cp)
        conn = project_db(projects.project_db_path(pid))
        try:
            run = new_run(conn, "read")
            proj = projects.get_project(pid) or {}
            # P10.2: the active focus_version wins over the registry's cached mirror — the
            # registry column stays in sync (api.py's /focus endpoints keep it that way) but a
            # project db opened from an older backup, or read before any focus was ever minted,
            # falls back to it untouched (contract §5: "READ receives the active focus").
            focus = store.active_focus(conn)
            if focus and (focus.get("text") or "").strip():
                research_question = focus["text"].strip()
            else:
                research_question = (proj.get("research_question") or "").strip() or None
            rows = conn.execute(
                "SELECT id, filename FROM document ORDER BY created_at, id").fetchall()
            order = [r[0] for r in rows]
            names = dict(rows)
            done = state.setdefault("docs", {})
            total = len(order)
            results: dict[str, dict] = {}
            for idx, doc_id in enumerate(order, 1):
                if doc_id in done:
                    continue
                progress(stage="read", doc_id=doc_id, done=idx - 1, total=total,
                         message=f"reading {names[doc_id]}")
                digest = _read_one_doc(conn, run, doc_id, span, research_question, progress)
                results[doc_id] = digest
                done[doc_id] = digest
                conn.execute("UPDATE document SET status='read' WHERE id=?", (doc_id,))
                conn.commit()
                runner.save_checkpoint(cp, state)
            if results:
                if conn.execute("SELECT 1 FROM code_family LIMIT 1").fetchone():
                    store.set_families_stale(conn, True)
                for mode in ("standard", "panel"):
                    n_themes = conn.execute(
                        "SELECT COUNT(*) FROM theme_v2 WHERE mode=?", (mode,)).fetchone()[0]
                    if n_themes:
                        store.set_themes_stale(conn, mode, True)
        finally:
            conn.close()
        return {"span": span, "docs": results}
    return work


@with_model
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
            # P10.2 findings live in theme_v2 under the SAME mode as this legacy walk, and
            # persist_themes replaces a mode wholesale — so running this after a synthesis would
            # silently delete the researcher's findings, their standing, and every reaction keyed
            # to them. Refuse instead: the two pipelines are alternatives, not a sequence.
            if conn.execute("SELECT 1 FROM step WHERE mode=? LIMIT 1", (mode,)).fetchone():
                raise RuntimeError(
                    f"'{mode}' already has synthesized findings — re-running the older theme walk "
                    "would replace them. Run Synthesize instead, or start a separate project.")
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


@with_model
def synthesize_work(pid: str, mode: str = "standard"):
    """SYNTHESIZE each not-yet-synthesized document (own checkpoint kind "synthesize" — a
    separate file from code_work/theme_work's `checkpoint_{mode}.json`, so it never collides with
    them), same per-doc skip-what's-done discipline as read_work. `mode` picks which finding
    id-space this project's SYNTHESIZE writes into (theme_v2's existing `mode` column — see
    synthesize.py's module docstring and store.py's "P10.2: findings" section for why findings
    ARE theme_v2 rows); it does not gate anything about which documents get synthesized — every
    document in the project is eligible, same as read_work."""
    def work(progress):
        cp = projects.checkpoint_path(pid, "synthesize")
        state = runner.load_checkpoint(cp)
        conn = project_db(projects.project_db_path(pid))
        try:
            rows = conn.execute(
                "SELECT id, filename FROM document ORDER BY created_at, id").fetchall()
            order = [r[0] for r in rows]
            names = dict(rows)
            done = state.setdefault("docs", {})
            total = len(order)
            results: dict[str, dict] = {}
            for idx, doc_id in enumerate(order, 1):
                if doc_id in done:
                    continue
                progress(stage="synthesize", doc_id=doc_id, done=idx - 1, total=total,
                         message=f"synthesizing {names[doc_id]}")
                data = synthesize.synthesize_document(conn, doc_id, mode)
                digest = synthesize.persist_synthesis(conn, mode, doc_id, data)
                results[doc_id] = digest
                done[doc_id] = digest
                runner.save_checkpoint(cp, state)
        finally:
            conn.close()
        return {"mode": mode, "docs": results}
    return work


@with_model
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


@with_model
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
