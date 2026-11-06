// Markers from a Playhead analysis: sections as regions; sound events, bar lines and onsets as markers.
// Playhead.toMarkers() picks them (as `playhead markers` does); Kurator only writes the file:
//   REAPER      the Region/Marker Manager's CSV (#, Name, Start, End, Length, Color), times as m:ss.mmm
//   Audacity    a label track (start, end, text in seconds, tab-separated)
//   TSV         kind, start, end, name - the same columns as `playhead markers`

import { openDialog } from './dialog.js';
import { phLib } from './playhead.js';
import { el, prefs } from './util.js';

const KINDS = [
  ['sections', 'Sections (as regions)', (d) => d.sections?.length || 0],
  ['events', 'Sound events', (d) => d.events?.length || 0],
  ['downbeats', 'Bar lines', (d) => (d.rhythm?.reliable ? d.rhythm.downbeats?.length || 0 : 0)],
  ['onsets', 'Onsets (every attack)', (d) => d.onsets?.length || 0],
];
const FORMATS = {
  reaper: { label: 'REAPER regions and markers (.csv)', ext: 'markers.csv', type: 'text/csv',
    hint: 'In REAPER: right-click the ruler, choose Minutes:Seconds; then View → Region/Marker Manager → Options → Import. Times count from the start of the project.' },
  audacity: { label: 'Audacity labels (.txt)', ext: 'labels.txt', type: 'text/plain', hint: 'In Audacity: File → Import → Labels…' },
  tsv: { label: 'Tab-separated, like playhead markers (.tsv)', ext: 'markers.tsv', type: 'text/tab-separated-values', hint: 'kind, start, end (regions), name; times in seconds.' },
};

/** m:ss.mmm (h:mm:ss.mmm from an hour on), as REAPER's Minutes:Seconds ruler shows time. */
export function clock(t) {
  const ms = Math.max(0, Math.round(t * 1000));
  const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = (ms % 60000) / 1000;
  const ss = s.toFixed(3).padStart(6, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** The text of a marker file. marks: [{ kind: 'region' | 'marker', start, end, name }]. */
export function markersText(marks, format) {
  const csv = (s) => (/[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const flat = (s) => String(s ?? '').replace(/[\t\r\n]+/g, ' ');
  if (format === 'reaper') {
    let r = 0, m = 0;
    const lines = ['#,Name,Start,End,Length,Color'];
    for (const k of marks) {
      if (k.kind === 'region' && k.end != null) lines.push([`R${++r}`, csv(flat(k.name)), clock(k.start), clock(k.end), clock(k.end - k.start), ''].join(','));
      else lines.push([`M${++m}`, csv(flat(k.name)), clock(k.start), '', '', ''].join(','));
    }
    return lines.join('\r\n') + '\r\n';
  }
  if (format === 'audacity') {
    return marks.map((k) => [k.start.toFixed(6), (k.end ?? k.start).toFixed(6), flat(k.name)].join('\t')).join('\n') + '\n';
  }
  return ['kind\tstart\tend\tname', ...marks.map((k) => [k.kind, k.start, k.end ?? '', flat(k.name)].join('\t'))].join('\n') + '\n';
}

export async function openPhMarkersDialog(app, doc, fileName) {
  const P = await phLib();
  const saved = prefs.get('playhead.markers', { kinds: ['sections', 'events'], format: 'reaper' });
  const boxes = KINDS.map(([k, label, count]) => {
    const n = count(doc);
    const c = el('input', { type: 'checkbox', value: k });
    c.checked = n > 0 && saved.kinds.includes(k);
    c.disabled = !n;
    return el('label', { class: 'chk' + (n ? '' : ' muted') }, c, `${label} (${n})`);
  });
  const fmt = el('select', {}, Object.entries(FORMATS).map(([k, f]) => el('option', { value: k, text: f.label })));
  fmt.value = FORMATS[saved.format] ? saved.format : 'reaper';
  const hint = el('div', { class: 'p-hint small' });
  const count = el('div', { class: 'small' });
  const kinds = () => boxes.map((b) => b.querySelector('input')).filter((c) => c.checked).map((c) => c.value);
  const marks = () => P.toMarkers(doc, { kinds: kinds() });
  const update = () => {
    hint.textContent = FORMATS[fmt.value].hint;
    const n = kinds().length ? marks().length : 0;
    count.textContent = n ? `${n} markers and regions` : 'Nothing chosen';
    d.buttons.Save.disabled = !n;
  };
  const save = (close) => {
    const f = FORMATS[fmt.value];
    prefs.set('playhead.markers', { kinds: kinds(), format: fmt.value });
    const url = URL.createObjectURL(new Blob([markersText(marks(), fmt.value)], { type: `${f.type};charset=utf-8` }));
    const base = String(fileName || 'sound').replace(/\.[^.]+$/, '');
    el('a', { href: url, download: `${base}.${f.ext}` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    app.toast(`Saved ${base}.${f.ext} (in your downloads)`);
    close();
  };
  const d = openDialog({
    title: `Markers for ${fileName}`, width: 480,
    body: el('div', { class: 'projdlg phmarkdlg' }, ...boxes, el('label', {}, 'Format', fmt), hint, count),
    buttons: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: save }],
  });
  for (const b of boxes) b.querySelector('input').addEventListener('change', update);
  fmt.addEventListener('change', update);
  update();
  return d;
}
