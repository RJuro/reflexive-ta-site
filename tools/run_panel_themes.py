#!/usr/bin/env python3
"""Distill a standpoint panel STRAIGHT into themes carrying paradigm provenance (for now).

panel (standard + standpoints, blind parallel) → pool codes (NO cross-paradigm reconcile)
→ theme pass → each theme annotated with which paradigms support it. Convergent themes (seen by
2+ lenses) are robust; paradigm-unique themes are the divergence, surfaced at the theme level.

The panel codes are cached so theming can be re-run without re-coding.
Usage:  .venv/bin/python ../tools/run_panel_themes.py [--recode] ["DP-40 GRANDE, M.txt"]
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

ARGS = [a for a in sys.argv[1:] if a != "--recode"]
RECODE = "--recode" in sys.argv
DOC = ARGS[0] if ARGS else "DP-40 GRANDE, M.txt"
PACK = m.ROOT.parent / "packs" / "migration_oral_history" / "standpoints"
CACHE = m.EXPORT_DIR / "panel_grande.json"
CODERS = {
    "standard":         (m.PROMPTS / "coder.prompt").read_text(encoding="utf-8"),
    "critical":         (PACK / "critical_political_economy.prompt").read_text(encoding="utf-8"),
    "phenomenological": (PACK / "phenomenological_memory.prompt").read_text(encoding="utf-8"),
}


def get_panel() -> dict:
    if CACHE.exists() and not RECODE:
        print(f"loaded panel cache ({CACHE.name}) — coding skipped")
        return json.loads(CACHE.read_text())
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "panel for themes")
    doc_id, secs, _ = m.ingest(conn, run, m.ROOT.parent / "transcripts_sample" / DOC)
    panel = m.code_sections_panel(conn, doc_id, CODERS)
    m.EXPORT_DIR.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(panel, indent=2, ensure_ascii=False))
    print(f"coded {DOC}: {secs and len(secs)} sections; cached → {CACHE.name}")
    return panel


def prov_str(p):
    return ", ".join(f"{k} {v}" for k, v in sorted(p.items(), key=lambda kv: -kv[1]))


def main() -> None:
    t0 = time.perf_counter()
    panel = get_panel()
    for name, codes in panel.items():
        print(f"  {name:16s}: {len(codes)} codes")

    themes, codebook, origin = m.theorize_panel(panel)
    dt = time.perf_counter() - t0

    convergent = [t for t in themes if len(t["paradigm_provenance"]) >= 2]
    unique = [t for t in themes if len(t["paradigm_provenance"]) == 1]
    print(f"\n{len(themes)} candidate themes ({len(convergent)} convergent, {len(unique)} "
          f"paradigm-unique), {dt:.0f}s\n")

    print("=== CONVERGENT themes (seen across 2+ paradigms — robust) ===")
    for t in sorted(convergent, key=lambda t: -len(t["paradigm_provenance"])):
        print(f"\n[{t['id']}] {t['central_concept']}")
        print(f"    provenance: {prov_str(t['paradigm_provenance'])}"
              f"   ({len(t['contradicting_code_ids'])} tension codes)")

    print("\n=== PARADIGM-UNIQUE themes (only one lens saw this — the divergence) ===")
    for t in unique:
        lens = next(iter(t["paradigm_provenance"]))
        print(f"\n[{t['id']}] ({lens}) {t['central_concept']}")
        print(f"    provenance: {prov_str(t['paradigm_provenance'])}")

    u = m.llm.usage()
    print(f"\nledger: {u['calls']} calls, {u['completion_tokens']:,} completion tokens")


if __name__ == "__main__":
    main()
