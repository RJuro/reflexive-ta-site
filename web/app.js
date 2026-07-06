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
    view: 'home',              // 'home' | 'doc' | 'codebook' | 'themes' | 'friction'
    docId: null,
    doc: null,                 // {title, meta, turns:[{speaker, sentences:[{id,text}]}]}
    sentText: {},              // docId → {sid → text} (for evidence quotes)
    codes: [], themes: null, friction: null, comments: [], memos: {},
    sel: null,                 // {type:'sentence'|'code'|'theme', id}
    jobs: {},                  // jobId → job row (being watched)
    cb: { q: '', lens: '', type: '', rejected: false },
    renaming: false,
  };

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
  const KINDS = { transcript: 'Interview transcript', fieldnotes: 'Field notes', focusgroup: 'Focus group', document: 'Document', other: 'Other source' };

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
  const doc = () => (S.detail?.documents || []).find(d => d.doc_id === S.docId);
  const anyCoded = () => (S.detail?.documents || []).some(d => (d.status || '').startsWith('coded'));
  const anyUncoded = () => (S.detail?.documents || []).some(d => !(d.status || '').startsWith('coded'));
  const running = () => Object.values(S.jobs).find(j => j.status === 'running' || j.status === 'queued');

  // ---- data loading ----------------------------------------------------------------------------
  async function loadProject(pid, keepView) {
    S.pid = pid;
    const detail = await API.project(pid);
    S.detail = detail;
    S.mode = detail.mode || (detail.project.pack_id ? 'panel' : 'standard');
    const [codes, themes, comments, memos] = await Promise.all([
      API.codes(pid).catch(() => []),
      API.themes(pid, S.mode).catch(() => ({ themes: [], snapshots: [], stale: false })),
      API.comments(pid).catch(() => []),
      API.memos(pid).catch(() => []),
    ]);
    S.codes = codes; S.themes = themes; S.comments = comments;
    S.memos = {};
    for (const m of memos) S.memos[`${m.target_type}:${m.target_id}`] = m.body;
    if (!S.docId || !detail.documents.find(d => d.doc_id === S.docId))
      S.docId = detail.documents[0]?.doc_id || null;
    if (!keepView) S.view = 'doc';
    if (S.docId) await loadDoc(S.docId);
    for (const j of detail.active_jobs || []) watchJob(j.id, j.kind, true);
    history.replaceState(null, '', `?project=${pid}`);
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
      sents.push({ id: s.id, speaker, text });
    }
    S.sentText[docId] = map;
    const turns = [];
    for (const s of sents) {
      const last = turns[turns.length - 1];
      if (last && last.speaker === s.speaker) last.sentences.push(s);
      else turns.push({ speaker: s.speaker, sentences: [s] });
    }
    S.doc = { id: docId, filename: rd.filename, turns, n: sents.length, nSec: rd.sections.length };
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
  const JOB_LABEL = { ingest: 'Reading source', code_standard: 'Coding', code_panel: 'Coding · panel', recode: 'Re-coding with feedback', theme: 'Building themes' };
  function watchJob(jobId, kind, reattach) {
    if (S.jobs[jobId]) return;
    S.jobs[jobId] = { id: jobId, kind, status: 'running', progress: {} };
    if (!reattach) renderToolbar();
    API.pollJob(jobId, j => { S.jobs[jobId] = j; renderToolbar(); }).then(async j => {
      delete S.jobs[jobId];
      if (j.status === 'done') {
        toast(`${JOB_LABEL[kind] || kind} — done`);
        await loadProject(S.pid, true);
      } else {
        toast(`${JOB_LABEL[kind] || kind} failed: ${j.error || j.status}`, true);
        renderToolbar();
      }
    });
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
      await API.addComment(S.pid, { target_type, target_id, doc_id: docId, body: body.trim(), context });
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
      await API.putMemo(S.pid, { target_type: type, target_id: id, body, context });
      S.memos[`${type}:${id}`] = body;
      if (statusEl) { statusEl.textContent = 'Saved'; setTimeout(() => { statusEl.textContent = ''; }, 1600); }
    } catch (e) { toast(String(e.message || e), true); }
  }, 700);

  function switchView(v) { S.view = v; S.sel = null; $('content').scrollTop = 0; render(); }

  function select(type, id) {
    S.sel = { type, id };
    S.renaming = false;
    renderContent();
    renderInspector();
  }
  function closeInspector() { S.sel = null; S.renaming = false; renderContent(); renderInspector(); }

  async function openDocView(docId, sid) {
    S.view = 'doc';
    if (S.docId !== docId || !S.doc || S.doc.id !== docId) { S.docId = docId; await loadDoc(docId); }
    S.sel = sid ? { type: 'sentence', id: sid } : null;
    render();
    if (sid) document.querySelector(`[data-sid="${sid}"]`)?.scrollIntoView({ block: 'center' });
  }
  function openCodeView(cid) {
    S.view = 'codebook';
    S.sel = { type: 'code', id: cid };
    render();
    document.querySelector(`[data-cid="${cid}"]`)?.scrollIntoView({ block: 'center' });
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
    sb.innerHTML = `
      <button class="proj" id="nav-home" title="All projects">
        <span class="proj__name">${esc(S.detail.project.name)}</span><span class="proj__chev">▾</span>
      </button>
      <div>
        <div class="group__label">Sources</div>
        ${docs.map(d => `
          <button class="row ${S.view === 'doc' && d.doc_id === S.docId ? 'is-active' : ''}" data-nav-doc="${d.doc_id}" title="${esc(KINDS[d.kind] || d.kind)}">
            <span class="dot ${(d.status || '').startsWith('coded') ? 'dot--done' : 'dot--plain'}"></span>
            <span class="row__name">${esc((d.filename || d.doc_id).replace(/\.(txt|md)$/i, ''))}</span>
            <span class="row__count">${d.n_sentences}</span>
          </button>`).join('')}
        <button class="row row--quiet" id="nav-add">＋ Add source</button>
      </div>
      <div>
        <div class="group__label">Project</div>
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
      </div>
      <div class="side-foot">${esc(pack?.title || 'Standard coding')}${S.mode === 'panel' ? `<br>${lensList().length || 3} lenses, blind` : ''}</div>`;
    $('nav-home').addEventListener('click', () => switchView('home'));
    $('nav-add').addEventListener('click', openUploadSheet);
    sb.querySelectorAll('[data-nav-doc]').forEach(b =>
      b.addEventListener('click', () => openDocView(b.dataset.navDoc)));
    sb.querySelectorAll('[data-nav]').forEach(b =>
      b.addEventListener('click', () => switchView(b.dataset.nav)));
  }

  // ---- render: toolbar (journey) -----------------------------------------------------------------
  function nextAction() {
    if (!S.detail || running() || S.view === 'home') return null;
    const docs = S.detail.documents;
    if (!docs.length) return { label: 'Add a source', fn: openUploadSheet };
    if (anyUncoded()) return { label: anyCoded() ? 'Code new sources' : 'Run coding', fn: runCoding, hint: 'calls the model · takes minutes' };
    if (S.view === 'doc' && S.docId && openCount(S.docId) > 0)
      return { label: `Re-code with feedback · ${openCount(S.docId)}`, fn: () => runRecode(S.docId), hint: 'your notes ride along' };
    if (!S.themes?.themes.length && anyCoded()) return { label: 'Build themes', fn: () => runThemes(false), hint: 'calls the model · takes minutes' };
    if (S.themes?.stale) return { label: openThemeNotes() ? 'Rebuild themes with feedback' : 'Rebuild themes', fn: () => runThemes(openThemeNotes() > 0) };
    if (openThemeNotes() > 0) return { label: `Rebuild themes with feedback · ${openThemeNotes()}`, fn: () => runThemes(true) };
    return null;
  }

  function renderToolbar() {
    const tb = $('toolbar');
    if (!S.detail) { tb.innerHTML = `<div class="tb-doc"><strong>MASSHINE</strong></div>`; return; }
    const d = doc();
    let left = '';
    if (S.view === 'doc' && d) {
      const coded = (d.status || '').startsWith('coded');
      const themed = S.themes?.themes.length > 0;
      const stale = S.themes?.stale;
      left = `
        <div class="tb-doc">
          <strong>${esc((d.filename || '').replace(/\.(txt|md)$/i, ''))}</strong>
          <div class="pipeline">
            <span class="step"><span class="step__check">✓</span> Uploaded</span><span class="sep">·</span>
            <span class="step ${coded ? '' : 'step--todo'}">${coded ? '<span class="step__check">✓</span>' : '○'} Coded</span><span class="sep">·</span>
            <span class="step ${themed ? (stale ? 'step--warn' : '') : 'step--todo'}">${themed ? (stale ? '⟳' : '<span class="step__check">✓</span>') : '○'} Themes${stale ? ' out of date' : ''}</span>
          </div>
        </div>`;
    } else {
      const titles = { codebook: 'Codebook', themes: 'Themes', friction: 'Standpoint friction', home: 'Projects', doc: esc(S.detail.project.name) };
      left = `<div class="tb-doc"><strong>${titles[S.view] || ''}</strong></div>`;
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
  }

  // ---- render: content ---------------------------------------------------------------------------
  function renderContent() {
    const c = $('content');
    if (S.view === 'home' || !S.detail) return renderHome(c);
    if (S.view === 'doc') return renderDoc(c);
    if (S.view === 'codebook') return renderCodebook(c);
    if (S.view === 'themes') return renderThemes(c);
    if (S.view === 'friction') return renderFriction(c);
  }

  async function renderHome(c) {
    c.innerHTML = `<div class="home">
      <h1>MASSHINE</h1>
      <p class="sub">Reflexive thematic analysis with a standpoint panel — machine sweep, human judgment.</p>
      <div class="home__new">
        <input id="np-name" type="text" placeholder="New project name…">
        <select id="np-pack"><option value="">Standard coding</option>
          ${S.packs.map(p => `<option value="${p.id}">${esc(p.title)} · panel</option>`).join('')}
        </select>
        <button class="primary" id="np-create">Create</button>
      </div>
      <div id="home-list"><p class="empty">Loading projects…</p></div>
    </div>`;
    $('np-create').addEventListener('click', async () => {
      const name = $('np-name').value.trim();
      if (!name) return;
      try {
        const p = await API.createProject(name, $('np-pack').value || null);
        await loadProject(p.id);
      } catch (e) { toast(String(e.message || e), true); }
    });
    try {
      const projs = await API.projects();
      $('home-list').innerHTML = projs.map(p => `
        <button class="proj-card" data-pid="${p.id}">
          <span class="proj-card__name">${esc(p.name)}</span>
          <span class="proj-card__meta">${esc(p.pack_id || 'standard')}</span>
        </button>`).join('') || '<p class="empty">No projects yet — create one above.</p>';
      $('home-list').querySelectorAll('[data-pid]').forEach(b =>
        b.addEventListener('click', () => loadProject(b.dataset.pid)));
    } catch (e) { $('home-list').innerHTML = `<p class="empty">${esc(String(e.message || e))}</p>`; }
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
    for (const code of S.codes) {
      if (code.status === 'rejected') continue;
      for (const ev of code.evidence) {
        const [dd, sid] = ev.split('#');
        if (dd === S.docId) codedSet.add(sid);
      }
    }
    c.innerHTML = `<div class="transcript">
      <div class="doc-head">
        <h1>${esc((d?.filename || '').replace(/\.(txt|md)$/i, ''))}</h1>
        <p>${esc(kindLabel)} · ${S.doc.nSec} sections · ${S.doc.n} sentences${codedSet.size ? ` · ${codedSet.size} coded` : ' · not yet coded'}</p>
      </div>
      <div class="turns">
        ${S.doc.turns.map(t => `
          <div class="turn">
            ${t.speaker ? `<div class="turn__speaker">${esc(t.speaker)}</div>` : ''}
            <div class="turn__body">${t.sentences.map(s => `<span
                class="s ${codedSet.has(s.id) ? 's--coded' : ''} ${S.sel?.type === 'sentence' && S.sel.id === s.id ? 's--active' : ''}"
                data-sid="${s.id}">${esc(s.text)}${noteSids.has(s.id) ? '<span class="note-mark"></span>' : ''}</span>`).join(' ')}
            </div>
          </div>`).join('')}
      </div>
    </div>`;
    c.querySelectorAll('[data-sid]').forEach(el =>
      el.addEventListener('click', () => select('sentence', el.dataset.sid)));
  }

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
    const groups = {};
    for (const x of list) (groups[x.coder] ||= []).push(x);
    const order = lenses.filter(l => groups[l]);
    c.innerHTML = `<div class="panel">
      <h1>Codebook</h1>
      <p class="sub">${S.codes.length} codes${S.mode === 'panel' ? ` from ${lenses.length} blind lenses` : ''}. Rename or reject a code and the model hears about it on the next re-code.</p>
      <div class="filterbar">
        <input type="search" id="cb-q" placeholder="Search codes…" value="${esc(q)}">
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
      </div>
      ${list.length ? order.map(l => `
        <div class="lens-head"><span class="lens-dot" style="background:${lensColor(l)}"></span>${esc(l)} · ${groups[l].length}</div>
        ${groups[l].map(x => {
          const notes = commentsFor('code', x.id).filter(n => n.status === 'open').length;
          return `<button class="code-row ${x.status === 'rejected' ? 'is-rejected' : ''} ${S.sel?.type === 'code' && S.sel.id === x.id ? 'is-active' : ''}" data-cid="${x.id}">
            ${notes ? '<span class="note-dot"></span>' : ''}
            <span class="code-row__label">${esc(label(x))}</span>
            <span class="code-row__meta"><span class="tag">${x.code_type}</span> · ${x.evidence.length}</span>
          </button>`;
        }).join('')}`).join('')
      : `<p class="empty">${S.codes.length ? 'Nothing matches the filter.' : 'No codes yet — run coding first.'}</p>`}
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
  }

  function renderThemes(c) {
    const th = S.themes?.themes || [];
    const lenses = lensList();
    c.innerHTML = `<div class="panel">
      <h1>Themes</h1>
      <p class="sub">Each theme is a claim with its evidence and paradigm provenance — which lenses independently support it. Select a theme to write your memo or leave a note for the model.</p>
      ${S.themes?.stale ? `<div class="banner">⟳ The codebook changed since these themes were built.
        <div class="tb-spacer"></div>
        <button class="btn-quiet" id="th-rebuild">Rebuild themes${openThemeNotes() ? ' with feedback' : ''}</button></div>` : ''}
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
            ${anchors.map(a => { const [dd, sid] = a.split('#'); return `<button class="anchor" data-doc="${dd}" data-sid="${sid}" title="${esc(dd)}">${esc(sid)}</button>`; }).join('')}</div>` : ''}
          ${tensions.length ? `<div class="theme-card__sect"><h4>In tension with</h4>
            ${tensions.map(x => `<button class="anchor" data-code="${x.id}">${esc(label(x))}</button>`).join('')}</div>` : ''}
          ${t.falsified_if ? `<div class="theme-card__sect"><h4>Falsified if</h4><p class="falsif">${esc(t.falsified_if)}</p></div>` : ''}
        </article>`;
      }).join('') : `<p class="empty">No themes yet — ${anyCoded() ? 'build them from the codebook.' : 'run coding first.'}</p>`}
    </div>`;
    $('th-rebuild')?.addEventListener('click', () => runThemes(openThemeNotes() > 0));
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
            <button class="anchor" data-doc="${S.frictionDoc}" data-sid="${f.sid}">${esc(f.sid)}</button>
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

  // ---- render: inspector -------------------------------------------------------------------------
  function notesBlock(type, id, docId, context, recodeHint) {
    const notes = commentsFor(type, id);
    return `
      <div class="insp-sec">
        <h3>Your notes</h3>
        ${notes.map(n => `
          <div class="note" data-nid="${n.id}">
            <p>${esc(n.body)}</p>
            <div class="note__meta">
              <span class="note__status note__status--${n.status === 'open' ? 'open' : 'addressed'}">${n.status}</span>
              <div class="note__actions">
                <button class="btn-bare" data-edit="${n.id}">edit</button>
                <button class="btn-bare" data-del="${n.id}">delete</button>
              </div>
            </div>
          </div>`).join('')}
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
    return `
      <div class="insp-sec memo-box">
        <h3>Memo</h3>
        <textarea id="memo-ta" placeholder="Your analytic memo — private, never sent to the model…">${esc(S.memos[`${type}:${id}`] || '')}</textarea>
        <div class="saved" id="memo-saved"></div>
      </div>`;
  }
  function wireMemo(root, type, id, context) {
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
          <p class="insp-def">${esc(c.definition)}</p>
        </div>
        <div class="insp-sec insp-actions">
          ${S.renaming ? '' : `<button class="btn-quiet" id="act-rename">Rename</button>`}
          <button class="btn-quiet" id="act-reject">${rejected ? 'Restore' : 'Reject'}</button>
        </div>
        <div class="insp-sec">
          <h3>Evidence</h3>
          <div id="ev-list"><p class="empty">Loading quotes…</p></div>
        </div>
        ${c.model_rationale ? `<div class="insp-sec"><h3>Model rationale</h3><p class="rationale">${esc(c.model_rationale)}</p></div>` : ''}
        ${memoBlock('code', c.id, { label: label(c) })}
        ${notesBlock('code', c.id, c.origin_doc_id, { label: label(c) },
          'Notes ride along when this source is re-coded. Rename/reject are also passed on.')}`;
      $('insp-x').addEventListener('click', closeInspector);
      $('act-rename')?.addEventListener('click', () => { S.renaming = true; renderInspector(); });
      $('rn-save')?.addEventListener('click', () => {
        const v = $('rn-input').value.trim();
        if (v && v !== c.label) reviseCode(c.id, 'rename', v); else { S.renaming = false; renderInspector(); }
      });
      $('rn-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') $('rn-save').click(); });
      $('act-reject').addEventListener('click', () => reviseCode(c.id, rejected ? 'restore' : 'reject'));
      wireMemo(box, 'code', c.id, { label: label(c) });
      wireNotes(box, 'code', c.id, c.origin_doc_id, { label: label(c) });
      await ensureDocsLoaded(evDocs);
      const evEl = box.querySelector('#ev-list');
      if (evEl) {
        evEl.innerHTML = c.evidence.map(e => {
          const [dd, sid] = e.split('#');
          const t = (S.sentText[dd] || {})[sid] || '';
          return `<button class="ev-item" data-doc="${dd}" data-sid="${sid}">
            <span class="sid">${esc(sid)}</span>${esc(t.slice(0, 160))}${t.length > 160 ? '…' : ''}</button>`;
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
    }
  }

  // ---- root render -------------------------------------------------------------------------------
  function render() { renderSidebar(); renderToolbar(); renderContent(); renderInspector(); }

  // ---- init --------------------------------------------------------------------------------------
  async function init() {
    try { S.packs = await API.packs(); } catch (e) { S.packs = []; }
    const pid = new URLSearchParams(location.search).get('project');
    if (pid) {
      try { await loadProject(pid); return; } catch (e) { console.error(e); }
    }
    S.view = 'home';
    render();
  }
  document.addEventListener('DOMContentLoaded', init);
})();
