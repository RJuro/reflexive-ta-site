/* MASSHINE API client — thin fetch wrappers over the FastAPI backend + job polling. */
(function () {
  'use strict';

  async function jget(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
    return r.json();
  }
  async function jpost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${await r.text()}`);
    return r.json();
  }

  const API = {
    packs: () => jget('/packs'),
    projects: () => jget('/projects'),
    project: (pid) => jget(`/projects/${pid}`),
    createProject: (name, pack_id) => jpost('/projects', { name, pack_id }),
    async upload(pid, file) {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch(`/projects/${pid}/documents`, { method: 'POST', body: fd });
      if (!r.ok) throw new Error(`upload → ${r.status}: ${await r.text()}`);
      return r.json();
    },
    document: (pid, doc) => jget(`/projects/${pid}/documents/${doc}`),
    codes: (pid, q) => {
      const s = new URLSearchParams(q || {}).toString();
      return jget(`/projects/${pid}/codes${s ? '?' + s : ''}`);
    },
    friction: (pid, doc) => jget(`/projects/${pid}/friction/${doc}`),
    themes: (pid, mode) => jget(`/projects/${pid}/themes?mode=${mode}`),
    runCoding: (pid, mode, recode) => jpost(`/projects/${pid}/code`, { mode, recode: !!recode }),
    runThemes: (pid, mode) => jpost(`/projects/${pid}/themes`, { mode }),
    job: (id) => jget(`/jobs/${id}`),
    jobs: (pid) => jget(`/projects/${pid}/jobs`)
  };

  // Poll a job to a terminal state, calling onProgress(job) on each tick (every 2s).
  async function pollJob(id, onProgress) {
    for (;;) {
      const j = await API.job(id);
      if (onProgress) onProgress(j);
      if (j.status === 'done' || j.status === 'failed' || j.status === 'interrupted') return j;
      await new Promise((res) => setTimeout(res, 2000));
    }
  }

  window.MASSHINE_API = API;
  window.pollJob = pollJob;
})();
