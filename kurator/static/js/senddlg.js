// Quick destinations ("Send to…", D then 1–9): numbered folders that copy or move the selection at
// once - no dialog, Ctrl+Z undoes. Global slots, and slots of the open project in their place
// (like its hotkeys): e.g. 1 Delivery, 2 Rejects, 3 References.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { digitKey, el, fmtNum, setKids } from './util.js';

const KEYS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const base = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;

async function effectiveSlots(app) {
  const all = await api.sendSlots();
  const out = {};
  for (const [k, s] of Object.entries(all.global || {})) out[k] = { ...s, scope: 'global' };
  const p = app.projects.active;
  if (p) for (const [k, s] of Object.entries(all[String(p)] || {})) out[k] = { ...s, scope: 'project' };
  return { all, slots: out };
}

export async function sendTo(app, slot, ids) {
  try {
    if (slot.action === 'move') app.onMoved(await api.move({ ids, dest: slot.path }));
    else app.onCopied(await api.copy({ ids, dest: slot.path, keep_tags: true }));
  } catch (e) { app.toast(`${slot.label || base(slot.path)}: ${e.message}`, 4000); }
}

export async function openSendMenu(app) {
  const ids = app.tagTargets();
  let st;
  try { st = await effectiveSlots(app); } catch (e) { app.toast(e.message); return; }
  const { slots } = st;
  let d;
  const run = (k) => {
    const s = slots[k];
    if (!s) return;
    if (!ids.length) { app.toast('Select files first'); return; }
    d.close();
    sendTo(app, s, ids);
  };
  const onKey = (e) => {
    const k = digitKey(e);
    if (k == null || e.ctrlKey || e.metaKey || e.altKey) {
      if ((e.key === 'e' || e.key === 'E') && !e.ctrlKey) { e.preventDefault(); e.stopPropagation(); d.close(); openSlotEditor(app); }
      return;
    }
    e.preventDefault(); e.stopPropagation();
    run(String(k));
  };
  document.addEventListener('keydown', onKey, true);
  const rows = KEYS.filter((k) => slots[k]).map((k) => {
    const s = slots[k];
    return el('button', { class: 'send-row', type: 'button', title: s.path, onclick: () => run(String(k)) },
      el('kbd', { text: k }), el('span', { class: 'send-act ' + s.action, text: s.action === 'move' ? 'Move' : 'Copy' }),
      el('span', { class: 'grow', text: s.label || base(s.path) }), el('span', { class: 'muted small fit', text: s.path }),
      s.scope === 'project' ? el('span', { class: 'muted small', text: '◆' }) : null);
  });
  d = openDialog({
    title: ids.length ? `Send ${fmtNum(ids.length)} item${ids.length > 1 ? 's' : ''} to…` : 'Quick destinations',
    width: 620,
    onClose: () => document.removeEventListener('keydown', onKey, true),
    body: el('div', { class: 'form' },
      rows.length ? el('div', { class: 'send-list' }, rows)
        : el('div', { class: 'p-hint', text: 'No quick destinations yet. Give folders a number (Edit…), then D and the number copies or moves the selection there.' }),
      el('div', { class: 'p-hint small', text: 'Press the number. E edits the list. ◆ = this project\'s own destination.' })),
    buttons: [{ label: 'Edit…', left: true, onClick: (close) => { close(); openSlotEditor(app); } }, { label: 'Close' }],
  });
}

export async function openSlotEditor(app) {
  let st;
  try { st = await effectiveSlots(app); } catch (e) { app.toast(e.message); return; }
  const proj = app.projects.current;
  let scope = proj && st.all[String(proj.id)] ? String(proj.id) : 'global';
  const grid = el('div', { class: 'slot-grid' });
  const msg = el('div');
  let inputs = {};
  const render = () => {
    const mine = st.all[scope] || {};
    const glob = st.all.global || {};
    inputs = {};
    setKids(grid, KEYS.map((k) => {
      const s = mine[k] || {};
      const path = el('input', { type: 'text', spellcheck: 'false', value: s.path || '',
        placeholder: scope !== 'global' && glob[k] ? `global: ${glob[k].label || glob[k].path}` : 'folder' });
      const action = el('select', {}, el('option', { value: 'copy', text: 'Copy' }), el('option', { value: 'move', text: 'Move' }));
      action.value = s.action || 'copy';
      const label = el('input', { type: 'text', spellcheck: 'false', value: s.label || '', placeholder: 'name (optional)', style: { width: '130px' } });
      inputs[k] = { path, action, label };
      return el('div', { class: 'slot-row' }, el('kbd', { text: k }), destField(app, path, { title: `Quick destination ${k}` }), action, label);
    }));
  };
  const collect = () => Object.fromEntries(KEYS.map((k) => [String(k), { path: inputs[k].path.value.trim(), action: inputs[k].action.value, label: inputs[k].label.value.trim() }])
    .filter(([, v]) => v.path));
  const scopeSel = proj ? el('select', {}, el('option', { value: 'global', text: 'Global (every project)' }),
    el('option', { value: String(proj.id), text: `Only in ${proj.name} (in place of the global ones)` })) : null;
  const dirty = new Set();
  if (scopeSel) {
    scopeSel.value = scope;
    scopeSel.onchange = () => { st.all[scope] = collect(); dirty.add(scope); scope = scopeSel.value; render(); };
  }
  render();
  openDialog({
    title: 'Quick destinations (D, then 1–9)', width: 820,
    body: el('div', { class: 'form' },
      scopeSel ? el('label', {}, 'Which list', scopeSel) : null,
      el('div', { class: 'p-hint small', text: 'Copy works with any folder; Move only inside the library. Both keep dates, never overwrite, and Ctrl+Z undoes them.' }),
      grid, msg),
    buttons: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async (close) => {
      st.all[scope] = collect();
      dirty.add(scope);
      try { for (const sc of dirty) await api.setSendSlots(sc, st.all[sc] || {}); } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); return; }
      close();
      app.toast('Quick destinations saved - D, then the number');
    } }],
  });
}
