#!/usr/bin/env python3
"""Architecture comparison on ONE document: arm A (single_cot, coder only) vs arm B
(coder_critic). Both arms share the same coder pass per section; arm B adds a critic.
Reconciles each arm and reports the critic's verdicts — which ARE the logged A-vs-B
disagreement. The point isn't "B is better"; it's that two architectures diverge, and that
divergence is the data (the differentiator vs one-consensus-codebook tools).

Usage:  .venv/bin/python ../tools/compare_arch.py ["DP-40 GRANDE, M.txt"]
"""
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOC = sys.argv[1] if len(sys.argv) > 1 else "DP-40 GRANDE, M.txt"


def main() -> None:
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "arch compare A/B")
    path = m.ROOT.parent / "transcripts_sample" / DOC

    t0 = time.perf_counter()
    doc_id, secs, _ = m.ingest(conn, run, path)
    raw_a, raw_b, notes = m.code_sections_compare(conn, doc_id)
    cb_a = m.reconcile(raw_a)
    cb_b = m.reconcile(raw_b)
    dt = time.perf_counter() - t0

    counts = Counter(n["verdict"] for n in notes)
    print(f"\n=== {DOC} — {len(secs)} sections, {dt:.0f}s ===")
    print(f"arm A (single_cot):   {len(raw_a):3d} raw codes -> {len(cb_a)} after reconcile")
    print(f"arm B (coder_critic): {len(raw_b):3d} raw codes -> {len(cb_b)} after reconcile")
    print("\ncritic verdicts on arm A's codes (the A-vs-B disagreement):")
    for v in ("endorse", "revise", "challenge", "drop", "missing"):
        print(f"   {v:9s} {counts.get(v, 0)}")

    for v in ("revise", "challenge", "drop", "missing"):
        ex = [n for n in notes if n["verdict"] == v][:4]
        if ex:
            print(f"\n  {v.upper()} (the critic disagreed here):")
            for n in ex:
                tail = f"  — {n['issue']}" if n["issue"] else ""
                print(f"    · {n['label']}{tail}")

    u = m.llm.usage()
    print(f"\nledger: {u['calls']} calls, {u['completion_tokens']:,} completion tokens")


if __name__ == "__main__":
    main()
