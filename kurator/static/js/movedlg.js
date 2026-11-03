// "Move to folder" dialog: choose destination + options, preview exactly what happens, then move.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtBytes, fmtNum, prefs, setKids } from './util.js';

export function openMoveDialog(app, ids) {
  if (!ids.length) { app.toast('Select images to move first'); return; }
  const recent = prefs.get('move.recent', []);
  const suggestions = [...new Set([...recent, ...app.sidebar.pins, ...app.sidebar.nodes.map((n) => n.path)])];
  const dest = el('input', { type: 'text', list: 'move-dests', spellcheck: 'false', placeholder: 'Folder inside your library, e.g. D:\\Art\\Creatures',
    value: recent[0] || app.query.folder || '' });
  const dl = el('datalist', { id: 'move-dests' }, suggestions.map((p) => el('option', { value: p })));
  const byYear = el('input', { type: 'checkbox' });
  byYear.checked = prefs.get('move.byYear', false);
  const prefix = el('input', { type: 'checkbox' });
  prefix.checked = prefs.get('move.prefix', false);
  const quick = el('div', { class: 'chip-list' }, [...new Set([...recent.slice(0, 5), ...app.sidebar.pins])].slice(0, 10).map((p) =>
    el('button', { class: 'chip', type: 'button', title: p, text: p.split(/[\\/]/).filter(Boolean).pop() || p,
      onclick: () => { dest.value = p; invalidate(); } })));
  const out = el('div', { class: 'move-preview' });
  let plan = null;

  const invalidate = () => {
    plan = null;
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = 'Move';
    out.replaceChildren(el('div', { class: 'p-hint small', text: 'Press Preview to see exactly what will happen. Nothing moves before you confirm.' }));
  };
  const body = () => ({ ids, dest: dest.value.trim(), by_year: byYear.checked, date_prefix: prefix.checked });

  const preview = async () => {
    if (!dest.value.trim()) { dest.focus(); return; }
    out.replaceChildren(el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { plan = await api.planMove(body()); } catch (e) { out.replaceChildren(el('div', { class: 'err', text: e.message })); return; }
    const rel = (p) => (p.startsWith(plan.dest) ? p.slice(plan.dest.length).replace(/^[\\/]/, '') : p);
    const base = (p) => p.split(/[\\/]/).pop();
    const list = el('div', { class: 'move-list' }, plan.moves.map((m) =>
      el('div', { class: 'mv' }, el('span', { class: 'from', text: base(m.from) }), el('span', { class: 'arrow', text: '→' }),
        el('span', { class: 'to' + (m.renamed ? ' renamed' : ''), text: rel(m.to) }))));
    const reasons = {};
    for (const s of plan.skipped) reasons[s.reason] = (reasons[s.reason] || 0) + 1;
    setKids(out,
      el('div', { class: 'move-sum' },
        el('b', { text: `${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'} will move` }), ` · ${fmtBytes(plan.bytes)} · into ${plan.dest}`,
        plan.cross_device ? el('div', { class: 'warn', text: 'Other disk: files are copied, checked byte-for-byte, then removed from the source. Dates are kept.' }) : el('div', { class: 'ok', text: 'Same disk: instant move, all dates kept.' }),
        Object.keys(reasons).length ? el('div', { class: 'muted', text: 'Skipped: ' + Object.entries(reasons).map(([r, c]) => `${c} ${r}`).join(', ') }) : null,
        plan.moves.some((m) => m.renamed) ? el('div', { class: 'muted', text: 'Highlighted names are changed (date prefix or " (2)" to avoid overwriting).' }) : null),
      list,
      plan.count > plan.moves.length ? el('div', { class: 'muted', text: `… and ${fmtNum(plan.count - plan.moves.length)} more` }) : null);
    d.buttons.go.disabled = plan.count === 0;
    d.buttons.go.textContent = `Move ${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'}`;
  };

  const go = async (close) => {
    if (!plan) return;
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = 'Moving…';
    let r;
    try { r = await api.move(body()); } catch (e) { app.toast(e.message); d.buttons.go.disabled = false; return; }
    prefs.set('move.recent', [plan.dest, ...recent.filter((p) => p !== plan.dest)].slice(0, 10));
    prefs.set('move.byYear', byYear.checked);
    prefs.set('move.prefix', prefix.checked);
    close();
    app.onMoved(r);
  };

  dest.addEventListener('input', () => invalidate());
  dest.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); plan ? go(d.close) : preview(); } });
  byYear.onchange = invalidate;
  prefix.onchange = invalidate;

  const d = openDialog({
    title: `Move ${fmtNum(ids.length)} item${ids.length > 1 ? 's' : ''} to folder`,
    width: 680,
    body: el('div', { class: 'form' },
      el('label', {}, 'Destination (created if missing)', dest, dl),
      quick,
      el('label', { class: 'chk' }, byYear, 'Year subfolders (…/2019/)'),
      el('label', { class: 'chk' }, prefix, 'Put the date in the file name (2019-04-12_name.png) when the name has no date yet'),
      out),
    buttons: [
      { label: 'Cancel' },
      { label: 'Preview', onClick: preview },
      { label: 'Move', id: 'go', primary: true, disabled: true, onClick: go },
    ],
  });
  invalidate();
}
