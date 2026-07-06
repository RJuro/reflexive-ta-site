#!/usr/bin/env python3
"""Standpoint panel on ONE document: the standard coder + 2 declared-standpoint personas read the
same sections blind and in parallel; frictions are surfaced sentence-by-sentence.

This is the structured-disagreement output — not one consensus codebook, but "on THIS sentence, the
standard coder saw X, the critical lens saw Y, the phenomenological lens saw Z (or nothing)."

Usage:  .venv/bin/python ../tools/run_panel.py ["DP-40 GRANDE, M.txt"]
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402
from masshine import packs  # noqa: E402

DOC = sys.argv[1] if len(sys.argv) > 1 else "DP-40 GRANDE, M.txt"
CODERS = packs.panel_coders("migration_oral_history")  # standard + the pack's standpoint lenses


def short(conn, ev, n=130):
    t = " ".join(m.resolve_ev(conn, ev).split())
    return t[:n] + ("…" if len(t) > n else "")


def codelabels(codes):
    return "; ".join(f"({c['code_type'][:3]}) {c['label']}" for c in codes)


def main() -> None:
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "standpoint panel")
    path = m.ROOT.parent / "transcripts_sample" / DOC

    t0 = time.perf_counter()
    doc_id, secs, _ = m.ingest(conn, run, path)
    panel, _ = m.code_sections_panel(conn, doc_id, CODERS)
    fr = m.friction(panel)
    dt = time.perf_counter() - t0

    print(f"\n=== {DOC} — {len(secs)} sections, {len(CODERS)} coders, {dt:.0f}s ===")
    for name in CODERS:
        print(f"  {name:16s}: {len(panel[name]):3d} codes, touched {fr['coverage'][name]:3d} sentences")

    # INTERPRETIVE friction — sentences coded by 2+ coders, richest first
    inter = sorted(fr["interpretive"].items(), key=lambda kv: -len(kv[1]))
    print(f"\n--- INTERPRETIVE friction: {len(inter)} sentences read by 2+ coders (same evidence, "
          f"different reading) ---")
    for ev, cm in inter[:7]:
        print(f"\n  [{ev}] \"{short(conn, ev)}\"")
        for name in CODERS:
            if name in cm:
                print(f"     {name:16s} → {codelabels(cm[name])}")

    # ATTENTIONAL friction — what each STANDPOINT coded that the STANDARD did not
    print(f"\n--- ATTENTIONAL friction: what a standpoint saw that the STANDARD did not ---")
    for lens in ("critical", "phenomenological"):
        solo = [(ev, cm[lens]) for ev, cm in fr["by_sent"].items()
                if lens in cm and "standard" not in cm]
        print(f"\n  {lens} coded {len(solo)} sentences the standard coder ignored, e.g.:")
        for ev, codes in solo[:4]:
            print(f"     [{ev}] {codelabels(codes)}")
            print(f"        \"{short(conn, ev, 110)}\"")

    u = m.llm.usage()
    print(f"\nledger: {u['calls']} calls, {u['completion_tokens']:,} completion tokens")


if __name__ == "__main__":
    main()
