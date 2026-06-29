"""Minimal LLM client — OpenAI-compatible MiniMax-M3 (Q11). KISS, one provider.

No temperature / sampling overrides — modern models are tuned for their defaults;
we don't touch them during dev. Tracks token + call usage on the side (a ledger).

Config from engine/.env (gitignored) or env:
    MASSHINE_BASE_URL, MASSHINE_API_KEY, MASSHINE_MODEL
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from openai import OpenAI

_ENV = Path(__file__).resolve().parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
_USAGE_LOCK = threading.Lock()  # parallel coder calls touch this


def usage() -> dict:
    return dict(_USAGE)


def reset_usage() -> None:
    for k in _USAGE:
        _USAGE[k] = 0


def model() -> str:
    return os.environ.get("MASSHINE_MODEL", "MiniMax-M3")


def _client(timeout: float | None = None, retries: int | None = None) -> OpenAI:
    base, key = os.environ.get("MASSHINE_BASE_URL"), os.environ.get("MASSHINE_API_KEY")
    if not (base and key):
        raise RuntimeError("set MASSHINE_BASE_URL and MASSHINE_API_KEY (see engine/.env)")
    # ponytail: a healthy thinking-on M3 call runs ~1 min (manual runs confirm this), so the DEFAULT
    # cap is 120s (2 min) — generous for structure/coder/reconcile; a call that exceeds it is a
    # PROBLEM to surface and resume from, not a wait to sit through (the pipeline is resumable, so
    # failing fast + re-running beats a long hang). The ONE accepted exception is the theorist, which
    # reads a whole transcript with thinking on (~150–300s) and passes its own longer per-call timeout.
    return OpenAI(base_url=base, api_key=key, timeout=timeout or 120.0,
                  max_retries=1 if retries is None else retries)


def chat_json(system: str, user: str, timeout: float | None = None,
              retries: int | None = None) -> dict:
    """One structured call → parsed JSON. Default sampling (we don't set temperature).
    Thinking stays ON (M3's default) — the reasoning trace is the interpretive lift.
    `timeout`/`retries` override the per-call ceiling for heavy calls (e.g. the theorist)."""
    resp = _client(timeout, retries).chat.completions.create(
        model=model(),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    u = getattr(resp, "usage", None)
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        if u:
            _USAGE["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            _USAGE["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
    return json.loads(_json_from(resp.choices[0].message.content))


def _json_from(text: str) -> str:
    # M3 is a reasoning model: drop <think>…</think> first, then take the {...} block.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i != -1 and j != -1 else text
