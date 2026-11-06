// Training dataset export: pictures + caption .txt files (kohya-ss, OneTrainer, ai-toolkit, diffusers),
// deduplicated, optionally resized, split into train / val. Copies only; the library is not changed.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtNum, prefs, setKids } from './util.js';

export async function openDatasetDialog(app) {
  const nSel = app.selected.size;
  const ids = nSel ? app.items.ids.filter((id) => app.selected.has(id)) : [...app.items.ids];
  if (!ids.length) { app.toast('Nothing to export - select pictures or filter the view'); return; }
  let opts;
  try { opts = await api.datasetOptions(); } catch (e) { app.toast(e.message); return; }
  const P = prefs.get('dataset', {});
  const sel = (obj, v) => { const s = el('select', {}, Object.entries(obj).map(([k, t]) => el('option', { value: k, text: t }))); s.value = v; return s; };
  const chk = (v) => { const c = el('input', { type: 'checkbox' }); c.checked = !!v; return c; };
  const dest = el('input', { type: 'text', value: P.dest || '', spellcheck: 'false', placeholder: 'e.g. D:\\Training' });
  const folder = el('input', { type: 'text', value: P.folder || 'dataset', spellcheck: 'false', style: { width: '180px' } });
  const captions = sel(opts.captions, P.captions || 'both');
  const facets = el('span', { class: 'ds-facets' }, app.tags.facets.map((f) => {
    const c = chk((P.facets || ['subject']).includes(f.key));
    c.dataset.k = f.key;
    return el('label', { class: 'chk' }, c, f.label);
  }));
  const trigger = el('input', { type: 'text', value: P.trigger || '', spellcheck: 'false', placeholder: 'optional, e.g. mystyle', style: { width: '160px' } });
  const near = chk(P.near_dupes !== false);
  const maxSide = sel({ 0: 'Keep the size', 512: '512 px', 768: '768 px', 1024: '1024 px', 1536: '1536 px', 2048: '2048 px' }, String(P.max_side ?? 0));
  const format = sel(opts.formats, P.format || 'keep');
  const flatten = chk(P.flatten);
  const val = el('input', { type: 'number', min: '0', max: '50', step: '5', value: String(P.val ?? 0), style: { width: '64px' } });
  const seed = el('input', { type: 'number', min: '0', step: '1', value: String(P.seed ?? 1), style: { width: '70px' } });
  const names = sel({ numbers: 'Numbers (00001.png ...)', original: 'Original file names' }, P.names || 'numbers');
  const out = el('div', { class: 'move-preview' });

  const body = () => ({
    ids, dest: dest.value.trim(), folder: folder.value.trim(), captions: captions.value, trigger: trigger.value.trim(),
    facets: [...facets.querySelectorAll('input:checked')].map((c) => c.dataset.k), near_dupes: near.checked,
    max_side: +maxSide.value, format: format.value, flatten: flatten.checked, val: (+val.value || 0) / 100,
    seed: +seed.value || 0, names: names.value,
  });
  const remember = () => { const { ids: _ids, ...b } = body(); prefs.set('dataset', { ...b, val: +val.value || 0 }); };
  const inline = (label, input, after) => el('label', { class: 'chk' }, label, input, after || null);
  const plural = (n, one, many) => `${fmtNum(n)} ${n === 1 ? one : many}`;

  let planned = null;
  const preview = async () => {
    remember();
    setKids(out, el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { planned = await api.datasetPlan(body()); } catch (e) { planned = null; setKids(out, el('div', { class: 'err', text: e.message })); d.buttons.Export.disabled = true; return; }
    const sk = planned.skipped;
    const skips = [[sk.identical, 'identical'], [sk.near, 'near-duplicates'], [sk.not_picture, 'not pictures'], [sk.missing, 'missing on disk']]
      .filter(([n]) => n).map(([n, t]) => `${fmtNum(n)} ${t}`);
    setKids(out,
      el('div', { class: 'move-sum' },
        el('b', { text: plural(planned.count, 'picture', 'pictures') + (planned.val ? ` · ${fmtNum(planned.train)} train, ${fmtNum(planned.val)} validation` : '') }),
        el('div', { class: 'muted small', text: [skips.length ? `left out: ${skips.join(', ')}` : null,
          planned.convert ? `${fmtNum(planned.convert)} converted or resized` : null].filter(Boolean).join(' · ') }),
        sk.no_caption ? el('div', { class: 'warn small', text: `${plural(sk.no_caption, 'has', 'have')} no caption from this source - make captions with a vision-model tag pool (AI → tag pool → also write a caption), or use tags` }) : null,
        planned.no_licence ? el('div', { class: 'muted small', text: `${fmtNum(planned.no_licence)} without a licence recorded (panel → Source & licence; search licence:none). Sources and licences go into kurator_dataset.json.` }) : null,
        planned.folder_not_empty ? el('div', { class: 'warn small', text: 'The folder already has files - files with the same names are replaced.' }) : null,
        el('div', { class: 'muted small', text: `into ${planned.root}` })),
      el('div', { class: 'mv-list' }, planned.examples.map((e) => el('div', { class: 'mv' },
        el('span', { class: 'to', text: e.file }), el('span', { class: 'muted small', text: `  ${e.text || '(no caption)'}` })))));
    d.buttons.Export.disabled = !planned.count;
  };
  const stale = () => { d.buttons.Export.disabled = true; setKids(out, el('div', { class: 'p-hint small', text: 'Press Preview to see the dataset.' })); };

  const d = openDialog({
    title: `Training dataset from ${fmtNum(ids.length)} ${nSel ? 'selected' : 'shown'} items`, width: 760,
    body: el('div', { class: 'projdlg dsdlg' },
      el('div', { class: 'p-hint small', text: 'Pictures with a caption .txt next to each, plus metadata.jsonl - the layout kohya-ss, OneTrainer, ai-toolkit and diffusers read. Identical files go in once; near-duplicates too (the biggest is kept). Your library is not changed.' }),
      el('label', {}, 'Save into', dest),
      el('div', { class: 'row' }, inline('Dataset folder', folder), inline('Trigger word first', trigger)),
      el('div', { class: 'row' }, inline('Captions', captions), el('span', { class: 'small muted', text: 'tags from:' }), facets),
      el('div', { class: 'row' }, inline('Longest side', maxSide), inline('Format', format), inline('Names', names)),
      el('div', { class: 'row' }, inline('Validation', val, '% (0: no split)'), inline('Shuffle seed', seed, 'same seed, same split')),
      el('label', { class: 'chk' }, near, 'Leave out near-duplicates (same picture resized or re-saved)'),
      el('label', { class: 'chk' }, flatten, 'Flatten transparency onto white (JPEG always is)'),
      out),
    buttons: [{ label: 'Close' }, { label: 'Preview', onClick: preview },
      { label: 'Export', primary: true, disabled: true, onClick: async () => {
        remember();
        try { await api.datasetRun(body()); } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); return; }
        d.buttons.Export.disabled = true;
        const bar = el('div', { class: 'pbar' }, el('span'));
        const line = el('div', { class: 'small' });
        setKids(out, bar, line, el('div', { class: 'dlg-inline-buttons' }, el('button', { class: 'btn small', text: 'Stop', onclick: () => api.datasetCancel() })));
        const tick = async () => {
          let st;
          try { st = await api.datasetJob(); } catch { return; }
          bar.firstChild.style.width = `${st.total ? Math.round((100 * st.done) / st.total) : 100}%`;
          line.textContent = `${fmtNum(st.done)} / ${fmtNum(st.total)} pictures`;
          if (st.running) { setTimeout(tick, 400); return; }
          const r = st.result || {};
          setKids(out, el('div', { class: r.failed ? 'err' : 'ok', text: `${r.cancelled ? 'Stopped' : 'Done'}: ${plural(r.written, 'picture', 'pictures')} with captions${r.failed ? `, ${r.failed} failed` : ''}` }),
            el('div', { class: 'small' }, 'Folder: ', el('code', { text: r.folder || '' }), ' ',
              el('button', { class: 'btn small', text: 'Copy path', onclick: () => navigator.clipboard?.writeText(r.folder || '') })),
            st.errors?.length ? el('div', { class: 'err small', text: st.errors.slice(0, 5).join(' · ') }) : null);
        };
        tick();
      } }],
  });
  for (const x of [dest, folder, captions, trigger, near, maxSide, format, flatten, val, seed, names, ...facets.querySelectorAll('input')]) x.addEventListener('change', stale);
  if (dest.value) preview();
  return d;
}
