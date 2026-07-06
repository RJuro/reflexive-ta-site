"""Phase-3 coverage (offline): project/job store, the job runner state machine, the pack loader,
and the API read loop against a project seeded from the cache fixture (theming replays → no LLM).
The autouse `_no_live_llm` guard proves nothing here reaches a live model call.
"""
import json

import pytest
from fastapi.testclient import TestClient

from masshine import jobs, packs, projects, seed
from masshine.api import app
from conftest import FIXTURES


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the registry + project dirs at a temp location for the test."""
    d = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", d)
    return d


def test_project_and_job_store(data_dir):
    p = projects.create_project("Test", "migration_oral_history")
    assert projects.get_project(p["id"])["name"] == "Test"
    assert [x["id"] for x in projects.list_projects()] == [p["id"]]
    j = projects.create_job(p["id"], "code_panel", {"mode": "panel"})
    assert j["status"] == "queued" and j["params"] == {"mode": "panel"}
    projects.update_job(j["id"], status="running", progress={"done": 1, "total": 2})
    assert projects.get_job(j["id"])["progress"] == {"done": 1, "total": 2}
    assert len(projects.active_jobs(p["id"])) == 1
    assert projects.reset_stale_jobs() == 1
    assert projects.active_jobs(p["id"]) == []


def test_job_runner_success_and_failure(data_dir):
    p = projects.create_project("R", None)
    ok = projects.create_job(p["id"], "t", {})
    jobs._run(ok["id"], lambda progress: {"n": 42})  # synchronous
    done = projects.get_job(ok["id"])
    assert done["status"] == "done" and done["result"] == {"n": 42}

    bad = projects.create_job(p["id"], "t", {})

    def boom(progress):
        raise ValueError("nope")
    jobs._run(bad["id"], boom)
    failed = projects.get_job(bad["id"])
    assert failed["status"] == "failed" and "nope" in failed["error"]


def test_pack_loader():
    ids = {p["id"] for p in packs.list_packs()}
    assert "migration_oral_history" in ids
    coders = packs.panel_coders("migration_oral_history")
    assert list(coders) == ["standard", "critical", "phenomenological"]
    assert packs.panel_coders(None) == {"standard": coders["standard"]}


def test_upload_queues_ingest_job_without_running_it(data_dir, monkeypatch, tmp_path):
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append(jid))  # don't run (no LLM)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "U"}).json()["id"]
    f = tmp_path / "sample.txt"
    f.write_text("PHILLIPS: hello.\nGRANDE: I was born in a village.\n")
    r = client.post(f"/projects/{pid}/documents",
                    files={"file": ("sample.txt", f.read_bytes(), "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] in submitted
    assert projects.get_job(body["job_id"])["status"] == "queued"


def test_api_read_loop_on_seeded_project(data_dir):
    pid = seed.import_cache(FIXTURES / "panel_2interview.json", "Seeded", "migration_oral_history")
    client = TestClient(app)
    detail = client.get(f"/projects/{pid}").json()
    assert detail["code_counts"] == {"standard": 164, "critical": 112, "phenomenological": 145}
    doc_id = detail["documents"][0]["doc_id"]

    rd = client.get(f"/projects/{pid}/documents/{doc_id}").json()
    assert rd["sections"] and rd["sections"][0]["sentences"][0]["text"].strip()

    codes = client.get(f"/projects/{pid}/codes", params={"coder": "critical"}).json()
    assert codes and all(c["coder"] == "critical" for c in codes)
    assert all(c["evidence"][0].startswith(doc_id.split("-")[0]) or "#" in c["evidence"][0]
               for c in codes[:5])

    fr = client.get(f"/projects/{pid}/friction/{doc_id}").json()
    assert set(fr["coverage"]) == {"standard", "critical", "phenomenological"}
    assert fr["friction"]
    kinds = {f["kind"] for f in fr["friction"]}
    assert "interpretive" in kinds and "attentional" in kinds
    assert all("readings" in f and f["text"] for f in fr["friction"][:5])

    th = client.get(f"/projects/{pid}/themes", params={"mode": "panel"}).json()
    assert th["themes"] and len(th["snapshots"]) == 2
    t = th["themes"][0]
    assert "of" in t["coverage"] and t["claim_scope"] in ("cross-case", "single-case")
    assert sum(t["paradigm_provenance"].values()) == len(t["supporting_code_ids"])
