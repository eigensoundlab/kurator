// The right-click menu on thumbnails, folders (sidebar, path bar), tags and projects, and the empty
// grid. In a browser a small page menu at the pointer; in the desktop app the system's own menu
// (and the page menu if the system's can't be shown). Text fields keep their usual menu.

import { api } from './api.js';
import { openAppsDialog } from './appsdlg.js';
import { LIGHTS, MATCAPS, VIEW_MODES } from './meshview.js';
import { openRegionDialog } from './regiondlg.js';
import { openSendMenu } from './senddlg.js';
import { sendToSfx } from './sfxdlg.js';
import { inShell, nativeMenu, writeClipboard, writeClipboardImage } from './shell.js';
import { el, extOf, fmtNum } from './util.js';
import { BGS } from './viewer.js';

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

/** The picture itself, full size (any kind Kurator reads: PSD, EXR, Krita...), for pasting elsewhere. */
async function copyImage(app, id) {
  app.toast('Copying the picture…', 20000);
  try { await writeClipboardImage(id); app.toast('Copied the picture'); } catch (e) { app.toast(`Could not copy the picture: ${e.message}`, 4000); }
}

async function remake(app, ids) {
  try {
    const r = await api.remake(ids);
    app.toast(r.queued ? `Making ${r.queued === 1 ? 'the preview' : `${fmtNum(r.queued)} previews`} again…` : 'Nothing to make again (not indexed yet?)');
    app.refreshState();
  } catch (e) { app.toast(e.message); }
}

/** Open with: the programs that open every one of these files. */
async function openWithItems(app, ids) {
  const idx = app.allItems ? app.allIndex() : new Map();
  const names = ids.map((id) => app.allItems?.n?.[idx.get(id)] ?? app.items.n[app.indexOf(id)]).filter(Boolean);
  const { apps, ext_kind: kindOf } = await programs();
  const exts = new Set(names.map((nm) => `.${extOf(nm)}`));
  const kinds = new Set([...exts].map((e) => kindOf[e]));
  const fits = (a) => !a.hidden && !a.missing && [...kinds].every((k) => a.kinds.includes(k)) && (!a.exts || [...exts].every((e) => a.exts.includes(e)));
  const list = [];
  for (const a of apps.filter(fits)) {
    list.push({ label: a.name, run: () => openWith(app, a, ids) });
    // several sounds as tracks of one REAPER project; several pictures as layers of one picture
    if (ids.length > 1 && a.layout === 'tracks' && [...kinds].every((k) => k === 'audio')) {
      list.push({ label: `${a.name}: one track each`, run: () => openWith(app, a, ids, 'tracks') });
    }
    if (ids.length > 1 && a.layout === 'layers' && [...kinds].every((k) => k === 'image')) {
      list.push({ label: `${a.name}: as layers of one picture`, run: () => openWith(app, a, ids, 'layers') });
    }
  }
  return { items: [...list, ...(list.length ? ['-'] : []), { label: 'Programs…', run: () => openAppsDialog(app) }], kinds };
}

// ------------------------------------------------------------------ items
async function fileItems(app, ids) {
  const n = ids.length;
  const ow = await openWithItems(app, ids);
  const images = ow.kinds.has('image');
  const sounds = ow.kinds.size === 1 && ow.kinds.has('audio');
  const s = n === 1 ? '' : ` ${fmtNum(n)} files`;
  return [
    { label: n === 1 ? 'Open' : `Open${s}`, key: 'O', disabled: n > 20, run: () => openAll(app, ids) },
    { label: 'Open with', items: ow.items },
    ...(sounds ? [{ label: n === 1 ? 'Send to SFX Workbench…' : `Send ${fmtNum(n)} sounds to SFX Workbench…`, disabled: n > 200, run: () => sendToSfx(app, ids) }] : []),
    { label: REVEAL, run: () => api.reveal(ids[0]).catch((e) => app.toast(e.message)) },
    { label: n === 1 ? 'Copy path' : `Copy ${fmtNum(n)} paths`, run: async () => copyText(app, (await api.paths(ids)).paths.join('\n'), n === 1 ? 'the path' : plural(n, 'path')) },
    ...(n === 1 && images ? [{ label: 'Copy picture', run: () => copyImage(app, ids[0]) }] : []),
    '-',
    { label: 'Copy to…', key: 'K', run: () => app.copyDialog() },
    { label: 'Move to…', key: 'M', run: () => app.moveDialog() },
    { label: 'Send to a quick destination…', key: 'D', run: () => openSendMenu(app) },
    { label: 'Rename…', key: 'F2', run: () => app.renameDialog() },
    { label: 'Resample…', key: 'I', disabled: !images, run: () => app.resampleDialog() },
    { label: n === 1 ? 'Regenerate preview' : `Regenerate ${fmtNum(n)} previews`, run: () => remake(app, ids) },
    '-',
    { label: n === 1 ? 'Delete…' : `Delete${s}…`, key: 'Del', run: () => app.deleteDialog() },
  ];
}

/** Right-click in the viewer: what fits the file shown (picture, SVG, PDF, 3D, video, sound), then
 * the usual file commands - for this file only, whatever else is selected. */
async function viewerItems(app, x, y) {
  const v = app.viewer, it = v.items, i = v.i, id = it.ids[i], name = it.n[i], ext = extOf(name);
  const one = [id];
  const ow = await openWithItems(app, one);
  const own = [];
  if (v.is3d && v.mv?.n) {
    const mv = v.mv;
    own.push(
      { label: 'Reset the view', key: '0', run: () => mv.reset() },
      { label: 'Shading', items: VIEW_MODES.map(([k, t]) => ({ label: t, checked: mv.mode === k && mv.showMap == null, run: () => mv.set({ mode: k }) })) },
      { label: 'Matcap', items: Object.entries(MATCAPS).map(([k, m]) => ({ label: m.name, checked: mv.mode === 'matcap' && mv.matcap === k, run: () => mv.set({ matcap: k }) })) },
      { label: 'Lights', items: Object.entries(LIGHTS).map(([k, l]) => ({ label: l.name, checked: mv.lights === k, run: () => mv.set({ lights: k }) })) },
      { label: 'Cavity', key: 'C', checked: mv.cavity, run: () => mv.set({ cavity: !mv.cavity }) },
      { label: 'Smooth shading', checked: !!mv.smooth, run: () => mv.set({ smooth: !mv.smooth }) });
  } else if (v.isVideo && v.video) {
    const vid = v.video;
    own.push(
      { label: vid.paused ? 'Play' : 'Pause', key: 'Space', run: () => (vid.paused ? vid.play() : vid.pause()) },
      { label: '5 seconds back', key: 'Shift+←', run: () => { vid.currentTime -= 5; } },
      { label: '5 seconds on', key: 'Shift+→', run: () => { vid.currentTime += 5; } },
      { label: 'Save this frame as a picture', run: () => v.saveFrame() });
  } else if (v.isSound) {
    own.push({ label: 'Play / pause', key: 'Space', run: () => v.onKey({ key: ' ', shiftKey: false, ctrlKey: false }) });
    if (v.wave?.sel) own.push({ label: 'Export the selection…', key: 'E', run: () => openRegionDialog(app, v.wave.id, v.wave.sel) });
  } else {
    const hex = v.colourAt(x, y);
    if (hex) own.push({ label: `Copy colour ${hex}`, run: () => copyText(app, hex, hex) });
    own.push(
      { label: v.fitted ? '100 %' : 'Fit to the window', key: v.fitted ? 'Z' : '0', run: () => (v.fitted ? v.zoomTo(1) : v.fitView()) },
      { label: 'Background', items: BGS.map((b) => ({ label: b[0].toUpperCase() + b.slice(1), checked: v.bg === b, run: () => v.setBg(b) })) },
      { label: 'Pixel-art zoom', key: 'N', checked: !!v.pixel, run: () => v.togglePixel() });
    if (v.root.classList.contains('has-maps')) own.push({ label: 'Lit preview', key: 'L', checked: !!v.litOn, run: () => v.toggleLit() });
    if (v.inksOk) own.push({ label: 'Inks', key: 'I', checked: !!v.inksOn, run: () => v.toggleInks() });
    if (/^svgz?$/.test(ext)) {
      const all = v.svgAll ?? !!app.state?.svg_all;
      own.push({ label: 'Show what is outside the page', checked: all, run: () => v.setSvgAll(!all) });
    }
    if (v.pdf?.count > 1) own.push({ label: 'Previous page', key: '[', run: () => v.gotoPage(v.page - 1) }, { label: 'Next page', key: ']', run: () => v.gotoPage(v.page + 1) });
  }
  const close = (fn) => () => { v.close(); fn(); };   // moving, renaming, deleting: back in the grid first
  return [
    ...own, ...(own.length ? ['-'] : []),
    { label: 'Open', key: 'O', run: () => openAll(app, one) },
    { label: 'Open with', items: ow.items },
    ...(ow.kinds.has('audio') ? [{ label: 'Send to SFX Workbench…', run: () => sendToSfx(app, one) }] : []),
    { label: REVEAL, run: () => api.reveal(id).catch((e) => app.toast(e.message)) },
    { label: 'Copy path', run: async () => copyText(app, (await api.paths(one)).paths.join('\n'), 'the path') },
    ...(ow.kinds.has('image') ? [{ label: 'Copy picture', run: () => copyImage(app, id) }] : []),
    '-',
    { label: 'Copy to…', run: () => app.copyDialog(one) },
    { label: 'Move to…', run: close(() => app.moveDialog(one)) },
    { label: 'Rename…', run: close(() => app.renameDialog(one)) },
    ...(ow.kinds.has('image') ? [{ label: 'Resample…', run: () => app.resampleDialog(one) }] : []),
    { label: 'Regenerate preview', run: () => remake(app, one) },
    { label: 'Delete…', run: close(() => app.deleteDialog(one)) },
    '-',
    { label: 'Selected', key: 'X', checked: app.selected.has(id), run: () => v.toggleSel() },
    { label: 'Previous', key: '←', disabled: i === 0, run: () => v.step(-1) },
    { label: 'Next', key: '→', disabled: i >= it.ids.length - 1, run: () => v.step(1) },
    { label: 'Back to the grid', key: 'Esc', run: () => v.close() },
  ];
}

async function openAll(app, ids) {
  for (const id of ids.slice(0, 20)) {
    try { await api.open(id); } catch (e) { app.toast(e.message); return; }
  }
}

async function openWith(app, a, ids, layout = null) {
  if (layout) app.toast(layout === 'tracks' ? `Making a REAPER project of ${plural(ids.length, 'sound')}…` : `Putting ${plural(ids.length, 'picture')} together as layers…`, 30000);
  try {
    const r = await api.openWith(a.id, ids, layout);
    app.toast(layout ? `Opening ${plural(r.files, 'file')} in ${r.app} as ${layout === 'tracks' ? 'tracks of one project' : 'layers of one picture'} - save it from there`
      : `Opening ${plural(r.files, 'file')} in ${r.app}${ids.length > r.files ? ` (the first ${fmtNum(r.files)})` : ''}`, 5000);
  } catch (e) { app.toast(e.message, 5000); }
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
    ...(inLib ? [{ label: 'New sounds in SFX Workbench…', run: () => sendToSfx(app, [], { dest: path }) }] : []),
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
    { label: 'New sounds in SFX Workbench…', run: () => sendToSfx(app, [], { tags: [...new Set([...(app.projects.active && app.projects.active !== t.id ? [app.projects.active] : []), t.id])] }) },
    '-',
    { label: project ? 'Edit project…' : 'Edit tag…', run: () => app.tags.editDialog(t) },
  ];
}

function gridItems(app) {
  const f = app.query.folder;
  return [
    { label: 'Select all', key: 'Ctrl+A', run: () => app.selectAll() },
    { label: 'Invert selection', run: () => app.invertSelection() },
    '-',
    { label: 'New sounds in SFX Workbench…', run: () => sendToSfx(app, []) },
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
  const ticks = items.some((it) => it !== '-' && it.checked !== undefined);
  const menu = el('div', { class: 'popmenu ctxmenu' + (ticks ? ' ticks' : ''), role: 'menu' });
  for (const it of items) {
    if (it === '-') { menu.append(el('div', { class: 'pm-sep' })); continue; }
    const sub = !!it.items;
    const row = el('button', { type: 'button', class: 'pm-row' + (sub ? ' sub' : ''), role: it.checked !== undefined ? 'menuitemcheckbox' : 'menuitem',
      'aria-checked': it.checked !== undefined ? String(!!it.checked) : null, disabled: !!it.disabled || (sub && !it.items.length) },
      ticks ? el('span', { class: 'pm-tick', text: it.checked ? '✓' : '' }) : null,
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
  app.viewerMenu = async (x, y) => showMenu(await viewerItems(app, x, y), x, y);
  document.addEventListener('contextmenu', async (e) => {
    const t = e.target;
    if (t.closest?.('input, textarea, select, [contenteditable]')) return;       // copy / paste as usual
    if (t.closest?.('.dlg-wrap, .popmenu')) { if (inShell()) e.preventDefault(); return; }
    let make = null;
    if (app.viewer.isOpen && app.viewer.root.contains(t)) {
      if (t.closest?.('.meshview')) return;           // the 3D view: right-drag moves; a right-click asks itself
      if (t.closest?.('.v-bar, .mv-controls, .v-pages')) { if (inShell()) e.preventDefault(); return; }
      e.preventDefault();
      app.viewerMenu(e.clientX, e.clientY);
      return;
    }
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
