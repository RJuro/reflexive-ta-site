"""friction — pure-Python sentence-anchored divergence across a coder panel.

coverage[lens]  = distinct sentences that lens touched
interpretive    = sentences coded by >=2 coders (readings to compare)
attentional     = sentences coded by only SOME coders (a lens saw what others missed)
"""
import json

import masshine as m
from conftest import GOLDEN


def _independent_coverage(panel):
    return {lens: len({ev for c in codes for ev in c["evidence"]})
            for lens, codes in panel.items()}


def test_coverage_matches_independent_recompute(panel_state):
    panel = panel_state["docs"]["dp-40-grande-m"]["panel"]
    fr = m.friction(panel)
    assert fr["coverage"] == _independent_coverage(panel)
    assert set(fr["coders"]) == set(panel)


def test_interpretive_and_attentional_partition(panel_state):
    panel = panel_state["docs"]["dp-40-grande-m"]["panel"]
    fr = m.friction(panel)
    n_coders = len(panel)
    for sent, cm in fr["by_sent"].items():
        if len(cm) >= 2:
            assert sent in fr["interpretive"]
        if len(cm) < n_coders:
            assert sent in fr["attentional"]
    # interpretive requires >=2 coders; attentional requires < all coders
    assert all(len(cm) >= 2 for cm in fr["interpretive"].values())
    assert all(len(cm) < n_coders for cm in fr["attentional"].values())


def test_friction_counts_golden(panel_state):
    panel = panel_state["docs"]["dp-40-grande-m"]["panel"]
    fr = m.friction(panel)
    counts = {"coverage": fr["coverage"],
              "n_interpretive": len(fr["interpretive"]),
              "n_attentional": len(fr["attentional"]),
              "n_sentences_touched": len(fr["by_sent"])}
    _assert_golden("friction_grande_counts.json", counts)


def _assert_golden(name, obj):
    import os
    path = GOLDEN / name
    payload = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        path.write_text(payload, encoding="utf-8")
        return  # bootstrap: record the snapshot, nothing to compare against yet
    assert path.read_text(encoding="utf-8") == payload, f"golden drift in {name}"
