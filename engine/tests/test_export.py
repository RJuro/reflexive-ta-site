"""Export endpoints (v5): self-contained JSON + flat CSVs, on a project seeded from the
cache fixture (0 LLM calls — the autouse guard proves it)."""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from masshine import projects, seed, store
from masshine.api import app
from masshine.db import project_db
from conftest import FIXTURES


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    pid = seed.import_cache(FIXTURES / "panel_2interview.json", "Export Demo",
                            "migration_oral_history")
    return pid


def test_export_json_is_self_contained(seeded):
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export")
    assert r.status_code == 200
    assert 'filename="masshine-export-demo.json"' in r.headers["content-disposition"]
    d = r.json()
    assert d["project"]["name"] == "Export Demo"
    assert len(d["codes"]) == 421 and len(d["themes"]) == 7
    # evidence carries resolved verbatim quotes, not just ids
    ev = d["codes"][0]["evidence"][0]
    assert ev["id"].count("#") == 1 and len(ev["quote"]) > 0
    assert isinstance(d["memos"], list) and isinstance(d["comments"], list)


def test_export_codes_csv_shape_and_revisions(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.add_revision(conn, "C0001", "rename", "Renamed For Export")
        store.set_memo(conn, "code", "C0001", "my memo")
    finally:
        conn.close()
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/codes.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    assert len(body) == 421
    assert header[:5] == ["id", "lens", "type", "status", "label"]
    c1 = next(x for x in body if x[0] == "C0001")
    assert c1[header.index("label")] == "Renamed For Export"       # researcher label wins
    assert c1[header.index("machine_label")] != "Renamed For Export"
    assert c1[header.index("researcher_memo")] == "my memo"
    assert c1[header.index("exemplar_quote")]                      # verbatim quote resolved


def test_export_themes_csv(seeded):
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/themes.csv")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    assert len(body) == 7
    t = body[0]
    assert t[0].startswith("T")
    assert "of" in t[header.index("coverage")]
    assert ":" in t[header.index("provenance")]                    # e.g. standard:5|critical:10
    assert int(t[header.index("n_supporting")]) > 0


def test_export_themes_csv_has_label_column_first_after_id(seeded):
    """P7: themes_csv gains a `label` column immediately after `id`. The seeded fixture's raw
    cache predates the label field, so every row's label is "" — the column must still exist
    and sit right after id (never error on a themeset with no labels)."""
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/themes.csv")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    assert header[:2] == ["id", "label"]
    assert all(row[1] == "" for row in body)  # no labels in this fixture's raw cache


def test_export_themes_csv_label_populated_when_present(seeded):
    """When a theme does carry a label (e.g. after a re-theme with the new prompt), the CSV
    surfaces it verbatim in the label column."""
    conn = project_db(projects.project_db_path(seeded))
    try:
        conn.execute("UPDATE theme_v2 SET payload = json_set(payload, '$.label', ?) "
                     "WHERE mode='panel' AND id='T01'", ("Kinship-mediated underpayment",))
        conn.commit()
    finally:
        conn.close()
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/themes.csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    t1 = next(row for row in body if row[0] == "T01")
    assert t1[header.index("label")] == "Kinship-mediated underpayment"


def test_export_unknown_project_404s():
    client = TestClient(app)
    assert client.get("/projects/Pnope/export").status_code == 404


def test_export_report_md_has_theme_claim_quote_code_and_filename(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        store.set_memo(conn, "code", "C0001", "a researcher memo on this code")
    finally:
        conn.close()
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/report.md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert 'filename="masshine-export-demo-report.md"' in r.headers["content-disposition"]
    body = r.text
    assert body.startswith("# Export Demo")

    conn = project_db(projects.project_db_path(seeded))
    try:
        th = store.themes_payload(conn, "panel")
        codes = store.codes_payload(conn)
    finally:
        conn.close()
    # a theme claim appears
    assert th["themes"][0]["central_concept"] in body
    # an anchor quote is resolved verbatim (not just the bare sentence id)
    anchor_sid = th["themes"][0]["key_evidence_sentence_ids"][0]
    conn = project_db(projects.project_db_path(seeded))
    try:
        from masshine.db import resolve_ev
        quote = " ".join(resolve_ev(conn, anchor_sid).split())
    finally:
        conn.close()
    assert quote in body
    # a code label appears in the codebook appendix
    assert codes[0]["label"] in body
    # the researcher memo made it into the appendix
    assert "a researcher memo on this code" in body


def test_report_md_theme_heading_falls_back_to_central_concept_when_no_label(seeded):
    """P7: report_md's theme headings use label when present, falling back to central_concept
    for themes that predate the field (the seeded fixture's raw cache has no labels)."""
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/report.md")
    assert r.status_code == 200
    conn = project_db(projects.project_db_path(seeded))
    try:
        th = store.themes_payload(conn, "panel")
    finally:
        conn.close()
    t0 = th["themes"][0]
    assert t0.get("label", "") == ""  # control: this fixture has no labels yet
    assert f"### {t0['id']} — {t0['central_concept']}" in r.text


def test_report_md_theme_heading_uses_label_when_present(seeded):
    conn = project_db(projects.project_db_path(seeded))
    try:
        conn.execute("UPDATE theme_v2 SET payload = json_set(payload, '$.label', ?) "
                     "WHERE mode='panel' AND id='T01'", ("Kinship-mediated underpayment",))
        conn.commit()
        th = store.themes_payload(conn, "panel")
    finally:
        conn.close()
    t1 = next(t for t in th["themes"] if t["id"] == "T01")
    client = TestClient(app)
    r = client.get(f"/projects/{seeded}/export/report.md")
    assert "### T01 — Kinship-mediated underpayment" in r.text
    assert f"### T01 — {t1['central_concept']}" not in r.text
