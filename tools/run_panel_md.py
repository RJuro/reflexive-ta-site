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

Usage:  .venv/bin/python ../tools/run_panel_md.py [--recode]
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
OUT = m.ROOT / "exports" / "md_panel"
CACHE = m.EXPORT_DIR / "panel_2interview.json"
RECODE = "--recode" in sys.argv      # rebuild everything (after a coder/standpoint prompt change)
RETHEME = "--retheme" in sys.argv    # keep coding, redo all theme steps (after a theorist change)

PACK = m.ROOT.parent / "packs" / "migration_oral_history" / "standpoints"
CODERS = {
    "standard":         (m.PROMPTS / "coder.prompt").read_text(encoding="utf-8"),
    "critical":         (PACK / "critical_political_economy.prompt").read_text(encoding="utf-8"),
    "phenomenological": (PACK / "phenomenological_memory.prompt").read_text(encoding="utf-8"),
}
CODER_BLURB = {
    "standard":         "the method's neutral reflexive-TA coder (engine default)",
    "critical":         "critical theory / political economy of migration standpoint",
    "phenomenological": "phenomenological / memory standpoint",
}


def w(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", name, flush=True)


# ---- resumable cache: ONE file checkpoints panel coding (per doc) and each theme step ------------
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
    """Fill any MISSING per-document panel coding — resumably. Already-coded docs load from the
    checkpoint; only the rest run the three lenses. The checkpoint is written after each doc, so a
    crash resumes at the next interview (the panel has no cross-document reconcile step)."""
    docs = state.setdefault("docs", {})
    state["order"] = [m._slug(Path(n)) for n in DOCS]
    missing = [n for n in DOCS if m._slug(Path(n)) not in docs]
    if missing:
        conn = sqlite3.connect(":memory:")
        m.init_db(conn)
        run = m.new_run(conn, "standpoint-panel MD run")
        for name in missing:
            path = m.ROOT.parent / "transcripts_sample" / name
            doc_id, secs, _ = m.ingest(conn, run, path)
            sents = [{"id": sid, "section_id": sec, "text": m.resolve(conn, doc_id, sid)}
                     for sid, sec in conn.execute(
                         "SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                         (doc_id,)).fetchall()]
            panel, nfail = m.code_sections_panel(conn, doc_id, CODERS)
            if nfail:  # an incomplete document must NOT be cached as done — fail loudly, retry on resume
                raise RuntimeError(
                    f"{name}: {nfail} lens-section(s) failed to code — document NOT cached; "
                    "re-run run_panel_md.py to retry.")
            # (No within-lens reconcile: a test run showed the codes inside a lens are mostly
            # distinct, so dedup cut only ~7% while adding 6 reconcile calls per run — not worth it.
            # The sequential theorist already keeps per-theme counts reasonable.)
            docs[doc_id] = {
                "name": name,
                "sections": [{"id": s["id"], "gist": s["gist"], "start_line": s["start_line"],
                              "end_line": s["end_line"]} for s in secs],
                "sentences": sents, "panel": panel}
            save_cache(state)
            print(f"{name}: {len(secs)} sections, {len(sents)} sentences; "
                  + ", ".join(f"{co} {len(panel[co])}" for co in CODERS) + " codes — checkpointed",
                  flush=True)
    return state


# ---- Markdown renderers (read only from state — no conn) -------------------------------------

def collapse(t):
    return " ".join(t.split())


def sections_md(doc):
    out = [f"# {doc['name']} — sections", "",
           f"{len(doc['sections'])} sections (LLM structure pass)", ""]
    for s in doc["sections"]:
        out.append(f"- **{s['id']}** (lines {s['start_line']}–{s['end_line']}): {s['gist']}")
    return "\n".join(out) + "\n"


def sentences_md(doc):
    out = [f"# {doc['name']} — sentence index", "",
           f"{len(doc['sentences'])} sentences (spaCy). Every code below cites these IDs; "
           "text is resolved from the index, never re-typed.", ""]
    cur = None
    for s in doc["sentences"]:
        if s["section_id"] != cur:
            out += ["", f"## {s['section_id']}", ""]
            cur = s["section_id"]
        out.append(f"- `{s['id']}` {collapse(s['text'])}")
    return "\n".join(out) + "\n"


def codes_md(doc, coder):
    codes = doc["panel"][coder]
    out = [f"# {doc['name']} — {coder} coder",
           f"*{CODER_BLURB[coder]}*", "",
           f"{len(codes)} codes (blind pass, before any reconcile)", ""]
    for c in codes:
        cites = ", ".join(e.split("#", 1)[1] for e in c["evidence"])
        out.append(f"### ({c['code_type']}) {c['label']}")
        out.append(c["definition"])
        out.append("")
        out.append(f"*evidence:* `{cites}`")
        if c.get("model_rationale"):
            out.append(f"*rationale:* {c['model_rationale']}")
        out.append("")
    return "\n".join(out) + "\n"


def friction_md(doc, fr, textmap):
    coders = fr["coders"]

    def labels(cs):
        return "; ".join(f"({c['code_type'][:3]}) {c['label']}" for c in cs)

    out = [f"# {doc['name']} — standpoint friction", "",
           "Where the three lenses diverge, sentence by sentence. **Interpretive friction** = the "
           "same sentence read by 2+ coders (compare the readings). **Attentional friction** = a "
           "lens coded a sentence the standard coder passed over (the lens found something codable "
           "there).", "",
           "**Coverage** (sentences each coder touched): "
           + ", ".join(f"{co} {fr['coverage'][co]}" for co in coders), ""]

    inter = sorted(fr["interpretive"].items(), key=lambda kv: -len(kv[1]))
    out += [f"## Interpretive friction — {len(inter)} sentences read by 2+ coders", ""]
    for ev, cm in inter:
        out.append(f"**`{ev.split('#', 1)[1]}`** — {collapse(textmap[ev])}")
        for co in coders:
            if co in cm:
                out.append(f"- *{co}:* {labels(cm[co])}")
        out.append("")

    out += ["## Attentional friction — what a standpoint coded that the standard did not", ""]
    for lens in coders:
        if lens == "standard":
            continue
        solo = [(ev, cm[lens]) for ev, cm in fr["by_sent"].items()
                if lens in cm and "standard" not in cm]
        out += [f"### {lens} — {len(solo)} sentences the standard coder ignored", ""]
        for ev, cs in solo:
            out.append(f"- **`{ev.split('#', 1)[1]}`** {labels(cs)}")
            out.append(f"  > {collapse(textmap[ev])}")
        out.append("")
    return "\n".join(out) + "\n"


def themes_md(themes, codebook, origin, snaps, textmap, failures):
    def lab(cid):
        return codebook.get(cid, {}).get("label", cid)

    def prov(p):
        return ", ".join(f"{k} {v}" for k, v in sorted(p.items(), key=lambda kv: -kv[1])) or "—"

    def anchor(q):
        return collapse(textmap.get(q, q))

    convergent = sum(1 for t in themes if len(t.get("paradigm_provenance", {})) >= 2)
    out = ["# Candidate themes — with paradigm provenance", ""]
    if failures:
        out += [f"> ⚠️ **INCOMPLETE RUN** — the theme step failed for {', '.join(failures)} "
                "(it was NOT integrated). The themes below do not reflect those interviews. "
                "Re-run `run_panel_md.py` (cached coding) to retry.", ""]
    out += [
           f"{len(themes)} themes, built one interview at a time across the three lenses. Each "
           "interview's transcript and codes were read against the themes so far, so a theme's "
           "**coverage** rises only when a later interview supports it, and its **scope** never "
           "outruns coverage. Codes stay paradigm-tagged (not merged), so each theme records how "
           "many supporting codes came from each lens — convergent (2+ lenses) vs single-lens.", "",
           f"**{convergent} convergent** (2+ lenses) · **{len(themes) - convergent} single-lens**.", ""]
    out += ["## How the themes emerged", ""]
    for doc_id, snap in snaps:
        out.append(f"**After {doc_id}** — {len(snap)} themes: "
                   + ("; ".join(f"{t['id']} ({t['coverage']}; {prov(t.get('paradigm_provenance', {}))})"
                                for t in snap) or "—"))
    out += ["", "## Final themes", ""]
    for t in themes:
        p = t.get("paradigm_provenance", {})
        kind = "convergent" if len(p) >= 2 else "single-lens"
        out.append(f"### {t['id']} — {t['central_concept']}")
        out.append("")
        out.append(f"*{kind} · {t['claim_scope']} · coverage {t['coverage']}* · "
                   f"**provenance:** {prov(p)}")
        out.append("")
        if t.get("subthemes"):
            out.append("**Sub-themes:**")
            for st in t["subthemes"]:
                out.append(f"- {st['claim']} — _{'; '.join(lab(c) for c in st['supporting_code_ids'])}_")
            out.append("")
        out.append("**Supporting:** "
                   + ("; ".join(f"`{c}` {lab(c)} ({origin.get(c, '?')})"
                                for c in t["supporting_code_ids"]) or "—"))
        out.append("")
        if t.get("key_evidence_sentence_ids"):
            out.append("**Anchored in:**")
            for q in t["key_evidence_sentence_ids"]:
                out.append(f"- `{q}` “{anchor(q)}”")
            out.append("")
        out.append("**Tensions (for a human to check):** "
                   + ("; ".join(f"`{c}` {lab(c)} ({origin.get(c, '?')})"
                                for c in t.get("tensions", [])) or "none recorded"))
        out.append("")
        if t.get("falsified_if"):
            out.append(f"**Falsified if:** {t['falsified_if']}")
            out.append("")
    return "\n".join(out) + "\n"


# ---- main ------------------------------------------------------------------------------------

def main():
    t0 = time.perf_counter()
    state = ensure_coded(load_cache())
    docs, order = state["docs"], state["order"]

    # per-document stages
    for doc_id in order:
        doc = docs[doc_id]
        w(f"1_{doc_id}_sections.md", sections_md(doc))
        w(f"2_{doc_id}_sentences.md", sentences_md(doc))
        for coder in CODERS:
            w(f"3_{doc_id}_codes_{coder}.md", codes_md(doc, coder))
        textmap = {f"{doc_id}#{s['id']}": s["text"] for s in doc["sentences"]}
        fr = m.friction(doc["panel"])
        w(f"4_{doc_id}_friction.md", friction_md(doc, fr, textmap))

    # themes across BOTH interviews, built sequentially (transcript-grounded), resumable, w/ provenance
    doc_order = order
    transcripts = {d: m.transcript_block_from_sentences(docs[d]["sentences"], docs[d]["sections"])
                   for d in doc_order}
    valid_sents_map = {d: {s["id"] for s in docs[d]["sentences"]} for d in doc_order}
    panel_by_doc = {d: docs[d]["panel"] for d in doc_order}
    theme_steps = state.setdefault("theme_steps", {})

    def save_raw(doc_id, raw):  # checkpoint each completed theme step as it succeeds
        theme_steps[doc_id] = raw
        save_cache(state)

    themes, codebook, origin, snaps, fails = m.theorize_panel_sequential(
        doc_order, panel_by_doc, transcripts, valid_sents_map,
        raw_cache=theme_steps, save_raw=save_raw)
    textmap = {f"{d}#{s['id']}": s["text"] for d in doc_order for s in docs[d]["sentences"]}
    w("5_themes.md", themes_md(themes, codebook, origin, snaps, textmap, fails))
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
