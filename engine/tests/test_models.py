"""Model registry (P10.1c) — pure functions, no network, no live LLM calls. Env vars are cleared
per-test (an .env-loaded MASSHINE_MODEL/MASSHINE_API_KEY etc. would otherwise leak in from
engine/.env — see test_llm_provider.py's identical discipline)."""
from __future__ import annotations

import json

import pytest

from masshine import llm, models


def _clear_all(monkeypatch):
    for k in ("MASSHINE_MODELS", "MASSHINE_MODEL", "MASSHINE_API_KEY", "MASSHINE_PROVIDER",
             "MISTRAL_API_KEY", "MASSHINE_MISTRAL_API_KEY", "MASSHINE_MISTRAL_MODEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    _clear_all(monkeypatch)


_BUILTIN_IDS = {"minimax-m3", "glm-5-2", "mistral-large", "magistral-medium", "mistral-medium"}


# ---- built-in registry shape --------------------------------------------------------------------

def test_builtin_registry_shape():
    reg = models.list_models()
    assert {e["id"] for e in reg} == _BUILTIN_IDS
    for e in reg:
        assert e["provider"] in ("minimax", "mistral")
        assert set(e) >= {"id", "label", "provider", "model", "note", "available"}


def test_codex_never_appears():
    reg = models.list_models()
    assert "codex-cli" not in {e["id"] for e in reg}
    assert all("codex" not in json.dumps(e).lower() for e in reg)


def test_minimax_default_mirrors_llm_default():
    assert models.resolve("minimax-m3")["model"] == llm.model() == "MiniMax-M3"


def test_minimax_model_follows_env_override(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODEL", "MiniMax-Custom")
    assert models.resolve("minimax-m3")["model"] == "MiniMax-Custom"


def test_resolve_unknown_or_falsy_id_returns_none():
    assert models.resolve("nope") is None
    assert models.resolve(None) is None
    assert models.resolve("") is None


# ---- availability flags (creds present/absent) --------------------------------------------------

def test_availability_false_with_no_credentials():
    reg = {e["id"]: e for e in models.list_models()}
    assert reg["minimax-m3"]["available"] is False
    assert reg["glm-5-2"]["available"] is False


def test_availability_true_once_credentials_set(monkeypatch):
    monkeypatch.setenv("MASSHINE_API_KEY", "k")
    monkeypatch.setenv("MISTRAL_API_KEY", "k2")
    reg = {e["id"]: e for e in models.list_models()}
    assert reg["minimax-m3"]["available"] is True
    assert reg["glm-5-2"]["available"] is True
    assert reg["mistral-large"]["available"] is True


def test_availability_mistral_fallback_key(monkeypatch):
    monkeypatch.setenv("MASSHINE_MISTRAL_API_KEY", "fallback")
    assert models.available(models.resolve("glm-5-2")) is True


# ---- MASSHINE_MODELS env override ----------------------------------------------------------------

def test_env_override_replaces_registry(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS",
                       json.dumps([{"id": "custom-1", "provider": "mistral", "model": "m-x"}]))
    reg = models.list_models()
    assert [e["id"] for e in reg] == ["custom-1"]
    assert reg[0]["label"] == "custom-1"   # defaulted from id when absent
    assert reg[0]["note"] == ""


def test_env_override_drops_only_malformed_entries(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS", json.dumps([
        {"id": "ok", "provider": "mistral", "model": "m"},
        {"id": "missing-model", "provider": "mistral"},   # dropped: no "model"
        {"provider": "mistral", "model": "m2"},           # dropped: no "id"
    ]))
    assert [e["id"] for e in models.list_models()] == ["ok"]


def test_env_override_malformed_json_falls_back_to_builtin(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS", "{not valid json")
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS


def test_env_override_non_list_json_falls_back(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS", '{"id": "x", "provider": "minimax", "model": "m"}')
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS


def test_env_override_all_entries_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS", '[{"id": "no-provider-or-model"}]')
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS


def test_env_override_empty_string_uses_builtin(monkeypatch):
    monkeypatch.setenv("MASSHINE_MODELS", "")
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS


# ---- server default id ---------------------------------------------------------------------------

def test_server_default_id_matches_env_minimax():
    assert models.server_default_id() == "minimax-m3"


def test_server_default_id_matches_env_mistral(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    assert models.server_default_id() == "glm-5-2"


def test_server_default_id_none_for_unlisted_model(monkeypatch):
    # minimax-m3's own registry entry mirrors MASSHINE_MODEL, so it can never go "unlisted" that
    # way — a bespoke MISTRAL model (the registry's other 3 entries are fixed strings) does.
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MASSHINE_MISTRAL_MODEL", "some-bespoke-model-not-in-the-registry")
    assert models.server_default_id() is None


if __name__ == "__main__":
    # ponytail self-check: run without pytest to prove the module's own logic holds, independent
    # of the test-suite's env-isolation fixtures.
    import os
    for k in ("MASSHINE_MODELS", "MASSHINE_MODEL", "MASSHINE_API_KEY", "MASSHINE_PROVIDER",
             "MISTRAL_API_KEY", "MASSHINE_MISTRAL_API_KEY"):
        os.environ.pop(k, None)
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS
    assert models.resolve("nope") is None
    os.environ["MASSHINE_MODELS"] = "not json"
    assert {e["id"] for e in models.list_models()} == _BUILTIN_IDS
    print("ok")
