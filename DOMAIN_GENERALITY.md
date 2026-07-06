# MASSHINE as a domain-general instrument

> **The claim this document protects.** MASSHINE is a general computational *qualitative-analysis
> instrument*, not a migration tool. **Migration oral history is the first corpus, not the system.**
> Everything that makes the engine valuable — sentence-anchored coding, divergence-as-data, the
> standpoint panel, the structured-disagreement output — is domain-independent. This doc is the
> guardrail that keeps the migration grant's specifics from calcifying into the architecture.

It exists because the danger is subtle: we're proving the system on one corpus (immigration oral
histories), and domain nouns and examples quietly seep into prompts until "what is an interview"
is wired into the engine. The fix is not a big framework — it's a discipline: **keep the engine
corpus-agnostic and put everything domain-specific in a swappable Domain Pack.**

---

## What is already general (the spine — do not touch)

The load-bearing parts are corpus-agnostic today:

- **The pipeline:** `structure → sentence index → blind parallel coding → reconcile (within/across docs) → theme pass → standpoint panel → friction analysis`. Nothing in this shape assumes interviews. It applies to any corpus of text documents.
- **The data model:** `Sentence{id,doc_id,section_id,char offsets}` · `Code{label,definition,code_type,evidence_sentence_ids,rationale}` · `CandidateTheme{central_concept,supporting/contradicting code ids}`. These are about *text segments and analytic claims*, not migration. No `speaker`, no `migration`, no domain column.
- **The invariants:** index-never-regenerate / resolve-by-ID, stable monotonic code ids, evidence-grounding (drop ungrounded codes), merges-only reconcile, divergence preserved.
- **The differentiator:** the **standpoint panel + divergence-as-data** is a *general stance on computational qualitative analysis*. It is not migration-specific in any way — which makes it a methods contribution, not a feature of one study.

This is the encouraging part: generality is almost entirely a **prompt-templating** concern. The code doesn't need to change.

---

## Where domain leaks in today (honest audit)

From a grep of `prompts/` and `masshine.py`:

| Location | Migration/oral-history specific content | Verdict |
|---|---|---|
| `structure.prompt` | "oral-history interview transcripts"; examples "the journey, home life, work, arrival"; "skip the front-matter header" | **leak** — corpus noun + examples |
| `coder.prompt` | "coding ONE section of an **oral-history interview transcript**"; "idea units within **speaker turns**" | **leak** — corpus framing |
| `critic.prompt` | "one **interview** section" | minor leak |
| `reconcile.prompt` | worked examples are all migration ("Origin under shifting borders", "Migration as escape from gendered constraint", "American streets", "linguistic hierarchy") | **leak** — examples teach via migration |
| `theorist.prompt` | none (codebook → themes) | ✅ general |
| `masshine.py` | `spacy.blank("en")` (English); `transcripts_sample/` demo path | **config** — language + corpus location |
| schema / pipeline | section / sentence / code / theme; evidence-by-id | ✅ general |
| **method frame** | reflexive TA is assumed in the prompts' wording | **config** — should be a pack choice (RTA / GT / framework / content analysis / IPA) |
| **standpoint roster** (planned) | critical theory / feminist / phenomenological / … grounded in *migration* scholarship | **config** — the roster IS domain-specific by nature |

**Net:** the leaks are concentrated and cheap to fix. No domain assumption is baked into the schema or control flow.

---

## The fix: Engine + Domain Pack

Split the system in two:

- **Engine (corpus-agnostic):** the pipeline, data model, invariants, prompt *logic*, and the
  standpoint-panel *mechanism*. Prompts reference placeholders (`{unit}`, `{corpus_context}`,
  `{method_frame}`) — never "interview" or "migration".
- **Domain Pack (swappable config bundle):** everything that varies by study. One folder per
  domain, e.g. `packs/migration_oral_history/`.

A Domain Pack supplies, along these axes:

| Axis | What it sets | Migration pack value |
|---|---|---|
| **Corpus / unit vocabulary** | `{unit}` = "interview transcript" / "document" / "response" / "field note" | "oral-history interview transcript" |
| **Structure guidance + examples** | the section-segmentation hints for `structure.prompt` | "by phase — journey, home life, work, arrival" |
| **Method frame** | reflexive TA · grounded theory · framework analysis · qualitative content analysis · IPA | reflexive TA |
| **Standpoint roster** | the persona library the researcher picks from (DATA, not code) | critical / feminist / phenomenological / post-colonial / pragmatist |
| **Language + tokenizer** | spaCy model / sentence splitter | English |
| **Reconcile worked-examples** | domain-neutral *or* per-domain illustrative merges | migration examples |
| **Ingest quirks** | header skipping, speaker markers, redaction tags | skip metadata header |

The standpoint roster deserves emphasis: **it is domain-specific by its nature.** Critical-theory and feminist *migration* lenses are grounded in migration scholarship; a clinical study's lenses would be different traditions entirely (below). So the roster is the clearest example of a thing that must be a pack, never engine code.

---

## Proof it isn't migration-only

The same engine, same pipeline, same data model — only the Domain Pack changes:

| Domain | Corpus | Method frame | Example standpoint roster |
|---|---|---|---|
| **Health / illness research** | patient interviews, illness narratives | IPA or reflexive TA | biomedical · patient-centered/lived-illness · psychosocial · critical-health |
| **UX / product research** | user-interview & usability-session transcripts | thematic analysis | task/usability · emotional-experience · accessibility · business-value |
| **Open survey responses** | free-text answers | qualitative content analysis | unmet-need · sentiment · feature-request (lighter roster) |
| **Policy / media analysis** | speeches, documents, articles | critical discourse analysis | ideological · institutional · rhetorical lenses |
| **Organizational ethnography** | field notes, observation logs | grounded theory | structural · cultural · power/politics lenses |
| **Education / learning science** | student reflections, think-alouds | thematic analysis | cognitive · metacognitive · affective · sociocultural |

If any row above required editing engine code rather than writing a pack, the separation has failed.

---

## Design rules (the guardrail going forward)

1. **No domain noun in engine code or shared prompt *logic*.** Prompts use `{unit}` / `{corpus_context}`; the word "interview" lives only in a pack.
2. **The standpoint roster is data, not code** — a config file the pack supplies, the researcher picks from.
3. **Method frame is config.** RTA is carried *now*; switching to grounded theory or framework analysis is a pack swap, not a rewrite. (This also resolves the grant's RTA-vs-Charmaz-GT question as a config choice, not a fork.)
4. **Language/tokenizer is config.** English is the default pack value, not a hardcoded assumption.
5. **Worked examples in shared prompts are either domain-neutral or pulled from the pack** — examples teach behavior, so migration examples quietly migration-ize a "general" prompt.
6. **The generality test:** *adding a new domain = writing a new pack and touching zero engine code.* If a new domain forces an engine edit, that edit is a leak to fix.
7. **The migration study is just `packs/migration_oral_history/`** — first among equals, the proving ground, not the product.

---

## What stays migration-specific (and that's fine)

The grant, the corpus, the validation data, the *chosen* standpoint roster, and the September
benchmark are all migration. That's the **first pack** and the place the architecture earns its
keep. Domain-generality doesn't mean building six domains now — it means the migration work goes
*into a pack* so that the seventh domain is a config file, not a rewrite.

**Immediate, low-cost actions** (not a refactor sprint — just stop the bleeding):
- The deep-research brief already drafted produces the **migration standpoint pack** — recognize it as *one pack*, and structure its output as reusable roster data.
- When next editing the prompts, swap the hardcoded corpus nouns for `{unit}` / `{corpus_context}` placeholders and move the migration examples into the pack. Cheap, and it converts the current leaks into the first pack.
- Keep `theorist.prompt` as the template for "already general" — no domain words, pure codebook→themes.
