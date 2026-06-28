#!/usr/bin/env python3
"""Mechanically assemble the EXACT per-document reconcile prompt and write it to a file.

It runs the real pipeline up to (but not including) the reconcile LLM call:
  ingest (structure + sentence index) → code_sections (parallel blind coding) →
  _reconcile_messages(raw_codes)  ← the same builder reconcile() uses.

So the dumped (system + user) is byte-identical to what the per-doc reconcile sends.
The codes are produced by a fresh coding pass (M3 is non-deterministic, so they won't
match any one past run — but the assembly is the real thing).

Usage:  .venv/bin/python ../tools/dump_reconcile_prompt.py ["DOC NAME.txt"]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import masshine as m  # noqa: E402

DOC = sys.argv[1] if len(sys.argv) > 1 else "NPS-93 SHIN, J.txt"
OUT = m.ROOT / "exports" / "reconcile_prompt_per_doc.txt"


def main() -> None:
    sample = m.ROOT.parent / "transcripts_sample" / DOC
    conn = sqlite3.connect(":memory:")
    m.init_db(conn)
    run = m.new_run(conn, "dump reconcile prompt")
    doc_id, sections, sents = m.ingest(conn, run, sample)
    raw_codes, dropped = m.code_sections(conn, doc_id)        # raw per-section codes
    system, user, tmp = m._reconcile_messages(raw_codes)      # the exact assembly

    m.EXPORT_DIR.mkdir(exist_ok=True)
    text = (
        f"# Per-document reconcile prompt — {DOC}\n"
        f"# {len(sections)} sections, {len(sents)} sentences, "
        f"{len(raw_codes)} raw codes in, {dropped} ungrounded dropped.\n"
        f"# Two messages are sent to the model (system + user). No transcript is included.\n\n"
        f"================= SYSTEM MESSAGE (prompts/reconcile.prompt) =================\n\n"
        f"{system}\n"
        f"================= USER MESSAGE (the {len(raw_codes)} codes to consolidate) =================\n\n"
        f"{user}\n"
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(m.ROOT.parent)}  "
          f"({len(raw_codes)} codes, ~{len(text)//4} tokens of prompt)")


if __name__ == "__main__":
    main()
