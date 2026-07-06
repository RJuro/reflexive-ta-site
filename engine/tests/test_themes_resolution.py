"""Sequential theming replayed from cached raw steps (zero LLM calls) — pins the deterministic
resolution in _resolve_step_themes / theorize_walk: stable ids, accumulated support, coverage
derived from evidence (never trusted to the model), scope tracking coverage, grounded anchors,
and paradigm provenance that sums to the supporting-code count.

Because the fixtures carry a full theme_steps cache, the whole walk REPLAYS — the autouse
`_no_live_llm` guard proves no model call escapes.
"""
import json
import os

import masshine as m
from conftest import GOLDEN


def _assert_golden(name, obj):
    path = GOLDEN / name
    payload = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        path.write_text(payload, encoding="utf-8")
        return
    assert path.read_text(encoding="utf-8") == payload, f"golden drift in {name}"


def _transcripts(state):
    return {d: m.transcript_block_from_sentences(state["docs"][d]["sentences"],
                                                 state["docs"][d]["sections"])
            for d in state["order"]}


def _valid_sents(state):
    return {d: {s["id"] for s in state["docs"][d]["sentences"]} for d in state["order"]}


def _qualified(state):
    return {f"{d}#{s['id']}" for d in state["order"]
            for s in state["docs"][d]["sentences"]}


def _theme_invariants(themes, codebook, n_docs, qualified, origin=None):
    ids = set()
    for t in themes:
        assert t["id"] not in ids, "theme ids must be unique"
        ids.add(t["id"])
        assert t["supporting_code_ids"], "a theme must keep at least one grounded support"
        assert set(t["supporting_code_ids"]) <= set(codebook), "support ids must be real codes"
        k, of = t["coverage"].split(" of ")
        assert int(of) == n_docs
        assert 1 <= int(k) <= n_docs, f"coverage {t['coverage']} out of range"
        assert t["claim_scope"] == ("cross-case" if int(k) >= 2 else "single-case")
        for q in t["key_evidence_sentence_ids"]:
            assert q in qualified, f"fabricated anchor {q}"
        if origin is not None:
            prov = t["paradigm_provenance"]
            assert sum(prov.values()) == len(t["supporting_code_ids"])
            assert set(prov) <= set(origin.values())


def test_project_walk_replays_and_holds_invariants(project_state):
    themes, codebook, snaps, fails = m.theorize_project_sequential(
        project_state["order"], project_state["project_codebook"],
        _transcripts(project_state), _valid_sents(project_state),
        raw_cache=project_state["theme_steps"])
    assert fails == []
    assert len(snaps) == len(project_state["order"])
    _theme_invariants(themes, codebook, len(project_state["order"]), _qualified(project_state))
    _assert_golden("project_themes.json", themes)


def test_panel_walk_replays_with_provenance(panel_state):
    themes, codebook, origin, snaps, fails = m.theorize_panel_sequential(
        panel_state["order"], {d: panel_state["docs"][d]["panel"] for d in panel_state["order"]},
        _transcripts(panel_state), _valid_sents(panel_state),
        raw_cache=panel_state["theme_steps"])
    assert fails == []
    _theme_invariants(themes, codebook, len(panel_state["order"]), _qualified(panel_state), origin)
    # every panel code carries a lens origin
    assert set(origin.values()) <= {"standard", "critical", "phenomenological"}
    _assert_golden("panel_themes.json", themes)


def test_resolve_step_themes_carries_label_through(project_state):
    """P7: a fresh theme with a `label` in the raw model output gets it carried into the
    resolved theme dict verbatim."""
    raw = [{"id": None, "central_concept": "Claim one.", "label": "Short Snappy Title",
            "supporting_code_ids": ["C0006"], "key_evidence_sentence_ids": []}]
    valid_codes = {"C0006"}
    all_codes = {"C0006": {"evidence": ["dp-40-grande-m#S1.001"]}}
    resolved = m._resolve_step_themes(raw, [], valid_codes, all_codes, set(), "dp-40-grande-m",
                                      [0], 2, None, {"dp-40-grande-m"})
    assert len(resolved) == 1
    assert resolved[0]["label"] == "Short Snappy Title"


def test_resolve_step_themes_defaults_label_to_empty_when_absent(project_state):
    """Backward/forward compatibility: raw model output with no `label` key resolves to ""."""
    raw = [{"id": None, "central_concept": "Claim one.",
            "supporting_code_ids": ["C0006"], "key_evidence_sentence_ids": []}]
    valid_codes = {"C0006"}
    all_codes = {"C0006": {"evidence": ["dp-40-grande-m#S1.001"]}}
    resolved = m._resolve_step_themes(raw, [], valid_codes, all_codes, set(), "dp-40-grande-m",
                                      [0], 2, None, {"dp-40-grande-m"})
    assert resolved[0]["label"] == ""


def test_resolve_step_themes_revision_overrides_label_when_nonempty(project_state):
    """On revision, a fresh non-empty label wins over the prior theme's label — the claim
    sharpened, and the label should track it."""
    prior = [{"id": "T01", "label": "Old Title", "central_concept": "Old claim.",
             "supporting_code_ids": ["C0001"], "key_evidence_sentence_ids": [],
             "subthemes": [], "tensions": [], "coverage": "1 of 2", "claim_scope": "single-case",
             "falsified_if": ""}]
    raw = [{"id": "T01", "label": "New Sharper Title", "central_concept": "Sharper claim.",
            "supporting_code_ids": ["C0006"], "key_evidence_sentence_ids": []}]
    valid_codes = {"C0001", "C0006"}
    all_codes = {"C0001": {"evidence": ["dp-40-grande-m#S1.001"]},
                "C0006": {"evidence": ["ei-845-rodwin#S1.001"]}}
    resolved = m._resolve_step_themes(raw, prior, valid_codes, all_codes, set(), "ei-845-rodwin",
                                      [1], 2, None, {"dp-40-grande-m", "ei-845-rodwin"})
    assert resolved[0]["id"] == "T01"
    assert resolved[0]["label"] == "New Sharper Title"


def test_resolve_step_themes_revision_keeps_prior_label_when_omitted(project_state):
    """On revision, an omitted/empty label keeps the prior theme's label rather than blanking
    it out — mirrors how other carried fields (tensions, key_evidence) accumulate."""
    prior = [{"id": "T01", "label": "Kept Title", "central_concept": "Old claim.",
             "supporting_code_ids": ["C0001"], "key_evidence_sentence_ids": [],
             "subthemes": [], "tensions": [], "coverage": "1 of 2", "claim_scope": "single-case",
             "falsified_if": ""}]
    raw = [{"id": "T01", "central_concept": "Slightly extended claim.",
            "supporting_code_ids": ["C0006"], "key_evidence_sentence_ids": []}]
    valid_codes = {"C0001", "C0006"}
    all_codes = {"C0001": {"evidence": ["dp-40-grande-m#S1.001"]},
                "C0006": {"evidence": ["ei-845-rodwin#S1.001"]}}
    resolved = m._resolve_step_themes(raw, prior, valid_codes, all_codes, set(), "ei-845-rodwin",
                                      [1], 2, None, {"dp-40-grande-m", "ei-845-rodwin"})
    assert resolved[0]["id"] == "T01"
    assert resolved[0]["label"] == "Kept Title"


def test_coverage_never_leaks_from_unseen_interviews(project_state):
    # after the FIRST interview's snapshot, no theme may claim coverage beyond "1 of N"
    _, _, snaps, _ = m.theorize_project_sequential(
        project_state["order"], project_state["project_codebook"],
        _transcripts(project_state), _valid_sents(project_state),
        raw_cache=project_state["theme_steps"])
    first_doc, first_snap = snaps[0]
    for t in first_snap:
        k = int(t["coverage"].split(" of ")[0])
        assert k == 1, f"{t['id']} leaked coverage {t['coverage']} at step 1"
