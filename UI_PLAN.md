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

---

# v5 — Make it shine (UX audit, 2026-07-06)

*A fresh-eyes pass over the deployed v4, prompted by coauthor-sharing. Verified findings
first, then the plan. The north star: a coauthor must trust and enjoy the analysis within
five minutes (orientation, titles, legible references), and the researcher's loop must stay
frictionless over weeks (lifecycle, provenance, export).*

## Verified findings

| # | Finding | Evidence |
|---|---------|----------|
| F1 | **Mojibake at ingestion.** `ingest.py:78` reads uploads as UTF-8 with `errors="replace"`; Windows-1252 files (curly apostrophe = 0x92) become `Let�s`. Two of ten sample transcripts are cp1252 (`DP-5 JOHNSON` 297 bad chars, `NPS-101 KEMPF` 45) — both decode 100% clean as cp1252. The damage bakes into `document.text`, the sentence index, and every prompt. `seed.py:29` has the same pattern. |
| F2 | **No way home (visible).** The only route back to Projects is clicking the project name with a tiny ▾ — an invisible affordance. The user didn't find it: that's a failed control, not a missing one. |
| F3 | **No project lifecycle.** Zero endpoints for rename / archive / delete on projects; same for documents. The registry accumulates test projects forever ("test", "Live ingest test", smoke tests…). |
| F4 | **Raw filenames as titles.** "DP-40 GRANDE, M" is the title everywhere — sidebar, header, project cards. The structure() call already reads the whole transcript at ingest and returns only sections; it could return a display title + abstract in the same call for ~0 extra cost. |
| F5 | **Sentence references are illegible.** Theme anchors render as bare `S5.029` chips — no quote preview, no source name (hover shows only the doc id). With 2+ sources, `S1.019` is genuinely ambiguous (both docs have one). After clicking an anchor you land in the doc with no highlight-flash and no way back to the theme you came from. |
| F6 | **Section gists are computed and never shown.** The LLM writes a one-line descriptive gist per section at ingest; the v4 frontend never renders them (`grep gist web/app.js` → nothing). A free navigation + summary asset, unused. |
| F7 | **Coauthors have no identity.** One shared PIN; comments and memos carry no author, no timestamps in the UI. Two coauthors' notes are indistinguishable — and both can silently trigger paid runs. |
| F8 | **No export.** `render_md.py` produces the full markdown report offline; the UI offers no download. Coauthors screenshot instead of citing. |
| F9 | **Job history invisible.** Only the live chip + toast exist; the registry's full job history (incl. failures, durations) has an endpoint but no UI. |

## The plan

### P1 — Trust & data quality *(do first; small)*
1. **Encoding-aware ingestion (F1).** ✅ SHIPPED (2026-07-06): `ingest.read_source()` reads
   bytes → strict UTF-8 → cp1252 fallback → last-resort utf-8/replace, then NFC-normalizes.
   Wired into `ingest.ingest()` (replacing the old `errors="replace"` read) and `seed._rebuild_doc`.
   Verified end-to-end with the two known cp1252 sample files (`DP-5 JOHNSON`, `NPS-101 KEMPF`)
   plus new unit tests. Skipped: the "data-quality warning" surfaced on the document and the
   re-read repair path for already-ingested docs — not requested by the P1/P2 spec that drove
   this pass; flagging as a follow-up if genuinely-corrupted docs turn out to exist in the wild.
2. **LLM front-matter at ingest (F4).** ✅ SHIPPED (2026-07-06): `structure.prompt` now returns
   `{title, summary, sections}`; `ingest.structure()` tolerates both shapes (defaults to
   `None`/`None`). Schema v5: `document.title`, `document.summary` (nullable). UI: sidebar rows,
   doc header, and a quiet abstract paragraph under the header meta line — all with graceful
   NULL fallback to the cleaned filename (verified pixel-for-pixel unchanged on the real demo
   project, which predates this migration). Skipped: an inline title-edit affordance in the doc
   header itself — the PATCH endpoint (P2.6) exists and is human-override-capable, but the UI
   entry point for editing a doc's title today is the sidebar ⋯ → Rename, not an in-header edit.
3. **Render section gists (F6).** ✅ SHIPPED (2026-07-06): quiet section headers (small caps,
   hairline rule) inserted into the transcript wherever a new section starts, plus a collapsible
   "On this page" TOC under the doc summary that jump-scrolls to a section's first sentence.

### P2 — Orientation & lifecycle *(the "it behaves like an app" tier)*
4. **Explicit Home (F2).** ✅ SHIPPED (2026-07-06): toolbar left is now a real breadcrumb
   (`Projects / ‹project name› / ‹view or doc title›`), both non-current segments clickable.
   Skipped: the Esc-closes-inspector → second-Esc-goes-home keyboard affordance — not
   implemented in this pass (mouse navigation via the breadcrumb covers F2's core complaint).
5. **Project + document lifecycle (F3).** ✅ SHIPPED (2026-07-06): registry gains an `archived`
   column (guarded ALTER); `rename_project`/`set_archived`/`delete_project`/`list_projects
   (include_archived=)`; `PATCH`/`DELETE /projects/{pid}`, list gains `?archived=1`. Documents:
   `PATCH`/`DELETE /projects/{pid}/documents/{doc_id}` with the full cascade (codes originated
   there deleted, cross-doc evidence stripped, comments/memos targeting the doc removed, both
   mode checkpoints popped, all theme_steps cleared, themes flagged stale). Frontend: project
   cards get quiet hover actions (rename inline, archive/unarchive, delete-with-type-to-confirm)
   plus a "Show archived" toggle; sidebar source rows get a ⋯ overflow (rename inline / delete
   with an explicit "codes removed, themes must be rebuilt" warning).
6. **Legible references (F5).** ✅ SHIPPED (2026-07-06): sid chips (theme anchors, evidence rows,
   friction chips) show a `Source · S1.019` prefix once the project has >1 document; hovering
   any chip shows a quiet tooltip with the verbatim sentence (async-filled via `ensureDocsLoaded`
   if not yet cached) and source title; jump arrival gets a `.s--flash` fade; `switchView`/
   `openDocView` push history state and a `popstate` handler restores view/doc/selection so
   browser-back returns you from a jump to where you came from.

### P3 — The coauthor tier
7. **Identity-lite (F7).** ✅ SHIPPED (2026-07-06): schema v6 adds `comment.author`/`memo.author`
   (nullable). `store.add_comment`/`set_memo` accept `author=`; `POST /comments` and
   `PUT /memos` carry an optional `author` field. Frontend: first note/memo write opens a
   one-input sheet ("How should your notes be signed?"), stored in `localStorage
   masshine_author` and stamped on every subsequent write (`ensureAuthor()`). Notes render
   "RJ · 2h ago" (relative-time helper `timeAgo`, author before status); memos show
   "edited by RJ · 2h ago" subtly beneath the textarea when an author is present. No accounts.
8. **Viewer vs editor.** ✅ SHIPPED (2026-07-06): optional `MASSHINE_VIEW_PIN` in `auth.py` —
   a password matching it (and not the editor `MASSHINE_PIN`) resolves role "viewer": GET/HEAD
   pass, everything else 403s with `{"detail": "view-only access"}`; the editor PIN still
   resolves "editor" (full access); no `MASSHINE_PIN` at all means no auth, role "editor".
   `GET /me` returns `{"role": ...}` computed from the same logic (reachable under either
   role; editor when no PIN is configured). Frontend fetches `/me` at init into `S.role` and
   gates every mutating affordance behind `isViewer()`: primary action button, Add source,
   sidebar doc overflow (⋯), code rename/reject, project rename/archive/delete, the home
   create-project form; notes show a "Notes are read-only in view mode" hint instead of the
   compose box, memo textareas render `readonly` with muted styling. All reads render normally.
9. **Notes review queue.** ✅ SHIPPED (2026-07-06): new sidebar "Notes" item (Project group,
   open-count badge) and view `notes` — every comment grouped by target type (Sentences /
   Codes / Themes / Sources), each row showing status, author + relative time, body, a context
   quote/label snippet, and a "Jump" button (sentence → `openDocView`; code → `openCodeView`;
   theme → themes view + select; document → `openDocView`). Filter segmented control All /
   Open / Addressed / Dismissed. Row actions reuse the existing edit/dismiss/delete handlers.
   Frontend-only, built on the existing comments API.
10. **Export (F8).** ✅ SHIPPED (2026-07-06): `GET /projects/{pid}/export` (self-contained
    JSON — codes with resolved quotes + revisions applied, full themes, memos, comments),
    `/export/codes.csv` and `/export/themes.csv` (flat, spreadsheet-ready), plus a quiet
    toolbar "Export" button opening a four-option sheet (see below for the fourth). The
    Markdown report is now also shipped: `GET /projects/{pid}/export/report.md` — a narrative
    report built fresh from the DB (`store.report_md`, not `render_md.py`'s checkpoint-shaped
    renderers): title block (name, pack, generated date, sources with titles/summaries +
    sentence counts), themes (claim, coverage, scope, provenance, subthemes, up to 5 anchor
    quotes resolved verbatim, tensions as code labels, falsified_if, researcher memo), a
    codebook appendix grouped by lens (label with researcher override winning, type,
    definition, evidence count + one exemplar quote, researcher memo; rejected codes in a
    struck-through list at the end), and an open-notes appendix. Same `Content-Disposition`
    attachment pattern as the sibling export endpoints. Toolbar sheet: "Report · Markdown —
    narrative report for reading/appendix".

### P4 — Provenance as the hero *(the differentiator)*
11. **Feedback diff after re-code.** ✅ SHIPPED (2026-07-06): `store.doc_code_labels` snapshots
    a document's `[(coder, label)]` before `jobs.recode_work` pops it and again after
    persisting; `store.diff_code_labels` set-diffs the two snapshots (label-based, since ids
    churn on recode) into `{new, new_more_n, dropped, dropped_more_n, kept_n}` (capped at 20
    per list). The job result carries `diff` and `notes_applied`. Frontend: when a `recode`
    job completes, `watchJob` toasts "Re-code done — N new codes, M dropped" and opens a quiet
    two-column details sheet (new / dropped labels, lens-dotted, capped-list overflow counts,
    "your N notes rode along") — the feedback loop's payoff, made visible.
12. **Analysis dashboard.** ✅ SHIPPED (2026-07-06): new view `overview` (sidebar "Overview",
    top of the Project group) — now the default landing view when a project with documents
    loads (a doc explicitly chosen via URL/history still opens straight to doc view). Cards:
    codes per lens (lens-dotted counts), sentence coverage per source (coded/total + bar),
    themes count with a stale-rebuild shortcut, open notes count linking to the Notes view,
    and "Recent activity" — last 8 jobs from `GET /projects/{pid}/jobs` (kind, status,
    relative time, duration when timestamps allow, error tail on failure). Quiet cards, no
    charts/libraries. (Per-source friction counts were skipped — panel friction is already
    one click away via the Friction view, and computing it per-doc here would mean N extra
    requests on every dashboard load for marginal value.)

### P5 — Reading comfort *(polish)*
13. ✅ SHIPPED (2026-07-06): in-document search (`/` focuses it) filters client-side, highlights
    matching sentences (quiet amber `.s--match`), shows "n of m", Enter/Shift+Enter and the
    ↑/↓ buttons cycle matches reusing the existing `.s--flash` jump animation. `j`/`k` move the
    sentence selection up/down and scroll it into view, active only when no input/textarea is
    focused. Sentence id revealed in a fixed-position left-margin gutter on hover (and for the
    current selection) via `getBoundingClientRect` positioning — never reflows the text column.
14. ✅ SHIPPED (2026-07-06): coding-density minimap — a slim (6px) fixed strip at the right edge
    of the transcript, one `<div>` cell per sentence (sampled evenly above an 800-cell cap),
    tinted by active-code count (transparent at 0, increasing accent opacity above that), a
    viewport band that tracks scroll position, click-to-jump via `openDocView`. Pure CSS/JS
    flex column, no canvas.

**Sequencing:** P1 is a half-day and removes the two things that actively erode trust
(mojibake, wrong titles). P2 makes it feel like a real app. P3 is what coauthor-sharing
actually needs. P4 is the demo-day differentiator. Ship P1+P2 together, then P3.

---

# P6 — Codebook consolidation (external review, 2026-07-06)

*Prompted by a reviewer's assessment of a real standard-mode run (Johnson + Brozinskas,
two ~1-hour transcripts → 234 active codes, 1,229 evidence links, 11 themes). Verdict:
"substantively sane, impressively grounded, but overcoded and too eager to theorize —
good raw material, not yet a clean qualitative coding scheme." This confirms the
granularity problem first measured on the demo panel (164/112/145). The design response:
keep the machine-exhaustive base layer (coverage, provenance, and falsifiability depend
on dense grounding) and add the missing curation layer on top — consolidation, not
lobotomy.*

## Diagnosis (why 234 codes happen)

1. **Blind per-section parallel coding** — the coder can't see it is re-proposing
   near-variants of concepts it already produced for other sections.
2. **Reconcile merges restatements, not siblings** — by design (guardrail against
   theme-as-bucket). "Opportunity pull" / "career stagnation push" / "extended
   deliberation" share a *family*, not an identity, so nothing collapses them.
3. **No stage in the pipeline owns consolidation.** That is the architectural gap.
4. Reviewer's secondary points, all confirmed: latent codes occasionally overreach
   without stated warrant (note: the run was standard mode — the standpoint panel is
   the designed discipline for exactly this); evidence presentation mixes exemplar and
   exhaustive (one code with 42 excerpts, 17 singletons); cross-doc merged codes carry a
   single `origin_doc_id` with no scope marker; one theme rests on a single code
   ("may be a memo, not a theme"); mojibake in the export (cp1252 — fix shipped in P1,
   but projects ingested before it carry the damage in stored text).

## Plan

1. ✅ SHIPPED (2026-07-06): **Consolidation pass → code families** — `consolidate.prompt` + `consolidate.py` (one LLM call, Python-validated: invented/rejected ids dropped, first-claim wins, Unfiled catch-all), schema v7 (`code_family` + `code.family_id`), `POST /consolidate` job with family-note guidance, staleness on codes rewrite, family grouping UI + inspector pane + exports.
   2026-07-06 (later): consolidation is now hierarchical — per-source families first (one call per source), then one aggregation into the project 8–15; single-source/small projects keep the one-call path.
   New job kind `consolidate`: ONE LLM call over the codebook proposes 8–15 families —
   `{label, definition, member_code_ids}` — with grounding rules (members must be real
   code ids; Python validates and drops inventions; unassigned codes go to an explicit
   "unfiled" family, never silently lost). Schema: `code_family` table +
   `code.family_id`. Codebook UI groups by family, collapsed by default (the 35–50-unit
   view a human can hold), fine codes + evidence intact underneath. Families are
   researcher-correctable (rename/reject) and re-runs of `consolidate` respect open
   feedback via the existing guidance compiler. Exports gain a `family` column.
2. **Merge as a researcher correction.** Extend the revision machinery
   (rename/reject/restore) with `merge` (code A absorbed into B: evidence unions at
   read time, guidance tells the model "the researcher merged X into Y" on re-runs).
   UI: multi-select in the codebook → "Merge into…". A later `split` is the inverse
   (needs per-evidence reassignment — defer until asked for).
3. **Scope marker.** Derive `scope: doc-local | cross-case` per code in Python from its
   evidence doc-spread; chip in the codebook/inspector + export column. Makes the
   reviewer's "conceptually messy" cross-doc merges visible so the researcher can
   comment/split them instead of discovering them in an export.
4. **Coder restraint (the long-deferred prompt edit).** `coder.prompt`: per-section code
   budget ("typically 3–8; only codes that would matter for a cross-interview
   codebook"), and latent codes must state an explicit *interpretive warrant* (what in
   the text licenses the inference) — thin-warrant latents are dropped at parse time.
   Then re-run the Johnson/Brozinskas pair and measure the delta (target: meaningfully
   under ~120 base codes for two 1-hour transcripts without losing the reviewer's
   "strongest codes" list).
5. **Evidence presentation.** Store everything (falsifiability needs it), *show*
   exemplars: 3–5 quotes + "N more" in inspector and exports (JSON keeps the full list;
   CSV gains an `exemplar_quotes` column while keeping full ids).
6. **Thin-support flags on themes.** Python-only: a theme whose support is a single
   code (or a single document when N>1) gets a quiet "thin support — consider a memo"
   chip. The reviewer's "posthumous completion" case, automated.
7. **Re-ingest repair.** For projects ingested before the encoding fix: re-read the
   stored upload with the new reader, rebuild the sentence index, and invalidate
   codes/themes with the standard staleness machinery (or simply document re-upload as
   the path if the surgery proves risky).

**Sequencing:** 1–3 are one coherent build (schema + job + UI + corrections); 4 needs a
paid re-run to validate; 5–6 are small and can ride along with any of it; 7 whenever a
damaged project actually matters (Johnson/Brozinskas does).

### P6 addendum — semantic color palette for code families (2026-07-06) — ✅ SHIPPED: hue = round(360·position/n) from the model's semantic-ring order, stored per family; muted oklch(60% 0.08 h) dots/tints in codebook + inspector only; transcript untouched

Once codes are consolidated into families (item 1), color them hierarchically:

- **Hue = family, assigned around the OKLCH wheel** at constant low chroma (~0.08) and
  fixed lightness (~60%) so every family color is a muted sibling of the existing
  palette — distinct but never loud. OKLCH's perceptual uniformity makes equal hue
  steps look balanced for free.
- **Semantic closeness → hue proximity.** The consolidation LLM call additionally
  returns the families in a "semantic ring" order (adjacent = most related; same call,
  no extra cost); hue is assigned by ring position, so related families sit next to
  each other on the wheel. Member codes inherit the family hue with small
  lightness/chroma steps (shades of one color = one conceptual neighborhood).
- **Where color appears (and where it must not):** family tint on codebook rows /
  family headers, code chips in the inspector, and the density minimap cells. The
  transcript reading surface stays neutral (dotted underlines as today) — color must
  not undo the quiet.
- **Collision rule:** lens identity keeps its small dot glyph; family identity is the
  hue. Two different dimensions, two different visual channels — never both as color.
- Hue stored on the family row at consolidation time (deterministic, stable across
  sessions, consistent in exports).

---

# P7 — Hierarchy discipline: codes ≠ families ≠ themes (reviewer round 2, 2026-07-06)

*Second external review (77 codes / 14 families / 5 themes on one interview — the coder
restraint is working; the granularity fight moved up a level). Diagnosis: families risk
becoming boxes that (a) hide overcoding by legitimizing weak codes, (b) blur into
proto-themes, and (c) have no visible connection to the themes layer. "The system now
needs a stronger hierarchy discipline: codes are reusable phenomena, families are clean
organizational clusters, themes are concise interpretive claims — with explicit checks
that each level is not repeating or rescuing the others."*

## Plan

1. **Hierarchy-discipline prompt block** (consolidate_source / consolidate_aggregate /
   theorist): families must not rescue weak codes; family labels are descriptive
   mid-level clusters, never theoretical claims (claims belong to themes); minimum ~3
   codes per family unless analytically central; target 6–10 robust families per source;
   a within-family redundancy sweep in the consolidation self-review (merge same-claim
   codes / flag subcode candidates); single-case themes use hedged language ("in this
   account", never universals).
2. **Family rationale — the "why".** The consolidation returns a one-sentence rationale
   per family ("why these codes belong together"); stored (`code_family.rationale`,
   schema v8) and rendered in the family header/inspector. A box becomes an argument.
3. **Theme label / claim split.** Theorist returns a short scannable `label` (≤6 words,
   e.g. "Kinship-mediated underpayment") alongside the one-sentence `central_concept`;
   label rides in theme_v2 payload; UI cards title on the label; exports updated.
4. **Theme ↔ family cross-links, Python-derived (NOT model-emitted).** Family ids churn
   on re-consolidation, so supporting_family_ids must be derived at read time from
   supporting codes → their families → counts. UI: theme cards show family chips in the
   family colors; family header/inspector shows "feeds themes: T01, T03". This is the
   connective tissue that makes the palette meaningful.
5. **Evidence-spread flag (computable, not promptable).** A theme whose supporting
   evidence concentrates in one section gets a quiet "narrow evidence base" chip —
   derived from sentence ids in Python, mirroring the thin-support flag.

Sequencing: implement immediately after the hierarchical per-source consolidation lands
(same files). Items 1–2 ride on the new prompts; 3 touches theorist + themes UI; 4–5 are
pure Python/UI.
