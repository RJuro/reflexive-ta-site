"""Theming: legacy codebook-only pass + the primary sequential, transcript-grounded walk.

The sequential theorist sees the actual TRANSCRIPT (not just code labels) and builds themes ONE
interview at a time: each step gets the prior themes + this interview's text + this interview's
codes, and returns the full updated theme set. Coverage rises as a theme recurs across interviews;
claim scope is forced to match coverage (computed from evidence in Python, not trusted to the model).
"""
from __future__ import annotations

import json
import sqlite3
import sys

from . import llm
from .config import PROMPTS
from .reconcile import load_codebook


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
        themes = llm.chat_json(system, _codebook_listing(codebook), label="theorist:codebook").get("themes", [])
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
        tag = f'"{t["label"]}" — ' if t.get("label") else ""
        lines.append(f'[{t["id"]}] {tag}{t["central_concept"]}  · supporting: '
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
        # P7: label is a short scannable title, separate from the central_concept claim. On a
        # revision, a fresh non-empty label wins (the claim sharpened); otherwise the prior
        # theme's label is kept — mirrors how every other field accumulates across steps.
        new_label = str(t.get("label", "")).strip()
        label = new_label or (prior.get("label", "") if prior else "")
        theme = {
            "id": tid, "label": label,
            "central_concept": str(t.get("central_concept", "")).strip(),
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


THEME_GUIDANCE_HEADER = (
    "RESEARCHER FEEDBACK (from reviewing an earlier theme catalogue — take it into account when "
    "constructing and revising themes. Grounding rules still apply: support only from real code "
    "ids, anchors only from real sentence ids):"
)


def theorize_walk(doc_order: list[str], doc_codes_map: dict, all_codes: dict,
                  transcripts: dict, valid_sents_map: dict, origin: dict | None = None,
                  timeout: float | None = None, raw_cache: dict | None = None,
                  save_raw=None, guidance: str | None = None) -> tuple[list[dict], list[tuple], list[str]]:
    """Walk interviews in order, building themes incrementally (one LLM call per interview). DB-free:
    the caller supplies each interview's transcript block + valid sentence ids. Returns
    (final_themes, snapshots, failures) where snapshots[i] = (doc_id, themes-after-that-interview)
    and `failures` lists any interviews whose theme step errored (e.g. timed out) and so were NOT
    integrated — a dropped interview must be LOUD, never silently swallowed into a clean-looking
    output. Calls stream with an IDLE timeout (no cap on total duration — see llm.chat_json), so the
    theorist's long thinking no longer trips a per-call ceiling; the step uses no retry, and a genuine
    hang (idle silence) still fails loudly and resumes on re-run.

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
            if guidance:
                user += f"\n\n{THEME_GUIDANCE_HEADER}\n{guidance}"
            try:
                raw = llm.chat_json(system, user, timeout=timeout, retries=0, label=f"theorist:step{i}").get("themes", [])
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
                                valid_sents_map: dict, timeout: float | None = None,
                                raw_cache: dict | None = None, save_raw=None,
                                guidance: str | None = None
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
                                         raw_cache=raw_cache, save_raw=save_raw,
                                         guidance=guidance)
    return themes, project_codebook, snaps, fails


def theorize_panel_sequential(doc_order: list[str], panel_by_doc: dict, transcripts: dict,
                              valid_sents_map: dict, timeout: float | None = None,
                              raw_cache: dict | None = None, save_raw=None,
                              guidance: str | None = None
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
                                         raw_cache=raw_cache, save_raw=save_raw,
                                         guidance=guidance)
    return themes, codebook, origin, snaps, fails
