#!/usr/bin/env python3
"""Phase-2 measurement probe: does MiniMax implicit prompt-caching actually hit MASSHINE's coder
calls? Fires the standard coder prompt at N real section blocks (reconstructed from the panel cache,
so no structure() call) SEQUENTIALLY — a warm shared system-prompt prefix should show up as
usage.prompt_tokens_details.cached_tokens on calls 2+. Reads the per-label ledger and prints the
cache-hit rate + think/json output split + timing. A few paid calls; thinking stays ON.

Usage:  .venv/bin/python ../tools/cache_probe.py [N]   (default N=4)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
CACHE = m.EXPORT_DIR / "panel_2interview.json"
state = json.loads(CACHE.read_text(encoding="utf-8"))
coder = (m.PROMPTS / "coder.prompt").read_text(encoding="utf-8")

# Reconstruct real section blocks from the cache (same shape code_sections builds).
blocks = []
for doc_id, doc in state["docs"].items():
    by_sec = {}
    for s in doc["sentences"]:
        by_sec.setdefault(s["section_id"], []).append(s)
    gist = {s["id"]: s["gist"] for s in doc["sections"]}
    for sec_id, sents in by_sec.items():
        body = "\n".join(f"[{s['id']}] {' '.join(s['text'].split())}" for s in sents)
        blocks.append(f"## {sec_id} — {gist.get(sec_id, '')}\n{body}")
    if len(blocks) >= N:
        break
blocks = blocks[:N]

print(f"coder.prompt ≈ {len(coder)} chars; firing {len(blocks)} sequential coder calls "
      f"(thinking on)…\n", flush=True)
m.llm.reset_usage()
for i, block in enumerate(blocks, 1):
    m.llm.chat_json(coder, block, label="coder")
    u = m.llm.usage()["by_label"]["coder"]
    print(f"  call {i}: cumulative prompt={u['prompt_tokens']:,} cached={u['cached_tokens']:,} "
          f"completion={u['completion_tokens']:,}", flush=True)

u = m.llm.usage()
c = u["by_label"]["coder"]
pt, ct = c["prompt_tokens"], c["cached_tokens"]
think, jsn = c["think_chars"], c["json_chars"]
print("\n=== coder ledger ===")
print(f"calls              {c['calls']}")
print(f"prompt tokens      {pt:,}")
print(f"cached (hit)       {ct:,}  → {100*ct/pt:.0f}% of prompt tokens were cache hits" if pt else "n/a")
print(f"completion tokens  {c['completion_tokens']:,}")
print(f"output think chars {think:,}  vs json chars {jsn:,}  → think is "
      f"{100*think/(think+jsn):.0f}% of output" if (think+jsn) else "n/a")
print(f"wall               {c['wall_s']:.0f}s total, {c['wall_s']/c['calls']:.0f}s/call")
