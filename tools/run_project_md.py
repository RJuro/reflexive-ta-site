#!/usr/bin/env python3
"""Run the STANDARD pipeline on two interviews and write plain-Markdown artifacts for every stage —
a barebones, readable record of the run. Coding is cached (it's the expensive step), so the themes
can be regenerated without re-coding.

Stages, as files in engine/exports/md/:
  0_README.md            index + run summary
  1_<doc>_sections.md    LLM structure pass
  2_<doc>_sentences.md   spaCy sentence index (each line resolves to verbatim text)
  3_<doc>_codes.md       per-document codes (after the within-document reconcile)
  4_codebook.md          project codebook (after the across-document reconcile, stable IDs)
  5_themes.md            candidate themes — built one interview at a time (sequential, transcript-
                         grounded): one claim each, line-anchored, with coverage/scope + a
                         falsification test, and a "how the themes emerged" trace

A self-contained cache (exports/project_2interview.json) holds sections + sentence text + per-doc
codes + the reconciled project codebook, so the Markdown (and the theme pass) can be regenerated
WITHOUT re-coding.

Usage:  .venv/bin/python ../tools/run_project_md.py [--recode]
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
OUT = m.ROOT / "exports" / "md"
CACHE = m.EXPORT_DIR / "project_2interview.json"
RECODE = "--recode" in sys.argv      # rebuild everything (after a coder.prompt change)
RETHEME = "--retheme" in sys.argv    # keep coding, redo all theme steps (after a theorist change)


def w(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", name, flush=True)


def collapse(t):
    return " ".join(t.split())


# ---- resumable cache: ONE file checkpoints coding (per doc), the reconcile, and each theme step --
# Re-running just fills what's missing: a crash/timeout resumes at the next incomplete step.
# bare run = resume · --retheme = keep coding, redo themes · --recode = rebuild all.

def load_cache() -> dict:
    if RECODE or not CACHE.exists():
        return {}
    state = json.loads(CACHE.read_text(encoding="utf-8"))
    if RETHEME:
        state.pop("theme_steps", None)
    return state


def save_cache(state) -> None:
    m.EXPORT_DIR.mkdir(exist_ok=True)
    tmp = CACHE.with_name(CACHE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)  # atomic rename: a crash mid-write can't corrupt the checkpoint


def ensure_coded(state) -> dict:
    """Fill any MISSING per-document coding, then the reconcile — resumably. Already-coded docs load
    from the checkpoint; only the rest hit the model. The checkpoint is written after each doc and
    after the reconcile, so a crash resumes at the next incomplete step."""
    docs = state.setdefault("docs", {})
    order = [m._slug(Path(n)) for n in DOCS]
    state["order"] = order
    missing = [n for n in DOCS if m._slug(Path(n)) not in docs]
    if missing:
        conn = sqlite3.connect(":memory:")
        m.init_db(conn)
        run = m.new_run(conn, "standard MD run")
        for name in missing:
            path = m.ROOT.parent / "transcripts_sample" / name
            doc_id, secs, _ = m.ingest(conn, run, path)
            sents = [{"id": sid, "section_id": sec, "text": m.resolve(conn, doc_id, sid)}
                     for sid, sec in conn.execute(
                         "SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                         (doc_id,)).fetchall()]
            ds, dropped, nfail = m.code_document(conn, run, doc_id)
            if nfail:  # an incomplete document must NOT be cached as done — fail loudly, retry on resume
                raise RuntimeError(
                    f"{name}: {nfail} section(s) failed to code — document NOT cached; re-run "
                    "run_project_md.py to retry (resume re-codes only the missing document).")
            docs[doc_id] = {
                "name": name,
                "sections": [{"id": s["id"], "gist": s["gist"], "start_line": s["start_line"],
                              "end_line": s["end_line"]} for s in secs],
                "sentences": sents, "codes": ds}
            state.pop("project_codebook", None)  # new codes → the reconcile is now stale
            save_cache(state)
            print(f"{name}: {len(secs)} sections, {len(sents)} sentences, {len(ds)} codes "
                  f"({dropped} ungrounded dropped) — checkpointed", flush=True)
    if not state.get("project_codebook"):
        conn = sqlite3.connect(":memory:")
        m.init_db(conn)
        run = m.new_run(conn, "reconcile")
        state["project_codebook"] = m.reconcile_project(
            conn, run, [docs[d]["codes"] for d in order])
        save_cache(state)
        print(f"reconciled: {len(state['project_codebook'])} codes — checkpointed", flush=True)
    return state


# ---- Markdown renderers (read only from state — no conn) -------------------------------------

def sections_md(doc):
    out = [f"# {doc['name']} — sections", "",
           f"{len(doc['sections'])} sections (LLM structure pass)", ""]
    for s in doc["sections"]:
        out.append(f"- **{s['id']}** (lines {s['start_line']}–{s['end_line']}): {s['gist']}")
    return "\n".join(out) + "\n"


def sentences_md(doc):
    out = [f"# {doc['name']} — sentence index", "",
           f"{len(doc['sentences'])} sentences (spaCy). Every code cites these IDs; text is "
           "resolved from the index, never re-typed.", ""]
    cur = None
    for s in doc["sentences"]:
        if s["section_id"] != cur:
            out += ["", f"## {s['section_id']}", ""]
            cur = s["section_id"]
        out.append(f"- `{s['id']}` {collapse(s['text'])}")
    return "\n".join(out) + "\n"


def codes_md(doc):
    codes = doc["codes"]
    out = [f"# {doc['name']} — codes ({len(codes)})", "",
           f"{len(codes)} codes after the within-document reconcile", ""]
    for c in codes:
        out.append(f"### ({c['code_type']}) {c['label']}")
        out.append(c["definition"])
        out.append("")
        out.append(f"*evidence:* `{', '.join(c['evidence'])}`")
        if c.get("model_rationale"):
            out.append(f"*rationale:* {c['model_rationale']}")
        out.append("")
    return "\n".join(out) + "\n"


def codebook_md(codebook):
    out = ["# Project codebook (across both interviews)", "",
           f"{len(codebook)} codes after the across-document reconcile. IDs are stable.", ""]
    for cid, c in codebook.items():
        docs = sorted({e.split("#", 1)[0] for e in c["evidence"]})
        out.append(f"### {cid} · ({c['code_type']}) {c['label']}")
        out.append(c["definition"])
        out.append(f"*evidence:* {len(c['evidence'])} excerpt(s) across {', '.join(docs)}")
        out.append("")
    return "\n".join(out) + "\n"


def themes_md(themes, codebook, snaps, textmap, failures):
    def lab(cid):
        return codebook.get(cid, {}).get("label", cid)

    def anchor(q):
        return collapse(textmap.get(q, q))

    out = ["# Candidate themes", ""]
    if failures:
        out += [f"> ⚠️ **INCOMPLETE RUN** — the theme step failed for {', '.join(failures)} "
                "(it was NOT integrated). The themes below do not reflect those interviews. "
                "Re-run `run_project_md.py` (cached coding) to retry.", ""]
    out += [f"{len(themes)} themes, built one interview at a time: each interview's transcript and "
            "codes were read against the themes so far, so a theme's **coverage** rises only when a "
            "later interview actually supports it, and its **scope** never outruns its coverage. "
            "Each theme states one arguable claim, anchored to specific lines, with the test that "
            "would disconfirm it. Candidates for a human to refine.", ""]
    out += ["## How the themes emerged", ""]
    for doc_id, snap in snaps:
        out.append(f"**After {doc_id}** — {len(snap)} themes: "
                   + ("; ".join(f"{t['id']} ({t['coverage']})" for t in snap) or "—"))
    out += ["", "## Final themes", ""]
    for t in themes:
        out.append(f"### {t['id']} — {t['central_concept']}")
        out.append("")
        out.append(f"*{t['claim_scope']} · coverage {t['coverage']}*")
        out.append("")
        if t.get("subthemes"):
            out.append("**Sub-themes:**")
            for st in t["subthemes"]:
                out.append(f"- {st['claim']} — _{'; '.join(lab(c) for c in st['supporting_code_ids'])}_")
            out.append("")
        out.append("**Supporting:** "
                   + ("; ".join(f"`{c}` {lab(c)}" for c in t["supporting_code_ids"]) or "—"))
        out.append("")
        if t.get("key_evidence_sentence_ids"):
            out.append("**Anchored in:**")
            for q in t["key_evidence_sentence_ids"]:
                out.append(f"- `{q}` “{anchor(q)}”")  # keep the doc-qualified id (which interview)
            out.append("")
        out.append("**Tensions (for a human to check):** "
                   + ("; ".join(f"`{c}` {lab(c)}" for c in t.get("tensions", [])) or "none recorded"))
        out.append("")
        if t.get("falsified_if"):
            out.append(f"**Falsified if:** {t['falsified_if']}")
            out.append("")
    return "\n".join(out) + "\n"


# ---- main ------------------------------------------------------------------------------------

def main():
    t0 = time.perf_counter()
    state = ensure_coded(load_cache())
    docs, order, project = state["docs"], state["order"], state["project_codebook"]

    for doc_id in order:
        w(f"1_{doc_id}_sections.md", sections_md(docs[doc_id]))
        w(f"2_{doc_id}_sentences.md", sentences_md(docs[doc_id]))
        w(f"3_{doc_id}_codes.md", codes_md(docs[doc_id]))
    w("4_codebook.md", codebook_md(project))

    transcripts = {d: m.transcript_block_from_sentences(docs[d]["sentences"], docs[d]["sections"])
                   for d in order}
    valid_sents = {d: {s["id"] for s in docs[d]["sentences"]} for d in order}
    theme_steps = state.setdefault("theme_steps", {})

    def save_raw(doc_id, raw):  # checkpoint each completed theme step as it succeeds
        theme_steps[doc_id] = raw
        save_cache(state)

    # theme over the PROJECT codebook so theme supporting ids == 4_codebook.md ids (auditable)
    themes, tcodebook, snaps, fails = m.theorize_project_sequential(
        order, project, transcripts, valid_sents, raw_cache=theme_steps, save_raw=save_raw)
    textmap = {f"{d}#{s['id']}": s["text"] for d in order for s in docs[d]["sentences"]}
    w("5_themes.md", themes_md(themes, tcodebook, snaps, textmap, fails))
    if fails:
        print(f"  [ERROR] theme steps dropped: {', '.join(fails)} — INCOMPLETE; re-run to resume",
              flush=True)

    warn = (f"\n⚠️ INCOMPLETE: the theme step failed for {', '.join(fails)} — re-run "
            "`run_project_md.py` to resume (coding + finished theme steps are cached).\n"
            if fails else "")
    readme = (
        "# MASSHINE — 2-interview run (Markdown record)\n\n"
        f"A plain-text record of one pipeline run over **{len(DOCS)} interviews**: "
        f"{', '.join(DOCS)}.\n\n"
        "Read the files in order — they are the stages of the pipeline:\n\n"
        "1. `1_*_sections.md` — the LLM structure pass splits each transcript into sections.\n"
        "2. `2_*_sentences.md` — spaCy indexes sentences; every later code points at these IDs.\n"
        "3. `3_*_codes.md` — each document's codes, after the within-document reconcile.\n"
        "4. `4_codebook.md` — the project codebook, after the across-document reconcile (stable IDs).\n"
        "5. `5_themes.md` — candidate themes, built one interview at a time (the theorist reads "
        "each transcript + its codes against the themes so far): one claim each, line-anchored, "
        "with coverage/scope, a falsification test, and a 'how the themes emerged' trace.\n"
        + warn +
        f"\nRun: {len(project)} codes in the project codebook, {len(themes)} candidate themes; "
        f"{m.llm.usage()['calls']} model calls this run (cached steps skipped).\n\n"
        "This is the barebones view. The same run, with standpoint provenance and an interface, "
        "is on the project page (`index.html`) and the workbench mockup.\n"
    )
    w("0_README.md", readme)
    print(f"done in {time.perf_counter() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
