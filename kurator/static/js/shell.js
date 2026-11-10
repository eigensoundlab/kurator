// Kurator inside its desktop app (Tauri): there the window knows where dropped files are, files can
// be dragged out to other programs, and right-click shows the system's own menu. In a browser none
// of this exists and every caller falls back to what the page does on its own.

const T = () => window.__TAURI__;

/** True in the desktop app. */
export const inShell = () => !!(window.__TAURI_INTERNALS__ && window.__TAURI__);

/** Files and folders dropped from Explorer / Finder. fn gets {type: enter|over|drop|leave, paths, x, y}
 * with x, y in page (CSS) pixels. Returns false when this window can't tell (a browser). */
export function onNativeDrop(fn) {
  const wv = T()?.webview?.getCurrentWebview?.();
  if (!wv?.onDragDropEvent) return false;
  wv.onDragDropEvent((ev) => {
    const p = ev.payload || {};
    const r = window.devicePixelRatio || 1;
    fn({ type: p.type, paths: p.paths || [], x: p.position ? p.position.x / r : -1, y: p.position ? p.position.y / r : -1 });
  }).catch((e) => console.warn('Kurator desktop: no file drops', e));
  return true;
}

/** Drag files out of the window into another program (a real file drag, as from Explorer / Finder).
 * icon: a PNG data URL. Resolves when the drag ends; false when not in the desktop app. */
export async function dragOut(paths, icon) {
  const core = T()?.core;
  if (!core?.invoke || !paths.length) return false;
  await core.invoke('plugin:drag|start_drag', { item: paths, image: icon, onEvent: new core.Channel() });
  return true;
}

const ACCEL = { Del: 'Delete', 'Ctrl+A': 'CmdOrCtrl+A', Enter: 'Enter' };
const accel = (key) => (!key ? undefined : ACCEL[key] || (/^(F\d{1,2}|[A-Z0-9])$/.test(key) ? key : undefined));
let last = null;

/** The system's own menu at the pointer. items: [{label, key, run, disabled, items}] and '-' for a line.
 * Returns false when it can't be shown (a browser, or the app said no): then show the page's menu. */
export async function nativeMenu(items) {
  const M = T()?.menu;
  if (!M?.Menu) return false;
  const conv = (list, keys) => list.map((it) => (it === '-' ? { item: 'Separator' }
    : it.items ? { text: it.label, enabled: !it.disabled && it.items.length > 0, items: conv(it.items, keys) }
      : { text: it.label, enabled: !it.disabled, accelerator: keys ? accel(it.key) : undefined, action: () => it.run?.(),
        ...(it.checked !== undefined ? { checked: !!it.checked } : {}) }));
  try { last?.close?.().catch(() => {}); } catch { /* already gone */ }
  last = null;
  for (const keys of [true, false]) {   // shortcuts shown beside the items; without them if the system objects
    try {
      last = await M.Menu.new({ items: conv(items, keys) });
      await last.popup();
      return true;
    } catch (e) {
      if (!keys) { console.warn('Kurator desktop: no native menu', e); return false; }
    }
  }
  return false;
}

/** Text to the clipboard. In the desktop app through the app (a menu choice is no click in the page,
 * and WebKit allows the page's clipboard only right after one). */
export async function writeClipboard(text) {
  if (inShell()) {
    try { await T().core.invoke('plugin:clipboard-manager|write_text', { text }); return; } catch { /* the page's own, below */ }
  }
  await navigator.clipboard.writeText(text);
}

/** The system's Open dialog for one file (a program for Open with); null when cancelled or in a browser. */
export async function pickFile(title) {
  if (!inShell()) return null;
  try {
    const r = await T().core.invoke('plugin:dialog|open', { options: { title, multiple: false, directory: false } });
    return typeof r === 'string' ? r : r?.path || null;
  } catch (e) { console.warn('Kurator desktop: no Open dialog', e); return null; }
}

/** A catalog file's picture to the clipboard, full size. The desktop app takes it from a PNG Kurator
 * writes; a browser from the PNG itself (it allows that right after a click only). */
export async function writeClipboardImage(id) {
  if (inShell()) {
    const r = await fetch(`/api/items/${id}/png?file=1`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    await T().core.invoke('plugin:clipboard-manager|write_image', { image: (await r.json()).path });
    return;
  }
  // the PNG as a promise: the clipboard call itself must come straight after the click
  const png = fetch(`/api/items/${id}/png`).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.blob();
  });
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
}

/** The desktop app's own version (null in a browser). */
export async function appVersion() {
  try { return inShell() ? await T().core.invoke('plugin:app|version') : null; } catch { return null; }
}

/** The desktop app: stop Kurator, run its update script, start it again (the start page shows meanwhile). */
export async function updateKurator() {
  await T().core.invoke('update_kurator');
}
