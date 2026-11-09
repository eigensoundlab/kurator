// Choosing a destination folder: a picker that clicks through drives and folders (and makes new
// ones), with recent destinations and pinned folders; destField() puts "Browse…" and the recent
// folders next to any path field (move, copy, resample, sheets, export, datasets…).

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtNum, setKids } from './util.js';

const base = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
let fieldNo = 0;

/** A path input with suggestions (recent destinations, pinned folders, extra) and a Browse… button. */
export function destField(app, input, { title = 'Choose a folder', extra = [], onPick } = {}) {
  const id = `dest-list-${++fieldNo}`;
  const dl = el('datalist', { id });
  input.setAttribute('list', id);
  api.recentFolders().then((r) => {
    const all = [...new Set([...r.recent.map((x) => x.path), ...r.pins, ...extra])];
    setKids(dl, all.map((p) => el('option', { value: p })));
  }).catch(() => {});
  const btn = el('button', { class: 'btn', type: 'button', text: 'Browse…', title: 'Click through your folders, or pick a recent one',
    onclick: async () => {
      const p = await pickFolder(app, { start: input.value.trim() || app.query.folder || '', title });
      if (p == null) return;
      input.value = p;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      onPick?.(p);
    } });
  return el('div', { class: 'destfield' }, input, btn, dl);
}

/** Resolves to the chosen folder, or null. */
export function pickFolder(app, { start = '', title = 'Choose a folder', use = 'Use this folder' } = {}) {
  return new Promise((resolve) => {
    let cur = null;
    let done = false;
    const finish = (v) => { if (done) return; done = true; resolve(v); };
    const pathIn = el('input', { type: 'text', spellcheck: 'false', placeholder: 'Type a path and press Enter' });
    const up = el('button', { class: 'btn', type: 'button', text: '↑', title: 'Parent folder', onclick: () => cur?.parent && go(cur.parent) });
    const newName = el('input', { type: 'text', spellcheck: 'false', placeholder: 'New folder name', style: { width: '170px' } });
    const mk = el('button', { class: 'btn', type: 'button', text: '+ New folder', title: 'Make a folder here and open it', onclick: async () => {
      if (!cur) return;
      if (!newName.value.trim()) { newName.focus(); return; }
      try { const r = await api.mkdir(cur.path, newName.value.trim()); newName.value = ''; go(r.path); } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
    } });
    const side = el('div', { class: 'fp-side' });
    const list = el('div', { class: 'fp-list' });
    const info = el('div', { class: 'muted small' });
    const msg = el('div');

    let nav = 0;   // only the newest navigation counts (the first one may answer after you typed a path)
    const go = async (path) => {
      const mine = ++nav;
      setKids(msg);
      let r;
      try { r = await api.listDir(path); } catch (e) {
        if (mine !== nav) return;
        // a destination that doesn't exist yet: open the nearest folder above it
        const parent = path.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]*$/, '');
        const next = /^[A-Za-z]:$/.test(parent) ? parent + '\\' : (parent || (path.startsWith('/') ? '/' : ''));
        if (next && next !== path) { go(next); return; }
        setKids(msg, el('div', { class: 'err', text: e.message }));
        return;
      }
      if (mine !== nav) return;
      cur = r;
      pathIn.value = r.path;
      up.disabled = !r.parent;
      setKids(list, r.dirs.length ? r.dirs.map((x) => el('div', { class: 'node', title: x.path, onclick: () => go(x.path) },
        el('span', { class: 'tw', text: '📁' }), el('span', { class: 'nm', text: x.name })))
        : el('div', { class: 'p-hint small', text: `No folders inside. "${use}" takes this one.` }),
      r.truncated ? el('div', { class: 'muted small', text: 'Only the first folders are shown.' }) : null);
      info.textContent = `${r.images ? `${fmtNum(r.images)} file${r.images === 1 ? '' : 's'} Kurator knows here · ` : ''}${r.library ? `in your library (${base(r.library)})` : 'outside your library'}`;
      d.buttons.use.disabled = false;
    };

    const sideRows = async () => {
      let rec = { recent: [], pins: [] }, places = { places: [], drives: [] };
      try { [rec, places] = await Promise.all([api.recentFolders(), api.places()]); } catch { /* offline */ }
      const head = (t) => el('div', { class: 'fp-head', text: t });
      const row = (p, icon, extraBtns = []) => el('div', { class: 'node', title: p, onclick: () => go(p) },
        el('span', { class: 'tw', text: icon }), el('span', { class: 'nm fit', text: base(p) }), ...extraBtns);
      const pinned = new Set(app.sidebar.pins);
      setKids(side,
        rec.recent.length ? head('Recent') : null,
        rec.recent.slice(0, 15).map((r) => row(r.path, '⟲', [
          el('span', { class: 'pin' + (pinned.has(r.path) ? ' on' : ''), title: pinned.has(r.path) ? 'Unpin' : 'Pin (also in the sidebar)', text: pinned.has(r.path) ? '★' : '☆',
            onclick: async (e) => { e.stopPropagation(); await app.sidebar.togglePin(r.path); sideRows(); } }),
          el('span', { class: 'rm', title: 'Forget this folder', text: '×', onclick: async (e) => { e.stopPropagation(); await api.forgetFolder(r.path); sideRows(); } })])),
        app.sidebar.pins.length ? head('Pinned') : null,
        app.sidebar.pins.map((p) => row(p, '★')),
        head('Places'),
        places.places.map((p) => row(p.path, '⌂')),
        places.drives.map((p) => row(p.path, '▣')));
    };

    pathIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); if (pathIn.value.trim()) go(pathIn.value.trim()); } });
    newName.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); mk.click(); } });
    const d = openDialog({
      title, width: 760,
      body: el('div', { class: 'form' },
        el('div', { class: 'fp-path' }, up, pathIn),
        el('div', { class: 'fp-wrap' }, side, el('div', {}, list, el('div', { class: 'row' }, newName, mk), info)),
        msg),
      buttons: [{ label: 'Cancel', onClick: (close) => { finish(null); close(); } },
        { label: use, id: 'use', primary: true, disabled: true, onClick: (close) => { finish(cur?.path ?? null); close(); } }],
      onClose: () => finish(null),
    });
    sideRows();
    if (start) go(start);
    else api.places().then((p) => go(p.places[0]?.path || p.drives[0]?.path || '/')).catch(() => {});
  });
}
