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


# ---- P3.8: viewer vs editor role ---------------------------------------------------------------

def test_me_reports_editor_when_no_pin_configured(monkeypatch):
    monkeypatch.delenv("MASSHINE_PIN", raising=False)
    monkeypatch.delenv("MASSHINE_VIEW_PIN", raising=False)
    with TestClient(app) as c:
        assert c.get("/me").json() == {"role": "editor"}


def test_me_reports_editor_for_edit_pin(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.setenv("MASSHINE_VIEW_PIN", "5678")
    with TestClient(app) as c:
        r = c.get("/me", headers=_basic("anyone", "1234"))
        assert r.status_code == 200 and r.json() == {"role": "editor"}


def test_me_reports_viewer_for_view_pin(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.setenv("MASSHINE_VIEW_PIN", "5678")
    with TestClient(app) as c:
        r = c.get("/me", headers=_basic("anyone", "5678"))
        assert r.status_code == 200 and r.json() == {"role": "viewer"}


def test_view_pin_allows_get_blocks_post(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.setenv("MASSHINE_VIEW_PIN", "5678")
    with TestClient(app) as c:
        headers = _basic("anyone", "5678")
        assert c.get("/packs", headers=headers).status_code == 200
        r = c.post("/projects", json={"name": "x"}, headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] == "view-only access"


def test_edit_pin_allows_post(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.setenv("MASSHINE_VIEW_PIN", "5678")
    with TestClient(app) as c:
        r = c.post("/projects", json={"name": "x"}, headers=_basic("anyone", "1234"))
        assert r.status_code == 200


def test_view_pin_unset_view_pin_credentials_rejected(monkeypatch):
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.delenv("MASSHINE_VIEW_PIN", raising=False)
    with TestClient(app) as c:
        assert c.get("/packs", headers=_basic("anyone", "5678")).status_code == 401


def test_view_pin_same_as_edit_pin_still_resolves_editor(monkeypatch):
    # Guards the "does NOT also match MASSHINE_PIN" precedence: if an operator sets both PINs to
    # the same value (misconfiguration), the stronger role wins rather than being locked out.
    monkeypatch.setenv("MASSHINE_PIN", "1234")
    monkeypatch.setenv("MASSHINE_VIEW_PIN", "1234")
    with TestClient(app) as c:
        r = c.get("/me", headers=_basic("anyone", "1234"))
        assert r.json() == {"role": "editor"}
