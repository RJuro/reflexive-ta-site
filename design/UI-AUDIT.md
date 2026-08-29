# UI audit — what's actually deployed vs. what we said we'd build

*2026-08-29, after the product owner's verdict: "it is like the legacy app mixed in with some
elements from the new one." That is exactly what it is, and this audit says why, point by point,
and what has to change.*

## The root cause

P10.3 and P10.4 were both briefed as **evolutions** of the P8-era app: "keep the existing
plumbing, replace the view layer." That was the wrong instruction, twice. The plumbing worth
keeping (auth, jobs, uploads, toasts) is maybe 400 lines. The view layer is ~2,900 lines built
around a different product — a codebook-curation tool — and each pass added new surfaces
*beside* the old ones rather than replacing them. `app.js` now routes **nine** views, five of
which belong to the deleted paradigm, and several new panels fall back to legacy renderers when
their endpoint is missing, so the same screen shows one product on some data and another product
on other data.

This is why nothing reads as a coherent instrument.

## Point by point

| # | What we said | What is deployed | Verdict |
|---|---|---|---|
| 1 | **Three surfaces** — Session · Journal · Text | Nine routed views: home, doc, session, journal, codebook, themes, friction, notes, overview | **Broken.** Legacy views still routed and reachable |
| 2 | **Journal is home** | `overview` is home and renders a project dashboard; Journal is a separate tab | Broken |
| 3 | **Codebook is a drawer** | Full codebook view still routed, with lens filters and chip walls | Broken |
| 4 | **No mode vocabulary** — one coder, lenses on demand | "Standard coding" / "panel" / "3 lenses, blind" in the sidebar, home subtitle, journey copy | Fixed this pass; verify no residue |
| 5 | **Text is a destination** opened at evidence | Text is a top-level nav item showing one arbitrary source; no sense of "why am I here" | **Broken.** Should be reached from a claim, not browsed |
| 6 | **Reading is legible** — "progress should feel like being read to" | A job chip with a stage name; no indication of what the system is doing to which document, or what will exist afterwards | **Broken** |
| 7 | **Upload is understandable** | Upload starts a job; the document appears in a list; nothing says what happened or what comes next | **Broken** |
| 8 | Session shows real synthesized steps | Falls back to a client-built pseudo-walkthrough derived from codes when `/session` is empty — two different products on the same screen | **Broken.** Fallbacks must be empty states, not a second product |
| 9 | Journal shows findings with standing/stance | Real when `/journal` responds; otherwise falls back to the legacy themes renderer | Same fallback problem |
| 10 | **One accent = researcher's voice** | Accent used for the primary button, active nav, links, and the researcher's voice | Diluted |
| 11 | No persona | Fixed | ✅ |
| 12 | Audio path visible and equal | Present and working | ✅ |
| 13 | Model selection visible, recorded | Present and working | ✅ |
| 14 | Evidence chips are doors | Present and working | ✅ |

## What has to happen

**Delete, don't add.** The next pass is a *replacement* of the view layer, not another evolution:

1. **Route exactly four surfaces**: Home (the project, with the analysis map and needs-judgment),
   Session, Journal, Text. Delete the routes for codebook, themes, friction, notes, overview and
   the legacy renderers behind them. The codebook becomes the drawer it was specced as; friction
   and notes fold into Journal; `overview` disappears into Home.
2. **No product-level fallbacks.** If `/session` has no synthesis, that is an *empty state* with
   one action ("Read this document" / "Synthesize"), never a different walkthrough built from
   codes. Same for Journal. A missing endpoint shows a quiet unavailable line — it does not
   summon the previous product.
3. **Make process legible.** Uploading, reading, and synthesizing each need a plain sentence
   saying what is happening, to which document, and what will exist when it finishes — and the
   document's row must carry its state in words ("not read yet" → "reading…" → "read · 21 codes"
   → "session ready").
4. **Text is opened, not browsed.** Reached from an evidence reference or explicitly from a
   document row; it always shows *which* claim brought you there and offers a way back.
5. **Reserve the accent.** The researcher's voice only. Primary actions become ink-filled; nav
   uses weight and ground, not colour.
6. **One state model.** A document is in exactly one state, computed centrally and rendered the
   same way everywhere: `ingesting → ingested → reading → read → synthesizing → session ready`.

## Scope note

This is a rewrite of `web/app.js`'s view layer (~2,900 lines → target well under half that),
keeping `api.js` and the shell plumbing. It is the fourth UI pass, and it is the one that
finally removes the previous product instead of layering over it.
