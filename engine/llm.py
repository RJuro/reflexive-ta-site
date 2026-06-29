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
    # ponytail: with streaming (see chat_json) this is an IDLE timeout — httpx applies the read
    # timeout per chunk, so it bounds SILENCE between tokens, not total call time. A healthy
    # thinking-on call streams steadily; ~120s with no token means it's actually hung. This removes
    # the per-call duration caps we kept re-tuning (a long <think> trace no longer trips it) while
    # still killing a true hang, and tells "slow" apart from "failed". Workers catch failures so one
    # bad call degrades instead of crashing the run.
    return OpenAI(base_url=base, api_key=key, timeout=timeout or 120.0,
                  max_retries=1 if retries is None else retries)


def chat_json(system: str, user: str, timeout: float | None = None,
              retries: int | None = None) -> dict:
    """One structured call → parsed JSON, STREAMED. Default sampling (we don't set temperature);
    thinking stays ON (M3's default). Streaming makes `timeout` an IDLE timeout (see _client): the
    call runs as long as tokens keep arriving and aborts only after `timeout` seconds of silence, so
    a long <think> trace no longer trips a cap and a true hang still dies. The streamed deltas are
    concatenated; `<think>…</think>` is stripped before the JSON is parsed."""
    parts: list[str] = []
    usage = None
    stream = _client(timeout, retries).chat.completions.create(
        model=model(),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        stream=True,
        stream_options={"include_usage": True},  # usage rides the final chunk
    )
    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        for choice in (chunk.choices or []):
            piece = getattr(getattr(choice, "delta", None), "content", None)
            if piece:
                parts.append(piece)
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        if usage:
            _USAGE["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            _USAGE["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
    return json.loads(_json_from("".join(parts)))


def _json_from(text: str) -> str:
    # M3 is a reasoning model: drop <think>…</think> first, then take the {...} block.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i != -1 and j != -1 else text
