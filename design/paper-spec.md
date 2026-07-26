# P9 — The paper shape

*Spec, 2026-07-26. Mockup: [masshine-paper-mockup.html](masshine-paper-mockup.html). Grounding:
the deep-research failure-mode taxonomy (compass artifact, repo root), reviewer rounds 1–4, and
the Nirosha-vs-AI pairing.*

## 1. Diagnosis

Two problems, one root.

**Over-generation is structural, not tonal.** `coder.prompt` already carries the discipline
language (quality bar, mid-level abstraction, perceived-vs-asserted guardrail) and still yields
~77 codes/doc, because (a) there is no numeric budget and (b) every section is coded against an
empty slate — the coder has never seen the codebook, so the same phenomenon is re-minted in new
words every section. A human coder holds ~20 codes per transcript by *constant comparison*:
reuse before mint. The machine never gets the chance.

**The UI is overwhelming because it exposes the raw layer.** 400+ chips, queues, and seven
sidebar views ask the researcher to manage the machine's exhaust. The researcher's actual
objects are: the document being read, the codebook taking shape, and the themes. Everything
else is plumbing.

Fix both at the source: generate less, assimilate continuously, and show the work the way a
researcher already knows how to read it — a printed document with marginalia, and a book index.

## 2. Product shape: three places

1. **Document** — the transcript as a typeset page; codes live in the margin. Friction,
   comments, and live-coding progress fold in here.
2. **Codebook** — the emerging index across all documents; families as headings, codes as
   dot-leader entries. Overview stats fold into its footer.
3. **Themes** — unchanged view, restyled to match.

Sidebar shrinks to these three (plus project switcher). Overview, Notes queue, and Friction
as separate views are deleted; their content relocates (§4, §6).

## 3. Engine changes

### 3.1 Coder restraint (prompt-only)

- Hard budget: **at most 3 codes per section**; "salience, not coverage"; returning zero codes
  for a thin section is explicitly endorsed (already half-said; make it numeric).
- **Reuse before mint**: the prompt gains a `PROJECT CODEBOOK` block — the current codebook
  index (id, label, one-line definition; researcher-authored codes listed first). Instruction:
  if an existing code names this phenomenon, cite its id in a `reuses` field instead of minting;
  mint only when nothing fits. The block is frozen at document start so sections still run in
  parallel; within-doc duplication is caught by reconcile and assimilation as today.
- Research-question scoping: `project.research_question` (nullable text, set in project
  settings) is injected when present; the coder may return `out_of_scope` sentence ranges with
  a one-line reason, logged, never coded — the Nirosha "declined to code" audit trail.

### 3.2 Assimilation (one call per document)

After a document's coding + reconcile completes, **one** LLM call sees: the full numbered
transcript, that document's raw codes (with evidence sids), and the project codebook index.
It returns, Python-validated (invented ids dropped, first-claim wins — the standard
validators):

- `filings`: raw code → existing codebook code. **Auto-applied** as merge revisions (evidence
  union, append-only, reversible). Routine filing must not require a click per item — the
  researcher's control is the margin itself: the passage now shows the codebook label, and
  detach/reassign is one action away (§5). Machine proposes structure; it does not queue
  bookkeeping.
- `mints`: raw codes promoted to new codebook entries, label rewritten at cross-case register.
- `merges`: two *established* codebook codes making the same claim → **pending proposal**
  (existing P8a queue). Structural changes to the shared codebook stay researcher-gated.
- `memos`: uncertainty flags, translation-ambiguity notes, perception-vs-event downgrades,
  split/refine suggestions, cross-case questions — written to the memos table (author:
  `assistant`). This is the "colleague's work, not an extraction job" layer, at zero extra
  calls.

Per-doc digest (derived, not stored): "2 minted, 9 filed, 1 merge proposed" — shown in the
index footer and the job toast. New-mints-per-doc trending to zero **is saturation**, a
concept every RTA researcher recognizes on sight.

Consolidate/compress remain as occasional reorganize tools; they stop being the primary
collapse mechanism.

### 3.3 Panel-where-it-matters (cost lever, optional per project)

Full 3-lens panel on the first 2–3 documents (codebook formation + friction baseline — the
perspectivist asset), standard coder + critic thereafter, panel on demand per document.
Roughly 60% cost cut on a 30-doc corpus. Default stays full-panel; this is a project setting.

### 3.4 Call budget (per document)

| Pass | Calls | Notes |
|---|---|---|
| Coding | ~24 (3 lenses × ~8 sections) | existing; budget cuts output tokens, not calls |
| Assimilation | 1 | new; ~10–15k in, small out |
| Consolidate/compress | 0 routine | occasional, researcher-triggered |

### 3.5 Quality gates

- Grounding gate unchanged (cited sids or dropped) — fabrication stays structurally impossible.
- `tools/coverage_check.py` (promote the scratchpad script): decile distribution of citations
  per doc; assimilation must not skew surviving evidence toward early deciles
  (lost-in-the-middle guard). Run in tests on fixtures and after real runs.
- Export bundle (`export_payload`) gains the revision + theme-revision logs and a run manifest
  (model id, prompt versions, timestamps) — closes R8 from the requirements table.
- Project settings gain a positionality/epistemology declaration (free text, exported) — R7,
  the Big-Q congruence move.

## 4. The Document page

Typeset like an archival typescript: serif, ~62ch measure, paper ground, page card, small-caps
speaker names, folio line ("DP-40 · Grande, M. · Yugoslavia 1920 · age 10 · page 1 of 6").

**Marginalia states:**
- *Rest*: coded passages show only pencil ticks in the gutter (one per lens/reading, hue-tinted).
  Uncoded passages show nothing — restraint is visible.
- *Hover/tap*: the passage's notes materialize in the margin — hue dot, label, lens tag,
  one-line definition. Click pins.
- *Two readings*: lens disagreement is one margin slot labeled "two readings", notes stacked.
  This replaces the Friction view for reading; the cross-doc friction table lives behind a
  filter in the Codebook.
- *Live coding*: while a job runs, ticks and notes appear as sections complete, with a quiet
  "reading section 4…" pulse at the frontier. The researcher reads along and can already act
  on any note that has landed. Latency becomes the engagement, not the wait.
- *Comments/memos on a passage*: italic, no dot — visually "handwriting", distinct from codes.

## 5. The writable margin (human change + add)

Every margin object is editable in place; every action is an append-only revision, reversible,
and feeds `compile_guidance` on re-runs. Machine text is never destructively mutated —
researcher overrides ride alongside (the established `researcher_label` pattern).

**On an existing note (hover reveals quiet affordances):**

| Action | Machinery |
|---|---|
| Rename code | exists (`researcher_label`) |
| Edit definition | new `researcher_definition`, same pattern |
| Detach this passage from the code | new revision action `remove_evidence` |
| Reassign to another code | `remove_evidence` + `add_evidence` |
| Reject code entirely | exists |
| Move to another family | exists (P8a reassign) |
| Comment on the note | exists (comments, author-attributed) |

**On a text selection (select sentences → floating "+ mark"):**

- *Apply existing code*: autocomplete over the codebook index (researcher codes first) →
  `add_evidence` revision on that code. Reuse-before-mint applies to humans too, by making
  reuse the fastest path.
- *New code*: type label (+ optional definition) → a code row with `coder='researcher'`,
  evidence = selected sids. First-class everywhere: it appears in the index, in the coder
  prompt's codebook block, and the assimilation pass **files observations under researcher
  codes preferentially** — the human's categories become attractors for the machine.
- *Margin note*: free-text comment anchored to the sids (no code) — the memo margin.

Schema deltas: `code.coder` value `'researcher'` (column exists); revision actions
`add_evidence` / `remove_evidence` / `edit_definition`; `project.research_question` +
`project.positionality`. No new tables.

## 6. The Codebook page (the index)

A book index, not a chip wall:

- Families as small-caps headings with hue dot and one-line rationale (italic).
- Codes as dot-leader rows: label …… `3 docs · 11`. Researcher codes marked (∗ or "yours");
  codes minted by the latest document marked "new". Merged variants nest under their survivor,
  collapsed.
- Click a row → the cross-case view: every passage cited by that code across all documents,
  grouped by document (this **is** the P9 retrieval matrix, delivered as a click-through
  instead of a grid).
- Direct manipulation = the hierarchy UX: drag a code onto a code → merge proposal (pre-filled,
  one confirm); drag onto a family heading → reassign (immediate, logged); "New family…"
  creates an empty heading to drag into. No new hierarchy schema — family → code → absorbed
  variants is the hierarchy.
- Footer: the per-doc assimilation digest and the saturation line ("After document 3: two new
  codes minted, nine filed under existing. The index is settling."). Pending merge proposals
  surface here as a single quiet line ("3 suggested merges") expanding to the P8a queue.

## 7. Build order

1. **Engine** (offline-testable): coder budget + codebook block + `reuses`; assimilation call
   + validators + auto-filing + memos; revision actions `add_evidence`/`remove_evidence`/
   `edit_definition`; `research_question`/`positionality` fields; export manifest + revision
   logs; `tools/coverage_check.py`. Tests against fixtures, zero live calls.
2. **Document page**: typeset reading view + margin rest/hover/pinned/two-readings states +
   live-coding marginalia + writable margin (§5).
3. **Codebook page**: index layout + click-through cross-case view + drag interactions +
   digest footer. Delete Overview/Notes/Friction views; fold remnants.
4. **Validation run**: one real paid run (Johnson/Brozinskas or the Nirosha transcript) through
   the new pipeline; coverage_check + code-count trajectory + a Nirosha alignment read as the
   acceptance evidence.

Each phase ships behind the existing test discipline (offline suite, then live browser check
against scratch data).

## 8. Out of scope (unchanged from P8 deferrals)

Exemplar-quote curation per code; methods-section export (the R8 manifest is its input);
adjudication workspace for imported human codebooks (own phase — needs the alignment data
model, and any agreement number needs a second human coder to be interpretable).
