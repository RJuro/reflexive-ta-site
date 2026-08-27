# MASSHINE — what the system is

*Consolidated spec, 2026-08-27. This is the front door: read it first. It states what the
system is after the radical simplification, and what was deliberately deleted to get there.
Detail lives in [data-session-spec.md](data-session-spec.md) (P10, current) and
[paper-spec.md](paper-spec.md) (P9 — engine sections live, UI direction superseded);
[UI_PLAN.md](../UI_PLAN.md) is the build ledger.*

## 1. In one paragraph

MASSHINE is an instrument for reflexive thematic analysis that sits between the computational
and the qualitative. It takes interviews in — as text or as **audio** — reads them closely
and cheaply, and then **debriefs the researcher like a junior colleague who did the immersive
first pass**: here is what I found, here is the evidence, here is what I'm unsure about, here
is what this interview does to what we thought after the last one. The researcher interrogates,
challenges, reframes, and decides. The machine never owns an interpretation, and every
sentence it says is one click from the verbatim words that produced it.

## 2. The simplification

The system grew a full CAQDAS surface — codebooks, families, merge queues, curation views —
and that turned out to be the wrong product. Managing the machine's taxonomy is clerical work;
it made the researcher the AI's librarian. The deletions ARE the design:

| Was | Is now | Why |
|---|---|---|
| ~77 codes per interview | **≤25**, hard cap in the prompt | Human coders hold ~20/transcript. 77 × 30 docs = an unusable 2000-code artifact. |
| Codes minted blind per section | **Reuse-before-mint** — every read sees the codebook so far | Constant comparison. Codes become reusable phenomena instead of transcript-specific micro-observations. |
| 19–25 LLM calls per document | **~2** (READ + SYNTHESIZE) | Thinking is billed per call; one big reasoning budget beats twenty small ones. |
| 3 blind coder lenses, always | **1 coder; second lenses on demand** | Perspectivism where interpretation is actually contested, not as three standing bureaucracies. ~⅓ the cost. |
| 5 primary views (Overview/Codebook/Themes/Friction/Notes) | **3 surfaces: Session · Journal · Text** | The researcher's real objects: the debrief, the accumulating analysis, the material. |
| Codebook as home | **Journal as home; codebook is a drawer + export** | Findings and memos are reflexive TA's artifacts. The codebook is infrastructure and a methods-section appendix. |
| Accept/dismiss merge queues as a duty | **Routine filing is automatic and reversible in the margin** | A queue per bookkeeping item recreates the overwhelm. Structural changes stay gated; clerical ones don't. |
| Researcher curates a taxonomy | **Researcher reacts to claims** (agree / challenge / reframe / park) | Judgment, not administration. Reactions steer the next document. |

**The rule that keeps it honest:** *every artifact is made of the material.* Intros, walkthrough
steps, and the project story speak with anchored passages and the participants' own words.
Abstract summary register is a validation failure, not a style choice.

## 3. The pipeline

```
audio ──► Voxtral ASR (diarized) ──► role mapping ──► canonical transcript ──┐
                                                                             ├──► ingest
text ────────────────────────────────────────────────────────────────────────┘   (sections,
                                                                                 sentence ids,
                                                                                 char offsets)
                                          │
                                          ▼
                    READ  (1 call · whole document · restrained)
                    codes ≤25 · reuses against the codebook · out-of-scope declines
                    · uncertainty flags          → coverage gate → finer span if front-heavy
                                          │
                                          ▼
                    SYNTHESIZE  (1 call)                         [P10.2 — not yet built]
                    finding updates · walkthrough steps · document intro · story-so-far
                                          │
                                          ▼
                    the data session (researcher) + Q&A on demand
```

Everything downstream of ingest hangs off **stable sentence ids with character offsets**. The
model cites ids; it never emits quote text. Quotes are resolved from offsets at render time,
which is why fabrication is structurally impossible rather than merely checked for.

## 4. The three surfaces

- **Session** — the paced walkthrough of one document: patterns (with their *weakest* evidence,
  not only the best), tensions, uncertainties, cross-case deltas, and what was declined as
  out-of-scope. Reactions persist and feed the next read. Open conversation underneath.
- **Journal** — project home. Findings with their evidence and history, memos in both voices
  (the assistant's uncertainty flags and refine proposals; the researcher's own), and the
  versioned story-so-far. A finding cannot be accepted until its evidence has actually been
  opened.
- **Text** — the transcript typeset as a document, marginalia in the gutter, always one click
  from any claim. The destination, never homework.

## 5. Choice of model — and where it runs

Model choice is a **methods fact**, not a preference: the resolved model is recorded per job and
in the export manifest, so a project coded partly with one model is honest in the record.

- **Per-project default, per-run override, server env fallback.**
- Selectable: **MiniMax M3** (production default, thinking-heavy) · **GLM-5.2**, **Mistral
  Large**, **Magistral Medium** (reasoning), **Mistral Medium** — the Mistral roster hosted
  under the university contract, i.e. **EU/GDPR-compliant**, €1.19/M in and €3.74/M out with
  thinking billed as output.
- **ASR** is Voxtral under the same contract. `voxtral-mini-tts` is available on it too — the
  spoken document intro has an engine waiting.
- **Local calibration only, never deployed and never user-selectable:** gpt-5.6-luna via
  codex-cli. It exists so prompt and read-span experiments cost nothing.

Because everything speaks OpenAI conventions, providers are a config profile — not a backend.

## 6. What the researcher actually does

1. Make a project; state the research question and a positionality note (both exported).
2. Drop in interviews — audio or text. Audio is transcribed, diarized, speaker-role-mapped,
   optionally cleaned through a gated redraft, and ingested. Names stay researcher territory.
3. The system reads. Two calls, a few minutes.
4. **Listen or read the intro**, then walk the debrief, reacting as you go.
5. Open the text whenever a claim deserves scrutiny.
6. Next interview: read under your standing reactions; the story-so-far is revised; deltas say
   what changed.
7. Export: findings, codes, every revision, the audit trail, the model manifest.

## 7. Invariants (never simplified away)

Verbatim provenance by construction · positional coverage across the whole document (measured,
gated) · append-only revision log, nothing destructive · researcher gates on every structural
change · declined-material logged · full audit-trail export · per-project git history with
restore points · private material never leaves the gitignored folders.

## 8. Status (2026-08-27)

**Built and deployed** — READ with span parameter + coverage gate; the audio path end-to-end
(verified live: diarization, role mapping, ingest); provider profiles; span-calibration
harness; the P8 authority substrate (revisions, researcher codes, theme authority) underneath.
**Building** — model selection; the Session/Journal/Text UI.
**Next** — SYNTHESIZE (walkthrough, intro, story-so-far), finding lifecycle, git history,
then validation: the Livicia Antoine transcript end-to-end against Nirosha's human coding.

## 9. Positioning

Reliable application, verbatim provenance, corpus-wide coverage, and audited human gates are
what this system claims. Autonomous theme generation is what it does not claim, and the
evidence forbids it. Analysis is conducted by the human–AI assemblage, dialogue logged,
interpretation owned by the researcher — the declaration that makes the tool congruent with
reflexive practice for everyone except the traditions that will refuse it on principle, which
no engineering can address.
