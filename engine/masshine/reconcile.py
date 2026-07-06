"""Reconcile / codebook: merge duplicate codes and maintain the project codebook with stable ids.

The model returns only ID groupings; Python applies them (P3: model judges, code aggregates).
Ids are minted from a persistent monotonic counter and never reused.
"""
from __future__ import annotations

import json
import sqlite3
import sys

from . import llm
from .config import PROMPTS


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
        groups = llm.chat_json(system, listing, timeout=timeout, label="reconcile").get("groups", [])
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
        groups = llm.chat_json(system, listing, label="reconcile:incremental").get("groups", [])
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
