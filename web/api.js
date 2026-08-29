/* MASSHINE API client — thin fetch wrappers over the FastAPI backend (same origin). */
window.MASSHINE_API = (() => {
  'use strict';
  const j = async (url, opts) => {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* keep statusText */ }
      throw new Error(msg);
    }
    return r.json();
  };
  const json = (method, body) => ({
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });

  const api = {
    me: () => j('/me'),
    packs: () => j('/packs'),
    projects: (archived = false) => j('/projects' + (archived ? '?archived=1' : '')),
    createProject: (name, pack_id) => j('/projects', json('POST', { name, pack_id })),
    project: pid => j(`/projects/${pid}`),
    patchProject: (pid, payload) => j(`/projects/${pid}`, json('PATCH', payload)),
    deleteProject: pid => j(`/projects/${pid}`, { method: 'DELETE' }),

    upload: (pid, file, kind = 'transcript') => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('kind', kind);
      return j(`/projects/${pid}/documents`, { method: 'POST', body: fd });
    },
    document: (pid, doc) => j(`/projects/${pid}/documents/${doc}`),
    patchDocument: (pid, doc, title) => j(`/projects/${pid}/documents/${doc}`, json('PATCH', { title })),
    deleteDocument: (pid, doc) => j(`/projects/${pid}/documents/${doc}`, { method: 'DELETE' }),

    codes: (pid, params = {}) => j(`/projects/${pid}/codes?` + new URLSearchParams(params)),
    friction: (pid, doc) => j(`/projects/${pid}/friction/${doc}`),
    themes: (pid, mode) => j(`/projects/${pid}/themes?mode=${mode}`),

    runCoding: (pid, mode) => j(`/projects/${pid}/code`, json('POST', { mode })),
    runRead: (pid, span) => j(`/projects/${pid}/read`, json('POST', { span: span || null })),

    uploadAudio: (pid, file) => {
      const fd = new FormData();
      fd.append('file', file);
      return j(`/projects/${pid}/audio`, { method: 'POST', body: fd });
    },
    audioTranscript: (pid, stem) => j(`/projects/${pid}/audio/${encodeURIComponent(stem)}/transcript`),
    runThemes: (pid, mode, feedback = false) =>
      j(`/projects/${pid}/themes`, json('POST', { mode, feedback })),
    recode: (pid, doc_id, mode) => j(`/projects/${pid}/recode`, json('POST', { doc_id, mode })),

    comments: (pid, params = {}) => j(`/projects/${pid}/comments?` + new URLSearchParams(params)),
    addComment: (pid, payload) => j(`/projects/${pid}/comments`, json('POST', payload)),
    editComment: (pid, cid, payload) => j(`/projects/${pid}/comments/${cid}`, json('PATCH', payload)),
    deleteComment: (pid, cid) => j(`/projects/${pid}/comments/${cid}`, { method: 'DELETE' }),

    memos: (pid, target_type) =>
      j(`/projects/${pid}/memos` + (target_type ? `?target_type=${target_type}` : '')),
    putMemo: (pid, payload) => j(`/projects/${pid}/memos`, json('PUT', payload)),

    revise: (pid, code_id, action, new_label) =>
      j(`/projects/${pid}/codes/${code_id}/revise`, json('POST', { action, new_label })),
    setCodeFamily: (pid, code_id, family_id) =>
      j(`/projects/${pid}/codes/${code_id}/family`, json('PATCH', { family_id })),

    reviseTheme: (pid, theme_id, action, mode, value) =>
      j(`/projects/${pid}/themes/${theme_id}/revise`, json('POST', { action, mode, value })),

    consolidate: pid => j(`/projects/${pid}/consolidate`, { method: 'POST' }),
    families: pid => j(`/projects/${pid}/families`),

    compress: pid => j(`/projects/${pid}/compress`, { method: 'POST' }),
    mergeProposals: (pid, status) =>
      j(`/projects/${pid}/merge-proposals` + (status ? `?status=${status}` : '')),
    acceptProposal: (pid, mpid) =>
      j(`/projects/${pid}/merge-proposals/${mpid}/accept`, { method: 'POST' }),
    dismissProposal: (pid, mpid) =>
      j(`/projects/${pid}/merge-proposals/${mpid}/dismiss`, { method: 'POST' }),

    login: pin => j('/auth/pin', json('POST', { pin })),
    logout: () => j('/auth/logout', { method: 'POST' }),

    job: id => j(`/jobs/${id}`),
    jobs: pid => j(`/projects/${pid}/jobs`),

    // P10.2 — SYNTHESIZE, the loop mechanisms (design/P10.2-CONTRACT.md §4). The engine for these
    // is being built in parallel; every call here is meant to be wrapped in .catch() by the caller
    // so a 404/500 degrades the panel quietly instead of breaking the app.
    synthesize: (pid, payload = {}) => j(`/projects/${pid}/synthesize`, json('POST', payload)),
    session: (pid, docId) => j(`/projects/${pid}/session/${docId}`),
    stepReact: (pid, stepId, payload) => j(`/projects/${pid}/steps/${stepId}/react`, json('POST', payload)),
    journal: pid => j(`/projects/${pid}/journal`),
    needsJudgment: pid => j(`/projects/${pid}/needs-judgment`),
    setFocus: (pid, text) => j(`/projects/${pid}/focus`, json('POST', { text })),
    focusProposal: (pid, n, decision) =>
      j(`/projects/${pid}/focus/proposal/${n}`, json('POST', { decision })),
    evidenceOpened: (pid, tid, sid) =>
      j(`/projects/${pid}/findings/${tid}/evidence-opened`, json('POST', { sid })),
    reframeResidue: (pid, idx) => j(`/projects/${pid}/residue/${idx}/reframe`, json('POST', {})),
  };

  api.models = () => j('/models');

  api.pollJob = (id, onTick) => new Promise(resolve => {
    const t = setInterval(async () => {
      try {
        const jb = await api.job(id);
        if (onTick) onTick(jb);
        if (['done', 'failed', 'interrupted'].includes(jb.status)) { clearInterval(t); resolve(jb); }
      } catch (e) { clearInterval(t); resolve({ status: 'failed', error: String(e) }); }
    }, 2000);
  });

  return api;
})();
