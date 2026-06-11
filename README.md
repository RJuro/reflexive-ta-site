# Reflexive TA Toolkit — Design Preview

A static, single-page educational site that walks a less-technical reader through a proposed LLM-driven reflexive thematic analysis toolkit. Built around Braun & Clarke's six phases, with provenance as a first-class deliverable.

> **Status:** v0 design preview · working title · not the production toolkit
>
> Funded by MASSHINE. All v0 design choices are auditable in natural language: every code, merge, and theme carries a written rationale and a verbatim span back to the source transcript.

## What's in the site

1. **The method** — Braun & Clarke's six phases, translated to LLM calls
2. **The pipeline** — one paragraph from Mary Grande's interview, walked through 7 numbered stages
3. **The locked decisions** — 10 v0 decisions, each with a spec clause, research evidence, and a transcript moment
4. **Three deep dives**:
   - **Worked example · DP-40 GRANDE** — childhood inside the agrarian household; wartime household as state-requisitioned space
   - **Worked example · EI-1257 HIRSCH** — kinship as the unit of survival; the family that is made by spelling and refusal
   - **Worked example · EI-338 BROZINSKAS** — late-life oral history as a three-person speech act
   - **Pipeline pressure test** — 11 nodes, click each to see what fails, what the audit catches, and what a human would do
   - **Competing systems comparison** — GATOS, CHALET, AbductivAI, Thematic-LM, TAMA, DeTAILS across 8 dimensions
5. **Audit & limits** — the four exit gates (G1–G4) and the two things the system deliberately does not do (no κ, no autonomous reflexivity)
6. **Reading list** — the 12 papers from the literature review that actually moved design choices

## Running the site

The site is fully static and self-contained. Two ways to run it:

**Option 1 — open the file directly** (works because content is inlined into `index.html`):

```sh
open index.html
```

**Option 2 — local server** (for development):

```sh
python3 -m http.server 8765
# then open http://localhost:8765/index.html
```

## Repo layout

```
.
├── index.html              # the entire site (content inlined as JSON)
├── styles.css              # design tokens + components
├── script.js               # theme toggle, scroll-reveal, data renderers
├── content/                # the source JSON (regenerated into index.html by tools/inline_content.py)
│   ├── pipeline-walkthrough.json
│   ├── pipeline.json
│   ├── decisions.json
│   ├── pressure-test.json
│   ├── systems-comparison.json
│   ├── reading-list.json
│   └── runs/
│       ├── manifest.json
│       ├── dp-40-grande.json
│       ├── ei-1257-hirsch.json
│       └── ei-338-brozinskas.json
├── tools/
│   ├── inline_content.py   # inlines content/*.json into index.html for file:// serving
│   └── verify_quotes.py    # checks every verbatim quote in the worked examples against the source
└── README.md
```

## Editing the site

1. Edit the JSON in `content/`.
2. Run `python3 tools/inline_content.py` to regenerate the inlined blocks in `index.html`.
3. Open `index.html` in a browser.

## Design language

Tokens ported from a parallel `redesign.html` design — `--bg: #f7f6f3` (cream) / `--accent: #2d6a4f` (forest green) / `Iowan Old Style` serif display font. Dark mode supported via `prefers-color-scheme` with a manual toggle.

## What's NOT in the production toolkit yet

This is a *design preview* of the v0 toolkit. The actual pipeline (LLM calls, codebook merge logic, audit runner) is not in this repo. The site describes what the toolkit will do, with worked examples of the artifacts it would produce, against real Ellis Island Oral History Project transcripts.

Source materials (the v0 spec, the literature review, the original transcripts) are kept private and are not in this repo.
