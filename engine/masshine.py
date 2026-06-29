"""MASSHINE engine — structure (LLM) + sentence index (mechanical).

Two layers, one artifact:
  sections   — an LLM 1-shot pass reads the transcript and returns its sections
               (descriptive gist + line range). No reliance on input format.
  sentences  — within each section, spaCy splits sentences (format-independent),
               stored as a SUB-HIERARCHY of the section. Pull verbatim by ID.

The LLM only returns line numbers; the system maps them to exact char offsets and
resolves verbatim text from source (P1: never regenerate). Coding comes later.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import spacy

import llm

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "masshine.db"
EXPORT_DIR = ROOT / "exports"
PROMPTS = ROOT / "prompts"
CONCURRENCY = 8  # parallel section-coder calls (blind, independent)

# ponytail: rule-based sentence splitter, no model download. Sentence splitting is
# format-independent — only document STRUCTURE needs the LLM.
_NLP = spacy.blank("en")
_NLP.add_pipe("sentencizer")


def _slug(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")


def _line_offsets(raw: str) -> list[int]:
    """offs[L-1] = char offset of the start of 1-based line L; offs[-1] = len(raw)."""
    offs, pos = [0], 0
    for line in raw.splitlines(keepends=True):
        pos += len(line)
        offs.append(pos)
    return offs


def _numbered(raw: str) -> str:
    return "".join(f"{i:04d}| {ln}"
                   for i, ln in enumerate(raw.splitlines(keepends=True), start=1))


def structure(raw: str) -> list[dict]:
    """LLM 1-shot → sections (gist + line range + char range)."""
    system = (PROMPTS / "structure.prompt").read_text(encoding="utf-8")
    data = llm.chat_json(system, _numbered(raw))
    offs = _line_offsets(raw)
    n_lines = len(offs) - 1
    sections = []
    for i, s in enumerate(data.get("sections", []), start=1):
        a = max(1, min(int(s["start_line"]), n_lines))
        b = max(a, min(int(s["end_line"]), n_lines))
        sections.append({
            "id": f"S{i}", "gist": str(s.get("gist", "")).strip(),
            "start_line": a, "end_line": b,
            "char_start": offs[a - 1], "char_end": offs[b],
        })
    return sections


def sentence_index(raw: str, sections: list[dict]) -> list[dict]:
    """spaCy-split sentences within each section. char offsets index `raw` exactly."""
    sents = []
    for sec in sections:
        k = 0
        for sent in _NLP(raw[sec["char_start"]:sec["char_end"]]).sents:
            if not sent.text.strip():
                continue
            sents.append({
                "id": f"{sec['id']}.{k:03d}", "section_id": sec["id"],
                "char_start": sec["char_start"] + sent.start_char,
                "char_end": sec["char_start"] + sent.end_char,
            })
            k += 1
    return sents


def _section_block(raw: str, sentences: list[dict]) -> str:
    """Render a section's sentences as `[ID] text` lines for the coder to cite."""
    return "\n".join(
        f"[{s['id']}] {raw[s['char_start']:s['char_end']].strip()}" for s in sentences
    )


def load_codebook(conn: sqlite3.Connection) -> dict:
    """The project-level codebook (codes accumulate across documents)."""
    cb: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT id, origin_doc_id, run_id, label, definition, code_type, evidence_ids, "
        "model_rationale FROM code ORDER BY id"
    ):
        cb[r[0]] = {"id": r[0], "origin_doc_id": r[1], "run_id": r[2], "label": r[3],
                    "definition": r[4], "code_type": r[5], "evidence": json.loads(r[6]),
                    "model_rationale": r[7]}
    return cb


def _norm_type(v) -> str:
    return "latent" if str(v).lower().startswith("lat") else "semantic"


def _reconcile_messages(codes: list[dict]) -> tuple[str, str, dict]:
    """Mechanically assemble the exact reconcile call: (system_prompt, user_message, tmp).
    The same builder feeds both reconcile() and tools/dump_reconcile_prompt.py, so the
    dumped prompt is byte-identical to what the LLM is sent."""
    system = (PROMPTS / "reconcile.prompt").read_text(encoding="utf-8")
    tmp = {f"t{i}": c for i, c in enumerate(codes)}
    listing = "\n".join(f"[{tid}] ({c['code_type']}) {c['label']} — {c['definition']}"
                        for tid, c in tmp.items())
    return system, listing, tmp


def reconcile(codes: list[dict], raise_on_fail: bool = False,
              timeout: float | None = None) -> list[dict]:
    """Merge duplicate codes via ONE compact LLM call. The model sees labels + definitions
    (no transcript) and returns only ID GROUPINGS + a `keep` id per group — it does NOT
    rewrite labels/definitions, so the output (and thus latency) stays tiny. The kept
    code's label/definition/type carries; evidence unions across the group. Codes the model
    drops fall back to their own group (defensive).

    `raise_on_fail`: when the LLM call fails, by default we degrade (return the codes unmerged) —
    fine for a within-doc or within-lens pass where unmerged-but-present is acceptable. The
    cross-document PROJECT reconcile passes `raise_on_fail=True`: a degraded project codebook must
    NOT be cached as a finished artifact — let it raise so the step stays incomplete and retries.
    """
    if not codes:
        return []
    system, listing, tmp = _reconcile_messages(codes)
    try:
        groups = llm.chat_json(system, listing, timeout=timeout).get("groups", [])
    except Exception as e:
        if raise_on_fail:
            raise
        print(f"  [warn] reconcile call failed ({type(e).__name__}); keeping codes unmerged",
              file=sys.stderr)
        return [{**c, "evidence": list(dict.fromkeys(c["evidence"]))} for c in codes]

    merged: list[dict] = []
    seen: set[str] = set()
    for g in groups:
        members = [m for m in g.get("members", []) if m in tmp and m not in seen]
        if not members:
            continue
        seen.update(members)
        keep = g.get("keep") if g.get("keep") in tmp else members[0]
        rep = tmp[keep]
        ev: list[str] = []
        for m in members:
            ev += tmp[m]["evidence"]
        merged.append({**rep, "evidence": list(dict.fromkeys(ev))})  # rep's label/def/type
    for tid, c in tmp.items():  # anything the model dropped → keep as its own code
        if tid not in seen:
            merged.append({**c, "evidence": list(dict.fromkeys(c["evidence"]))})
    return merged


def _parse_codes(items, valid: set[str], doc_id: str) -> tuple[list[dict], int]:
    """Validate model-proposed codes: doc-qualify evidence ids, drop ungrounded ones (P1).
    Shared by the coder and the critic's `missing` codes. Returns (codes, n_dropped)."""
    out, drop = [], 0
    for c in items or []:
        ev_raw = [str(x).strip() for x in c.get("evidence_sentence_ids", [])]
        ev = [f"{doc_id}#{x}" for x in ev_raw if x in valid]
        drop += len(ev_raw) - len(ev)
        if not ev:
            continue
        out.append({
            "label": str(c.get("label", "")).strip(),
            "definition": str(c.get("definition", "")).strip(),
            "code_type": _norm_type(c.get("code_type")),
            "evidence": ev,
            "model_rationale": str(c.get("rationale", "")).strip(),
        })
    return out, drop


def code_sections(conn: sqlite3.Connection, doc_id: str) -> tuple[list[dict], int]:
    """Blind per-section coding IN PARALLEL — the raw codes BEFORE reconcile.
    Returns (raw_codes, n_dropped). Evidence doc-qualified; ungrounded ids dropped (P1)."""
    system = (PROMPTS / "coder.prompt").read_text(encoding="utf-8")
    raw = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    sections = conn.execute(
        "SELECT id, gist FROM section WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()

    # Pre-build each section's prompt in the main thread (no DB access from workers).
    tasks = []
    for sec_id, gist in sections:
        sents = [dict(zip(("id", "char_start", "char_end"), r)) for r in conn.execute(
            "SELECT id, char_start, char_end FROM sentence WHERE doc_id = ? AND section_id = ? "
            "ORDER BY char_start", (doc_id, sec_id)).fetchall()]
        if sents:
            tasks.append((f"## {sec_id} — {gist}\n{_section_block(raw, sents)}",
                          {s["id"] for s in sents}))

    def code_one(task) -> tuple[list[dict], int, int]:
        prompt, valid = task
        try:
            data = llm.chat_json(system, prompt)  # only the network call runs in the worker
        except Exception as e:  # one flaky section must not crash the run, but it MUST be counted
            print(f"  [ERROR] section coder failed ({type(e).__name__}); section NOT coded",
                  file=sys.stderr)
            return [], 0, 1  # third element flags a failed section
        codes, dropped = _parse_codes(data.get("codes", []), valid, doc_id)
        return codes, dropped, 0

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        results = list(ex.map(code_one, tasks))  # blind, independent, parallel
    raw_codes = [c for codes, _, _ in results for c in codes]
    dropped = sum(d for _, d, _ in results)
    n_failed = sum(f for _, _, f in results)
    return raw_codes, dropped, n_failed


def code_document(conn: sqlite3.Connection, run_id: str, doc_id: str) -> tuple[list[dict], int, int]:
    """Code a document: parallel blind section coding, then ONE reconcile call to merge
    duplicate codes within the document. Returns (doc_codebook, n_dropped, n_failed_sections).
    `n_failed_sections > 0` means the document is INCOMPLETE — the caller must not cache it as done."""
    raw_codes, dropped, n_failed = code_sections(conn, doc_id)
    return reconcile(raw_codes), dropped, n_failed  # within-doc reconcile degrades on failure


def _apply_critic(kmap: dict, vdata: dict, valid: set[str], doc_id: str) -> tuple[list[dict], list[dict]]:
    """Apply a critic's verdicts to the coder's codes → (arm_B codes, disagreement notes).
    endorse → keep; revise → keep with corrected label/def/level; challenge → keep + flag;
    drop → remove (logged); missing → add the critic's grounded new codes. Divergence is logged,
    never silently smoothed — the notes ARE the structured disagreement between coder and critic."""
    verdicts = {v.get("id"): v for v in vdata.get("verdicts", [])}
    arm_b, notes = [], []
    for kid, c in kmap.items():
        v = verdicts.get(kid, {"verdict": "endorse"})
        verdict = str(v.get("verdict", "endorse")).lower()
        issue = str(v.get("issue", "")).strip()
        if verdict == "drop":
            notes.append({"verdict": "drop", "label": c["label"], "issue": issue})
            continue
        code = dict(c)
        if verdict == "revise":
            for f in ("label", "definition"):
                if v.get(f):
                    code[f] = str(v[f]).strip()
            if v.get("code_type"):
                code["code_type"] = _norm_type(v["code_type"])
        notes.append({"verdict": verdict if verdict in ("revise", "challenge") else "endorse",
                      "label": c["label"], "issue": issue})
        arm_b.append(code)
    missing, _ = _parse_codes(vdata.get("missing", []), valid, doc_id)
    for m in missing:
        notes.append({"verdict": "missing", "label": m["label"], "issue": ""})
    arm_b.extend(missing)
    return arm_b, notes


def code_sections_compare(conn: sqlite3.Connection, doc_id: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Run two architectures over the SAME coder pass, per section, in parallel:
      arm A (single_cot)    = the coder's codes
      arm B (coder_critic)  = the coder's codes after a critic reviews them
    Returns (raw_a, raw_b, notes). The critic's verdicts (notes) are the logged A-vs-B divergence."""
    coder_sys = (PROMPTS / "coder.prompt").read_text(encoding="utf-8")
    critic_sys = (PROMPTS / "critic.prompt").read_text(encoding="utf-8")
    raw = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    sections = conn.execute(
        "SELECT id, gist FROM section WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()

    tasks = []
    for sec_id, gist in sections:
        sents = [dict(zip(("id", "char_start", "char_end"), r)) for r in conn.execute(
            "SELECT id, char_start, char_end FROM sentence WHERE doc_id = ? AND section_id = ? "
            "ORDER BY char_start", (doc_id, sec_id)).fetchall()]
        if sents:
            tasks.append((f"## {sec_id} — {gist}\n{_section_block(raw, sents)}",
                          {s["id"] for s in sents}))

    def run_one(task):
        block, valid = task
        try:
            arm_a, _ = _parse_codes(llm.chat_json(coder_sys, block).get("codes", []), valid, doc_id)
        except Exception as e:
            print(f"  [warn] coder failed ({type(e).__name__}); skipping section", file=sys.stderr)
            return [], [], []
        if not arm_a:
            return [], [], []
        kmap = {f"k{i}": c for i, c in enumerate(arm_a)}
        listing = "\n".join(
            f"[{k}] ({c['code_type']}) {c['label']} — {c['definition']}  "
            f"⟨cites: {', '.join(e.split('#', 1)[1] for e in c['evidence'])}⟩  "
            f"rationale: {c['model_rationale']}" for k, c in kmap.items())
        try:
            vdata = llm.chat_json(critic_sys, f"{block}\n\nCODER'S PROPOSED CODES:\n{listing}")
        except Exception as e:  # critic flaked → arm B falls back to arm A (no disagreement logged)
            print(f"  [warn] critic failed ({type(e).__name__}); arm B = arm A", file=sys.stderr)
            return arm_a, list(arm_a), []
        arm_b, notes = _apply_critic(kmap, vdata, valid, doc_id)
        return arm_a, arm_b, notes

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        results = list(ex.map(run_one, tasks))
    raw_a = [c for a, _, _ in results for c in a]
    raw_b = [c for _, b, _ in results for c in b]
    notes = [n for _, _, ns in results for n in ns]
    return raw_a, raw_b, notes


def code_sections_panel(conn: sqlite3.Connection, doc_id: str, coders: dict[str, str]) -> dict[str, list[dict]]:
    """Run a PANEL of independent coders over the same sections, blind and in parallel. `coders` maps
    a name → its system prompt (the standard coder, or a standpoint persona). Each coder is the same
    coding mechanism with a different system prompt, so all codings share the schema and cite the same
    sentence ids — making them directly comparable. Returns {coder_name: raw_codes}."""
    raw = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    sections = conn.execute(
        "SELECT id, gist FROM section WHERE doc_id = ? ORDER BY char_start", (doc_id,)).fetchall()
    blocks = []
    for sec_id, gist in sections:
        sents = [dict(zip(("id", "char_start", "char_end"), r)) for r in conn.execute(
            "SELECT id, char_start, char_end FROM sentence WHERE doc_id = ? AND section_id = ? "
            "ORDER BY char_start", (doc_id, sec_id)).fetchall()]
        if sents:
            blocks.append((f"## {sec_id} — {gist}\n{_section_block(raw, sents)}",
                           {s["id"] for s in sents}))
    tasks = [(name, system, block, valid)
             for name, system in coders.items() for (block, valid) in blocks]

    def run_one(task):
        name, system, block, valid = task
        try:
            codes, _ = _parse_codes(llm.chat_json(system, block).get("codes", []), valid, doc_id)
            return name, codes, 0
        except Exception as e:  # one coder failing on one section must be counted, not silently lost
            print(f"  [ERROR] panel coder '{name}' failed on a section ({type(e).__name__})",
                  file=sys.stderr)
            return name, [], 1

    out: dict[str, list[dict]] = {name: [] for name in coders}
    n_failed = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for name, codes, failed in ex.map(run_one, tasks):
            out[name].extend(codes)
            n_failed += failed
    return out, n_failed


def friction(panel: dict[str, list[dict]]) -> dict:
    """Sentence-anchored divergence across a coder panel — pure Python, no LLM.
    INTERPRETIVE friction: a sentence coded by 2+ coders (same evidence, different readings to compare).
    ATTENTIONAL friction: a sentence coded by only SOME coders (a lens found something codable there
    that others did not — including a standpoint seeing what the standard missed).
    Returns coverage per coder + both friction sets keyed by doc-qualified sentence id."""
    by_sent: dict[str, dict[str, list[dict]]] = {}
    for coder, codes in panel.items():
        for c in codes:
            for ev in c["evidence"]:
                by_sent.setdefault(ev, {}).setdefault(coder, []).append(c)
    coders = list(panel)
    coverage = {co: sum(1 for cm in by_sent.values() if co in cm) for co in coders}
    interpretive = {s: cm for s, cm in by_sent.items() if len(cm) >= 2}
    attentional = {s: cm for s, cm in by_sent.items() if len(cm) < len(coders)}
    return {"coverage": coverage, "interpretive": interpretive,
            "attentional": attentional, "by_sent": by_sent, "coders": coders}


def _next_code_id(conn: sqlite3.Connection) -> str:
    """Hand out the next code id from a persistent monotonic counter. Ids are NEVER reused
    or renumbered — a code that merges away frees nothing — so `C0042` means the same code
    for the life of the project (the referential anchor themes/decisions/UI depend on)."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'code_seq'").fetchone()
    n = (row[0] if row else 0) + 1
    conn.execute("INSERT INTO meta (key, value) VALUES ('code_seq', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (n,))
    return f"C{n:04d}"


def _write_codebook(conn: sqlite3.Connection, run_id: str,
                    items: list[tuple[str | None, dict]]) -> dict:
    """Rewrite the `code` table from (id|None, code) pairs. A None id is minted fresh from the
    monotonic counter; a real id is preserved verbatim. Stable ids survive the rewrite."""
    out: dict[str, dict] = {}
    for cid, c in items:
        cid = cid or _next_code_id(conn)
        out[cid] = {
            "id": cid, "origin_doc_id": (c["evidence"][0].split("#", 1)[0] if c["evidence"] else ""),
            "run_id": run_id, "label": c["label"], "definition": c["definition"],
            "code_type": c["code_type"], "evidence": c["evidence"],
            "model_rationale": c.get("model_rationale", ""),
        }
    conn.execute("DELETE FROM code")
    conn.executemany(
        "INSERT INTO code (id, origin_doc_id, run_id, label, definition, code_type, "
        "evidence_ids, model_rationale) VALUES "
        "(:id,:origin_doc_id,:run_id,:label,:definition,:code_type,:evidence_ids,:model_rationale)",
        [{**c, "evidence_ids": json.dumps(c["evidence"])} for c in out.values()],
    )
    conn.commit()
    return out


def reconcile_project(conn: sqlite3.Connection, run_id: str, doc_codebooks: list[list[dict]]) -> dict:
    """Build the project codebook from scratch over ALL docs (one reconcile call), assigning
    fresh monotonic ids. Use this to seed a corpus; use add_document() to grow one incrementally."""
    project = reconcile([c for cb in doc_codebooks for c in cb], raise_on_fail=True)
    return _write_codebook(conn, run_id, [(None, c) for c in project])


def reconcile_into(conn: sqlite3.Connection, run_id: str, new_codes: list[dict]) -> dict:
    """INCREMENTAL reconcile: fold a new document's codes into the EXISTING project codebook.
    Existing codes keep their ids; a new code that restates an existing one merges into it
    (evidence unions, the established definition wins); a genuinely new code gets a fresh
    monotonic id. If two existing codes are judged the same, the lower id survives. No existing
    document is re-coded. (First call, empty codebook → same as the from-scratch build.)"""
    existing = load_codebook(conn)
    if not existing:
        return _write_codebook(conn, run_id, [(None, c) for c in reconcile(new_codes)])

    # one reconcile call over existing-codes + new-codes; the prompt is unchanged (symmetric
    # dedup) — id stability is enforced here, in Python, by who is in each group.
    tagged: list[tuple[str | None, dict]] = (
        [(cid, existing[cid]) for cid in existing] + [(None, c) for c in new_codes]
    )
    system, listing, tmp = _reconcile_messages([c for _, c in tagged])
    tag_of = {f"t{i}": tagged[i][0] for i in range(len(tagged))}  # temp id -> real id | None
    try:
        groups = llm.chat_json(system, listing).get("groups", [])
    except Exception as e:
        print(f"  [warn] incremental reconcile failed ({type(e).__name__}); appending new "
              f"codes unmerged", file=sys.stderr)
        groups = []

    final: list[tuple[str | None, dict]] = []
    seen: set[str] = set()
    for g in groups:
        members = [m for m in g.get("members", []) if m in tmp and m not in seen]
        if len(members) < 2:
            continue
        seen.update(members)
        existing_ids = sorted(tag_of[m] for m in members if tag_of[m] is not None)
        ev: list[str] = []
        for m in members:
            ev += tmp[m]["evidence"]
        ev = list(dict.fromkeys(ev))
        if existing_ids:  # an established code absorbs the group; its definition wins
            keep_id = existing_ids[0]
            canon = existing[keep_id]
        else:             # all-new group → one new code
            keep_id = None
            km = g.get("keep") if g.get("keep") in members else members[0]
            canon = tmp[km]
        final.append((keep_id, {**canon, "evidence": ev}))
    for i, (cid, c) in enumerate(tagged):  # untouched codes pass through with their id
        if f"t{i}" not in seen:
            final.append((cid, {**c, "evidence": list(dict.fromkeys(c["evidence"]))}))
    return _write_codebook(conn, run_id, final)


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


def _codebook_listing(codebook: dict) -> str:
    """Render the codebook as `[C0007] (semantic) label — definition` lines for the theorist."""
    return "\n".join(f"[{cid}] ({c['code_type']}) {c['label']} — {c['definition']}"
                     for cid, c in codebook.items())


def theorize(codebook: dict) -> list[dict]:
    """ONE LLM pass: codebook → candidate themes. The theorist sees only codes (by id), never
    the transcript, and returns themes as CLAIMS with supporting + contradicting code ids
    (divergence preserved). Themes are candidates for a human to review — not final. Invented
    or empty-support code ids are dropped (P1-style grounding for the code layer)."""
    if not codebook:
        return []
    # the LEGACY codebook-only prompt — `theorist.prompt` was rewritten for the sequential,
    # transcript-grounded walk and would be malformed here. This pooled path (demo/add_document/
    # run_panel_themes) keeps its own compatible prompt. (P2: don't feed the new prompt old input.)
    system = (PROMPTS / "theorist_codebook.prompt").read_text(encoding="utf-8")
    try:
        themes = llm.chat_json(system, _codebook_listing(codebook)).get("themes", [])
    except Exception as e:  # degrade gracefully: no themes rather than crash the run
        print(f"  [warn] theorist call failed ({type(e).__name__}); no themes", file=sys.stderr)
        return []
    valid = set(codebook)
    out: list[dict] = []
    for i, t in enumerate(themes, start=1):
        sup = [c for c in t.get("supporting_code_ids", []) if c in valid]
        con = [c for c in t.get("contradicting_code_ids", []) if c in valid]
        if not sup:  # a theme with no grounded supporting code is not a theme
            continue
        out.append({"id": f"T{i:02d}",
                    "central_concept": str(t.get("central_concept", "")).strip(),
                    "supporting_code_ids": sup, "contradicting_code_ids": con})
    return out


def theorize_project(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Run the theme pass over the whole project codebook; store + return candidate themes."""
    themes = theorize(load_codebook(conn))
    conn.execute("DELETE FROM theme")
    conn.executemany(
        "INSERT INTO theme (id, run_id, central_concept, supporting_code_ids, "
        "contradicting_code_ids) VALUES (:id,:run_id,:central_concept,:sup,:con)",
        [{"id": t["id"], "run_id": run_id, "central_concept": t["central_concept"],
          "sup": json.dumps(t["supporting_code_ids"]),
          "con": json.dumps(t["contradicting_code_ids"])} for t in themes],
    )
    conn.commit()
    return themes


def theorize_panel(panel: dict[str, list[dict]]) -> tuple[list[dict], dict, dict]:
    """Distill a standpoint panel STRAIGHT into themes, each carrying PARADIGM PROVENANCE.
    Codes are pooled WITHOUT cross-paradigm reconcile — on purpose: keeping each lens's codes
    distinct is what lets a theme record how many paradigms independently support it (convergence)
    vs being unique to one lens (divergence). The theorist themes by meaning, paradigm-blind;
    provenance is counted programmatically afterward (LLM constructs, Python counts → P3).
    Returns (themes, codebook, origin) where origin maps code id → coder/paradigm name."""
    codebook: dict[str, dict] = {}
    origin: dict[str, str] = {}
    i = 0
    for coder, codes in panel.items():
        for c in codes:
            i += 1
            cid = f"C{i:04d}"
            codebook[cid] = c
            origin[cid] = coder
    themes = theorize(codebook)
    for t in themes:  # count supporting codes by paradigm → provenance
        prov: dict[str, int] = {}
        for cid in t["supporting_code_ids"]:
            co = origin.get(cid)
            if co:
                prov[co] = prov.get(co, 0) + 1
        t["paradigm_provenance"] = prov
    return themes, codebook, origin


# --- sequential, transcript-grounded theming -------------------------------------------------
# The theorist now sees the actual TRANSCRIPT (not just code labels) and builds themes ONE
# interview at a time: each step gets the prior themes + this interview's text + this interview's
# codes, and returns the full updated theme set. Coverage rises as a theme recurs across interviews;
# claim scope is forced to match coverage (computed from evidence, not trusted to the model).

def _doc_transcript_block(conn: sqlite3.Connection, doc_id: str) -> str:
    """The whole document as `[S1.003] text` lines grouped by section — the theorist's view of one
    interview's text, resolved from the index (P1)."""
    raw = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    rows = conn.execute(
        "SELECT s.id, s.section_id, s.char_start, s.char_end, sec.gist FROM sentence s "
        "JOIN section sec ON sec.doc_id = s.doc_id AND sec.id = s.section_id "
        "WHERE s.doc_id = ? ORDER BY s.char_start", (doc_id,)).fetchall()
    out, cur = [], None
    for sid, sec, cs, ce, gist in rows:
        if sec != cur:
            out.append(f"\n## {sec} — {gist}")
            cur = sec
        out.append(f"[{sid}] {' '.join(raw[cs:ce].split())}")
    return "\n".join(out)


def transcript_block_from_sentences(sentences: list[dict], sections: list[dict]) -> str:
    """Same transcript block, built from already-resolved sentence text (id, section_id, text) +
    section gists — so a cached run can theme without a live DB."""
    gist = {s["id"]: s.get("gist", "") for s in sections}
    out, cur = [], None
    for s in sentences:
        if s["section_id"] != cur:
            out.append(f"\n## {s['section_id']} — {gist.get(s['section_id'], '')}")
            cur = s["section_id"]
        out.append(f"[{s['id']}] {' '.join(s['text'].split())}")
    return "\n".join(out)


def _theorist_codes_block(doc_codes: list[tuple[str, dict]]) -> str:
    """`[C0007] (latent) "label" — definition · cites: S2.007, S2.008 · instances: 3` per code."""
    lines = []
    for cid, c in doc_codes:
        cites = ", ".join(e.split("#", 1)[1] for e in c["evidence"])
        lines.append(f'[{cid}] ({c["code_type"]}) "{c["label"]}" — {c["definition"]} '
                     f'· cites: {cites} · instances: {len(c["evidence"])}')
    return "\n".join(lines)


def _prior_themes_block(themes: list[dict]) -> str:
    if not themes:
        return "none yet — this is the first interview."
    lines = []
    for t in themes:
        lines.append(f'[{t["id"]}] {t["central_concept"]}  · supporting: '
                     f'{", ".join(t["supporting_code_ids"])}  · coverage: {t.get("coverage", "?")}')
        for st in t.get("subthemes", []):
            lines.append(f'    – sub: {st.get("claim", "")}  · supporting: '
                         f'{", ".join(st.get("supporting_code_ids", []))}')
    return "\n".join(lines)


def _resolve_step_themes(raw: list, prior: list[dict], valid_codes: set, all_codes: dict,
                         valid_sents: set, doc_id: str, seq: list, n_docs: int,
                         origin: dict | None, docs_seen: set,
                         valid_qualified: set | None = None) -> list[dict]:
    """Validate one step's returned themes: keep grounded support only, assign STABLE ids (echoed
    prior id reused, else minted monotonically), derive coverage/scope from evidence (not trusted to
    the model), qualify new sentence anchors, count paradigm provenance when origin is given.

    `docs_seen` = the interviews processed so far; coverage counts only those, so a cross-document
    code can't leak coverage from an interview the walk hasn't reached yet (a no-op for the panel,
    whose codes are doc-local). `valid_qualified` = every real `doc#sentence` id, used to reject a
    fabricated already-qualified anchor."""
    prior_by_id = {t["id"]: t for t in prior}
    out, used = [], set()
    for t in raw:
        rid = t.get("id")
        if rid in prior_by_id and rid not in used:
            tid = rid
        else:
            seq[0] += 1
            tid = f"T{seq[0]:02d}"
        used.add(tid)
        new_sup = [c for c in t.get("supporting_code_ids", []) if c in valid_codes]
        # ACCUMULATE across interviews. Codes are doc-local, so a theme is cross-case only if it
        # carries codes from >1 interview. The model is unreliable at re-listing earlier interviews'
        # code ids when it revises a theme, so PYTHON unions prior + new (never the prompt) — this is
        # what actually lets coverage rise to "2 of 2". (P3: model judges, Python aggregates.)
        prior = prior_by_id.get(tid)
        sup = list(dict.fromkeys((prior["supporting_code_ids"] if prior else []) + new_sup))
        if not sup:  # a theme with no grounded supporting code is not a theme
            continue
        docs = {ev.split("#", 1)[0] for cid in sup
                for ev in all_codes.get(cid, {}).get("evidence", [])} & docs_seen
        k = len(docs)
        kev = []
        for s in t.get("key_evidence_sentence_ids", []):
            s = str(s).strip()
            if s in valid_sents:
                kev.append(f"{doc_id}#{s}")
            elif "#" in s and (valid_qualified is None or s in valid_qualified):
                kev.append(s)  # carried/qualified anchor — validated against the real sentence index
        if prior:  # keep earlier interviews' anchors too → a cross-case theme shows both interviews
            kev = list(dict.fromkeys(prior.get("key_evidence_sentence_ids", []) + kev))
        new_tens = [c for c in t.get("tensions", []) if c in valid_codes]
        tensions = list(dict.fromkeys((prior.get("tensions", []) if prior else []) + new_tens))
        subs = []
        for st in t.get("subthemes", []) or []:
            ssup = [c for c in st.get("supporting_code_ids", []) if c in valid_codes]
            if ssup:
                subs.append({"claim": str(st.get("claim", "")).strip(),
                             "supporting_code_ids": ssup})
        theme = {
            "id": tid, "central_concept": str(t.get("central_concept", "")).strip(),
            "subthemes": subs, "supporting_code_ids": sup, "key_evidence_sentence_ids": kev,
            "coverage": f"{k} of {n_docs}",
            "claim_scope": "cross-case" if k >= 2 else "single-case",
            "tensions": tensions,
            "falsified_if": str(t.get("falsified_if", "")).strip(),
        }
        if origin is not None:
            prov: dict[str, int] = {}
            for cid in sup:
                lens = origin.get(cid)
                if lens:
                    prov[lens] = prov.get(lens, 0) + 1
            theme["paradigm_provenance"] = prov
        out.append(theme)
    return out


def theorize_walk(doc_order: list[str], doc_codes_map: dict, all_codes: dict,
                  transcripts: dict, valid_sents_map: dict, origin: dict | None = None,
                  timeout: float | None = 420.0, raw_cache: dict | None = None,
                  save_raw=None) -> tuple[list[dict], list[tuple], list[str]]:
    """Walk interviews in order, building themes incrementally (one LLM call per interview). DB-free:
    the caller supplies each interview's transcript block + valid sentence ids. Returns
    (final_themes, snapshots, failures) where snapshots[i] = (doc_id, themes-after-that-interview)
    and `failures` lists any interviews whose theme step errored (e.g. timed out) and so were NOT
    integrated — a dropped interview must be LOUD, never silently swallowed into a clean-looking
    output. The theorist gets a LONGER per-call timeout than the 120s default (it reads a whole
    transcript with thinking on, which legitimately runs ~150–300s) and no retry — a step that still
    exceeds it fails loudly and resumes on re-run rather than hanging.

    RESUMABLE: if `raw_cache` maps a doc_id → that step's raw model output, the step is REPLAYED from
    it (no API call) and re-resolved deterministically; `save_raw(doc_id, raw)` is called after a
    fresh successful call so the caller can persist the checkpoint. A failed step is NOT saved, so a
    re-run resumes exactly there. (Replay reproduces ids/coverage because resolution is deterministic
    given the accumulated prior state.)"""
    n = len(doc_order)
    system = (PROMPTS / "theorist.prompt").read_text(encoding="utf-8")
    all_qualified = {f"{d}#{s}" for d, ss in valid_sents_map.items() for s in ss}
    seq, themes, snapshots, referenced, failures = [0], [], [], set(), []
    prefix_intact = raw_cache is not None  # replay only a CONTIGUOUS prefix of cached steps
    for i, doc_id in enumerate(doc_order, start=1):
        doc_codes = doc_codes_map.get(doc_id, [])
        valid_codes = {cid for cid, _ in doc_codes} | referenced
        valid_sents = valid_sents_map.get(doc_id, set())
        docs_seen = set(doc_order[:i])  # interviews processed up to and including this one
        if prefix_intact and doc_id in raw_cache:
            print(f"  resumed theme step {i}/{n} ({doc_id}) from checkpoint", flush=True)
            raw = raw_cache[doc_id]
        else:
            # Each step's prompt embeds the PRIOR steps' themes, so once we run ANY step fresh, every
            # later cached step is stale (generated against a different prior state) and must be
            # re-run, never replayed. (P1: a mid-sequence failure can't resurrect stale downstream.)
            prefix_intact = False
            user = (f"N (total interviews) = {n}. This is interview {i} of {n}.\n\n"
                    f"PRIOR THEMES:\n{_prior_themes_block(themes)}\n\n"
                    f"TRANSCRIPT (interview {i}):\n{transcripts[doc_id]}\n\n"
                    f"CODES (from interview {i}):\n{_theorist_codes_block(doc_codes)}")
            try:
                raw = llm.chat_json(system, user, timeout=timeout, retries=0).get("themes", [])
            except Exception as e:
                print(f"  [ERROR] theorist step DROPPED interview {i}/{n} ({doc_id}): "
                      f"{type(e).__name__} — this interview is NOT in the themes below",
                      file=sys.stderr)
                failures.append(doc_id)
                raw = None
            else:
                if save_raw is not None:
                    save_raw(doc_id, raw)
        if raw is not None:
            resolved = _resolve_step_themes(raw, themes, valid_codes, all_codes, valid_sents,
                                            doc_id, seq, n, origin, docs_seen, all_qualified)
            if resolved or not themes:  # don't let an empty return wipe a real theme set
                themes = resolved
        referenced |= {c for t in themes for c in t["supporting_code_ids"]}
        snapshots.append((doc_id, json.loads(json.dumps(themes))))
    return themes, snapshots, failures


def theorize_project_sequential(doc_order: list[str], project_codebook: dict, transcripts: dict,
                                valid_sents_map: dict, timeout: float | None = 420.0,
                                raw_cache: dict | None = None, save_raw=None
                                ) -> tuple[list[dict], dict, list[tuple], list[str]]:
    """Standard pipeline: sequential, transcript-grounded theming over the RECONCILED PROJECT
    CODEBOOK — so a theme's supporting ids are the SAME stable `Cxxxx` ids printed in `4_codebook.md`
    (theme → codebook tracing is auditable). Each interview's codes = project codes whose evidence
    touches that interview (a cross-document code shows up under both). Coverage is computed from
    interviews-processed-so-far inside `theorize_walk`, so a cross-document code can't pre-leak
    "2 of 2" onto a step-1 theme. DB-free; `raw_cache`/`save_raw` make the theme steps resumable.
    Returns (themes, codebook, snapshots, failures)."""
    doc_codes_map = {
        doc_id: [(cid, c) for cid, c in project_codebook.items()
                 if any(ev.split("#", 1)[0] == doc_id for ev in c["evidence"])]
        for doc_id in doc_order
    }
    themes, snaps, fails = theorize_walk(doc_order, doc_codes_map, project_codebook, transcripts,
                                         valid_sents_map, timeout=timeout,
                                         raw_cache=raw_cache, save_raw=save_raw)
    return themes, project_codebook, snaps, fails


def theorize_panel_sequential(doc_order: list[str], panel_by_doc: dict, transcripts: dict,
                              valid_sents_map: dict, timeout: float | None = 420.0,
                              raw_cache: dict | None = None, save_raw=None
                              ) -> tuple[list[dict], dict, dict, list[tuple], list[str]]:
    """Panel pipeline: sequential theming over the three lenses' codes, paradigm-blind, with
    provenance counted afterward. panel_by_doc = {doc_id: {lens: [codes]}}; codes get stable global
    ids and stay doc-local (coverage rises only across interviews). `raw_cache`/`save_raw` make the
    theme steps resumable. Returns (themes, codebook, origin, snapshots, failures)."""
    codebook, origin, doc_codes_map = {}, {}, {}
    i = 0
    for doc_id in doc_order:
        lst = []
        for lens, codes in panel_by_doc[doc_id].items():
            for c in codes:
                i += 1
                cid = f"C{i:04d}"
                codebook[cid] = c
                origin[cid] = lens
                lst.append((cid, c))
        doc_codes_map[doc_id] = lst
    themes, snaps, fails = theorize_walk(doc_order, doc_codes_map, codebook, transcripts,
                                         valid_sents_map, origin=origin, timeout=timeout,
                                         raw_cache=raw_cache, save_raw=save_raw)
    return themes, codebook, origin, snaps, fails


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS run (id TEXT PRIMARY KEY, created_at TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS document (
            id TEXT PRIMARY KEY, run_id TEXT, path TEXT, text TEXT, text_hash TEXT, char_len INTEGER
        );
        CREATE TABLE IF NOT EXISTS section (
            id TEXT, doc_id TEXT, gist TEXT,
            start_line INTEGER, end_line INTEGER, char_start INTEGER, char_end INTEGER,
            PRIMARY KEY (doc_id, id)
        );
        CREATE TABLE IF NOT EXISTS sentence (
            id TEXT, doc_id TEXT, section_id TEXT, char_start INTEGER, char_end INTEGER,
            PRIMARY KEY (doc_id, id)
        );
        CREATE TABLE IF NOT EXISTS code (
            id TEXT PRIMARY KEY, origin_doc_id TEXT, run_id TEXT,
            label TEXT, definition TEXT, code_type TEXT,
            evidence_ids TEXT, model_rationale TEXT
        );
        CREATE TABLE IF NOT EXISTS theme (
            id TEXT PRIMARY KEY, run_id TEXT, central_concept TEXT,
            supporting_code_ids TEXT, contradicting_code_ids TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
        """
    )
    conn.commit()


def new_run(conn: sqlite3.Connection, note: str = "") -> str:
    run_id = "R" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    conn.execute("INSERT INTO run (id, created_at, note) VALUES (?,?,?)",
                 (run_id, datetime.now(timezone.utc).isoformat(), note))
    conn.commit()
    return run_id


def ingest(conn: sqlite3.Connection, run_id: str, path: Path) -> tuple[str, list[dict], list[dict]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    doc_id = _slug(path)
    sections = structure(raw)
    sents = sentence_index(raw, sections)
    conn.execute("DELETE FROM sentence WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM section WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM document WHERE id = ?", (doc_id,))
    conn.execute(
        "INSERT INTO document (id, run_id, path, text, text_hash, char_len) VALUES (?,?,?,?,?,?)",
        (doc_id, run_id, str(path), raw, hashlib.sha256(raw.encode()).hexdigest()[:16], len(raw)),
    )
    conn.executemany(
        "INSERT INTO section (id, doc_id, gist, start_line, end_line, char_start, char_end) "
        "VALUES (:id,:doc_id,:gist,:start_line,:end_line,:char_start,:char_end)",
        [{**s, "doc_id": doc_id} for s in sections],
    )
    conn.executemany(
        "INSERT INTO sentence (id, doc_id, section_id, char_start, char_end) "
        "VALUES (:id,:doc_id,:section_id,:char_start,:char_end)",
        [{**s, "doc_id": doc_id} for s in sents],
    )
    conn.commit()
    return doc_id, sections, sents


def export_json(conn: sqlite3.Connection, doc_id: str) -> Path:
    """Artifact: sections with their sentences nested (sub-hierarchy)."""
    doc = conn.execute(
        "SELECT id, run_id, text_hash, char_len FROM document WHERE id = ?", (doc_id,)
    ).fetchone()
    secs = conn.execute(
        "SELECT id, gist, start_line, end_line, char_start, char_end FROM section "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    sents = conn.execute(
        "SELECT id, section_id, char_start, char_end FROM sentence "
        "WHERE doc_id = ? ORDER BY char_start", (doc_id,)
    ).fetchall()
    by_section: dict[str, list] = {}
    for r in sents:
        by_section.setdefault(r[1], []).append(
            {"id": r[0], "char_start": r[2], "char_end": r[3]}
        )
    # codes are project-level (evidence is doc-qualified); include the whole codebook
    codes = conn.execute(
        "SELECT id, origin_doc_id, label, definition, code_type, evidence_ids, model_rationale "
        "FROM code ORDER BY id"
    ).fetchall()
    themes = conn.execute(
        "SELECT id, central_concept, supporting_code_ids, contradicting_code_ids "
        "FROM theme ORDER BY id"
    ).fetchall()
    EXPORT_DIR.mkdir(exist_ok=True)
    payload = {
        "document": {"id": doc[0], "run_id": doc[1], "text_hash": doc[2], "char_len": doc[3]},
        "sections": [
            {"id": r[0], "gist": r[1], "start_line": r[2], "end_line": r[3],
             "char_start": r[4], "char_end": r[5], "sentences": by_section.get(r[0], [])}
            for r in secs
        ],
        "codebook": [
            {"id": c[0], "origin_doc_id": c[1], "label": c[2], "definition": c[3],
             "code_type": c[4], "evidence_sentence_ids": json.loads(c[5]),
             "model_rationale": c[6]}
            for c in codes
        ],
        "themes": [
            {"id": t[0], "central_concept": t[1],
             "supporting_code_ids": json.loads(t[2]),
             "contradicting_code_ids": json.loads(t[3])}
            for t in themes
        ],
    }
    out = EXPORT_DIR / f"{doc_id}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out


def resolve(conn: sqlite3.Connection, doc_id: str, sentence_id: str) -> str:
    """Pull verbatim text for a sentence ID from the index (P1: never regenerate)."""
    text = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    cs, ce = conn.execute(
        "SELECT char_start, char_end FROM sentence WHERE doc_id = ? AND id = ?",
        (doc_id, sentence_id),
    ).fetchone()
    return text[cs:ce]


def resolve_ev(conn: sqlite3.Connection, qualified: str) -> str:
    """Resolve a doc-qualified evidence id 'doc_id#sentence_id' to verbatim text."""
    doc_id, sentence_id = qualified.split("#", 1)
    return resolve(conn, doc_id, sentence_id)


def demo(doc_names: list[str] | None = None) -> None:
    """Project demo: code transcripts (parallel, blind) → per-doc reconcile → cross-doc
    reconcile → one shared codebook → one theme pass → export (sections + codebook + themes).
    Reports wall-clock since latency is the concern."""
    import time
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


if __name__ == "__main__":
    demo()
