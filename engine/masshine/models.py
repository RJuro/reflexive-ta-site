"""Model registry (P10.1c): a small declarative list of models a researcher can pick per project
(with a per-run override) — see llm.py's `use_model` / jobs.py's model resolution and GET /models.

codex-cli/luna is NEVER in this registry and never will be: it's LOCAL-CALIBRATION-ONLY (see
llm.py's module docstring) — a dev-machine harness for tools/read_span_calibrate.py, not a
deployed backend. Making it selectable (here, in an env override, or in any API response) would
let a researcher point a real run at a local model that no deployment actually has; every entry
below is provider "minimax" | "mistral" only, both of which llm.py's OpenAI-compatible client
already knows how to reach.
"""
from __future__ import annotations

import json
import os

from . import llm


def _builtin() -> list[dict]:
    """Computed fresh (not module-level) so it reflects the CURRENT env — MASSHINE_MODEL may be
    set after import (tests monkeypatch it; a real deploy sets it once at boot either way)."""
    return [
        {"id": "minimax-m3", "label": "MiniMax M3", "provider": "minimax",
         "model": os.environ.get("MASSHINE_MODEL", "MiniMax-M3"),
         "note": "Current production default — thinking-heavy, highest quality."},
        {"id": "glm-5-2", "label": "GLM-5.2 (Mistral, EU)", "provider": "mistral",
         "model": "glm-5-2",
         "note": "GDPR — hosted under the university's Mistral contract; "
                 "€1.19/M in, €3.74/M out."},
        {"id": "mistral-large", "label": "Mistral Large", "provider": "mistral",
         "model": "mistral-large-latest", "note": "EU-hosted general model."},
        {"id": "magistral-medium", "label": "Magistral Medium (reasoning)", "provider": "mistral",
         "model": "magistral-medium-latest", "note": "EU-hosted reasoning model."},
        {"id": "mistral-medium", "label": "Mistral Medium", "provider": "mistral",
         "model": "mistral-medium-latest", "note": "EU-hosted, cheaper."},
    ]


_REQUIRED = ("id", "provider", "model")


def _from_env() -> list[dict] | None:
    """MASSHINE_MODELS: a JSON array REPLACING the built-in list entirely. None (fall back to
    _builtin()) on: unset/empty, invalid JSON, not a list, or every entry malformed. Malformed
    individual entries are dropped with a warning, not fatal to the rest of the list."""
    raw = os.environ.get("MASSHINE_MODELS")
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print("[models] MASSHINE_MODELS is not valid JSON — falling back to the built-in "
              "registry", flush=True)
        return None
    if not isinstance(data, list):
        print("[models] MASSHINE_MODELS must be a JSON array — falling back to the built-in "
              "registry", flush=True)
        return None
    out = []
    for entry in data:
        if not isinstance(entry, dict) or not all(k in entry for k in _REQUIRED):
            print(f"[models] dropping malformed MASSHINE_MODELS entry (needs id/provider/model): "
                  f"{entry!r}", flush=True)
            continue
        out.append({"id": entry["id"], "label": entry.get("label", entry["id"]),
                    "provider": entry["provider"], "model": entry["model"],
                    "note": entry.get("note", "")})
    return out or None


def _registry() -> list[dict]:
    return _from_env() or _builtin()


def available(entry: dict) -> bool:
    """True when the entry's provider has credentials configured — mirrors llm.py's own
    resolution (minimax -> MASSHINE_API_KEY; mistral -> MISTRAL_API_KEY / MASSHINE_MISTRAL_API_KEY)."""
    if entry.get("provider") == "mistral":
        return bool(os.environ.get("MISTRAL_API_KEY") or os.environ.get("MASSHINE_MISTRAL_API_KEY"))
    if entry.get("provider") == "minimax":
        return bool(os.environ.get("MASSHINE_API_KEY"))
    return False


def list_models() -> list[dict]:
    return [dict(e, available=available(e)) for e in _registry()]


def resolve(model_id: str | None) -> dict | None:
    """The registry entry for `model_id`, or None (unknown id, or model_id itself falsy)."""
    if not model_id:
        return None
    return next((e for e in _registry() if e["id"] == model_id), None)


def server_default_id() -> str | None:
    """Which registry entry (if any) matches what llm.py resolves with NO override active — i.e.
    what a job actually runs today when neither a job param nor a project default is set. None
    when the env-configured provider/model doesn't match any listed entry (e.g. a bespoke
    MASSHINE_MODEL) — still a valid, just unlisted, server default."""
    provider = (os.environ.get("MASSHINE_PROVIDER", "").strip().lower()) or "minimax"
    resolved_model = llm.model()
    for e in _registry():
        if e["provider"] == provider and e["model"] == resolved_model:
            return e["id"]
    return None
