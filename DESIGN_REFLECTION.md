# MASSHINE — Design Reflection (engine + product)

> **What this is.** A thinking document, not an implementation plan. It lays out the real design decisions for the MASSHINE coding system, each as: **the idea → the tension → my lean → the open question(s)**. We work the open questions together, converge, then derive a revised spec and scaffold. Where I make a recommendation it is a *lean*, not a commitment.
>
> **Proposed home once approved:** `/Users/roman/Desktop/MASSHINE_Qual_LLM/DESIGN_REFLECTION.md` (alongside the spec, research, and the preview artifact). The open-questions table at the end is the agenda for our next pass.

---

## Context — why we're reopening the design

The `MASSHINE_v0_SPEC.md` walking skeleton is **methodologically careful but engineering-naïve**. Its locked decisions (no ML, whole-codebook-in-context, sentence-level, files-not-DB, every judgment a fresh LLM call) were chosen to maximize auditability — but they ignore how efficient LLM annotation actually works. They leave money and latency on the table and, more importantly, they encode a *linear, call-per-fact* mental model that doesn't match a cache-optimized, index-based, structured-output system.

Your slide + notes propose a different spine: **index once and never regenerate the text; keep the expensive context in a cacheable prefix; emit structured outputs; aggregate programmatically; keep prompts as external files; manage per-doc state as JSON; co-work through a UI.** This doc reconciles that engineering stance with the reflexive-TA epistemology the research foundation is built on — because several efficiency moves quietly collide with fidelity, and those collisions are the interesting part.

**The product stance (governs everything below).** MASSHINE is an **analytic workbench, not an annotation pipeline.** It does **not automate qualitative coding** — it produces *inspectable, sentence-anchored suggestions and structured disagreement* that a researcher reads, compares, and turns into claims. The felt experience should be **read transcript → invite model suggestions → compare interpretations → write/refine analysis**, not *upload → pipeline → inspect outputs*. The machinery (runs, batches, prompt hashes, packets, event log) is **backstage/audit**; the foreground is transcript, suggestions, evidence, alternative readings, decisions, memos, themes. Foreground verbs are **researcher actions** ("suggest codes", "challenge this reading", "show related decisions", "find overlooked evidence", "draft a theme claim", "explain the disagreement"), not system stages ("run conciliator").

**Decisions already fixed (this session):**
- **Methodology frame: reflexive TA, for now.** (⚠️ see Consideration A — the *grant* spec commits to Charmaz grounded theory; this needs reconciliation before the human benchmark is coded.)
- **Two operating modes, both first-class (Consideration M):** **autopilot** (efficient batch coding of a whole transcript/corpus, minimizing API calls — for comparison/eval) and **co-pilot** (interactive assisted reading, suggestion-by-suggestion, close to the data) — plus an analytic-synthesis surface over codes/themes/memos.
- **Architecture: clean FastAPI backend + JSON-based DB; frontend on top.** Build both now; frontend tech is an open discussion (Consideration J).
- **Runtime: provider-agnostic, OpenAI-compatible API.** Prompts live in external `.prompt` files, hot-loaded, composed from a few analytic-role layers (Consideration K).

**Grant constraints that govern everything (from `aga_masshine_application2026/masshine_spec.md`):**
- Near-term deliverable (**2026-06-30**): *3 prototype architectures operational + a locked output schema for human-AI comparability.*
- Coding unit: **idea unit within a speaker turn** (rules for short/long turns) — already decided, not open.
- Three architectures to **compare**, not one to build: (a) single-agent CoT, (b) coder–critic debate, (c) orchestrator + specialists.
- Output must be **format-compatible with the human benchmark**: code spans + labels + definitions + memos + theoretical sketches.
- **Disagreement is logged as data (CHALET), never adjudicated.** Multiple runs per config → variance envelope.
- Corpus reality: 5k–21k words/transcript (most 8k–15k), speaker-turn dialogue, `[PH]` markers, header metadata. NPS subcorpus excluded.

---

## Design axioms — how LLM systems actually want to be built

These are the engineering principles your notes encode. I'm stating them up front because the rest of the doc treats them as load-bearing, and because each one *changes* a v0 decision.

- **P1 — Index, never regenerate.** The model emits **IDs / char-offsets**, never re-typed source text. The system pulls verbatim spans from the index. This makes **hallucinated *source text* structurally impossible** (not just caught after the fact by the G2 gate) **and** collapses output tokens. *Interpretive* hallucination — in labels, rationales, memos, sketches, claims about absent context — still has to be caught by critique, provenance, and human review. Still the single most important shift from v0.
- **P2 — Maximize cacheability; don't assume cache economics.** Order every prompt so the expensive, reused material (full transcript, codebook digest, structure map) sits in a **byte-stable prefix**, and only the small per-unit ask varies at the end. *If* the provider offers reliable prefix caching, full-transcript context becomes economically viable — which would dissolve the "summary-for-semantic / full-text-for-latent" split in your slide (Consideration E). But OpenAI-compatible endpoints vary wildly (no cache / opaque hit rates / token thresholds / invalidation on tiny edits), and cached input doesn't always fix wall-clock latency. So: **design for cacheability, measure before relying on it** (validation probe below).
- **P3 — Models judge and write; code counts and joins.** LLMs emit typed JSON (a code, a merge verdict, a section↔theme match). All aggregation, counting, dedues-by-id, and saturation math is **plain Python**. "Find a smart algo for theme aggregation" → the algo is mostly bookkeeping; the *construction* stays LLM+human (Consideration G).
- **P4 — Determinism where it isn't interpretive.** temp 0 for parsing / indexing / theme-assignment / verification. Higher temp or persona variation **only** at the interpretive coding step, where divergence is the product, not noise.
- **P5 — Prompts are data.** External `.prompt` files, versioned, hot-loadable. The system's entire analytic logic is readable as *prompts + schema + JSON state* — which is also what makes it auditable.
- **P6 — State is an (initially minimal) event log with JSON views.** Working state is per-doc JSON + a project-level codebook (the **index is the spine**: codes point at sentence IDs, themes at codes, memos at anything). The *audit layer* is an **append-only log of mutations** — create / merge / split / edit / human-decision, each with who, why, prompt-version, run. **For v1: just append events + export current state — no replay logic yet** (replay can come later); we already model most of these as `MergeDecision` / `HumanDecision`, so it's naming, not new weight.
- **P7 — Don't pre-segment interpretation; index evidence, let coding create the units.** The mechanical layer is a **sentence index** (+ speaker-turn metadata); it is *not* an analytic decision. Codes attach to one or more **sentence IDs**; the "idea-unit within a speaker turn" is given to the model as an *instruction*, not built as a preprocessing object. This is the simplification that de-risks the first build (Consideration C).
- **P8 — Workbench, not pipeline; suggestions, not classifications.** The system foregrounds *suggestion · alternative reading · evidence · precedent · decision · memo* — never *classification / prediction / annotation-completion / consensus*. Machinery stays backstage (audit mode); the everyday surface is the transcript and the researcher's own analytic actions. The headline claim to protect: **MASSHINE doesn't automate coding — it structures inspectable disagreement around sentence-anchored evidence.**

**The meta-tension to hold throughout:** every efficiency move trades against auditability or reflexive fidelity *somewhere*. The win condition is to take the efficiency where it's free (P1, P2, P3-counting) and *refuse* it where it would flatten interpretation (don't cut rationales to save tokens; don't let a similarity score name a theme).

### Revisions after an external review pass — what I took, what I didn't
A second model reviewed the first draft. Its spine-level verdict matched: index-never-regenerate, frame-neutral schema, architecture-as-config, LLM-judges/Python-aggregates, external prompts, async backend — all sound. I **adopted** its systems-engineering corrections (woven into the considerations below): SQLite-operational + JSON-export instead of bare JSON files (B); event-log-as-audit-layer (P6); runs/configs **first-class now** (H); provider **capability flags** not a bare "OpenAI-compatible" assumption (K); **batch ≠ analytic unit** (K); a **curated comparison packet + `defer` verdict** for the conciliator (F); `method_frame` declared per run (I); and two precise rewordings (P1, P2).
I **declined two** per your steer: (1) **no schema blowup** — analytic objects stay minimal; all operational metadata moves to a `Run`/provenance *envelope* that wraps artifacts (I); (2) **no heavyweight segmentation subsystem**.
A **further review** contributed the deepest reframe — codebook context-selection as a **constrained submodular / precedent-based selection problem**, not similarity retrieval (Consideration F+). Folded in, because it *strengthens* the no-embeddings stance.
A **third simplification pass** then cut v1 scope hard (and I agree — several parts had drifted into over-engineering): **drop the idea-unit overlay entirely** in favour of a sentence index (P7, C); **minimal event log, no replay** (P6); **dumb fixed-quota "coverage packet v0"** before any formal submodular scoring (F+); **no full theorist loop** in v1 (G); **sentence-level highlighting**, defer arbitrary char-span selection (J). The infrastructure stays (SQLite, JSON export, Run envelope, prompt hashes, capability flags, sentence/char index, structured output, comparison packets, architecture configs, runs first-class) — that's not over-engineering, it's the spine.
A **fourth pass (researcher/UX + theory)** reframed *what the system is*: an **analytic workbench, not a pipeline** (P8) — suggestions and structured disagreement, never automation. This added: **two first-class operating modes, autopilot + co-pilot** (M, your explicit requirement); **phase-dependent reconciliation** — loose in open coding, stabilize late — to avoid behaving like positivist reliability software (F); specialists exposed as **researcher actions / "lenses"** not an agent pipeline (H); the **four-view UX + backstage/audit split** (J); **prompt modules at the role layer**, not fragments (K); **precedent** language instead of "comparison packet" (F+); and **distinct memo kinds** so model rationales don't pollute the memo tradition (I). See **Engine v1 — the lean build shape** below.

---

## Considerations & open questions

### A. Methodology vocabulary — RTA now, but the grant says grounded theory
**Idea.** Build the engine and schema in reflexive-TA terms (your call for now).
**Tension.** The funded grant commits to **Charmaz pragmatic-constructivist GT** — *initial coding → focused coding → theoretical sketching*, idea-unit-within-turn — and the **human benchmark coders will code in that frame**. The hard constraint is "output format-compatible with the human benchmark." If humans produce GT artifacts (initial codes, focused codes, sketches, memos) and the engine produces RTA artifacts (semantic/latent codes, themes), the blind comparison breaks at the schema level.
**Lean.** Use RTA *language* in stakeholder-facing material (the preview artifact), but design the **output schema to be frame-neutral** so it can carry either: `{span, label, definition, memo, code_type, evidence_refs}` maps cleanly onto both "semantic/latent code" (RTA) and "initial/focused code" (GT). Decide the *governing* frame before the benchmark is coded, not before the engine is scaffolded.
**Open Q1.** Is "RTA for now" a genuine pivot away from the grant's GT, or a working frame for prototyping while the schema stays frame-neutral? (This determines whether the human coders' instructions and the engine's prompts share vocabulary.)

### B. State, persistence & the project-level codebook
**Idea.** Per-doc JSON state; project as a collection; FastAPI + a JSON-based DB. The index is the spine.
**Tension.** v0 chose files+git for provenance-as-diff. Mongo/JSON-DB is more ergonomic for hierarchical state and a UI, but you lose `git diff` as the free audit layer unless you design for it.
**Lean (revised after review).** **SQLite as the operational store, JSON export always.** A prototype with async jobs + UI writes + multiple runs + codebook mutations will hit race conditions and corrupted writes on plain JSON files fast. SQLite buys transactions, querying, and async safety with **zero ops dependency** — and JSON-snapshot exports after each run/checkpoint preserve the git-diffable provenance we wanted (and feed the frontend). JSON columns where a blob is genuinely document-shaped. Mongo stays a Phase-4 question, not a now question. The **project-level codebook** is a *materialized view* over the mutation log (P6): `{code_id, label, definition, first_seen, member_code_ids[], minority_flag}` as the view; create/merge/split/edit as logged events.
**Open Q2.** Confirm SQLite-operational + JSON-export (my lean) over a pure JSON-document store? And: is the codebook **global to the project** from the first document, or **per-document then reconciled up**?

### C. Indexing — sentence index only, no interpretive pre-segmentation *(simplified)*
**Idea.** Index evidence mechanically; let coding create the analytic units (P7).
**Tension.** Earlier drafts added an LLM **idea-unit overlay** (propose units → freeze → code units). That's over-engineering for v1: idea-unit splitting is itself interpretive, so a frozen overlay can contaminate everything downstream, and it needs versioning/correction machinery we don't want to build now.
**Lean (simplified — final).** **One mechanical layer: a sentence index.** Deterministic parse → turns + speakers + a **sentence index with char offsets**: `Sentence{id, doc_id, turn_id, speaker, char_start, char_end}`. That's it — no unit overlay, no frozen segmentation, no regeneration rules. The coder receives a **flexible window** (a turn or a section) and decides which **sentence IDs** support each code (one ID or a short adjacent range `S103–S108`). The grant's "idea unit within a speaker turn" is honoured as an **instruction in the coder prompt**, not a preprocessing object: *"Code at the level of analytically coherent idea units within speaker turns; use sentence IDs as evidence anchors; a code may cite one sentence or a short adjacent range."* So the model does idea-unit *reasoning*; the system only indexes *sentences*. Integrity = `document_text_hash` + `sentence_index_hash` (no segmentation versioning). Char-level spans can be added later if fine-grained highlighting is needed.
**Open Q3.** Confirm: sentence index is the only mechanical layer; idea-unit lives in the prompt, not as objects; evidence = sentence IDs/ranges. (If the *benchmark* truly requires idea-units as a comparison object, we revisit — but I don't think it does.)

### D. Familiarization & how we model "reflexive"
**Idea.** Either a neutral summary (the context a coder would build in their head) or the whole text in context — "try both."
**Tension.** Under P2 (caching), full-text-in-prefix is cheap, so the *cost* argument for summaries largely evaporates. What remains is an *epistemic* argument: does a summary-primed coder behave more like a familiarized human, or does it just lose grounding? Reflexive TA treats familiarization as irreducibly human (research.md, DeTAILS caution).
**Lean.** Default to **full text in the cached prefix** + a **neutral, descriptive familiarization memo** (not a summary that pre-interprets) as a first-class artifact the *human* reads too — framed as scaffolding for human reflexivity, not a substitute. Keep "summary-only" as a measurable experimental arm, not the default.
**Open Q4.** Is "reflexive" modeled as (i) prompt framing + positionality-anchored personas, (ii) the human-in-loop checkpoints, or (iii) both — and do we run summary-vs-full-text as an actual experiment or just pick full-text?

### E. The coder — context assembly, semantic/latent, per-segment memo, personas
**Idea.** Coder needs context (what came before, the overall arc, the interview questions). Semantic + latent codes. Write a short interpretive piece per segment. Paradigm-anchored personas. Parallel passes.
**Tension.** Your slide proposes **summary→semantic, full-text→latent** as a cost split. But (a) P2 makes full context cheap for *both*; (b) "minimise output tokens" collides with the finding (Dunivin) that **CoT rationales materially improve coding fidelity** — the rationale/memo is exactly the interpretive content we must *not* cut.
**Lean.** One coder prompt shape, cached prefix = `[familiarization memo][full transcript][codebook digest]`, variable tail = `[a window of adjacent sentences/turns + "code at idea-unit level; cite sentence IDs"]`. The coder emits, **by sentence ID** (P7): `{code_type: semantic|latent, label, definition, evidence_sentence_ids: ["S0042","S0043"], rationale_memo}`. **Keep the rationale_memo** — it's the auditable interpretive lift and the thing the September workshop scores ("reasoning or confabulation?"). Personas = **prompt variants anchored in positionality** (ties to the grant's positionality memos), not different code types — each persona emits both semantic and latent where warranted. (This corrects a conflation already fixed in the preview artifact.)
**Open Q5.** Do we keep the summary/full-text *split per code-type*, or unify on cached-full-context for both and drop the split? And: how many personas, anchored to which paradigms/positionalities?

### F. The conciliator & codebook evolution
**Idea.** New code vs. the project codebook → create / merge / minority, with written rationale; divergence preserved.
**Tension.** Whole-codebook-in-context is auditable but caps scale (v0 knew this). Embedding-retrieval is the literature's "proven recipe" but reintroduces the cosine-number opacity RTA objects to. **And the real theoretical danger (from review): premature stabilization.** Forcing every new code into create/merge/minority/defer *early* makes the system behave like positivist coding-reliability software — it optimizes agreement before the analysis has earned it, which is exactly what reflexive TA rejects.
**Lean (revised — now phase-dependent).** Keep the **LLM conciliator with a written rationale** as the unit of decision (P3: it *judges*; code applies the verdict), but **gate it by analytic phase**:
- **Phase 1 — open coding / exploration:** *low reconciliation.* Accumulate candidate codes freely; the model suggests, the human reads. No forced merging.
- **Phase 2 — codebook shaping:** precedent packets become useful; create/merge/split/minority/**defer** decisions with rationale; definitions improve.
- **Phase 3 — theme construction:** codes become evidence for claims; contradictions and minority cases are *surfaced*, not resolved away.

It sees not a raw codebook dump but a **precedent set** (mechanism in F+). Verdict ∈ **create | merge | split | minority | defer** — `defer` matters most in Phase 1. **Never delete** a minority code (CHALET, grant). The create/merge/minority **ratio is itself a saturation signal**.
**Open Q6.** Is the phase set by the researcher, or inferred from saturation signals? At what corpus size (if ever, at 8–16 docs) do we need a digest/retrieval rather than the whole codebook? Reconcile **continuously** or **in batches**?

#### F+ · Building the comparison packet — context selection, not similarity retrieval *(DEFERRED — not built in v1)*
> **ponytail:** at 8–16 transcripts the per-project codebook fits in the prompt, so v1 **sends the whole codebook** — none of the machinery below is built. This section is kept only as the upgrade path *if* the codebook ever stops fitting (and even then, prompt-based selection before embeddings, per Q6).

The deeper CS insight: **choosing what prior codes the conciliator sees is not a clustering/similarity problem — it's a constrained *context-selection* problem**, and it can be solved *without embeddings*, which keeps faith with the no-cosine stance (decision A / §9). Two framings combine:

- **Codebook as a precedent system (case-based reasoning).** Prior codes aren't labels to match — they're *cases*: "when we saw this kind of excerpt before, what did we decide, why, and what boundary did we draw?" The conciliator should see relevant precedents, **contrasting** precedents, **minority** precedents, and **overruled/merged** ones — prior analytic *decisions* that constrain or complicate this one. Closer to legal reasoning and qualitative memoing than to retrieval, and methodologically more defensible. **User-facing, this is never called a "comparison packet"** — it's *related previous decisions / boundary cases / minority readings / contrasting examples*, and the system can always answer "why are you showing me these?" (same section · recent boundary decision · minority interpretation · frequent code · under-reviewed). "Packet"/"submodular" are backstage words only.
- **Packet = submodular coverage, selected greedily.** Define what a *good* packet covers, then take the k items with the largest marginal gain — every feature transparent metadata, **no vector math**:
  ```
  gain(candidate, packet) =
      relevance_to_context     (same doc / question / speaker role / section / code-type / family)
    + coverage_gain            (adds a family or boundary not yet represented)
    + minority_bonus + boundary_case_bonus + recency_bonus + stability_bonus
    − redundancy_with_packet − overexposure_penalty
  greedy:  while |packet| < k:  add argmax marginal gain
  ```
  The packet is "the k codes that best *cover* the relevant analytic landscape," not "the k most similar" — and you can show *why* each code is in it (auditable input, not a cosine score).

Supporting mechanisms, layered only as needed:
- **Rolling coreset:** always include a small representative subset of the codebook (stable + minority + boundary + recent) so every decision is anchored to the codebook's shape; add local candidates on top.
- **Exploration–exploitation exposure:** young codebook → explore (surface minority/old/underexposed); stabilizing → exploit (dominant/relevant); rising disagreement → raise contested/minority exposure. Stops the system always re-showing dominant codes.
- **Active-learning routing:** the same selector decides *what needs human/critic attention* — candidates that straddle several boundaries, sit in under-reviewed areas, conflict with a stable code, repeat a minority, or show high cross-architecture disagreement get routed up. This is how we tame noise from diverse annotation **without flattening it** (route the unstable cases, don't vote them away).

**Parked as deeper/later (named so we don't reinvent them):** DPPs (diversity via metadata — but greedy submodular is simpler and more explainable), formal concept analysis (a code/attribute lattice — elegant, too heavy for v1), and **argumentation frameworks** (decisions as arguments with supports/attacks/rebuts — too much for June, but a strong candidate for the *methods-paper contribution*: a **structured-disagreement engine**, not a consensus engine — exactly the CHALET stance).

**Why this matters for MASSHINE:** it gives codebook context-selection a principled foundation that (a) needs **no embeddings**, so it doesn't smuggle back the opaque cosine numbers RTA/§9 reject; (b) makes the conciliator's *input* auditable, not just its output; and (c) turns saturation, minority-preservation, and disagreement-routing into properties of **one selection policy** instead of bolted-on heuristics.

**v1 = "coverage packet v0" (dumb on purpose).** Don't implement the gain function, coreset math, or exploration policy yet. Ship a **fixed-quota** packet first and formalize later:
```
coverage packet v0 = 3 most-recent decisions + 3 same-document/section
                   + 3 stable high-frequency codes + 3 minority/contested + 2 random/underexposed
```
Same *intent* (cover recent + local + stable + minority + a control), trivial to build and read. The gain-function/coreset/bandit version is an upgrade path, not a v1 requirement.
**Open Q6b (resolved-ish):** v1 = coverage packet v0 (fixed quotas); formal submodular + coreset + exploration deferred to the research track. Confirm the quota mix.

### G. The theorist & theming — the "smart algo," reframed
**Idea.** 0-shot seed themes from a transcript; iterate by section emitting **KV (section ↔ theme) matches**; aggregate programmatically; then iterate over themes (merge / split / create) while seeing siblings.
**Tension.** "Find a smart algo for theme aggregation" is exactly where pipelines slide into **theme-as-bucket** (research.md's recurring distortion). If a clustering/aggregation step *names* the theme, you've replaced "pattern of shared meaning" with "cluster of codes."
**Lean (cleanest application of P3) — but minimal for v1.** Split the work: **(1) Assignment & counting = programmatic.** The LLM emits structured `{section_id, theme_id, strength}` KV pairs; Python aggregates coverage, co-occurrence, counts. **(2) Construction = LLM + human.** The programmatic layer organizes; it never *names*. **For v1, do NOT build a full theorist/refinement loop.** One pass produces, per candidate theme: `{supporting_code_ids[], contradicting/minority_code_ids[], central_concept_memo}` — a claim, not a label, with its counter-evidence attached — and **the human does the merge/split/refine at the checkpoint.** Saturation tracked at **two levels** (code-level: new codes/turn slowing; theme-level: new themes/doc slowing) as *human-prompting signals*, not hard stops.
**Open Q7.** Semantic + latent in one theming pass or two? (Lean: one pass, flagged.) Confirm v1 stops at candidate-themes-with-counter-evidence + human refinement, with the LLM-proposed merge/split loop deferred.

### H. Architectures as configuration (the v0 spec missed this)
**Idea (from grant).** Three architectures must be *compared*: single-agent CoT, coder–critic debate, orchestrator+specialists.
**Tension.** v0 hard-codes one pipeline (orchestrator+specialists). The grant's actual deliverable is a **harness that can run all three over the same state and emit the same schema**, with multiple runs per config for a variance envelope.
**Lean (revised — config-driven harness, but exposed as *lenses* not *agents*).** Design the engine as a **config-driven harness**: shared state + index + schema, and an architecture config (`single_cot` | `coder_critic` | `orchestrator`) wiring which steps run in what order. Specialists (coder, critic, conciliator, theorist, surprise-detector) are **composable steps internally** — but coding, critique, memoing, and theming are *intertwined*, so exposing them as a rigid agent pipeline feels artificial. **User-facing, they appear as researcher actions / "model lenses":** *Suggest codes · Challenge this reading · Compare with previous decisions · Find overlooked evidence · Sketch a possible theme · Explain disagreement between runs.* Each lens maps internally to a modular prompt (Consideration K); the researcher never picks "run the conciliator."
**And: runs are first-class *now*, not a later eval bolt-on.** Every run is an addressable object `Run{id, architecture, method_frame, model, temperature, seed?, prompt_versions{}, repetition_idx}`; N repetitions per config are part of the core data model — so the grant's variance-envelope and architecture comparison fall out as *queries over runs*, not a retrofit.
**Resolved (was Open Q8):** variance/repetition is first-class from day one.

### I. The output schema — lock this first
**Idea.** Benchmark-compatible: spans + labels + definitions + memos + sketches; full provenance chain.
**Tension.** This is the **June 30 gating item** and everything (human coding instructions, engine output, evaluation) depends on it. It's also where the RTA/GT question (A) bites hardest.
**Lean (minimal, sentence-anchored).** The whole analytic schema is two objects + three small ones:
```
Sentence { id, doc_id, turn_id, speaker, char_start, char_end }      # the only index layer (P7)
Code     { id, label, definition, code_type, evidence_sentence_ids[], model_rationale }
CandidateTheme { id, central_concept_memo, supporting_code_ids[], contradicting_code_ids[] }
Memo     { id, kind, author_type(human|model), text, links[] }       # kind ∈ model_rationale | critic_note | researcher_memo | theoretical_memo
HumanDecision { id, target, action, rationale }
```
`code_type` is the union across frames (`semantic|latent|initial|focused`). **Keep memo authorship distinct** (review point 9): the model's `model_rationale` and a `critic_note` are *not* the same kind of object as a `researcher_memo` or `theoretical_memo` — collapsing them pollutes the memo tradition with machine justifications. One `Memo.kind` field handles it; the UI renders them visually distinct (Consideration J). **All operational metadata (run_id, architecture, persona, prompt_version, model, temperature, status) lives in the `Run`/provenance envelope that *wraps* artifacts — not inlined into every `Code`** — full comparability without schema bloat. Declare **`method_frame` + `coding_stage` on the Run** so frame-neutral-*looking* objects aren't mistaken for methodologically comparable ones. Every theme resolves theme → codes → **sentence IDs** → text (the interpretive-depth audit). Verbatim verification = *resolve-by-ID* (P1, P7), not string search.
**Open Q9.** Human benchmark sketches as structured objects or free memos? (Lean: `central_concept_memo` is one free-text field for v1; structure later if the benchmark needs it.)

### J. Backend / frontend — build both now; frontend is the open discussion
**Idea.** FastAPI backend + JSON DB; a co-working annotation UI (span highlighting, hierarchy-of-aggregation views, human split/merge/annotate writing back as HumanDecisions).
**Backend lean.** FastAPI service exposing resources along the state spine: `projects / documents / index / codes / codebook / themes / decisions / runs`. LLM calls run as **async jobs** (coding a document is long-running) with a job/status endpoint the UI polls. Provider-agnostic OpenAI-compatible client; prompts loaded from `.prompt` files (P5).
**Frontend — interaction model before framework, and machinery backstage (P8).** The first decision isn't React-vs-Svelte, it's the **researcher-facing surface**. Four views carry the workbench:
- **A · Reading view (home).** Transcript on the left; suggested codes / evidence / rationale / *alternative readings* in the margin; sentence-level highlighting. The researcher stays close to the material.
- **B · Codebook view.** The codebook as an *analytic object*, not a table: code · definition · example excerpts · memos · minority/contested status · where it appears · merge/split history.
- **C · Comparison view.** Where runs (architectures / models / personas) agree and disagree, which excerpts create the disagreement, which minority readings recur — this is where the variance envelope becomes *useful to a researcher*, not just an eval number.
- **D · Theme / memo view.** Feels like *writing*, not clustering: candidate theme · central claim · supporting excerpts · contradicting excerpts · linked codes · researcher memo. Supports argument-building.

**Two visibility layers.** Foreground = transcript · suggestions · evidence · alternative readings · decisions · memos · themes · comparison. **Backstage / "audit mode"** = run metadata · prompt versions · batch IDs · provider flags · event log · JSON exports · cost ledger. Normal work shows *code + evidence + rationale*; one click ("why did the model suggest this?") reveals the audit detail. **Auditable without being bureaucratic.**

The decisive *backend* capability is whether the data model can represent overlapping/nested spans, sentence-level vs range annotations, human corrections, merges/splits, memos on *any* object (by `kind`), and **comparison across runs** — you can build the wrong thing beautifully in any framework. Framework options matter only at the margin:

| Option | Fit | Cost |
|---|---|---|
| **SvelteKit or React + a *simple custom* span renderer** | Right default: full control of span interaction, no early lock-in to a heavy lib | Build the renderer yourself (but it's small) |
| **React/Next + a mature annotation lib** (recogito-js etc.) | Tempting for NVivo-like richness | Easy to get *trapped* integrating it early — review's explicit warning |
| **Server-rendered + HTMX** | Fast to a working tool | Rich interactive span-editing fights you |
| **Streamlit / Panel** | Quickest *internal* run-inspector for June–Sept | Not productizable; weak span UX |

**Lean (simplified for v1).** FastAPI + SQLite + **sentence-level highlighting** — a clickable sentence index gives you evidence display and annotation *without* arbitrary char-span selection, which is the expensive part. Defer fine-grained char-span UI (and any heavy annotation library) to later. SvelteKit or React as the shell; the renderer stays small because the unit of interaction is the indexed sentence (P7).
**Open Q10.** Is the frontend a **productizable end-user app** (build the custom renderer now) or **internal team tooling for June–Sept** that CALDISS later replaces (a Streamlit run-inspector buys a week)? Two different bets — and it changes how much we invest in the span renderer.

### K. Efficiency, prompt modularity & cost mechanics
**Lean (revised after review).** Make the principles operational: (1) **prompt assembly order** fixed as `[system][familiarization][full transcript][codebook digest] || [variable ask]` so the prefix *can* cache (P2 — verify, don't assume); (2) **structured outputs** via Pydantic-validated JSON; (3) **separate the batch (API-call) level from the analytic (code) level** — the coder gets a **window of adjacent sentences/turns** per call and cites sentence IDs; failed windows retried independently (per-call coding of a whole transcript at once hurts interpretive quality and error recovery; one call per sentence wastes everything); (4) **temp 0** except interpretive coding; (5) **one MiniMax-M3 client** via the OpenAI-compatible SDK — *no provider-capability abstraction until a 2nd provider is real* (ponytail/YAGNI; the capability-flags layer is the upgrade path, not v1). Ledger tokens-in/out and cost per document.

**Prompt modularity — at the level of analytic roles, not fragments (review point 5).** Compose each prompt from a **small number of readable layers**, not a swarm of rule-snippets (that way lies prompt spaghetti):
```
[ method_frame ]   reflexive_ta | charmaz_gt
[ role/lens ]      coder | critic | conciliator | theorist
[ task ]           "suggest codes for this window" | "challenge this reading" | ...
[ context ]        transcript window + precedents + codebook notes
[ output_schema ]  the JSON contract
```
Good `.prompt` modules: `method_frame`, `coder`, `critic`, `conciliator`, `theorist`, `output_schema`, `style_constraints`. **Avoid** `definition_rules` / `rationale_rules` / `evidence_rules` / `json_rules` fragmentation. **Versioning is backstage (P8):** every suggestion is traceable to its prompt-version/run in audit mode, but a researcher never sees a hash during normal work.
**Open Q11.** Which OpenAI-compatible provider(s) for prototyping, and which capability flags do they satisfy (esp. explicit prompt-caching and batch)? (MiniMax-M3 is in the repo config; the grant implies multi-family for bias control.)

### M. Operating modes — autopilot, co-pilot, synthesis *(explicit requirement)*
**Idea.** Pure batch coding feels like "the model did the analysis" — the core theoretical problem (P8). But batch is exactly what you need for efficiency and for architecture/variance comparison. So ship **both**, first-class:
- **Autopilot.** Codes a whole transcript/corpus efficiently, **minimizing API calls** — full-text in cached prefix (P2), windowed batching (K3), structured output, temp-0 deterministic stages, parallel windows. This drives the August benchmark runs, the variance envelope, and synthetic recovery. Output is *suggestions to inspect*, never an accepted analysis.
- **Co-pilot.** Interactive assisted reading: the researcher reads and asks a **lens** to act on a passage ("suggest codes here", "challenge my code", "show related decisions"). Low-latency, close to the data, one suggestion at a time. This is where reflexivity actually lives.
- **Synthesis.** Working over accumulated codes/themes/memos: "what patterns are emerging?", "where are the contradictions?", "what theme claim connects these excerpts?" Argument-building, not a clustering screen (view D).

All three run on the **same state, schema, and runs model** — autopilot and co-pilot differ in *granularity and latency*, not in their artifacts. Phase-dependent reconciliation (F) applies to both: autopilot in Phase-1 should *accumulate*, not prematurely stabilize.
**Open Q12.** For the June prototype, which mode is the first build target — co-pilot (best showcases the workbench stance) or autopilot (fastest path to benchmark/variance data)? (Lean: co-pilot loop first, autopilot as the same lenses run unattended over windows.)

### L. Evaluation hooks (mostly inherited — confirm, don't redesign)
Synthetic-recovery track (planted themes, different model family as judge, distractors for hallucination baseline), variance envelope (H), ground-truth firewall, blind expert workshop comparability. **Lean:** bake the `run_id` + config + prompt-version stamping in from day one so every artifact is attributable — evaluation is then mostly queries over state, not a separate system.

---

## Engine v1 — the lean build shape

A **shared core** both operating modes (M) run on — the researcher loop is *read → suggest → compare → decide → memo → theme*, never a one-way pipeline:

```
import transcript → deterministic parse (turns, speakers) → sentence index w/ char offsets   (only mechanical layer — P7)
create run { architecture, method_frame, model, temp, prompt_versions }

— a LENS acts on a window of adjacent sentences/turns (idea-unit reasoning lives in the prompt) —
   model → { code_type, label, definition, evidence_sentence_ids[], model_rationale }
   system resolves evidence text by ID                                   (never regenerated — P1)
   show "related previous decisions" (coverage packet v0: recent/local/stable/minority/random)
   conciliator → create | merge | split | minority | defer  + rationale   (phase-gated — loose in Phase 1)
   append event to log + JSON export                                      (no replay yet — P6)

— synthesis —
   one theme pass → candidate themes + supporting/contradicting code IDs + central-concept memo
   human refines at checkpoints (split / merge / write memo / draft claim)
```
- **Co-pilot** = a researcher triggers a lens on one passage at a time (low latency, view A).
- **Autopilot** = the same lenses run unattended over all windows, minimizing API calls (cached prefix, batched windows, parallel) → suggestions to inspect in views B/C, never an accepted analysis.

**Keep (infrastructure, backstage):** SQLite + JSON exports · `Run`/provenance envelope · `.prompt` role-layer modules with version hashes · provider capability flags · sentence/char index · structured JSON output · precedent packets · architecture configs · runs/repetitions first-class.
**Cut/defer from v1:** idea-unit overlay & frozen segmentation (→ prompt instruction only) · event replay (→ append+export) · formal submodular/coreset/exploration packet (→ fixed-quota v0) · full theorist/refinement loop (→ one pass + human) · arbitrary char-span UI (→ sentence highlighting) · rigid agent-pipeline UI (→ researcher-action lenses).

---

## What must be locked by 2026-06-30 (vs. what can stay open)

**Lock now (gates the prototypes + benchmark):**
- The **workbench stance** (P8) and **two operating modes** (M): suggestions not automation; autopilot + co-pilot on shared state.
- The **minimal sentence-anchored schema** (+ distinct `Memo.kind`) + **`Run`/provenance envelope** (I).
- The **sentence index as the only mechanical layer** (P7, C) — idea-unit lives in the prompt; evidence = sentence IDs.
- The **architecture-as-config** harness with **runs/repetitions first-class** (H), specialists exposed as **lenses** not stages.
- The **index-never-regenerate / resolve-by-ID** contract (P1), prompt-assembly order + **role-layer prompt modules** (K).
- **SQLite + JSON-export** (B), append-only decisions/no-replay (P6), **one MiniMax-M3 client** — no provider abstraction (K).
- **`method_frame` per run** (I); **phase inferred from saturation** (F); conciliator gets the **whole per-project codebook in the prompt** — no packet selector (F/F+ deferred).
- **spaCy sentence tokenization** — no hand-rolled tokenizer (C).
- **Foreground/backstage split** (P8/J): machinery hidden in audit mode from day one.

**Can stay open through July:**
- Which mode is the first build target — co-pilot vs autopilot (M, Q12); frontend framework (J) — but lock the *interaction model* + four views + data model; sentence-highlighting UI; start the API now.
- Packet sophistication (F+: formal submodular/coreset/exploration), theme-refinement loop (G), char-span UI (J), phase-inference vs phase-set-by-researcher (F) — all deferred upgrades.
- Summary-vs-full-text experiment (D/E), saturation thresholds (F/G), persona roster (E), continuous-vs-batch reconciliation (F).

**Reconcile before the benchmark is coded (August):**
- RTA vs GT governing frame (A).

---

## How we'll validate the design choices (before committing to code)

1. **Schema dry-run:** hand-code a few sentences of GRANDE (or take a human-coded transcript) and express it as `Code{evidence_sentence_ids[...]}`. If the human output doesn't fit cleanly, the schema (I) is wrong — fix before building.
2. **Cache/cost probe:** a 20-line spike that codes a few sentence-windows of a 15k-word transcript with full-text-in-prefix, measuring cache-hit rate and cost/window on the target provider — confirms P2's economics before we commit to "full context for everything."
3. **Bucket-test the theme pass:** run the assignment + concept-memo pass (G) on one transcript and check that candidate themes read as *claims* with attached counter-evidence, not category labels — the research.md failure mode.
4. **Three-config smoke test:** the harness (H) runs `single_cot` and `orchestrator` over the same document and emits the same schema — proves the comparison is apples-to-apples.
5. **Packet probe:** for a real candidate code, confirm coverage packet v0 (F+) surfaces minority / contrasting precedents, not just recent/dominant codes — if the quotas drown out minority, retune them.
6. **Co-pilot feel test:** walk one passage through *read → ask a lens → see suggestion + evidence + alternative reading → see related decisions → accept/edit/memo* (M, view A). If it feels like inspecting pipeline output rather than co-working, the foreground/backstage split (P8) is wrong.

---

## Resolved this pass (your answers) — and what they let us NOT build

> **Operating discipline: ponytail.** Old grumpy dev. Lazy = efficient, not careless. Climb the ladder (does it need to exist? → stdlib/lib → one line → minimal code) and stop at the first rung that holds. Every answer below was used to *cut* scope, not add it.

| # | Decision (locked) | What we therefore DON'T build |
|---|---|---|
| Q1 | **Reflexive TA** vocabulary for now (`method_frame` field still carries it, so GT later is a config change). | — |
| Q2 | **SQLite + JSON export.** **Codebook is per-project**, global from doc 1. | No per-doc-then-reconcile-up dance. |
| Q3 | Sentence index is the only mechanical layer. **Tokenize with spaCy** (or stdlib) — **no hand-rolled tokenizer.** | No idea-unit overlay, no segmentation engineering. |
| Q4 | "Reflexive" = prompt framing/personas **and** human checkpoints. | — |
| Q5 | Coder context: **my call** — one cached-full-context prompt, semantic+latent together (test splits later). | No per-code-type prompt split in v1. |
| Q6 | **No embeddings — all prompt-based.** Phase **inferred from saturation** (count new codes/window; slowdown flips phase — one function, tune later). | No ITS curve math, no embedding/retrieval. |
| Q6b | At 8–16 transcripts the **per-project codebook fits in the prompt → send the whole thing.** | **No packet selector, no coreset, no submodular/bandit/coverage math.** (That whole F+ apparatus only exists *if* the codebook one day stops fitting — parked.) |
| Q7 | **One theme pass** (variants later). | No theorist/critic refinement loop. |
| Q9 | Human input = **free-text memos**, injected into prompts **labeled as researcher memos** (kept distinct from model rationale). | No structured-sketch schema. |
| Q10 | **Proper app from the start**, simple design, refine later (SvelteKit/React + sentence highlighting). | No heavy annotation lib, no char-span UI yet. |
| Q11 | **Route everything to MiniMax-M3** via the OpenAI-compatible client. | **No provider-capability abstraction layer** until a 2nd provider is real (YAGNI). |
| Q12 | **Build both, autopilot first, in phases.** | No co-pilot UI until autopilot produces something worth reading. |

**Net effect on the doc above:** F+ (submodular/coreset/exploration) and the K capability-flags adapter are **deferred, not built**; the conciliator just gets the whole project codebook; the provider layer is one MiniMax client. Everything else in the considerations stands but in its *minimal* form.

---

## Build plan — autopilot first, in phases (ponytail)

Each phase ships something runnable. Stop and look before starting the next. `ponytail:` comments mark deliberate shortcuts + their upgrade path.

- **Phase 0 — skeleton.** FastAPI app + SQLite + `Run` row. Import a transcript, spaCy-tokenize → `Sentence{id,doc_id,turn_id,speaker,char_start,char_end}` rows, JSON export. *Check:* round-trip a transcript, sentence IDs resolve to exact source text.
- **Phase 1 — autopilot coder.** One `coder.prompt` (method_frame + role + task + context + schema layers). Walk windows of adjacent sentences/turns; MiniMax-M3 → `Code{label,definition,code_type,evidence_sentence_ids[],model_rationale}`; resolve evidence by ID (P1). *Check:* code a real transcript; every `evidence_sentence_ids` resolves; no regenerated text.
- **Phase 2 — conciliator (whole codebook in prompt).** Per new code, send the whole per-project codebook + `conciliator.prompt` → create/merge/minority/defer + rationale; append decision, never delete minority. Phase inferred from a new-codes-slowdown counter. *Check:* second transcript reuses codes from the first; minority preserved.
- **Phase 3 — one theme pass + export.** `theorist.prompt` → candidate themes + supporting/contradicting code IDs + central-concept memo. Full JSON export; theme → codes → sentence IDs → text resolves. *Check:* themes read as claims with counter-evidence, not bucket labels.
- **Phase 4 — the app.** Reading view (transcript + suggestions + evidence), codebook view, a memo box. Just enough to *read and decide*, machinery in an audit panel.
- **Phase 5 — co-pilot + comparison.** Same lenses on demand per passage; run a 2nd architecture/model and diff. (Variance envelope falls out of the `Run` rows.)

**Next step:** on approval I save this doc to `…/MASSHINE_Qual_LLM/DESIGN_REFLECTION.md`, then start Phase 0 — FastAPI + SQLite + spaCy sentence index — nothing more until it round-trips.


