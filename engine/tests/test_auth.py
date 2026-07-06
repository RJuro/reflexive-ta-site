"""PIN gate: no-op when MASSHINE_PIN is unset (local/dev/CI); enforced when set."""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from masshine.api import app


def _basic(user: str, pw: str) -> dict:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_pin_set_means_no_auth(monkeypatch):
    monkeypatch.delenv("MASSHINE_PIN", raising=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/packs").status_code == 200


def test_pin_set_blocks_without_credentials(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    with TestClient(app) as c:
        r = c.get("/packs")
        assert r.status_code == 401
        assert "Basic" in r.headers["www-authenticate"]


def test_pin_set_health_stays_open(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_pin_set_accepts_correct_pin_any_username(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    with TestClient(app) as c:
        assert c.get("/packs", headers=_basic("anyone", "1234")).status_code == 200
        assert c.get("/packs", headers=_basic("", "1234")).status_code == 200


def test_pin_set_rejects_wrong_pin(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    with TestClient(app) as c:
        assert c.get("/packs", headers=_basic("anyone", "0000")).status_code == 401
