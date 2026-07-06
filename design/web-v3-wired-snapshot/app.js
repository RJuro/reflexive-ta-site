/* MASSHINE workbench — renders the v3 views from live backend data, plus a project shell
   (create / upload / run coding / run themes) with job polling. Data holders are populated by
   adapters that map API payloads onto the shapes the view renderers expect. */
(function () {
  'use strict';
  const API = window.MASSHINE_API;

  // ---- data holders (fed by adapters) --------------------------------------------------------
  let DOC = { title: '', subtitle: '', sentences: [] };
  let ANNOTATIONS = [];
  let COPILOT = {};
  let AUTOPILOT = [];
  let CODES = [];
  let CODEBOOK = [];
  let FRICTION = [];
  let THEMES = [];
  let activeFilters = new Set(['semantic', 'latent']);
  const STATE = { pid: null, docId: null, mode: 'panel', project: null, docs: [] };

  function escapeHTML(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ---- reading view ----------------------------------------------------------------------------
  function annotateText(text, sid) {
    const spans = ANNOTATIONS.filter(a => a.sid === sid).sort((a, b) => a.start - b.start);
    if (!spans.length) return escapeHTML(text);
    let html = '', cursor = 0;
    for (const s of spans) {
      if (cursor < s.start) html += escapeHTML(text.slice(cursor, s.start));
      if (s.start < cursor) continue;
      const hidden = activeFilters.has(s.source) ? '' : ' style="display:none"';
      html += `<span class="code-span code-span--${s.source}" data-code="${s.code}" data-sid="${sid}" data-rationale="${escapeHTML(s.rationale)}" data-source="${s.source}"${hidden}>${escapeHTML(text.slice(s.start, s.end))}</span>`;
      cursor = Math.max(cursor, s.end);
    }
    if (cursor < text.length) html += escapeHTML(text.slice(cursor));
    return html;
  }

  function renderTranscript() {
    const root = document.getElementById('sentences');
    if (!DOC.sentences.length) {
      root.innerHTML = '<p class="empty">No document loaded. Open the Projects panel to upload a transcript and run coding.</p>';
      return;
    }
    root.innerHTML = DOC.sentences.map(s => {
      const sentenceCodes = ANNOTATIONS.filter(a => a.sid === s.id);
      const seen = new Set();
      const strip = sentenceCodes.length ? `
        <div class="sentence__code-strip">
          ${sentenceCodes.filter(a => !seen.has(a.code) && seen.add(a.code)).map(a => {
            const c = CODES.find(x => x.id === a.code);
            return `<span class="badge badge--${a.source}" data-code="${a.code}" data-sid="${s.id}">${c ? escapeHTML(c.label) : a.code}</span>`;
          }).join('')}
        </div>` : '';
      return `
        <p class="sentence" data-sid="${s.id}">
          <span class="sent-id">${s.id}</span>
          <span class="speaker">${escapeHTML(s.speaker)}</span>
          <span class="sent-text">${annotateText(s.text, s.id)}</span>
          ${strip}
        </p>`;
    }).join('');
    root.querySelectorAll('.sentence').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('.code-span, .badge')) return;
        selectSentence(el.dataset.sid);
      });
    });
    root.querySelectorAll('.code-span').forEach(el => {
      el.addEventListener('mouseenter', e => showTooltip(e, el));
      el.addEventListener('mouseleave', hideTooltip);
      el.addEventListener('click', e => { e.stopPropagation(); openCode(el.dataset.code); switchView('codebook'); });
    });
    root.querySelectorAll('.sentence__code-strip .badge').forEach(badge => {
      badge.addEventListener('click', e => { e.stopPropagation(); openCode(badge.dataset.code); switchView('codebook'); });
    });
  }

  function selectSentence(sid) {
    document.querySelectorAll('.sentence').forEach(el => el.classList.toggle('is-active', el.dataset.sid === sid));
    const data = COPILOT[sid];
    const panel = document.getElementById('copilot-content');
    if (!data) {
      panel.innerHTML = '<p class="empty">No coded suggestion for this sentence.</p>';
      return;
    }
    panel.innerHTML = `
      <div class="run-meta"><span class="badge badge--run">${escapeHTML(STATE.mode)}</span><code>${escapeHTML(data.lens)}</code></div>
      ${data.codes.map(c => `
        <div class="suggestion">
          <div class="suggestion__head"><span class="badge badge--${c.type}">${c.type}</span><span class="suggestion__label">${escapeHTML(c.label)}</span></div>
          <p class="suggestion__rationale">${escapeHTML(c.rationale)}</p>
          <p class="suggestion__evidence">Evidence: <strong>${sid}</strong></p>
        </div>`).join('')}`;
  }

  function renderAutopilot() {
    document.getElementById('autopilot-content').innerHTML = AUTOPILOT.length
      ? AUTOPILOT.map(a => `
        <div class="suggestion">
          <div class="suggestion__head"><span class="badge badge--${a.type}">${a.type}</span><span class="suggestion__label">${escapeHTML(a.label)}</span></div>
          <p class="suggestion__evidence">${escapeHTML(a.sid)} · ${escapeHTML((DOC.sentences.find(s => s.id === a.sid)?.text || '').slice(0, 60))}…</p>
          <p class="suggestion__rationale">${escapeHTML(a.rationale)}</p>
        </div>`).join('')
      : '<p class="empty">No codes yet — run coding.</p>';
  }

  // ---- codebook --------------------------------------------------------------------------------
  function renderCodebook() {
    const list = document.getElementById('codebook-list');
    list.innerHTML = CODEBOOK.length ? CODEBOOK.map(c => `
      <div class="code-card" data-cid="${c.id}">
        <div class="suggestion__head"><span class="badge badge--${c.type}">${c.type}</span><span class="code-card__title">${escapeHTML(c.label)}</span></div>
        <div class="code-card__meta">${escapeHTML(c.coder)} · ${c.evidence.length} sentence${c.evidence.length > 1 ? 's' : ''}</div>
      </div>`).join('') : '<p class="empty">No codebook yet.</p>';
    list.querySelectorAll('.code-card').forEach(card => card.addEventListener('click', () => openCode(card.dataset.cid)));
    if (CODEBOOK.length) openCode(CODEBOOK[0].id);
  }

  function openCode(cid) {
    document.querySelectorAll('.code-card').forEach(c => c.classList.toggle('is-active', c.dataset.cid === cid));
    const c = CODEBOOK.find(x => x.id === cid) || CODES.find(x => x.id === cid);
    if (!c) return;
    const tag = c.coder ? `<span class="badge badge--run">${escapeHTML(c.coder)}</span>`
      : (c.status ? `<span class="badge badge--run">${escapeHTML(c.status)}</span>` : '');
    const ev = (c.evidence || []).map(e => `<code>${escapeHTML(e)}</code>`).join(' ');
    document.getElementById('codebook-detail').innerHTML = `
      <div class="detail__header">
        <div class="suggestion__head"><span class="badge badge--${c.type}">${c.type}</span>${tag}</div>
        <h2 class="detail__title">${escapeHTML(c.label)}</h2>
        <p class="detail__def">${escapeHTML(c.definition)}</p>
      </div>
      ${c.quote ? `<div class="detail__section"><h3>Verbatim evidence</h3><blockquote class="detail__quote">${escapeHTML(c.quote)}</blockquote></div>` : ''}
      ${ev ? `<div class="detail__section"><h3>Evidence — sentence IDs, resolved from the index</h3><p style="font-size:.82rem;line-height:2;">${ev}</p></div>` : ''}
      ${c.rationale ? `<div class="detail__section"><h3>Model rationale</h3><p class="detail__memo">${escapeHTML(c.rationale)}</p></div>` : ''}
      <div class="detail__section"><h3>Researcher memo</h3><textarea class="theme-memo" placeholder="Write a memo…">${escapeHTML(c.memo || '')}</textarea></div>`;
  }

  // ---- comparison (standpoint friction) --------------------------------------------------------
  function renderComparison() {
    const body = document.getElementById('comparison-body');
    const coders = [
      { key: 'standard', label: 'Standard' },
      { key: 'critical', label: 'Critical / political-economy' },
      { key: 'phenomenological', label: 'Phenomenological / memory' }
    ];
    if (!FRICTION.length) { body.innerHTML = '<p class="empty">No friction yet — run a standpoint panel (coding mode: panel).</p>'; return; }
    body.innerHTML = FRICTION.map(f => `
      <div class="friction">
        <div class="friction__bar">
          <span class="friction__kind friction__kind--${f.kind}">${f.kind} friction</span>
          <span class="friction__sent">${f.sentence.id}</span>
        </div>
        <blockquote class="friction__quote">“${escapeHTML(f.sentence.text)}”</blockquote>
        ${f.note ? `<p class="friction__note">${escapeHTML(f.note)}</p>` : ''}
        <div class="friction__cols">
          ${coders.map(c => {
            const reads = f.readings[c.key] || [];
            return `<div class="friction__col">
              <div class="friction__coder">${c.label}</div>
              ${reads.length ? reads.map(r => `
                <div class="friction__reading">
                  <div class="suggestion__head"><span class="badge badge--${r.type || 'semantic'}">${r.type || 'code'}</span><span class="suggestion__label">${escapeHTML(r.label)}</span></div>
                  ${r.rationale ? `<p class="suggestion__rationale">${escapeHTML(r.rationale)}</p>` : ''}
                </div>`).join('') : '<p class="friction__silent">— silent here</p>'}
            </div>`;
          }).join('')}
        </div>
      </div>`).join('');
  }

  // ---- themes ----------------------------------------------------------------------------------
  function renderThemes() {
    const kindLabel = { convergent: 'Convergent · 2+ lenses', 'critical-signature': 'Critical-signature', 'phenomenological-signature': 'Phenomenological-signature' };
    const order = ['standard', 'critical', 'phenomenological'];
    const el = document.getElementById('themes-content');
    if (!THEMES.length) { el.innerHTML = '<p class="empty">No themes yet — run coding, then run themes.</p>'; return; }
    el.innerHTML = THEMES.map(t => `
      <article class="theme-card">
        <div class="theme-card__prov">
          <span class="badge badge--run">${escapeHTML(t.id)} · ${escapeHTML(kindLabel[t.kind] || t.kind)}</span>
          ${order.filter(k => t.provenance[k] != null).map(k => `<span class="prov-chip"><strong>${k}</strong> ${t.provenance[k]}</span>`).join('')}
          <span class="prov-chip">${t.tension} tension codes</span>
        </div>
        <p class="theme-card__claim">${escapeHTML(t.claim)}</p>
        ${t.note ? `<p class="theme-card__note">${escapeHTML(t.note)}</p>` : ''}
        <div class="theme-card__section"><h4>Researcher memo</h4><textarea class="theme-memo">${escapeHTML(t.memo)}</textarea></div>
      </article>`).join('');
  }

  // ---- connections -----------------------------------------------------------------------------
  function renderConnections() {
    const groups = {};
    ANNOTATIONS.forEach(a => { (groups[a.code] ||= []).push(a); });
    const el = document.getElementById('connections-content');
    if (!Object.keys(groups).length) { el.innerHTML = '<p class="empty">No coded sentences yet.</p>'; return; }
    el.innerHTML = Object.entries(groups).map(([cid, list]) => {
      const code = CODES.find(c => c.id === cid);
      return `
        <article class="connection-card">
          <div class="connection-card__code"><span class="badge badge--${code?.type || 'semantic'}">${code?.type || 'code'}</span> ${escapeHTML(code?.label || cid)}</div>
          <ul class="connection-card__list">
            ${list.map(a => `<li data-sid="${a.sid}"><strong>${a.sid}</strong> · “${escapeHTML((DOC.sentences.find(s => s.id === a.sid)?.text || '').slice(0, 120))}”</li>`).join('')}
          </ul>
        </article>`;
    }).join('');
    el.querySelectorAll('.connection-card__list li').forEach(li => {
      li.addEventListener('click', () => { switchView('reading'); selectSentence(li.dataset.sid); });
    });
  }

  function switchView(view) {
    document.querySelectorAll('.nav button').forEach(b => b.classList.toggle('is-active', b.dataset.view === view));
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('is-active', v.dataset.view === view));
  }

  // ---- tooltip / selection toolbar (local annotation demo) -------------------------------------
  const toolbar = () => document.getElementById('annotation-toolbar');
  const tooltip = () => document.getElementById('code-tooltip');
  function showTooltip(e, el) {
    const code = CODES.find(c => c.id === el.dataset.code);
    document.getElementById('tooltip-title').innerHTML = `<span class="badge badge--${el.dataset.source}">${el.dataset.source}</span> ${escapeHTML(code?.label || el.dataset.code)}`;
    document.getElementById('tooltip-rationale').textContent = el.dataset.rationale || '';
    const tt = tooltip(); tt.classList.add('is-open');
    const rect = el.getBoundingClientRect(), main = document.querySelector('.main').getBoundingClientRect();
    let left = rect.left; if (left + 280 > main.right) left = main.right - 288;
    tt.style.top = (rect.bottom + 8) + 'px'; tt.style.left = left + 'px';
    document.getElementById('tooltip-open').onclick = () => { openCode(el.dataset.code); switchView('codebook'); };
    document.getElementById('tooltip-dismiss').onclick = hideTooltip;
  }
  function hideTooltip() { tooltip().classList.remove('is-open'); }

  // ---- adapters: API payloads → view shapes ----------------------------------------------------
  function parseSpeaker(text) {
    const m = text.match(/^[\t ]*([A-Z][A-Z0-9 .,'’\-]{1,28}):[\t ]/);
    if (m) return { speaker: m[1].trim(), text: text.slice(m[0].length) };
    return { speaker: '', text: text.replace(/^[\t ]+/, '') };
  }
  function buildDOC(rd) {
    const sentences = [];
    for (const sec of rd.sections) for (const s of sec.sentences) {
      const p = parseSpeaker(s.text);
      sentences.push({ id: s.id, speaker: p.speaker, text: p.text });
    }
    return { title: rd.filename, subtitle: `${rd.sections.length} sections · ${sentences.length} sentences`, sentences };
  }
  const bareSid = e => e.split('#')[1] || e;
  function buildFromCodes(dcodes) {
    CODES = dcodes.map(c => {
      const ev = c.evidence.map(bareSid);
      return { id: c.id, label: c.label, type: c.code_type, evidence: ev, coder: c.coder,
               definition: c.definition, memo: '', rationale: c.model_rationale,
               quote: (DOC.sentences.find(s => s.id === ev[0]) || {}).text || '' };
    });
    ANNOTATIONS = [];
    COPILOT = {};
    for (const c of CODES) for (const sid of c.evidence) {
      const sent = DOC.sentences.find(s => s.id === sid);
      if (sent) ANNOTATIONS.push({ sid, start: 0, end: sent.text.length, code: c.id, source: c.type, rationale: c.rationale });
      (COPILOT[sid] ||= { lens: 'Coded suggestions', codes: [] }).codes.push({ type: c.type, label: c.label, rationale: c.rationale });
    }
    AUTOPILOT = CODES.map(c => ({ sid: c.evidence[0], type: c.type, label: c.label, rationale: c.rationale })).filter(a => a.sid);
  }
  function buildCODEBOOK(all) {
    return all.map(c => ({ id: c.id, coder: c.coder, type: c.code_type, label: c.label,
                           evidence: c.evidence.map(bareSid), definition: c.definition, rationale: c.model_rationale, memo: '' }));
  }
  function buildFriction(fr) {
    const map = r => {
      const out = {};
      for (const k of ['standard', 'critical', 'phenomenological']) out[k] = (r[k] || []).map(x => ({ type: x.type, label: x.label, rationale: '' }));
      return out;
    };
    return fr.friction.map(f => ({ sentence: { id: f.sid, text: f.text }, kind: f.kind, note: '', readings: map(f.readings) }));
  }
  function themeKind(prov) {
    const ks = Object.keys(prov || {});
    if (ks.length >= 2) return 'convergent';
    if (prov && prov.critical) return 'critical-signature';
    if (prov && prov.phenomenological) return 'phenomenological-signature';
    return 'convergent';
  }
  function buildThemes(th) {
    return th.themes.map(t => ({
      id: t.id, kind: themeKind(t.paradigm_provenance), tension: (t.tensions || []).length,
      claim: t.central_concept, provenance: t.paradigm_provenance || {},
      note: '', memo: t.falsified_if ? ('Falsified if: ' + t.falsified_if) : ('Coverage ' + (t.coverage || ''))
    }));
  }

  // ---- loading ---------------------------------------------------------------------------------
  function setTopbar(title, sub) {
    const box = document.querySelector('.topbar__doc');
    if (box) box.innerHTML = `<strong>${escapeHTML(title)}</strong><span>${escapeHTML(sub)}</span>`;
  }
  function setStatus(counts, nThemes) {
    const rows = document.querySelectorAll('.status .status__row strong');
    const order = ['standard', 'critical', 'phenomenological'];
    const cs = order.filter(k => counts[k] != null).map(k => counts[k]).join(' / ') || '0';
    if (rows[1]) rows[1].textContent = cs;
    if (rows[2]) rows[2].textContent = String(nThemes);
    // nav counts
    const nav = document.querySelectorAll('.nav .nav__count');
    if (nav[0]) nav[0].textContent = CODEBOOK.length ? String(new Set(ANNOTATIONS.map(a => a.sid)).size) : '';
    if (nav[1]) nav[1].textContent = String(CODEBOOK.length);
    if (nav[3]) nav[3].textContent = String(nThemes);
  }
  function renderAll() {
    [renderTranscript, renderAutopilot, renderCodebook, renderComparison, renderThemes, renderConnections]
      .forEach(fn => { try { fn(); } catch (e) { console.error('[render]', e); } });
    const firstCoded = ANNOTATIONS[0]?.sid || DOC.sentences[0]?.id;
    if (firstCoded) selectSentence(firstCoded);
  }
  async function loadDocData() {
    if (!STATE.docId) { DOC = { title: STATE.project.name, subtitle: 'no documents yet', sentences: [] }; CODES = ANNOTATIONS = AUTOPILOT = FRICTION = []; COPILOT = {}; return; }
    DOC = buildDOC(await API.document(STATE.pid, STATE.docId));
    buildFromCodes(await API.codes(STATE.pid, { doc_id: STATE.docId }));
    try { FRICTION = buildFriction(await API.friction(STATE.pid, STATE.docId)); } catch (e) { FRICTION = []; }
  }
  async function loadProject(pid) {
    STATE.pid = pid;
    const detail = await API.project(pid);
    STATE.project = detail.project; STATE.docs = detail.documents;
    STATE.mode = detail.code_counts && detail.code_counts.critical ? 'panel' : 'standard';
    if (!STATE.docId || !STATE.docs.find(d => d.doc_id === STATE.docId)) STATE.docId = (STATE.docs[0] || {}).doc_id || null;
    await loadDocData();
    try { CODEBOOK = buildCODEBOOK(await API.codes(pid)); } catch (e) { CODEBOOK = []; }
    let th = { themes: [] };
    try { th = await API.themes(pid, STATE.mode); } catch (e) {}
    THEMES = buildThemes(th);
    setTopbar(detail.project.name, DOC.subtitle || `${STATE.docs.length} document(s)`);
    setStatus(detail.code_counts || {}, THEMES.length);
    renderAll();
    history.replaceState(null, '', `?project=${pid}`);
  }

  // ---- project shell ---------------------------------------------------------------------------
  function injectShellStyles() {
    const css = `
    .shell{position:fixed;inset:0;background:oklch(20% 0.02 250 / .55);backdrop-filter:blur(3px);display:none;z-index:100;align-items:flex-start;justify-content:center;padding:4rem 1rem;overflow:auto}
    .shell.is-open{display:flex}
    .shell__panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);width:min(760px,100%);padding:1.5rem}
    .shell__panel header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem}
    .shell__panel h1{font-family:var(--font-display);font-size:1.3rem}
    .shell__new{display:flex;gap:.5rem;margin-bottom:1rem;flex-wrap:wrap}
    .shell__new input,.shell__new select{padding:.5rem .6rem;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
    .shell__new input{flex:1;min-width:180px}
    .shell__list{display:flex;flex-direction:column;gap:.35rem;margin-bottom:1rem}
    .shell__proj{display:flex;justify-content:space-between;align-items:center;padding:.55rem .7rem;border:1px solid var(--border);border-radius:8px;cursor:pointer;background:var(--bg)}
    .shell__proj:hover{border-color:var(--accent)}
    .shell__proj.is-active{border-color:var(--accent);background:var(--accent-soft)}
    .shell__proj small{color:var(--muted)}
    .shell__detail{border-top:1px solid var(--border);padding-top:1rem}
    .shell__docs{display:flex;flex-direction:column;gap:.3rem;margin:.5rem 0}
    .shell__doc{display:flex;justify-content:space-between;padding:.45rem .6rem;border:1px solid var(--border);border-radius:8px;cursor:pointer;font-size:.85rem;background:var(--bg)}
    .shell__doc:hover{border-color:var(--accent)}
    .shell__doc.is-active{border-color:var(--accent);background:var(--accent-soft)}
    .shell__actions{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:.75rem 0}
    .shell__jobs{display:flex;flex-direction:column;gap:.3rem;font-size:.8rem}
    .shell__job{display:flex;justify-content:space-between;padding:.35rem .5rem;border-radius:6px;background:var(--bg);border:1px solid var(--border)}
    .shell__job.running{border-color:var(--accent)} .shell__job.failed{border-color:var(--latent)}
    .shell__hint{color:var(--muted);font-size:.8rem;margin:.25rem 0}`;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  }

  function buildShellDOM() {
    const el = document.createElement('div');
    el.className = 'shell'; el.id = 'shell';
    el.innerHTML = `
      <div class="shell__panel">
        <header><h1>MASSHINE — projects</h1><button class="button" id="shell-close">Close</button></header>
        <div class="shell__new">
          <input id="np-name" placeholder="New project name…">
          <select id="np-pack"></select>
          <button class="button button--primary" id="np-create">Create</button>
        </div>
        <div class="shell__list" id="shell-projects"></div>
        <div class="shell__detail" id="shell-detail"><p class="shell__hint">Select or create a project.</p></div>
      </div>`;
    document.body.appendChild(el);
    return el;
  }

  let PACKS = [];
  async function refreshProjects() {
    const box = document.getElementById('shell-projects');
    const projs = await API.projects();
    box.innerHTML = projs.map(p => `
      <div class="shell__proj ${p.id === STATE.pid ? 'is-active' : ''}" data-pid="${p.id}">
        <span>${escapeHTML(p.name)}</span>
        <small>${escapeHTML(p.pack_id || 'standard')} · ${p.id}</small>
      </div>`).join('') || '<p class="shell__hint">No projects yet — create one.</p>';
    box.querySelectorAll('.shell__proj').forEach(row =>
      row.addEventListener('click', () => openProjectInShell(row.dataset.pid)));
  }

  async function openProjectInShell(pid) {
    STATE.pid = pid;
    await refreshProjects();
    const detail = await API.project(pid);
    const d = document.getElementById('shell-detail');
    const docs = detail.documents;
    const busy = detail.active_jobs.length > 0;
    d.innerHTML = `
      <h3 style="margin-bottom:.5rem;">${escapeHTML(detail.project.name)}</h3>
      <div class="shell__hint">Documents (${docs.length}) — click one to open in the workbench:</div>
      <div class="shell__docs">
        ${docs.map(x => `<div class="shell__doc ${x.doc_id === STATE.docId ? 'is-active' : ''}" data-doc="${x.doc_id}">
          <span>${escapeHTML(x.filename || x.doc_id)}</span>
          <small>${x.n_sentences} sentences · ${escapeHTML(x.status || '')}</small></div>`).join('') || '<p class="shell__hint">No documents — upload a .txt below.</p>'}
      </div>
      <div class="shell__actions">
        <label class="button">Upload .txt<input type="file" accept=".txt" id="up-file" style="display:none"></label>
        <button class="button" id="run-standard">Run coding · standard</button>
        <button class="button" id="run-panel">Run coding · panel</button>
        <button class="button" id="run-themes">Run themes</button>
        ${busy ? '<span class="shell__hint">a job is running…</span>' : ''}
      </div>
      <div class="shell__jobs" id="shell-jobs"></div>`;
    d.querySelectorAll('.shell__doc').forEach(row => row.addEventListener('click', () => {
      STATE.docId = row.dataset.doc; closeShell(); loadProject(pid);
    }));
    document.getElementById('up-file').addEventListener('change', async e => {
      const file = e.target.files[0]; if (!file) return;
      const { job_id } = await API.upload(pid, file);
      watchJob(pid, job_id, 'ingest ' + file.name);
    });
    const detectMode = () => detail.code_counts && detail.code_counts.critical ? 'panel' : 'standard';
    document.getElementById('run-standard').addEventListener('click', async () => {
      const { job_id } = await API.runCoding(pid, 'standard'); watchJob(pid, job_id, 'coding · standard');
    });
    document.getElementById('run-panel').addEventListener('click', async () => {
      const { job_id } = await API.runCoding(pid, 'panel'); watchJob(pid, job_id, 'coding · panel');
    });
    document.getElementById('run-themes').addEventListener('click', async () => {
      const { job_id } = await API.runThemes(pid, detectMode()); watchJob(pid, job_id, 'themes');
    });
    await refreshJobs(pid);
  }

  async function refreshJobs(pid) {
    const box = document.getElementById('shell-jobs'); if (!box) return;
    const jobs = await API.jobs(pid);
    box.innerHTML = jobs.slice(0, 6).map(j => {
      const p = j.progress || {};
      const detail = j.status === 'running' && p.total ? ` ${p.stage || ''} ${p.done || 0}/${p.total}` : (j.error ? ' ' + j.error.slice(0, 50) : '');
      return `<div class="shell__job ${j.status}"><span>${escapeHTML(j.kind)}${escapeHTML(detail)}</span><span>${j.status}</span></div>`;
    }).join('');
  }

  async function watchJob(pid, jobId, label) {
    const status = document.createElement('div');
    await refreshJobs(pid);
    pollJob(jobId, () => refreshJobs(pid)).then(j => {
      refreshJobs(pid);
      if (j.status === 'done') { openProjectInShell(pid); if (pid === STATE.pid) loadProject(pid); }
    });
  }

  function openShell() { document.getElementById('shell').classList.add('is-open'); refreshProjects(); if (STATE.pid) openProjectInShell(STATE.pid); }
  function closeShell() { document.getElementById('shell').classList.remove('is-open'); }

  // ---- init ------------------------------------------------------------------------------------
  async function init() {
    injectShellStyles();
    buildShellDOM();
    document.getElementById('shell-close').addEventListener('click', closeShell);
    try { PACKS = await API.packs(); } catch (e) { PACKS = []; }
    document.getElementById('np-pack').innerHTML =
      '<option value="">standard (no pack)</option>' +
      PACKS.map(p => `<option value="${p.id}">${escapeHTML(p.title)} (panel)</option>`).join('');
    document.getElementById('np-create').addEventListener('click', async () => {
      const name = document.getElementById('np-name').value.trim(); if (!name) return;
      const pack = document.getElementById('np-pack').value || null;
      const p = await API.createProject(name, pack);
      document.getElementById('np-name').value = '';
      await refreshProjects(); openProjectInShell(p.id);
    });

    // topbar Projects button
    const btn = document.createElement('button');
    btn.className = 'button'; btn.textContent = 'Projects'; btn.id = 'projects-toggle';
    btn.addEventListener('click', openShell);
    const audit = document.getElementById('audit-toggle');
    audit.parentElement.insertBefore(btn, audit);

    // nav / mode / audit / tweaks / filters (from the mockup)
    document.querySelectorAll('.nav [data-view]').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));
    document.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('is-active', x === b));
      document.getElementById('reading-layout').className = 'reading is-' + b.dataset.mode;
    }));
    const drawer = document.getElementById('audit-drawer');
    document.getElementById('audit-toggle').addEventListener('click', () => drawer.classList.toggle('is-open'));
    const tweaks = document.getElementById('tweaks-panel');
    document.getElementById('tweaks-toggle')?.addEventListener('click', () => tweaks.classList.toggle('is-open'));
    document.getElementById('tweaks-close')?.addEventListener('click', () => tweaks.classList.remove('is-open'));
    document.querySelectorAll('.filter-chip').forEach(chip => chip.addEventListener('click', () => {
      const k = chip.dataset.filter;
      if (activeFilters.has(k)) activeFilters.delete(k); else activeFilters.add(k);
      chip.classList.toggle('is-active', activeFilters.has(k));
      renderTranscript();
    }));

    const pid = new URLSearchParams(location.search).get('project');
    if (pid) { try { await loadProject(pid); return; } catch (e) { console.error(e); } }
    openShell();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
