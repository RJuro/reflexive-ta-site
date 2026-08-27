"""READ: the restrained whole-document coding pass (P10.1a) — a NEW parallel path alongside the
existing sectioned coder+critic/panel (coding.py), which stays untouched. One prompt (read.prompt)
carries coder.prompt's quality bar plus a hard 25-code-per-document budget, reuse-before-mint
against a PROJECT CODEBOOK block, optional research-question scoping (`out_of_scope` declines),
and a per-code `uncertainty` flag in place of an always-on rationale.

`span` controls how many LLM calls one document costs: "doc" (one call, the whole transcript) |
"halves" (two calls, split at the section boundary nearest the sentence-count midpoint) |
"groups" (calls over consecutive groups of ~3 sections) | "sections" (one call per section — the
fine-grained fallback the coverage gate reruns at). Same prompt, same validators, same output
contract at every span (data-session-spec.md §5) — a multi-call span just concatenates codes and
merges reuses of the same code_id; there is no cross-call reconcile here (that would defeat the
point of fewer calls, and a false split is what the coverage gate exists to catch).

Python validators are the point of this module (P1/P3: model proposes, Python disposes): evidence
is doc-qualified and ungrounded ids are dropped exactly as coding.py does; the 25-code cap is
enforced as a backstop regardless of prompt compliance; a `reuses` entry must name a code_id that
is actually in the codebook passed to that call and cite grounded sids, or it is dropped.

Persistence here is dumb by design — no LLM, no judgment calls: new codes get fresh ids from
reconcile._next_code_id (the one id sequence every path shares), reused codes get their evidence
unioned in, and declines fold into one assistant memo on the document. It stays a plain function,
not folded into read_document, so the calibration harness can call the read half without ever
touching the database.
"""
from __future__ import annotations

import json
import sqlite3

from . import consolidate, llm, store
from .coding import _norm_type, _section_block
from .config import PROMPTS
# NOT `from . import reconcile`: masshine/__init__.py's compatibility façade does
# `from .reconcile import (..., reconcile, ...)`, which rebinds the PACKAGE attribute
# `masshine.reconcile` to that function — `from . import reconcile` anywhere else would then
# silently hand back the function, not the module. Import the one name this module needs
# directly, the same way seed.py reaches into reconcile/ingest for their private helpers.
from .reconcile import _next_code_id

READ_CODE_CAP = 25
GROUP_SIZE = 3


# ---- span slicing (pure — no DB) -------------------------------------------------------------

def _split_halves(section_sentence_counts: list[tuple[str, int]]) -> list[list[str]]:
    """Section ids in two groups, split at the section boundary nearest the sentence-count
    midpoint (a section is never split). One or zero sections -> a single group."""
    ids = [sid for sid, _ in section_sentence_counts]
    if len(ids) <= 1:
        return [ids] if ids else []
    counts = [n for _, n in section_sentence_counts]
    target = sum(counts) / 2
    running = 0
    best_i, best_diff = 1, None
    for i, n in enumerate(counts, start=1):
        running += n
        diff = abs(running - target)
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    best_i = min(max(best_i, 1), len(ids) - 1)  # keep both halves non-empty
    return [ids[:best_i], ids[best_i:]]


def slices_for_span(section_sentence_counts: list[tuple[str, int]], span: str) -> list[list[str]]:
    """[[section_id, ...], ...] — one inner list per READ call this span issues, in document
    order. `section_sentence_counts` is [(section_id, n_sentences), ...] in document order (the
    sentence counts only matter for "halves"; the other spans only need the ids)."""
    ids = [sid for sid, _ in section_sentence_counts]
    if span == "doc":
        return [ids] if ids else []
    if span == "halves":
        return _split_halves(section_sentence_counts)
    if span == "groups":
        return [ids[i:i + GROUP_SIZE] for i in range(0, len(ids), GROUP_SIZE)]
    if span == "sections":
        return [[sid] for sid in ids]
    raise ValueError(f"unknown span {span!r} — choose from doc | halves | groups | sections")


# ---- pure decile math (shared by the coverage gate and the calibration harness) ---------------

def decile_buckets(char_starts: list[int], doc_len: int) -> list[int]:
    """Bucket i (0..9) holds citations whose char position falls in the doc's i-th tenth of
    length. Pure — no DB, no LLM — so this is where the coverage-gate math actually lives and
    is tested; `citation_deciles` below and tools/read_span_calibrate.py both just gather
    positions and call this."""
    if doc_len <= 0:
        return [0] * 10
    out = [0] * 10
    for cs in char_starts:
        out[min(9, max(0, int(cs / doc_len * 10)))] += 1
    return out


def first30_share(deciles: list[int]) -> float:
    """Share of citations in the first 3 deciles (0.0-1.0); 0.0 when there are no citations."""
    total = sum(deciles)
    return (sum(deciles[:3]) / total) if total else 0.0


def citation_deciles(conn: sqlite3.Connection, doc_id: str) -> list[int]:
    """Decile histogram of every CURRENT citation into `doc_id`'s sentences, across the whole
    `code` table — a reused code's citation into this doc counts too, not just codes this run
    minted. This is the lost-in-the-middle guard's own metric (jobs.read_work's coverage gate):
    doc length = max(char_end) over the doc's sentences; a citation's position = its sentence's
    char_start."""
    doc_len = conn.execute(
        "SELECT MAX(char_end) FROM sentence WHERE doc_id=?", (doc_id,)).fetchone()[0] or 0
    starts = dict(conn.execute(
        "SELECT id, char_start FROM sentence WHERE doc_id=?", (doc_id,)))
    prefix = f"{doc_id}#"
    positions = []
    for (ev_json,) in conn.execute("SELECT evidence_ids FROM code"):
        for ev in json.loads(ev_json or "[]"):
            if ev.startswith(prefix):
                cs = starts.get(ev[len(prefix):])
                if cs is not None:
                    positions.append(cs)
    return decile_buckets(positions, doc_len)


# ---- user message: numbered transcript slice + codebook block + RQ line -----------------------

def _transcript_block(conn: sqlite3.Connection, doc_id: str, raw: str,
                      section_ids: list[str]) -> str:
    """One '## {sec_id} — {gist}' + sid-prefixed sentence block per section in the slice — the
    same shape coding.code_sections builds per section (coding._section_block), concatenated
    across however many sections this READ call covers."""
    wanted = set(section_ids)
    blocks = []
    for sec_id, gist in conn.execute(
            "SELECT id, gist FROM section WHERE doc_id=? ORDER BY char_start", (doc_id,)):
        if sec_id not in wanted:
            continue
        sents = [dict(zip(("id", "char_start", "char_end"), r)) for r in conn.execute(
            "SELECT id, char_start, char_end FROM sentence WHERE doc_id=? AND section_id=? "
            "ORDER BY char_start", (doc_id, sec_id)).fetchall()]
        if sents:
            blocks.append(f"## {sec_id} — {gist}\n{_section_block(raw, sents)}")
    return "\n\n".join(blocks)


def codebook_block(codes: list[dict]) -> str:
    """PROJECT CODEBOOK block for a READ call: one line per active code (skip rejected/merged,
    researcher_label wins — reuses consolidate.codebook_listing's line shape), with the
    researcher's own codes (coder='researcher') listed FIRST under their own heading — reuse
    before mint applies to a human's categories first. "" when there is nothing active to show
    (the caller omits the block entirely)."""
    active = [c for c in codes if c.get("status") not in ("rejected", "merged")]
    if not active:
        return ""
    researcher = [c for c in active if c.get("coder") == "researcher"]
    rest = [c for c in active if c.get("coder") != "researcher"]
    lines = ['PROJECT CODEBOOK — reuse an existing code (cite its id under "reuses") before '
             "minting a new one that says nearly the same thing:"]
    if researcher:
        lines.append("")
        lines.append("the researcher's own codes:")
        lines.append(consolidate.codebook_listing(researcher))
    if rest:
        lines.append("")
        lines.append(consolidate.codebook_listing(rest))
    return "\n".join(lines)


def build_user_message(conn: sqlite3.Connection, doc_id: str, raw: str, section_ids: list[str],
                       codebook: list[dict], research_question: str | None = None) -> str:
    parts = [_transcript_block(conn, doc_id, raw, section_ids)]
    cb = codebook_block(codebook)
    if cb:
        parts.append(cb)
    if research_question:
        parts.append(f"RESEARCH QUESTION: {research_question.strip()}")
    return "\n\n".join(p for p in parts if p)


# ---- validators (P1/P3: model proposes, Python disposes) --------------------------------------

def _parse_codes(items, valid: set[str], doc_id: str) -> tuple[list[dict], int]:
    """Doc-qualify + drop ungrounded evidence (P1) — the same discipline as
    coding._parse_codes, minus its always-on `rationale` (READ carries explanatory text only
    via `uncertainty` — see read.prompt)."""
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
            "uncertainty": str(c.get("uncertainty", "")).strip(),
        })
    return out, drop


def _parse_reuses(items, valid: set[str], doc_id: str,
                  codebook_ids: set[str]) -> tuple[list[dict], int]:
    """A reuse must name a code_id actually in the codebook passed to this call and cite at
    least one grounded sid; anything else is dropped and counted (invented ids, sids outside
    this slice, or a reuse left with zero evidence after grounding)."""
    out, dropped = [], 0
    for r in items or []:
        cid = str(r.get("code_id", "")).strip()
        ev_raw = [str(x).strip() for x in r.get("evidence_sentence_ids", [])]
        ev = [f"{doc_id}#{x}" for x in ev_raw if x in valid]
        if cid not in codebook_ids or not ev:
            dropped += 1
            continue
        out.append({"code_id": cid, "evidence": ev})
    return out, dropped


def _parse_out_of_scope(items, valid: set[str], doc_id: str) -> tuple[list[dict], int]:
    """Ground out_of_scope sentence ids the same way as code evidence; an entry left with zero
    grounded ids is dropped (counted once)."""
    out, dropped = [], 0
    for d in items or []:
        raw_ids = [str(x).strip() for x in d.get("sentence_ids", [])]
        sids = [f"{doc_id}#{x}" for x in raw_ids if x in valid]
        if not sids:
            dropped += 1
            continue
        out.append({"sentence_ids": sids, "reason": str(d.get("reason", "")).strip()})
    return out, dropped


# ---- the READ call(s) ---------------------------------------------------------------------------

def read_document(conn: sqlite3.Connection, doc_id: str, span: str = "doc",
                  research_question: str | None = None
                  ) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Read one document (or re-read it at a different span) → (codes, reuses, declines,
    drop_stats). The codebook is frozen once, at the start of this call, from the CURRENT `code`
    table — every slice of a multi-call span sees the full codebook (reuse-before-mint needs the
    whole book, not just neighboring slices), never just the current slice's sentences.

    codes    — [{"label","definition","code_type","evidence","uncertainty"}], evidence doc-
               qualified, capped at READ_CODE_CAP total across every call this span issued.
    reuses   — [{"code_id","evidence"}], evidence doc-qualified, deduplicated per code_id
               (order-preserving) across calls.
    declines — [{"sentence_ids","reason"}], sentence_ids doc-qualified.
    drop_stats — what Python threw away: ungrounded_evidence / over_cap / invalid_reuses /
               ungrounded_out_of_scope counts.
    """
    system = (PROMPTS / "read.prompt").read_text(encoding="utf-8")
    raw = conn.execute("SELECT text FROM document WHERE id=?", (doc_id,)).fetchone()[0]
    counts = [(sec_id, conn.execute(
        "SELECT COUNT(*) FROM sentence WHERE doc_id=? AND section_id=?",
        (doc_id, sec_id)).fetchone()[0])
        for (sec_id,) in conn.execute(
            "SELECT id FROM section WHERE doc_id=? ORDER BY char_start", (doc_id,))]
    slices = slices_for_span(counts, span)

    codebook = store.codes_payload(conn)
    codebook_ids = {c["id"] for c in codebook if c.get("status") not in ("rejected", "merged")}

    all_codes: list[dict] = []
    reuse_ev: dict[str, list[str]] = {}
    declines: list[dict] = []
    drop = {"ungrounded_evidence": 0, "over_cap": 0, "invalid_reuses": 0,
           "ungrounded_out_of_scope": 0}

    for section_ids in slices:
        valid: set[str] = set()
        for sec_id in section_ids:
            valid |= {sid for (sid,) in conn.execute(
                "SELECT id FROM sentence WHERE doc_id=? AND section_id=?", (doc_id, sec_id))}
        user = build_user_message(conn, doc_id, raw, section_ids, codebook, research_question)
        data = llm.chat_json(system, user, label="read")

        codes, ev_dropped = _parse_codes(data.get("codes", []), valid, doc_id)
        all_codes.extend(codes)
        drop["ungrounded_evidence"] += ev_dropped

        reuses, invalid = _parse_reuses(data.get("reuses", []), valid, doc_id, codebook_ids)
        drop["invalid_reuses"] += invalid
        for r in reuses:
            bucket = reuse_ev.setdefault(r["code_id"], [])
            for sid in r["evidence"]:
                if sid not in bucket:
                    bucket.append(sid)

        decl, oos_dropped = _parse_out_of_scope(data.get("out_of_scope", []), valid, doc_id)
        drop["ungrounded_out_of_scope"] += oos_dropped
        declines.extend(decl)

    if len(all_codes) > READ_CODE_CAP:
        drop["over_cap"] = len(all_codes) - READ_CODE_CAP
        all_codes = all_codes[:READ_CODE_CAP]

    reuses_out = [{"code_id": cid, "evidence": ev} for cid, ev in reuse_ev.items()]
    return all_codes, reuses_out, declines, drop


# ---- persistence (no LLM — pure disposal) ------------------------------------------------------

def persist_read(conn: sqlite3.Connection, run_id: str, doc_id: str, codes: list[dict],
                 reuses: list[dict], declines: list[dict]) -> dict:
    """Write READ's already-validated output. New codes get fresh ids from the one shared id
    sequence (reconcile._next_code_id) and coder='standard' — mirroring the row shape
    reconcile._write_codebook produces, just inserted incrementally instead of rewriting the
    whole table. A reuse unions its new sids into the existing code's evidence (de-duplicated,
    order-preserving). Declines become ONE assistant memo on the document, replacing any earlier
    READ run's decline memo for this doc; a run that declines nothing leaves an earlier memo
    alone rather than clearing it.

    `uncertainty` rides into the existing `model_rationale` column — no schema bump: it is
    already "the model's note", and for a READ-origin code the note IS the uncertainty clause.
    """
    for c in codes:
        cid = _next_code_id(conn)
        conn.execute(
            "INSERT INTO code (id, origin_doc_id, run_id, label, definition, code_type, "
            "evidence_ids, model_rationale, coder) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, doc_id, run_id, c["label"], c["definition"], c["code_type"],
             json.dumps(c["evidence"]), c.get("uncertainty", ""), "standard"))
    for r in reuses:
        row = conn.execute("SELECT evidence_ids FROM code WHERE id=?", (r["code_id"],)).fetchone()
        if not row:
            continue  # codebook drifted between validation and persistence — skip defensively
        existing = json.loads(row[0] or "[]")
        merged = existing + [e for e in r["evidence"] if e not in existing]
        conn.execute("UPDATE code SET evidence_ids=? WHERE id=?", (json.dumps(merged), r["code_id"]))
    if declines:
        lines = [f"Declined as out of scope ({', '.join(d['sentence_ids'])}): {d['reason']}"
                for d in declines]
        store.set_memo(conn, "document", doc_id, "\n".join(lines),
                       {"n_ranges": len(declines)}, author="assistant")
    conn.commit()
    return {"n_new": len(codes), "n_reused": len(reuses), "n_declined": len(declines)}
