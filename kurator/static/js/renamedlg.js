// Batch rename (F2): a name pattern with parts like {date} {tag} {n}, find / replace, case. Files stay
// in their folders and keep their extension; sidecars (XMP, Unity .meta, Godot .import) follow.
// Live preview; Ctrl+Z renames them back.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { debounce, el, fmtNum, projPrefs, setKids } from './util.js';

const PARTS = [['{name}', 'the current name'], ['{n}', 'counter (padding below, or {n:3})'], ['{date}', 'date 2024-05-31 (or {date:%Y%m%d})'],
  ['{year}', ''], ['{month}', ''], ['{day}', ''], ['{time}', 'HHMMSS'], ['{tag}', 'first subject tag'], ['{tags}', 'all subject tags'],
  ['{project}', ''], ['{folder}', 'folder name'], ['{w}', 'width'], ['{h}', 'height'], ['{author}', '']];
const PRESETS = [['{name}', 'Keep (find / replace, case)'], ['{date}_{name}', 'Date first'], ['{folder}_{n}', 'Folder + number'],
  ['{tag}_{n}', 'Tag + number'], ['{project}_{tag}_{n}', 'Project, tag, number'], ['{date:%Y%m%d}_{n}', 'Date + number']];

export function openRenameDialog(app) {
  const ids = app.selected.size ? app.items.ids.filter((id) => app.selected.has(id)) : app.tagTargets();   // grid order
  if (!ids.length) { app.toast('Select files to rename first'); return; }
  const pp = projPrefs(app);
  const P = pp.get('rename', {});
  const tpl = el('input', { type: 'text', spellcheck: 'false', value: P.template || (ids.length > 1 ? '{name}_{n}' : '{name}') });
  const num = (v, min, w = '70px') => el('input', { type: 'number', min: String(min), value: v, style: { width: w } });
  const start = num(P.start ?? 1, 0);
  const step = num(P.step ?? 1, 1);
  const pad = num(P.pad ?? Math.max(2, String(ids.length).length), 0, '60px');
  const find = el('input', { type: 'text', spellcheck: 'false', value: P.find || '', placeholder: 'find', style: { width: '160px' } });
  const repl = el('input', { type: 'text', spellcheck: 'false', value: P.replace || '', placeholder: 'replace with', style: { width: '160px' } });
  const regex = el('input', { type: 'checkbox' }); regex.checked = !!P.regex;
  const kase = el('select', {}, [['keep', 'Case as it is'], ['lower', 'lower case'], ['upper', 'UPPER CASE'], ['title', 'Title Case']].map(([k, t]) => el('option', { value: k, text: t })));
  kase.value = P.case || 'keep';
  const out = el('div', { class: 'move-preview' });
  let planned = null;
  const body = () => ({ ids, template: tpl.value, start: +start.value || 0, step: +step.value || 1, pad: +pad.value || 0,
    find: find.value, replace: repl.value, regex: regex.checked, case: kase.value });
  const base = (p) => p.split(/[\\/]/).pop();

  const preview = debounce(async () => {
    try { planned = await api.renamePlan(body()); } catch (e) { planned = null; setKids(out, el('div', { class: 'err', text: e.message })); d.buttons.go.disabled = true; return; }
    const sk = {};
    for (const s of planned.skipped) sk[s.reason] = (sk[s.reason] || 0) + 1;
    setKids(out,
      el('div', { class: 'move-sum' }, el('b', { text: `${fmtNum(planned.count)} file${planned.count === 1 ? '' : 's'} will be renamed` }),
        Object.keys(sk).length ? el('div', { class: 'muted', text: Object.entries(sk).map(([r, c]) => `${fmtNum(c)} ${r}`).join(', ') }) : null,
        planned.renames.some((r) => r.clash) ? el('div', { class: 'warn', text: 'Highlighted names would clash with others: " (2)" is added. Add {n} to number them.' }) : null),
      el('div', { class: 'move-list' }, planned.renames.map((r) => el('div', { class: 'mv' },
        el('span', { class: 'from', text: base(r.from) }), el('span', { class: 'arrow', text: '→' }),
        el('span', { class: 'to' + (r.clash ? ' renamed' : ''), text: base(r.to) })))),
      planned.count > planned.renames.length ? el('div', { class: 'muted', text: `… and ${fmtNum(planned.count - planned.renames.length)} more` }) : null);
    d.buttons.go.disabled = !planned.count;
    d.buttons.go.textContent = planned.count ? `Rename ${fmtNum(planned.count)}` : 'Rename';
  }, 180);

  const go = async (close) => {
    if (!planned?.count) return;
    d.buttons.go.disabled = true;
    let r;
    try { r = await api.rename(body()); } catch (e) { app.toast(e.message); d.buttons.go.disabled = false; return; }
    const b = body();
    pp.set('rename', { template: b.template, start: b.start, step: b.step, pad: b.pad, find: b.find, replace: b.replace, regex: b.regex, case: b.case });
    close();
    app.onRenamed(r);
  };

  for (const x of [tpl, start, step, pad, find, repl, regex, kase]) x.addEventListener('input', preview);
  tpl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(d.close); } });
  const insert = (t) => { const a = tpl.selectionStart ?? tpl.value.length; tpl.value = tpl.value.slice(0, a) + t + tpl.value.slice(tpl.selectionEnd ?? a); tpl.focus(); preview(); };
  const d = openDialog({
    title: `Rename ${fmtNum(ids.length)} file${ids.length > 1 ? 's' : ''}`,
    width: 720,
    body: el('div', { class: 'form' },
      el('label', {}, 'Name pattern (the extension stays)', tpl),
      el('div', { class: 'chip-list' }, PARTS.map(([t, hint]) => el('button', { class: 'chip small', type: 'button', text: t, title: hint || t, onclick: () => insert(t) }))),
      el('div', { class: 'chip-list' }, el('span', { class: 'tb-group', text: 'Patterns' }), PRESETS.map(([t, label]) => el('button', { class: 'chip small', type: 'button', text: label, title: t, onclick: () => { tpl.value = t; preview(); } }))),
      el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Counter from', start), el('label', { class: 'chk' }, 'step', step), el('label', { class: 'chk' }, 'digits', pad)),
      el('div', { class: 'row' }, find, repl, el('label', { class: 'chk small' }, regex, 'regular expression'), kase),
      el('div', { class: 'p-hint small', text: 'The counter follows the order of the grid. Names can be swapped or renumbered in place. Tags, dates and stacks stay with the files.' }),
      out),
    buttons: [{ label: 'Cancel' }, { label: 'Rename', id: 'go', primary: true, disabled: true, onClick: go }],
  });
  preview();
}
