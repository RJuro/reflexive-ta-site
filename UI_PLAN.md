# UI Stabilization & UX Plan

*Written 2026-07-03, after the backend rework (see `REWORK_PLAN.md` / plan
`i-think-the-design-linear-pizza`). Status of that work: engine modularized
(`engine/masshine/`, 30 offline tests green), FastAPI backend with per-project
SQLite + jobs, v3 mockup wired to the API in `web/`.*

## Verdict

The plumbing is real — projects, uploads, jobs, coding, themes all flow through
the API — but the front end is still the static v3 mockup with live data poured
into it. The "UI is not really there" feeling comes from three distinct layers:

1. **Actual bugs** (the app behaves brokenly),
2. **Mockup residue** (prominent chrome that is dead or shows fake data),
3. **Missing workflow UX** (the real analyst loop has no first-class surface).

Everything below was verified live against the running app
(`http://127.0.0.1:8760`, demo project `P6aea6da5`) on 2026-07-03.

---

## Findings

### A. Bugs

| # | Finding | Where |
|---|---------|-------|
| A1 | **View switching half-broken.** `.view--reading`, `.view--themes`, `.view--connections` set `display:block` unconditionally, overriding `.view { display:none }`. Those three views are always rendered, stacked vertically — clicking "Codebook" in the nav appears to do nothing (the codebook renders below the fold). Only Codebook and Friction swap correctly (their display comes from `.view.is-active { display:grid }`, higher specificity). | `web/index.html` (mockup CSS) |
| A2 | **Reading-view document header is hardcoded.** "DP-40 GRANDE — Mary Yankovik Grande / Yugoslavia, 1920 · age 10 · …" is static mockup HTML; `app.js` never updates it. It coincidentally matches the demo doc but shows on *every* project — including empty ones — and would lie when viewing any other document. | `web/index.html`, `web/app.js` (`setTopbar` only touches the topbar) |
| A3 | **Status box partially fake.** "Phase: Open coding" and "Sentence index: sha-9f1c" are frozen mockup values; only the codes/themes counts are live (`setStatus` writes `rows[1]`/`rows[2]` only). | `web/app.js:299` |
| A4 | **Speaker parsing inconsistent.** Some sentences render the raw `GRANDE:` prefix inline instead of in the speaker column (observed: S1.005, S1.009 in the demo doc). Root cause not yet isolated — regex in `parseSpeaker` vs. actual sentence text needs a look. | `web/app.js:237` |
| A5 | **No responsive behavior.** Below ~1200px the four-column reading layout (nav / lenses / transcript / autopilot) crushes into unusable slivers. | `web/index.html` CSS |

### B. Mockup residue (dead or lying chrome)

| # | Finding | Where |
|---|---------|-------|
| B1 | **The entire "Lenses" panel is inert.** Second column of the reading view, five interactive-looking cards ("Suggest codes · standard", "Read · critical / political-economy", …) — no click handlers, since the live `/suggest` endpoint was deferred (Phase 5 of the rework plan). The most prominent panel in the app does nothing. | `web/index.html` |
| B2 | **Topbar search box is dead.** | `web/index.html` |
| B3 | **Audit drawer shows a hardcoded fake event log** ("structure() → 10 sections", "9 codes", "3 themes" — real demo project: 12 sections / 421 codes / 7 themes). No `/usage` endpoint exists yet to feed it. | `web/index.html`; `engine/masshine/api.py` |
| B4 | **Memo textareas silently lose text.** Code and theme memo fields accept input; nothing persists them (no memo column in schema, no endpoint). | `web/app.js`, `engine/masshine/db.py` |
| B5 | **"Human" filter chip filters nothing** (no human annotations exist). | `web/index.html` |
| B6 | **Tweaks gear (bottom-right) is a design-tool leftover.** | `web/index.html` |
| B7 | **Co-pilot pane is the autopilot data relabeled** — it shows already-persisted codes for the selected sentence, not live suggestions. Honest as far as it goes, but the "Co-pilot / Autopilot / Split" mode toggle implies a distinction that doesn't exist yet. | `web/app.js` (`selectSentence`, `COPILOT` adapter) |

### C. Missing workflow UX

| # | Finding |
|---|---------|
| C1 | **Project/document navigation lives in a modal.** No doc switcher in the main UI — with two documents in the demo project, nothing on screen indicates the second one exists. Switching docs = open Projects modal → click doc → modal closes. |
| C2 | **Jobs are invisible outside the modal.** A ~12-minute coding run has no global progress indicator and no completion notification. Errors show truncated to 50 chars inside the modal only. |
| C3 | **No run guardrails.** "Run coding · panel" is offered on pack-less projects; double-clicks can queue duplicate jobs; nothing sets the ~12-min expectation before launching a paid LLM run. |
| C4 | **Codebook is a wall.** 421 codes, flat list — no search, no filter (lens / type / document), no sort; evidence sentence IDs are plain text, not links back to the transcript. |
| C5 | **Themes view discards most of the engine's output.** `theme_v2` carries subthemes, anchor quotes, per-doc snapshots, tensions, coverage; the card shows one claim sentence + chips, and `falsified_if` is crammed into the memo textarea as pre-filled text. |
| C6 | **No onboarding path.** A fresh project shows the fake Grande header, an empty transcript note, and dead lens cards — instead of "Upload transcript → Run coding → Build themes". |

---

## Plan

### P0 — Stabilize (small; ~a dozen targeted edits to `web/`)

Goal: from "feels broken" to "small but honest". No new features.

- [ ] Fix view-switching CSS: scope the per-view display rules to `.is-active`
      (e.g. `.view--reading.is-active { display:block }`), so exactly one view
      shows at a time. (A1)
- [ ] Make the reading-view doc header dynamic from `DOC.title`/`DOC.subtitle`;
      show a neutral placeholder when no document is loaded. (A2)
- [ ] Status box: drive "Phase" from real state (uncoded / coded / themed) or
      drop the row; replace "Sentence index sha-9f1c" with the real sentence
      count or drop it. (A3)
- [ ] Fix or root-cause speaker parsing (log the unmatched sentences, adjust
      `parseSpeaker`). (A4)
- [ ] Remove or hide dead chrome until it works: search box, Human chip,
      tweaks gear, audit drawer (or feed the drawer real numbers from the
      project detail payload as an interim). (B2, B3, B5, B6)
- [ ] Hide the Lenses panel behind a feature flag OR collapse it to a short
      explainer of the coding modes until `/suggest` exists. (B1)
- [ ] Minimal responsive fallback: below ~1100px collapse lenses/autopilot
      rails into toggleable drawers. (A5)

**Acceptance:** click every nav item → exactly one view swaps in; open an empty
project → no fake data anywhere; nothing on screen is clickable-looking but dead.

### P1 — Make the workflow first-class (medium)

Goal: the analyst loop (upload → code → theme → read) is visible and driveable
without the modal.

- [ ] Replace the modal-only shell with a persistent left project/document
      sidebar (or a topbar doc switcher): project name, doc list with status
      (`ingested / coded / themed`), doc switching in one click. (C1)
- [ ] Global job chip in the topbar: live progress (stage + done/total from
      `job.progress`), spinner while running, toast + auto-refresh of views on
      completion, full error text on failure. Poll `active_jobs` on load so a
      refresh mid-run reattaches. (C2)
- [ ] Run guardrails: mode-aware buttons (panel only when the project has a
      pack), disabled while a job of that kind runs, "~12 min, calls the LLM"
      hint before launch. (C3)
- [ ] Guided empty state on fresh projects: three-step card
      (Upload → Run coding → Build themes) with the actual buttons inline. (C6)

**Acceptance:** run a full loop on a fresh project without ever opening a
modal; leave and reload mid-job and still see progress.

### P2 — Codebook usability

- [ ] Search box (label/definition substring) + filters: coder/lens, code
      type (semantic/latent), document. (C4)
- [ ] Clickable evidence: sentence ID → switch to reading view, scroll to and
      select that sentence. (C4)
- [ ] Sort options (evidence count, label, lens); consider virtualizing the
      list at 400+ codes.

### P3 — Themes detail

- [ ] Render the full `theme_v2` payload: subthemes, anchor quotes (linked to
      sentences), tension details, coverage, per-doc snapshots. (C5)
- [ ] `falsified_if` as its own labeled field, not memo placeholder. (C5)

### P4 — Live lenses + persistence extras

- [ ] `POST /projects/{pid}/suggest` (one section × one lens, live LLM call)
      and wire the Lenses panel to it — this makes B1/B7 real. (Rework-plan
      Phase 5 item.)
- [ ] Persist researcher memos: `memo` column (or notes table) on code +
      theme, PATCH endpoints, debounced save from the textareas. (B4)
- [ ] `GET /projects/{pid}/usage` from the LLM ledger; feed the audit drawer
      real per-label numbers. (B3)

### Non-goals (unchanged from the rework plan)

- Sub-sentence char-range highlighting (engine codes at sentence granularity).
- Human-annotation write-back from the selection toolbar.
- Framework migration — stays vanilla JS + fetch; the OKLCH design system in
  `web/index.html` is good and stays.

## Suggested order

**P0 + P1 in one pass** — that's where the "UI isn't there" feeling lives.
P2–P4 as a second iteration, each independently shippable.

---

# v4 — The quiet redesign (2026-07-06)

*Supersedes the P0/P1 patching approach above: instead of fixing the mockup shell,
rebuild it around the researcher's process. Visual concept:
`design/masshine-v4-quiet.html`.*

## The researcher's journey (what the UI is actually for)

1. **Upload** a transcript → machine structures + indexes it.
2. **First codes** — the machine pass (standard or 3-lens panel, ~12 min).
3. **Read & react** — the researcher reads the transcript *as text*, sees where
   codes landed, agrees/disagrees, leaves **notes**, renames or rejects codes.
4. **Themes** — the sequential walk builds the catalogue; researcher reviews,
   notes tensions, challenges claims.
5. **Revision loop** — the researcher's notes/corrections are **fed back to the
   model**: re-code a document with feedback, rebuild themes with feedback.
   This loop is the product. Reflexive TA *requires* the researcher's judgment;
   the software's job is to make that judgment cheap to give and impossible to lose.

## Design principles

- **Three regions, one focus.** Source-list sidebar (project / documents /
  codebook / themes / friction) · reading surface · on-demand inspector.
  No lenses panel, no autopilot rail, no mode switch, no audit drawer.
- **The transcript is a text, not a table.** Speaker turns, 60ch measure,
  16px/1.75 serif-adjacent reading. Coding presence = quiet dotted underline;
  lens colors appear only in the inspector and friction view.
- **One primary action.** The toolbar always shows the single next step:
  Upload → Run coding → Build themes → Re-code with feedback (n notes) →
  Rebuild themes. Journey state (✓ Uploaded · ✓ Coded · ⟳ Themes out of date)
  replaces the fake status box.
- **Feedback is first-class.** Notes attach to sentences, codes, themes; a
  margin dot marks them; the inspector holds the thread. Microcopy tells the
  truth: *"Notes ride along with the next re-code — every lens sees them."*

## The feedback loop (backend, already implemented — tests green)

- Schema v3: `comment` (target, doc, body, JSON context snapshot, status) and
  `revision` (rename / reject / restore per code) tables.
- `store.compile_guidance()` folds open comments + revisions into a plain-text
  block; `coding.py` and `themes.py` accept `guidance=` and append it to their
  prompts (grounding rules unchanged).
- `jobs.recode_work(pid, doc_id, mode)`: re-codes ONE document with guidance,
  invalidates stale theme steps (panel: from that doc's position; standard: all),
  sets a `themes_stale` flag, marks comments addressed.
- `jobs.theme_work(..., feedback=True)`: full re-walk with theme guidance.

## Status — BUILT (2026-07-06)

All of it is live (40 tests green, verified in the browser on the demo project):

- **API**: comments CRUD (add / edit / delete / dismiss), memos (upsert, empty
  body deletes), `POST /codes/{id}/revise` (rename / reject / restore),
  `POST /recode` (per-document, feedback-compiled), `feedback` flag on themes,
  journey fields on project detail. Schema v4 (`comment`, `revision`, `memo`
  tables; `document.kind`). Upload accepts .txt/.md + a source kind
  (transcript / field notes / focus group / document / other) — "general tool".
- **Frontend** (`web/`, full rewrite; v3 snapshot in
  `design/web-v3-wired-snapshot/`): three regions (sidebar · reading ·
  inspector), turn-based typographic transcript, codebook panel with
  search/lens/type/rejected filters + rename/reject + memos + notes, themes
  panel with the full payload (coverage, provenance, subthemes, anchors,
  tensions, falsified-if) + memos + notes, friction with per-source switcher,
  guided empty state, single journey-driven primary action, global job chip
  with toasts.
- **Memos vs notes**: memos are the researcher's private analytic writing
  (never sent to the model); notes + rename/reject compile into the guidance
  block on re-runs. Verified: UI actions → `store.compile_guidance()` output.

Deferred: live per-sentence lens suggestions (`/suggest`), non-plain-text
ingestion (PDF/docx), memo export into the markdown artifacts.
