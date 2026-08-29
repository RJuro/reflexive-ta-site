# UI/UX brief — designing the MASSHINE data session

*For a design agent (or human designer) with full repo access. 2026-08-27. Your deliverable
is a MOCKUP that shows the complete envisioned system — the target the next builds aim at —
not a restyle of what currently runs.*

## 1. Mission

MASSHINE is an instrument for reflexive thematic analysis that sits **between the
computational and the qualitative**. The researcher gives it interviews (audio or text); the
system reads them closely; then it **reports what it found** — the patterns, the evidence
under each, what it could not resolve, and what this interview does to the account so far.
The researcher interrogates, challenges, reframes, decides. The machine never owns an
interpretation, and every sentence it produces is one click from the verbatim words behind it.

**No persona, ever.** The system is an instrument, not a character: no "your AI colleague",
no assistant avatar, no chattiness, no first-person charm, no debrief-from-a-helpful-friend
framing. It states findings and shows evidence. Write and design the way Apple describes a
tool — plainly, about what it does — never the way a startup describes a companion.

**It must not feel like NVivo with AI bolted on.** No code-tree manager, no chip walls, no
accept/dismiss queues as a daily duty, no dashboard aesthetics, and none of the
gradient-sparkle "AI product" look. The researcher works at a slightly higher level with
artifacts the model made — the relationship is mediated — but it must never feel like
"stupid summaries": every artifact is made of the material, speaks with anchored passages
and the participants' own words, and the marked-up transcript is always one click away.

## 2. Read these first, in order

1. `design/MASSHINE.md` — the consolidated spec. §2 (the loop architecture: evidence
   ascends, direction descends, every crossing checked) is the system model your design must
   EXPRESS — the two currents should be visible and felt. §3's deletions table is the stance.
2. `design/data-session-spec.md` — the detail: walkthrough step types (§3), journal +
   acceptance gate (§4), familiarization layer (§6), git-backed history (§7), design
   language (§8), audio path (§12).
3. `design/masshine-paper-mockup.html` — OPEN IN A BROWSER. The approved aesthetic for the
   Text view: archival typescript, serif, margin ticks at rest, notes on hover, the
   codebook as a book index. Your mockup must feel like a sibling of this page.
4. `web/index.html` + `web/app.js` — what currently runs (P10.3): the three-surface retrofit
   on the older v4 shell. Good bones (OKLCH tokens, quiet chrome), but it is a RETROFIT —
   you are allowed, encouraged, to redesign past it.
5. `design/masshine-v4-quiet.html` and `design/masshine-shell-v3-mockup.html` — lineage only.
6. `engine/seed_data/DP-40 GRANDE, M.txt` — real transcript content (public; use it).
7. `engine/tests/fixtures/panel_2interview.json` — real data shapes (codes with evidence
   sentence ids, themes with provenance).

## 3. The researcher's day (design to this loop)

1. **Make a project** — name, research question, positionality, reading model. Projects are
   the front door; creating one must be prominent and feel consequential, like opening a
   fresh notebook.
2. **Drop in material** — audio (mp3/m4a/wav/aiff) or text, as equals. Audio flows:
   transcribe → diarize → speaker roles detected ("interviewer / Marija Jankovic") →
   researcher reviews the transcript, optionally runs a **gated redraft** (a per-segment
   diff: the model's conservative fixes, accept each or all; proper names are researcher
   territory) → ingest. After ingest the text is immutable.
3. **The system reads** (~2 model calls, a few minutes). Progress should feel like being
   read to — quiet, literate — not like a build pipeline.
4. **Familiarize**: a ~3-minute **document introduction** — listenable (TTS) or readable —
   built from the extraction, every claim carrying evidence anchors that open the text.
   Closing paragraph: what this document does to the project's story.
5. **The walkthrough** (the centerpiece): a paced sequence of typed steps —
   `pattern` (with its weakest evidence too, not just best quotes), `tension`,
   `uncertainty` (perception-vs-event, translation ambiguity), `delta` (supports /
   complicates / contradicts standing findings), `declined` (out-of-scope, disclosed).
   One step at a time; reactions **agree / challenge / reframe / park** persist and steer
   the next document's reading. Beneath it: **open conversation** — "where does she talk
   about pay?", "read this passage against the grain" (summons a second lens for one
   passage only).
6. **The journal** (project home): findings, memos in both voices, and the
   **story-so-far** — a versioned narrative revised after each document (v1 after doc 1 …
   the evolution itself is inspectable). Findings do NOT ask the researcher for verdicts
   they can't yet give: each carries a computed evidential **standing** (from support below
   — never a researcher input) and the researcher's lightweight **stance** (their walkthrough
   reactions, rolled up). **Verdicts** — keep-and-name, merge, split, demote, drop — happen
   only at a deliberate **theme review sitting** with the pooled cross-case evidence in view
   (RTA phase 4 made literal), and naming a finding requires having opened its evidence.
   Design the sitting as its own moment — a different register from the daily walkthrough —
   and make the standing/stance distinction legible at a glance.
7. **The text** whenever a claim deserves scrutiny — the paper view, opened at the passage,
   marginalia in the gutter. Destination, never homework.
8. **History**: every processing run and session is a restore point (git-backed). Linear
   timeline, "restore to here", researcher-labeled "mark this moment". No git jargon ever.
9. **Export** — findings, codes, revisions, audit trail, model manifest.

## 4. Surfaces to design

Required, as coherent states of one system: **Home** (project front door + material +
declaration + model picker + notes) · **Session** (intro → walkthrough → conversation) ·
**Journal** (story-so-far, findings + lifecycle + gate, memos, history timeline) · **Text**
(paper view + margin notes + codebook drawer). Also design the smaller moments no one has
designed yet: the audio arrival + role review + redraft diff; the reading-in-progress state;
the on-demand second lens appearing inside a step; the acceptance-gate moment; empty states
for a brand-new project (the blank notebook must invite, not lecture).

## 5. Design language

- **Paper, archival, calm.** Serif for material and claims; quiet sans for chrome. The
  existing OKLCH token system (see `web/index.html` `:root`) is the palette family; the
  paper mockup shows the register. Light-first.
- **Restraint of the kind Apple practises** — the discipline, not the pastiche. Concretely:
  *content is the interface* (no card-in-card, almost no boxes — separate with whitespace and
  the occasional hairline); *typography carries hierarchy* (a tight scale, few weights, size
  and colour instead of borders and badges); *chrome recedes* (translucent, thin, out of the
  way until reached for); *one accent used sparingly* — here it marks the researcher's own
  voice and nothing else; *state is shown, not announced* (no pills or badge vocabulary where
  a plain sentence works); *motion is small and explanatory*. Every element must justify its
  ink. If a rule, box, or label can be deleted without losing meaning, delete it.
- **Generous whitespace; one thing at a time.** The walkthrough is a sequence, not a form.
  Density is the enemy — the old UI died of chips.
- **Evidence chips are doors, not decoration** — sentence-id chips open the text at the
  passage. Every model-made claim visibly carries its anchors.
- **Two voices, visibly distinct**: the machine's contributions and the researcher's
  (labels, memos, reframed statements) must be distinguishable at a glance — the original
  is kept when the researcher overrides, shown small.
- **Sensitive material**: transcripts are real people's lives (Ellis Island oral histories;
  a Haitian home-aide's account). The register must stay respectful — narrator's experience,
  never group characterization.
- Use REAL content from the seed transcripts in the mockup — the "made of the material"
  rule applies to mockups too. No lorem ipsum, no invented participants.
- **Make the loops visible** (MASSHINE.md §2): evidence rising (anchors, computed standing),
  direction descending (stance, guidance trail), and above all the **check-back moment** —
  the system answering a researcher's steer with "supports / strains / could not find" —
  plus the evolving research question (versioned, with machine-proposed refinements) and
  residue on display (what didn't fit, offered as the seed of a reframe).

## 6. Technical frame

- Deliverable: one or more **self-contained static HTML files in `design/`** (inline CSS,
  no external assets beyond system/Google fonts, light JS only for hover/pin/step-paging
  demonstrations). Multiple surfaces may live in one scrolling file (the paper mockup's
  two-page pattern) or as linked files — your call; say why.
- Production is a vanilla-JS SPA served by FastAPI (`web/`), PIN-gated, viewer/editor roles.
  Your mockup need not be wired, but every element must map to real data: codes
  `{id, label, researcher_label, definition, code_type, evidence[{id, quote}], coder,
  model_rationale}`; themes `{central_concept, supporting_code_ids, coverage,
  key_evidence_sentence_ids, tensions, falsified_if}`; models
  `{id, label, note, available}`; jobs with progress. Invent presentation, not data shapes —
  where the backend lacks a shape (intro, story-so-far, walkthrough steps, history), match
  `design/data-session-spec.md` §3/§5/§6/§7.
- Honest-state note: SYNTHESIZE (intro/story/real walkthrough steps), the finding lifecycle,
  Q&A, history, and the audio review UI are NOT built yet — that is exactly why the mockup
  exists. Design the complete system; the build follows the mockup (that is this repo's
  tradition: v3 mockup → v4 shell → paper mockup → P10.3).

## 7. Quality bar

A skeptical qualitative researcher should look at any screen and see their OWN practice —
data sessions, memos, an analytic journal, marginalia — not a tech product's idea of it.
Every claim shows its receipts. The writing is plain and precise — no theory-inflated wording,
no persona, no charm. Narrow-width must not break (the paper mockup has the collapse
pattern). And the whole thing should be quietly beautiful — the kind of tool a researcher
would *want* to spend a day in.
