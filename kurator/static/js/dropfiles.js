// Files dropped onto Kurator from Explorer, Finder or another app. A web page never sees where a
// dropped file is - only its name, size, modified time and bytes - so:
//   * files already in the library are found by name, size and time and get the tags in place;
//   * new files are copied into a folder you choose (keeping their time), then tagged.
// The desktop app knows the paths: files in the library are found by path, new ones are copied from
// where they are (nothing is uploaded), and dropped folders can be added to the library.
// Which tags: the tag or project you drop onto (sidebar, tag bar, project chip), else the open
// project and the tags the view is filtered by. One confirmation with "Don't ask again" (⚙ Settings
// brings it back); Ctrl+Z undoes the tags and deletes the copied files.

import { api } from './api.js';
import { ask, openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { inShell, onNativeDrop } from './shell.js';
import { el, fmtNum, prefs, projPrefs, setKids } from './util.js';

const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');

function targetTags(app, tagEl) {
  if (tagEl) return [+tagEl.dataset.tagId].filter((id) => app.tags.byId.has(id));
  const ids = [];
  if (app.projects.active) ids.push(app.projects.active);
  for (const t of (app.query.tag || '').split(',').filter(Boolean)) ids.push(+t);
  return [...new Set(ids)].filter((id) => app.tags.byId.has(id));
}

const tagLabel = (app, id) => {
  const t = app.tags.byId.get(id);
  return t ? `${t.facet === 'project' ? '◆' : '#'} ${t.name}` : '?';
};

export function installDrop(app) {
  const box = el('div', { class: 'drop-box' });
  const overlay = el('div', { class: 'drop-overlay' }, box);
  document.body.append(overlay);
  let depth = 0;
  let hot = null;
  const mark = (e) => {
    const t = e.target?.closest?.('[data-tag-id]') || null;
    if (t !== hot) { hot?.classList.remove('droptarget'); hot = t; hot?.classList.add('droptarget'); }
    const tags = targetTags(app, hot);
    box.textContent = tags.length ? `Drop to add to ${tags.map((id) => tagLabel(app, id)).join(', ')}`
      : 'Drop files here: new ones are copied into the library - or drop them onto a tag or project';
  };
  const hide = () => { depth = 0; overlay.classList.remove('show'); hot?.classList.remove('droptarget'); hot = null; };
  // the desktop app: its own drop events, with the paths (the page gets no drop events for files)
  const native = inShell() && onNativeDrop((d) => {
    const under = d.x >= 0 ? document.elementFromPoint(d.x, d.y) : null;
    if (d.type === 'leave') { hide(); return; }
    if (d.type === 'drop') {
      const tagEl = under?.closest?.('[data-tag-id]') || null;
      hide();
      if (!document.body.classList.contains('modal-open')) droppedPaths(app, d.paths, targetTags(app, tagEl), !!tagEl);
      return;
    }
    if (document.body.classList.contains('modal-open')) return;
    overlay.classList.add('show');
    mark({ target: under });
  });
  if (native) return;
  document.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    overlay.classList.add('show');
    mark(e);
  });
  document.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();                       // (otherwise the browser opens the file instead)
    e.dataTransfer.dropEffect = document.body.classList.contains('modal-open') ? 'none' : 'copy';
    mark(e);
  });
  document.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) hide();
  });
  document.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    const tagEl = e.target?.closest?.('[data-tag-id]') || null;
    hide();
    if (document.body.classList.contains('modal-open')) return;
    // the files must be taken now: the drop's data is gone once this handler returns
    const files = [];
    let folders = 0;
    const items = [...(e.dataTransfer.items || [])].filter((it) => it.kind === 'file');
    if (items.length) {
      for (const it of items) {
        if (it.webkitGetAsEntry?.()?.isDirectory) { folders++; continue; }
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    } else files.push(...e.dataTransfer.files);
    dropped(app, files, folders, targetTags(app, tagEl), !!tagEl);
  });
}

async function dropped(app, files, folders, tagIds, onTag) {
  if (!files.length) { app.toast(folders ? 'Drop files, not folders - add a folder to the library with Add… in the sidebar' : 'Nothing to add'); return; }
  let m;
  try { m = await api.dropMatch(files.map((f) => ({ name: f.name, size: f.size, mtime: f.lastModified }))); } catch (e) { app.toast(e.message); return; }
  const known = [], fresh = [];
  files.forEach((f, i) => (m.ids[i]?.length ? known.push({ f, ids: m.ids[i] }) : fresh.push(f)));
  confirmDrop(app, known, fresh, Array.from({ length: folders }, () => ({})), tagIds, onTag);
}

/** The desktop app's drop: paths of files and folders. */
async function droppedPaths(app, paths, tagIds, onTag) {
  if (!paths.length) return;
  let r;
  try { r = await api.dropPaths(paths); } catch (e) { app.toast(e.message); return; }
  const known = r.files.filter((f) => f.ids.length).map((f) => ({ f, ids: f.ids }));
  const fresh = r.files.filter((f) => !f.ids.length);
  if (r.skipped.length) console.warn('Dropped but not added', r.skipped);
  if (!r.files.length) {
    const out = r.folders.filter((f) => !f.in_library);
    if (!out.length) {
      if (r.folders.length === 1) app.openPath(r.folders[0].path);
      else app.toast(r.folders.length ? 'Those folders are in the library already' : `Nothing to add${r.skipped.length ? `: ${r.skipped[0].error}` : ''}`);
      return;
    }
    const list = out.map((f) => f.path).join('\n');
    if (!(await ask(`Add ${out.length === 1 ? 'this folder' : `these ${fmtNum(out.length)} folders`} to the library?\n\n${list}\n\nKurator indexes ${out.length === 1 ? 'it' : 'them'} with all subfolders. Files on disk are not changed.`, { ok: 'Add to library' }))) return;
    for (const f of out) await app.sidebar.addRoot(f.path);
    return;
  }
  confirmDrop(app, known, fresh, r.folders, tagIds, onTag);
}

function confirmDrop(app, known, fresh, folders, tagIds, onTag) {
  const files = [...known, ...fresh];
  const newFolders = folders.filter((f) => f.path && !f.in_library);
  const pp = projPrefs(app);
  const here = app.query.folder && app.isLibraryPath(app.query.folder) ? app.query.folder : '';
  const dest0 = pp.get('drop.dest', '') || here;
  if (!prefs.get('drop.ask', true) && !newFolders.length && (!fresh.length || dest0) && (tagIds.length || fresh.length)) {
    run(app, known, fresh, tagIds, dest0, true, folders.filter((f) => !f.path).length, []);
    return;
  }
  const checks = tagIds.map((id) => { const c = el('input', { type: 'checkbox' }); c.checked = true; return [id, c]; });
  const dest = el('input', { type: 'text', spellcheck: 'false', value: dest0, placeholder: 'Folder for the new files, e.g. D:\\Art\\Inbox' });
  const copy = el('input', { type: 'checkbox' }); copy.checked = true;
  const never = el('input', { type: 'checkbox' });
  const addFolders = el('input', { type: 'checkbox' }); addFolders.checked = true;
  const names = (list) => el('div', { class: 'muted small', text: list.slice(0, 6).map((x) => (x.f || x).name).join(', ') + (list.length > 6 ? ` … +${fmtNum(list.length - 6)}` : '') });
  const msg = el('div');
  const d = openDialog({
    title: `Add ${fmtNum(files.length)} dropped file${files.length === 1 ? '' : 's'}`, width: 640,
    body: el('div', { class: 'form' },
      checks.length ? el('div', {}, el('div', { class: 'p-hint small', text: onTag ? 'Dropped onto:' : 'The open project and the tags this view is filtered by:' }),
        checks.map(([id, c]) => el('label', { class: 'chk' }, c, tagLabel(app, id))))
        : el('div', { class: 'p-hint small', text: 'No tag or project to add: drop onto a tag or project (sidebar, tag bar), or open a project or filter by a tag first. New files can still be copied in.' }),
      known.length ? el('div', { class: 'drop-part' }, el('b', { text: `${fmtNum(known.length)} already in your library` }), checks.length ? ' get the ticked tags.' : ' - nothing to do without a tag.', names(known)) : null,
      fresh.length ? el('div', { class: 'drop-part' },
        el('label', { class: 'chk' }, copy, el('span', {}, el('b', { text: `${fmtNum(fresh.length)} new` }), ': copy into this folder (they keep their date), then tag')),
        destField(app, dest, { title: 'Copy the dropped files into…' }), names(fresh),
        el('div', { class: 'p-hint small', text: inShell() ? 'Copied from where they are. Inside the library they are indexed and tagged; elsewhere only copied.'
          : 'A page can\'t see where dropped files are, so new ones are copied. Inside the library they are indexed and tagged; elsewhere only copied.' })) : null,
      newFolders.length ? el('div', { class: 'drop-part' },
        el('label', { class: 'chk' }, addFolders, el('span', {}, el('b', { text: `${fmtNum(newFolders.length)} folder${newFolders.length === 1 ? '' : 's'}` }), ': add to the library (indexed with all subfolders)')),
        el('div', { class: 'muted small', text: newFolders.map((f) => f.path).join(', ') })) : null,
      folders.length > newFolders.length ? el('div', { class: 'muted small', text: folders[0].path
        ? `${fmtNum(folders.length - newFolders.length)} folder${folders.length - newFolders.length === 1 ? ' is' : 's are'} in the library already.`
        : `${fmtNum(folders.length)} folder${folders.length === 1 ? '' : 's'} left out - add folders with Add… in the sidebar.` }) : null,
      el('label', { class: 'chk' }, never, 'Don\'t ask again (⚙ Settings → Dropping files to ask again)'),
      msg),
    buttons: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: (close) => {
      const tags = checks.filter(([, c]) => c.checked).map(([id]) => id);
      const doCopy = copy.checked && fresh.length > 0;
      const add = addFolders.checked ? newFolders.map((f) => f.path) : [];
      if (doCopy && !dest.value.trim()) { setKids(msg, el('div', { class: 'err', text: 'Choose a folder for the new files' })); dest.focus(); return; }
      if (!tags.length && !doCopy && !add.length) { setKids(msg, el('div', { class: 'err', text: 'Nothing to do: tick a tag, or copy the new files' })); return; }
      if (doCopy) pp.set('drop.dest', dest.value.trim());
      if (never.checked) prefs.set('drop.ask', false);
      close();
      run(app, known, fresh, tags, dest.value.trim(), doCopy, folders.filter((f) => !f.path).length, add);
    } }],
  });
  return d;
}

async function run(app, known, fresh, tagIds, dest, doCopy, folders, addFolders) {
  let got = null;
  const failed = [];
  if (doCopy && fresh.length) {
    const batch = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let k = 0;
    for (const f of fresh) {
      app.toast(`Copying ${fmtNum(++k)} / ${fmtNum(fresh.length)}: ${f.name}`, 60000);
      try { await (f.path ? api.dropCopy(batch, dest, f.path) : api.dropFile(batch, dest, f)); } catch (e) { failed.push(e.message); }
    }
    try { got = await api.dropDone(batch); } catch (e) { app.toast(e.message); }
  }
  const ids = [...new Set([...known.flatMap((x) => x.ids), ...(got?.ids || [])])];
  const changes = [];
  for (const t of tagIds) {
    if (!ids.length) break;
    try {
      const r = await api.applyTag(t, ids, 'add');
      app.updateLocalTags(t, r.added, r.removed);
      for (const a of r.also_removed || []) app.updateLocalTags(a.tag_id, [], a.ids);
      changes.push({ tagId: t, added: r.added, also: r.also_removed || [] });
    } catch (e) { failed.push(e.message); }
  }
  if (got?.op_id || changes.some((c) => c.added.length)) app.pushHistory({ kind: 'drop', opId: got?.op_id || null, changes });
  const parts = [];
  if (got?.received) parts.push(`${fmtNum(got.received)} copied${got.indexed < got.received ? ` (${fmtNum(got.received - got.indexed)} outside the library)` : ''}`);
  const tagged = changes.reduce((n, c) => n + c.added.length, 0);
  if (tagIds.length) parts.push(tagged ? `${tagIds.map((id) => tagLabel(app, id)).join(', ')} added` : 'already tagged');
  if (folders) parts.push(`${fmtNum(folders)} folder${folders === 1 ? '' : 's'} left out`);
  for (const p of addFolders) { if (await app.sidebar.addRoot(p)) parts.push(`${p} added to the library`); }
  if (failed.length) { parts.push(`${fmtNum(failed.length)} failed: ${failed[0]}`); console.warn('Drop failures', failed); }
  app.toast(`Dropped files: ${parts.join(' · ') || 'nothing to do'}${got?.op_id || tagged ? ' · Ctrl+Z to undo' : ''}`, failed.length ? 6000 : 4000);
  app.tags.refreshSoon();
  app.refreshState();
  app.reload();
}
