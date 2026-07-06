"""Codebook consolidation (P6): group the whole codebook into 8–15 code FAMILIES — the
35–50-unit view a researcher can hold, with the fine codes and their evidence intact
underneath. Small projects still get the original ONE-CALL pass; multi-source projects use a
HIERARCHICAL map-reduce so no single call has to skim a 400+-code flat list: each source's
codes are grouped into per-source families first (map), then those per-source families are
aggregated across sources into the project-level 8–15 (reduce) — mirroring the within-case
then cross-case order of qualitative practice, and the engine's own sequential theming walk.

Python validates and disposes at BOTH stages (P3: model judges, code aggregates) — invented
or rejected member ids are dropped, a member claimed by two families stays with the first,
and anything left unplaced is filed under an explicit "Unfiled" family rather than silently
lost. Per-source families are intermediate computation only — never persisted; only the final
project-level families (with real code members) go to the database, in the schema that
existed before this pass shipped.

Hue is assigned purely from the FINAL ring position (`hue = round(360 * position / n)`), so
it is deterministic and stable across re-runs that don't reshuffle the order.
"""
from __future__ import annotations

from . import llm
from .config import PROMPTS

GUIDANCE_HEADER = (
    "RESEARCHER FEEDBACK (from reviewing an earlier consolidation of this codebook — take it "
    "into account when grouping: adjust family boundaries, rename, or re-split as directed):"
)

# Small-project fast path: below this many active codes (or a single source), skip the
# map-reduce entirely and do the original one-call pass — map-reduce's per-source overhead
# only pays for itself once there's enough codebook to actually skim.
SMALL_PROJECT_CODE_LIMIT = 40


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


def _validate_families(proposed: list[dict], active_ids: set[str], id_key: str) -> tuple[list[dict], set[str]]:
    """Shared validator: drop invented/unknown ids, first-claim wins across families, drop
    empty families. Returns (families-without-position/hue, set of ids actually claimed).
    `id_key` is the member-id field name in each proposed family dict (either
    "member_code_ids" or "member_family_ids")."""
    families: list[dict] = []
    claimed: set[str] = set()
    for fam in proposed:
        label = str(fam.get("label", "")).strip()
        definition = str(fam.get("definition", "")).strip()
        members = []
        for mid in fam.get(id_key, []) or []:
            mid = str(mid).strip()
            if mid not in active_ids:   # invented or unknown id — drop
                continue
            if mid in claimed:          # claimed by an earlier family — first claim wins
                continue
            members.append(mid)
        if not members:                 # empty family (all members invalid/duplicate) — drop
            continue
        claimed.update(members)
        families.append({"label": label, "definition": definition, id_key: members})
    return families, claimed


def _assign_ring(families: list[dict]) -> list[dict]:
    n = len(families)
    for i, fam in enumerate(families):
        fam["position"] = i
        fam["hue"] = round(360 * i / n) if n else 0
    return families


def _single_call_consolidate(codes: list[dict], active_ids: set[str],
                             guidance: str | None) -> list[dict]:
    """The original ONE-CALL pass, used for single-source/small projects."""
    system = (PROMPTS / "consolidate.prompt").read_text(encoding="utf-8")
    listing = _with_guidance(codebook_listing(codes), guidance)
    data = llm.chat_json(system, listing, label="consolidate")
    proposed = data.get("families", []) or []

    families, claimed = _validate_families(proposed, active_ids, "member_code_ids")
    unplaced = sorted(active_ids - claimed)
    if unplaced:
        families.append({
            "label": "Unfiled",
            "definition": "Codes the consolidation pass did not place.",
            "member_code_ids": unplaced,
        })
    return _assign_ring(families)


def _source_listing(codes: list[dict]) -> str:
    """Same line shape as codebook_listing — codes are already scoped to one source by the
    caller, so no extra source marker is needed in the line itself."""
    return codebook_listing(codes)


def _consolidate_one_source(doc_id: str, codes: list[dict], sf_counter: list[int],
                            progress, done: int, total: int) -> tuple[list[dict], list[str]]:
    """Map step: group ONE source's active codes into per-source families with temporary
    SFxx ids (assigned globally across sources via the shared sf_counter). Returns
    (source_families, unplaced_code_ids) — unplaced codes are carried by the caller into the
    final Unfiled family rather than lost.

    Each returned family: {"sf_id", "label", "definition", "member_code_ids", "origin_doc_id"}.
    """
    active_ids = {c["id"] for c in codes if c.get("status") != "rejected"}
    if progress:
        progress(stage="consolidating", done=done, total=total,
                 message=f"grouping source {doc_id}")
    if not active_ids:
        return [], []

    system = (PROMPTS / "consolidate_source.prompt").read_text(encoding="utf-8")
    listing = _source_listing(codes)
    data = llm.chat_json(system, listing, label=f"consolidate:src:{doc_id}")
    proposed = data.get("families", []) or []

    families, claimed = _validate_families(proposed, active_ids, "member_code_ids")
    out = []
    for fam in families:
        sf_counter[0] += 1
        out.append({
            "sf_id": f"SF{sf_counter[0]:02d}",
            "label": fam["label"],
            "definition": fam["definition"],
            "member_code_ids": fam["member_code_ids"],
            "origin_doc_id": doc_id,
        })
    unplaced = sorted(active_ids - claimed)
    return out, unplaced


def _aggregate_listing(source_families: list[dict], codes_by_id: dict[str, dict],
                       doc_titles: dict[str, str]) -> str:
    """Group per-source family lines under a heading per source, in source-processing order.
    Each family line carries its SFxx id, label, definition, code count, and up to 3 example
    member code labels."""
    by_doc: dict[str, list[dict]] = {}
    for fam in source_families:
        by_doc.setdefault(fam["origin_doc_id"], []).append(fam)

    blocks = []
    for doc_id in by_doc:  # preserves insertion order == processing order
        title = doc_titles.get(doc_id, doc_id)
        blocks.append(f"SOURCE: {title}")
        for fam in by_doc[doc_id]:
            examples = []
            for cid in fam["member_code_ids"][:3]:
                c = codes_by_id.get(cid)
                if c:
                    lbl = c.get("researcher_label") or c["label"]
                    examples.append(f'"{lbl}"')
            ex_text = "; ".join(examples)
            n = len(fam["member_code_ids"])
            blocks.append(
                f'[{fam["sf_id"]}] (source: {title}) "{fam["label"]}" — {fam["definition"]} '
                f'· {n} code{"s" if n != 1 else ""}'
                + (f" · e.g. {ex_text}" if ex_text else "")
            )
        blocks.append("")
    return "\n".join(blocks).rstrip()


def consolidate_codebook(codes: list[dict], guidance: str | None = None,
                         doc_titles: dict[str, str] | None = None,
                         progress=None) -> list[dict]:
    """Group the codebook into families, then validate in Python.

    Small projects (single source, or <= SMALL_PROJECT_CODE_LIMIT active codes total) use the
    original ONE-CALL pass. Larger multi-source projects use a hierarchical map-reduce: each
    source's active codes are consolidated into per-source families first (one call per
    source), then those per-source families are aggregated into the final project families
    (one more call) — researcher `guidance`, if any, is given ONLY to the aggregate call.

    Returns families in ring order, each:
      {"label": ..., "definition": ..., "member_code_ids": [...], "position": 0, "hue": 0}
    An "Unfiled" family (only if non-empty) is appended last with its own position/hue.

    `progress`, if given, is called like `progress(stage=..., done=..., total=...)` at each
    source/aggregate step; it is never required (tolerate None).
    """
    doc_titles = doc_titles or {}
    active_ids = {c["id"] for c in codes if c.get("status") != "rejected"}
    if not active_ids:
        return []

    by_doc: dict[str, list[dict]] = {}
    for c in codes:
        by_doc.setdefault(c.get("origin_doc_id"), []).append(c)

    if len(by_doc) <= 1 or len(active_ids) <= SMALL_PROJECT_CODE_LIMIT:
        if progress:
            progress(stage="consolidating", done=0, total=1)
        result = _single_call_consolidate(codes, active_ids, guidance)
        if progress:
            progress(stage="consolidating", done=1, total=1)
        return result

    # ---- map: per-source families ------------------------------------------------------------
    codes_by_id = {c["id"]: c for c in codes}
    doc_ids = sorted(by_doc)  # deterministic processing order
    n_sources = len(doc_ids)
    total_steps = n_sources + 1  # + the aggregate call

    sf_counter = [0]
    source_families: list[dict] = []
    unplaced_by_source: list[str] = []
    for i, doc_id in enumerate(doc_ids):
        fams, unplaced = _consolidate_one_source(
            doc_id, by_doc[doc_id], sf_counter, progress, done=i, total=total_steps)
        source_families.extend(fams)
        unplaced_by_source.extend(unplaced)

    if progress:
        progress(stage="consolidating", done=n_sources, total=total_steps,
                 message="aggregating into project families")

    # ---- reduce: aggregate per-source families into project families -----------------------
    if not source_families:
        # nothing survived the per-source passes — fall back to Unfiled for every active code
        families = []
        unplaced = sorted(active_ids)
    else:
        sf_ids = {fam["sf_id"] for fam in source_families}
        system = (PROMPTS / "consolidate_aggregate.prompt").read_text(encoding="utf-8")
        listing = _with_guidance(
            _aggregate_listing(source_families, codes_by_id, doc_titles), guidance)
        data = llm.chat_json(system, listing, label="consolidate:aggregate")
        proposed = data.get("families", []) or []

        agg_families, claimed_sf = _validate_families(proposed, sf_ids, "member_family_ids")

        sf_by_id = {fam["sf_id"]: fam for fam in source_families}
        families = []
        for fam in agg_families:
            member_code_ids: list[str] = []
            for sf_id in fam["member_family_ids"]:
                member_code_ids.extend(sf_by_id[sf_id]["member_code_ids"])
            if not member_code_ids:  # shouldn't happen (each SF has >=1 code) but be defensive
                continue
            families.append({
                "label": fam["label"],
                "definition": fam["definition"],
                "member_code_ids": member_code_ids,
            })

        unplaced_sf = sf_ids - claimed_sf
        unplaced = sorted(unplaced_by_source)
        for sf_id in unplaced_sf:
            unplaced.extend(sf_by_id[sf_id]["member_code_ids"])
        unplaced = sorted(set(unplaced))

    if unplaced:
        families.append({
            "label": "Unfiled",
            "definition": "Codes the consolidation pass did not place.",
            "member_code_ids": unplaced,
        })

    if progress:
        progress(stage="consolidating", done=total_steps, total=total_steps)

    return _assign_ring(families)
