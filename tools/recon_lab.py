#!/usr/bin/env python3
"""Reconcile lab — code two docs ONCE (cached), then iterate the reconcile prompt fast.

Coding (structure + per-section coders, thinking on) is the ~12-min cost; it is cached to
JSON. Reconcile reads only the cached raw codes (no DB, no re-coding), so iterating the
prompt in prompts/reconcile.prompt costs only the reconcile calls (~1-2 min each).

It mirrors the real pipeline — within-doc reconcile per doc, then a cross-doc reconcile over
the per-doc codebooks — and PRINTS each merge group (member labels) so we can judge merge
QUALITY, not just the count.

Usage:
    .venv/bin/python ../tools/recon_lab.py            # use cache (code first if missing)
    .venv/bin/python ../tools/recon_lab.py --recode   # force a fresh coding pass
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOCS = ["DP-40 GRANDE, M.txt", "EI-845 RODWIN.txt"]
CACHE = m.EXPORT_DIR / "raw_codes_cache.json"


def code_all() -> dict:
    """Run the expensive coding pass once; cache raw per-doc codes to JSON."""
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "recon_lab cache")
    out: dict[str, list] = {}
    for name in DOCS:
        path = m.ROOT.parent / "transcripts_sample" / name
        doc_id, secs, _ = m.ingest(conn, run, path)
        raw, dropped = m.code_sections(conn, doc_id)
        out[doc_id] = raw
        print(f"coded {name}: {len(secs)} sections → {len(raw)} raw codes ({dropped} dropped)")
    m.EXPORT_DIR.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"cached → {CACHE.relative_to(m.ROOT.parent)}\n")
    return out


def reconcile_show(codes: list[dict], title: str) -> list[dict]:
    """Reconcile a code list, print every merge group with member labels, return merged."""
    system, listing, tmp = m._reconcile_messages(codes)
    t0 = time.perf_counter()
    try:
        groups = m.llm.chat_json(system, listing).get("groups", [])
    except Exception as e:
        print(f"[{title}] reconcile FAILED ({type(e).__name__})")
        return codes
    dt = time.perf_counter() - t0

    merges = [g for g in groups if len(g.get("members", [])) > 1]
    collapsed = sum(len(g["members"]) - 1 for g in merges)
    print(f"\n=== {title} ===")
    print(f"{len(codes)} codes → {len(codes) - collapsed} after merge "
          f"({len(merges)} merge groups collapse {collapsed} codes, {dt:.0f}s)")
    for g in merges:
        members = [tmp[mid] for mid in g.get("members", []) if mid in tmp]
        if len(members) < 2:
            continue
        keep = g.get("keep")
        print("  MERGE:")
        for mid, c in zip(g["members"], members):
            mark = "*" if mid == keep else " "
            print(f"   {mark}({c['code_type']}) {c['label']}")
    return m.reconcile(codes)  # reuse the real apply logic for the returned codebook


def main() -> None:
    recode = "--recode" in sys.argv
    if recode or not CACHE.exists():
        raw = code_all()
    else:
        raw = json.loads(CACHE.read_text())
        print(f"loaded cache ({sum(len(v) for v in raw.values())} raw codes across "
              f"{len(raw)} docs) — re-coding skipped\n")

    t0 = time.perf_counter()
    doc_cbs = []
    for doc_id, codes in raw.items():
        doc_cbs.append(reconcile_show(codes, f"WITHIN {doc_id}"))

    combined = [c for cb in doc_cbs for c in cb]
    reconcile_show(combined, f"ACROSS {len(doc_cbs)} docs")

    print(f"\ntotal reconcile wall-clock: {time.perf_counter() - t0:.0f}s")
    u = m.llm.usage()
    print(f"ledger this run: {u['calls']} calls, "
          f"{u['completion_tokens']:,} completion tokens")


if __name__ == "__main__":
    main()
