"""Minimal LLM client — OpenAI-compatible MiniMax-M3 (Q11). KISS, one provider.

No temperature / sampling overrides — modern models are tuned for their defaults; we don't touch
them during dev. Thinking stays ON (M3's default) — the reasoning trace is the interpretive lift.

Instrumentation (Phase 2, measurement-first): a side ledger tracks calls, prompt/completion tokens,
IMPLICIT-CACHE hit tokens (usage.prompt_tokens_details.cached_tokens), think-vs-json output split,
wall time and time-to-first-token — per call and per label — so we can SEE cache efficacy and
thinking overhead before changing anything. Set MASSHINE_LLM_LOG=1 to also append one JSON line per
call to exports/llm_log.jsonl.

Config from engine/.env (gitignored) or env:
    MASSHINE_BASE_URL, MASSHINE_API_KEY, MASSHINE_MODEL, MASSHINE_RETRIES (default 0 extra retries)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from openai import OpenAI

# .env lives at the engine root (engine/.env); this module sits at engine/masshine/llm.py.
_ENV = Path(__file__).resolve().parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_FIELDS = ("calls", "prompt_tokens", "completion_tokens", "cached_tokens",
           "think_chars", "json_chars", "wall_s")
_USAGE = {k: 0 for k in _FIELDS}
_BY_LABEL: dict[str, dict] = {}
_USAGE_LOCK = threading.Lock()  # parallel coder calls touch this


def usage() -> dict:
    """Flat totals (old keys `calls`/`prompt_tokens`/`completion_tokens` preserved) plus the new
    `cached_tokens`/`think_chars`/`json_chars`/`wall_s`, and a `by_label` breakdown."""
    with _USAGE_LOCK:
        out = dict(_USAGE)
        out["by_label"] = {k: dict(v) for k, v in _BY_LABEL.items()}
    return out


def reset_usage() -> None:
    with _USAGE_LOCK:
        for k in _USAGE:
            _USAGE[k] = 0
        _BY_LABEL.clear()


def model() -> str:
    return os.environ.get("MASSHINE_MODEL", "MiniMax-M3")


def _default_retries() -> int:
    try:
        return int(os.environ.get("MASSHINE_RETRIES", "0"))
    except ValueError:
        return 0


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


def _record(label: str, prompt_t: int, completion_t: int, cached_t: int,
            think_c: int, json_c: int, wall_s: float, ttft_s: float | None) -> None:
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        _USAGE["prompt_tokens"] += prompt_t
        _USAGE["completion_tokens"] += completion_t
        _USAGE["cached_tokens"] += cached_t
        _USAGE["think_chars"] += think_c
        _USAGE["json_chars"] += json_c
        _USAGE["wall_s"] += wall_s
        d = _BY_LABEL.setdefault(label or "unlabeled", {k: 0 for k in _FIELDS})
        d["calls"] += 1
        d["prompt_tokens"] += prompt_t
        d["completion_tokens"] += completion_t
        d["cached_tokens"] += cached_t
        d["think_chars"] += think_c
        d["json_chars"] += json_c
        d["wall_s"] += wall_s
    if os.environ.get("MASSHINE_LLM_LOG"):
        _append_log({"label": label or "unlabeled", "model": model(),
                     "prompt_tokens": prompt_t, "cached_tokens": cached_t,
                     "completion_tokens": completion_t, "think_chars": think_c,
                     "json_chars": json_c, "wall_s": round(wall_s, 2),
                     "ttft_s": round(ttft_s, 2) if ttft_s is not None else None})


def _append_log(row: dict) -> None:
    try:
        from .config import EXPORT_DIR
        EXPORT_DIR.mkdir(exist_ok=True)
        with _USAGE_LOCK:
            with (EXPORT_DIR / "llm_log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break a run


def _cached_tokens(usage_obj) -> int:
    """Implicit-cache hit tokens. OpenAI-SDK path exposes them under prompt_tokens_details."""
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is None:
        return 0
    if isinstance(details, dict):
        return int(details.get("cached_tokens", 0) or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)


def chat_json(system: str, user: str, timeout: float | None = None,
              retries: int | None = None, label: str = "") -> dict:
    """One structured call → parsed JSON, STREAMED. Default sampling (we don't set temperature);
    thinking stays ON (M3's default). Streaming makes `timeout` an IDLE timeout (see _client): the
    call runs as long as tokens keep arriving and aborts only after `timeout` seconds of silence, so
    a long <think> trace no longer trips a cap and a true hang still dies. The streamed deltas are
    concatenated; `<think>…</think>` is stripped before the JSON is parsed.

    `retries` = EXTRA whole-call retries with exponential backoff around stream consumption (for a
    mid-stream idle death the SDK's request-level retry can't cover). Defaults to MASSHINE_RETRIES
    (0). The theorist passes retries=0 explicitly — its no-retry/resume semantics are load-bearing.
    `label` tags the ledger (structure / coder / panel:<lens> / reconcile / theorist:step<i>)."""
    outer = retries if retries is not None else _default_retries()
    attempt = 0
    while True:
        try:
            return _stream_once(system, user, timeout, retries, label)
        except Exception:
            if attempt >= outer:
                raise
            time.sleep(min(8, 2 ** attempt))
            attempt += 1


def _stream_once(system: str, user: str, timeout, retries, label: str) -> dict:
    parts: list[str] = []
    usage = None
    t0 = time.perf_counter()
    ttft = None
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
                if ttft is None:
                    ttft = time.perf_counter() - t0
                parts.append(piece)
    text = "".join(parts)
    wall = time.perf_counter() - t0
    think_c = sum(len(m) for m in re.findall(r"<think>.*?</think>", text, flags=re.DOTALL))
    prompt_t = getattr(usage, "prompt_tokens", 0) or 0 if usage else 0
    completion_t = getattr(usage, "completion_tokens", 0) or 0 if usage else 0
    cached_t = _cached_tokens(usage) if usage else 0
    payload = _json_from(text)
    _record(label, prompt_t, completion_t, cached_t, think_c, len(payload), wall, ttft)
    return json.loads(payload)


def _json_from(text: str) -> str:
    # M3 is a reasoning model: drop <think>…</think> first, then take the {...} block.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i != -1 and j != -1 else text
