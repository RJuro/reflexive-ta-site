"""Coding layer: blind per-section coding (single / coder-critic / standpoint panel), in parallel.

Every coder is the same mechanism with a different system prompt, so all codings share the schema
and cite the same sentence ids — directly comparable. Evidence is doc-qualified and ungrounded
ids are dropped (P1). The within-document reconcile merges duplicates.
"""
from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .config import CONCURRENCY, PROMPTS
from .reconcile import reconcile


def _section_block(raw: str, sentences: list[dict]) -> str:
    """Render a section's sentences as `[ID] text` lines for the coder to cite."""
    return "\n".join(
        f"[{s['id']}] {raw[s['char_start']:s['char_end']].strip()}" for s in sentences
    )


def _norm_type(v) -> str:
    return "latent" if str(v).lower().startswith("lat") else "semantic"


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


GUIDANCE_HEADER = (
    "RESEARCHER FEEDBACK (from reviewing an earlier coding of this interview — take it into "
    "account when coding: adjust granularity, drop or rename interpretations as directed. "
    "The evidence-grounding rules above still apply in full):"
)


def _with_guidance(block: str, guidance: str | None) -> str:
    return f"{block}\n\n{GUIDANCE_HEADER}\n{guidance}" if guidance else block


def code_sections(conn: sqlite3.Connection, doc_id: str,
                  guidance: str | None = None) -> tuple[list[dict], int]:
    """Blind per-section coding IN PARALLEL — the raw codes BEFORE reconcile.
    Returns (raw_codes, n_dropped). Evidence doc-qualified; ungrounded ids dropped (P1).
    `guidance` (researcher feedback, compiled by store.compile_guidance) rides along in every
    section's user message on a recode."""
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
            tasks.append((_with_guidance(f"## {sec_id} — {gist}\n{_section_block(raw, sents)}",
                                         guidance),
                          {s["id"] for s in sents}))

    def code_one(task) -> tuple[list[dict], int, int]:
        prompt, valid = task
        try:
            data = llm.chat_json(system, prompt, label="coder")  # only the network call runs in the worker
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


def code_document(conn: sqlite3.Connection, run_id: str, doc_id: str,
                  guidance: str | None = None) -> tuple[list[dict], int, int]:
    """Code a document: parallel blind section coding, then ONE reconcile call to merge
    duplicate codes within the document. Returns (doc_codebook, n_dropped, n_failed_sections).
    `n_failed_sections > 0` means the document is INCOMPLETE — the caller must not cache it as done."""
    raw_codes, dropped, n_failed = code_sections(conn, doc_id, guidance=guidance)
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
            arm_a, _ = _parse_codes(llm.chat_json(coder_sys, block, label="coder").get("codes", []), valid, doc_id)
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
            vdata = llm.chat_json(critic_sys, f"{block}\n\nCODER'S PROPOSED CODES:\n{listing}", label="critic")
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


def code_sections_panel(conn: sqlite3.Connection, doc_id: str, coders: dict[str, str],
                        guidance: str | None = None) -> dict[str, list[dict]]:
    """Run a PANEL of independent coders over the same sections, blind and in parallel. `coders` maps
    a name → its system prompt (the standard coder, or a standpoint persona). Each coder is the same
    coding mechanism with a different system prompt, so all codings share the schema and cite the same
    sentence ids — making them directly comparable. Returns {coder_name: raw_codes}.
    `guidance` (researcher feedback) rides along in every lens × section user message on a recode —
    every lens hears the same researcher, and stays blind to the other lenses."""
    raw = conn.execute("SELECT text FROM document WHERE id = ?", (doc_id,)).fetchone()[0]
    sections = conn.execute(
        "SELECT id, gist FROM section WHERE doc_id = ? ORDER BY char_start", (doc_id,)).fetchall()
    blocks = []
    for sec_id, gist in sections:
        sents = [dict(zip(("id", "char_start", "char_end"), r)) for r in conn.execute(
            "SELECT id, char_start, char_end FROM sentence WHERE doc_id = ? AND section_id = ? "
            "ORDER BY char_start", (doc_id, sec_id)).fetchall()]
        if sents:
            blocks.append((_with_guidance(f"## {sec_id} — {gist}\n{_section_block(raw, sents)}",
                                          guidance),
                           {s["id"] for s in sents}))
    tasks = [(name, system, block, valid)
             for name, system in coders.items() for (block, valid) in blocks]

    def run_one(task):
        name, system, block, valid = task
        try:
            codes, _ = _parse_codes(llm.chat_json(system, block, label=f"panel:{name}").get("codes", []), valid, doc_id)
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
