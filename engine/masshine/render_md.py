"""Markdown renderers for the CLI run records — moved verbatim out of tools/run_*_md.py so the
tools stay thin and both pipelines share one home. Standard (std_*) and panel (pan_*) variants are
kept as separate functions where their wording differs, so regenerated Markdown is byte-identical
to the pre-refactor tools. Every renderer reads only from plain state (no DB connection)."""
from __future__ import annotations


def collapse(t):
    return " ".join(t.split())


# ---- standard pipeline (was tools/run_project_md.py) ----------------------------------------

def std_sections_md(doc):
    out = [f"# {doc['name']} — sections", "",
           f"{len(doc['sections'])} sections (LLM structure pass)", ""]
    for s in doc["sections"]:
        out.append(f"- **{s['id']}** (lines {s['start_line']}–{s['end_line']}): {s['gist']}")
    return "\n".join(out) + "\n"


def std_sentences_md(doc):
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


def std_codes_md(doc):
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


def std_codebook_md(codebook):
    out = ["# Project codebook (across both interviews)", "",
           f"{len(codebook)} codes after the across-document reconcile. IDs are stable.", ""]
    for cid, c in codebook.items():
        docs = sorted({e.split("#", 1)[0] for e in c["evidence"]})
        out.append(f"### {cid} · ({c['code_type']}) {c['label']}")
        out.append(c["definition"])
        out.append(f"*evidence:* {len(c['evidence'])} excerpt(s) across {', '.join(docs)}")
        out.append("")
    return "\n".join(out) + "\n"


def std_themes_md(themes, codebook, snaps, textmap, failures):
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


# ---- standpoint panel (was tools/run_panel_md.py) -------------------------------------------

def pan_sections_md(doc):
    out = [f"# {doc['name']} — sections", "",
           f"{len(doc['sections'])} sections (LLM structure pass)", ""]
    for s in doc["sections"]:
        out.append(f"- **{s['id']}** (lines {s['start_line']}–{s['end_line']}): {s['gist']}")
    return "\n".join(out) + "\n"


def pan_sentences_md(doc):
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


def pan_codes_md(doc, coder, blurb):
    codes = doc["panel"][coder]
    out = [f"# {doc['name']} — {coder} coder",
           f"*{blurb}*", "",
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


def pan_friction_md(doc, fr, textmap):
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


def pan_themes_md(themes, codebook, origin, snaps, textmap, failures):
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
