/* MASSHINE v4 — the quiet workbench.
   Three regions: source-list sidebar · reading surface · on-demand inspector.
   The researcher's journey drives the single primary action (upload → code → themes → revise);
   comments and corrections are first-class and ride into the model's prompts on re-runs. */
(function () {
  'use strict';
  const API = window.MASSHINE_API;

  // ---- state -----------------------------------------------------------------------------------
  const S = {
    pid: null, detail: null, mode: 'standard', packs: [],
    view: 'home',              // 'home' | 'doc' | 'codebook' | 'themes' | 'friction' | 'notes' | 'overview'
    docId: null,
    doc: null,                 // {title, meta, turns:[{speaker, sentences:[{id,text}]}]}
    sentText: {},              // docId → {sid → text} (for evidence quotes)
    codes: [], themes: null, friction: null, comments: [], memos: {},
    sel: null,                 // {type:'sentence'|'code'|'theme', id}
    jobs: {},                  // jobId → job row (being watched)
    cb: { q: '', lens: '', type: '', rejected: false },
    families: { families: [], stale: false }, cbGroup: null, famOpen: new Set(),
    notesFilter: 'open',        // notes view (P3.9): 'all' | 'open' | 'addressed' | 'dismissed'
    renaming: false,
    showArchived: false,        // home view: include archived projects
    renamingDoc: null,          // doc_id currently showing an inline rename input (sidebar)
    role: 'editor',             // 'editor' | 'viewer' (P3.8) — resolved from GET /me at init
    author: localStorage.getItem('masshine_author') || null,  // identity-lite (P3.7)
  };
  const isViewer = () => S.role === 'viewer';

  // ---- helpers ---------------------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  function toast(msg, isErr) {
    const el = document.createElement('div');
    el.className = 'toast' + (isErr ? ' toast--err' : '');
    el.textContent = msg;
    $('toasts').appendChild(el);
    setTimeout(() => el.remove(), isErr ? 9000 : 4500);
  }

  const LENS_FALLBACK = ['oklch(56% 0.115 32)', 'oklch(50% 0.09 255)', 'oklch(52% 0.08 150)', 'oklch(60% 0.1 90)'];
  function lensColor(name) {
    const known = { standard: 'var(--lens-standard)', critical: 'var(--lens-critical)', phenomenological: 'var(--lens-phenomenological)' };
    if (known[name]) return known[name];
    const lenses = lensList();
    return LENS_FALLBACK[Math.max(0, lenses.indexOf(name)) % LENS_FALLBACK.length];
  }
  function lensList() {
    const seen = [];
    for (const c of S.codes) if (!seen.includes(c.coder)) seen.push(c.coder);
    const order = ['standard'];
    for (const l of seen) if (l !== 'standard') order.push(l);
    return seen.length ? order.filter(l => seen.includes(l)) : [];
  }
  const label = c => c.researcher_label || c.label;
  const famColor = (hue, a) => a != null ? `oklch(60% 0.08 ${hue} / ${a})` : `oklch(60% 0.08 ${hue})`;
  const famById = fid => (S.families?.families || []).find(f => f.id === fid);
  const KINDS = { transcript: 'Interview transcript', fieldnotes: 'Field notes', focusgroup: 'Focus group', document: 'Document', other: 'Other source' };
  const cleanName = n => (n || '').replace(/\.(txt|md)$/i, '');
  // Display title: LLM/human title wins; falls back to the cleaned filename (today's behavior) —
  // NULL title on old/demo documents must render identically to before this feature existed.
  const docTitle = d => (d?.title || '').trim() || cleanName(d?.filename || d?.doc_id || '');
  // First meaningful word of a title/filename, for multi-source sid-chip prefixes (F5/P2.7).
  function sourceShort(d) {
    const t = docTitle(d);
    const w = t.split(/[\s—–\-:,·]+/).find(x => x.length > 1);
    return w || t.slice(0, 12) || d?.doc_id || '';
  }
  // Chip label for a doc-qualified sentence reference: bare "S5.029" in a single-source project,
  // "Grande · S5.029" once the project has 2+ documents (F5) — ambiguity only appears then.
  function sidChipLabel(docId, sid) {
    const docs = S.detail?.documents || [];
    if (docs.length <= 1) return sid;
    const d = docById(docId);
    return d ? `${sourceShort(d)} · ${sid}` : sid;
  }

  // Relative time for note/memo timestamps (F7/P3.7) — coarse buckets, no library.
  function timeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 45) return 'just now';
    const m = s / 60; if (m < 60) return `${Math.round(m)}m ago`;
    const h = m / 60; if (h < 24) return `${Math.round(h)}h ago`;
    const d = h / 24; if (d < 30) return `${Math.round(d)}d ago`;
    const mo = d / 30; if (mo < 12) return `${Math.round(mo)}mo ago`;
    return `${Math.round(mo / 12)}y ago`;
  }

  // ---- identity-lite (P3.7): ask once for a display name, stamp it on notes/memos ---------------
  function ensureAuthor() {
    return new Promise(resolve => {
      if (S.author) return resolve(S.author);
      const root = $('sheet-root');
      root.innerHTML = `
        <div class="sheet-wrap" id="sheet-bg">
          <div class="sheet">
            <h2>How should your notes be signed?</h2>
            <p class="hint" style="margin-bottom:10px">Shown next to your notes and memos so coauthors can tell whose judgment they're reading. Stored on this device only.</p>
            <input id="author-input" type="text" placeholder="e.g. RJ" autocomplete="off">
            <div class="sheet__foot">
              <button class="btn-quiet" id="author-save">Save</button>
            </div>
          </div>
        </div>`;
      const close = () => { root.innerHTML = ''; };
      const input = $('author-input');
      input.focus();
      const save = () => {
        const v = input.value.trim();
        if (!v) return;
        S.author = v;
        localStorage.setItem('masshine_author', v);
        close();
        resolve(v);
      };
      $('author-save').addEventListener('click', save);
      input.addEventListener('keydown', e => { if (e.key === 'Enter') save(); });
    });
  }

  // ---- derived ---------------------------------------------------------------------------------
  const codeById = id => S.codes.find(c => c.id === id);
  function codesForSentence(sid, docId) {
    const key = `${docId}#${sid}`;
    return S.codes.filter(c => c.evidence.includes(key));
  }
  function commentsFor(type, id) {
    return S.comments.filter(c => c.target_type === type && c.target_id === id);
  }
  function openNoteSids(docId) {
    return new Set(S.comments.filter(c => c.status === 'open' && c.doc_id === docId &&
                                     c.target_type === 'sentence').map(c => c.target_id));
  }
  function openCount(docId) {
    return S.comments.filter(c => c.status === 'open' &&
      (docId ? c.doc_id === docId : true)).length;
  }
  const openThemeNotes = () => S.comments.filter(c => c.status === 'open' && c.target_type === 'theme').length;
  // How many coded sources aren't reflected in the current theme walk yet (P4.x: coding a new
  // doc into an already-themed project sets themes_stale but doesn't clear theme_steps — the
  // gap is exactly the coded docs minus the ones theme_work has replayed/walked so far).
  const nNewThemeSources = () => {
    const coded = (S.detail?.documents || []).filter(d => (d.status || '').startsWith('coded')).length;
    const themed = S.detail?.n_themed_docs ?? 0;
    return Math.max(0, coded - themed);
  };
  const doc = () => (S.detail?.documents || []).find(d => d.doc_id === S.docId);
  const docById = id => (S.detail?.documents || []).find(d => d.doc_id === id);
  const anyCoded = () => (S.detail?.documents || []).some(d => (d.status || '').startsWith('coded'));
  const anyUncoded = () => (S.detail?.documents || []).some(d => !(d.status || '').startsWith('coded'));
  const running = () => Object.values(S.jobs).find(j => j.status === 'running' || j.status === 'queued');

  // ---- data loading ----------------------------------------------------------------------------
  async function loadProject(pid, keepView, initial) {
    S.pid = pid;
    const detail = await API.project(pid);
    S.detail = detail;
    S.mode = detail.mode || (detail.project.pack_id ? 'panel' : 'standard');
    const [codes, themes, comments, memos, families] = await Promise.all([
      API.codes(pid).catch(() => []),
      API.themes(pid, S.mode).catch(() => ({ themes: [], snapshots: [], stale: false })),
      API.comments(pid).catch(() => []),
      API.memos(pid).catch(() => []),
      API.families(pid).catch(() => ({ families: [], stale: false })),
    ]);
    S.codes = codes; S.themes = themes; S.comments = comments; S.families = families;
    S.memos = {};
    for (const m of memos) S.memos[`${m.target_type}:${m.target_id}`] = m;
    if (!S.docId || !detail.documents.find(d => d.doc_id === S.docId))
      S.docId = detail.documents[0]?.doc_id || null;
    if (!keepView) {
      // Overview is the default landing view for a project with documents (P4.12); a doc chosen
      // explicitly via the URL/history (initial.doc) keeps doc view as the entry point instead.
      S.view = (initial?.view) ? initial.view
        : (initial?.doc) ? 'doc'
        : detail.documents.length ? 'overview' : 'doc';
      if (initial?.doc) S.docId = initial.doc;
    }
    if (S.docId) await loadDoc(S.docId);
    for (const j of detail.active_jobs || []) watchJob(j.id, j.kind, true);
    const qs = new URLSearchParams({ project: pid });
    if (S.view !== 'doc') qs.set('view', S.view);
    if (S.view === 'doc' && S.docId) qs.set('doc', S.docId);
    history.replaceState({ view: S.view, docId: S.docId, sel: S.sel }, '', `?${qs.toString()}`);
    render();
  }

  async function loadDoc(docId) {
    const rd = await API.document(S.pid, docId);
    const sents = []; const map = {};
    let lastSpeaker = '';
    for (const sec of rd.sections) for (const s of sec.sentences) {
      const raw = s.text.trim();
      map[s.id] = raw;
      const m = raw.match(/^([A-Z][A-Z0-9 .,'’\-]{0,28}):\s*(.+)$/s);
      let speaker = lastSpeaker, text = raw;
      if (m) { speaker = m[1].trim(); text = m[2]; lastSpeaker = speaker; }
      sents.push({ id: s.id, speaker, text, sectionId: sec.id });
    }
    S.sentText[docId] = map;
    const turns = [];
    for (const s of sents) {
      const last = turns[turns.length - 1];
      if (last && last.speaker === s.speaker && last.sectionId === s.sectionId) last.sentences.push(s);
      else turns.push({ speaker: s.speaker, sectionId: s.sectionId, sentences: [s] });
    }
    const sections = rd.sections.map(sec => ({
      id: sec.id, gist: sec.gist, firstSid: sec.sentences[0]?.id || null,
    })).filter(sec => sec.firstSid);
    S.doc = { id: docId, filename: rd.filename, title: rd.title, summary: rd.summary,
      turns, sections, n: sents.length, nSec: rd.sections.length };
  }

  async function ensureDocsLoaded(docIds) {
    const missing = [...new Set(docIds)].filter(d => !S.sentText[d]);
    await Promise.all(missing.map(async d => {
      try {
        const rd = await API.document(S.pid, d);
        const map = {};
        for (const sec of rd.sections) for (const s of sec.sentences) map[s.id] = s.text.trim();
        S.sentText[d] = map;
      } catch (e) { S.sentText[d] = {}; }
    }));
  }

  async function refreshComments() { S.comments = await API.comments(S.pid).catch(() => S.comments); }
  async function refreshCodes() { S.codes = await API.codes(S.pid).catch(() => S.codes); }

  // ---- jobs ------------------------------------------------------------------------------------
  const JOB_LABEL = { ingest: 'Reading source', code_standard: 'Coding', code_panel: 'Coding · panel', recode: 'Re-coding with feedback', theme: 'Building themes', consolidate: 'Consolidating codebook' };
  function watchJob(jobId, kind, reattach) {
    if (S.jobs[jobId]) return;
    S.jobs[jobId] = { id: jobId, kind, status: 'running', progress: {} };
    if (!reattach) renderToolbar();
    API.pollJob(jobId, j => { S.jobs[jobId] = j; renderToolbar(); }).then(async j => {
      delete S.jobs[jobId];
      if (j.status === 'done') {
        if (kind === 'recode' && j.result?.diff) {
          const { diff } = j.result;
          const n = diff.new.length + diff.new_more_n, d = diff.dropped.length + diff.dropped_more_n;
          toast(`Re-code done — ${n} new code${n === 1 ? '' : 's'}, ${d} dropped`);
          openRecodeDiffSheet(j.result);
        } else {
          toast(`${JOB_LABEL[kind] || kind} — done`);
        }
        await loadProject(S.pid, true);
      } else {
        toast(`${JOB_LABEL[kind] || kind} failed: ${j.error || j.status}`, true);
        renderToolbar();
      }
    });
  }

  // ---- recode feedback diff sheet (P4.11) — makes the re-code loop's payoff visible -------------
  function openRecodeDiffSheet(result) {
    const { diff, notes_applied } = result;
    const col = (items, moreN, emptyMsg) => `
      <div class="diff-col">
        ${items.length ? items.map(x => `<div class="diff-item"><span class="lens-dot" style="background:${lensColor(x.coder)}"></span>${esc(x.label)}</div>`).join('')
          : `<p class="empty">${emptyMsg}</p>`}
        ${moreN ? `<p class="hint">+${moreN} more</p>` : ''}
      </div>`;
    const root = $('sheet-root');
    root.innerHTML = `
      <div class="sheet-wrap" id="sheet-bg">
        <div class="sheet" style="width:560px">
          <h2>Re-code with feedback — done</h2>
          <p class="hint" style="margin-bottom:12px">${diff.kept_n} code${diff.kept_n === 1 ? '' : 's'} unchanged · your ${notes_applied || 0} note${(notes_applied || 0) === 1 ? '' : 's'} rode along.</p>
          <div class="diff-cols">
            <div>
              <div class="group__label">New · ${diff.new.length + diff.new_more_n}</div>
              ${col(diff.new, diff.new_more_n, 'No new codes.')}
            </div>
            <div>
              <div class="group__label">Dropped · ${diff.dropped.length + diff.dropped_more_n}</div>
              ${col(diff.dropped, diff.dropped_more_n, 'Nothing dropped.')}
            </div>
          </div>
          <div class="sheet__foot"><button class="btn-quiet" id="diff-close">Close</button></div>
        </div>
      </div>`;
    const close = () => { root.innerHTML = ''; };
    $('diff-close').addEventListener('click', close);
    $('sheet-bg').addEventListener('click', e => { if (e.target.id === 'sheet-bg') close(); });
  }

  // ---- actions ---------------------------------------------------------------------------------
  async function act(fn, label) {
    try { const { job_id } = await fn(); watchJob(job_id, label); }
    catch (e) { toast(String(e.message || e), true); }
  }
  const runCoding = () => act(() => API.runCoding(S.pid, S.mode), `code_${S.mode}`);
  const runThemes = fb => act(() => API.runThemes(S.pid, S.mode, fb), 'theme');
  const runRecode = docId => act(() => API.recode(S.pid, docId, S.mode), 'recode');

  async function addNote(target_type, target_id, docId, body, context) {
    if (!body.trim()) return;
    try {
      const author = await ensureAuthor();
      await API.addComment(S.pid, { target_type, target_id, doc_id: docId, body: body.trim(), context, author });
      await refreshComments();
      render();
    } catch (e) { toast(String(e.message || e), true); }
  }
  async function editNote(cid, body) {
    try { await API.editComment(S.pid, cid, { body }); await refreshComments(); render(); }
    catch (e) { toast(String(e.message || e), true); }
  }
  async function deleteNote(cid) {
    try { await API.deleteComment(S.pid, cid); await refreshComments(); render(); }
    catch (e) { toast(String(e.message || e), true); }
  }
  async function dismissNote(cid) {
    try { await API.editComment(S.pid, cid, { status: 'dismissed' }); await refreshComments(); render(); }
    catch (e) { toast(String(e.message || e), true); }
  }
  async function reviseCode(cid, action, newLabel) {
    try {
      await API.revise(S.pid, cid, action, newLabel);
      await refreshCodes();
      S.renaming = false;
      render();
    } catch (e) { toast(String(e.message || e), true); }
  }
  const saveMemo = debounce(async (type, id, body, context, statusEl) => {
    try {
      const author = await ensureAuthor();
      const saved = await API.putMemo(S.pid, { target_type: type, target_id: id, body, context, author });
      S.memos[`${type}:${id}`] = { ...saved, target_type: type, target_id: id };
      if (statusEl) { statusEl.textContent = 'Saved'; setTimeout(() => { statusEl.textContent = ''; }, 1600); }
    } catch (e) { toast(String(e.message || e), true); }
  }, 700);

  // ---- navigation + history (P2.7: browser-back returns you from a jump) ------------------------
  // Minimal pushState: every view/doc change pushes {view, docId, sel}; popstate restores it
  // without pushing again. We don't try to reconstruct scroll position beyond the sid flash.
  let _restoring = false;
  function _pushNavState() {
    if (_restoring) return;
    const state = { view: S.view, docId: S.docId, sel: S.sel };
    const qs = new URLSearchParams({ project: S.pid || '' });
    if (S.view !== 'doc') qs.set('view', S.view);
    if (S.view === 'doc' && S.docId) qs.set('doc', S.docId);
    history.pushState(state, '', `?${qs.toString()}`);
  }

  function switchView(v) { S.view = v; S.sel = null; $('content').scrollTop = 0; _pushNavState(); render(); }

  function select(type, id) {
    S.sel = { type, id };
    S.renaming = false;
    renderContent();
    renderInspector();
    setTimeout(() => _mmReposition && _mmReposition(), 60);
  }
  function closeInspector() {
    S.sel = null; S.renaming = false;
    renderContent(); renderInspector();
    setTimeout(() => _mmReposition && _mmReposition(), 60);
  }

  function flashSentence(sid) {
    const el = document.querySelector(`[data-sid="${sid}"]`);
    if (!el) return;
    el.classList.remove('s--flash'); void el.offsetWidth; // restart animation if re-triggered
    el.classList.add('s--flash');
    setTimeout(() => el.classList.remove('s--flash'), 1500);
  }

  async function openDocView(docId, sid, opts) {
    S.view = 'doc';
    if (S.docId !== docId || !S.doc || S.doc.id !== docId) { S.docId = docId; await loadDoc(docId); }
    S.sel = sid ? { type: 'sentence', id: sid } : null;
    if (!opts?.noPush) _pushNavState();
    render();
    if (sid) {
      document.querySelector(`[data-sid="${sid}"]`)?.scrollIntoView({ block: 'center' });
      flashSentence(sid);
    }
  }
  function openCodeView(cid, opts) {
    S.view = 'codebook';
    S.sel = { type: 'code', id: cid };
    if (!opts?.noPush) _pushNavState();
    render();
    document.querySelector(`[data-cid="${cid}"]`)?.scrollIntoView({ block: 'center' });
  }

  window.addEventListener('popstate', e => {
    const st = e.state;
    _restoring = true;
    (async () => {
      if (!st) { S.view = 'home'; S.sel = null; render(); _restoring = false; return; }
      S.view = st.view || 'doc';
      if (st.docId && (S.docId !== st.docId || !S.doc)) { S.docId = st.docId; await loadDoc(st.docId).catch(() => {}); }
      S.sel = st.sel || null;
      render();
      if (S.view === 'doc' && S.sel?.type === 'sentence')
        document.querySelector(`[data-sid="${S.sel.id}"]`)?.scrollIntoView({ block: 'center' });
      _restoring = false;
    })();
  });

  // ---- confirm-delete sheet (type-the-name pattern, shared by project + document delete) --------
  function openConfirmDeleteSheet({ title, warning, confirmName, onConfirm }) {
    const root = $('sheet-root');
    root.innerHTML = `
      <div class="sheet-wrap" id="sheet-bg">
        <div class="sheet">
          <h2>${esc(title)}</h2>
          <p class="hint" style="margin-bottom:10px">${warning}</p>
          <label>Type <strong>${esc(confirmName)}</strong> to confirm</label>
          <input id="cf-input" type="text" autocomplete="off">
          <div class="sheet__foot">
            <button class="btn-quiet" id="cf-cancel">Cancel</button>
            <button class="btn-quiet btn-danger" id="cf-go" disabled>Delete</button>
          </div>
        </div>
      </div>`;
    const close = () => { root.innerHTML = ''; };
    $('cf-cancel').addEventListener('click', close);
    $('sheet-bg').addEventListener('click', e => { if (e.target.id === 'sheet-bg') close(); });
    const input = $('cf-input');
    const go = $('cf-go');
    input.addEventListener('input', () => { go.disabled = input.value !== confirmName; });
    input.focus();
    go.addEventListener('click', async () => {
      if (input.value !== confirmName) return;
      close();
      await onConfirm();
    });
  }

  // ---- upload sheet ------------------------------------------------------------------------------
  function openUploadSheet() {
    const root = $('sheet-root');
    root.innerHTML = `
      <div class="sheet-wrap" id="sheet-bg">
        <div class="sheet">
          <h2>Add a source</h2>
          <label>Kind</label>
          <select id="up-kind">
            ${Object.entries(KINDS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}
          </select>
          <label>File</label>
          <div class="sheet__file" id="up-drop">Choose a .txt or .md file…</div>
          <div class="sheet__foot">
            <button class="btn-quiet" id="up-cancel">Cancel</button>
          </div>
        </div>
      </div>`;
    const close = () => { root.innerHTML = ''; };
    $('up-cancel').addEventListener('click', close);
    $('sheet-bg').addEventListener('click', e => { if (e.target.id === 'sheet-bg') close(); });
    $('up-drop').addEventListener('click', () => {
      const input = $('file-input');
      input.onchange = async () => {
        const file = input.files[0];
        input.value = '';
        if (!file) return;
        const kind = $('up-kind').value;
        close();
        try {
          const { job_id } = await API.upload(S.pid, file, kind);
          watchJob(job_id, 'ingest');
        } catch (e) { toast(String(e.message || e), true); }
      };
      input.click();
    });
  }

  function openExportSheet() {
    const root = $('sheet-root');
    const base = `/projects/${S.pid}/export`;
    root.innerHTML = `
      <div class="sheet-wrap" id="sheet-bg">
        <div class="sheet">
          <h2>Export</h2>
          <div class="export-list">
            <a class="export-item" href="${base}" download>
              <span><strong>Everything</strong> · JSON</span>
              <span class="hint">codes with quotes, themes, memos, notes — archival</span>
            </a>
            <a class="export-item" href="${base}/codes.csv" download>
              <span><strong>Codebook</strong> · CSV</span>
              <span class="hint">one row per code — labels, definitions, evidence, memos</span>
            </a>
            <a class="export-item" href="${base}/themes.csv" download>
              <span><strong>Themes</strong> · CSV</span>
              <span class="hint">one row per theme — claims, provenance, falsifiability</span>
            </a>
            <a class="export-item" href="${base}/report.md" download>
              <span><strong>Report</strong> · Markdown</span>
              <span class="hint">narrative report for reading/appendix</span>
            </a>
          </div>
          <div class="sheet__foot"><button class="btn-quiet" id="ex-close">Close</button></div>
        </div>
      </div>`;
    const close = () => { root.innerHTML = ''; };
    $('ex-close').addEventListener('click', close);
    $('sheet-bg').addEventListener('click', e => { if (e.target.id === 'sheet-bg') close(); });
  }

  // ---- render: sidebar ---------------------------------------------------------------------------
  function renderSidebar() {
    const sb = $('sidebar');
    if (!S.detail) {
      sb.innerHTML = `<button class="proj"><span class="proj__name">MASSHINE</span></button>`;
      return;
    }
    const docs = S.detail.documents;
    const pack = S.packs.find(p => p.id === S.detail.project.pack_id);
    const stale = S.themes?.stale;
    const openNotes = openCount();
    sb.innerHTML = `
      <button class="proj" id="nav-home" title="All projects">
        <span class="proj__name">${esc(S.detail.project.name)}</span><span class="proj__chev">▾</span>
      </button>
      <div>
        <div class="group__label">Sources</div>
        ${docs.map(d => d.doc_id === S.renamingDoc ? `
          <div class="row row--rename">
            <input class="row-rename__input" data-rn="${d.doc_id}" type="text" value="${esc(docTitle(d))}">
          </div>` : `
          <div class="row-wrap ${S.view === 'doc' && d.doc_id === S.docId ? 'is-active' : ''}">
            <button class="row" data-nav-doc="${d.doc_id}" title="${esc(KINDS[d.kind] || d.kind)}">
              <span class="dot ${(d.status || '').startsWith('coded') ? 'dot--done' : 'dot--plain'}"></span>
              <span class="row__name">${esc(docTitle(d))}</span>
              <span class="row__count">${d.n_sentences}</span>
            </button>
            ${isViewer() ? '' : `<button class="row-more" data-more="${d.doc_id}" title="More">⋯</button>`}
          </div>`).join('')}
        ${isViewer() ? '' : `<button class="row row--quiet" id="nav-add">＋ Add source</button>`}
      </div>
      <div>
        <div class="group__label">Project</div>
        <button class="row ${S.view === 'overview' ? 'is-active' : ''}" data-nav="overview">
          <span class="row__name">Overview</span>
        </button>
        <button class="row ${S.view === 'codebook' ? 'is-active' : ''}" data-nav="codebook">
          <span class="row__name">Codebook</span><span class="row__count">${S.codes.length || ''}</span>
        </button>
        <button class="row ${S.view === 'themes' ? 'is-active' : ''}" data-nav="themes">
          ${stale ? '<span class="dot dot--stale"></span>' : ''}
          <span class="row__name">Themes</span><span class="row__count">${S.themes?.themes.length || ''}</span>
        </button>
        ${S.mode === 'panel' ? `
        <button class="row ${S.view === 'friction' ? 'is-active' : ''}" data-nav="friction">
          <span class="row__name">Friction</span>
        </button>` : ''}
        <button class="row ${S.view === 'notes' ? 'is-active' : ''}" data-nav="notes">
          <span class="row__name">Notes</span><span class="row__count">${openNotes || ''}</span>
        </button>
      </div>
      <div class="side-foot">${esc(pack?.title || 'Standard coding')}${S.mode === 'panel' ? `<br>${lensList().length || 3} lenses, blind` : ''}</div>`;
    $('nav-home').addEventListener('click', () => switchView('home'));
    $('nav-add')?.addEventListener('click', openUploadSheet);
    sb.querySelectorAll('[data-nav-doc]').forEach(b =>
      b.addEventListener('click', () => openDocView(b.dataset.navDoc)));
    sb.querySelectorAll('[data-nav]').forEach(b =>
      b.addEventListener('click', () => switchView(b.dataset.nav)));
    sb.querySelectorAll('[data-more]').forEach(b =>
      b.addEventListener('click', e => { e.stopPropagation(); openDocOverflowMenu(b, b.dataset.more); }));
    sb.querySelectorAll('[data-rn]').forEach(input => {
      const save = async () => {
        const docId = input.dataset.rn;
        const v = input.value.trim();
        S.renamingDoc = null;
        if (v && v !== docTitle(docById(docId))) {
          try { await API.patchDocument(S.pid, docId, v); toast('Renamed'); await loadProject(S.pid, true); }
          catch (err) { toast(String(err.message || err), true); renderSidebar(); }
        } else renderSidebar();
      };
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') input.blur();
        if (e.key === 'Escape') { S.renamingDoc = null; renderSidebar(); }
      });
      input.addEventListener('blur', save);
      input.focus(); input.select();
    });
  }

  function openDocOverflowMenu(anchorEl, docId) {
    document.querySelector('.overflow-menu')?.remove();
    const rect = anchorEl.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.className = 'overflow-menu';
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.left = `${rect.right - 130}px`;
    menu.innerHTML = `
      <button data-act="rename">Rename</button>
      <button data-act="delete" style="color:var(--red)">Delete</button>`;
    document.body.appendChild(menu);
    const close = () => { menu.remove(); document.removeEventListener('click', close); };
    setTimeout(() => document.addEventListener('click', close), 0);
    menu.querySelector('[data-act="rename"]').addEventListener('click', () => {
      close(); S.renamingDoc = docId; renderSidebar();
    });
    menu.querySelector('[data-act="delete"]').addEventListener('click', () => {
      close();
      const d = docById(docId);
      const name = docTitle(d);
      openConfirmDeleteSheet({
        title: 'Delete source',
        warning: `Codes from this source are removed and themes must be rebuilt. This cannot be undone.`,
        confirmName: name,
        onConfirm: async () => {
          try {
            await API.deleteDocument(S.pid, docId);
            toast('Source deleted');
            if (S.docId === docId) { S.docId = null; S.doc = null; }
            await loadProject(S.pid, true);
          } catch (err) { toast(String(err.message || err), true); }
        },
      });
    });
  }

  // ---- render: toolbar (journey) -----------------------------------------------------------------
  function nextAction() {
    if (isViewer() || !S.detail || running() || S.view === 'home') return null;
    const docs = S.detail.documents;
    if (!docs.length) return { label: 'Add a source', fn: openUploadSheet };
    if (anyUncoded()) return { label: anyCoded() ? 'Code new sources' : 'Run coding', fn: runCoding, hint: 'calls the model · takes minutes' };
    if (S.view === 'doc' && S.docId && openCount(S.docId) > 0)
      return { label: `Re-code with feedback · ${openCount(S.docId)}`, fn: () => runRecode(S.docId), hint: 'your notes ride along' };
    if (!S.themes?.themes.length && anyCoded()) return { label: 'Build themes', fn: () => runThemes(false), hint: 'calls the model · takes minutes' };
    if (S.themes?.stale) {
      const n = nNewThemeSources();
      const fb = openThemeNotes() > 0;
      if (n > 0 && !fb) {
        return { label: `Extend themes · ${n} new source${n > 1 ? 's' : ''}`, fn: () => runThemes(false),
                 hint: 'already-themed sources replay free — only new sources call the model' };
      }
      return { label: fb ? 'Rebuild themes with feedback' : 'Rebuild themes', fn: () => runThemes(fb) };
    }
    if (openThemeNotes() > 0) return { label: `Rebuild themes with feedback · ${openThemeNotes()}`, fn: () => runThemes(true) };
    return null;
  }

  function renderToolbar() {
    const tb = $('toolbar');
    if (!S.detail) { tb.innerHTML = `<div class="tb-doc"><strong>MASSHINE</strong></div>`; return; }
    const d = doc();
    // Breadcrumb (F2/P2.4): Projects / ‹project name› / ‹view or doc title›. "Projects" always
    // goes home; the project-name segment goes to the doc view of the current document.
    const crumbTitles = { codebook: 'Codebook', themes: 'Themes', friction: 'Standpoint friction',
      notes: 'Notes', overview: 'Overview' };
    const lastCrumb = S.view === 'doc' ? (d ? esc(docTitle(d)) : null) : (crumbTitles[S.view] || null);
    const breadcrumb = `
      <div class="crumb">
        <button class="crumb__seg" id="crumb-home">Projects</button>
        <span class="crumb__sep">/</span>
        <button class="crumb__seg ${lastCrumb ? '' : 'crumb__seg--current'}" id="crumb-project">${esc(S.detail.project.name)}</button>
        ${lastCrumb ? `<span class="crumb__sep">/</span><span class="crumb__seg crumb__seg--current">${lastCrumb}</span>` : ''}
      </div>`;
    let left = '';
    if (S.view === 'doc' && d) {
      const coded = (d.status || '').startsWith('coded');
      const themed = S.themes?.themes.length > 0;
      const stale = S.themes?.stale;
      left = `
        <div class="tb-doc">
          ${breadcrumb}
          <div class="pipeline">
            <span class="step"><span class="step__check">✓</span> Uploaded</span><span class="sep">·</span>
            <span class="step ${coded ? '' : 'step--todo'}">${coded ? '<span class="step__check">✓</span>' : '○'} Coded</span><span class="sep">·</span>
            <span class="step ${themed ? (stale ? 'step--warn' : '') : 'step--todo'}">${themed ? (stale ? '⟳' : '<span class="step__check">✓</span>') : '○'} Themes${stale ? ' out of date' : ''}</span>
          </div>
        </div>`;
    } else {
      left = `<div class="tb-doc">${breadcrumb}</div>`;
    }
    const job = running();
    const p = job?.progress || {};
    const jobChip = job ? `
      <span class="job-chip"><span class="spinner"></span>
        ${esc(JOB_LABEL[job.kind] || job.kind || 'Working')}${p.total ? ` · ${p.done || 0}/${p.total}` : ''}
      </span>` : '';
    const open = openCount();
    const notesChip = open && !job ? `<span class="notes-chip"><b>${open}</b> note${open > 1 ? 's' : ''} pending</span>` : '';
    const na = nextAction();
    const exportBtn = S.view !== 'home' && S.codes.length
      ? '<button class="btn-quiet" id="tb-export">Export</button>' : '';
    tb.innerHTML = `${left}<div class="tb-spacer"></div>${exportBtn}${notesChip}${jobChip}
      ${na ? `<button class="primary" id="tb-primary" ${na.hint ? `title="${esc(na.hint)}"` : ''}>${esc(na.label)}</button>` : ''}`;
    $('tb-export')?.addEventListener('click', openExportSheet);
    $('tb-primary')?.addEventListener('click', na?.fn);
    $('crumb-home')?.addEventListener('click', () => switchView('home'));
    $('crumb-project')?.addEventListener('click', () => { if (S.docId) openDocView(S.docId); });
  }

  // ---- render: content ---------------------------------------------------------------------------
  function renderContent() {
    const c = $('content');
    if (S.view === 'home' || !S.detail) return renderHome(c);
    if (S.view === 'doc') return renderDoc(c);
    if (S.view === 'codebook') return renderCodebook(c);
    if (S.view === 'themes') return renderThemes(c);
    if (S.view === 'friction') return renderFriction(c);
    if (S.view === 'notes') return renderNotes(c);
    if (S.view === 'overview') return renderOverview(c);
  }

  async function renderHome(c) {
    c.innerHTML = `<div class="home">
      <h1>MASSHINE</h1>
      <p class="sub">Reflexive thematic analysis with a standpoint panel — machine sweep, human judgment.</p>
      ${isViewer() ? '' : `
      <div class="home__new">
        <input id="np-name" type="text" placeholder="New project name…">
        <select id="np-pack"><option value="">Standard coding</option>
          ${S.packs.map(p => `<option value="${p.id}">${esc(p.title)} · panel</option>`).join('')}
        </select>
        <button class="primary" id="np-create">Create</button>
      </div>`}
      <div id="home-list"><p class="empty">Loading projects…</p></div>
      <label class="check" id="home-archived-toggle" style="margin-top:14px">
        <input type="checkbox" id="home-archived" ${S.showArchived ? 'checked' : ''}> Show archived
      </label>
    </div>`;
    $('np-create')?.addEventListener('click', async () => {
      const name = $('np-name').value.trim();
      if (!name) return;
      try {
        const p = await API.createProject(name, $('np-pack').value || null);
        await loadProject(p.id);
      } catch (e) { toast(String(e.message || e), true); }
    });
    $('home-archived').addEventListener('change', e => { S.showArchived = e.target.checked; renderContent(); });
    await renderHomeList();
    if (S.pinRequired && !$('home-signout')) {
      const out = document.createElement('button');
      out.id = 'home-signout';
      out.className = 'btn-bare';
      out.style.marginTop = '18px';
      out.textContent = S.role === 'viewer' ? 'Sign out (viewing)' : 'Sign out';
      out.addEventListener('click', async () => {
        try { await API.logout(); } catch (e) { /* cookie may already be gone */ }
        location.reload();
      });
      $('home-list')?.after(out);
    }
  }

  async function renderHomeList() {
    const list = $('home-list');
    if (!list) return;
    try {
      const projs = await API.projects(S.showArchived);
      list.innerHTML = projs.map(p => `
        <div class="proj-card ${p.archived ? 'is-archived' : ''}" data-pid="${p.id}">
          <button class="proj-card__main" data-open="${p.id}">
            <span class="proj-card__name">${esc(p.name)}</span>
            <span class="proj-card__meta">${esc(p.pack_id || 'standard')}${p.archived ? ' · archived' : ''}</span>
          </button>
          ${isViewer() ? '' : `
          <div class="proj-card__actions">
            <button class="btn-bare" data-rename="${p.id}" title="Rename">Rename</button>
            <button class="btn-bare" data-archive="${p.id}" title="${p.archived ? 'Unarchive' : 'Archive'}">${p.archived ? 'Unarchive' : 'Archive'}</button>
            <button class="btn-bare" data-delete="${p.id}" title="Delete" style="color:var(--red)">Delete</button>
          </div>`}
        </div>`).join('') || `<p class="empty">${S.showArchived ? 'No archived projects.' : 'No projects yet — create one above.'}</p>`;
      list.querySelectorAll('[data-open]').forEach(b =>
        b.addEventListener('click', () => loadProject(b.dataset.open)));
      list.querySelectorAll('[data-rename]').forEach(b =>
        b.addEventListener('click', e => { e.stopPropagation(); startRenameProjectCard(b.dataset.rename); }));
      list.querySelectorAll('[data-archive]').forEach(b =>
        b.addEventListener('click', async e => {
          e.stopPropagation();
          const pid = b.dataset.archive;
          const p = (await API.projects(true)).find(x => x.id === pid);
          try {
            await API.patchProject(pid, { archived: !p?.archived });
            toast(p?.archived ? 'Project unarchived' : 'Project archived');
            await renderHomeList();
          } catch (err) { toast(String(err.message || err), true); }
        }));
      list.querySelectorAll('[data-delete]').forEach(b =>
        b.addEventListener('click', e => {
          e.stopPropagation();
          const pid = b.dataset.delete;
          const card = list.querySelector(`[data-pid="${pid}"] .proj-card__name`);
          const name = card?.textContent || pid;
          openConfirmDeleteSheet({
            title: 'Delete project',
            warning: `This permanently deletes <strong>${esc(name)}</strong> — all sources, codes, themes, notes, and exports. This cannot be undone.`,
            confirmName: name,
            onConfirm: async () => {
              try {
                await API.deleteProject(pid);
                toast('Project deleted');
                await renderHomeList();
              } catch (err) { toast(String(err.message || err), true); }
            },
          });
        }));
    } catch (e) { list.innerHTML = `<p class="empty">${esc(String(e.message || e))}</p>`; }
  }

  function startRenameProjectCard(pid) {
    const row = document.querySelector(`.proj-card[data-pid="${pid}"] .proj-card__main`);
    if (!row) return;
    const cur = row.querySelector('.proj-card__name')?.textContent || '';
    row.innerHTML = `<input class="proj-card__rename" type="text" value="${esc(cur)}">`;
    const input = row.querySelector('input');
    input.focus(); input.select();
    const save = async () => {
      const v = input.value.trim();
      if (v && v !== cur) {
        try { await API.patchProject(pid, { name: v }); toast('Renamed'); }
        catch (err) { toast(String(err.message || err), true); }
      }
      await renderHomeList();
    };
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') renderHomeList();
    });
    input.addEventListener('blur', save);
  }

  function renderDoc(c) {
    if (!S.doc || !S.docId) {
      c.innerHTML = `<div class="journey">
        <h2>Start with a source</h2>
        <p>MASSHINE reads any plain-text source — interview transcripts, field notes, focus groups, documents.</p>
        <div class="journey__steps">
          <div class="journey__step"><span class="journey__num">1</span> Add a source (.txt / .md)</div>
          <div class="journey__step"><span class="journey__num">2</span> Run coding — ${S.mode === 'panel' ? 'a blind panel of lenses reads it' : 'the coder reads it blind'}</div>
          <div class="journey__step"><span class="journey__num">3</span> Read, then leave notes — the model considers them when you re-code</div>
          <div class="journey__step"><span class="journey__num">4</span> Build themes into the project catalogue</div>
        </div><br>
        <button class="primary" id="journey-add">Add a source</button>
      </div>`;
      $('journey-add')?.addEventListener('click', openUploadSheet);
      return;
    }
    const d = doc();
    const noteSids = openNoteSids(S.docId);
    const kindLabel = KINDS[d?.kind] || 'Source';
    const codedSet = new Set();
    const codeCountBySid = {};
    for (const code of S.codes) {
      if (code.status === 'rejected') continue;
      for (const ev of code.evidence) {
        const [dd, sid] = ev.split('#');
        if (dd === S.docId) { codedSet.add(sid); codeCountBySid[sid] = (codeCountBySid[sid] || 0) + 1; }
      }
    }
    const sections = S.doc.sections || [];
    const gistById = {};
    for (const sec of sections) gistById[sec.id] = sec.gist;
    let curSection = null;
    c.innerHTML = `<div class="transcript">
      <div class="doc-head">
        <h1>${esc(docTitle(d))}</h1>
        <p>${esc(kindLabel)} · ${S.doc.nSec} sections · ${S.doc.n} sentences${codedSet.size ? ` · ${codedSet.size} coded` : ' · not yet coded'}</p>
        ${S.doc.summary ? `<p class="doc-summary">${esc(S.doc.summary)}</p>` : ''}
        ${sections.filter(s => s.gist).length > 1 ? `
        <details class="doc-toc">
          <summary>On this page · ${sections.length} sections</summary>
          <div class="doc-toc__list">
            ${sections.filter(s => s.gist).map(s => `<button class="doc-toc__item" data-toc-sid="${s.firstSid}">${esc(s.gist)}</button>`).join('')}
          </div>
        </details>` : ''}
        <div class="doc-search">
          <input type="search" id="doc-search-q" placeholder="Search this source… (/)">
          <span class="doc-search__count" id="doc-search-count"></span>
          <button class="btn-bare" id="doc-search-prev" title="Previous match">↑</button>
          <button class="btn-bare" id="doc-search-next" title="Next match">↓</button>
        </div>
      </div>
      <div class="turns">
        ${S.doc.turns.map(t => {
          let gistHtml = '';
          if (t.sectionId !== curSection) {
            curSection = t.sectionId;
            const gist = gistById[t.sectionId];
            if (gist) gistHtml = `<div class="section-head" id="sec-${esc(t.sectionId)}"><span class="section-head__label">${esc(gist)}</span></div>`;
          }
          return `${gistHtml}
          <div class="turn">
            ${t.speaker ? `<div class="turn__speaker">${esc(t.speaker)}</div>` : ''}
            <div class="turn__body">${t.sentences.map(s => `<span
                class="s ${codedSet.has(s.id) ? 's--coded' : ''} ${S.sel?.type === 'sentence' && S.sel.id === s.id ? 's--active' : ''}"
                data-sid="${s.id}">${esc(s.text)}${noteSids.has(s.id) ? '<span class="note-mark"></span>' : ''}</span>`).join(' ')}
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>
    <div class="minimap" id="minimap"></div>`;
    c.querySelectorAll('[data-sid]').forEach(el =>
      el.addEventListener('click', () => select('sentence', el.dataset.sid)));
    c.querySelectorAll('[data-toc-sid]').forEach(el =>
      el.addEventListener('click', () => {
        c.closest('.content')?.querySelector(`[data-sid="${el.dataset.tocSid}"]`)?.scrollIntoView({ block: 'center' });
        c.querySelector('.doc-toc')?.removeAttribute('open');
      }));
    wireDocSearch(c);
    wireSentGutter(c);
    wireMinimap(c, codeCountBySid);
  }

  let _mmReposition = null;  // set by wireMinimap; called when the inspector reflows the grid

  // ---- coding-density minimap (P5.14) --------------------------------------------------------------
  // One thin cell per sentence (order preserved, capped at ~800 — sampled evenly beyond that),
  // tinted by active-code count; a highlighted band tracks the current viewport; click jumps.
  function wireMinimap(c, codeCountBySid) {
    const mm = $('minimap');
    if (!mm) return;
    const allSids = _sentenceEls().map(el => el.dataset.sid);
    if (allSids.length < 8) { mm.style.display = 'none'; return; }
    const CAP = 800;
    const step = Math.max(1, Math.ceil(allSids.length / CAP));
    const sampled = allSids.filter((_, i) => i % step === 0);
    const maxN = Math.max(1, ...sampled.map(sid => codeCountBySid[sid] || 0));
    mm.innerHTML = sampled.map(sid => {
      const n = codeCountBySid[sid] || 0;
      const op = n ? Math.min(1, 0.22 + 0.78 * (n / maxN)) : 0;
      return `<div class="minimap__cell" data-sid="${sid}" style="${n ? `background:oklch(56% 0.115 32 / ${op.toFixed(2)})` : ''}"></div>`;
    }).join('');
    mm.querySelectorAll('[data-sid]').forEach(el =>
      el.addEventListener('click', () => openDocView(S.docId, el.dataset.sid)));
    const content = c.closest('.content');
    const band = document.createElement('div');
    band.className = 'minimap__band';
    mm.appendChild(band);
    const positionStrip = () => {
      if (!content) return;
      const cRect = content.getBoundingClientRect();
      mm.style.top = `${cRect.top}px`;
      mm.style.height = `${cRect.height}px`;
      mm.style.left = `${cRect.right - 10}px`;
    };
    const updateBand = () => {
      if (!content) return;
      const cRect = content.getBoundingClientRect();
      const scrollH = content.scrollHeight || 1;
      const top = (content.scrollTop / scrollH) * mm.offsetHeight;
      const h = Math.max(16, (cRect.height / scrollH) * mm.offsetHeight);
      band.style.top = `${top}px`;
      band.style.height = `${h}px`;
    };
    positionStrip();
    updateBand();
    content?.addEventListener('scroll', updateBand);
    window.addEventListener('resize', () => { positionStrip(); updateBand(); });
    // the content box also changes width when the inspector opens/closes — that path calls
    // _mmReposition explicitly (select/closeInspector), since ResizeObserver delivery proved
    // unreliable in embedded webviews. Belt-and-braces: keep an RO for other reflows.
    _mmReposition = () => { if (mm.isConnected) { positionStrip(); updateBand(); } };
    if (window.ResizeObserver && content) {
      const ro = new ResizeObserver(() => {
        if (!mm.isConnected) { ro.disconnect(); return; }
        positionStrip(); updateBand();
      });
      ro.observe(content);
    }
  }

  // ---- in-document search (P5.13) -----------------------------------------------------------------
  // Client-side substring match over the sentences already in the DOM; quiet <mark>-style
  // highlight, "n of m" readout, prev/next cycles through matches and reuses the existing
  // .s--flash jump animation so it feels like the same navigation primitive as sid jumps.
  const _search = { q: '', matches: [], idx: -1 };
  function _searchApply(c) {
    c.querySelectorAll('.s').forEach(el => el.classList.remove('s--match', 's--match-active'));
    const q = _search.q.trim().toLowerCase();
    _search.matches = [];
    _search.idx = -1;
    if (q) {
      c.querySelectorAll('.s').forEach(el => {
        if (el.textContent.toLowerCase().includes(q)) {
          el.classList.add('s--match');
          _search.matches.push(el);
        }
      });
    }
    const countEl = $('doc-search-count');
    if (countEl) countEl.textContent = q ? (_search.matches.length ? `${_search.idx + 1 || 0} of ${_search.matches.length}` : '0 of 0') : '';
  }
  function _searchGo(dir) {
    if (!_search.matches.length) return;
    _search.idx = (_search.idx + dir + _search.matches.length) % _search.matches.length;
    const el = _search.matches[_search.idx];
    el.classList.remove('s--flash'); void el.offsetWidth;
    el.classList.add('s--match-active', 's--flash');
    el.scrollIntoView({ block: 'center' });
    setTimeout(() => el.classList.remove('s--flash'), 1500);
    const countEl = $('doc-search-count');
    if (countEl) countEl.textContent = `${_search.idx + 1} of ${_search.matches.length}`;
  }
  function wireDocSearch(c) {
    const input = $('doc-search-q');
    if (!input) return;
    input.value = _search.q;
    _searchApply(c);
    input.addEventListener('input', () => { _search.q = input.value; _searchApply(c); });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); _searchGo(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { input.value = ''; _search.q = ''; _searchApply(c); input.blur(); }
    });
    $('doc-search-next').addEventListener('click', () => _searchGo(1));
    $('doc-search-prev').addEventListener('click', () => _searchGo(-1));
  }

  // ---- sentence-id gutter (P5.13) ------------------------------------------------------------------
  // A quiet mono id shown in the left margin on hover/selection — positioned via getBoundingClientRect
  // (like the sid-tip) so it never reflows the text column.
  let _gutterEl = null;
  function _gutter() {
    if (!_gutterEl) { _gutterEl = document.createElement('div'); _gutterEl.className = 'sent-gutter'; document.body.appendChild(_gutterEl); }
    return _gutterEl;
  }
  function _showGutter(el) {
    const sid = el.dataset.sid;
    if (!sid) return;
    const g = _gutter();
    const r = el.getBoundingClientRect();
    const colEl = document.querySelector('.turns');
    const colRect = colEl ? colEl.getBoundingClientRect() : { left: r.left };
    g.textContent = sid;
    g.style.display = 'block';
    g.style.top = `${r.top + (r.height - 14) / 2}px`;
    g.style.left = `${Math.max(4, colRect.left - 8)}px`;
  }
  function _hideGutter() { if (_gutterEl) _gutterEl.style.display = 'none'; }
  function wireSentGutter(c) {
    c.addEventListener('mouseover', e => {
      const el = e.target.closest('[data-sid]');
      if (el) _showGutter(el);
    });
    c.addEventListener('mouseout', e => {
      const el = e.target.closest('[data-sid]');
      if (el && !el.contains(e.relatedTarget)) _hideGutter();
    });
    if (S.sel?.type === 'sentence') {
      const el = c.querySelector(`[data-sid="${S.sel.id}"]`);
      if (el) _showGutter(el);
    }
  }

  // ---- j/k sentence navigation (P5.13) — only when no input/textarea is focused -------------------
  function _sentenceEls() {
    return Array.from(document.querySelectorAll('#content .s[data-sid]'));
  }
  document.addEventListener('keydown', e => {
    if (S.view !== 'doc' || !S.doc) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    const inField = tag === 'input' || tag === 'textarea' || document.activeElement?.isContentEditable;
    if (!inField && e.key === '/') { e.preventDefault(); $('doc-search-q')?.focus(); return; }
    if (inField) return;
    if (e.key !== 'j' && e.key !== 'k') return;
    e.preventDefault();
    const els = _sentenceEls();
    if (!els.length) return;
    const curIdx = S.sel?.type === 'sentence' ? els.findIndex(el => el.dataset.sid === S.sel.id) : -1;
    let nextIdx = e.key === 'j' ? curIdx + 1 : curIdx - 1;
    nextIdx = Math.max(0, Math.min(els.length - 1, nextIdx));
    const el = els[nextIdx];
    if (!el) return;
    select('sentence', el.dataset.sid);
    el.scrollIntoView({ block: 'center' });
  });

  function renderCodebook(c) {
    const { q, lens, type, rejected } = S.cb;
    const lenses = lensList();
    let list = S.codes;
    if (!rejected) list = list.filter(x => x.status !== 'rejected');
    if (lens) list = list.filter(x => x.coder === lens);
    if (type) list = list.filter(x => x.code_type === type);
    if (q) {
      const qq = q.toLowerCase();
      list = list.filter(x => label(x).toLowerCase().includes(qq) || (x.definition || '').toLowerCase().includes(qq));
    }
    const fams = S.families?.families || [];
    const grouping = fams.length && (S.cbGroup || 'family') === 'family' ? 'family' : 'lens';
    const rowFor = (x, hue) => {
      const notes = commentsFor('code', x.id).filter(n => n.status === 'open').length;
      return `<button class="code-row ${x.status === 'rejected' ? 'is-rejected' : ''} ${S.sel?.type === 'code' && S.sel.id === x.id ? 'is-active' : ''}" data-cid="${x.id}"
          ${hue != null ? `style="border-left:2px solid ${famColor(hue, 0.55)}"` : ''}>
        ${notes ? '<span class="note-dot"></span>' : ''}
        <span class="lens-dot" style="background:${lensColor(x.coder)};flex:none;align-self:center"></span>
        <span class="code-row__label">${esc(label(x))}</span>
        <span class="code-row__meta"><span class="tag">${x.code_type}</span> · ${x.evidence.length}</span>
      </button>`;
    };
    let listing;
    if (!list.length) {
      listing = `<p class="empty">${S.codes.length ? 'Nothing matches the filter.' : 'No codes yet — run coding first.'}</p>`;
    } else if (grouping === 'family') {
      const byFam = {};
      for (const x of list) (byFam[x.family_id || '_none'] ||= []).push(x);
      listing = fams.map(f => {
        const members = byFam[f.id] || [];
        const open = S.famOpen.has(f.id) || (q && members.length > 0);
        const famNotes = commentsFor('family', f.id).filter(n => n.status === 'open').length;
        return `<div class="fam" style="border-left:3px solid ${famColor(f.hue)}">
          <div class="fam__head" data-fam-toggle="${f.id}">
            <span class="fam__chev">${open ? '▾' : '▸'}</span>
            <span class="lens-dot" style="background:${famColor(f.hue)}"></span>
            <span class="fam__label">${esc(f.label)}</span>
            ${famNotes ? '<span class="note-dot"></span>' : ''}
            <span class="fam__def">${esc(f.definition)}</span>
            <span class="fam__count">${members.length}${members.length !== f.n_codes ? ` of ${f.n_codes}` : ''}</span>
            <button class="btn-bare" data-fam-open="${f.id}" title="Memo & notes">✎</button>
          </div>
          ${open ? `<div class="fam__body">${members.length
            ? members.map(x => rowFor(x, f.hue)).join('')
            : '<p class="empty">No codes match the current filter.</p>'}</div>` : ''}
        </div>`;
      }).join('') + ((byFam._none || []).length ? `
        <div class="lens-head">not yet filed · ${byFam._none.length}</div>
        ${byFam._none.map(x => rowFor(x, null)).join('')}` : '');
    } else {
      const groups = {};
      for (const x of list) (groups[x.coder] ||= []).push(x);
      const order = lenses.filter(l => groups[l]);
      listing = order.map(l => `
        <div class="lens-head"><span class="lens-dot" style="background:${lensColor(l)}"></span>${esc(l)} · ${groups[l].length}</div>
        ${groups[l].map(x => {
          const notes = commentsFor('code', x.id).filter(n => n.status === 'open').length;
          return `<button class="code-row ${x.status === 'rejected' ? 'is-rejected' : ''} ${S.sel?.type === 'code' && S.sel.id === x.id ? 'is-active' : ''}" data-cid="${x.id}">
            ${notes ? '<span class="note-dot"></span>' : ''}
            <span class="code-row__label">${esc(label(x))}</span>
            <span class="code-row__meta"><span class="tag">${x.code_type}</span> · ${x.evidence.length}</span>
          </button>`;
        }).join('')}`).join('');
    }
    c.innerHTML = `<div class="panel">
      <h1>Codebook</h1>
      <p class="sub">${S.codes.length} codes${S.mode === 'panel' ? ` from ${lenses.length} blind lenses` : ''}${fams.length ? `, ${fams.length} families` : ''}. Rename or reject a code and the model hears about it on the next re-code.</p>
      ${S.families?.stale && fams.length ? `<div class="banner">⟳ The codebook changed since these families were built.
        <div class="tb-spacer"></div>
        ${isViewer() ? '' : '<button class="btn-quiet" id="cb-reconsolidate">Re-consolidate</button>'}</div>` : ''}
      <div class="filterbar">
        <input type="search" id="cb-q" placeholder="Search codes…" value="${esc(q)}">
        ${fams.length ? `<span class="seg" id="cb-group">
          <button data-v="family" class="${grouping === 'family' ? 'is-active' : ''}">Families</button>
          <button data-v="lens" class="${grouping === 'lens' ? 'is-active' : ''}">Lenses</button>
        </span>` : ''}
        ${lenses.length > 1 ? `<span class="seg" id="cb-lens">
          <button data-v="" class="${lens === '' ? 'is-active' : ''}">All</button>
          ${lenses.map(l => `<button data-v="${l}" class="${lens === l ? 'is-active' : ''}">${esc(l)}</button>`).join('')}
        </span>` : ''}
        <span class="seg" id="cb-type">
          <button data-v="" class="${type === '' ? 'is-active' : ''}">All</button>
          <button data-v="semantic" class="${type === 'semantic' ? 'is-active' : ''}">Semantic</button>
          <button data-v="latent" class="${type === 'latent' ? 'is-active' : ''}">Latent</button>
        </span>
        <label class="check"><input type="checkbox" id="cb-rej" ${rejected ? 'checked' : ''}> rejected</label>
        ${!isViewer() && S.codes.length ? `<button class="btn-quiet" id="cb-consolidate">${fams.length ? 'Re-consolidate' : 'Consolidate codebook'}</button>` : ''}
      </div>
      ${listing}
    </div>`;
    const qEl = $('cb-q');
    qEl.addEventListener('input', () => { S.cb.q = qEl.value; renderContent(); const el2 = $('cb-q'); el2.focus(); el2.setSelectionRange(el2.value.length, el2.value.length); });
    $('cb-lens')?.querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => { S.cb.lens = b.dataset.v; renderContent(); }));
    $('cb-type').querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => { S.cb.type = b.dataset.v; renderContent(); }));
    $('cb-rej').addEventListener('change', e => { S.cb.rejected = e.target.checked; renderContent(); });
    c.querySelectorAll('[data-cid]').forEach(b =>
      b.addEventListener('click', () => select('code', b.dataset.cid)));
    $('cb-group')?.querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => { S.cbGroup = b.dataset.v; renderContent(); }));
    c.querySelectorAll('[data-fam-toggle]').forEach(h =>
      h.addEventListener('click', e => {
        if (e.target.closest('[data-fam-open]')) return;
        const fid = h.dataset.famToggle;
        if (S.famOpen.has(fid)) S.famOpen.delete(fid); else S.famOpen.add(fid);
        renderContent();
      }));
    c.querySelectorAll('[data-fam-open]').forEach(b =>
      b.addEventListener('click', e => { e.stopPropagation(); select('family', b.dataset.famOpen); }));
    const kick = () => { const n = S.codes.filter(x => x.status !== 'rejected').length; openConsolidateSheet(n); };
    $('cb-consolidate')?.addEventListener('click', kick);
    $('cb-reconsolidate')?.addEventListener('click', kick);
  }

  function openConsolidateSheet(nCodes) {
    const root = $('sheet-root');
    const has = (S.families?.families || []).length > 0;
    root.innerHTML = `
      <div class="sheet-wrap" id="sheet-bg">
        <div class="sheet">
          <h2>${has ? 'Re-consolidate the codebook' : 'Consolidate the codebook'}</h2>
          <p class="hint" style="font-size:12px;line-height:1.5;margin:0 0 6px">
            One model call groups ${nCodes} active codes into 8–15 families — the compact view of
            the codebook, with every code and its evidence kept underneath.${has ? ' Existing families are replaced; your open family notes ride along.' : ''}
          </p>
          <div class="sheet__foot">
            <button class="btn-quiet" id="cs-cancel">Cancel</button>
            <button class="primary" id="cs-go">${has ? 'Re-consolidate' : 'Consolidate'}</button>
          </div>
        </div>
      </div>`;
    const close = () => { root.innerHTML = ''; };
    $('cs-cancel').addEventListener('click', close);
    $('sheet-bg').addEventListener('click', e => { if (e.target.id === 'sheet-bg') close(); });
    $('cs-go').addEventListener('click', async () => {
      close();
      try { const { job_id } = await API.consolidate(S.pid); watchJob(job_id, 'consolidate'); }
      catch (e) { toast(String(e.message || e), true); }
    });
  }

  function renderThemes(c) {
    const th = S.themes?.themes || [];
    const lenses = lensList();
    const staleN = S.themes?.stale ? nNewThemeSources() : 0;
    const fb = openThemeNotes() > 0;
    const bannerText = staleN > 0 && !fb
      ? `${staleN} source${staleN > 1 ? 's' : ''} aren't in these themes yet.`
      : `The codebook changed since these themes were built.`;
    const bannerBtnLabel = staleN > 0 && !fb
      ? `Extend themes · ${staleN} new source${staleN > 1 ? 's' : ''}`
      : `Rebuild themes${fb ? ' with feedback' : ''}`;
    c.innerHTML = `<div class="panel">
      <h1>Themes</h1>
      <p class="sub">Each theme is a claim with its evidence and paradigm provenance — which lenses independently support it. Select a theme to write your memo or leave a note for the model.</p>
      ${S.themes?.stale ? `<div class="banner">⟳ ${bannerText}
        <div class="tb-spacer"></div>
        <button class="btn-quiet" id="th-rebuild" ${staleN > 0 && !fb ? `title="already-themed sources replay free — only new sources call the model"` : ''}>${bannerBtnLabel}</button></div>` : ''}
      ${th.length ? th.map(t => {
        const prov = t.paradigm_provenance || {};
        const provChips = lenses.filter(l => prov[l]).map(l =>
          `<span class="chip"><span class="lens-dot" style="background:${lensColor(l)}"></span>${esc(l)} ${prov[l]}</span>`).join('');
        const anchors = (t.key_evidence_sentence_ids || []).slice(0, 8);
        const tensions = (t.tensions || []).map(codeById).filter(Boolean);
        const notes = commentsFor('theme', t.id).filter(n => n.status === 'open').length;
        return `<article class="theme-card ${S.sel?.type === 'theme' && S.sel.id === t.id ? 'is-active' : ''}" data-tid="${t.id}">
          <div class="theme-card__meta">
            <span class="chip chip--id">${esc(t.id)}</span>
            <span class="chip">${esc(t.coverage || '')} sources</span>
            <span class="chip">${esc(t.claim_scope || '')}</span>
            ${provChips}
            ${tensions.length ? `<span class="chip chip--warn">${tensions.length} tension${tensions.length > 1 ? 's' : ''}</span>` : ''}
            ${notes ? `<span class="chip chip--warn">● ${notes} note${notes > 1 ? 's' : ''}</span>` : ''}
          </div>
          <p class="theme-card__claim">${esc(t.central_concept)}</p>
          ${(t.subthemes || []).map(st => `<p class="theme-card__sub">${esc(st.claim)}</p>`).join('')}
          ${anchors.length ? `<div class="theme-card__sect"><h4>Anchored in</h4>
            ${anchors.map(a => { const [dd, sid] = a.split('#'); return `<button class="anchor" data-doc="${dd}" data-sid="${sid}" data-tip-doc="${dd}" data-tip-sid="${sid}">${esc(sidChipLabel(dd, sid))}</button>`; }).join('')}</div>` : ''}
          ${tensions.length ? `<div class="theme-card__sect"><h4>In tension with</h4>
            ${tensions.map(x => `<button class="anchor" data-code="${x.id}">${esc(label(x))}</button>`).join('')}</div>` : ''}
          ${t.falsified_if ? `<div class="theme-card__sect"><h4>Falsified if</h4><p class="falsif">${esc(t.falsified_if)}</p></div>` : ''}
        </article>`;
      }).join('') : `<p class="empty">No themes yet — ${anyCoded() ? 'build them from the codebook.' : 'run coding first.'}</p>`}
    </div>`;
    $('th-rebuild')?.addEventListener('click', () => runThemes(fb));
    c.querySelectorAll('[data-tid]').forEach(card =>
      card.addEventListener('click', e => {
        if (e.target.closest('[data-doc],[data-code]')) return;
        select('theme', card.dataset.tid);
      }));
    c.querySelectorAll('[data-doc]').forEach(b =>
      b.addEventListener('click', () => openDocView(b.dataset.doc, b.dataset.sid)));
    c.querySelectorAll('[data-code]').forEach(b =>
      b.addEventListener('click', () => openCodeView(b.dataset.code)));
  }

  async function renderFriction(c) {
    const docs = S.detail.documents.filter(d => (d.status || '').startsWith('coded'));
    if (!docs.length) { c.innerHTML = '<div class="panel"><h1>Standpoint friction</h1><p class="empty">Run panel coding first.</p></div>'; return; }
    if (!S.frictionDoc || !docs.find(d => d.doc_id === S.frictionDoc)) S.frictionDoc = docs[0].doc_id;
    c.innerHTML = `<div class="panel">
      <h1>Standpoint friction</h1>
      <p class="sub">Where the lenses diverge is data, not error. <strong>Interpretive</strong>: same sentence, different readings. <strong>Attentional</strong>: one lens codes what another passes over.</p>
      <div class="filterbar">${docs.length > 1 ? `<span class="seg" id="fr-doc">
        ${docs.map(d => `<button data-v="${d.doc_id}" class="${S.frictionDoc === d.doc_id ? 'is-active' : ''}">${esc((d.filename || d.doc_id).replace(/\.(txt|md)$/i, ''))}</button>`).join('')}
      </span>` : ''}</div>
      <div id="fr-body"><p class="empty">Computing friction…</p></div>
    </div>`;
    $('fr-doc')?.querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => { S.frictionDoc = b.dataset.v; renderContent(); }));
    try {
      const fr = await API.friction(S.pid, S.frictionDoc);
      const coders = fr.coders || [];
      $('fr-body').innerHTML = fr.friction.map(f => `
        <div class="friction-card">
          <div class="friction-card__bar">
            <span class="chip ${f.kind === 'interpretive' ? 'chip--id' : ''}">${f.kind}</span>
            <button class="anchor" data-doc="${S.frictionDoc}" data-sid="${f.sid}" data-tip-doc="${S.frictionDoc}" data-tip-sid="${f.sid}">${esc(sidChipLabel(S.frictionDoc, f.sid))}</button>
          </div>
          <p class="friction-card__quote">“${esc(f.text)}”</p>
          <div class="friction-card__cols" style="grid-template-columns:repeat(${coders.length},1fr)">
            ${coders.map(co => {
              const reads = f.readings[co] || [];
              return `<div class="friction-card__col">
                <div class="lens-name"><span class="lens-dot" style="background:${lensColor(co)}"></span>${esc(co)}</div>
                ${reads.length ? reads.map(r => `<div class="reading">${esc(r.label)} <span class="tag">${esc(r.type || '')}</span></div>`).join('') : '<div class="silent">— silent here</div>'}
              </div>`;
            }).join('')}
          </div>
        </div>`).join('') || '<p class="empty">No friction — the lenses agree everywhere they overlap.</p>';
      $('fr-body').querySelectorAll('[data-doc]').forEach(b =>
        b.addEventListener('click', () => openDocView(b.dataset.doc, b.dataset.sid)));
    } catch (e) { $('fr-body').innerHTML = `<p class="empty">${esc(String(e.message || e))}</p>`; }
  }

  // ---- render: notes review queue (P3.9) ----------------------------------------------------------
  // The pre-flight check before "Re-code with feedback": every open/addressed/dismissed comment,
  // grouped by what it targets, with a jump straight to the thing it's about. Frontend-only — the
  // comments API already carries everything this view needs.
  function renderNotes(c) {
    const GROUPS = [
      { type: 'sentence', title: 'Sentences' },
      { type: 'code', title: 'Codes' },
      { type: 'theme', title: 'Themes' },
      { type: 'document', title: 'Sources' },
    ];
    const filter = S.notesFilter;
    const matches = n => filter === 'all' || n.status === filter;
    const counts = { all: S.comments.length, open: 0, addressed: 0, dismissed: 0 };
    for (const n of S.comments) counts[n.status] = (counts[n.status] || 0) + 1;
    function rowFor(n) {
      const ctx = n.context || {};
      let jump = null, snippet = '';
      if (n.target_type === 'sentence') {
        snippet = ctx.quote || '';
        jump = () => openDocView(n.doc_id, n.target_id);
      } else if (n.target_type === 'code') {
        const cd = codeById(n.target_id);
        snippet = ctx.label || cd?.label || n.target_id;
        jump = () => openCodeView(n.target_id);
      } else if (n.target_type === 'theme') {
        snippet = ctx.claim || n.target_id;
        jump = () => { switchView('themes'); select('theme', n.target_id); };
      } else if (n.target_type === 'document') {
        const d = docById(n.target_id);
        snippet = docTitle(d) || n.target_id;
        jump = () => openDocView(n.target_id);
      }
      return `
        <div class="notes-row" data-nid="${n.id}">
          <div class="notes-row__main">
            <div class="notes-row__meta">
              <span class="note__status note__status--${n.status === 'open' ? 'open' : 'addressed'}">${n.status}</span>
              ${n.author ? `<span class="note__author">${esc(n.author)}</span> · ` : ''}
              <span class="note__when">${timeAgo(n.created_at)}</span>
            </div>
            <p class="notes-row__body">${esc(n.body)}</p>
            ${snippet ? `<p class="notes-row__ctx">${esc(String(snippet).slice(0, 140))}</p>` : ''}
          </div>
          <div class="notes-row__actions">
            ${jump ? `<button class="btn-quiet" data-jump="${n.id}">Jump</button>` : ''}
            ${isViewer() ? '' : `
              <button class="btn-bare" data-edit="${n.id}">edit</button>
              ${n.status === 'open' ? `<button class="btn-bare" data-dismiss="${n.id}">dismiss</button>` : ''}
              <button class="btn-bare" data-del="${n.id}">delete</button>`}
          </div>
        </div>`;
    }
    const jumpFns = {};
    let body = '';
    for (const g of GROUPS) {
      const rows = S.comments.filter(n => n.target_type === g.type && matches(n));
      if (!rows.length) continue;
      body += `<div class="lens-head">${esc(g.title)} · ${rows.length}</div>`;
      for (const n of rows) body += rowFor(n);
    }
    if (!body) body = `<p class="empty">No notes${filter !== 'all' ? ` · ${filter}` : ''}.</p>`;
    c.innerHTML = `<div class="panel">
      <h1>Notes</h1>
      <p class="sub">Every note left on a sentence, code, theme, or source — the pre-flight check before re-coding or rebuilding themes with feedback.</p>
      <div class="filterbar">
        <span class="seg" id="notes-filter">
          <button data-v="all" class="${filter === 'all' ? 'is-active' : ''}">All ${counts.all || ''}</button>
          <button data-v="open" class="${filter === 'open' ? 'is-active' : ''}">Open ${counts.open || ''}</button>
          <button data-v="addressed" class="${filter === 'addressed' ? 'is-active' : ''}">Addressed ${counts.addressed || ''}</button>
          <button data-v="dismissed" class="${filter === 'dismissed' ? 'is-active' : ''}">Dismissed ${counts.dismissed || ''}</button>
        </span>
      </div>
      ${body}
    </div>`;
    $('notes-filter').querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => { S.notesFilter = b.dataset.v; renderContent(); }));
    // re-derive jump targets after the DOM exists (rowFor closures aren't retained across innerHTML)
    for (const n of S.comments) {
      const el = c.querySelector(`[data-jump="${n.id}"]`);
      if (!el) continue;
      el.addEventListener('click', () => {
        if (n.target_type === 'sentence') openDocView(n.doc_id, n.target_id);
        else if (n.target_type === 'code') openCodeView(n.target_id);
        else if (n.target_type === 'theme') { switchView('themes'); select('theme', n.target_id); }
        else if (n.target_type === 'document') openDocView(n.target_id);
      });
    }
    c.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', () => {
        const n = S.comments.find(x => x.id === b.dataset.edit);
        const row = c.querySelector(`[data-nid="${b.dataset.edit}"] .notes-row__main`);
        if (!n || !row) return;
        row.innerHTML = `<textarea rows="3">${esc(n.body)}</textarea>
          <div class="notes-row__meta" style="margin-top:6px">
            <button class="btn-bare" data-save="1">save</button>
            <button class="btn-bare" data-cancel="1">cancel</button>
          </div>`;
        const ta = row.querySelector('textarea'); ta.focus();
        row.querySelector('[data-save]').addEventListener('click', () => {
          if (ta.value.trim()) editNote(n.id, ta.value.trim()).then(() => renderContent());
        });
        row.querySelector('[data-cancel]').addEventListener('click', () => renderContent());
        ta.addEventListener('keydown', e => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') row.querySelector('[data-save]').click();
        });
      }));
    c.querySelectorAll('[data-dismiss]').forEach(b =>
      b.addEventListener('click', () => dismissNote(b.dataset.dismiss).then(() => renderContent())));
    c.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', () => deleteNote(b.dataset.del).then(() => renderContent())));
  }

  // ---- render: analysis overview / dashboard (P4.12) ----------------------------------------------
  async function renderOverview(c) {
    const docs = S.detail.documents;
    const lenses = lensList();
    const byLens = {};
    for (const l of lenses) byLens[l] = S.codes.filter(x => x.coder === l && x.status !== 'rejected').length;
    const codedSentPerDoc = {};
    for (const code of S.codes) {
      if (code.status === 'rejected') continue;
      for (const ev of code.evidence) {
        const [dd, sid] = ev.split('#');
        (codedSentPerDoc[dd] ||= new Set()).add(sid);
      }
    }
    const nThemes = S.themes?.themes.length || 0;
    const stale = S.themes?.stale;
    const openNotes = openCount();
    c.innerHTML = `<div class="panel">
      <h1>Overview</h1>
      <p class="sub">Where the analysis stands — coding coverage, themes, and what's waiting on you.</p>

      <div class="ov-grid">
        <div class="ov-card">
          <h3>Codes per lens</h3>
          ${lenses.length ? lenses.map(l => `
            <div class="ov-lens-row"><span class="lens-dot" style="background:${lensColor(l)}"></span>
              <span class="ov-lens-name">${esc(l)}</span><span class="ov-lens-n">${byLens[l]}</span></div>`).join('')
            : '<p class="empty">No codes yet.</p>'}
        </div>

        <div class="ov-card">
          <h3>Sentence coverage</h3>
          ${docs.length ? docs.map(d => {
            const n = codedSentPerDoc[d.doc_id]?.size || 0;
            const total = d.n_sentences || 1;
            const pct = Math.round(100 * n / total);
            return `<div class="ov-cov-row">
              <div class="ov-cov-label">${esc(docTitle(d))}</div>
              <div class="ov-cov-bar"><div class="ov-cov-bar__fill" style="width:${pct}%"></div></div>
              <div class="ov-cov-n">${n}/${total}</div>
            </div>`;
          }).join('') : '<p class="empty">No sources yet.</p>'}
        </div>

        <div class="ov-card">
          <h3>Themes</h3>
          <p class="ov-big">${nThemes}</p>
          ${stale ? '<p class="hint" style="color:var(--amber)">⟳ out of date — codebook changed since these were built</p>' : ''}
          <button class="btn-quiet" id="ov-go-themes">Open themes</button>
        </div>

        <div class="ov-card">
          <h3>Open notes</h3>
          <p class="ov-big">${openNotes}</p>
          <button class="btn-quiet" id="ov-go-notes">Review notes</button>
        </div>

        <div class="ov-card ov-card--wide">
          <h3>Recent activity</h3>
          <div id="ov-jobs"><p class="empty">Loading…</p></div>
        </div>
      </div>
    </div>`;
    $('ov-go-themes').addEventListener('click', () => switchView('themes'));
    $('ov-go-notes').addEventListener('click', () => switchView('notes'));
    try {
      const jobs = (await API.jobs(S.pid)).slice(0, 8);
      $('ov-jobs').innerHTML = jobs.length ? jobs.map(j => {
        const dur = (j.started_at && j.finished_at)
          ? `${Math.max(1, Math.round((new Date(j.finished_at) - new Date(j.started_at)) / 1000))}s` : '';
        return `<div class="ov-job-row">
          <span class="ov-job-kind">${esc(JOB_LABEL[j.kind] || j.kind)}</span>
          <span class="chip ${j.status === 'failed' ? 'chip--warn' : ''}">${esc(j.status)}</span>
          <span class="ov-job-when">${timeAgo(j.created_at)}</span>
          ${dur ? `<span class="ov-job-dur">${dur}</span>` : ''}
          ${j.status === 'failed' && j.error ? `<span class="ov-job-err" title="${esc(j.error)}">${esc(j.error.slice(0, 60))}</span>` : ''}
        </div>`;
      }).join('') : '<p class="empty">No jobs yet.</p>';
    } catch (e) { $('ov-jobs').innerHTML = `<p class="empty">${esc(String(e.message || e))}</p>`; }
  }

  // ---- render: inspector -------------------------------------------------------------------------
  function notesBlock(type, id, docId, context, recodeHint) {
    const notes = commentsFor(type, id);
    const noteRow = n => `
      <div class="note" data-nid="${n.id}">
        <p>${esc(n.body)}</p>
        <div class="note__meta">
          ${n.author ? `<span class="note__author">${esc(n.author)}</span> · ` : ''}
          <span class="note__when">${timeAgo(n.created_at)}</span>
          <span class="note__status note__status--${n.status === 'open' ? 'open' : 'addressed'}">${n.status}</span>
          ${isViewer() ? '' : `<div class="note__actions">
            <button class="btn-bare" data-edit="${n.id}">edit</button>
            <button class="btn-bare" data-del="${n.id}">delete</button>
          </div>`}
        </div>
      </div>`;
    if (isViewer()) {
      return `
        <div class="insp-sec">
          <h3>Notes</h3>
          ${notes.map(noteRow).join('') || '<p class="empty">No notes.</p>'}
          <p class="hint">Notes are read-only in view mode.</p>
        </div>`;
    }
    return `
      <div class="insp-sec">
        <h3>Your notes</h3>
        ${notes.map(noteRow).join('')}
        <div class="note-box">
          <textarea id="note-new" placeholder="Add a note for the model…"></textarea>
          <div class="note-box__foot">
            <span class="hint">${recodeHint}</span>
            <button class="btn-quiet" id="note-add">Add note</button>
          </div>
        </div>
      </div>`;
  }
  function wireNotes(root, type, id, docId, context) {
    if (isViewer()) return;
    root.querySelector('#note-add')?.addEventListener('click', () => {
      addNote(type, id, docId, root.querySelector('#note-new').value, context);
    });
    root.querySelector('#note-new')?.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') root.querySelector('#note-add').click();
    });
    root.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', () => deleteNote(b.dataset.del)));
    root.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', () => {
        const box = root.querySelector(`[data-nid="${b.dataset.edit}"]`);
        const cur = box.querySelector('p').textContent;
        box.innerHTML = `<textarea rows="3">${esc(cur)}</textarea>
          <div class="note__meta"><div class="note__actions">
            <button class="btn-bare" data-save="1">save</button>
            <button class="btn-bare" data-cancel="1">cancel</button></div></div>`;
        const ta = box.querySelector('textarea'); ta.focus();
        box.querySelector('[data-save]').addEventListener('click', () => editNote(b.dataset.edit, ta.value));
        box.querySelector('[data-cancel]').addEventListener('click', renderInspector);
      }));
  }
  function memoBlock(type, id, context) {
    const m = S.memos[`${type}:${id}`];
    const body = (typeof m === 'string' ? m : m?.body) || '';
    const edited = m && typeof m !== 'string' && m.author
      ? `<div class="memo-edited">edited by ${esc(m.author)}${m.updated_at ? ` · ${timeAgo(m.updated_at)}` : ''}</div>` : '';
    if (isViewer()) {
      return `
        <div class="insp-sec memo-box">
          <h3>Memo</h3>
          <textarea id="memo-ta" readonly>${esc(body)}</textarea>
          ${edited}
        </div>`;
    }
    return `
      <div class="insp-sec memo-box">
        <h3>Memo</h3>
        <textarea id="memo-ta" placeholder="Your analytic memo — private, never sent to the model…">${esc(body)}</textarea>
        <div class="saved" id="memo-saved"></div>
        ${edited}
      </div>`;
  }
  function wireMemo(root, type, id, context) {
    if (isViewer()) return;
    const ta = root.querySelector('#memo-ta');
    ta?.addEventListener('input', () =>
      saveMemo(type, id, ta.value, context, root.querySelector('#memo-saved')));
  }

  async function renderInspector() {
    const box = $('inspector');
    if (!S.sel) { box.className = 'inspector is-hidden'; box.innerHTML = ''; return; }
    box.className = 'inspector';
    const { type, id } = S.sel;

    if (type === 'sentence') {
      const text = S.sentText[S.docId]?.[id] || '';
      const codes = codesForSentence(id, S.docId);
      const byLens = {};
      for (const c of codes) (byLens[c.coder] ||= []).push(c);
      const lenses = lensList().filter(l => byLens[l]);
      box.innerHTML = `
        <div class="insp-head"><span class="sid">${esc(id)}</span><h2>Sentence</h2>
          <button class="insp-close" id="insp-x">✕</button></div>
        <blockquote class="insp-quote">“${esc(text.replace(/^[A-Z][A-Z0-9 .,'’\-]{0,28}:\s*/, ''))}”</blockquote>
        <div class="insp-sec">
          <h3>Codes${codes.length ? ` · ${codes.length}` : ''}</h3>
          ${codes.length ? lenses.map(l => `
            <div class="lens-name"><span class="lens-dot" style="background:${lensColor(l)}"></span>${esc(l)}</div>
            ${byLens[l].map(c => `
              <button class="code-item" data-cid="${c.id}">
                <div class="code-item__label ${c.status === 'rejected' ? 'is-rejected' : ''}">${esc(label(c))}</div>
                <div class="code-item__type">${c.code_type} · ${c.evidence.length} sentence${c.evidence.length > 1 ? 's' : ''}</div>
              </button>`).join('')}`).join('')
          : '<p class="empty">No codes on this sentence.</p>'}
        </div>
        ${notesBlock('sentence', id, S.docId, { quote: text.slice(0, 120) },
          'Notes ride along with the next re-code of this source — every lens sees them.')}`;
      $('insp-x').addEventListener('click', closeInspector);
      box.querySelectorAll('[data-cid]').forEach(b =>
        b.addEventListener('click', () => openCodeView(b.dataset.cid)));
      wireNotes(box, 'sentence', id, S.docId, { quote: text.slice(0, 120) });
      return;
    }

    if (type === 'code') {
      const c = codeById(id);
      if (!c) { closeInspector(); return; }
      const rejected = c.status === 'rejected';
      const evDocs = c.evidence.map(e => e.split('#')[0]);
      box.innerHTML = `
        <div class="insp-head"><span class="sid">${esc(c.id)}</span><h2>Code</h2>
          <button class="insp-close" id="insp-x">✕</button></div>
        <div class="insp-sec">
          ${S.renaming ? `
            <div class="rename-box">
              <input id="rn-input" type="text" value="${esc(label(c))}">
              <button class="btn-quiet" id="rn-save">Save</button>
            </div>` : `
            <div class="insp-title-row">
              <h1 class="insp-title ${rejected ? 'is-rejected' : ''}">${esc(label(c))}</h1>
            </div>`}
          ${c.researcher_label ? `<p class="orig-label">machine label: ${esc(c.label)}</p>` : ''}
          <p class="code-item__type" style="margin-bottom:8px">${c.code_type} · ${esc(c.coder)} · ${c.evidence.length} sentence${c.evidence.length > 1 ? 's' : ''}${rejected ? ' · <b style="color:var(--red)">rejected</b>' : ''}</p>
          ${c.family_id && famById(c.family_id) ? `<p style="margin:0 0 8px"><button class="chip" data-fam-chip="${c.family_id}" style="cursor:pointer"><span class="lens-dot" style="background:${famColor(famById(c.family_id).hue)}"></span>${esc(famById(c.family_id).label)}</button></p>` : ''}
          <p class="insp-def">${esc(c.definition)}</p>
        </div>
        ${isViewer() ? '' : `
        <div class="insp-sec insp-actions">
          ${S.renaming ? '' : `<button class="btn-quiet" id="act-rename">Rename</button>`}
          <button class="btn-quiet" id="act-reject">${rejected ? 'Restore' : 'Reject'}</button>
        </div>`}
        <div class="insp-sec">
          <h3>Evidence</h3>
          <div id="ev-list"><p class="empty">Loading quotes…</p></div>
        </div>
        ${c.model_rationale ? `<div class="insp-sec"><h3>Model rationale</h3><p class="rationale">${esc(c.model_rationale)}</p></div>` : ''}
        ${memoBlock('code', c.id, { label: label(c) })}
        ${notesBlock('code', c.id, c.origin_doc_id, { label: label(c) },
          'Notes ride along when this source is re-coded. Rename/reject are also passed on.')}`;
      $('insp-x').addEventListener('click', closeInspector);
      box.querySelector('[data-fam-chip]')?.addEventListener('click', () => select('family', c.family_id));
      $('act-rename')?.addEventListener('click', () => { S.renaming = true; renderInspector(); });
      $('rn-save')?.addEventListener('click', () => {
        const v = $('rn-input').value.trim();
        if (v && v !== c.label) reviseCode(c.id, 'rename', v); else { S.renaming = false; renderInspector(); }
      });
      $('rn-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') $('rn-save').click(); });
      $('act-reject')?.addEventListener('click', () => reviseCode(c.id, rejected ? 'restore' : 'reject'));
      wireMemo(box, 'code', c.id, { label: label(c) });
      wireNotes(box, 'code', c.id, c.origin_doc_id, { label: label(c) });
      await ensureDocsLoaded(evDocs);
      const evEl = box.querySelector('#ev-list');
      if (evEl) {
        evEl.innerHTML = c.evidence.map(e => {
          const [dd, sid] = e.split('#');
          const t = (S.sentText[dd] || {})[sid] || '';
          return `<button class="ev-item" data-doc="${dd}" data-sid="${sid}" data-tip-doc="${dd}" data-tip-sid="${sid}">
            <span class="sid">${esc(sidChipLabel(dd, sid))}</span>${esc(t.slice(0, 160))}${t.length > 160 ? '…' : ''}</button>`;
        }).join('');
        evEl.querySelectorAll('[data-doc]').forEach(b =>
          b.addEventListener('click', () => openDocView(b.dataset.doc, b.dataset.sid)));
      }
      return;
    }

    if (type === 'theme') {
      const t = (S.themes?.themes || []).find(x => x.id === id);
      if (!t) { closeInspector(); return; }
      box.innerHTML = `
        <div class="insp-head"><span class="sid">${esc(t.id)}</span><h2>Theme</h2>
          <button class="insp-close" id="insp-x">✕</button></div>
        <blockquote class="insp-quote">${esc(t.central_concept)}</blockquote>
        <div class="insp-sec">
          <p class="code-item__type">${esc(t.coverage || '')} sources · ${esc(t.claim_scope || '')} · ${(t.supporting_code_ids || []).length} supporting codes</p>
        </div>
        ${memoBlock('theme', t.id, { claim: t.central_concept })}
        ${notesBlock('theme', t.id, null, { claim: t.central_concept },
          'Notes are considered when themes are rebuilt with feedback.')}`;
      $('insp-x').addEventListener('click', closeInspector);
      wireMemo(box, 'theme', t.id, { claim: t.central_concept });
      wireNotes(box, 'theme', t.id, null, { claim: t.central_concept });
      return;
    }

    if (type === 'family') {
      const f = famById(id);
      if (!f) { closeInspector(); return; }
      box.innerHTML = `
        <div class="insp-head"><span class="sid">${esc(f.id)}</span><h2>Code family</h2>
          <button class="insp-close" id="insp-x">✕</button></div>
        <div class="insp-sec">
          <div class="insp-title-row">
            <span class="lens-dot" style="background:${famColor(f.hue)};align-self:center"></span>
            <h1 class="insp-title">${esc(f.label)}</h1>
          </div>
          <p class="insp-def">${esc(f.definition)}</p>
          <p class="code-item__type" style="margin-top:6px">${f.n_codes} code${f.n_codes === 1 ? '' : 's'}</p>
        </div>
        ${memoBlock('family', f.id, { label: f.label })}
        ${notesBlock('family', f.id, null, { label: f.label },
          'Notes are considered when the codebook is re-consolidated.')}`;
      $('insp-x').addEventListener('click', closeInspector);
      wireMemo(box, 'family', f.id, { label: f.label });
      wireNotes(box, 'family', f.id, null, { label: f.label });
    }
  }

  // ---- sid-chip hover preview (F5/P2.7) -----------------------------------------------------------
  // One shared tooltip div, positioned near whichever [data-tip-doc][data-tip-sid] chip is
  // hovered (anchors, evidence rows, friction chips). Quiet styling like .insp-quote. The quote
  // may need a doc load first — show "…" then fill in async, matching S.sentText's cache shape.
  let _tipEl = null;
  function _tip() {
    if (!_tipEl) { _tipEl = document.createElement('div'); _tipEl.className = 'sid-tip'; document.body.appendChild(_tipEl); }
    return _tipEl;
  }
  function _hideTip() { if (_tipEl) _tipEl.style.display = 'none'; }
  async function _showTip(el) {
    const { tipDoc: docId, tipSid: sid } = el.dataset;
    if (!docId || !sid) return;
    const tip = _tip();
    const d = docById(docId);
    const cached = (S.sentText[docId] || {})[sid];
    tip.innerHTML = `<div class="sid-tip__src">${esc(d ? docTitle(d) : docId)} · ${esc(sid)}</div>
      <div class="sid-tip__q">${cached ? esc(cached) : '…'}</div>`;
    const r = el.getBoundingClientRect();
    tip.style.display = 'block';
    tip.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 340))}px`;
    tip.style.top = `${r.bottom + 6}px`;
    if (!cached) {
      await ensureDocsLoaded([docId]);
      if (tip.style.display !== 'none' && el.dataset.tipSid === sid) {
        const text = (S.sentText[docId] || {})[sid] || '';
        tip.querySelector('.sid-tip__q').textContent = text || '(sentence unavailable)';
      }
    }
  }
  document.addEventListener('mouseover', e => {
    const el = e.target.closest('[data-tip-doc][data-tip-sid]');
    if (el) _showTip(el);
  });
  document.addEventListener('mouseout', e => {
    const el = e.target.closest('[data-tip-doc][data-tip-sid]');
    if (el && !el.contains(e.relatedTarget)) _hideTip();
  });
  document.addEventListener('scroll', _hideTip, true);

  // ---- root render -------------------------------------------------------------------------------
  function render() { renderSidebar(); renderToolbar(); renderContent(); renderInspector(); }

  // ---- init --------------------------------------------------------------------------------------
  async function init() {
    try { const me = await API.me(); S.role = me.role; S.pinRequired = !!me.pin_required; }
    catch (e) { S.role = 'editor'; S.pinRequired = false; }
    try { S.packs = await API.packs(); } catch (e) { S.packs = []; }
    const qp = new URLSearchParams(location.search);
    const pid = qp.get('project');
    if (pid) {
      try {
        await loadProject(pid, false, { doc: qp.get('doc'), view: qp.get('view') });
        return;
      } catch (e) { console.error(e); }
    }
    S.view = 'home';
    render();
  }
  document.addEventListener('DOMContentLoaded', init);
})();
