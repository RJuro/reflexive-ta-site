#!/usr/bin/env python3
"""Read-span calibration harness (P10.1a, data-session-spec.md §5) — LIVE, makes real (possibly
paid) LLM calls. Never imported by the test suite except its pure helpers (`decile_table`,
`report_md`) — see tests/test_read_span_calibrate.py. NEVER RUN THIS AUTOMATICALLY.

Ingests ONE held-out transcript into a throwaway project, then runs masshine.read.read_document
at every span ("doc", "halves", "groups", "sections") against the SAME (empty) codebook state —
nothing is ever persisted to the `code` table, so every span reads the identical starting point —
and writes a Markdown report comparing: code count, citations + distinct sentences cited, the
coverage-gate's own decile distribution + first-30% share, reuse-vs-mint, drop stats, tokens
(from the llm ledger), and wall time.

Endpoint-agnostic (MASSHINE_BASE_URL / MASSHINE_LLM_BACKEND) by design — data-session-spec.md §5
calls for running this FIRST against a local model at zero API cost, THEN confirming on M3 before
the default span is fixed. codex-cli (see masshine/llm.py) exists for exactly this: local
calibration only, never a deployed backend.

Usage (run from engine/):
    .venv/bin/python tools/read_span_calibrate.py --transcript <path.txt> \
        [--spans doc,halves,groups,sections] [--report <out.md>]

Example — the held Livicia Antoine transcript, against a local codex-cli model (never against
the deployed openai backend):
    MASSHINE_LLM_BACKEND=codex-cli MASSHINE_CODEX_MODEL=gpt-5.6-luna \
        .venv/bin/python tools/read_span_calibrate.py \
        --transcript "../pairing_nirosha/Livicia_Antoine_LOC_transcript.txt" \
        --report exports/read_span_calibration.md

Optional estimated-cost column: set MASSHINE_PRICE_IN_EUR_PER_M / MASSHINE_PRICE_OUT_EUR_PER_M
(EUR per million tokens) to have the report price each span's run; left unset, the column is
omitted rather than showing a bogus figure. Mistral glm-5-2 under the university contract
(2026-08-27): **€1.19/M input, €3.74/M output** — reasoning/thinking tokens count as OUTPUT at
that rate, e.g.:
    MASSHINE_PRICE_IN_EUR_PER_M=1.19 MASSHINE_PRICE_OUT_EUR_PER_M=3.74 \
        .venv/bin/python tools/read_span_calibrate.py --transcript <path.txt>

Each run creates a throwaway project under a temp MASSHINE_DATA_DIR (set in `main()`, BEFORE the
first `import masshine`, so config.DATA_DIR never touches the real engine/data/) and never writes
outside it.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

SPANS = ("doc", "halves", "groups", "sections")


# ---- pure helpers (offline-tested; no DB, no LLM, no masshine import) --------------------------

def decile_table(deciles: list[int]) -> str:
    """One line of `n (pct%)` per decile bucket, sharing masshine.read.decile_buckets's bucket
    order — pure formatting, importable and testable without a DB connection."""
    total = sum(deciles) or 1
    return " | ".join(f"{n} ({n / total:.0%})" for n in deciles)


def estimated_cost_eur(prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated cost in EUR for one run's tokens, from MASSHINE_PRICE_IN_EUR_PER_M /
    MASSHINE_PRICE_OUT_EUR_PER_M (EUR per million tokens) — None when either is unset, so the
    report OMITS the cost column entirely rather than showing a bogus figure. Fold
    reasoning/thinking tokens into `completion_tokens` before calling — they count as OUTPUT
    tokens for pricing (see the module docstring: Mistral glm-5-2 under the university contract,
    2026-08-27, is €1.19/M input, €3.74/M output)."""
    price_in = os.environ.get("MASSHINE_PRICE_IN_EUR_PER_M")
    price_out = os.environ.get("MASSHINE_PRICE_OUT_EUR_PER_M")
    if not price_in or not price_out:
        return None
    try:
        return (prompt_tokens * float(price_in) + completion_tokens * float(price_out)) / 1_000_000
    except ValueError:
        return None


def report_md(transcript_name: str, results: list[dict]) -> str:
    """Markdown report from a list of run_span()-shaped dicts — pure, no DB/LLM dependency, so
    it is testable with synthetic result dicts. The cost column only appears when pricing env
    vars are set (see estimated_cost_eur)."""
    costs = []
    for r in results:
        u = r["usage"]
        completion_for_cost = u.get("completion_tokens", 0) + u.get("think_chars", 0)
        costs.append(estimated_cost_eur(u.get("prompt_tokens", 0), completion_for_cost))
    show_cost = any(c is not None for c in costs)

    header = ("| span | codes | reuses | declined | citations | distinct sents | first-30% share | "
              "cap/grounding drop | tokens (prompt/completion) | wall s |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    if show_cost:
        header += " est. cost (€) |"
        sep += "---|"

    lines = [
        f"# Read-span calibration — {transcript_name}", "",
        f"*{len(results)} span(s), same empty codebook state (nothing persisted between runs).*",
        "", header, sep,
    ]
    for r, cost in zip(results, costs):
        u = r["usage"]
        row = (
            f"| {r['span']} | {r['n_codes']} | {r['n_reuses']} | {r['n_declined']} | "
            f"{r['n_citations']} | {r['n_distinct_sentences']} | {r['first30_share']:.0%} | "
            f"{r['drop'].get('over_cap', 0)}/{r['drop'].get('ungrounded_evidence', 0)} | "
            f"{u['prompt_tokens']}/{u['completion_tokens']} | {r['wall_s']} |")
        if show_cost:
            row += f" {cost:.4f} |" if cost is not None else " — |"
        lines.append(row)
    lines += ["", "## Decile distribution (citations by tenth of document length)", ""]
    for r in results:
        lines.append(f"**{r['span']}**: {decile_table(r['deciles'])}")
        lines.append("")
    return "\n".join(lines)


# ---- live half (deferred masshine import — see main()) ----------------------------------------

def _setup_env() -> None:
    """MASSHINE_DATA_DIR must be set before `masshine` (specifically masshine.config) is
    imported anywhere in this process — called first thing in main(), never at module import
    time, so importing this file's pure helpers from a test triggers no tmp dir and no env
    mutation."""
    os.environ.setdefault("MASSHINE_DATA_DIR", tempfile.mkdtemp(prefix="masshine_calibrate_"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # engine/ on sys.path


def _deciles_from_codes(conn, doc_id: str, codes: list[dict]):
    from masshine import read
    starts = dict(conn.execute("SELECT id, char_start FROM sentence WHERE doc_id=?", (doc_id,)))
    doc_len = conn.execute(
        "SELECT MAX(char_end) FROM sentence WHERE doc_id=?", (doc_id,)).fetchone()[0] or 0
    prefix = f"{doc_id}#"
    positions = [starts[ev[len(prefix):]] for c in codes for ev in c["evidence"]
                if ev.startswith(prefix) and ev[len(prefix):] in starts]
    return read.decile_buckets(positions, doc_len)


def run_span(conn, doc_id: str, span: str) -> dict:
    """READ `doc_id` at `span`. read.read_document never persists anything, so calling this
    repeatedly with different spans always sees the SAME (empty) codebook — no span's result is
    contaminated by an earlier span's mints."""
    from masshine import llm, read
    llm.reset_usage()
    t0 = time.perf_counter()
    codes, reuses, declines, drop = read.read_document(conn, doc_id, span)
    wall = time.perf_counter() - t0
    cited = {e for c in codes for e in c["evidence"]}
    deciles = _deciles_from_codes(conn, doc_id, codes)
    return {
        "span": span, "n_codes": len(codes), "n_reuses": len(reuses), "n_declined": len(declines),
        "n_citations": sum(len(c["evidence"]) for c in codes), "n_distinct_sentences": len(cited),
        "deciles": deciles, "first30_share": read.first30_share(deciles), "drop": drop,
        "wall_s": round(wall, 1), "usage": llm.usage(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", required=True, help="path to a .txt transcript")
    ap.add_argument("--spans", default=",".join(SPANS),
                    help=f"comma-separated spans to compare (default: all of {SPANS})")
    ap.add_argument("--report", default=None, help="markdown report path (default: stdout)")
    args = ap.parse_args()

    spans = [s.strip() for s in args.spans.split(",") if s.strip()]
    for s in spans:
        if s not in SPANS:
            raise SystemExit(f"unknown span {s!r} — choose from {SPANS}")

    transcript = Path(args.transcript).expanduser().resolve()
    if not transcript.exists():
        raise SystemExit(f"no such transcript: {transcript}")

    _setup_env()
    from masshine import projects
    from masshine.db import new_run, project_db
    from masshine.ingest import ingest

    proj = projects.create_project(f"calibrate-{transcript.stem}")
    conn = project_db(projects.project_db_path(proj["id"]))
    try:
        run = new_run(conn, "read-span-calibrate")
        doc_id, secs, sents = ingest(conn, run, transcript)
        print(f"ingested {transcript.name}: {len(secs)} sections, {len(sents)} sentences",
              flush=True)

        results = []
        for span in spans:
            print(f"reading at span={span} ...", flush=True)
            r = run_span(conn, doc_id, span)
            results.append(r)
            print(f"  {r['n_codes']} codes, {r['n_reuses']} reuses, {r['n_declined']} declined, "
                  f"first-30% share {r['first30_share']:.0%}, {r['wall_s']}s", flush=True)
    finally:
        conn.close()

    md = report_md(transcript.name, results)
    if args.report:
        Path(args.report).write_text(md, encoding="utf-8")
        print(f"wrote {args.report}", flush=True)
    else:
        print("\n" + md)


if __name__ == "__main__":
    main()
