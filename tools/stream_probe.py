#!/usr/bin/env python3
"""Probe: does the MiniMax-M3 endpoint STREAM tokens incrementally (so an idle timeout works), or
buffer the whole response (incl. <think>) and send it at the end (idle timeout would misfire)?

Prints time-to-first-chunk (TTFC) and the largest gap between chunks. If TTFC and max-gap are both
small while a long answer is produced, streaming is incremental → idle timeout is safe.

Usage:  .venv/bin/python ../tools/stream_probe.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import llm  # noqa: E402 (also loads engine/.env)

client = llm._client(120.0, 0)
t0 = time.perf_counter()
stream = client.chat.completions.create(
    model=llm.model(),
    messages=[
        {"role": "system", "content": "You are a careful qualitative researcher. Think carefully before answering."},
        {"role": "user", "content": "From oral-history migration interviews, propose 8 distinct candidate "
         "themes, each a one-sentence arguable claim with a brief justification. Reason it through first."},
    ],
    stream=True, stream_options={"include_usage": True},
)
n = chars = 0
ttfc = None
last = t0
maxgap = 0.0
for chunk in stream:
    now = time.perf_counter()
    for ch in (chunk.choices or []):
        piece = getattr(getattr(ch, "delta", None), "content", None)
        if piece:
            if ttfc is None:
                ttfc = now - t0
                print(f"  first chunk at {ttfc:.1f}s", flush=True)
            n += 1
            chars += len(piece)
            gap = now - last
            last = now
            maxgap = max(maxgap, gap)
            if n % 200 == 0:
                print(f"  {now - t0:5.1f}s  chunks={n} chars={chars} max-gap={maxgap:.1f}s", flush=True)
total = time.perf_counter() - t0
print(f"\nTTFC={ttfc:.1f}s  total={total:.1f}s  chunks={n}  chars={chars}  MAX-GAP={maxgap:.1f}s")
print("VERDICT:", "incremental streaming — idle timeout is safe" if (ttfc or 999) < 20 and maxgap < 20
      else "looks buffered / long pauses — idle timeout needs to exceed the gap")
