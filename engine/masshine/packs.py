"""Pack loader — replaces hardcoded standpoint paths. A pack is a directory under packs/ that
carries standpoint coder prompts (packs/<id>/standpoints/*.prompt) and, optionally, a pack.json
manifest naming short lens keys, their prompt files, and a blurb. Without a manifest, lens keys
fall back to the prompt filename stem and blurbs are empty.

The 'standard' coder is engine-level (engine/prompts/coder.prompt), always the panel baseline; a
pack only contributes the standpoint lenses layered on top.
"""
from __future__ import annotations

import json

from .config import PROMPTS, ROOT

PACKS_DIR = ROOT.parent / "packs"

STANDARD_BLURB = "the method's neutral reflexive-TA coder (engine default)"


def _standpoints_dir(pack_id: str):
    return PACKS_DIR / pack_id / "standpoints"


def list_packs() -> list[dict]:
    """Every pack dir that has at least one standpoint prompt → [{id, title, lenses:[{name,blurb}]}]."""
    out = []
    if not PACKS_DIR.exists():
        return out
    for d in sorted(PACKS_DIR.iterdir()):
        if not d.is_dir() or not _standpoints_dir(d.name).exists():
            continue
        pack = load_pack(d.name)
        out.append({"id": pack["id"], "title": pack["title"],
                    "lenses": [{"name": k, "blurb": pack["blurbs"].get(k, "")}
                               for k in pack["standpoints"]]})
    return out


def load_pack(pack_id: str) -> dict:
    """{id, title, standpoints:{lens: prompt_text}, blurbs:{lens: str}} from packs/<id>/."""
    base = PACKS_DIR / pack_id
    manifest = base / "pack.json"
    standpoints: dict[str, str] = {}
    blurbs: dict[str, str] = {}
    title = pack_id
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        title = data.get("title", pack_id)
        for lens, spec in data.get("lenses", {}).items():
            standpoints[lens] = (base / spec["prompt"]).read_text(encoding="utf-8")
            blurbs[lens] = spec.get("blurb", "")
    else:  # no manifest → derive lens keys from filenames, blurbs empty
        for p in sorted(_standpoints_dir(pack_id).glob("*.prompt")):
            standpoints[p.stem] = p.read_text(encoding="utf-8")
            blurbs[p.stem] = ""
    return {"id": pack_id, "title": title, "standpoints": standpoints, "blurbs": blurbs}


def panel_coders(pack_id: str | None) -> dict[str, str]:
    """{name: system_prompt} for a standpoint panel: always the standard coder first, then the
    pack's standpoints (manifest order preserved). Insertion order matters — it fixes panel code
    enumeration order downstream."""
    coders = {"standard": (PROMPTS / "coder.prompt").read_text(encoding="utf-8")}
    if pack_id:
        coders.update(load_pack(pack_id)["standpoints"])
    return coders


def coder_blurbs(pack_id: str | None) -> dict[str, str]:
    """{name: blurb}, standard first — mirrors panel_coders' keys for display."""
    blurbs = {"standard": STANDARD_BLURB}
    if pack_id:
        blurbs.update(load_pack(pack_id)["blurbs"])
    return blurbs
