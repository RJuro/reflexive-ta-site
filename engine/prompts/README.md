# Prompts — exactly what the LLM is asked to do

Every LLM call in the engine loads its instruction from a `.prompt` file here (P5:
prompts are data, versioned, hot-loaded — no inline strings). The model is **MiniMax-M3**
via the OpenAI-compatible API; **temperature is left at the model default** (we don't set
sampling params during dev). Each prompt is the **system** message; the **user** message
is the data described below. All calls must return **JSON only** (M3's `<think>…</think>`
block is stripped before parsing).

Two invariants hold across every prompt:
- **Index, never regenerate (P1):** the model cites IDs (sentence IDs, code IDs) — it
  never quotes or rewrites source text. The system resolves verbatim spans from the index.
- **Reflexive-TA frame:** semantic + latent codes, divergence preserved, themes are
  claims not buckets. (The frame is currently inlined in each prompt; swap to grounded
  theory later = edit these files, no code change.)

## The pipeline and its calls

```
import → structure.prompt (1 call/doc) → sentence index (spaCy, no LLM)
       → coder.prompt   (1 call/SECTION, run in PARALLEL, blind)
       → reconcile.prompt (1 call/doc: merge duplicate codes → doc codebook)
       → reconcile.prompt (1 call/project: merge across docs → project codebook)
       → theorist.prompt  (1 call: candidate themes)   ← next phase, drafted not yet wired
```

| File | Call site | User message (input) | Returns | Wired? |
|---|---|---|---|---|
| `structure.prompt` | `structure()` — once per document | the transcript with every line numbered (`0007| …`) | `{header_end_line, sections:[{gist,start_line,end_line}]}` — descriptive section gists, no interpretation | ✅ |
| `coder.prompt` | `code_document()` — once per **section**, **parallel** (concurrency 8), each call **blind** (no other sections, no codebook) | one section: `[S2.007] <sentence>` lines | `{codes:[{label,definition,code_type,evidence_sentence_ids,rationale}]}` | ✅ |
| `reconcile.prompt` | `reconcile()` — once per document, then once across documents | the codes so far as `[t12] (semantic) label — definition` (labels + definitions only, **no transcript**) | `{groups:[{keep, members:[t-ids]}]}` — **ID-only output** (no rewritten labels) so latency stays low; the `keep` code's label/definition carries, evidence unions | ✅ |
| `theorist.prompt` | theming pass — once over the project codebook | the codebook as `[C0007] (semantic) label — definition` (**no transcript**) | `{themes:[{central_concept,supporting_code_ids,contradicting_code_ids}]}` | ⬜ next |

## Why coding is blind + parallel, then reconciled

Section coding has **no dependency between sections**, so the calls run concurrently
(fast) instead of serially carrying a growing codebook (slow). Consistency is restored
*after* coding by `reconcile.prompt`, which sees only labels + definitions (a small
payload) and merges duplicates — first within a document, then across documents. Evidence
(doc-qualified sentence IDs) unions when codes merge; genuinely distinct codes stay
separate (divergence is data).

## Not drafted yet (deferred, KISS)

- **critic / conciliator-verdict** (create/merge/minority/defer with written rationale,
  phase-dependent reconciliation) — the richer conciliator from the design doc. The
  current `reconcile.prompt` is the lean stand-in (group-duplicates). Add when needed.
