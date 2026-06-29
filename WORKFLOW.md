# MASSHINE — pipeline walkthrough (for debugging)

> **Purpose.** A stage-by-stage map of what the engine does: the input, the prompt used, **what the
> model literally sees**, and the output artifact at every step. Read it alongside the artifacts in
> `engine/exports/md/` (standard run) and `engine/exports/md_panel/` (panel run) and the prompt files
> in `engine/prompts/` + `packs/migration_oral_history/standpoints/`. Every claim here is traceable
> to a function in `engine/masshine.py` and a `.prompt` file — when the output looks wrong, this tells
> you which stage and which prompt to open.

---

## 0. The two pipelines

There are **two runs** over the same two interviews. They share stages 1–2, diverge at 3–5.

```
                        ┌── ingest ──┐   ┌──── code ────┐   ┌─ reconcile ─┐   ┌─ theme ─┐
STANDARD  transcript →  structure +  →  coder.prompt    →  within-doc +    →  theorist  →  md/5_themes.md
(md/)                   sentence idx     (1 coder)          across-doc        (sequential)
                                         per section        dedup

                        ┌── ingest ──┐   ┌──────── code ────────┐   (NO reconcile)   ┌─ theme ─┐   ┌ friction ┐
PANEL     transcript →  structure +  →  coder + critical +       →  codes kept      →  theorist  →  + provenance → md_panel/
(md_panel/)             sentence idx     phenomenological          per-lens,           (sequential)   (Python)      5_themes.md
                                         (3 coders, blind)         per-doc, RAW                                      4_*_friction.md
```

**The single most important structural difference:** the **standard** pipeline runs a *reconcile*
(dedup) step over the codes; the **panel** pipeline **does not reconcile at all** — every lens's codes
are kept raw and distinct, on purpose (to keep the lenses comparable). That choice is the root of
most of the panel's "code explosion" — see §8.

Runners: `tools/run_project_md.py` (standard) · `tools/run_panel_md.py` (panel). Both are cache-based
and resumable (§7).

---

## 1. STAGE — Structure (LLM)

| | |
|---|---|
| **Function** | `structure(raw)` → called inside `ingest()` |
| **Prompt** | `engine/prompts/structure.prompt` |
| **Input** | the raw transcript text, line-numbered |
| **Output** | a list of sections `{id: "S1", gist, start_line, end_line, char_start, char_end}` |
| **Artifact** | `1_<doc>_sections.md` |

**What the model sees:**
- **system** = the full text of `structure.prompt`.
- **user** = the transcript with every line prefixed `NNNN| ` (from `_numbered(raw)`), e.g.
  ```
  0001| INTERVIEWER: Can you tell me where you were born?
  0002| GRANDE: Well, I was born in...
  ```
The model returns only **line ranges + a gist per section**. The system then maps lines → exact
character offsets; it never trusts the model with text. Section ids are `S1, S2, …` in document order.

---

## 2. STAGE — Sentence index (mechanical, no LLM)

| | |
|---|---|
| **Function** | `sentence_index(raw, sections)` (spaCy `sentencizer`) → `ingest()` |
| **Prompt** | none |
| **Input** | raw text + the section char-ranges from stage 1 |
| **Output** | sentences `{id: "S2.007", section_id: "S2", char_start, char_end}` |
| **Artifact** | `2_<doc>_sentences.md` |

Within each section, spaCy splits sentences; each gets an id `<section>.<NNN>` (e.g. `S2.007`). **These
ids are the evidence anchors for the entire rest of the pipeline.** Verbatim text is always *resolved
from the index by id* (`resolve()` / `resolve_ev()`), never regenerated — so quoted text can't be
hallucinated (the preserved typos in the anchors are proof of this).

---

## 3a. STAGE — Coding, STANDARD (LLM, parallel)

| | |
|---|---|
| **Function** | `code_sections(conn, doc_id)` — one call **per section**, `ThreadPoolExecutor` (concurrency 8) |
| **Prompt** | `engine/prompts/coder.prompt` |
| **Input** | each section's sentences |
| **Output** | raw codes `{label, definition, code_type: semantic|latent, evidence: ["doc#S2.007"], model_rationale}` |
| **Artifact** | feeds `3_<doc>_codes.md` (after the within-doc reconcile in §4) |

**What the model sees, per section call:**
- **system** = `coder.prompt`.
- **user** = `_section_block`:
  ```
  ## S2 — <section gist>
  [S2.000] <verbatim sentence>
  [S2.001] <verbatim sentence>
  ...
  ```
The model returns codes that cite the listed sentence ids. Each section is coded **blind** (the coder
sees only that section, not the codes from other sections), which is why the same observation can be
re-coded in every section it appears in — there is no cross-section awareness here. `_parse_codes()`
drops any code whose evidence ids aren't in the section (grounding gate, P1).

## 3b. STAGE — Coding, PANEL (LLM, 3 coders, blind, parallel)

| | |
|---|---|
| **Function** | `code_sections_panel(conn, doc_id, coders)` — one call **per (lens × section)** |
| **Prompts** | `coder.prompt` (lens `standard`) + `packs/migration_oral_history/standpoints/critical_political_economy.prompt` + `…/phenomenological_memory.prompt` |
| **Input** | each section's sentences, coded independently by all three lenses |
| **Output** | `{ "standard": [codes], "critical": [codes], "phenomenological": [codes] }` — **raw, never reconciled** |
| **Artifact** | `3_<doc>_codes_<lens>.md` (one file per lens) |

**What the model sees, per (lens, section) call:** identical to 3a — **system** = that lens's prompt,
**user** = the same `## S2 — gist` + `[S2.007] text` block. The three lenses are the *same coding
mechanism with a different system prompt*, so they cite the same sentence ids and are directly
comparable.

**Key facts that drive the panel's behavior:**
- The panel codes are **not deduplicated** — no within-section, within-doc, or cross-lens merge. Every
  code each lens emits in every section is kept. (Intentional: merging would blur the lenses.)
- So one observation a lens makes in N sections → **N codes**. And one observation seen by all three
  lenses → up to **3× more codes**. There is nothing downstream in the panel path that collapses these.

---

## 4. STAGE — Reconcile (STANDARD only; LLM)

| | |
|---|---|
| **Function** | `code_document()` (within-doc) then `reconcile_project()` (across-doc); core is `reconcile()` |
| **Prompt** | `engine/prompts/reconcile.prompt` |
| **Input** | the raw codes (labels + definitions only — **not** the transcript) |
| **Output** | merged codes; evidence unions across a merge group; stable ids `C0001…` (`_next_code_id`, monotonic) |
| **Artifact** | `3_<doc>_codes.md` (within-doc) and `4_codebook.md` (across-doc) |

**What the model sees:** **system** = `reconcile.prompt`; **user** = a flat listing
`[t0] (semantic) <label> — <definition>` for every code. It returns only **groupings** (`{members, keep}`);
Python applies them (the kept code's label/definition wins, evidence unions). This is the step that
collapses near-duplicates in the standard run — and the step the **panel deliberately omits**.

> **The panel pipeline has NO equivalent of this stage.** Panel codes go straight from §3b to theming
> with their raw, un-merged ids. This is why the standard run's code lists are short and the panel's
> explode.

---

## 5. STAGE — Theme (LLM, sequential, transcript-grounded) — *the most-implicated stage*

| | |
|---|---|
| **Function** | `theorize_walk()`; wrappers `theorize_project_sequential()` (standard) / `theorize_panel_sequential()` (panel) |
| **Prompt** | `engine/prompts/theorist.prompt` |
| **Input** | per interview, in reading order: the prior themes + that interview's **full transcript** + that interview's codes |
| **Output** | themes (schema below), plus `paradigm_provenance` in the panel case |
| **Artifact** | `5_themes.md` |

This is **not** one pass over a pooled codebook. It walks the interviews **one at a time** (`for i, doc_id in doc_order`). Each interview is one LLM call.

**What the model sees, at interview `i`** (assembled in `theorize_walk`):
- **system** = `theorist.prompt`.
- **user** =
  ```
  N (total interviews) = 2. This is interview 2 of 2.

  PRIOR THEMES:
  [T01] <central_concept>  · supporting: C0003, C0007  · coverage: 1 of 2
      – sub: <subtheme claim>  · supporting: C0003
  [T02] <central_concept>  · supporting: C0011  · coverage: 1 of 2

  TRANSCRIPT (interview 2):

  ## S1 — <gist>
  [S1.000] <verbatim sentence>
  [S1.001] <verbatim sentence>
  ...

  CODES (from interview 2):
  [C0040] (latent) "<label>" — <definition> · cites: S1.012, S1.013 · instances: 2
  [C0041] (semantic) "<label>" — <definition> · cites: S2.003 · instances: 1
  ...
  ```
  (`_prior_themes_block`, `transcript_block_from_sentences`, `_theorist_codes_block`.)

**Critical nuances for debugging:**
- On interview 1 the `PRIOR THEMES` block is `none yet — this is the first interview.`
- The model is told: to revise a prior theme, **reuse its id**; to add a new theme, omit the id.
- **The theorist is paradigm-blind.** In the panel run the `CODES` block pools all three lenses' codes
  for that interview **with no lens label** — the theorist never sees which lens a code came from.
  The lens of each code id is tracked separately in Python (`origin`) and used only afterward for
  provenance counting (§5b). So the theorist groups by *meaning*, not by lens.
- **`instances: N`** shown per code = the number of evidence sentence ids on *that one code* — **not**
  a dedup count. Forty near-duplicate codes still appear as forty separate lines here.

**Output schema (per theme), after `_resolve_step_themes`:**
```
id                         stable T-id (reused on revision, else minted; never renumbered)
central_concept            the one-claim sentence
subthemes[]                {claim, supporting_code_ids[]}  — where surplus is meant to go
supporting_code_ids[]      UNION of prior + this-interview's supporting codes (see below)
key_evidence_sentence_ids  line anchors (union of prior + new, doc-qualified)
coverage / claim_scope     COMPUTED in Python from the docs spanned by supporting codes' evidence
tensions[]                 codes that would weaken the claim (union of prior + new)
falsified_if               the disconfirmation test
paradigm_provenance        {lens: count}  — PANEL ONLY (§5b)
```

**The coverage-accumulation rule (the recent fix, and a key debug point).** When the model revises a
prior theme (`_resolve_step_themes`, ~line 616–622):
```python
new_sup = [c for c in t["supporting_code_ids"] if c in valid_codes]
sup = list(dict.fromkeys((prior["supporting_code_ids"] if prior else []) + new_sup))
```
The supporting set is the **union** of the prior interview's codes and this interview's — Python
accumulates, because the model is unreliable at re-listing old ids. `coverage` is then computed from
the distinct documents those codes' evidence spans (`k of N`), and `claim_scope` is forced to match.
**Consequence:** the supporting list only ever grows across interviews, and nothing collapses
near-duplicates — so on the panel path (no reconcile) a recurring theme accumulates *every* lens code
from *both* interviews. This is correct for coverage but is the mechanism behind the inflated counts.

## 5b. STAGE — Provenance (PANEL only, pure Python)

In `_resolve_step_themes`, for each theme:
```python
prov[lens] += 1   for each supporting_code_id whose origin == lens
theme["paradigm_provenance"] = prov     # e.g. {"phenomenological": 64, "standard": 10, "critical": 1}
```
**`paradigm_provenance[lens]` is a count of distinct supporting *code ids* of that lens — not
observations, not instances.** A theme's headline "phenomenological 64" means 64 distinct phenom code
objects are attached, which (given §3b/§4) badly over-counts the underlying observations.

The run-level header in `run_panel_md.py` (`themes_md`) computes:
```python
convergent = sum(1 for t in themes if len(t["paradigm_provenance"]) >= 2)
```
i.e. a theme is "convergent" if **two or more lenses appear at all** — `{64, 10, 1}` and `{25, 25, 25}`
are both "convergent." There is **no balance/share threshold** (this is the metric the feedback flags).

---

## 6. STAGE — Friction (PANEL only, pure Python, no LLM)

| | |
|---|---|
| **Function** | `friction(panel)` |
| **Input** | the per-lens codes for one document |
| **Output** | per-sentence divergence sets |
| **Artifact** | `4_<doc>_friction.md` |

For each sentence id, collect which lenses coded it. **Interpretive friction** = a sentence ≥2 lenses
both coded (compare readings). **Attentional friction** = a sentence only some lenses coded (a lens saw
what another skipped). This is descriptive only; it doesn't feed theming.

---

## 7. Caching / resume (how to re-run cheaply)

One JSON cache per pipeline is the run's checkpoint, written atomically after each step:
`engine/exports/project_2interview.json` (standard) and `panel_2interview.json` (panel). It holds:
- `docs[doc_id]` = `{name, sections, sentences(text), codes | panel}` — per-document coding,
- `project_codebook` (standard only) — the reconcile result,
- `theme_steps[doc_id]` = the **raw model output** of each theme step.

Re-running fills only what's missing. CLI flags (both runners):
- **bare run** → resume (skip cached steps; a timed-out theme step retries just itself),
- **`--retheme`** → keep coding, clear `theme_steps`, redo theming (use after editing `theorist.prompt`),
- **`--recode`** → rebuild everything (use after editing `coder.prompt` or a standpoint prompt).

Theorist calls run with a 900 s timeout, no retry; a dropped step is recorded in `failures` and shown
as a `⚠️ INCOMPLETE RUN` banner atop `5_themes.md` (never silently swallowed).

---

## 8. Artifact map — what produced each file

`engine/exports/md/` (standard) and `engine/exports/md_panel/` (panel):

| file | produced by | contains |
|---|---|---|
| `0_README.md` | runner `main()` | run summary + the file index + any INCOMPLETE banner |
| `1_<doc>_sections.md` | stage 1 | section gists + line ranges |
| `2_<doc>_sentences.md` | stage 2 | the sentence index (id → verbatim text) |
| `3_<doc>_codes.md` (standard) | stages 3a+4 | within-doc reconciled codes |
| `3_<doc>_codes_<lens>.md` (panel) | stage 3b | each lens's raw codes, side by side |
| `4_codebook.md` (standard) | stage 4 | the cross-doc reconciled project codebook |
| `4_<doc>_friction.md` (panel) | stage 6 | per-sentence interpretive/attentional divergence |
| `5_themes.md` | stage 5 (+5b panel) | themes: claim, sub-themes, supporting codes, anchors, coverage/scope, tensions, falsified-if, (provenance) |

Caches: `project_2interview.json`, `panel_2interview.json` (the resumable checkpoints).
Prompts: `engine/prompts/{structure,coder,reconcile,theorist,critic}.prompt` (critic = the superseded
coder–critic arm, not used by these runs) + `packs/migration_oral_history/standpoints/{critical_political_economy,phenomenological_memory}.prompt`.

---

## 9. Debugging the current feedback — symptom → stage → mechanism → where to look

Each row is traceable to a stage above. "By design" marks a deliberate choice that has a cost; "bug/gap"
marks something unintended.

### A. "Code counts exploded — phenomenological 64 on one theme; one observation re-stamped 40 ways"
- **Stage:** 3b (panel coding) + absence of 4 (no reconcile) + 5 (union accumulation).
- **Mechanism:** (1) the coder is **blind per section** (§3a), so one observation ("eloquent silence")
  is re-coded in every section/event it touches; (2) the **panel never reconciles** (§4, by design) so
  those near-duplicates never collapse; (3) the theme step **unions** supporting codes across both
  interviews (§5) and never dedups; (4) provenance counts **distinct code ids**, not observations (§5b).
  Net: 64 = the number of phenom code objects attached to T03, ≈ 5 real observations × (sections × docs).
- **Inspect:** `md_panel/3_*_codes_phenomenological.md` — you'll see the "eloquent silence around X / Y /
  Z" and "Disfluency as…" families as separate codes. Then `md_panel/5_themes.md` T03 supporting list.
- **Where a fix lives:** a near-duplicate **code-merge step for the panel** before counting — i.e. give
  the panel a reconcile (like §4) that merges *within a lens* (and optionally records cross-lens
  equivalence) so 40 labels become 1 code with 40 instances. The standard run already has this, which
  is why it's tight. (`code_sections_panel` currently returns raw; today nothing merges it.)

### B. "Convergence metric counts presence, not balance — 64/10/1 is labeled 'convergent'"
- **Stage:** 5b.
- **Mechanism:** `convergent = len(paradigm_provenance) >= 2` in `run_panel_md.py` `themes_md`, and the
  per-theme `*convergent*` label in the same function. No minimum share per lens.
- **Inspect:** the header line of `md_panel/5_themes.md` ("N convergent, M single-lens") and each
  theme's `provenance:` line.
- **Where a fix lives:** require a minimum lens share (e.g. each counted lens ≥ X% of supporting codes)
  before calling a theme convergent — or drop the label and just print the distribution. Note this is
  **downstream of A**: once codes are deduped (A), the 64/10/1 split itself shrinks and rebalances.

### C. "Theme over-stuffing — T01 is 48 codes, T03 is 75; the understatement finding is buried"
- **Stage:** 5 (theorist judgment) + the union rule.
- **Mechanism:** the theorist is allowed `subthemes[]` for surplus but isn't forced to split, and the
  union (§5) makes a recurring theme's supporting list grow monotonically across interviews. Nothing
  caps theme size or splits an omnibus.
- **Inspect:** `theorist.prompt` (the "ONE claim / split it / subthemes" instructions) vs the actual
  `central_concept` + `supporting_code_ids` length in `5_themes.md`.
- **Where a fix lives:** strengthen `theorist.prompt` toward splitting (or a post-pass that splits a
  theme whose sub-themes are themselves cross-cutting); again partly **downstream of A** (75 codes is
  inflated; deduping shrinks it).

### D. "Near-duplicate themes — T04 and T05 are the same Rodwin bakery material"
- **Stage:** 5 (theorist judgment across the sequence).
- **Mechanism:** the model proposes parallel themes for one cluster of moments instead of one theme +
  sub-themes; there is no theme-merge pass.
- **Inspect:** `5_themes.md` T04/T05 supporting lists (shared code ids) and central concepts.
- **Where a fix lives:** a theme-level merge/dedup pass after the walk, or a prompt nudge to fold
  overlapping clusters into sub-themes.

### E. "Disfluency read as meaning-work — Rodwin is 90 and aphasic; word-finding coded as phenomenology"
- **Stage:** 3b, the **phenomenological** lens specifically.
- **Mechanism:** `phenomenological_memory.prompt` uses Portelli/"eloquent silence"/"errors as meaning"
  as sensitizing concepts. Its anti-caricature paragraph says to hold a gap as a *site for attention,
  not a diagnosis*, but the model over-applies it to retrieval failure, and because the panel doesn't
  dedup (A) the over-reading appears as many codes → looks robust.
- **Inspect:** `md_panel/3_<doc>_codes_phenomenological.md` for the "Disfluency as…" family; compare to
  the transcript anchors in `2_*_sentences.md` (the "I forget the name of it" passages).
- **Where a fix lives:** tighten the anti-caricature clause in `phenomenological_memory.prompt`
  (explicitly: do not read age-related word-finding/aphasia as interpretive labor without independent
  warrant) — and A (dedup) stops one over-read from looking like a dozen.

### Bottom line for triage
A (no panel-side dedup) is upstream of B, C, and the *appearance* of E — most of the inflation traces
to the deliberate "panel never reconciles" choice combined with the coverage-union. The standard run
looks tighter because stage 4 (reconcile) collapses duplicates before theming. The honest options are:
give the panel a within-lens reconcile (closing A), then fix the convergence metric (B) and theme
splitting (C/D); or accept that the lens run is a richer-but-noisier exploratory view and treat the
standard run as the tight product. This document is the map; it does not change behavior.
