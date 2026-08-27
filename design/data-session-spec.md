# P10 — The data session

*Spec, 2026-08-27. Supersedes the UI direction of [paper-spec.md](paper-spec.md) (§2, §4–§6
there); the engine changes in paper-spec §3 carry forward and are referenced below. Grounding:
the deep-research taxonomy (compass artifact), the Nirosha pairing, reviewer rounds 1–4.*

## 1. The reframe

Everything built so far, the paper UI included, casts the researcher as **curator of the AI's
taxonomy**: review codes, approve merges, drag families. That is AI-augmented NVivo however
good the typography. MASSHINE's actual position is *between* the computational and the
qualitative, and the form that expresses it comes from research practice itself: **the data
session**. A junior colleague has done the immersive first pass — read everything, coded
everything, never got tired — and debriefs the senior researcher, who interrogates and owns
the interpretation. The researcher's work stops being clerical (managing a code tree) and
becomes judgment.

This is also the methodologically congruent form per the literature we analyzed and then
ignored in P9: Friese's "From Coding to Conversation", Morgan's QBA (broad queries → specific
sub-themes → substantiate with quotations — a debrief grammar), De Paoli's human–LLM
"cognitive assemblage", the relational-subjectivity reframe. Dialogue, not classification.

**Mediation has a structural answer, not a dial.** The researcher engages raw material
*through claims*: every digested statement is a door opening the full text at that passage.
Reading becomes forensic instead of exhaustive — what's load-bearing, what's contested, what
*disconfirms*. Mediation without receipts was the Copilot failure; here every sentence of the
debrief is one click from a verbatim span at a known offset (the substrate already
guarantees it).

## 2. The researcher journey

1. **Drop in a transcript.** Processing runs: structure, restrained coding, assimilation into
   the growing codebook, theme-walk step. All infrastructure; progress shown quietly.
2. **The walkthrough** (first contact, paced): a sequenced data session over what was found.
3. **The journal** (project home): findings with status, memos from both voices, accumulating
   across documents.
4. **The text** (destination, never homework): the paper view from
   [masshine-paper-mockup.html](masshine-paper-mockup.html), opened at evidence, marginalia
   intact.

Three surfaces total: **Session · Journal · Text**. The codebook demotes to a drawer +
export (still audited, still the methods-section artifact — no longer a researcher-facing
management duty).

## 3. The walkthrough

One paced sequence per processed document, in a colleague's voice, plain register. Steps are
typed, ordered by the generator:

| kind | content |
|---|---|
| `pattern` | A strong pattern in this document — statement + the passages carrying it, **including the weakest/edge evidence**, not only the best quotes |
| `tension` | Something that cuts two ways; on-demand second lens offered here |
| `uncertainty` | Perception-vs-event, translation ambiguity, thin evidence — voiced, not hidden |
| `delta` | What this document does to existing findings: supports / complicates / contradicts (powered by the theme-walk step snapshot, which already computes exactly this) |
| `declined` | What was NOT coded and why (out-of-scope per the research question) — trust through disclosed limits |

Each step: statement, evidence sids (doors to Text), linked code/finding ids, and a prompt
for the researcher. **Reactions are the input**: *agree* · *challenge* (with note) ·
*reframe* (edit the statement — researcher wording wins, original kept) · *park*. Reactions
persist, update finding status, and feed `compile_guidance` — the next document is read under
the researcher's reactions.

Beneath the walkthrough, **open conversation** over the coded corpus (QBA grammar): "where
does she talk about pay?", "is this perception or event?", "compare with Grande on kin
networks", "read this passage against the grain" (summons a critical/phenomenological reading
of that passage only — perspectivism on demand, where interpretation is actually contested).
Answers are retrieval-grounded in codes + sentences; every quote resolved from offsets, never
generated.

## 4. The journal

Project home. A chronological analytic journal — reflexive TA's actual artifact set:

- **Findings**: statement + status (`emerging` → `supported` / `challenged` / `dropped` /
  `accepted`), evidence trail, per-document delta history, researcher notes. Findings are
  `theme_v2` rows + a status/lifecycle layer — the P8b theme-authority machinery (relabel,
  reclaim, merge, demote) carries over as the researcher's editing surface, reached from the
  journal, not from a taxonomy view.
- **Memos**, both voices: assistant memos (uncertainty flags, split/refine proposals,
  cross-case questions — the layer Nirosha had and our export lacked) and researcher memos.
- **The acceptance gate** (R6, enforced by design, not checkbox): a finding cannot move to
  `accepted` until its evidence doors have been opened — the researcher has actually read the
  passages. Gate visits are logged; the interaction telemetry (challenged, reframed, dropped)
  is the validation data for the MASSHINE deliverables.

## 5. Computational shape: few calls per document (read-span is empirical)

With a thinking model (MiniMax-M3), cost and latency live in thinking + output tokens, and
thinking is paid **per call**. The old pipeline paid it ~19–25 times per document. Fewer,
larger calls amortize one reasoning budget over the whole transcript; prompts get richer,
not more numerous; output discipline is enforced in one place.

- **Call 1 — READ.** Full numbered transcript + codebook index (researcher codes first) +
  research question. (`structure()` stays at ingest — sentence ids are minted per section
  and the whole substrate hangs off them; READ absorbs the coder+critic calls only.)
  Returns: restrained codes (hard cap ~20–25/doc, evidence as sid lists), `reuses` against the
  codebook (filing at source — assimilation's routine work largely disappears; a separate
  assimilation pass remains only as a retrofit/reorganize tool), `out_of_scope` declines,
  uncertainty flags. Output discipline: one-line definitions; rationale only where an
  uncertainty flag warrants it; no rationale on reuses.
- **Call 2 — SYNTHESIZE.** Transcript + READ output + current findings + story-so-far +
  standing researcher reactions. Returns: the theme/finding update (the walk step), the
  typed walkthrough steps (§3), the **document introduction** (§6), and the revised
  **story-so-far** (§6). Python-validated throughout: every cited sid must resolve
  (grounding gate), every referenced code/finding id must exist; failures dropped and
  counted.
- **The session Q&A endpoint**: one call per researcher turn; context = retrieval over
  codes/sentences/findings, never whole-corpus stuffing. On-demand lens reading = one call
  scoped to one passage. Critic and panel become on-demand instruments of the same kind.

**Whole-document coding is the regime the lost-in-the-middle literature warns about** — three
defenses: a ~15k-token transcript is far below the measured degradation regimes; the
restraint cap means the model is never asked for exhaustive coverage (the documented failure
mode); and `tools/coverage_check.py` runs per document as a hard gate — front-heavy citation
deciles auto-fall back to the sectioned coding path, which is kept, not deleted.

**Read-span calibration (empirical, not doctrine).** Whether one READ call per document is
too few is an open question. READ takes a `span` parameter — whole-doc / halves /
section-groups / per-section — same prompt, same validators, same output contract at every
span. The calibration experiment: one held transcript (Livicia Antoine), READ at each span
against the same codebook state, compared in Python with no LLM judge: citation coverage
deciles, code count + evidence density, reuse-vs-mint rate, cross-span code overlap, wall
time + tokens. Runnable at zero API cost against a local model (gpt-5.6-luna via the
codex-cli backend), then confirmed on M3 before the default is fixed. Fewer calls remain
the goal; the data picks the span. **The codex-cli/luna backend is a local calibration
instrument ONLY** — explicit env opt-in, never a deployed or online backend, never
documented in deployment docs.

Call budget per document: **2 at whole-doc span** (+ researcher-paced Q&A), vs ~19
single-coder-sectioned and ~25+ panel. Coder restraint, reuse-before-mint, RQ scoping, and
the writable-margin revision actions carry over from paper-spec §3.1/§5 unchanged in
substance.

## 6. The familiarization layer

- **Document introduction**: produced by SYNTHESIZE *from the extraction* — the patterns
  that carry the document, what is uncertain or ambiguous, and a closing paragraph on what
  this document does to the project's emerging story. The intro is a **navigable surface,
  not detached prose**: every claim carries its evidence sids, rendered in the UI as quiet
  doors into the passages. The anchors live in structure (per claim/paragraph), never as
  bracketed ids inside sentence text — which is exactly what keeps it **TTS-ready**: the
  spoken rendering reads the clean prose, the on-screen rendering stays clickable. A
  ~3-minute listenable briefing to familiarize before working deeper. Session order: intro
  (listen or read) → walkthrough → open conversation.
- **Story-so-far**: a project-level narrative the system revises after each document,
  **versioned per document position** (story v1 after doc 1 … replayable like theme_steps).
  Lives at the top of the Journal. The version trail is itself a reflexivity artifact — the
  interpretation's evolution is inspectable, which no CAQDAS produces.
- TTS engineering is out of scope for the first build; the contract is only that intro and
  story prose is spoken-register with anchors carried structurally, never as ids embedded in
  sentence text — clickable on screen, clean when read aloud.

## 7. Project history (git-backed)

Each project directory is its own **git repository** (engine/data/ is ignored by the main
repo, so nesting is clean). Auto-commit at quiescent moments: after every completed job
(READ, SYNTHESIZE, import), after a researcher session, and always **before any destructive
re-run** — the safety point. Alongside the binary DB, each commit writes the canonical
pretty-printed export, so project history is *diffable*: what changed between two sessions
reads as a diff of findings, codes, and story — and the git log doubles as the R5/R8 audit
artifact (full provenance for a methods section).

**No git UX leaks into the product.** The UI concept is a linear timeline: "History —
restore to here", plus researcher-labeled save points ("mark this moment"). No branches,
merges, or remotes in v1 (forked counterfactual analyses are a future note). Restore is
whole-project, executed at job quiescence (the single job worker makes this safe).
Fine-grained undo remains the revision log's job; history is the coarse instrument.

## 8. Design language

Projects are the front door: creating one is prominent, and a project is **core documents +
notes** (researcher notes as first-class quick capture on the project home). The feel:
simple, visually appealing, the paper aesthetic carried through (serif, calm, archival) —
the researcher works at a slightly higher level with artifacts the model made, but the
connection to the material must stay felt. The rule that enforces it: **every artifact is
made of the material** — intros, steps, and story speak with anchored passages and the
participants' own words, never in abstract summary register ("stupid summaries" are a
validation failure, not a style choice), and the full marked-up text is always one click
away.

## 9. Epistemology positioning

The project declaration (research question + positionality, paper-spec §3.1c) plus an
explicit epistemology line: analysis is conducted *by the human–AI assemblage*, dialogue
logged, interpretation owned by the researcher. Claims we make: reliable application,
verbatim provenance, positional coverage, audited human gates. Claims we never make:
autonomous theme generation. Orthodox reflexive-TA reviewers will still say no; the journal
is what lets everyone else say yes.

## 10. What survives / what's demoted

**Survives untouched**: ingest, sentence ids + offsets, grounding gate, reconcile, revision
log, memos/comments, guidance loop, export + manifest, coverage tool, theme walk (as the
delta engine), theme authority (as finding editing), merge proposals (as assimilation's
gated structural moves), auth, jobs.
**Demoted**: codebook/family curation as a primary view; accept/dismiss queues as a
researcher duty; the three-place paper UI as the app shell (Text keeps the paper design).
**Deleted from the plan**: standing-panel-by-default; chip-dense codebook home.

## 11. Build order

1. **P10.1 engine** — the P9.1a scope (restraint, reuse, RQ scoping, revision actions,
   researcher codes, manifest, coverage tool) built into the READ call architecture (§5)
   with the `span` parameter, sectioned path kept as the coverage-gate fallback; includes
   the span-calibration harness (`tools/read_span_calibrate.py`, endpoint-agnostic so it
   runs against a local model first).
2. **P10.2 engine** — SYNTHESIZE (finding lifecycle + walkthrough + intro + story-so-far)
   + reactions endpoints + session Q&A + on-demand lens. All offline-testable.
3. **P10.3 UI** — Session (walkthrough + reactions + chat) and Journal; Text = paper view
   opened-at-evidence; codebook drawer.
4. **P10.4 validation** — run the Livicia Antoine transcript end-to-end; compare the
   walkthrough + memos against Nirosha's sheet (the pairing assets in `pairing_nirosha/`).
   Acceptance: every walkthrough claim resolves to real spans; the memo layer reads like a
   colleague's; the researcher path from debrief to accepted finding works without ever
   visiting a codebook view.

## 12. Open questions (deliberately deferred)

Walkthrough length calibration (how many steps before fatigue); whether Q&A transcripts
belong in the journal wholesale or as researcher-promoted excerpts; adjudication workspace
for imported human codebooks (unchanged deferral); multi-project cross-corpus sessions.
