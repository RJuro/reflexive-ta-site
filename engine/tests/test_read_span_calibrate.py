"""Pure helpers of tools/read_span_calibrate.py (the LIVE calibration harness, never run by this
suite). Importing the module must trigger no tmp dir, no env mutation, and no masshine import —
`decile_table`/`report_md` are plain formatting functions."""
from __future__ import annotations

import pytest

from tools import read_span_calibrate as calib


def test_module_import_defers_the_masshine_import():
    """`masshine` (and therefore its tmp-dir-setting `_setup_env`) must not be imported at
    module load time — only inside run_span/main, which this test never calls. Proves importing
    this file's pure helpers never touches MASSHINE_DATA_DIR or the real engine/data/."""
    top_level_names = set(vars(calib))
    assert "masshine" not in top_level_names
    for leaked in ("llm", "projects", "read", "new_run", "project_db", "ingest"):
        assert leaked not in top_level_names, f"{leaked} imported at module scope, not deferred"


def test_decile_table_formats_counts_and_percentages():
    row = calib.decile_table([5, 5, 0, 0, 0, 0, 0, 0, 0, 10])
    parts = row.split(" | ")
    assert len(parts) == 10
    assert parts[0] == "5 (25%)"
    assert parts[-1] == "10 (50%)"


def test_decile_table_all_zero_does_not_divide_by_zero():
    row = calib.decile_table([0] * 10)
    assert row == " | ".join(["0 (0%)"] * 10)


def _fake_result(span: str, n_codes=3, first30=0.2) -> dict:
    return {
        "span": span, "n_codes": n_codes, "n_reuses": 1, "n_declined": 0,
        "n_citations": n_codes * 2, "n_distinct_sentences": n_codes,
        "deciles": [1, 1, 0, 0, 0, 0, 0, 0, 0, 1], "first30_share": first30,
        "drop": {"over_cap": 0, "ungrounded_evidence": 2}, "wall_s": 4.2,
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }


def test_report_md_contains_a_row_per_span_and_the_transcript_name():
    results = [_fake_result("doc"), _fake_result("sections", n_codes=9, first30=0.5)]
    md = calib.report_md("Livicia_Antoine_LOC_transcript.txt", results)
    assert "Livicia_Antoine_LOC_transcript.txt" in md
    assert "| doc |" in md and "| sections |" in md
    assert "20%" in md and "50%" in md
    assert "0/2" in md          # cap/grounding drop column
    assert "1000/200" in md    # tokens column


def test_report_md_empty_results_still_renders_header():
    md = calib.report_md("empty.txt", [])
    assert "empty.txt" in md and "0 span(s)" in md


# ---- estimated cost (pure, env-driven) ---------------------------------------------------------

def test_estimated_cost_eur_none_when_pricing_unset(monkeypatch):
    monkeypatch.delenv("MASSHINE_PRICE_IN_EUR_PER_M", raising=False)
    monkeypatch.delenv("MASSHINE_PRICE_OUT_EUR_PER_M", raising=False)
    assert calib.estimated_cost_eur(1000, 500) is None


def test_estimated_cost_eur_none_when_only_one_price_set(monkeypatch):
    monkeypatch.setenv("MASSHINE_PRICE_IN_EUR_PER_M", "1.19")
    monkeypatch.delenv("MASSHINE_PRICE_OUT_EUR_PER_M", raising=False)
    assert calib.estimated_cost_eur(1000, 500) is None


def test_estimated_cost_eur_computes_mistral_glm52_rate(monkeypatch):
    """Mistral glm-5-2 under the university contract (2026-08-27): €1.19/M in, €3.74/M out."""
    monkeypatch.setenv("MASSHINE_PRICE_IN_EUR_PER_M", "1.19")
    monkeypatch.setenv("MASSHINE_PRICE_OUT_EUR_PER_M", "3.74")
    cost = calib.estimated_cost_eur(1_000_000, 1_000_000)
    assert cost == pytest.approx(1.19 + 3.74)


def test_estimated_cost_eur_zero_tokens_is_zero(monkeypatch):
    monkeypatch.setenv("MASSHINE_PRICE_IN_EUR_PER_M", "1.19")
    monkeypatch.setenv("MASSHINE_PRICE_OUT_EUR_PER_M", "3.74")
    assert calib.estimated_cost_eur(0, 0) == 0.0


def test_report_md_omits_cost_column_when_pricing_unset(monkeypatch):
    monkeypatch.delenv("MASSHINE_PRICE_IN_EUR_PER_M", raising=False)
    monkeypatch.delenv("MASSHINE_PRICE_OUT_EUR_PER_M", raising=False)
    md = calib.report_md("t.txt", [_fake_result("doc")])
    assert "est. cost" not in md


def test_report_md_includes_cost_column_and_folds_thinking_into_output(monkeypatch):
    monkeypatch.setenv("MASSHINE_PRICE_IN_EUR_PER_M", "1.19")
    monkeypatch.setenv("MASSHINE_PRICE_OUT_EUR_PER_M", "3.74")
    r = _fake_result("doc")
    r["usage"] = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000, "think_chars": 500_000}
    md = calib.report_md("t.txt", [r])
    assert "est. cost" in md
    # (1_000_000 * 1.19 + 1_000_000 * 3.74) / 1e6 == 4.93 — think_chars folded into output tokens
    assert "4.9300" in md
