"""codex-cli LLM backend (P10.1a): `subprocess.run` is mocked, never the real CLI. The autouse
`_no_live_llm` fixture stubs the PUBLIC `llm.chat_json` wholesale, so these tests call the real
function object captured at import time (`real_chat_json`) — that's the function under test, and
it still dispatches internally to `_codex_chat_json` exactly as a live call would."""
from __future__ import annotations

import json

import pytest

import llm
from masshine.llm import chat_json as real_chat_json


@pytest.fixture(autouse=True)
def _codex_backend(monkeypatch):
    monkeypatch.setenv("MASSHINE_LLM_BACKEND", "codex-cli")
    monkeypatch.setenv("MASSHINE_CODEX_MODEL", "gpt-5.6-luna")
    llm.reset_usage()


def _agent_message(text: str) -> str:
    return json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": text}})


def _turn_completed(input_tokens=100, output_tokens=20, reasoning_output_tokens=5) -> str:
    return json.dumps({"type": "turn.completed",
                       "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                 "reasoning_output_tokens": reasoning_output_tokens}})


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_prompt_assembly_via_stdin(monkeypatch):
    """system + '\\n\\n----\\n\\n' + user goes in as `input=`, argv passes '-' as the prompt arg,
    and the model comes from MASSHINE_CODEX_MODEL."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeProc("\n".join([_agent_message('{"codes": []}'), _turn_completed()]))

    monkeypatch.setattr("masshine.llm.subprocess.run", fake_run)
    real_chat_json("SYSTEM TEXT", "USER TEXT", label="read")

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:2] == ["codex", "exec"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-5.6-luna"
    assert argv[-1] == "-"
    assert "--json" in argv and "--skip-git-repo-check" in argv and "--ephemeral" in argv
    assert kwargs["input"] == "SYSTEM TEXT\n\n----\n\nUSER TEXT"


def test_jsonl_parsing_last_agent_message_wins(monkeypatch):
    stdout = "\n".join([
        _agent_message('{"codes": ["draft"]}'),   # an earlier turn — not the answer
        "not json, ignored",
        _agent_message('{"codes": ["final"]}'),   # the LAST agent_message wins
        _turn_completed(),
    ])
    monkeypatch.setattr("masshine.llm.subprocess.run", lambda *a, **k: _FakeProc(stdout))
    data = real_chat_json("sys", "usr")
    assert data == {"codes": ["final"]}


def test_stderr_noise_tolerated(monkeypatch):
    """Non-empty stderr (even containing the word ERROR) never fails the call — only stdout
    parseability does."""
    stdout = "\n".join([_agent_message('{"ok": true}'), _turn_completed()])
    stderr = "2026-08-27T00:00:00 ERROR failed to load models cache\n"
    monkeypatch.setattr("masshine.llm.subprocess.run",
                        lambda *a, **k: _FakeProc(stdout, stderr=stderr, returncode=1))
    assert real_chat_json("sys", "usr") == {"ok": True}


def test_no_agent_message_raises(monkeypatch):
    stdout = _turn_completed()  # usage only, no agent_message anywhere
    monkeypatch.setattr("masshine.llm.subprocess.run", lambda *a, **k: _FakeProc(stdout))
    with pytest.raises(RuntimeError, match="no agent_message"):
        real_chat_json("sys", "usr")


def test_timeout_passed_through(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return _FakeProc("\n".join([_agent_message("{}"), _turn_completed()]))

    monkeypatch.setattr("masshine.llm.subprocess.run", fake_run)
    real_chat_json("sys", "usr", timeout=42.0)
    assert seen["timeout"] == 42.0


def test_timeout_defaults_generous(monkeypatch):
    seen = {}
    monkeypatch.setattr("masshine.llm.subprocess.run",
                        lambda argv, **kw: (seen.update(kw), _FakeProc(
                            "\n".join([_agent_message("{}"), _turn_completed()])))[1])
    real_chat_json("sys", "usr")
    assert seen["timeout"] == 1200.0


def test_ledger_updated(monkeypatch):
    stdout = "\n".join([_agent_message('{"x": 1}'),
                        _turn_completed(input_tokens=111, output_tokens=22,
                                        reasoning_output_tokens=7)])
    monkeypatch.setattr("masshine.llm.subprocess.run", lambda *a, **k: _FakeProc(stdout))
    real_chat_json("sys", "usr", label="read")
    u = llm.usage()
    assert u["calls"] == 1
    assert u["prompt_tokens"] == 111
    assert u["completion_tokens"] == 22
    assert u["by_label"]["read"]["calls"] == 1
    assert u["by_label"]["read"]["prompt_tokens"] == 111


def test_missing_codex_binary_fails_loudly_no_silent_fallback(monkeypatch):
    """A missing `codex` binary must raise a clear error, never silently retry on the openai
    (paid) backend."""
    def fake_run(*a, **k):
        raise FileNotFoundError("codex")

    monkeypatch.setattr("masshine.llm.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="codex.*binary is not on PATH"):
        real_chat_json("sys", "usr")


def test_missing_model_env_raises(monkeypatch):
    monkeypatch.delenv("MASSHINE_CODEX_MODEL", raising=False)
    monkeypatch.setattr("masshine.llm.subprocess.run",
                        lambda *a, **k: pytest.fail("must not shell out without a model"))
    with pytest.raises(RuntimeError, match="MASSHINE_CODEX_MODEL"):
        real_chat_json("sys", "usr")


def test_openai_backend_untouched_by_default(monkeypatch):
    """No MASSHINE_LLM_BACKEND set -> "openai" (the deployed default), which needs
    MASSHINE_BASE_URL/API_KEY — proves the dispatch actually branches rather than always
    taking codex-cli, and that codex-cli activates ONLY via explicit env opt-in."""
    monkeypatch.delenv("MASSHINE_LLM_BACKEND", raising=False)
    monkeypatch.delenv("MASSHINE_BASE_URL", raising=False)
    monkeypatch.delenv("MASSHINE_API_KEY", raising=False)
    assert llm.backend() == "openai"
    with pytest.raises(RuntimeError, match="MASSHINE_BASE_URL"):
        real_chat_json("sys", "usr")
