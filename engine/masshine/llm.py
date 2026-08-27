"""Minimal LLM client — OpenAI-compatible MiniMax-M3 (Q11) by default, KISS. A second backend
(codex-cli, P10.1a) is a thin dispatch on top of the same `chat_json` signature and ledger — the
two never mix mid-call, so everything below the dispatch in `chat_json` is unchanged.

codex-cli is LOCAL CALIBRATION ONLY — never a deployed backend. It exists solely for
tools/read_span_calibrate.py to run the read-span experiment against a local model at zero API
cost before the default span is fixed on M3. The default backend is, and stays, "openai";
nothing in this codebase selects codex-cli implicitly — it activates only when something sets
MASSHINE_LLM_BACKEND=codex-cli itself, which the deployed app and its config never do. Do not
wire it into a production/deploy path, and do not document it anywhere deployment-facing (see
the calibration harness's own header for its usage).

No temperature / sampling overrides — modern models are tuned for their defaults; we don't touch
them during dev. Thinking stays ON (M3's default) — the reasoning trace is the interpretive lift.

Instrumentation (Phase 2, measurement-first): a side ledger tracks calls, prompt/completion tokens,
IMPLICIT-CACHE hit tokens (usage.prompt_tokens_details.cached_tokens), think-vs-json output split,
wall time and time-to-first-token — per call and per label — so we can SEE cache efficacy and
thinking overhead before changing anything. Set MASSHINE_LLM_LOG=1 to also append one JSON line per
call to exports/llm_log.jsonl. The same ledger also carries `audio_seconds` (P10.1b): Voxtral ASR
calls don't go through chat_json (see masshine/transcribe.py) but feed the ledger via
`record_audio_usage` so `usage()` reports audio cost alongside LLM cost.

Config from engine/.env (gitignored) or env:
    MASSHINE_BASE_URL, MASSHINE_API_KEY, MASSHINE_MODEL, MASSHINE_RETRIES (default 0 extra retries)
    MASSHINE_LLM_BACKEND ("openai" default | "codex-cli", local-calibration-only — see above),
    MASSHINE_CODEX_MODEL (codex-cli only)

Provider profiles (still the "openai" backend — same OpenAI-compatible client, just a different
base/key/model triple; this is deployment-relevant, unlike codex-cli): MASSHINE_PROVIDER unset
or empty is EXACTLY the original behavior (MASSHINE_BASE_URL/MASSHINE_API_KEY/MASSHINE_MODEL —
the MiniMax production config). MASSHINE_PROVIDER=mistral switches to api.mistral.ai (verified
OpenAI-compatible, including streaming + prompt_tokens_details.cached_tokens — a GDPR-compliant
alternative for EU deployments): base_url defaults to https://api.mistral.ai/v1 (override
MASSHINE_MISTRAL_BASE_URL), the key comes from MISTRAL_API_KEY (fallback
MASSHINE_MISTRAL_API_KEY), model defaults to "glm-5-2" (override MASSHINE_MISTRAL_MODEL).
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
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
           "think_chars", "json_chars", "wall_s", "audio_seconds")
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


# ---- researcher-selectable model override (P10.1c) ---------------------------------------------
# A ContextVar, not a global: jobs.py enters `use_model(entry)` FROM CODE THAT ALREADY RUNS ON THE
# WORKER THREAD (inside the job body, not at submit() time) — contextvars don't cross threads on
# their own, so this only works because we never need them to; each job sets/resets its own value
# on the single worker thread, so two sequential jobs never see each other's override. `entry` is
# one masshine.models registry dict ({"id", "provider", "model", ...}) or None (no override — every
# resolver below falls through to today's env-only behavior, unchanged for any caller that never
# opts in). codex-cli is never a valid entry here — see models.py's module comment.
_ACTIVE_MODEL: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "masshine_active_model", default=None)


@contextmanager
def use_model(entry: dict | None):
    token = _ACTIVE_MODEL.set(entry)
    try:
        yield
    finally:
        _ACTIVE_MODEL.reset(token)


def active_model() -> dict | None:
    """The registry entry currently overriding provider+model on this thread, or None."""
    return _ACTIVE_MODEL.get()


def _provider() -> str:
    """"" (default) or "mistral" — an OpenAI-COMPATIBLE provider profile switch, not a new
    backend: both go through the same streamed client below. See the module docstring. An active
    use_model() override wins first ("minimax" reads the same as "" below — only "mistral" is
    ever branched on)."""
    entry = _ACTIVE_MODEL.get()
    if entry:
        return entry["provider"]
    return os.environ.get("MASSHINE_PROVIDER", "").strip().lower()


def _resolved_base_and_key() -> tuple[str | None, str | None]:
    """(base_url, api_key) for the active provider profile. Unset/empty MASSHINE_PROVIDER is
    EXACTLY the original MASSHINE_BASE_URL/MASSHINE_API_KEY pair — untouched."""
    if _provider() == "mistral":
        base = os.environ.get("MASSHINE_MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
        key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("MASSHINE_MISTRAL_API_KEY")
        return base, key
    return os.environ.get("MASSHINE_BASE_URL"), os.environ.get("MASSHINE_API_KEY")


def model() -> str:
    """The ACTIVE resolved model — an active use_model() override wins outright (this feeds the
    usage ledger, db.new_run's per-run provenance, and the export manifest), else today's env
    resolution."""
    entry = _ACTIVE_MODEL.get()
    if entry:
        return entry["model"]
    if _provider() == "mistral":
        return os.environ.get("MASSHINE_MISTRAL_MODEL", "glm-5-2")
    return os.environ.get("MASSHINE_MODEL", "MiniMax-M3")


def _default_retries() -> int:
    try:
        return int(os.environ.get("MASSHINE_RETRIES", "0"))
    except ValueError:
        return 0


def _client(timeout: float | None = None, retries: int | None = None) -> OpenAI:
    base, key = _resolved_base_and_key()
    if not (base and key):
        if _provider() == "mistral":
            raise RuntimeError("set MISTRAL_API_KEY (or MASSHINE_MISTRAL_API_KEY) for the "
                               "mistral provider profile (MASSHINE_PROVIDER=mistral)")
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
            think_c: int, json_c: int, wall_s: float, ttft_s: float | None,
            audio_s: float = 0.0) -> None:
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        _USAGE["prompt_tokens"] += prompt_t
        _USAGE["completion_tokens"] += completion_t
        _USAGE["cached_tokens"] += cached_t
        _USAGE["think_chars"] += think_c
        _USAGE["json_chars"] += json_c
        _USAGE["wall_s"] += wall_s
        _USAGE["audio_seconds"] += audio_s
        d = _BY_LABEL.setdefault(label or "unlabeled", {k: 0 for k in _FIELDS})
        d["calls"] += 1
        d["prompt_tokens"] += prompt_t
        d["completion_tokens"] += completion_t
        d["cached_tokens"] += cached_t
        d["think_chars"] += think_c
        d["json_chars"] += json_c
        d["wall_s"] += wall_s
        d["audio_seconds"] += audio_s
    if os.environ.get("MASSHINE_LLM_LOG"):
        _append_log({"label": label or "unlabeled", "model": model(),
                     "prompt_tokens": prompt_t, "cached_tokens": cached_t,
                     "completion_tokens": completion_t, "think_chars": think_c,
                     "json_chars": json_c, "wall_s": round(wall_s, 2),
                     "ttft_s": round(ttft_s, 2) if ttft_s is not None else None,
                     "audio_seconds": round(audio_s, 2) if audio_s else None})


def record_audio_usage(label: str, *, prompt_audio_seconds: float, prompt_tokens: int,
                       completion_tokens: int, wall_s: float) -> None:
    """The ASR ledger hook (P10.1b): transcribe.py's Voxtral calls don't go through chat_json (no
    chat-completions shape, no streaming, no thinking) but still cost real money and time, so they
    ride the SAME ledger — one `_record` per real Voxtral call, under whatever label the caller
    passes (transcribe.py uses "asr"). think_chars/json_chars/cached_tokens don't apply to an ASR
    call and stay 0; audio_seconds is the one field only this path ever writes."""
    _record(label, int(prompt_tokens), int(completion_tokens), 0, 0, 0, wall_s, None,
            audio_s=float(prompt_audio_seconds))


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


def backend() -> str:
    """"openai" (default, the deployed backend — always wins unless something sets the env var
    itself) or "codex-cli" (local calibration only, see the module docstring)."""
    return os.environ.get("MASSHINE_LLM_BACKEND", "openai")


def chat_json(system: str, user: str, timeout: float | None = None,
              retries: int | None = None, label: str = "") -> dict:
    """One structured call → parsed JSON. Default backend is the OpenAI-compatible streamed
    client below; MASSHINE_LLM_BACKEND=codex-cli routes to `_codex_chat_json` instead (same
    signature and ledger, no streaming, no retry loop — see its docstring). That env var is
    LOCAL-CALIBRATION-ONLY (see the module docstring) — nothing in this codebase sets it, so the
    dispatch always takes the openai path unless something outside this module opts in explicitly.

    OpenAI path: STREAMED, default sampling (we don't set temperature); thinking stays ON (M3's
    default). Streaming makes `timeout` an IDLE timeout (see _client): the call runs as long as
    tokens keep arriving and aborts only after `timeout` seconds of silence, so a long <think>
    trace no longer trips a cap and a true hang still dies. The streamed deltas are concatenated;
    `<think>…</think>` is stripped before the JSON is parsed.

    `retries` = EXTRA whole-call retries with exponential backoff around stream consumption (for a
    mid-stream idle death the SDK's request-level retry can't cover). Defaults to MASSHINE_RETRIES
    (0). The theorist passes retries=0 explicitly — its no-retry/resume semantics are load-bearing.
    `label` tags the ledger (structure / coder / panel:<lens> / reconcile / theorist:step<i> / read)."""
    if backend() == "codex-cli":
        return _codex_chat_json(system, user, timeout, label)
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


# ---- codex-cli backend (P10.1a) — LOCAL CALIBRATION ONLY, never a deployed backend --------------
# `codex exec` as a subprocess, JSON events on stdout, the prompt on STDIN (arg "-" tells codex to
# read it there instead of argv, sidestepping OS arg-length limits on a whole-transcript prompt).
# Verified invocation on this machine, codex-cli 0.147.0. Only reachable via an explicit
# MASSHINE_LLM_BACKEND=codex-cli — see the module docstring; do not call this from any code path
# that a deployment could reach by default.

_CODEX_DEFAULT_TIMEOUT = 1200.0  # local inference is slow; this is a plain subprocess timeout,
                                 # NOT the openai path's idle timeout — there is no streaming here.


def _codex_chat_json(system: str, user: str, timeout: float | None, label: str) -> dict:
    """codex-cli backend for chat_json (dev/calibration only — see the module docstring). Codex
    has no separate system slot, so `system` and `user` are concatenated into one prompt. stdout
    is JSONL; the answer text is the LAST `item.completed` event whose item is an `agent_message`
    (codex can emit more than one turn of scratch/tool chatter before its final answer — only the
    last agent_message is the answer). Usage rides a `turn.completed` event. stderr can carry
    harmless noise (codex-cli is known to log e.g. "failed to load models cache" even on a clean
    run) — it is captured but never inspected; only stdout's parseability decides success. A
    missing `codex` binary fails loudly (FileNotFoundError re-raised with a clear message) —
    there is no silent fallback to the paid openai path."""
    model = os.environ.get("MASSHINE_CODEX_MODEL")
    if not model:
        raise RuntimeError("set MASSHINE_CODEX_MODEL (e.g. gpt-5.6-luna) for the codex-cli backend")
    prompt = f"{system}\n\n----\n\n{user}"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["codex", "exec", "--model", model, "--skip-git-repo-check", "--ephemeral",
             "-s", "read-only", "--json", "-"],
            input=prompt, capture_output=True, text=True, timeout=timeout or _CODEX_DEFAULT_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "codex-cli backend selected (MASSHINE_LLM_BACKEND=codex-cli) but the `codex` binary "
            "is not on PATH — install codex-cli or unset MASSHINE_LLM_BACKEND. Refusing to fall "
            "back to the openai backend silently."
        ) from e
    wall = time.perf_counter() - t0
    text, prompt_t, completion_t, reasoning_t = _parse_codex_jsonl(proc.stdout)
    if not text:
        raise RuntimeError(
            f"codex-cli returned no agent_message (exit {proc.returncode}); "
            f"stderr tail: {proc.stderr[-500:]}")
    payload = _json_from(text)
    # reasoning_output_tokens has no dedicated ledger slot; it rides think_chars the way the
    # openai path's <think>-block char count approximates thinking overhead — a token count in a
    # char-count field, so this is approximate by construction, not a token-accurate figure.
    _record(label, prompt_t, completion_t, 0, reasoning_t, len(payload), wall, None)
    return json.loads(payload)


def _parse_codex_jsonl(stdout: str) -> tuple[str, int, int, int]:
    """(answer_text, input_tokens, output_tokens, reasoning_output_tokens) from codex exec's
    JSONL stdout. Non-JSON lines are skipped (codex can interleave plain progress noise); the
    LAST agent_message wins if several turns complete."""
    text = ""
    prompt_t = completion_t = reasoning_t = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev_type = ev.get("type")
        if ev_type == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text") or text
        elif ev_type == "turn.completed":
            usage = ev.get("usage") or {}
            prompt_t = usage.get("input_tokens", 0) or 0
            completion_t = usage.get("output_tokens", 0) or 0
            reasoning_t = usage.get("reasoning_output_tokens", 0) or 0
    return text, prompt_t, completion_t, reasoning_t
