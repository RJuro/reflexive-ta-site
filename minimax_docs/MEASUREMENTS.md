# MiniMax-M3 usage measurements (MASSHINE)

Instrumented `masshine/llm.py` now logs, per call and per label: prompt / completion tokens,
implicit-cache hit tokens (`usage.prompt_tokens_details.cached_tokens`), think-vs-json output
split, wall time and time-to-first-token. Set `MASSHINE_LLM_LOG=1` for a per-call JSONL at
`exports/llm_log.jsonl`.

## Probe: does implicit prompt-caching help the coder path?

`tools/cache_probe.py` fired the standard `coder.prompt` at 4 real section blocks **sequentially**
(warm shared system-prompt prefix), thinking on. Measured 2026-07-02, MiniMax-M3:

| metric | value |
|---|---|
| coder.prompt size | ~1,571 chars (~400 tokens) |
| calls | 4 |
| prompt tokens (total) | 5,325 |
| **cached (hit) tokens** | **448 → 8% of input** |
| completion tokens | 8,041 |
| **output think vs json** | **19,496 vs 17,187 chars → think is 53% of output** |
| wall | 158s total, ~40s/call |

## Reading

1. **Implicit caching is on but marginal here (~8%).** The only byte-stable shared prefix across
   coder calls is the ~400-token system prompt, which sits under MiniMax's 512-token cache floor as
   a standalone prefix, so only a sliver caches. Nothing to configure — it already works as much as
   it can.
2. **Input is the minority of cost.** 5.3k prompt vs 8.0k completion tokens. Caching attacks the
   smaller half; even perfect input caching (and only ~8% is cacheable) barely moves the total.
3. **Thinking is ~half the output.** 53% of generated characters are the `<think>` trace. That is
   the real cost/latency lever — but thinking is deliberately ON (the reasoning trace is the
   interpretive lift the method wants), so this is an accepted, now-quantified cost, not a bug.

## Decision (Phase 2 gate)

**Do NOT adopt explicit caching (`cache_control` / Anthropic-format endpoint).** It would add a
client/endpoint swap to save a fraction of the *smaller* half of the bill. Implicit caching already
captures the available prefix reuse. Keep the OpenAI-compatible client as-is.

**Kept from Phase 2:** cache-hit + think-share + timing telemetry (per-label ledger, JSONL log),
configurable extra retries with backoff via `MASSHINE_RETRIES` (default 0; the theorist keeps
`retries=0`), and `label=` on every call site (`structure`, `coder`, `panel:<lens>`, `reconcile`,
`reconcile:incremental`, `theorist:step<i>`). These make cost/latency observable for the grant's
architecture-comparison runs without changing model behavior.

**If input ever dominates** (much larger codebooks/transcripts in the cached prefix), revisit: the
upgrade path is an `MASSHINE_API_FORMAT=anthropic` backend inside `chat_json` with `cache_control`
on the system block — same signature, call sites unchanged.
