// Small DOM + formatting helpers. All text goes through textContent (file metadata is untrusted).

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

export const $ = (sel, root = document) => root.querySelector(sel);

/** replaceChildren that skips null/false (DOM would print them as text). */
export function setKids(node, ...kids) {
  node.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
}

const dtf = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const monthf = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short' });
const nf = new Intl.NumberFormat();

export const fmtDate = (ts) => (ts ? dtf.format(new Date(ts * 1000)) : '—');
export const fmtMonth = (ts) => (ts ? monthf.format(new Date(ts * 1000)) : '');
export const fmtNum = (n) => nf.format(n);

export function fmtBytes(b) {
  if (b == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`;
}

export function debounce(fn, ms) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export const prefs = {
  get(k, d) { try { const v = localStorage.getItem('kurator.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('kurator.' + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

// Filled from the server's type registry at start-up.
export const VIEWABLE = new Set(['jpg', 'jpeg', 'jfif', 'jpe', 'png', 'webp', 'gif', 'bmp', 'avif', 'svg']);
export const MESHES = new Set(['glb', 'gltf', 'obj', 'fbx', 'stl', 'ply', 'dae', '3mf', '3ds', 'off', 'usda', 'usdz', 'usd', 'usdc', 'vox']);
export const VIDEO = new Set(['mp4', 'm4v', 'mov', 'webm', 'ogv', 'mkv', 'avi', 'wmv', 'flv', 'mpg', 'mpeg', 'm2ts', 'mts', 'ts', '3gp', '3g2', 'mxf', 'vob']);
export const AUDIO = new Set(['wav', 'mp3', 'ogg', 'oga', 'opus', 'flac', 'm4a', 'aac', 'aif', 'aiff', 'aifc', 'caf', 'w64', 'rf64', 'wma']);
export const fmtDuration = (s) => { if (s == null) return '—'; if (s < 1) return `${s.toFixed(2)} s`; const m = Math.floor(s / 60); return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`; };
export const extOf = (name) => { const i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i + 1).toLowerCase(); };

// Date source -> trust level for badges.
export const SOURCE_LABEL = {
  'exif-original': ['embedded', 'Taken/created date stored in the file (EXIF)'],
  'xmp': ['embedded', 'Creation date stored in the file (XMP)'],
  'document': ['embedded', 'Creation date stored in the document'],
  'png': ['embedded', 'Creation time stored in the PNG'],
  'sidecar': ['embedded', 'Creation date from the .xmp sidecar next to the file'],
  'exif-modified': ['saved', 'Last-saved date stored in the file (EXIF DateTime)'],
  'filename': ['filename', 'Date read from the file name'],
  'file-created': ['guess', 'File system creation date (can change when copying)'],
  'file-modified': ['guess', 'File system modification date (can change when copying)'],
  'manual': ['embedded', 'Set by you'],
};

export const isTyping = (t) => t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);

/** The number key pressed (0-9), or null. National layouts (Czech, Slovak…) type letters on
 * the number row without Shift (ě š č…): the key's place counts then, so hotkeys work unshifted. */
export function digitKey(e) {
  if (/^[0-9]$/.test(e.key)) return +e.key;
  const m = /^(?:Digit|Numpad)([0-9])$/.exec(e.code || '');
  return m && e.key.length === 1 && e.key.charCodeAt(0) > 127 ? +m[1] : null;
}

/** Dialog settings remembered per project: inside a project its own, else the last ones used anywhere. */
export function projPrefs(app) {
  const p = app.projects?.active;
  return {
    get: (k, d) => (p ? prefs.get(`${k}@${p}`, prefs.get(k, d)) : prefs.get(k, d)),
    set: (k, v) => { prefs.set(k, v); if (p) prefs.set(`${k}@${p}`, v); },
  };
}
