"""P10.1c end-to-end (offline): PATCH /projects/{pid} model_id validation, job-param > project ->
server-default resolution, a job's resolved model landing in its row's params, and the export
manifest reflecting a project override. The autouse `_no_live_llm` guard (conftest.py) proves
nothing here reaches a live model call — jobs are inspected via their resolved `work.model_id`
attribute or a monkeypatched `jobs.submit`, never actually run.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from masshine import jobs, llm, projects, seed
from masshine.api import app
from conftest import FIXTURES


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", d)
    return d


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    pid = seed.import_cache(FIXTURES / "panel_2interview.json", "Export Demo",
                            "migration_oral_history")
    return pid


# ---- PATCH /projects/{pid} model_id validation ---------------------------------------------------

def test_patch_unknown_model_id_400s(data_dir):
    pid = projects.create_project("P")["id"]
    client = TestClient(app)
    r = client.patch(f"/projects/{pid}", json={"model_id": "not-a-real-model"})
    assert r.status_code == 400
    assert projects.get_project(pid)["model_id"] is None


def test_patch_sets_and_explicit_null_clears(data_dir):
    pid = projects.create_project("P")["id"]
    client = TestClient(app)
    r = client.patch(f"/projects/{pid}", json={"model_id": "glm-5-2"})
    assert r.status_code == 200 and r.json()["model_id"] == "glm-5-2"
    assert projects.get_project(pid)["model_id"] == "glm-5-2"

    r = client.patch(f"/projects/{pid}", json={"model_id": None})
    assert r.status_code == 200 and r.json()["model_id"] is None
    assert projects.get_project(pid)["model_id"] is None


def test_patch_omitted_model_id_leaves_it_untouched(data_dir):
    pid = projects.create_project("P")["id"]
    client = TestClient(app)
    client.patch(f"/projects/{pid}", json={"model_id": "glm-5-2"})
    r = client.patch(f"/projects/{pid}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert projects.get_project(pid)["model_id"] == "glm-5-2"   # untouched by the rename-only PATCH


def test_get_project_echoes_model_id(data_dir):
    pid = projects.create_project("P")["id"]
    client = TestClient(app)
    client.patch(f"/projects/{pid}", json={"model_id": "mistral-large"})
    detail = client.get(f"/projects/{pid}").json()
    assert detail["project"]["model_id"] == "mistral-large"


# ---- GET /models ----------------------------------------------------------------------------------

def test_get_models_lists_registry_without_codex(data_dir):
    client = TestClient(app)
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    ids = {m["id"] for m in body["models"]}
    assert "minimax-m3" in ids and "glm-5-2" in ids
    assert "codex-cli" not in ids
    assert "default_model_id" in body


# ---- job param > project default > server default precedence -------------------------------------

def test_resolve_job_model_precedence(data_dir):
    pid = projects.create_project("P")["id"]
    assert jobs.resolve_job_model(pid, None) is None
    projects.set_model(pid, "glm-5-2")
    assert jobs.resolve_job_model(pid, None)["id"] == "glm-5-2"            # project default
    assert jobs.resolve_job_model(pid, "mistral-large")["id"] == "mistral-large"  # job param wins


def test_with_model_decorator_resolves_and_tags_the_work_callable(data_dir):
    pid = projects.create_project("P")["id"]
    work = jobs.consolidate_work(pid)
    assert work.model_id is None
    projects.set_model(pid, "glm-5-2")
    work2 = jobs.consolidate_work(pid)
    assert work2.model_id == "glm-5-2"
    work3 = jobs.consolidate_work(pid, model_id="mistral-large")
    assert work3.model_id == "mistral-large"


def test_with_model_wraps_execution_in_use_model(data_dir):
    """The decorator's whole point: llm calls made INSIDE the job body see the resolved model,
    and it's cleaned up once the body returns."""
    pid = projects.create_project("P")["id"]
    projects.set_model(pid, "glm-5-2")

    @jobs.with_model
    def dummy_work(pid):
        def work(progress):
            return {"model": llm.model(), "active_id": llm.active_model()["id"]}
        return work

    work = dummy_work(pid)
    result = work(lambda **_: None)
    assert result["model"] == "glm-5-2"
    assert result["active_id"] == "glm-5-2"
    assert llm.active_model() is None


# ---- a job endpoint records its resolved model in the job row's params ----------------------------

def test_run_coding_rejects_unknown_model_id_and_creates_no_job(data_dir):
    pid = projects.create_project("P")["id"]
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/code", json={"model_id": "nope"})
    assert r.status_code == 400
    assert projects.list_jobs(pid) == []


def test_run_coding_job_param_recorded_in_params(data_dir, monkeypatch):
    pid = projects.create_project("P")["id"]
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda jid, work: submitted.append((jid, work)))
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/code", json={"model_id": "glm-5-2"})
    assert r.status_code == 200
    job = projects.get_job(r.json()["job_id"])
    assert job["params"]["model_id"] == "glm-5-2"


def test_run_coding_falls_back_to_project_default_when_no_job_param(data_dir, monkeypatch):
    pid = projects.create_project("P")["id"]
    projects.set_model(pid, "mistral-medium")
    monkeypatch.setattr(jobs, "submit", lambda jid, work: None)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/code", json={})
    job = projects.get_job(r.json()["job_id"])
    assert job["params"]["model_id"] == "mistral-medium"


def test_run_coding_job_param_overrides_project_default(data_dir, monkeypatch):
    pid = projects.create_project("P")["id"]
    projects.set_model(pid, "mistral-medium")
    monkeypatch.setattr(jobs, "submit", lambda jid, work: None)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/code", json={"model_id": "magistral-medium"})
    job = projects.get_job(r.json()["job_id"])
    assert job["params"]["model_id"] == "magistral-medium"


def test_run_coding_no_override_anywhere_records_none(data_dir, monkeypatch):
    pid = projects.create_project("P")["id"]
    monkeypatch.setattr(jobs, "submit", lambda jid, work: None)
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/code", json={})
    job = projects.get_job(r.json()["job_id"])
    assert job["params"]["model_id"] is None


def test_run_read_and_run_themes_also_validate_and_record(data_dir, monkeypatch):
    pid = projects.create_project("P")["id"]
    monkeypatch.setattr(jobs, "submit", lambda jid, work: None)
    client = TestClient(app)
    assert client.post(f"/projects/{pid}/read", json={"model_id": "nope"}).status_code == 400
    r = client.post(f"/projects/{pid}/read", json={"model_id": "glm-5-2"})
    assert projects.get_job(r.json()["job_id"])["params"]["model_id"] == "glm-5-2"

    assert client.post(f"/projects/{pid}/themes", json={"model_id": "nope"}).status_code == 400
    r = client.post(f"/projects/{pid}/themes", json={"model_id": "glm-5-2"})
    assert projects.get_job(r.json()["job_id"])["params"]["model_id"] == "glm-5-2"


# ---- export manifest reflects the project's override, run-table history stays honest --------------

def test_export_manifest_default_when_no_override(seeded):
    expected_model = llm.model()   # env default — no override anywhere in this test
    client = TestClient(app)
    d = client.get(f"/projects/{seeded}/export").json()
    assert d["manifest"]["model_id"] is None
    assert d["manifest"]["model"] == expected_model
    assert d["manifest"]["models_used"] == [expected_model]   # seed.import_cache's one run row


def test_export_manifest_reflects_project_override(seeded):
    before_model = llm.model()
    assert before_model != "glm-5-2"   # sanity: the override below is a real change
    client = TestClient(app)
    client.patch(f"/projects/{seeded}", json={"model_id": "glm-5-2"})
    d = client.get(f"/projects/{seeded}/export").json()
    assert d["manifest"]["model_id"] == "glm-5-2"
    assert d["manifest"]["model"] == "glm-5-2"
    # honest history: the seed's own run predates the override and still shows the OLD model
    assert d["manifest"]["models_used"] == [before_model]
