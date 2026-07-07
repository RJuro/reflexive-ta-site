"""The compress pass (P8a): the actual codebook COLLAPSE, as opposed to consolidation's mere
ORGANIZING into families. One LLM call per family (>= COMPRESS_MIN_FAMILY_CODES active codes),
plus one extra call for the no-family "batch" if it has enough orphaned codes, proposes
within-batch merge groups: sets of codes making the same analytic claim, one of which (the
survivor) could absorb the rest. Python validates and disposes (P3: model judges, code
aggregates) — everything returned here is a PROPOSAL, stored pending in `merge_proposal`;
nothing is merged until a researcher accepts it (api.py's accept/dismiss endpoints, store.py's
persist_merge_proposals). The machine proposes, the researcher disposes.
"""
from __future__ import annotations

from . import llm
from .config import PROMPTS

# A family (or the no-family batch) needs at least this many ACTIVE codes before a compress call
# is worth making — below this, there isn't enough redundancy risk to justify a model call.
COMPRESS_MIN_FAMILY_CODES = 4


def _batch_listing(codes: list[dict]) -> str:
    """One line per code: [C0001] "label" — definition · N excerpts. researcher_label wins."""
    lines = []
    for c in codes:
        lbl = c.get("researcher_label") or c["label"]
        n = len(c.get("evidence") or [])
        lines.append(f'[{c["id"]}] "{lbl}" — {c["definition"]} · {n} excerpt{"s" if n != 1 else ""}')
    return "\n".join(lines)


def _validate_merges(proposed: list[dict], active_ids: set[str],
                     family_id: str | None) -> list[dict]:
    """Drop invented ids, self-merges, and ids reused across groups within this batch (first
    claim wins — same discipline as consolidate's family validator). Empty absorbed_ids after
    cleaning drops the whole group."""
    out = []
    claimed: set[str] = set()
    for m in proposed:
        survivor = str(m.get("survivor_id", "")).strip()
        if survivor not in active_ids or survivor in claimed:
            continue
        absorbed = []
        for aid in m.get("absorbed_ids", []) or []:
            aid = str(aid).strip()
            if aid not in active_ids:        # invented/unknown id — drop
                continue
            if aid == survivor:              # survivor listed as its own absorbed — drop
                continue
            if aid in claimed:                # already claimed by an earlier group or as a survivor
                continue
            absorbed.append(aid)
        if not absorbed:
            continue
        claimed.add(survivor)
        claimed.update(absorbed)
        out.append({
            "family_id": family_id,
            "survivor_id": survivor,
            "absorbed_ids": absorbed,
            "merged_label": str(m.get("merged_label") or "").strip() or None,
            "rationale": str(m.get("rationale", "")).strip(),
        })
    return out


def compress_batches(codes: list[dict], families: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """The batching decision shared by propose_merges and jobs.compress_work's reporting: one
    batch per family with >= COMPRESS_MIN_FAMILY_CODES active codes, plus one no-family batch
    under the same threshold. Deterministic order: real families first (by family position),
    then the no-family batch last."""
    active = [c for c in codes if c.get("status") not in ("rejected", "merged")]
    by_family: dict[str | None, list[dict]] = {}
    for c in active:
        by_family.setdefault(c.get("family_id"), []).append(c)

    batches = [(fid, members) for fid, members in by_family.items()
               if len(members) >= COMPRESS_MIN_FAMILY_CODES]
    fam_order = {f["id"]: f.get("position", i) for i, f in enumerate(families)}
    batches.sort(key=lambda kv: (kv[0] is None, fam_order.get(kv[0], 0)))
    return batches


def propose_merges(codes: list[dict], families: list[dict], progress=None) -> list[dict]:
    """One `chat_json` call per batch from compress_batches (label `compress:<family_id>`, or
    `compress:unfiled` for the no-family batch). Returns validated proposals tagged with
    family_id (None for the no-family batch). `progress`, if given, is called like
    `progress(stage="compress", done=i, total=n)`."""
    batches = compress_batches(codes, families)
    system = (PROMPTS / "compress.prompt").read_text(encoding="utf-8")
    proposals: list[dict] = []
    total = len(batches)
    for i, (fid, members) in enumerate(batches):
        if progress:
            progress(stage="compress", done=i, total=total,
                     message=f"scanning {fid or 'unfiled codes'} for redundancy")
        active_ids = {c["id"] for c in members}
        listing = _batch_listing(members)
        label = f"compress:{fid}" if fid else "compress:unfiled"
        data = llm.chat_json(system, listing, label=label)
        proposed = data.get("merges", []) or []
        proposals.extend(_validate_merges(proposed, active_ids, fid))
    if progress:
        progress(stage="compress", done=total, total=total)
    return proposals
