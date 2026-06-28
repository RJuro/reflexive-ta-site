# Domain Packs

A **Domain Pack** holds everything study-specific (see `../DOMAIN_GENERALITY.md`). The engine is
corpus-agnostic; a pack supplies the unit vocabulary, structure examples, method frame, **standpoint
roster**, language, and reconcile examples. New domain = new pack, zero engine changes.

```
packs/
  _brief_template.md                  # GENERALIZATION (dormant) — parameterized research brief
  migration_oral_history/             # PACK #1 — the live pack
    reference.md                      # the deep-research result (grounded scholarship)
    standpoints/
      critical_political_economy.prompt
      phenomenological_memory.prompt
      ... (more distilled from reference.md as needed)
```

## Authoring flow (how a standpoint roster is created)

Three stages, run **once per study/domain** (not per transcript):

1. **Generate a research brief** — fill `_brief_template.md` with the domain, corpus, candidate
   standpoints, language. (This is the "research brief for any domain" generator.)
2. **Run it in your own deep-research tool, then review/edit** the result → a grounded
   `reference.md`. External / bring-your-own; the human owns and inspects the scholarship (this is
   the defensibility checkpoint).
3. **Distill `reference.md` → per-standpoint `.prompt` files** — each profile compressed to its
   operational core (stance → sensitizing concepts as *attentions* → analytic questions → the
   "where the lens goes quiet" guard → anti-caricature contrast), matching the coder output schema.

At **run time** (separate, repeatable): the researcher picks the standpoints + the standard coder →
blind parallel panel → friction analysis.

## Status — GENERALIZATION IS OFF FOR NOW

Stages 1–2 (brief generation, automated distillation) are **scaffolding, intentionally dormant**:
- `_brief_template.md` exists as the generator content but nothing runs it automatically.
- Distillation (stage 3) is currently done **by hand** from `reference.md`; an automated distiller
  is the "on" version, deferred.

The **active path** is the pre-built `migration_oral_history` pack: its `reference.md` is the
already-produced research result, and its `standpoints/*.prompt` were distilled from it by hand.
This keeps the engine focused and provider-agnostic (no research engine baked into the coding
engine) until we choose to turn the general path on.
