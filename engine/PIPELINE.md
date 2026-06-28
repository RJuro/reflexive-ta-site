# MASSHINE engine — pipeline, prompts, and plan adherence

What the engine does, call by call: which prompt runs when, what goes in, what comes out.
Then an honest check against `DESIGN_REFLECTION.md`.

Model: **MiniMax-M3**, OpenAI-compatible, **default sampling** (we don't set temperature),
per-call **timeout 120s / 1 retry**. Every prompt is a file in `prompts/` (P5); the model
returns **JSON only** (its `<think>…</think>` is stripped before parsing); the model cites
**IDs, never raw text** (P1) and the system resolves verbatim spans from the index.

---

## The flow (as built)

```
import transcript
  │
  ├─ [LLM] structure.prompt        1 call/doc      → sections (gist + line range)
  │
  ├─ [spaCy] sentence index        0 calls         → sentences nested under sections
  │
  ├─ [LLM] coder.prompt            1 call/SECTION, PARALLEL (blind)  → raw codes per section
  │
  ├─ [LLM] reconcile.prompt        1 call/doc      → per-document codebook (dedup)
  │
  ├─ [LLM] reconcile.prompt        1 call/project  → project codebook (dedup across docs)
  │
  └─ [LLM] theorist.prompt         1 call/project  → candidate themes (claims + tensions)
```

Per-document LLM calls = `1 (structure) + N_sections (parallel) + 1 (reconcile)`.
Per-project = `+ 1 (cross-doc reconcile)`. Measured: structure/coder/reconcile ≈ **~3s fixed
+ ~15–20s per call** (scales with output); 4 calls in parallel finish in ~1 call's time.

---

## Each call in detail

### 1. `structure.prompt` — segment the document  *(once per document)*
- **When:** first, on raw transcript text.
- **In (user msg):** the whole transcript, every line prefixed `0007| …`.
- **Out:** `{"header_end_line": int, "sections": [{"gist": str, "start_line": int, "end_line": int}]}`
- **Post:** line numbers → exact char offsets (mechanical); stored as `section` rows. Gists are
  *descriptive only* (no interpretation).
- **Function:** `structure()` in `masshine.py`.

### 2. sentence index — *mechanical, no LLM*  *(once per document)*
- spaCy `sentencizer` splits each section's char range into sentences.
- Stored as `sentence` rows nested under their section: `Sentence{id "S2.007", doc_id, section_id,
  char_start, char_end}`. Pull verbatim text by ID via `resolve()`.

### 3. `coder.prompt` — code one section  *(once per section, run in parallel, blind)*
- **When:** after indexing; all sections coded concurrently (concurrency 8). Each call sees
  **only its own section** — no other sections, no codebook.
- **In (user msg):** one section as `[S2.007] <sentence text>` lines.
- **Out:** `{"codes": [{"label", "definition", "code_type": "semantic|latent",
  "evidence_sentence_ids": ["S2.007"], "rationale"}]}`
- **Post (grounding gate, P1):** keep only `evidence_sentence_ids` that resolve; **doc-qualify**
  them (`docid#S2.007`); drop any code with no surviving evidence.
- **Function:** `code_document()` → `code_one()` workers.

### 4. `reconcile.prompt` — merge duplicate codes  *(once per document, then once across documents)*
- **When:** (a) after a document's sections are coded → per-doc codebook; (b) after all docs →
  project codebook.
- **In (user msg):** the codes as `[t12] (semantic) label — definition` — **labels + definitions
  only, no transcript** (small payload).
- **Out:** `{"groups": [{"keep": "t12", "members": ["t12","t40"]}]}` — **ID-only**: the model
  clusters by id and names a representative; it does NOT rewrite labels/definitions. This keeps
  the *output* tiny, which is what drives M3 latency (measured: reconcile of 50 codes went from a
  120s+ timeout to **~12s** with ID-only output).
- **Post:** the `keep` code's label/definition/type carries; evidence **unions** across the
  group; un-grouped codes survive as their own code; stored in `code` table.
- **Code ids are stable + monotonic.** A persistent counter (`meta.code_seq`) mints `C0001…`;
  ids are **never renumbered or reused**, so `C0042` means the same code for the project's life
  (the anchor themes / future decisions / the UI reference). `_next_code_id()` / `_write_codebook()`.
- **Two entry points:** `reconcile_project()` builds the codebook from scratch over ALL docs
  (seed a corpus); `reconcile_into()` folds ONE new doc's codes into the EXISTING codebook —
  existing codes keep their ids, a restatement merges into the established code (evidence unions,
  its definition wins), a genuinely new code gets a fresh id, and **no existing doc is re-coded**.
- **Functions:** `reconcile()`, `reconcile_project()`, `reconcile_into()`, `add_document()`.

  **Adding a document** → call `add_document(conn, run, path)`: codes only the new doc, runs
  `reconcile_into`, re-derives themes. (Previously the only path was a full rebuild that
  renumbered every id — now fixed.)

### 5. `theorist.prompt` — candidate themes  *(WIRED)*
- **In:** the project codebook as `[C0007] (semantic) label — definition` (no transcript).
- **Out:** `{"themes": [{"central_concept", "supporting_code_ids", "contradicting_code_ids"}]}`
- Themes are *claims*, not bucket labels; divergence (contradicting codes) preserved. Invented
  or empty-support code ids are dropped; themes stored in the `theme` table and exported.
- **Functions:** `theorize()`, `theorize_project()`.

---

## Worked example (DP-40 GRANDE)

1. `structure` → ~10 sections, e.g. `S2 "Family, home, and farm life" (L34–89)`.
2. sentence index → `S2.000 … S2.060` resolving to exact source text.
3. `coder` (parallel over sections) → e.g. S1 yields *"Maiden name as ethnic identity marker"*
   ← `["grande#S1.005","grande#S1.009"]`.
4. `reconcile` (doc) → duplicate codes across sections merged (e.g. "place of origin" appearing
   in S1 and S5 becomes one code, evidence unioned).
5. `reconcile` (project) → same code merged with a matching code from another transcript;
   evidence now spans both docs.

Artifact: `exports/<doc>.json` = `{document, sections:[{…, sentences:[…]}], codebook:[{…,
evidence_sentence_ids:[doc-qualified], …}]}`.

---

## Are we still following the plan?

**Spine: yes.** Sentence-index-only + cite-IDs-never-regenerate (P1/P7), prompts as external
files, SQLite + JSON export, one MiniMax client at default settings, RTA framing, autopilot-first
— all intact.

**Phase status (vs the build plan):**

| Phase | Plan | Status |
|---|---|---|
| 0 skeleton | FastAPI + SQLite + sentence index, round-trips | ✅ done |
| 1 autopilot coder | code sections, cite sentence IDs | ✅ done (blind + parallel) |
| 2 conciliator | per-code, **whole codebook in prompt**, **create/merge/minority/defer + rationale**, phase from saturation | ⚠️ **deviated** — see below |
| 3 theme pass | `theorist.prompt` → candidate themes | ✅ done (1 pass, claims + tensions, exported) |
| 4 the app | reading + codebook views | ⬜ not started |
| 5 co-pilot + comparison | interactive lenses, variance | ⬜ not started |

**The one real deviation (Phase 2), and why.** The plan's conciliator coded *with the whole
codebook in context* and emitted **per-code verdicts (create/merge/minority/defer) each with a
written rationale**, phase-gated by saturation. We built that, but it was **too slow** (serial,
growing prompt, ~17 min/doc). On your steer we pivoted to **blind parallel coding + a post-hoc
`reconcile` call that groups duplicates**. This is much faster and still preserves the codebook
and divergence-by-not-over-merging — **but we lost two design properties**:
- **No per-merge rationale** — we record *that* codes merged, not *why* (the auditable merge memo).
- **No explicit minority/defer verdict** — reconcile is instructed not to over-merge, but it
  doesn't flag a kept-apart code as a preserved minority reading.

These were valued in the design (auditability, "divergence is data"). They're recoverable later
by having `reconcile` emit a short rationale per group + a `minority` flag — cheap to add when we
want it. Flagging so it's a conscious choice, not silent drift.

**Locked decisions — deferred / not-yet (not dropped):**
- `Run`/provenance envelope is **partial** (run row exists; we don't yet stamp prompt-version /
  model / params per artifact).
- **Architecture-as-config + runs/repetitions first-class (variance)** — not yet (single path).
- **`method_frame` per run** — not yet (RTA inlined in prompts).
- **Append-only event log** — not yet. The `code` table is rewritten each reconcile, but ids
  are now **stable + monotonic** (a code keeps its id across adds), so the missing piece is the
  *history* of mutations (which code merged into which, when), not referential stability.
- **Phase-from-saturation** — dropped with the Phase-2 pivot.
- `CandidateTheme` is now produced (the `theme` table + export); `Memo` / `HumanDecision`
  schema objects — not yet (no human loop).

**Net:** on-plan for the autopilot spine; Phase 2's conciliator was deliberately simplified for
speed (with two auditability properties parked); Phases 3–5 and the provenance/variance machinery
are still ahead.
