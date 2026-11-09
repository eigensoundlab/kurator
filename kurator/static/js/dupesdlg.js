// "Clean up duplicates…": one copy of each exact duplicate stays, the others go to the trash (system
// trash or Kurator's trash folder, ⚙ Settings → Delete). The copy that stays can get the tags of the
// others. Preview first; Ctrl+Z brings everything back.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { el, fmtBytes, fmtNum, projPrefs, setKids } from './util.js';

const KEEP = [['tags', 'the one with the most tags'], ['oldest', 'the oldest'], ['newest', 'the newest'], ['shortest', 'the one with the shortest path'],
  ['folder', 'the one inside this folder…'], ['project', 'the one in the open project']];

export async function openDupesDialog(app) {
  const ids = app.selected.size ? [...app.selected] : [...app.items.ids];
  if (!ids.length) { app.toast('Open Duplicates in the sidebar first'); return; }
  let tr = { mode: 'system' };
  try { tr = await api.trash(); } catch { /* shown as system */ }
  const pp = projPrefs(app);
  const P = pp.get('dupes', {});
  const keep = el('select', {}, KEEP.filter(([k]) => k !== 'project' || app.projects.active).map(([k, t]) => el('option', { value: k, text: t })));
  keep.value = KEEP.some(([k]) => k === P.keep) && (P.keep !== 'project' || app.projects.active) ? P.keep : 'tags';
  const folder = el('input', { type: 'text', spellcheck: 'false', value: P.folder || app.query.folder || '', placeholder: 'the folder whose copies stay' });
  const folderBox = destField(app, folder, { title: 'Keep the copies in…' });
  const merge = el('input', { type: 'checkbox' }); merge.checked = P.merge !== false;
  const out = el('div', { class: 'move-preview' });
  let planned = null;
  const body = () => ({ ids, keep: keep.value, folder: folder.value.trim(), project: app.projects.active || null, merge_tags: merge.checked });
  const where = tr.mode === 'kurator' ? "Kurator's trash folder" : 'the Recycle Bin / Trash';

  const preview = async () => {
    folderBox.style.display = keep.value === 'folder' ? '' : 'none';
    setKids(out, el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { planned = await api.dupesPlan(body()); } catch (e) { planned = null; setKids(out, el('div', { class: 'err', text: e.message })); d.buttons.go.disabled = true; return; }
    setKids(out,
      el('div', { class: 'move-sum' }, el('b', { text: planned.count ? `${fmtNum(planned.count)} duplicate${planned.count === 1 ? '' : 's'} go to ${where} · ${fmtBytes(planned.bytes)}` : 'Nothing to remove' }),
        el('div', { class: 'muted', text: `${fmtNum(planned.groups.length)} file${planned.groups.length === 1 ? '' : 's'} with copies; one of each stays.` })),
      el('div', { class: 'move-list dupes' }, planned.groups.map((g) => el('div', { class: 'dgroup' },
        el('div', { class: 'mv keep', title: g.keep.path }, el('span', { class: 'ok', text: '✓' }), el('span', { class: 'to', text: g.keep.path }),
          g.keep.in_scope ? null : el('span', { class: 'muted small', text: '(not in the view)' }), g.keep.tags ? el('span', { class: 'muted small', text: `${g.keep.tags} tags` }) : null),
        g.remove.map((r) => el('div', { class: 'mv', title: r.path }, el('span', { class: 'err', text: '✗' }), el('span', { class: 'from', text: r.path }),
          r.tags ? el('span', { class: 'muted small', text: `${r.tags} tags` }) : null))))));
    d.buttons.go.disabled = !planned.count;
    d.buttons.go.textContent = planned.count ? `Remove ${fmtNum(planned.count)}` : 'Remove';
  };
  const go = async (close) => {
    if (!planned?.count) return;
    d.buttons.go.disabled = true;
    let r;
    try { r = await api.dupesClean(body()); } catch (e) { app.toast(e.message); d.buttons.go.disabled = false; return; }
    pp.set('dupes', { keep: keep.value, folder: folder.value.trim(), merge: merge.checked });
    close();
    app.onDeleted(r, r.merged ? ` · tags of ${fmtNum(r.merged)} merged into the kept copies` : '');
  };
  for (const x of [keep, merge]) x.addEventListener('change', preview);
  folder.addEventListener('change', preview);
  const d = openDialog({
    title: `Clean up duplicates (${fmtNum(ids.length)} files)`, width: 760,
    body: el('div', { class: 'form' },
      el('div', { class: 'row' }, el('label', {}, 'Keep', keep), folderBox),
      el('label', { class: 'chk' }, merge, 'The copy that stays gets the tags of the removed ones'),
      el('div', { class: 'p-hint small', text: `Only files in ${app.selected.size ? 'the selection' : 'this view'} are removed; the copy that stays may be elsewhere. Removed files go to ${where} (⚙ Settings → Delete); Ctrl+Z brings them back.` }),
      out),
    buttons: [{ label: 'Cancel' }, { label: 'Remove', id: 'go', primary: true, danger: true, disabled: true, onClick: go }],
  });
  preview();
}
