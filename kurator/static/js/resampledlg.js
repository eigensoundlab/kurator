// "Resample…": resized copies of the selected pictures (next to the originals, in a subfolder or in
// one folder). Preview first; runs in the background with progress; Ctrl+Z deletes the copies.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { el, fmtNum, projPrefs, setKids } from './util.js';

const MODES = [['long', 'Longest side'], ['percent', 'Percent'], ['width', 'Width'], ['height', 'Height'], ['fit', 'Fit inside'], ['exact', 'Exact size (may stretch)']];
const FILTERS = [['lanczos', 'Lanczos (sharp)'], ['bicubic', 'Bicubic'], ['bilinear', 'Bilinear'], ['box', 'Area average (smooth reduction)'], ['nearest', 'Nearest neighbour (pixel art)']];
const FORMATS = [['keep', 'As the original (work files: PNG)'], ['png', 'PNG'], ['jpg', 'JPEG'], ['webp', 'WebP']];
const WHERE = [['beside', 'Next to the originals'], ['subfolder', 'In a subfolder next to each'], ['folder', 'In one folder']];
const PRESETS = [
  ['50 %', { mode: 'percent', value: 50 }], ['25 %', { mode: 'percent', value: 25 }],
  ['4096 px', { mode: 'long', value: 4096 }], ['2048 px', { mode: 'long', value: 2048 }], ['1024 px', { mode: 'long', value: 1024 }], ['512 px', { mode: 'long', value: 512 }],
  ['Pixel art ×2', { mode: 'percent', value: 200, filter: 'nearest', enlarge: true }], ['Pixel art ×4', { mode: 'percent', value: 400, filter: 'nearest', enlarge: true }],
];

export function openResampleDialog(app, only = null) {
  const ids = only || (app.selected.size ? app.items.ids.filter((id) => app.selected.has(id)) : app.tagTargets());
  if (!ids.length) { app.toast('Select some pictures first'); return; }
  const pp = projPrefs(app);
  const P = pp.get('resample', {});
  const sel = (opts, v) => { const s = el('select', {}, opts.map(([k, t]) => el('option', { value: k, text: t }))); s.value = v; return s; };
  const num = (v, min, max, w = '90px') => el('input', { type: 'number', min: String(min), max: String(max), value: v ?? '', style: { width: w } });
  const chk = (v) => { const c = el('input', { type: 'checkbox' }); c.checked = !!v; return c; };
  const mode = sel(MODES, P.mode || 'long');
  const value = num(P.value ?? 2048, 1, 30000);
  const value2 = num(P.value2 ?? 2048, 1, 30000);
  const unit = el('span', { class: 'muted' });
  const by = el('span', { class: 'muted', text: '×' });
  const filter = sel(FILTERS, P.filter || 'lanczos');
  const enlarge = chk(P.enlarge);
  const format = sel(FORMATS, P.format || 'keep');
  const quality = num(P.quality ?? 90, 1, 100, '70px');
  const qualityBox = el('label', { class: 'chk' }, 'Quality', quality, el('small', { class: 'muted', text: 'JPEG / WebP (WebP 100 = lossless)' }));
  const where = sel(WHERE, P.where || 'beside');
  const folder = el('input', { type: 'text', spellcheck: 'false', value: P.where === 'folder' ? (P.folder || '') : (P.subfolder || 'resampled') });
  const folderBox = destField(app, folder, { title: 'Save the resampled copies in…' });
  const listId = folder.getAttribute('list');
  const suffix = el('input', { type: 'text', spellcheck: 'false', value: P.suffix ?? '_{w}x{h}', style: { width: '140px' } });
  const keepDate = chk(P.keep_date !== false);
  const keepTags = chk(P.keep_tags !== false);
  const out = el('div', { class: 'move-preview' });
  let planned = null;
  let running = false;

  const paint = () => {
    const m = mode.value;
    unit.textContent = m === 'percent' ? '%' : 'px';
    const two = m === 'fit' || m === 'exact';
    value2.style.display = by.style.display = two ? '' : 'none';
    qualityBox.style.display = format.value === 'jpg' || format.value === 'webp' || format.value === 'keep' ? '' : 'none';
    folderBox.style.display = where.value === 'beside' ? 'none' : '';
    folderBox.querySelector('button').style.display = where.value === 'folder' ? '' : 'none';
    if (where.value === 'folder') folder.setAttribute('list', listId); else folder.removeAttribute('list');
    folder.placeholder = where.value === 'folder' ? 'Folder, e.g. D:\\Delivery\\Web' : 'Subfolder name, e.g. resampled';
  };
  const body = () => ({ ids, mode: mode.value, value: +value.value, value2: +value2.value || null, enlarge: enlarge.checked,
    filter: filter.value, format: format.value, quality: +quality.value || 90, where: where.value,
    folder: where.value === 'beside' ? '' : folder.value.trim(), suffix: suffix.value, keep_date: keepDate.checked, keep_tags: keepTags.checked });
  const remember = () => {
    const b = body();
    pp.set('resample', { mode: b.mode, value: b.value, value2: b.value2, filter: b.filter, enlarge: b.enlarge, format: b.format,
      quality: b.quality, where: b.where, suffix: b.suffix, keep_date: b.keep_date, keep_tags: b.keep_tags,
      folder: b.where === 'folder' ? b.folder : P.folder, subfolder: b.where === 'subfolder' ? b.folder : P.subfolder });
  };
  const invalidate = () => {
    if (running) return;
    planned = null;
    paint();
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = 'Resample';
    setKids(out, el('div', { class: 'p-hint small', text: 'Press Preview to see the new sizes and names. Originals are never changed.' }));
  };
  const base = (p) => p.split(/[\\/]/).pop();
  const preview = async () => {
    if (running) return;
    setKids(out, el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { planned = await api.resamplePlan(body()); } catch (e) { planned = null; setKids(out, el('div', { class: 'err', text: e.message })); return; }
    const sk = Object.entries(planned.skipped || {});
    setKids(out,
      el('div', { class: 'move-sum' }, el('b', { text: `${fmtNum(planned.count)} resampled cop${planned.count === 1 ? 'y' : 'ies'}` }),
        sk.length ? el('div', { class: 'muted', text: 'Left out: ' + sk.map(([r, c]) => `${fmtNum(c)} ${r}`).join(', ') + (sk.some(([r]) => r.startsWith('already')) ? ' (tick "Also enlarge" to scale them up)' : '') }) : null),
      el('div', { class: 'move-list' }, planned.items.map((m) => el('div', { class: 'mv' },
        el('span', { class: 'from', text: base(m.from) }), el('span', { class: 'muted small', text: ` ${m.size[0]}×${m.size[1]}` }),
        el('span', { class: 'arrow', text: '⇒' }), el('span', { class: 'to', text: base(m.to) }), el('span', { class: 'muted small', text: ` ${m.new[0]}×${m.new[1]}` })))),
      planned.count > planned.items.length ? el('div', { class: 'muted', text: `… and ${fmtNum(planned.count - planned.items.length)} more` }) : null);
    d.buttons.go.disabled = !planned.count;
    d.buttons.go.textContent = `Resample ${fmtNum(planned.count)}`;
  };
  const run = async () => {
    if (!planned || running) return;
    remember();
    try { await api.resampleRun(body()); } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); return; }
    running = true;
    d.buttons.go.disabled = true;
    d.buttons.Cancel.textContent = 'Stop';
    const bar = el('div', { class: 'pbar' }, el('span'));
    const line = el('div', { class: 'small' });
    setKids(out, bar, line);
    const tick = async () => {
      let st;
      try { st = await api.resampleJob(); } catch { setTimeout(tick, 800); return; }
      bar.firstChild.style.width = `${st.total ? Math.round((100 * st.done) / st.total) : 100}%`;
      line.textContent = `${fmtNum(st.done)} / ${fmtNum(st.total)} pictures`;
      if (st.running) { setTimeout(tick, 300); return; }
      running = false;
      d.buttons.Cancel.textContent = 'Close';
      const r = st.result || {};
      setKids(out, el('div', { class: r.failed ? 'err' : 'ok', text: `${r.cancelled ? 'Stopped' : 'Done'}: ${fmtNum(r.written || 0)} resampled cop${r.written === 1 ? 'y' : 'ies'}${r.failed ? `, ${r.failed} failed` : ''} · Ctrl+Z deletes them again` }),
        st.errors?.length ? el('div', { class: 'err small', text: st.errors.slice(0, 5).join(' · ') }) : null,
        r.files?.length ? el('div', { class: 'move-list' }, r.files.map((f) => el('div', { class: 'mv' }, el('span', { class: 'to', text: f })))) : null);
      app.onResampled(r);
    };
    tick();
  };

  for (const x of [mode, value, value2, filter, enlarge, format, where, folder, suffix, keepTags]) x.addEventListener('input', invalidate);
  where.addEventListener('change', () => {   // the folder field holds a subfolder name or a path: remember both
    folder.value = where.value === 'folder' ? (pp.get('resample', {}).folder || app.query.folder || '') : (pp.get('resample', {}).subfolder || 'resampled');
    invalidate();
  });
  const presets = el('div', { class: 'chip-list' }, PRESETS.map(([label, p]) => el('button', { class: 'chip', type: 'button', text: label, onclick: () => {
    mode.value = p.mode; value.value = p.value;
    filter.value = p.filter || (filter.value === 'nearest' ? 'lanczos' : filter.value);
    enlarge.checked = !!p.enlarge;
    invalidate();
  } })));
  const d = openDialog({
    title: `Resample ${fmtNum(ids.length)} picture${ids.length > 1 ? 's' : ''} (as new copies)`,
    width: 700,
    body: el('div', { class: 'form' },
      presets,
      el('div', { class: 'row bottom' }, el('label', {}, 'Size', mode), value, by, value2, unit),
      el('label', { class: 'chk' }, enlarge, 'Also enlarge smaller pictures (otherwise they are left out)'),
      el('div', { class: 'row' }, el('label', {}, 'Filter', filter), el('label', {}, 'Format', format)),
      qualityBox,
      el('div', { class: 'row bottom' }, el('label', {}, 'Save', where), folderBox),
      el('div', { class: 'row' }, el('label', {}, 'Name ending', suffix), el('small', { class: 'muted', text: '{w} {h} = new size, {pct} = percent · e.g. hero.png → hero_512x256.png' })),
      el('label', { class: 'chk' }, keepDate, 'Keep the original\'s date (EXIF and file time), so the copy sorts next to it'),
      el('label', { class: 'chk' }, keepTags, 'Copies in the library get the original\'s tags, source, author and licence'),
      el('div', { class: 'p-hint small', text: 'Colour profiles are kept; animated GIF / WebP / PNG keep their frames. Layered files (PSD, Krita…), SVG and HEIC are saved as PNG unless you choose a format.' }),
      out),
    buttons: [
      { label: 'Cancel', onClick: (close) => { if (running) api.resampleCancel(); else close(); } },
      { label: 'Preview', onClick: preview },
      { label: 'Resample', id: 'go', primary: true, disabled: true, onClick: run },
    ],
  });
  invalidate();
}
