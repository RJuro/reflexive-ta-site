"""_parse_codes / _norm_type — the grounding gate (P1): evidence ids are doc-qualified,
ungrounded ids are dropped and counted, a code with no surviving evidence is discarded."""
import masshine as m


def test_grounded_evidence_is_doc_qualified():
    valid = {"S1.000", "S1.001"}
    items = [{"label": "  L  ", "definition": " D ", "code_type": "semantic",
              "evidence_sentence_ids": ["S1.000", "S1.001"], "rationale": " r "}]
    codes, dropped = m._parse_codes(items, valid, "doc")
    assert dropped == 0
    assert len(codes) == 1
    c = codes[0]
    assert c["evidence"] == ["doc#S1.000", "doc#S1.001"]
    assert c["label"] == "L" and c["definition"] == "D" and c["model_rationale"] == "r"


def test_ungrounded_ids_dropped_and_counted():
    valid = {"S1.000"}
    items = [{"label": "L", "definition": "D", "code_type": "latent",
              "evidence_sentence_ids": ["S1.000", "S9.999", "nope"]}]
    codes, dropped = m._parse_codes(items, valid, "doc")
    assert dropped == 2
    assert codes[0]["evidence"] == ["doc#S1.000"]


def test_code_with_no_surviving_evidence_is_discarded():
    valid = {"S1.000"}
    items = [{"label": "L", "definition": "D", "code_type": "semantic",
              "evidence_sentence_ids": ["S9.999"]},
             {"label": "L2", "definition": "D2", "code_type": "semantic",
              "evidence_sentence_ids": []}]
    codes, dropped = m._parse_codes(items, valid, "doc")
    assert codes == []
    assert dropped == 1  # only the one bad id counts; the empty-evidence code adds nothing


def test_empty_items_is_safe():
    assert m._parse_codes(None, set(), "doc") == ([], 0)
    assert m._parse_codes([], {"S1.000"}, "doc") == ([], 0)


def test_norm_type_coercion():
    assert m._norm_type("latent") == "latent"
    assert m._norm_type("Latent") == "latent"
    assert m._norm_type("lat") == "latent"
    assert m._norm_type("semantic") == "semantic"
    assert m._norm_type("SEMANTIC") == "semantic"
    assert m._norm_type("garbage") == "semantic"
    assert m._norm_type(None) == "semantic"
