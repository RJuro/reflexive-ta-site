"""llm.use_model / _ACTIVE_MODEL (P10.1c) — the researcher-selectable-model override that sits on
top of the env-only provider/model resolution tested in test_llm_provider.py. Pure contextvar
mechanics: no network, no live LLM calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from masshine import llm


def _clear_all(monkeypatch):
    for k in ("MASSHINE_PROVIDER", "MASSHINE_BASE_URL", "MASSHINE_API_KEY", "MASSHINE_MODEL",
             "MASSHINE_MISTRAL_BASE_URL", "MISTRAL_API_KEY", "MASSHINE_MISTRAL_API_KEY",
             "MASSHINE_MISTRAL_MODEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    _clear_all(monkeypatch)


MINIMAX_ENTRY = {"id": "minimax-m3", "provider": "minimax", "model": "MiniMax-M3"}
MISTRAL_ENTRY = {"id": "glm-5-2", "provider": "mistral", "model": "glm-5-2"}


# ---- no override: today's env-only behavior, unchanged ------------------------------------------

def test_no_override_falls_through_to_env(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODEL", "MiniMax-Env-Default")
    assert llm.active_model() is None
    assert llm.model() == "MiniMax-Env-Default"


def test_use_model_none_is_a_no_op(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODEL", "MiniMax-Env-Default")
    with llm.use_model(None):
        assert llm.model() == "MiniMax-Env-Default"
    assert llm.active_model() is None


# ---- an active override wins outright ------------------------------------------------------------

def test_override_wins_over_env(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")   # env says mistral...
    monkeypatch.setenv("MASSHINE_MISTRAL_MODEL", "some-other-model")
    with llm.use_model(MINIMAX_ENTRY):                    # ...override says minimax
        assert llm.model() == "MiniMax-M3"
        assert llm._provider() == "minimax"
        assert llm.active_model() == MINIMAX_ENTRY


def test_override_resolves_base_and_key_for_its_own_provider(monkeypatch):
    monkeypatch.setenv("MASSHINE_BASE_URL", "https://minimax.example/v1")
    monkeypatch.setenv("MASSHINE_API_KEY", "mm-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-key")
    with llm.use_model(MISTRAL_ENTRY):
        base, key = llm._resolved_base_and_key()
        assert base == "https://api.mistral.ai/v1" and key == "mistral-key"
    # back to minimax once the block exits
    base, key = llm._resolved_base_and_key()
    assert base == "https://minimax.example/v1" and key == "mm-key"


def test_override_resets_after_the_with_block():
    with llm.use_model(MISTRAL_ENTRY):
        assert llm.active_model() == MISTRAL_ENTRY
    assert llm.active_model() is None
    assert llm.model() == "MiniMax-M3"   # env default, nothing left over


def test_override_resets_even_on_exception():
    with pytest.raises(ValueError):
        with llm.use_model(MISTRAL_ENTRY):
            raise ValueError("boom")
    assert llm.active_model() is None


def test_nested_override_restores_outer_on_exit():
    with llm.use_model(MINIMAX_ENTRY):
        with llm.use_model(MISTRAL_ENTRY):
            assert llm.active_model() == MISTRAL_ENTRY
        assert llm.active_model() == MINIMAX_ENTRY
    assert llm.active_model() is None


# ---- no leakage between sequential jobs on ONE worker thread (the ThreadPoolExecutor shape) ------

def test_no_leakage_between_sequential_jobs_on_one_worker_thread():
    """Mirrors jobs.py's real shape: a single-worker executor, model set INSIDE the submitted
    function (not at submit() time), one job after another. The whole point of entering
    use_model() from code that already runs ON the worker thread is that no contextvars ever need
    to cross a thread boundary — this proves job 2 never sees job 1's leftover override."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        def job_with_override():
            with llm.use_model(MISTRAL_ENTRY):
                return llm.model()

        def job_without_override():
            return llm.active_model(), llm.model()

        assert executor.submit(job_with_override).result() == "glm-5-2"
        # a second, unrelated job on the SAME worker thread must see no override at all
        assert executor.submit(job_without_override).result() == (None, "MiniMax-M3")
    finally:
        executor.shutdown(wait=True)


def test_setting_at_submit_time_would_not_help_but_inside_the_job_does():
    """Sanity-checks the documented reason jobs.py sets the context INSIDE the job body: a
    ContextVar.set() made on the SUBMITTING thread before handing work to the executor does not
    propagate to the worker thread (they are different OS threads with independent contexts)."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        token = llm._ACTIVE_MODEL.set(MISTRAL_ENTRY)   # set on THIS (submitting) thread
        try:
            seen_on_worker = executor.submit(llm.active_model).result()
        finally:
            llm._ACTIVE_MODEL.reset(token)
        assert seen_on_worker is None   # did NOT cross the thread boundary
    finally:
        executor.shutdown(wait=True)
