// Settings: XMP sidecars (write tags + dates next to your files), keyword import, info.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtNum, setKids } from './util.js';

export async function openSettingsDialog(app) {
  let st;
  try { st = await api.settings(); } catch (e) { app.toast(e.message); return; }
  const auto = el('input', { type: 'checkbox' });
  auto.checked = !!st.xmp_auto;
  auto.onchange = async () => {
    try { await api.saveSettings({ xmp_auto: auto.checked }); app.toast(auto.checked ? 'Sidecars are now written automatically' : 'Automatic sidecars off'); }
    catch (e) { app.toast(e.message); auto.checked = !auto.checked; }
  };
  const svgAll = el('input', { type: 'checkbox' });
  svgAll.checked = !!st.svg_all;
  svgAll.onchange = async () => {
    try { await api.saveSettings({ svg_all: svgAll.checked }); app.toast('SVG previews are being made again'); }
    catch (e) { app.toast(e.message); svgAll.checked = !svgAll.checked; }
  };
  const untagged = el('input', { type: 'checkbox' });
  const out = el('div', { class: 'move-preview' });

  const nSel = app.selected.size;
  const scopeSel = () => el('select', {},
    el('option', { value: 'selection', text: `Selection (${fmtNum(nSel)})`, disabled: !nSel }),
    el('option', { value: 'view', text: `Current view (${fmtNum(app.items.ids.length)})` }),
    el('option', { value: 'library', text: 'Whole library' }));
  const writeScope = scopeSel();
  const importScope = scopeSel();
  writeScope.value = importScope.value = nSel ? 'selection' : 'view';

  const ids = async (scope) => {
    if (scope === 'selection') return [...app.selected];
    if (scope === 'view') return [...app.items.ids];
    return (await api.items({})).ids;
  };
  const busy = (msg) => setKids(out, el('div', { class: 'p-hint small', text: msg }));

  const write = async () => {
    busy('Writing sidecars…');
    try {
      const r = await api.writeSidecars(await ids(writeScope.value), untagged.checked);
      setKids(out, el('div', { class: 'move-sum' },
        el('b', { text: `${fmtNum(r.written)} sidecar${r.written === 1 ? '' : 's'} written` }),
        `, ${fmtNum(r.unchanged)} already up to date`,
        r.skipped_untagged ? el('div', { class: 'muted', text: `${fmtNum(r.skipped_untagged)} untagged files skipped (tick "also untagged" to store their dates)` }) : null,
        r.skipped_project ? el('div', { class: 'muted', text: `${fmtNum(r.skipped_project)} files are inside a Unity/Godot/Unreal project or a git repository - no sidecars added there` }) : null,
        r.skipped_foreign.length ? el('div', { class: 'warn', text: `${fmtNum(r.skipped_foreign.length)} files already have a sidecar from another program - left untouched (e.g. ${r.skipped_foreign[0]})` }) : null,
        r.errors.length ? el('div', { class: 'err', text: `${r.errors.length} errors, e.g. ${r.errors[0]}` }) : null));
    } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); }
  };

  const doImport = async () => {
    busy('Reading keywords…');
    try {
      const r = await api.importKeywords(await ids(importScope.value));
      setKids(out, el('div', { class: 'move-sum' },
        el('b', { text: `${fmtNum(r.with_keywords)} of ${fmtNum(r.files)} files had keywords` }),
        ` · ${fmtNum(r.tags_created)} new tags · ${fmtNum(r.applied)} tags applied`));
      await app.tags.refresh();
      app.reload();
    } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); }
  };

  const w = app.state?.watch;
  openDialog({
    title: 'Settings',
    width: 640,
    body: el('div', { class: 'form' },
      el('h3', { text: 'XMP sidecars' }),
      el('div', { class: 'p-hint small', text: 'A small "photo.jpg.xmp" file next to each image stores its tags and creation date in the standard format read by Lightroom, Bridge, darktable and digiKam. Your images themselves are never modified, and sidecars made by other programs are never overwritten.' }),
      el('label', { class: 'chk' }, auto, 'Write sidecars automatically when tags change'),
      el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', text: 'Write sidecars now', onclick: write }), writeScope,
        el('label', { class: 'chk small' }, untagged, 'also untagged (date only)')),
      el('h3', { text: 'Import keywords' }),
      el('div', { class: 'p-hint small', text: 'Turns keywords already stored with your files into tags: embedded XMP (Photoshop, Lightroom…), Windows Explorer tags, and .xmp sidecars. "Creatures|Dragons" becomes Creatures/Dragons.' }),
      el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', text: 'Import keywords', onclick: doImport }), importScope),
      out,
      el('h3', { text: 'Previews' }),
      el('label', { class: 'chk' }, svgAll, 'SVG: show all objects, also those outside the page'),
      el('div', { class: 'p-hint small', text: 'Inkscape and Illustrator drawings often keep parts beside the page. With this on, previews and the viewer grow the page to take them in. Drawings whose page has no size always show everything.' }),
      el('h3', { text: 'About this installation' }),
      el('table', { class: 'kv' },
        el('tr', {}, el('th', { text: 'Version' }), el('td', { text: app.state?.version || '' })),
        el('tr', {}, el('th', { text: 'Catalog & cache' }), el('td', { text: st.data_dir })),
        el('tr', {}, el('th', { text: 'Live folder watching' }), el('td', { text: w?.enabled ? `on - ${w.watching} folders${Object.keys(w.errors || {}).length ? ` (${Object.keys(w.errors).length} could not be watched; use Rescan for those)` : ''}` : 'off (start without --no-watch to enable)' })))),
    buttons: [{ label: 'Close', primary: true }],
  });
}
