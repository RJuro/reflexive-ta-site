#!/usr/bin/env python3
"""Run the STANDPOINT PANEL over two interviews and write plain-Markdown artifacts for every stage —
the panel counterpart to run_project_md.py (which runs the standard single coder). Same two
interviews, same barebones MD shape, but THREE coders read each section blind and in parallel:

  standard          — the method's neutral reflexive-TA coder (packs-agnostic engine default)
  critical          — critical theory / political economy of migration standpoint
  phenomenological  — phenomenological / memory standpoint

The panel codes are NOT reconciled across lenses (on purpose): keeping each lens distinct is what
lets the friction and the theme provenance show convergence vs divergence. Files:

  0_README.md                 index + run summary (what this version is, the 3 coders)
  1_<doc>_sections.md         LLM structure pass (per doc)
  2_<doc>_sentences.md        spaCy sentence index (per doc)
  3_<doc>_codes_<coder>.md    each coder's codes for that doc (3 coders x 2 docs = 6 files)
  4_<doc>_friction.md         sentence-anchored divergence: interpretive + attentional (per doc)
  5_themes.md                 candidate themes across BOTH interviews, each tagged with which
                              paradigms support it (convergent vs paradigm-unique)

A self-contained cache (exports/panel_2interview.json) holds sections + sentence text + panel codes,
so the Markdown can be regenerated (and the theme pass re-run) WITHOUT re-coding.

The pipeline internals live in the `masshine` package (runner + render_md + packs); this script just
wires the two sample interviews and the migration pack's lenses to them.

Usage:  .venv/bin/python ../tools/run_panel_md.py [--recode] [--retheme]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402
from masshine import packs, render_md as R  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
DOC_PATHS = [m.ROOT.parent / "transcripts_sample" / n for n in DOCS]
OUT = m.ROOT / "exports" / "md_panel"
CACHE = m.EXPORT_DIR / "panel_2interview.json"
RECODE = "--recode" in sys.argv      # rebuild everything (after a coder/standpoint prompt change)
RETHEME = "--retheme" in sys.argv    # keep coding, redo all theme steps (after a theorist change)

PACK_ID = "migration_oral_history"
CODERS = packs.panel_coders(PACK_ID)          # standard + the pack's standpoint lenses
CODER_BLURB = packs.coder_blurbs(PACK_ID)


def w(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", name, flush=True)


def main():
    t0 = time.perf_counter()
    state = m.ensure_coded_panel(m.load_checkpoint(CACHE, RECODE, RETHEME), DOC_PATHS, CODERS, CACHE)
    docs, order = state["docs"], state["order"]

    # per-document stages
    for doc_id in order:
        doc = docs[doc_id]
        w(f"1_{doc_id}_sections.md", R.pan_sections_md(doc))
        w(f"2_{doc_id}_sentences.md", R.pan_sentences_md(doc))
        for coder in CODERS:
            w(f"3_{doc_id}_codes_{coder}.md", R.pan_codes_md(doc, coder, CODER_BLURB[coder]))
        textmap = {f"{doc_id}#{s['id']}": s["text"] for s in doc["sentences"]}
        fr = m.friction(doc["panel"])
        w(f"4_{doc_id}_friction.md", R.pan_friction_md(doc, fr, textmap))

    # themes across BOTH interviews, built sequentially (transcript-grounded), resumable, w/ provenance
    doc_order = order
    transcripts = {d: m.transcript_block_from_sentences(docs[d]["sentences"], docs[d]["sections"])
                   for d in doc_order}
    valid_sents_map = {d: {s["id"] for s in docs[d]["sentences"]} for d in doc_order}
    panel_by_doc = {d: docs[d]["panel"] for d in doc_order}
    theme_steps = state.setdefault("theme_steps", {})

    def save_raw(doc_id, raw):  # checkpoint each completed theme step as it succeeds
        theme_steps[doc_id] = raw
        m.save_checkpoint(CACHE, state)

    themes, codebook, origin, snaps, fails = m.theorize_panel_sequential(
        doc_order, panel_by_doc, transcripts, valid_sents_map,
        raw_cache=theme_steps, save_raw=save_raw)
    textmap = {f"{d}#{s['id']}": s["text"] for d in doc_order for s in docs[d]["sentences"]}
    w("5_themes.md", R.pan_themes_md(themes, codebook, origin, snaps, textmap, fails))
    if fails:
        print(f"  [ERROR] theme steps dropped: {', '.join(fails)} — INCOMPLETE; re-run to resume",
              flush=True)

    convergent = sum(1 for t in themes if len(t.get("paradigm_provenance", {})) >= 2)
    totals = {co: sum(len(docs[d]["panel"][co]) for d in doc_order) for co in CODERS}
    readme = (
        "# MASSHINE — 2-interview run, STANDPOINT-PANEL version (Markdown record)\n\n"
        f"The same two interviews as the standard version, coded by a **panel of three lenses** "
        "reading each section blind and in parallel:\n\n"
        + "".join(f"- **{co}** — {CODER_BLURB[co]} ({totals[co]} codes)\n" for co in CODERS)
        + "\nThe lenses are **not merged** — keeping them distinct is what makes the disagreement "
        "legible. Read the files in order:\n\n"
        "1. `1_*_sections.md` — the LLM structure pass (per interview).\n"
        "2. `2_*_sentences.md` — the spaCy sentence index every code points at.\n"
        "3. `3_*_codes_<coder>.md` — each lens's codes for that interview, side by side.\n"
        "4. `4_*_friction.md` — sentence-anchored divergence: interpretive (same sentence, "
        "different readings) and attentional (a lens coded what the standard passed over).\n"
        "5. `5_themes.md` — candidate themes, built one interview at a time (the theorist reads "
        "each transcript + its lens codes against the themes so far): one claim each, line-anchored, "
        "with coverage/scope, a falsification test, and paradigm provenance (convergent vs "
        "single-lens).\n"
        + (f"\n⚠️ INCOMPLETE: the theme step failed for {', '.join(fails)} — re-run "
           "`run_panel_md.py` to resume (coding + finished theme steps are cached).\n" if fails else "")
        + f"\nRun: {sum(totals.values())} codes across the three lenses, {len(themes)} candidate "
        f"themes ({convergent} convergent across 2+ lenses); "
        f"{m.llm.usage()['calls']} model calls this run (cached steps skipped).\n\n"
        "This is the panel variation. The **standard single-coder** version of the same two "
        "interviews is in `../md/`.\n"
    )
    w("0_README.md", readme)
    print(f"done in {time.perf_counter() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
