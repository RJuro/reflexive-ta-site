"""friction — sentence-anchored divergence across a coder panel. Pure Python, no LLM."""
from __future__ import annotations


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
