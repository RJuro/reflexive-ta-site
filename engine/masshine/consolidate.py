"""Codebook consolidation (P6): ONE LLM call groups the whole codebook into 8–15 code
FAMILIES — the 35–50-unit view a researcher can hold, with the fine codes and their
evidence intact underneath. The model proposes groupings and a semantic ring order;
Python validates and disposes (P3: model judges, code aggregates) — invented or rejected
member ids are dropped, a code claimed by two families stays with the first, and anything
left unplaced is filed under an explicit "Unfiled" family rather than silently lost.

Hue is assigned purely from ring position (`hue = round(360 * position / n_families)`),
so it is deterministic and stable across re-runs that don't reshuffle the order.
"""
from __future__ import annotations

from . import llm
from .config import PROMPTS

GUIDANCE_HEADER = (
    "RESEARCHER FEEDBACK (from reviewing an earlier consolidation of this codebook — take it "
    "into account when grouping: adjust family boundaries, rename, or re-split as directed):"
)


def _with_guidance(listing: str, guidance: str | None) -> str:
    return f"{listing}\n\n{GUIDANCE_HEADER}\n{guidance}" if guidance else listing


def codebook_listing(codes: list[dict]) -> str:
    """One line per ACTIVE code (skip rejected), researcher_label winning over the
    machine label — the exact listing shape the consolidation prompt expects."""
    lines = []
    for c in codes:
        if c.get("status") == "rejected":
            continue
        lbl = c.get("researcher_label") or c["label"]
        lines.append(f'[{c["id"]}] ({c["coder"]}/{c["code_type"]}) "{lbl}" — {c["definition"]}')
    return "\n".join(lines)


def consolidate_codebook(codes: list[dict], guidance: str | None = None) -> list[dict]:
    """Group the codebook into families via ONE LLM call, then validate in Python.

    Returns families in ring order, each:
      {"id": "F01", "label": ..., "definition": ..., "member_code_ids": [...],
       "position": 0, "hue": 0}
    An "Unfiled" family (only if non-empty) is appended last with its own position/hue.
    """
    active_ids = {c["id"] for c in codes if c.get("status") != "rejected"}
    if not active_ids:
        return []

    system = (PROMPTS / "consolidate.prompt").read_text(encoding="utf-8")
    listing = _with_guidance(codebook_listing(codes), guidance)
    data = llm.chat_json(system, listing, label="consolidate")
    proposed = data.get("families", []) or []

    families: list[dict] = []
    claimed: set[str] = set()
    for fam in proposed:
        label = str(fam.get("label", "")).strip()
        definition = str(fam.get("definition", "")).strip()
        members = []
        for cid in fam.get("member_code_ids", []) or []:
            cid = str(cid).strip()
            if cid not in active_ids:   # invented or rejected id — drop
                continue
            if cid in claimed:          # claimed by an earlier family — first claim wins
                continue
            members.append(cid)
        if not members:                 # empty family (all members invalid/duplicate) — drop
            continue
        claimed.update(members)
        families.append({"label": label, "definition": definition, "member_code_ids": members})

    unplaced = sorted(active_ids - claimed)
    if unplaced:
        families.append({
            "label": "Unfiled",
            "definition": "Codes the consolidation pass did not place.",
            "member_code_ids": unplaced,
        })

    n = len(families)
    for i, fam in enumerate(families):
        fam["position"] = i
        fam["hue"] = round(360 * i / n) if n else 0

    return families
