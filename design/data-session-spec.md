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

## 5. Pipeline simplification

- **Single coder + critic** becomes the default mode. The blind 3-lens panel remains available
  per project but stops being the flagship; friction machinery survives for it and for
  on-demand second readings. Roughly a third of the coding cost.
- Coder restraint, reuse-before-mint, RQ scoping, and the writable-margin revision actions:
  unchanged from paper-spec §3.1/§5 (P9.1a work — still to build, minus panel-first framing).
- Assimilation (paper-spec §3.2): unchanged — it is what keeps the codebook a stable,
  saturating analytic object underneath the dialogue.
- **New: the debrief generator.** One call per document after assimilation: input = the doc's
  restrained codes (+ evidence), the assimilation digest (minted/filed), the theme-walk step
  snapshot (deltas), memos, research question, and the researcher's standing reactions.
  Output = the typed walkthrough steps. Python-validated: every cited sid must resolve
  (grounding gate), every referenced code/finding id must exist; steps failing validation are
  dropped and counted. Stored per doc (replayable like theme_steps).
- **New: the session Q&A endpoint.** One call per researcher turn; context = retrieval over
  codes/sentences/findings (never whole-corpus stuffing — the lost-in-the-middle guard
  applies to chat too). On-demand lens reading = one call scoped to one passage.

Call budget per document: ~16 coding (coder+critic per section) + 1 assimilation + 1 theme
step + 1 debrief ≈ **19**, vs ~25+ under the panel default. Q&A researcher-paced.

## 6. Epistemology positioning

The project declaration (research question + positionality, paper-spec §3.1c) plus an
explicit epistemology line: analysis is conducted *by the human–AI assemblage*, dialogue
logged, interpretation owned by the researcher. Claims we make: reliable application,
verbatim provenance, positional coverage, audited human gates. Claims we never make:
autonomous theme generation. Orthodox reflexive-TA reviewers will still say no; the journal
is what lets everyone else say yes.

## 7. What survives / what's demoted

**Survives untouched**: ingest, sentence ids + offsets, grounding gate, reconcile, revision
log, memos/comments, guidance loop, export + manifest, coverage tool, theme walk (as the
delta engine), theme authority (as finding editing), merge proposals (as assimilation's
gated structural moves), auth, jobs.
**Demoted**: codebook/family curation as a primary view; accept/dismiss queues as a
researcher duty; the three-place paper UI as the app shell (Text keeps the paper design).
**Deleted from the plan**: standing-panel-by-default; chip-dense codebook home.

## 8. Build order

1. **P10.1 engine** — the P9.1a scope (restraint, reuse, RQ scoping, revision actions,
   researcher codes, manifest, coverage tool) with single-coder default framing.
2. **P10.2 engine** — assimilation (paper-spec §3.2) + finding status layer + debrief
   generator + reactions endpoints + session Q&A + on-demand lens. All offline-testable.
3. **P10.3 UI** — Session (walkthrough + reactions + chat) and Journal; Text = paper view
   opened-at-evidence; codebook drawer.
4. **P10.4 validation** — run the Livicia Antoine transcript end-to-end; compare the
   walkthrough + memos against Nirosha's sheet (the pairing assets in `pairing_nirosha/`).
   Acceptance: every walkthrough claim resolves to real spans; the memo layer reads like a
   colleague's; the researcher path from debrief to accepted finding works without ever
   visiting a codebook view.

## 9. Open questions (deliberately deferred)

Walkthrough length calibration (how many steps before fatigue); whether Q&A transcripts
belong in the journal wholesale or as researcher-promoted excerpts; adjudication workspace
for imported human codebooks (unchanged deferral); multi-project cross-corpus sessions.
