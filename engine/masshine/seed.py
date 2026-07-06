"""Dev seed: reconstruct a full project from an existing run cache (exports/{panel,project}_
2interview.json) with ZERO LLM calls, so the frontend can be built against real codes/friction/
themes without waiting on ~12-min live runs. Sentence char offsets are recovered by searching each
cached (verbatim) sentence back into its source transcript; themes replay from the cached theme
steps. Auto-detects standard vs panel from the cache shape.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import packs, projects, runner, store
from .config import ROOT
from .db import new_run, project_db
from .ingest import _line_offsets
from .reconcile import _write_codebook
from .themes import (theorize_panel_sequential, theorize_project_sequential,
                     transcript_block_from_sentences)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rebuild_doc(conn, run, doc_id, doc, source_dir: Path):
    """Insert document/section/sentence rows with offsets recovered from the source transcript."""
    raw = (source_dir / doc["name"]).read_text(encoding="utf-8", errors="replace")
    offs = _line_offsets(raw)
    n_lines = len(offs) - 1
    secs, bounds = [], {}
    for s in doc["sections"]:
        a = max(1, min(int(s["start_line"]), n_lines))
        b = max(a, min(int(s["end_line"]), n_lines))
        cs, ce = offs[a - 1], offs[b]
        secs.append({"id": s["id"], "doc_id": doc_id, "gist": s["gist"],
                     "start_line": a, "end_line": b, "char_start": cs, "char_end": ce})
        bounds[s["id"]] = (cs, ce)
    sents, cursor = [], {}
    for s in doc["sentences"]:
        sid, sec, text = s["id"], s["section_id"], s["text"]
        lo = cursor.get(sec, bounds.get(sec, (0, len(raw)))[0])
        hi = bounds.get(sec, (0, len(raw)))[1]
        idx = raw.find(text, lo, hi)
        if idx < 0:
            idx = raw.find(text, lo)
        if idx < 0:
            idx = raw.find(text)
        cs = idx if idx >= 0 else lo
        ce = cs + len(text)
        cursor[sec] = ce
        sents.append({"id": sid, "doc_id": doc_id, "section_id": sec,
                      "char_start": cs, "char_end": ce})
    conn.execute("DELETE FROM sentence WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM section WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM document WHERE id=?", (doc_id,))
    conn.execute(
        "INSERT INTO document (id, run_id, path, text, text_hash, char_len, filename, status, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_id, run, doc["name"], raw, hashlib.sha256(raw.encode()).hexdigest()[:16],
         len(raw), doc["name"], "coded", _now()))
    conn.executemany(
        "INSERT INTO section (id, doc_id, gist, start_line, end_line, char_start, char_end) "
        "VALUES (:id,:doc_id,:gist,:start_line,:end_line,:char_start,:char_end)", secs)
    conn.executemany(
        "INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
        "VALUES (:id,:doc_id,:section_id,:char_start,:char_end)", sents)


def import_cache(cache_path: Path, name: str | None = None,
                 pack_id: str = "migration_oral_history",
                 source_dir: Path | None = None) -> str:
    """`source_dir` is where each doc's raw transcript (`doc["name"]`) is read from — defaults to
    the dev sample corpus; a bundled demo seed (see api._maybe_seed_demo) points this at
    engine/seed_data/ instead, so it never depends on the (gitignored) transcripts_sample/."""
    source_dir = source_dir or (ROOT.parent / "transcripts_sample")
    st = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    order = st["order"]
    sample_doc = st["docs"][order[0]]
    mode = "panel" if "panel" in sample_doc else "standard"
    proj = projects.create_project(name or f"Imported ({mode})", pack_id if mode == "panel" else None)
    pid = proj["id"]
    conn = project_db(projects.project_db_path(pid))
    try:
        run = new_run(conn, "import-cache")
        for doc_id in order:
            _rebuild_doc(conn, run, doc_id, st["docs"][doc_id], source_dir)
        conn.commit()

        if mode == "panel":
            coders = packs.panel_coders(pack_id)
            store.persist_panel_codes(conn, run, order, st["docs"], coders)
        else:
            cb = st["project_codebook"]
            _write_codebook(conn, run, [(cid, c) for cid, c in cb.items()])
            max_n = max((int(cid[1:]) for cid in cb), default=0)
            conn.execute("INSERT INTO meta (key, value) VALUES ('code_seq', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (max_n,))
            conn.commit()

        # copy the run cache in as the project checkpoint so theming replays (0 LLM calls)
        runner.save_checkpoint(projects.checkpoint_path(pid, mode), st)

        transcripts = {d: transcript_block_from_sentences(
            st["docs"][d]["sentences"], st["docs"][d]["sections"]) for d in order}
        valid = {d: {s["id"] for s in st["docs"][d]["sentences"]} for d in order}
        steps = st.get("theme_steps", {})
        if mode == "panel":
            panel_by_doc = {d: st["docs"][d]["panel"] for d in order}
            themes, _, _, snaps, fails = theorize_panel_sequential(
                order, panel_by_doc, transcripts, valid, raw_cache=steps)
        else:
            themes, _, snaps, fails = theorize_project_sequential(
                order, st["project_codebook"], transcripts, valid, raw_cache=steps)
        store.persist_themes(conn, mode, themes, snaps)
    finally:
        conn.close()
    print(f"seeded project {pid} ({mode}): {len(order)} docs, {len(themes)} themes"
          + (f", DROPPED {fails}" if fails else ""), flush=True)
    return pid
