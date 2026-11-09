// Settings: XMP sidecars (write tags + dates next to your files), keyword import, info.

import { api } from './api.js';
import { PH_HOME, PH_PAPER, phEnabled, phGridPref, phLib, phSetEnabled, phSetGridPref } from './playhead.js';
import { openDialog } from './dialog.js';
import { openPhLibraryDialog } from './phlibdlg.js';
import { el, fmtBytes, fmtNum, prefs, setKids } from './util.js';

export async function openSettingsDialog(app) {
  let st;
  try { st = await api.settings(); } catch (e) { app.toast(e.message); return; }
  const auto = el('input', { type: 'checkbox' });
  auto.checked = !!st.xmp_auto;
  auto.onchange = async () => {
    try { await api.saveSettings({ xmp_auto: auto.checked }); app.toast(auto.checked ? 'Sidecars are now written automatically' : 'Automatic sidecars off'); }
    catch (e) { app.toast(e.message); auto.checked = !auto.checked; }
  };
  const merge = el('input', { type: 'checkbox' });
  merge.checked = !!st.xmp_merge;
  merge.onchange = async () => {
    try { await api.saveSettings({ xmp_merge: merge.checked }); app.toast(merge.checked ? 'Keywords will also go into sidecars of other programs' : 'Sidecars of other programs are left alone'); }
    catch (e) { app.toast(e.message); merge.checked = !merge.checked; }
  };
  // Playhead analysis: a setting of this browser (it runs here, or with the command line on the server)
  const ph = el('input', { type: 'checkbox' });
  ph.checked = phEnabled();
  // ... and its strips as grid thumbnails of analysed sounds, in a look of Playhead's
  const gp = phGridPref();
  const phGrid = el('input', { type: 'checkbox' });
  phGrid.checked = gp.on;
  const phLook = el('select', { title: 'How Playhead draws the strips (its looks)' }, el('option', { value: gp.look, text: gp.look === 'auto' ? 'Automatic' : gp.look }));
  const fillLooks = () => phLib().then((P) => {   // only while Playhead is on: no request for it otherwise
    setKids(phLook, Object.entries(P.looks).map(([k, L]) => el('option', { value: k, text: L.name, title: L.desc })));
    phLook.value = P.looks[gp.look] ? gp.look : 'auto';
  }).catch(() => {});
  if (ph.checked) fillLooks();
  phGrid.onchange = () => phSetGridPref({ on: phGrid.checked });
  phLook.onchange = () => phSetGridPref({ look: phLook.value });
  const phRow = el('div', { style: { display: ph.checked ? '' : 'none' } },
    el('div', { class: 'row' }, el('label', { class: 'chk' }, phGrid, 'Playhead strips as thumbnails of analysed sounds'), phLook),
    el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', text: 'Analyse sounds…', title: 'Analyse many sounds at once (for the Look filter, the sorts and the grid strips)',
      onclick: () => openPhLibraryDialog(app) })));
  ph.onchange = () => {
    phSetEnabled(ph.checked);
    phRow.style.display = ph.checked ? '' : 'none';
    if (ph.checked) fillLooks();
    app.toast(ph.checked ? 'Playhead analysis is on: choose it in the waveform\'s style menu' : 'Playhead analysis is off');
  };
  const svgAll = el('input', { type: 'checkbox' });
  svgAll.checked = !!st.svg_all;
  svgAll.onchange = async () => {
    try { await api.saveSettings({ svg_all: svgAll.checked }); app.toast('SVG previews are being made again'); }
    catch (e) { app.toast(e.message); svgAll.checked = !svgAll.checked; }
  };
  // CMYK proofing profile (print preview of RGB pictures, ink views)
  const proofSel = el('select', {}, el('option', { value: '', text: 'None' }));
  const proofPath = el('input', { type: 'text', value: st.proof_profile || '', spellcheck: 'false', placeholder: 'or the path of a CMYK .icc file' });
  const saveProof = async (v) => {
    try {
      await api.saveSettings({ proof_profile: v });
      if (app.state) app.state.proof_profile = !!v;
      app.toast(v ? 'Proofing profile set' : 'No proofing profile');
    } catch (e) { app.toast(e.message); }
  };
  api.iccProfiles().then((r) => {
    for (const p of r.profiles) proofSel.append(el('option', { value: p.path, text: p.name }));
    proofSel.value = r.current && r.profiles.some((p) => p.path === r.current) ? r.current : '';
  }).catch(() => {});
  proofSel.onchange = () => { proofPath.value = proofSel.value; saveProof(proofSel.value); };
  proofPath.onchange = () => saveProof(proofPath.value.trim());
  // Delete: the system trash (default) or Kurator's own trash folder
  const delMode = el('select', {}, el('option', { value: 'system', text: 'Recycle Bin / Trash of the system' }),
    el('option', { value: 'kurator', text: "Kurator's trash folder (on the same disk)" }));
  delMode.value = st.delete_mode || 'system';
  const days = el('input', { type: 'number', min: '0', max: '3650', value: String(st.trash_days ?? 30), style: { width: '70px' } });
  const trashInfo = el('span', { class: 'muted small' });
  const emptyBtn = el('button', { class: 'btn', type: 'button', text: 'Empty now' });
  const daysRow = el('div', { class: 'row' }, el('label', { class: 'chk' }, 'Empty after', days, 'days (0: never)'), emptyBtn, trashInfo);
  const paintTrash = async () => {
    daysRow.style.display = delMode.value === 'kurator' ? '' : 'none';
    try {
      const t = await api.trash();
      trashInfo.textContent = t.files ? `${fmtNum(t.files)} files, ${fmtBytes(t.bytes)} kept` : 'empty';
      emptyBtn.disabled = !t.files;
    } catch { trashInfo.textContent = ''; }
  };
  delMode.onchange = async () => {
    try { await api.saveSettings({ delete_mode: delMode.value }); app.toast(delMode.value === 'kurator' ? "Deleted files go to Kurator's trash folder" : 'Deleted files go to the Recycle Bin / Trash'); }
    catch (e) { app.toast(e.message); }
    paintTrash();
  };
  days.onchange = async () => { try { await api.saveSettings({ trash_days: +days.value || 0 }); } catch (e) { app.toast(e.message); } };
  emptyBtn.onclick = async () => {
    if (!window.confirm("Delete the files in Kurator's trash folder for good? They can't be brought back afterwards.")) return;
    try { const r = await api.emptyTrash(); app.toast(`${fmtNum(r.removed)} files deleted for good`); } catch (e) { app.toast(e.message); }
    paintTrash();
  };
  paintTrash();
  const dropAsk = el('input', { type: 'checkbox' });
  dropAsk.checked = prefs.get('drop.ask', true);
  dropAsk.onchange = () => { prefs.set('drop.ask', dropAsk.checked); app.toast(dropAsk.checked ? 'Dropped files: Kurator asks first' : 'Dropped files are added without asking'); };
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
        r.merged ? `, ${fmtNum(r.merged)} of other programs updated` : '',
        `, ${fmtNum(r.unchanged)} already up to date`,
        r.skipped_untagged ? el('div', { class: 'muted', text: `${fmtNum(r.skipped_untagged)} untagged files skipped (tick "also untagged" to store their dates)` }) : null,
        r.skipped_project ? el('div', { class: 'muted', text: `${fmtNum(r.skipped_project)} files are inside a Unity/Godot/Unreal project or a git repository - no sidecars added there` }) : null,
        r.skipped_foreign.length ? el('div', { class: 'warn', text: merge.checked
          ? `${fmtNum(r.skipped_foreign.length)} sidecars of other programs could not be merged safely - left untouched (e.g. ${r.skipped_foreign[0]})`
          : `${fmtNum(r.skipped_foreign.length)} files already have a sidecar from another program - left untouched (e.g. ${r.skipped_foreign[0]}); tick "also update sidecars of other programs" to add the keywords there` }) : null,
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
      el('div', { class: 'p-hint small', text: 'A small "photo.jpg.xmp" file next to each image stores its tags, creation date, source, author and licence in the standard format read by Lightroom, Bridge, darktable and digiKam. Your images themselves are never modified.' }),
      el('label', { class: 'chk' }, auto, 'Write sidecars automatically when tags change'),
      el('label', { class: 'chk' }, merge, 'Also update sidecars of other programs: add the keywords, keep everything else (a copy is kept first)'),
      el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', text: 'Write sidecars now', onclick: write }), writeScope,
        el('label', { class: 'chk small' }, untagged, 'also untagged (date only)')),
      el('h3', { text: 'Import keywords' }),
      el('div', { class: 'p-hint small', text: 'Turns keywords already stored with your files into tags: embedded XMP (Photoshop, Lightroom…), Windows Explorer tags, and .xmp sidecars. "Creatures|Dragons" becomes Creatures/Dragons.' }),
      el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', text: 'Import keywords', onclick: doImport }), importScope),
      out,
      el('h3', { text: 'Delete' }),
      el('label', {}, 'Deleted files (Del, Clean up duplicates) go to', delMode),
      st.system_trash === false ? el('div', { class: 'warn small', text: 'The system trash needs the Send2Trash package: run update.bat / update.sh once.' }) : null,
      el('div', { class: 'p-hint small', text: "Either way Ctrl+Z brings files back, with their tags. From the system trash you can also restore files yourself - Kurator gives them their tags back. Kurator's trash folder is its data folder when that is on the same disk, else a hidden .kurator-trash folder at the top of the disk: deleting is instant and keeps everything until it is emptied." }),
      daysRow,
      el('h3', { text: 'Dropping files' }),
      el('label', { class: 'chk' }, dropAsk, 'Ask before adding files dropped from Explorer / Finder'),
      el('div', { class: 'p-hint small', text: 'Files already in the library get the tag or project they are dropped onto; new ones are copied into a folder (remembered per project). Without asking, the last folder and the tags of the drop are used.' }),
      el('h3', { text: 'Sounds' }),
      el('label', { class: 'chk' }, ph, 'Playhead analysis (experimental)'),
      el('div', { class: 'p-hint small' },
        'Adds "Playhead analysis" to the waveform\'s style menu: curves for loudness, activity, harshness and tension, sound events and their types, beats and sections for music, with a Look menu to compare ways of drawing them. A Playhead section in the side panel lists events, decay and material, time arrow, tempo and sections, and saves them as markers for REAPER or Audacity. Analysed sounds can be filtered by character (Look ▾) and sorted by harshness, ring time and more. It runs in this browser, or with the Playhead command line when it is installed; results are kept for next time. ',
        el('a', { href: PH_HOME, target: '_blank', rel: 'noopener noreferrer', text: 'Playhead' }), ' · ',
        el('a', { href: PH_PAPER, target: '_blank', rel: 'noopener noreferrer', text: 'About Playhead analysis (paper)' })),
      phRow,
      el('h3', { text: 'Previews' }),
      el('label', { class: 'chk' }, svgAll, 'SVG: show all objects, also those outside the page'),
      el('div', { class: 'p-hint small', text: 'Inkscape and Illustrator drawings often keep parts beside the page. With this on, previews and the viewer grow the page to take them in. Drawings whose page has no size always show everything.' }),
      el('h3', { text: 'Print' }),
      el('div', { class: 'p-hint small', text: 'A CMYK profile of your printing condition (e.g. Coated FOGRA39, GRACoL, PSO Uncoated) lets the viewer show RGB pictures as they would print: ◧ Inks shows the C, M, Y, K plates, total ink and colours out of gamut. CMYK pictures always have it, with their own profile. Profiles installed by Adobe apps, ECI or printer drivers are listed.' }),
      el('div', { class: 'row' }, proofSel), proofPath,
      el('h3', { text: 'About this installation' }),
      el('table', { class: 'kv' },
        el('tr', {}, el('th', { text: 'Version' }), el('td', { text: app.state?.version || '' })),
        el('tr', {}, el('th', { text: 'Catalog & cache' }), el('td', { text: st.data_dir })),
        el('tr', {}, el('th', { text: 'Live folder watching' }), el('td', { text: w?.enabled ? `on - ${w.watching} folders${Object.keys(w.errors || {}).length ? ` (${Object.keys(w.errors).length} could not be watched; use Rescan for those)` : ''}` : 'off (start without --no-watch to enable)' })))),
    buttons: [{ label: 'Close', primary: true }],
  });
}
