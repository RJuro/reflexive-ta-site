"""SYNTHESIZE: one call per document, after READ (design/P10.2-CONTRACT.md §3) — mirrors read.py's
shape (input assembly → one `llm.chat_json` call → Python validates and disposes) but produces the
whole session/journal update in one pass: finding updates, typed walkthrough steps, check-backs on
standing researcher steers, residue (what nothing claims), the document introduction, the revised
story-so-far, and an optional focus proposal.

**Findings are theme_v2 rows, not a new table** (contract §2's own instruction; see store.py's
"P10.2: findings" section for the full rationale) — this module's job is to decide, in Python,
which theme_v2 row a model-returned finding updates (echoed id) versus mints (fresh id, handed out
by store.upsert_finding at persist time), and to ACCUMULATE support across documents rather than
trust a revision to re-list its own earlier evidence — exactly themes.py's `_resolve_step_themes`
discipline, carried over because it already solved this problem once.

Python validators are the point of this module, same philosophy as read.py: every sid must
resolve to a real sentence (this document's own numbering; the story additionally accepts an
already-qualified `doc#sent` anchor carried over from an earlier document, since the story-so-far
spans the whole project — see `_resolve_paragraphs`); every code/finding id a step or check-back
references must already exist; a check-back whose supports/strains/not_found are all empty is
dropped (contract §5.2); a finding left with zero grounded supporting codes is not a finding.
**Standing is never read from the model** — a model-supplied "standing"/"standing_note" key on a
finding is silently ignored by construction (`_resolve_finding` only ever copies known fields);
the real standing is computed afterward, from the accumulated evidence, by
store.recompute_finding_state (contract §3, §5.1).

Residue's candidate set (codes no finding has claimed, sentences no code touches) is computed in
PYTHON and handed to the model as input — the model only writes the note and the reframe offer,
never decides what counts as unclaimed (contract §3's "compute the candidate set in Python").
"""
from __future__ import annotations

import sqlite3

from . import llm, store, themes
from .config import PROMPTS

STEP_KINDS = {"pattern", "tension", "uncertainty", "delta", "declined"}


# ---- input assembly: transcript + codes + findings + focus + story + residue candidates + guidance

def _codes_touching(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """Active codes whose evidence touches `doc_id` — the same cross-cutting filter
    theorize_project_sequential uses for a document's code slice (evidence-based, not
    `origin_doc_id`-based, so a code reused from an earlier document still counts)."""
    prefix = f"{doc_id}#"
    return [c for c in store.codes_payload(conn)
            if c.get("status") not in ("rejected", "merged")
            and any(ev.startswith(prefix) for ev in c["evidence"])]


def _codes_block(doc_codes: list[dict]) -> str:
    return themes._theorist_codes_block([(c["id"], c) for c in doc_codes])


def _findings_block(findings: list[dict]) -> str:
    if not findings:
        return "CURRENT FINDINGS: none yet — this is the first document synthesized."
    lines = ["CURRENT FINDINGS:"]
    for f in findings:
        lbl = f.get("label") or f["central_concept"]
        lines.append(f'[{f["id"]}] "{lbl}" — {f["central_concept"]} · supporting codes: '
                     f'{", ".join(f.get("supporting_code_ids", [])) or "none"}')
    return "\n".join(lines)


def _focus_block(conn: sqlite3.Connection) -> str:
    focus = store.active_focus(conn)
    if not focus:
        return ""
    lines = [f'RESEARCH FOCUS: {focus["text"]}']
    history = store.focus_history(conn)
    if len(history) > 1:
        lines.append(f'(previously: "{history[-2]["text"]}")')
    return "\n".join(lines)


def _story_block(conn: sqlite3.Connection) -> str:
    story = store.latest_story(conn)
    if not story["paras"]:
        return "STORY SO FAR: none yet — this is the first document."
    lines = ["STORY SO FAR (existing anchors shown — echo one back verbatim to keep a paragraph "
            "unchanged; a paragraph with no sids surviving validation is dropped):"]
    for p in story["paras"]:
        lines.append(f'{p["para"]} [{", ".join(p.get("sids", []))}]')
    return "\n".join(lines)


def _residue_candidates_block(unclaimed_codes: list[dict], uncoded_sids: list[str]) -> str:
    if not unclaimed_codes and not uncoded_sids:
        return ""
    lines = ["UNCLAIMED MATERIAL — nothing above has taken these up; note them under \"residue\" "
            "only where genuinely analytically interesting, not merely because they are unused:"]
    if unclaimed_codes:
        lines.append("codes no finding claims: " + ", ".join(
            f'[{c["id"]}] "{c.get("researcher_label") or c["label"]}"' for c in unclaimed_codes))
    if uncoded_sids:
        lines.append("sentence ids no code touches: " + ", ".join(uncoded_sids))
    return "\n".join(lines)


def _guidance_block(conn: sqlite3.Connection, doc_id: str, mode: str) -> str:
    """Doc-scoped comments/revisions (read.py's half of compile_guidance) PLUS project-scoped
    theme comments/theme_revisions (theme_work's half) — SYNTHESIZE needs both at once;
    compile_guidance only ever gives one half per call by design (READ only ever needed the
    doc-scoped half). ponytail: calling it twice means store._p10_2_guidance_lines' tail (focus/
    declines/reactions) rides along on both calls — deduplicated below so the model never sees
    the same guidance line twice; add a real `include_p10_2=` toggle if this ever needs more."""
    seen: set[str] = set()
    merged: list[str] = []
    for block in (store.compile_guidance(conn, doc_id=doc_id),
                 store.compile_guidance(conn, mode=mode)):
        for line in block.splitlines():
            if line not in seen:
                seen.add(line)
                merged.append(line)
    return "\n".join(merged)


def build_user_message(conn: sqlite3.Connection, doc_id: str, doc_codes: list[dict],
                       findings: list[dict], unclaimed_codes: list[dict],
                       uncoded_sids: list[str], guidance: str) -> str:
    parts = [
        f"TRANSCRIPT (document {doc_id}):\n{themes._doc_transcript_block(conn, doc_id)}",
        f"THIS DOCUMENT'S CODES:\n{_codes_block(doc_codes)}" if doc_codes else "",
        _findings_block(findings),
        _focus_block(conn),
        _story_block(conn),
        _residue_candidates_block(unclaimed_codes, uncoded_sids),
    ]
    if guidance:
        parts.append(f"RESEARCHER GUIDANCE (steers, reactions, reframes, declines):\n{guidance}")
    return "\n\n".join(p for p in parts if p)


# ---- validators (P1/P3: model proposes, Python disposes) --------------------------------------

def _ids(raw) -> list[str]:
    """Model output is untrusted SHAPE as well as untrusted content: a field documented as a list
    of ids comes back, in practice, as a list of rich objects ({"code_id": ..., "note": ...}) about
    as often as as a list of strings. Coerce both to plain ids and drop anything else, rather than
    letting a dict reach a set membership test (which is how a real run died: TypeError:
    unhashable type: 'dict'). Same P3 discipline as everywhere else — the model proposes, and
    Python decides what that even was."""
    out = []
    for x in raw or []:
        if isinstance(x, str):
            v = x.strip()
        elif isinstance(x, dict):
            v = str(x.get("id") or x.get("code_id") or x.get("sentence_id")
                    or x.get("sid") or "").strip()
        else:
            v = ""
        if v:
            out.append(v)
    return out


def _resolve_finding(item: dict, rid: str, prior: dict | None, valid_code_ids: set[str],
                     valid_sents: set[str], doc_id: str) -> dict | None:
    """Merge one model-returned finding against its PRIOR persisted state (ACCUMULATE, never
    trust a revision to re-list its own earlier support — themes._resolve_step_themes's
    discipline). None if the finding ends up with zero grounded supporting codes (not a finding).
    A model-supplied "standing"/"standing_note" is never read — only these five keys are ever
    copied out of `item`, and standing is computed later from the `supporting_code_ids` this
    returns (contract §5.1)."""
    new_sup = [c for c in _ids(item.get("supporting_code_ids")) if c in valid_code_ids]
    sup = list(dict.fromkeys((prior["supporting_code_ids"] if prior else []) + new_sup))
    if not sup:
        return None
    kev = []
    for s in _ids(item.get("key_evidence_sentence_ids")):
        if s in valid_sents:
            kev.append(f"{doc_id}#{s}")
    kev = list(dict.fromkeys((prior["key_evidence_sentence_ids"] if prior else []) + kev))
    new_tens = [c for c in _ids(item.get("tensions")) if c in valid_code_ids]
    tensions = list(dict.fromkeys((prior["tensions"] if prior else []) + new_tens))
    label = str(item.get("label", "")).strip() or (prior["label"] if prior else "")
    central = (str(item.get("central_concept", "")).strip()
              or (prior["central_concept"] if prior else ""))
    return {"id": rid, "label": label, "central_concept": central, "supporting_code_ids": sup,
            "key_evidence_sentence_ids": kev, "tensions": tensions}


def resolve_findings(items: list, prior: list[dict], valid_code_ids: set[str],
                     valid_sents: set[str], doc_id: str) -> list[dict]:
    """[{"id","label","central_concept","supporting_code_ids","key_evidence_sentence_ids",
    "tensions"}], `id` == "" for a brand-new finding (store.upsert_finding mints it at persist
    time). An echoed id that doesn't match a real prior finding — or repeats one already claimed
    earlier in THIS same call — is treated as a new finding rather than trusted blindly."""
    prior_by_id = {f["id"]: f for f in prior}
    out, used = [], set()
    for item in items or []:
        rid = str(item.get("id", "")).strip()
        if rid in used or rid not in prior_by_id:
            rid = ""
        resolved = _resolve_finding(item, rid, prior_by_id.get(rid), valid_code_ids,
                                    valid_sents, doc_id)
        if resolved is None:
            continue
        if resolved["id"]:
            used.add(resolved["id"])
        out.append(resolved)
    return out


def _resolve_checkback(item: dict, valid_finding_ids: set[str], valid_sents: set[str],
                       doc_id: str) -> dict | None:
    """None if `target` isn't a real finding, or if supports/strains/not_found are ALL empty —
    the contract's own definition of an invalid check-back (§5.2): a steer the material cannot
    speak to either way is not silently dropped elsewhere in the pipeline, but a check-back that
    says NOTHING about it (no support text/sids, no strain text/sids, no not-found note) is not a
    check-back at all."""
    target = str(item.get("target", "")).strip()
    if target not in valid_finding_ids:
        return None

    def _block(key: str) -> dict:
        b = item.get(key) or {}
        text = str(b.get("text", "")).strip()
        sids = [f"{doc_id}#{s}" for s in _ids(b.get("sids")) if s in valid_sents]
        return {"text": text, "sids": sids}

    supports, strains = _block("supports"), _block("strains")
    not_found_text = str((item.get("not_found") or {}).get("text", "")).strip()
    if not (supports["text"] or supports["sids"] or strains["text"] or strains["sids"]
           or not_found_text):
        return None
    return {"steer": str(item.get("steer", "")).strip(), "target": target,
            "supports": supports, "strains": strains, "not_found": {"text": not_found_text},
            "proposal": str(item.get("proposal", "")).strip()}


def _resolve_residue(item: dict, valid_code_ids: set[str], valid_sents: set[str],
                     doc_id: str) -> dict | None:
    """None if nothing survives grounding — a residue note with no real sids and no real code ids
    points at nothing."""
    sids = [f"{doc_id}#{s}" for s in _ids(item.get("sids")) if s in valid_sents]
    code_ids = [c for c in _ids(item.get("code_ids")) if c in valid_code_ids]
    if not sids and not code_ids:
        return None
    return {"note": str(item.get("note", "")).strip(), "sids": sids, "code_ids": code_ids,
            "reframe_offer": str(item.get("reframe_offer", "")).strip()}


def _resolve_steps(items: list, valid_code_ids: set[str], valid_sents: set[str], doc_id: str,
                   valid_finding_ids: set[str]) -> list[dict]:
    """A step needs a statement AND at least one grounded sid to exist at all — an ungrounded
    walkthrough claim is exactly what the grounding gate exists to catch. `finding_id` is nulled
    (not fatal to the step) when it names a finding that isn't real — see the module docstring on
    why a finding minted fresh in this SAME call can never be a valid target yet."""
    out = []
    for item in items or []:
        kind = str(item.get("kind", "")).strip()
        if kind not in STEP_KINDS:
            continue
        statement = str(item.get("statement", "")).strip()
        sids = [f"{doc_id}#{s}" for s in _ids(item.get("sids")) if s in valid_sents]
        if not statement or not sids:
            continue
        code_ids = [c for c in _ids(item.get("code_ids")) if c in valid_code_ids]
        weakest = [f"{doc_id}#{s}" for s in _ids(item.get("weakest_sids"))
                  if s in valid_sents]
        finding_id = item.get("finding_id")
        if finding_id is not None and str(finding_id) not in valid_finding_ids:
            finding_id = None
        out.append({"kind": kind, "statement": statement, "sids": sids, "code_ids": code_ids,
                    "weakest_sids": weakest, "finding_id": finding_id})
    return out


def _resolve_paragraphs(items: list, valid_sents: set[str], doc_id: str,
                        valid_qualified: set[str] | None = None) -> list[dict]:
    """A paragraph whose sids ALL fail grounding is dropped (contract §3). A bare id qualifies
    against THIS document; an already-qualified `doc#sent` id (the story-so-far carrying an
    anchor forward from an earlier document — see `_story_block`) is accepted only against
    `valid_qualified`, a project-wide set — this is what lets the story keep citing earlier
    documents without the model having to fabricate ids for text it cannot see this call."""
    out = []
    for item in items or []:
        para = str(item.get("para", "")).strip()
        if not para:
            continue
        sids = []
        for s in _ids(item.get("sids")):
            if s in valid_sents:
                sids.append(f"{doc_id}#{s}")
            elif valid_qualified is not None and s in valid_qualified:
                sids.append(s)
        if not sids:
            continue
        out.append({"para": para, "sids": sids})
    return out


def _resolve_focus_proposal(fp) -> dict | None:
    if not isinstance(fp, dict):
        return None
    text = str(fp.get("text", "")).strip()
    if not text:
        return None
    return {"text": text, "rationale": str(fp.get("rationale", "")).strip()}


# ---- the SYNTHESIZE call -------------------------------------------------------------------------

def synthesize_document(conn: sqlite3.Connection, doc_id: str, mode: str) -> dict:
    """One document's SYNTHESIZE call → the fully validated result, ready for `persist_synthesis`.
    Mirrors read.read_document's shape: assemble input from the current DB state, one
    `llm.chat_json` call, Python validates and disposes."""
    system = (PROMPTS / "synthesize.prompt").read_text(encoding="utf-8")
    valid_sents = {sid for (sid,) in conn.execute(
        "SELECT id FROM sentence WHERE doc_id=?", (doc_id,))}
    valid_qualified = {f"{d}#{s}" for d, s in conn.execute("SELECT doc_id, id FROM sentence")}
    codes = store.codes_payload(conn)
    valid_code_ids = {c["id"] for c in codes if c.get("status") not in ("rejected", "merged")}
    doc_codes = _codes_touching(conn, doc_id)

    prior_findings = store.findings_for_mode(conn, mode)
    prior_by_id = {f["id"]: f for f in prior_findings}
    claimed = {cid for f in prior_findings for cid in f.get("supporting_code_ids", [])}
    unclaimed_codes = [c for c in doc_codes if c["id"] not in claimed]
    prefix = f"{doc_id}#"
    covered = {ev[len(prefix):] for c in doc_codes for ev in c["evidence"] if ev.startswith(prefix)}
    uncoded_sids = sorted(valid_sents - covered)

    guidance = _guidance_block(conn, doc_id, mode)
    user = build_user_message(conn, doc_id, doc_codes, prior_findings, unclaimed_codes,
                              uncoded_sids, guidance)
    data = llm.chat_json(system, user, label="synthesize")

    findings = resolve_findings(data.get("findings", []), prior_findings, valid_code_ids,
                               valid_sents, doc_id)
    # valid check-back/step finding targets: prior ids plus ids ECHOED (not freshly minted) this
    # call — a finding introduced for the first time this call has no stable id until persistence
    # mints one, so nothing in this same call can reference it yet (see the module docstring).
    valid_finding_ids = set(prior_by_id) | {f["id"] for f in findings if f["id"]}

    checkbacks = [cb for cb in (
        _resolve_checkback(item, valid_finding_ids, valid_sents, doc_id)
        for item in data.get("checkbacks", []) or []) if cb]
    residue = [r for r in (
        _resolve_residue(item, valid_code_ids, valid_sents, doc_id)
        for item in data.get("residue", []) or []) if r]
    steps = _resolve_steps(data.get("steps", []), valid_code_ids, valid_sents, doc_id,
                           valid_finding_ids)
    intro = _resolve_paragraphs(data.get("intro", []), valid_sents, doc_id)
    story = _resolve_paragraphs(data.get("story", []), valid_sents, doc_id, valid_qualified)
    focus_proposal = _resolve_focus_proposal(data.get("focus_proposal"))

    return {"findings": findings, "checkbacks": checkbacks, "residue": residue, "steps": steps,
            "intro": intro, "story": story, "focus_proposal": focus_proposal}


# ---- persistence (no LLM — pure disposal) ------------------------------------------------------

def persist_synthesis(conn: sqlite3.Connection, mode: str, doc_id: str, data: dict) -> dict:
    """Write one document's already-validated SYNTHESIZE output. Findings are UPSERTED one at a
    time (never wholesale-replaced — store.upsert_finding, not persist_themes); steps/check-backs/
    residue each become a `step` row (kind carries which); intro replaces this document's intro
    (idempotent on a re-run); story becomes a NEW story_version (contract: "versioned per document
    position" — never overwritten); a focus_proposal mints a 'proposed' focus_version row.
    finding_state is recomputed for EVERY finding under `mode` afterward, not only the ones this
    document's findings list touched — jobs.synthesize_work's contract, and cheap regardless
    (recompute is a handful of SELECTs, no LLM)."""
    for f in data["findings"]:
        store.upsert_finding(conn, mode, f)
    for s in data["steps"]:
        store.insert_step(conn, mode, doc_id, s["kind"],
                          {"statement": s["statement"], "sids": s["sids"],
                           "code_ids": s["code_ids"], "weakest_sids": s["weakest_sids"],
                           "finding_id": s["finding_id"]})
    for cb in data["checkbacks"]:
        store.insert_step(conn, mode, doc_id, "checkback", cb)
    for r in data["residue"]:
        store.insert_step(conn, mode, doc_id, "residue", r)
    if data["intro"]:
        store.set_intro(conn, doc_id, data["intro"])
    if data["story"]:
        store.add_story_version(conn, data["story"])
    if data["focus_proposal"]:
        store.propose_focus(conn, data["focus_proposal"]["text"],
                            data["focus_proposal"]["rationale"])
    for (fid,) in conn.execute("SELECT id FROM theme_v2 WHERE mode=?", (mode,)):
        store.recompute_finding_state(conn, mode, fid)
    conn.commit()
    return {"n_findings": len(data["findings"]), "n_steps": len(data["steps"]),
            "n_checkbacks": len(data["checkbacks"]), "n_residue": len(data["residue"]),
            "has_intro": bool(data["intro"]), "has_story": bool(data["story"]),
            "focus_proposed": bool(data["focus_proposal"])}
