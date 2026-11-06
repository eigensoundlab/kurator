// Thin wrapper around the local HTTP API.

async function req(method, url, body, signal) {
  const opt = { method, signal, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    if (typeof msg !== 'string') msg = JSON.stringify(msg);
    throw new Error(msg);
  }
  return r.json();
}

/** POST that returns a picture (previews): { url, headers }. */
async function postImage(url, body, signal) {
  const r = await fetch(url, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return { url: URL.createObjectURL(await r.blob()), headers: r.headers };
}

export const api = {
  state: () => req('GET', '/api/state'),
  folders: (project) => req('GET', '/api/folders' + (project ? `?project=${project}` : '')),
  items: (params, signal) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '' && v !== false) q.set(k, v === true ? 1 : v);
    return req('GET', '/api/items?' + q, undefined, signal);
  },
  item: (id, signal) => req('GET', `/api/items/${id}`, undefined, signal),
  addRoot: (path) => req('POST', '/api/roots', { path }),
  removeRoot: (id) => req('DELETE', `/api/roots/${id}`),
  scan: () => req('POST', '/api/scan', {}),
  // browsing & pins
  places: () => req('GET', '/api/fs/places'),
  listDir: (path) => req('GET', '/api/fs/list?' + new URLSearchParams({ path })),
  browse: (path) => req('POST', '/api/browse', { path }),
  pins: () => req('GET', '/api/pins'),
  setPins: (paths) => req('PUT', '/api/pins', { paths }),
  // tags
  tags: (project) => req('GET', '/api/tags' + (project ? `?project=${project}` : '')),
  projects: () => req('GET', '/api/projects'),
  views: () => req('GET', '/api/views'),
  similarStatus: () => req('GET', '/api/similar/status'),
  audioWave: (id, bins = 1600) => req('GET', `/api/audio/${id}/wave?bins=${bins}`),
  exportRegion: (id, body) => req('POST', `/api/audio/${id}/export`, body),
  unitySettings: (body) => req('POST', '/api/unity/settings', body),
  unityUndo: (id) => req('POST', '/api/unity/undo', { id }),
  iccProfiles: () => req('GET', '/api/icc/profiles'),
  stack: (ids, byName = false) => req('POST', '/api/stacks', { ids, by_name: byName }),
  unstack: (ids) => req('POST', '/api/stacks/unstack', { ids }),
  stackCover: (id) => req('POST', '/api/stacks/cover', { id }),
  sheet: (kind, body, signal) => (body.preview ? postImage(`/api/sheets/${kind}`, body, signal) : req('POST', `/api/sheets/${kind}`, body)),
  related: (id) => req('GET', `/api/items/${id}/related`),
  exportPresets: () => req('GET', '/api/export/presets'),
  exportPlan: (body) => req('POST', '/api/export/plan', body),
  exportRun: (body) => req('POST', '/api/export/run', body),
  exportJob: () => req('GET', '/api/export/job'),
  exportCancel: () => req('POST', '/api/export/cancel', {}),
  saveView: (name, query) => req('POST', '/api/views', { name, query }),
  deleteView: (id) => req('DELETE', `/api/views/${id}`),
  setProjectSources: (id, sources) => req('PUT', `/api/projects/${id}/sources`, { sources }),
  createTag: (name, opts = {}) => req('POST', '/api/tags', { name, ...opts }),
  updateTag: (id, patch) => req('PATCH', `/api/tags/${id}`, patch),
  deleteTag: (id) => req('DELETE', `/api/tags/${id}`),
  applyTag: (id, ids, op = 'toggle') => req('POST', `/api/tags/${id}/apply`, { ids, op }),
  // files
  planMove: (body) => req('POST', '/api/move/plan', body),
  move: (body) => req('POST', '/api/move', body),
  undoOp: (id) => req('POST', `/api/operations/${id}/undo`, {}),
  reveal: (id) => req('POST', '/api/reveal', { id }),
  open: (id) => req('POST', '/api/open', { id }),
  // rules, sidecars, settings
  rules: () => req('GET', '/api/rules'),
  setRules: (rules) => req('PUT', '/api/rules', { rules }),
  planRules: (ids) => req('POST', '/api/rules/plan', { ids }),
  applyRules: (ids) => req('POST', '/api/rules/apply', { ids }),
  settings: () => req('GET', '/api/settings'),
  saveSettings: (s) => req('PUT', '/api/settings', s),
  writeSidecars: (ids, include_untagged = false) => req('POST', '/api/xmp/write', { ids, include_untagged }),
  importKeywords: (ids) => req('POST', '/api/xmp/import', { ids }),
  // backups
  backups: () => req('GET', '/api/backups'),
  createBackup: (b) => req('POST', '/api/backups', b),
  updateBackup: (id, b) => req('PUT', `/api/backups/${id}`, b),
  deleteBackup: (id) => req('DELETE', `/api/backups/${id}`),
  planBackup: (id) => req('POST', `/api/backups/${id}/plan`, {}),
  runBackup: (id) => req('POST', `/api/backups/${id}/run`, {}),
  planRestore: (id, mode, folder) => req('POST', `/api/backups/${id}/restore/plan`, { mode, folder }),
  runRestore: (id, mode, folder) => req('POST', `/api/backups/${id}/restore`, { mode, folder }),
  job: () => req('GET', '/api/job'),
  rclone: () => req('GET', '/api/rclone'),
  rcloneConnect: (name) => req('POST', '/api/rclone/connect', { name }),
  cancelJob: () => req('POST', '/api/job/cancel', {}),
  // AI (optional)
  ai: () => req('GET', '/api/ai'),
  aiSettings: (s) => req('PUT', '/api/ai/settings', s),
  aiEmbed: (ids = null) => req('POST', '/api/ai/embed', { ids }),
  aiSearch: (q, limit = 500) => req('POST', '/api/ai/search', { q, limit }),
  aiSimilar: (id, limit = 500) => req('GET', `/api/ai/similar/${id}?limit=${limit}`),
  aiProfiles: () => req('GET', '/api/ai/profiles'),
  aiSaveProfile: (p, id = null) => req(id ? 'PUT' : 'POST', id ? `/api/ai/profiles/${id}` : '/api/ai/profiles', p),
  aiDeleteProfile: (id) => req('DELETE', `/api/ai/profiles/${id}`),
  aiTrain: (id) => req('POST', `/api/ai/profiles/${id}/train`, {}),
  aiRun: (id, ids) => req('POST', `/api/ai/profiles/${id}/run`, { ids }),
  aiAccept: (sel) => req('POST', '/api/ai/suggestions/accept', sel),
  aiReject: (sel) => req('POST', '/api/ai/suggestions/reject', sel),
  aiJob: () => req('GET', '/api/ai/job'),
  aiCancel: () => req('POST', '/api/ai/cancel', {}),
  aiOllama: () => req('GET', '/api/ai/ollama'),
  datasetOptions: () => req('GET', '/api/dataset/options'),
  infoOptions: () => req('GET', '/api/info/options'),
  setInfo: (body) => req('POST', '/api/info', body),
  datasetPlan: (body) => req('POST', '/api/dataset/plan', body),
  datasetRun: (body) => req('POST', '/api/dataset/run', body),
  datasetJob: () => req('GET', '/api/dataset/job'),
  datasetCancel: () => req('POST', '/api/dataset/cancel'),
  tagPrompt: (id, prompt) => req('PUT', `/api/tags/${id}/prompt`, { prompt }),
};

// Shown for files that could not be decoded (no request to the server).
export const BROKEN_IMG = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#2a2e38"/>' +
  '<text x="32" y="40" font-size="28" text-anchor="middle" fill="#ef7a5a">⚠</text></svg>');

export const thumbUrl = (items, i, size) => {
  const v = items.v[i];
  if (v === 'e') return BROKEN_IMG;
  return `/t/${items.ids[i]}?s=${size}` + (v ? `&v=${v}` : '');
};

export const originalUrl = (items, i) => `/f/${items.ids[i]}`;
