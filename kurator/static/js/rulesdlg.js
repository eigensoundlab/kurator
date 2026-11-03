// Move rules: "tag -> folder template". Edit, preview for the selection or the current view, apply.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtBytes, fmtNum, setKids } from './util.js';

export async function openRulesDialog(app) {
  let rules;
  try { rules = await api.rules(); } catch (e) { app.toast(e.message); return; }
  const tags = app.tags.list;
  const list = el('div', { class: 'rules' });
  const out = el('div', { class: 'move-preview' });
  let plan = null;

  const tagSelect = (value) => el('select', {}, el('option', { value: '', text: '— tag —' }),
    app.tags.facets.map((f) => el('optgroup', { label: f.label }, tags.filter((t) => t.facet === f.key)
      .map((t) => el('option', { value: String(t.id), text: t.name, selected: t.id === value })))));

  const render = () => {
    const rows = rules.map((r, i) => {
      const sel = tagSelect(r.tag_id);
      sel.onchange = () => { r.tag_id = +sel.value; invalidate(); };
      const dest = el('input', { type: 'text', value: r.dest || '', spellcheck: 'false', list: 'rule-dests',
        placeholder: 'D:\\Art\\{tag}\\{subtag}\\{year}' });
      dest.oninput = () => { r.dest = dest.value; invalidate(); };
      const pre = el('input', { type: 'checkbox', title: 'Put the date in the file name (2019-04-12_name.png)' });
      pre.checked = !!r.date_prefix;
      pre.onchange = () => { r.date_prefix = pre.checked; invalidate(); };
      return el('div', { class: 'rule' },
        el('span', { class: 'rn', text: `${i + 1}.` }), sel, el('span', { class: 'arrow', text: '→' }), dest,
        el('label', { class: 'chk small', title: 'Date prefix in file name' }, pre, 'date'),
        el('button', { class: 'mini', text: '↑', title: 'Earlier (wins over later rules)', type: 'button', onclick: () => { if (i) { [rules[i - 1], rules[i]] = [rules[i], rules[i - 1]]; render(); invalidate(); } } }),
        el('button', { class: 'mini', text: '↓', type: 'button', onclick: () => { if (i < rules.length - 1) { [rules[i + 1], rules[i]] = [rules[i], rules[i + 1]]; render(); invalidate(); } } }),
        el('button', { class: 'mini', text: '×', title: 'Delete rule', type: 'button', onclick: () => { rules.splice(i, 1); render(); invalidate(); } }));
    });
    if (!rules.length) rows.push(el('div', { class: 'p-hint small', text: 'No rules yet. Example: Creatures → D:\\Art\\Creatures\\{subtag}\\{year}' }));
    setKids(list, rows);
  };
  const dl = el('datalist', { id: 'rule-dests' }, [...new Set([...app.sidebar.pins, ...app.sidebar.nodes.map((n) => n.path)])].map((p) => el('option', { value: p })));

  const nSel = app.selected.size;
  const scope = el('select', {},
    el('option', { value: 'selection', text: `Selection (${fmtNum(nSel)})`, disabled: !nSel }),
    el('option', { value: 'view', text: `Current view (${fmtNum(app.items.ids.length)})` }));
  scope.value = nSel ? 'selection' : 'view';
  scope.onchange = () => invalidate();
  const ids = () => (scope.value === 'selection' ? [...app.selected] : [...app.items.ids]);

  const invalidate = () => {
    plan = null;
    if (d) { d.buttons.go.disabled = true; d.buttons.go.textContent = 'Move'; }
    setKids(out, el('div', { class: 'p-hint small', text: 'Save & preview shows exactly which files go where. Nothing moves before you confirm.' }));
  };

  const save = async () => {
    try { rules = await api.setRules(rules.filter((r) => r.tag_id || r.dest)); } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); return false; }
    render();
    return true;
  };

  const preview = async () => {
    if (!(await save())) return;
    setKids(out, el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { plan = await api.planRules(ids()); } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); return; }
    const base = (p) => p.split(/[\\/]/).pop();
    const reasons = {};
    for (const s of plan.skipped) reasons[s.reason] = (reasons[s.reason] || 0) + 1;
    const perRule = Object.entries(plan.per_rule).map(([n, c]) => {
      const t = app.tags.byId.get(rules[+n]?.tag_id);
      return `${fmtNum(c)} × ${t ? t.name : 'rule ' + (+n + 1)}`;
    }).join(' · ');
    setKids(out,
      el('div', { class: 'move-sum' },
        el('b', { text: `${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'} will move` }), ` · ${fmtBytes(plan.bytes)}`,
        perRule ? el('div', { text: perRule }) : null,
        plan.cross_device ? el('div', { class: 'warn', text: 'Some files go to another disk: copied, verified byte-for-byte, then removed from the source. Dates are kept.' }) : el('div', { class: 'ok', text: 'Same disk: instant moves, all dates kept.' }),
        Object.keys(reasons).length ? el('div', { class: 'muted', text: 'Skipped: ' + Object.entries(reasons).map(([r, c]) => `${fmtNum(c)} ${r}`).join(', ') }) : null),
      el('div', { class: 'move-list' }, plan.moves.map((m) => el('div', { class: 'mv' },
        el('span', { class: 'from', text: base(m.from) }), el('span', { class: 'arrow', text: '→' }),
        el('span', { class: 'to' + (m.renamed ? ' renamed' : ''), text: m.to, title: m.to })))),
      plan.count > plan.moves.length ? el('div', { class: 'muted', text: `… and ${fmtNum(plan.count - plan.moves.length)} more` }) : null);
    d.buttons.go.disabled = plan.count === 0;
    d.buttons.go.textContent = `Move ${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'}`;
  };

  const go = async (close) => {
    if (!plan) return;
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = 'Moving…';
    let r;
    try { r = await api.applyRules(ids()); } catch (e) { app.toast(e.message); d.buttons.go.disabled = false; return; }
    close();
    app.onMoved(r);
  };

  let d = null;
  d = openDialog({
    title: 'Move rules',
    width: 780,
    body: el('div', { class: 'form' },
      el('div', { class: 'p-hint small', text: 'The first rule whose tag (or a sub-tag of it) a file has, decides where it goes. Destinations must be inside the library; missing folders are created. Tokens: {year} {month} {day} {tag} {subtag} {project} {status} {source} — empty parts are dropped.' }),
      list, dl,
      el('div', { class: 'row' },
        el('button', { class: 'btn small', type: 'button', text: '+ Add rule', onclick: () => { rules.push({ tag_id: 0, dest: '', date_prefix: false }); render(); invalidate(); } }),
        el('span', { class: 'grow' }),
        el('label', { class: 'chk' }, 'Apply to', scope)),
      out),
    buttons: [
      { label: 'Close' },
      { label: 'Save rules', onClick: async () => { if (await save()) app.toast('Rules saved'); } },
      { label: 'Save & preview', onClick: preview },
      { label: 'Move', id: 'go', primary: true, disabled: true, onClick: go },
    ],
  });
  render();
  invalidate();
}
