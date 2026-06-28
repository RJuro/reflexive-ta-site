#!/usr/bin/env python3
"""Run the pipeline on two interviews and write plain-Markdown artifacts for every stage — a
barebones, readable record of the run. Files are written as each stage completes, so the results
appear one at a time in engine/exports/md/.

Stages, as files:
  0_README.md            index + run summary
  1_<doc>_sections.md    LLM structure pass
  2_<doc>_sentences.md   spaCy sentence index (each line resolves to verbatim text)
  3_<doc>_codes.md       per-document codes (after the within-document reconcile)
  4_codebook.md          project codebook (after the across-document reconcile, stable IDs)
  5_themes.md            candidate themes (claims with supporting + contradicting codes)

Usage:  .venv/bin/python ../tools/run_project_md.py
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
OUT = m.ROOT / "exports" / "md"


def w(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8")
    print("wrote", name, flush=True)


def sections_md(name, doc_id, secs):
    out = [f"# {name} — sections", "", f"`{doc_id}` · {len(secs)} sections (LLM structure pass)", ""]
    for s in secs:
        out.append(f"- **{s['id']}** (lines {s['start_line']}–{s['end_line']}): {s['gist']}")
    return "\n".join(out) + "\n"


def sentences_md(conn, name, doc_id):
    rows = conn.execute("SELECT id, section_id FROM sentence WHERE doc_id=? ORDER BY char_start",
                        (doc_id,)).fetchall()
    out = [f"# {name} — sentence index", "",
           f"`{doc_id}` · {len(rows)} sentences (spaCy). Every code cites these IDs; text is "
           "resolved from the index, never re-typed.", ""]
    cur = None
    for sid, sec in rows:
        if sec != cur:
            out += ["", f"## {sec}", ""]
            cur = sec
        out.append(f"- `{sid}` {' '.join(m.resolve(conn, doc_id, sid).split())}")
    return "\n".join(out) + "\n"


def codes_md(title, codes):
    out = [f"# {title}", "", f"{len(codes)} codes", ""]
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


def themes_md(themes, codebook):
    def lab(cid):
        return codebook.get(cid, {}).get("label", cid)
    out = ["# Candidate themes", "",
           f"{len(themes)} themes — claims with supporting and contradicting codes. "
           "Candidates for a human to refine.", ""]
    for t in themes:
        out.append(f"## {t['id']} — {t['central_concept']}")
        out.append("")
        out.append("**Supporting:** " + ("; ".join(lab(c) for c in t["supporting_code_ids"]) or "—"))
        out.append("")
        out.append("**Contradicting / minority:** "
                   + ("; ".join(lab(c) for c in t["contradicting_code_ids"]) or "none recorded"))
        out.append("")
    return "\n".join(out) + "\n"


def main():
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    m.llm.reset_usage()
    run = m.new_run(conn, "2-interview MD run")
    t0 = time.perf_counter()
    doc_ids, doc_cbs = [], []

    for name in DOCS:
        path = m.ROOT.parent / "transcripts_sample" / name
        doc_id, secs, sents = m.ingest(conn, run, path)
        doc_ids.append(doc_id)
        w(f"1_{doc_id}_sections.md", sections_md(name, doc_id, secs))
        w(f"2_{doc_id}_sentences.md", sentences_md(conn, name, doc_id))
        ds, dropped = m.code_document(conn, run, doc_id)
        doc_cbs.append(ds)
        w(f"3_{doc_id}_codes.md", codes_md(f"{name} — codes ({len(ds)})", ds))
        print(f"{name}: {len(secs)} sections, {len(sents)} sentences, {len(ds)} codes", flush=True)

    codebook = m.reconcile_project(conn, run, doc_cbs)
    w("4_codebook.md", codebook_md(codebook))
    themes = m.theorize_project(conn, run)
    w("5_themes.md", themes_md(themes, codebook))

    u, dt = m.llm.usage(), time.perf_counter() - t0
    readme = (
        "# MASSHINE — 2-interview run (Markdown record)\n\n"
        f"A plain-text record of one pipeline run over **{len(DOCS)} interviews**: "
        f"{', '.join(DOCS)}.\n\n"
        "Read the files in order — they are the stages of the pipeline:\n\n"
        "1. `1_*_sections.md` — the LLM structure pass splits each transcript into sections.\n"
        "2. `2_*_sentences.md` — spaCy indexes sentences; every later code points at these IDs.\n"
        "3. `3_*_codes.md` — each document's codes, after the within-document reconcile.\n"
        "4. `4_codebook.md` — the project codebook, after the across-document reconcile (stable IDs).\n"
        "5. `5_themes.md` — candidate themes: claims with supporting and contradicting codes.\n\n"
        f"Run: {len(codebook)} codes in the project codebook, {len(themes)} candidate themes; "
        f"{u['calls']} model calls, {dt:.0f}s.\n\n"
        "This is the barebones view. The same run, with standpoint provenance and an interface, "
        "is on the project page (`index.html`) and the workbench mockup.\n"
    )
    w("0_README.md", readme)
    print(f"done in {dt:.0f}s; {u['calls']} calls", flush=True)


if __name__ == "__main__":
    main()
