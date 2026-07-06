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

The pipeline internals live in the `masshine` package (runner + render_md); this script just wires
the two sample interviews to them.

Usage:  .venv/bin/python ../tools/run_project_md.py [--recode] [--retheme]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402
from masshine import render_md as R  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
DOC_PATHS = [m.ROOT.parent / "transcripts_sample" / n for n in DOCS]
OUT = m.ROOT / "exports" / "md"
CACHE = m.EXPORT_DIR / "project_2interview.json"
RECODE = "--recode" in sys.argv      # rebuild everything (after a coder.prompt change)
RETHEME = "--retheme" in sys.argv    # keep coding, redo all theme steps (after a theorist change)


def w(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", name, flush=True)


def main():
    t0 = time.perf_counter()
    state = m.ensure_coded_standard(m.load_checkpoint(CACHE, RECODE, RETHEME), DOC_PATHS, CACHE)
    docs, order, project = state["docs"], state["order"], state["project_codebook"]

    for doc_id in order:
        w(f"1_{doc_id}_sections.md", R.std_sections_md(docs[doc_id]))
        w(f"2_{doc_id}_sentences.md", R.std_sentences_md(docs[doc_id]))
        w(f"3_{doc_id}_codes.md", R.std_codes_md(docs[doc_id]))
    w("4_codebook.md", R.std_codebook_md(project))

    transcripts = {d: m.transcript_block_from_sentences(docs[d]["sentences"], docs[d]["sections"])
                   for d in order}
    valid_sents = {d: {s["id"] for s in docs[d]["sentences"]} for d in order}
    theme_steps = state.setdefault("theme_steps", {})

    def save_raw(doc_id, raw):  # checkpoint each completed theme step as it succeeds
        theme_steps[doc_id] = raw
        m.save_checkpoint(CACHE, state)

    # theme over the PROJECT codebook so theme supporting ids == 4_codebook.md ids (auditable)
    themes, tcodebook, snaps, fails = m.theorize_project_sequential(
        order, project, transcripts, valid_sents, raw_cache=theme_steps, save_raw=save_raw)
    textmap = {f"{d}#{s['id']}": s["text"] for d in order for s in docs[d]["sentences"]}
    w("5_themes.md", R.std_themes_md(themes, tcodebook, snaps, textmap, fails))
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
