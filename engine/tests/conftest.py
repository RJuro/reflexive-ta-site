"""Shared test scaffolding.

The whole suite runs OFFLINE. An autouse fixture replaces `llm.chat_json` with a guard that
fails any test which reaches a live model call it did not explicitly stub — so a forgotten
monkeypatch surfaces as a clear test failure, never a network request or a paid call.

Tests that DO need a canned model response monkeypatch `llm.chat_json` themselves (this fixture
runs first; their patch wins for the duration of the test).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import llm

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """Any un-stubbed llm.chat_json call fails loudly instead of hitting the network."""
    def _blocked(*args, **kwargs):
        raise AssertionError(
            "llm.chat_json was called without a stub — a test must monkeypatch it "
            "with a canned response (the suite is offline)."
        )
    monkeypatch.setattr(llm, "chat_json", _blocked)


@pytest.fixture
def panel_state() -> dict:
    return load_fixture("panel_2interview.json")


@pytest.fixture
def project_state() -> dict:
    return load_fixture("project_2interview.json")
