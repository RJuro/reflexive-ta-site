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
    packs: () => j('/packs'),
    projects: () => j('/projects'),
    createProject: (name, pack_id) => j('/projects', json('POST', { name, pack_id })),
    project: pid => j(`/projects/${pid}`),

    upload: (pid, file, kind = 'transcript') => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('kind', kind);
      return j(`/projects/${pid}/documents`, { method: 'POST', body: fd });
    },
    document: (pid, doc) => j(`/projects/${pid}/documents/${doc}`),

    codes: (pid, params = {}) => j(`/projects/${pid}/codes?` + new URLSearchParams(params)),
    friction: (pid, doc) => j(`/projects/${pid}/friction/${doc}`),
    themes: (pid, mode) => j(`/projects/${pid}/themes?mode=${mode}`),

    runCoding: (pid, mode) => j(`/projects/${pid}/code`, json('POST', { mode })),
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

    job: id => j(`/jobs/${id}`),
    jobs: pid => j(`/projects/${pid}/jobs`),
  };

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
