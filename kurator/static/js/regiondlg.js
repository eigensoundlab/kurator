// Save a selected region of a sound as a new file: name, folder, format, fades, normalising.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { el, prefs, setKids } from './util.js';

const FORMATS = [['same', 'Same as the original'], ['wav24', 'WAV 24-bit'], ['wav16', 'WAV 16-bit'], ['wav32f', 'WAV 32-bit float'],
  ['flac', 'FLAC (lossless)'], ['ogg', 'Ogg Vorbis'], ['mp3', 'MP3']];

export async function openRegionDialog(app, id, sel) {
  let item;
  try { item = await api.item(id); } catch (e) { app.toast(e.message); return; }
  const P = prefs.get('region', {});
  const stem = item.name.replace(/\.[^.]+$/, '');
  const t = (x) => `${Math.floor(x / 60)}m${(x % 60).toFixed(2).padStart(5, '0').replace('.', '_')}s`;
  const field = (label, input, hint) => el('label', { class: 'sf' }, el('span', { text: label }), input, hint ? el('small', { class: 'muted', text: hint }) : null);
  const name = el('input', { type: 'text', value: `${stem}_${t(sel.a)}`, spellcheck: 'false' });
  const dest = el('input', { type: 'text', value: P.sameFolder === false && P.dest ? P.dest : item.dir, spellcheck: 'false' });
  const fmt = el('select', {}, FORMATS.map(([k, x]) => el('option', { value: k, text: x })));
  fmt.value = P.format || 'same';
  const num = (v) => el('input', { type: 'number', min: '0', max: '60000', step: '1', value: String(v), style: { width: '90px' } });
  const fin = num(P.fade_in ?? 5), fout = num(P.fade_out ?? 20);
  const norm = el('select', {}, [['', 'Keep the level'], ['-0.1', 'Normalise to −0.1 dBFS'], ['-1', 'Normalise to −1 dBFS'], ['-3', 'Normalise to −3 dBFS'], ['-6', 'Normalise to −6 dBFS']]
    .map(([k, x]) => el('option', { value: k, text: x })));
  norm.value = P.normalize ?? '';
  const msg = el('div');
  const d = openDialog({
    title: `Save region ${(sel.b - sel.a).toFixed(2)} s of ${item.name}`, width: 560,
    body: el('div', { class: 'sheetcol' }, field('Name', name), field('Save into', destField(app, dest, { title: 'Save the region into…' })), field('Format', fmt),
      el('div', { class: 'row' }, field('Fade in (ms)', fin), field('Fade out (ms)', fout)),
      field('Level', norm), el('div', { class: 'p-hint small', text: 'Short fades avoid clicks at the cut. The original file is not changed.' }), msg),
    buttons: [{ label: 'Close' }, { label: 'Save', primary: true, onClick: async () => {
      const body = { start: sel.a, end: sel.b, name: name.value.trim(), dest: dest.value.trim(), format: fmt.value,
        fade_in: (+fin.value || 0) / 1000, fade_out: (+fout.value || 0) / 1000, normalize: norm.value === '' ? null : +norm.value };
      prefs.set('region', { format: fmt.value, fade_in: +fin.value || 0, fade_out: +fout.value || 0, normalize: norm.value,
        dest: dest.value.trim(), sameFolder: dest.value.trim() === item.dir });
      try {
        const r = await api.exportRegion(id, body);
        setKids(msg, el('div', { class: 'ok', text: `Saved ${r.file.split(/[\\/]/).pop()}` }),
          el('div', { class: 'row' }, el('button', { class: 'btn small', text: 'Show the folder', onclick: () => { d.close(); app.viewer?.close(); app.openPath(r.file.replace(/[\\/][^\\/]*$/, '')); } })));
        name.value = `${stem}_${t(sel.a)}`;
      } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
      return false;
    } }],
  });
}
