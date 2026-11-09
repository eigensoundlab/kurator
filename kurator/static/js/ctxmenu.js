// The right-click menu on thumbnails, folders (sidebar, path bar), tags and projects, and the empty
// grid. In a browser a small page menu at the pointer; in the desktop app the system's own menu
// (and the page menu if the system's can't be shown). Text fields keep their usual menu.

import { api } from './api.js';
import { openAppsDialog } from './appsdlg.js';
import { openSendMenu } from './senddlg.js';
import { inShell, nativeMenu, writeClipboard } from './shell.js';
import { el, extOf, fmtNum } from './util.js';

const UA = navigator.userAgent;
export const REVEAL = /Windows/.test(UA) ? 'Show in Explorer' : /Mac/.test(UA) ? 'Show in Finder' : 'Show in the file manager';

let appList = null;   // programs for Open with: loaded on first use, again after they are edited
export const forgetApps = () => { appList = null; };
async function programs() {
  if (!appList) {
    try { appList = await api.apps(); } catch { appList = { apps: [], ext_kind: {} }; }
  }
  return appList;
}

const plural = (n, one, many = `${one}s`) => `${fmtNum(n)} ${n === 1 ? one : many}`;

async function copyText(app, text, what) {
  try { await writeClipboard(text); app.toast(`Copied ${what}`); } catch (e) { app.toast(`Could not copy: ${e.message}`); }
}

// ------------------------------------------------------------------ items
async function fileItems(app, ids) {
  const n = ids.length;
  const idx = app.allItems ? app.allIndex() : new Map();
  const names = ids.map((id) => app.allItems?.n?.[idx.get(id)]).filter(Boolean);
  const { apps, ext_kind: kindOf } = await programs();
  const exts = new Set(names.map((nm) => `.${extOf(nm)}`));
  const kinds = new Set([...exts].map((e) => kindOf[e]));
  const fits = (a) => !a.hidden && !a.missing && [...kinds].every((k) => a.kinds.includes(k)) && (!a.exts || [...exts].every((e) => a.exts.includes(e)));
  const withApps = apps.filter(fits).map((a) => ({ label: a.name, run: () => openWith(app, a, ids) }));
  const images = [...kinds].includes('image');
  const s = n === 1 ? '' : ` ${fmtNum(n)} files`;
  return [
    { label: n === 1 ? 'Open' : `Open${s}`, key: 'O', disabled: n > 20, run: () => openAll(app, ids) },
    { label: 'Open with', items: [...withApps, ...(withApps.length ? ['-'] : []), { label: 'Programs…', run: () => openAppsDialog(app) }] },
    { label: REVEAL, run: () => api.reveal(ids[0]).catch((e) => app.toast(e.message)) },
    { label: n === 1 ? 'Copy path' : `Copy ${fmtNum(n)} paths`, run: async () => copyText(app, (await api.paths(ids)).paths.join('\n'), n === 1 ? 'the path' : plural(n, 'path')) },
    '-',
    { label: 'Copy to…', key: 'K', run: () => app.copyDialog() },
    { label: 'Move to…', key: 'M', run: () => app.moveDialog() },
    { label: 'Send to a quick destination…', key: 'D', run: () => openSendMenu(app) },
    { label: 'Rename…', key: 'F2', run: () => app.renameDialog() },
    { label: 'Resample…', key: 'I', disabled: !images, run: () => app.resampleDialog() },
    '-',
    { label: n === 1 ? 'Delete…' : `Delete${s}…`, key: 'Del', run: () => app.deleteDialog() },
  ];
}

async function openAll(app, ids) {
  for (const id of ids.slice(0, 20)) {
    try { await api.open(id); } catch (e) { app.toast(e.message); return; }
  }
}

async function openWith(app, a, ids) {
  try {
    const r = await api.openWith(a.id, ids);
    app.toast(`Opening ${plural(r.files, 'file')} in ${r.app}${ids.length > r.files ? ` (the first ${fmtNum(r.files)})` : ''}`);
  } catch (e) { app.toast(e.message, 4000); }
}

function folderItems(app, path) {
  const sel = [...app.selected];
  const n = sel.length;
  const inLib = app.isLibraryPath(path);
  const isRoot = (app.state?.roots || []).some((r) => r.kind !== 'browse' && r.path === path);
  const pinned = app.sidebar.isPinned(path);
  const here = n ? plural(n, 'selected file') : 'the selected files';
  return [
    { label: 'Open', run: () => app.openPath(path) },
    { label: REVEAL, run: () => api.revealFolder(path).catch((e) => app.toast(e.message)) },
    { label: 'Copy path', run: () => copyText(app, path, 'the path') },
    { label: pinned ? 'Unpin' : 'Pin', run: () => app.sidebar.togglePin(path) },
    '-',
    { label: `Copy ${here} here`, disabled: !n, run: async () => { try { app.onCopied(await api.copy({ ids: sel, dest: path, keep_tags: true })); } catch (e) { app.toast(e.message); } } },
    { label: `Move ${here} here`, disabled: !n, run: async () => { try { app.onMoved(await api.move({ ids: sel, dest: path })); } catch (e) { app.toast(e.message); } } },
    '-',
    isRoot ? { label: 'Remove from library…', run: () => app.sidebar.removeRoot(path) }
      : inLib ? { label: 'In the library', disabled: true } : { label: 'Add to library', run: () => app.sidebar.addRoot(path) },
  ];
}

function tagItems(app, t) {
  const n = app.tagTargets().length;
  const project = t.facet === 'project' && !t.name.includes('/');
  const open = project && app.projects.active === t.id;
  return [
    project ? { label: open ? 'Leave this project' : 'Open this project', run: () => (open ? app.projects.leave() : app.projects.activate(t.id)) }
      : { label: 'Show everything with this tag', run: () => app.setTagFilter(t.id) },
    { label: 'Narrow this view to it', run: () => app.setTagFilter(t.id, true) },
    { label: 'Hide its files', run: () => app.setTagFilter(t.id, false, { exclude: true }) },
    '-',
    { label: n ? `Add to ${plural(n, 'item')}` : 'Add to the selected items', disabled: !n, run: () => app.applyTag(t.id, { op: 'add' }) },
    { label: n ? `Remove from ${plural(n, 'item')}` : 'Remove from the selected items', disabled: !n, run: () => app.applyTag(t.id, { op: 'remove' }) },
    '-',
    { label: project ? 'Edit project…' : 'Edit tag…', run: () => app.tags.editDialog(t) },
  ];
}

function gridItems(app) {
  const f = app.query.folder;
  return [
    { label: 'Select all', key: 'Ctrl+A', run: () => app.selectAll() },
    { label: 'Invert selection', run: () => app.invertSelection() },
    ...(f ? ['-', { label: `${REVEAL}: this folder`, run: () => api.revealFolder(f).catch((e) => app.toast(e.message)) }] : []),
  ];
}

// ------------------------------------------------------------- page menu
let stack = [];
let closer = null;

function closeAll() {
  for (const m of stack) m.remove();
  stack = [];
  closer?.();
  closer = null;
}

function place(menu, x, y, alt) {
  document.body.append(menu);
  const w = menu.offsetWidth, h = menu.offsetHeight;
  const left = x + w > innerWidth - 4 ? Math.max(4, (alt ?? x) - w) : x;
  const top = Math.max(4, Math.min(y, innerHeight - h - 4));
  Object.assign(menu.style, { left: `${left}px`, top: `${top}px` });
}

function pageMenu(items, x, y, level = 0) {
  for (const m of stack.splice(level)) m.remove();
  const menu = el('div', { class: 'popmenu ctxmenu', role: 'menu' });
  for (const it of items) {
    if (it === '-') { menu.append(el('div', { class: 'pm-sep' })); continue; }
    const sub = !!it.items;
    const row = el('button', { type: 'button', class: 'pm-row' + (sub ? ' sub' : ''), role: 'menuitem', disabled: !!it.disabled || (sub && !it.items.length) },
      el('span', { class: 'grow', text: it.label }), it.key ? el('kbd', { text: it.key }) : null, sub ? el('span', { class: 'pm-arrow', text: '›' }) : null);
    const openSub = () => { const r = row.getBoundingClientRect(); pageMenu(it.items, r.right - 2, r.top - 5, level + 1); stack[level + 1].dataset.from = it.label; };
    row.addEventListener('pointerenter', () => {
      if (sub) { if (stack[level + 1]?.dataset.from !== it.label) openSub(); } else for (const m of stack.splice(level + 1)) m.remove();
    });
    row.addEventListener('click', () => {
      if (sub) { openSub(); stack[level + 1].querySelector('button:not([disabled])')?.focus(); return; }
      closeAll();
      it.run?.();
    });
    menu.append(row);
  }
  stack[level] = menu;
  const parent = level ? stack[level - 1].getBoundingClientRect().left : null;
  place(menu, x, y, parent);
  if (!closer) {
    const outside = (e) => { if (!stack.some((m) => m.contains(e.target))) closeAll(); };
    const key = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeAll(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); e.stopPropagation();
        const m = stack.find((s) => s.contains(document.activeElement)) || stack[stack.length - 1];
        const rows = [...m.querySelectorAll('button:not([disabled])')];
        const i = rows.indexOf(document.activeElement), down = e.key === 'ArrowDown';
        rows[i < 0 ? (down ? 0 : rows.length - 1) : (i + (down ? 1 : -1) + rows.length) % rows.length]?.focus();
      }
    };
    const gone = () => closeAll();
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', key, true);
    addEventListener('blur', gone);
    addEventListener('resize', gone);
    document.addEventListener('wheel', gone, { capture: true, passive: true });
    closer = () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', key, true);
      removeEventListener('blur', gone);
      removeEventListener('resize', gone);
      document.removeEventListener('wheel', gone, { capture: true });
    };
  }
}

/** Show a menu at the pointer: the system's in the desktop app, else the page's. */
export async function showMenu(items, x, y) {
  closeAll();
  if (inShell() && await nativeMenu(items)) return;
  pageMenu(items, x, y);
}

// ----------------------------------------------------------------- hook
export function installContextMenu(app) {
  app.onAppsChanged = forgetApps;
  document.addEventListener('contextmenu', async (e) => {
    const t = e.target;
    if (t.closest?.('input, textarea, select, [contenteditable]')) return;       // copy / paste as usual
    if (t.closest?.('.dlg-wrap, .popmenu')) { if (inShell()) e.preventDefault(); return; }
    let make = null;
    const tagEl = t.closest?.('[data-tag-id]');
    const folderEl = t.closest?.('[data-folder]');
    if (tagEl && app.tags.byId.has(+tagEl.dataset.tagId)) {
      const tag = app.tags.byId.get(+tagEl.dataset.tagId);
      make = () => tagItems(app, tag);
    } else if (folderEl) {
      make = () => folderItems(app, folderEl.dataset.folder);
    } else if (app.grid.el.contains(t) && !document.body.classList.contains('modal-open')) {
      const i = app.indexAtClient(e.clientX, e.clientY);
      if (i >= 0) {
        const id = app.items.ids[i];
        if (!app.selected.has(id)) { app.setSelection(new Set([id])); app.setAnchor(i); }
        app.setFocus(i, { scroll: false });
        make = () => fileItems(app, [...app.selected]);
      } else make = () => gridItems(app);
    }
    if (!make) {
      // elsewhere: the page has nothing to offer; in the desktop app hide the web view's own menu
      // (Back, Reload, Inspect...) unless there is text selected to copy
      if (inShell() && !String(window.getSelection?.() || '')) e.preventDefault();
      return;
    }
    e.preventDefault();
    showMenu(await make(), e.clientX, e.clientY);
  });
}
