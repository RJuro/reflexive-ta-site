"""Run orchestration shared by the CLI tools and (Phase 3) the API.

The resumable-checkpoint pattern is lifted verbatim from tools/run_{project,panel}_md.py — one JSON
file checkpoints coding (per doc), the reconcile, and each theme step, so a crash/timeout resumes at
the next incomplete step. Kept here once instead of copied per tool.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import llm
from .coding import code_document, code_sections_panel
from .config import CONCURRENCY, EXPORT_DIR, ROOT
from .db import export_json, init_db, new_run, resolve, resolve_ev
from .ingest import _slug, ingest
from .reconcile import reconcile_into, reconcile_project
from .themes import theorize_project


# ---- resumable checkpoint ------------------------------------------------------------------------

def load_checkpoint(path: Path, recode: bool = False, retheme: bool = False) -> dict:
    """bare = resume · retheme = keep coding, redo themes · recode = rebuild all."""
    if recode or not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if retheme:
        state.pop("theme_steps", None)
    return state


def save_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic rename: a crash mid-write can't corrupt the checkpoint


# ---- coding orchestration (fills MISSING work only, resumably) -----------------------------------

def ensure_coded_standard(state: dict, doc_paths: list[Path], cache_path: Path) -> dict:
    """Standard pipeline: per-document coding + the across-document reconcile, resumably. The
    checkpoint is written after each doc and after the reconcile."""
    docs = state.setdefault("docs", {})
    order = [_slug(p) for p in doc_paths]
    state["order"] = order
    missing = [p for p in doc_paths if _slug(p) not in docs]
    if missing:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        run = new_run(conn, "standard MD run")
        for path in missing:
            doc_id, secs, _ = ingest(conn, run, path)
            sents = [{"id": sid, "section_id": sec, "text": resolve(conn, doc_id, sid)}
                     for sid, sec in conn.execute(
                         "SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                         (doc_id,)).fetchall()]
            ds, dropped, nfail = code_document(conn, run, doc_id)
            if nfail:  # an incomplete document must NOT be cached as done — fail loudly, retry on resume
                raise RuntimeError(
                    f"{path.name}: {nfail} section(s) failed to code — document NOT cached; re-run "
                    "to retry (resume re-codes only the missing document).")
            docs[doc_id] = {
                "name": path.name,
                "sections": [{"id": s["id"], "gist": s["gist"], "start_line": s["start_line"],
                              "end_line": s["end_line"]} for s in secs],
                "sentences": sents, "codes": ds}
            state.pop("project_codebook", None)  # new codes → the reconcile is now stale
            save_checkpoint(cache_path, state)
            print(f"{path.name}: {len(secs)} sections, {len(sents)} sentences, {len(ds)} codes "
                  f"({dropped} ungrounded dropped) — checkpointed", flush=True)
    if not state.get("project_codebook"):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        run = new_run(conn, "reconcile")
        state["project_codebook"] = reconcile_project(conn, run, [docs[d]["codes"] for d in order])
        save_checkpoint(cache_path, state)
        print(f"reconciled: {len(state['project_codebook'])} codes — checkpointed", flush=True)
    return state


def ensure_coded_panel(state: dict, doc_paths: list[Path], coders: dict[str, str],
                       cache_path: Path) -> dict:
    """Panel pipeline: per-document panel coding (no cross-lens reconcile), resumably. The
    checkpoint is written after each doc."""
    docs = state.setdefault("docs", {})
    state["order"] = [_slug(p) for p in doc_paths]
    missing = [p for p in doc_paths if _slug(p) not in docs]
    if missing:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        run = new_run(conn, "standpoint-panel MD run")
        for path in missing:
            doc_id, secs, _ = ingest(conn, run, path)
            sents = [{"id": sid, "section_id": sec, "text": resolve(conn, doc_id, sid)}
                     for sid, sec in conn.execute(
                         "SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                         (doc_id,)).fetchall()]
            panel, nfail = code_sections_panel(conn, doc_id, coders)
            if nfail:  # an incomplete document must NOT be cached as done — fail loudly, retry on resume
                raise RuntimeError(
                    f"{path.name}: {nfail} lens-section(s) failed to code — document NOT cached; "
                    "re-run to retry.")
            docs[doc_id] = {
                "name": path.name,
                "sections": [{"id": s["id"], "gist": s["gist"], "start_line": s["start_line"],
                              "end_line": s["end_line"]} for s in secs],
                "sentences": sents, "panel": panel}
            save_checkpoint(cache_path, state)
            print(f"{path.name}: {len(secs)} sections, {len(sents)} sentences; "
                  + ", ".join(f"{co} {len(panel[co])}" for co in coders) + " codes — checkpointed",
                  flush=True)
    return state


# ---- single-document add + full-pipeline demo (moved from the monolith) --------------------------

def add_document(conn: sqlite3.Connection, run_id: str, path: Path) -> tuple[str, dict, list[dict], int]:
    """Add ONE document to an existing project: code only this doc (parallel, blind, per-doc
    reconcile), fold its codes into the project codebook with stable ids, re-derive themes.
    Existing documents are NOT re-coded. Returns (doc_id, codebook, themes, n_dropped)."""
    doc_id, _, _ = ingest(conn, run_id, path)
    new_codes, dropped, _ = code_document(conn, run_id, doc_id)
    codebook = reconcile_into(conn, run_id, new_codes)
    themes = theorize_project(conn, run_id)  # cheap; theme code-refs now point at stable ids
    export_json(conn, doc_id)
    return doc_id, codebook, themes, dropped


def demo(doc_names: list[str] | None = None) -> None:
    """Project demo: code transcripts (parallel, blind) → per-doc reconcile → cross-doc
    reconcile → one shared codebook → one theme pass → export (sections + codebook + themes).
    Reports wall-clock since latency is the concern."""
    doc_names = doc_names or ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
    conn = sqlite3.connect(":memory:")  # all DB access stays on the main thread
    init_db(conn)
    llm.reset_usage()
    run_id = new_run(conn, note="project: parallel coding + reconcile")
    t0 = time.perf_counter()
    n_dropped = 0
    doc_codebooks: list[list[dict]] = []
    doc_ids: list[str] = []

    for name in doc_names:
        sample = ROOT.parent / "transcripts_sample" / name
        raw = sample.read_text(encoding="utf-8", errors="replace")
        doc_id, sections, sents = ingest(conn, run_id, sample)
        for s in sents:  # round-trip: indexed sentence resolves to exact source text
            assert 0 <= s["char_start"] < s["char_end"] <= len(raw), s
        ds, dc, _ = code_document(conn, run_id, doc_id)
        doc_ids.append(doc_id)
        doc_codebooks.append(ds)
        n_dropped += dc
        print(f"{name}: {len(sections)} sections, {len(sents)} sentences → "
              f"{len(ds)} codes (after per-doc reconcile); {dc} dropped")

    # cross-document reconcile → project codebook, then one theme pass over it
    codebook = reconcile_project(conn, run_id, doc_codebooks)
    for c in codebook.values():  # grounding gate over the whole codebook
        for ev in c["evidence"]:
            assert resolve_ev(conn, ev), c
    themes = theorize_project(conn, run_id)
    for doc_id in doc_ids:  # export AFTER codes + themes exist → complete artifact
        export_json(conn, doc_id)
    elapsed = time.perf_counter() - t0
    sem = sum(1 for c in codebook.values() if c["code_type"] == "semantic")
    lat = sum(1 for c in codebook.values() if c["code_type"] == "latent")
    cross = [c for c in codebook.values()
             if len({ev.split("#", 1)[0] for ev in c["evidence"]}) > 1]
    print(f"\nPROJECT codebook: {len(codebook)} codes ({sem} semantic, {lat} latent); "
          f"{len(cross)} span >1 document; {n_dropped} ungrounded dropped")
    for c in cross[:5]:
        docs = sorted({ev.split("#", 1)[0] for ev in c["evidence"]})
        print(f"    [{c['code_type']}] {c['label']}  ← {len(c['evidence'])} excerpts in {docs}")

    print(f"\n{len(themes)} candidate themes (claims, with supporting/contradicting codes):")
    for t in themes:
        print(f"    [{t['id']}] {t['central_concept'][:100]}"
              f"  ({len(t['supporting_code_ids'])} support, "
              f"{len(t['contradicting_code_ids'])} tension)")

    u = llm.usage()
    print(f"\nledger: {u['calls']} LLM calls, "
          f"{u['prompt_tokens']:,} prompt + {u['completion_tokens']:,} completion tokens, "
          f"{elapsed:.0f}s wall-clock (concurrency {CONCURRENCY})")
