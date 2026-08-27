"""OpenAI-path provider profiles (MASSHINE_PROVIDER): still the same streamed OpenAI-compatible
client — this only resolves which base_url/api_key/model triple it uses. Pure env resolution,
no network, no subprocess. Unset/empty MASSHINE_PROVIDER must be byte-identical to the original
MASSHINE_BASE_URL/MASSHINE_API_KEY/MASSHINE_MODEL behavior."""
from __future__ import annotations

import pytest

from masshine.llm import _client, _resolved_base_and_key, model


def _clear_all(monkeypatch):
    for k in ("MASSHINE_PROVIDER", "MASSHINE_BASE_URL", "MASSHINE_API_KEY", "MASSHINE_MODEL",
             "MASSHINE_MISTRAL_BASE_URL", "MISTRAL_API_KEY", "MASSHINE_MISTRAL_API_KEY",
             "MASSHINE_MISTRAL_MODEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    _clear_all(monkeypatch)


# ---- default (unset/empty MASSHINE_PROVIDER): exactly the original behavior -------------------

def test_default_profile_uses_original_vars_unchanged(monkeypatch):
    monkeypatch.setenv("MASSHINE_BASE_URL", "https://minimax.example/v1")
    monkeypatch.setenv("MASSHINE_API_KEY", "mm-key")
    monkeypatch.setenv("MASSHINE_MODEL", "MiniMax-M3")
    assert _resolved_base_and_key() == ("https://minimax.example/v1", "mm-key")
    assert model() == "MiniMax-M3"


def test_default_model_falls_back_when_unset(monkeypatch):
    assert model() == "MiniMax-M3"


def test_empty_provider_string_is_also_default(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "")
    monkeypatch.setenv("MASSHINE_BASE_URL", "https://minimax.example/v1")
    monkeypatch.setenv("MASSHINE_API_KEY", "mm-key")
    assert _resolved_base_and_key() == ("https://minimax.example/v1", "mm-key")


def test_default_client_error_message_unchanged(monkeypatch):
    with pytest.raises(RuntimeError, match="MASSHINE_BASE_URL and MASSHINE_API_KEY"):
        _client()


# ---- mistral profile -----------------------------------------------------------------------

def test_mistral_profile_defaults(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-key")
    base, key = _resolved_base_and_key()
    assert base == "https://api.mistral.ai/v1"
    assert key == "mistral-key"
    assert model() == "glm-5-2"


def test_mistral_profile_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "MISTRAL")
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-key")
    assert _resolved_base_and_key()[0] == "https://api.mistral.ai/v1"


def test_mistral_base_url_override(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MASSHINE_MISTRAL_BASE_URL", "https://eu.mistral.example/v1")
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    assert _resolved_base_and_key()[0] == "https://eu.mistral.example/v1"


def test_mistral_model_override(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MASSHINE_MISTRAL_MODEL", "mistral-large-latest")
    assert model() == "mistral-large-latest"


def test_mistral_key_prefers_mistral_api_key_over_fallback(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "primary")
    monkeypatch.setenv("MASSHINE_MISTRAL_API_KEY", "fallback")
    assert _resolved_base_and_key()[1] == "primary"


def test_mistral_key_falls_back_when_primary_unset(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MASSHINE_MISTRAL_API_KEY", "fallback")
    assert _resolved_base_and_key()[1] == "fallback"


def test_mistral_profile_ignores_minimax_vars(monkeypatch):
    """Switching to mistral must not accidentally pick up MASSHINE_BASE_URL/MODEL/API_KEY."""
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    monkeypatch.setenv("MASSHINE_BASE_URL", "https://minimax.example/v1")
    monkeypatch.setenv("MASSHINE_API_KEY", "mm-key")
    monkeypatch.setenv("MASSHINE_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-key")
    base, key = _resolved_base_and_key()
    assert base == "https://api.mistral.ai/v1"
    assert key == "mistral-key"
    assert model() == "glm-5-2"


def test_mistral_missing_key_error_message(monkeypatch):
    monkeypatch.setenv("MASSHINE_PROVIDER", "mistral")
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        _client()
