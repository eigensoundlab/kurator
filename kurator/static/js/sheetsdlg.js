// Contact sheets and joined images from the selection (or the view). Live preview; saves new files.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { debounce, el, fmtNum, prefs, setKids } from './util.js';

export function openSheetsDialog(app, tab = prefs.get('sheets.tab', 'join')) {
  const ids = app.selected.size ? app.items.ids.filter((id) => app.selected.has(id)) : [...app.items.ids];   // in grid order
  if (!ids.length) { app.toast('Select some images first'); return; }
  const firstDir = app.panel.detail?.dir || app.query.folder || '';
  const P = prefs.get('sheets', {});
  const field = (label, input, hint) => el('label', { class: 'sf' }, el('span', { text: label }), input, hint ? el('small', { class: 'muted', text: hint }) : null);
  const sel = (opts, v) => { const s = el('select', {}, opts.map(([k, t]) => el('option', { value: k, text: t }))); s.value = v; return s; };
  const num = (v, min = 0, max = 100000, ph = '') => el('input', { type: 'number', min: String(min), max: String(max), value: v ?? '', placeholder: ph, style: { width: '90px' } });
  const chk = (v) => { const c = el('input', { type: 'checkbox' }); c.checked = !!v; return c; };

  // join
  const jLayout = sel([['horizontal', 'Side by side (horizontal)'], ['vertical', 'Stacked (vertical)'], ['grid', 'Grid']], P.layout || 'horizontal');
  const jCols = num(P.columns ?? 3, 1, 50);
  const jSize = sel([['smallest', 'to the smallest'], ['largest', 'to the largest']], P.size || 'smallest');
  const jGap = num(P.gap ?? 0, 0, 2000);
  const jTW = num(P.target_width, 0, 30000, 'any');
  const jTH = num(P.target_height, 0, 30000, 'any');
  const jBg = sel([['white', 'White'], ['light', 'Light gray'], ['gray', 'Gray'], ['dark', 'Dark'], ['black', 'Black']], P.background || 'white');
  const jTr = chk(P.transparent);
  const jFmt = sel([['png', 'PNG'], ['jpg', 'JPEG'], ['webp', 'WebP']], P.jformat || 'png');
  // contact sheet
  const cPage = sel([['A4', 'A4'], ['A3', 'A3'], ['A5', 'A5'], ['Letter', 'Letter'], ['Tabloid', 'Tabloid'], ['custom', 'Custom size (pixels)']], P.page || 'A4');
  const cLand = chk(P.landscape);
  const cDpi = sel([['100', '100 dpi (screen)'], ['150', '150 dpi'], ['300', '300 dpi (print)']], String(P.dpi || 150));
  const cW = num(P.width || 1920, 100, 12000); const cH = num(P.height || 1080, 100, 12000);
  const cCols = num(P.ccolumns ?? 5, 1, 20);
  const cCap = sel([['none', 'No captions'], ['name', 'File name'], ['name_date', 'Name and date'], ['name_tags', 'Name and tags']], P.caption || 'name');
  const cTitle = el('input', { type: 'text', value: P.title || '', placeholder: 'optional, e.g. Creatures – round 2' });
  const cBg = sel([['white', 'White'], ['light', 'Light gray'], ['dark', 'Dark'], ['black', 'Black']], P.cbackground || 'white');
  const cNum = chk(P.page_numbers !== false);
  const cFmt = sel([['pdf', 'PDF (all pages in one file)'], ['png', 'PNG (one file per page)'], ['jpg', 'JPEG (one file per page)']], P.cformat || 'pdf');
  // shared
  const dest = el('input', { type: 'text', value: P.dest || firstDir, spellcheck: 'false', placeholder: 'Folder to save into' });
  const name = el('input', { type: 'text', value: '', spellcheck: 'false', placeholder: 'file name (optional)' });
  const prevImg = el('img', { class: 'sheet-prev', alt: '' });
  const info = el('div', { class: 'muted small' });
  const msg = el('div');
  const tabs = el('div', { class: 'segs' });
  const body = el('div', { class: 'sheetopts' });

  const joinBox = el('div', {}, field('Layout', jLayout), field('Columns (grid)', jCols),
    field('Scale every image', jSize, 'to one height (side by side) or width (stacked, grid)'), field('Gap (px)', jGap),
    el('div', { class: 'row' }, field('Result width', jTW, 'px, fits inside'), field('Result height', jTH, 'px')),
    field('Background', jBg), el('label', { class: 'chk' }, jTr, 'Transparent background (PNG/WebP)'), field('Format', jFmt));
  const sheetBox = el('div', {}, el('div', { class: 'row' }, field('Page', cPage), el('label', { class: 'chk' }, cLand, 'Landscape')),
    el('div', { class: 'row' }, field('Width', cW), field('Height', cH)), field('Resolution', cDpi), field('Columns', cCols),
    field('Captions', cCap), field('Title', cTitle), field('Background', cBg), el('label', { class: 'chk' }, cNum, 'Page numbers'), field('Format', cFmt));

  const kind = () => (tab === 'join' ? 'join' : 'contact');
  const opts = () => (tab === 'join'
    ? { layout: jLayout.value, columns: +jCols.value, size: jSize.value, gap: +jGap.value || 0, target_width: +jTW.value || null,
      target_height: +jTH.value || null, background: jBg.value, transparent: jTr.checked, format: jFmt.value }
    : { page: cPage.value, landscape: cLand.checked, dpi: +cDpi.value, width: +cW.value, height: +cH.value, columns: +cCols.value,
      caption: cCap.value, title: cTitle.value, background: cBg.value, page_numbers: cNum.checked, format: cFmt.value });
  const remember = () => {
    const o = opts();
    const keep = tab === 'join'
      ? { layout: o.layout, columns: o.columns, size: o.size, gap: o.gap, target_width: o.target_width, target_height: o.target_height, background: o.background, transparent: o.transparent, jformat: o.format }
      : { page: o.page, landscape: o.landscape, dpi: o.dpi, width: o.width, height: o.height, ccolumns: o.columns, caption: o.caption, title: o.title, cbackground: o.background, page_numbers: o.page_numbers, cformat: o.format };
    prefs.set('sheets', { ...prefs.get('sheets', {}), ...keep, dest: dest.value.trim() });
  };

  let ctrl = null;
  const preview = debounce(async () => {
    ctrl?.abort();
    ctrl = new AbortController();
    info.textContent = 'Making a preview…';
    try {
      const r = await api.sheet(kind(), { ids, ...opts(), preview: true }, ctrl.signal);
      if (prevImg.src) URL.revokeObjectURL(prevImg.src);
      prevImg.src = r.url;
      const size = (r.headers.get('X-Size') || '').replace('x', ' × ');
      const pages = r.headers.get('X-Pages');
      info.textContent = `${fmtNum(ids.length)} images · ${size} px${pages ? ` · ${pages} page${pages === '1' ? '' : 's'} (preview: page 1)` : ''}`;
      setKids(msg);
    } catch (e) { if (e.name !== 'AbortError') { info.textContent = ''; setKids(msg, el('div', { class: 'err', text: e.message })); } }
  }, 350);

  const render = () => {
    setKids(tabs, [['join', 'Join images'], ['contact', 'Contact sheet']].map(([k, t]) =>
      el('button', { class: 'seg' + (tab === k ? ' on' : ''), type: 'button', text: t, onclick: () => { tab = k; prefs.set('sheets.tab', k); render(); preview(); } })));
    setKids(body, tab === 'join' ? joinBox : sheetBox);
    const custom = cPage.value === 'custom';
    cW.parentElement.parentElement.style.display = custom ? '' : 'none';
    cLand.parentElement.style.display = custom ? 'none' : '';
    cDpi.parentElement.style.display = custom ? 'none' : '';
  };
  for (const x of [jLayout, jCols, jSize, jGap, jTW, jTH, jBg, jTr, cPage, cLand, cDpi, cW, cH, cCols, cCap, cTitle, cBg, cNum]) {
    x.addEventListener(x.tagName === 'INPUT' && x.type !== 'checkbox' ? 'input' : 'change', () => { render(); preview(); });
  }

  const d = openDialog({
    title: `Join or make a contact sheet - ${fmtNum(ids.length)} ${app.selected.size ? 'selected' : 'images in view'}`, width: 980,
    body: el('div', { class: 'sheetdlg' },
      el('div', { class: 'sheetcol' }, tabs, body, field('Save into', dest), field('Name', name), msg),
      el('div', { class: 'sheetcol prevcol' }, prevImg, info)),
    buttons: [{ label: 'Close' }, { label: 'Save', primary: true, onClick: async () => {
      remember();
      try {
        const r = await api.sheet(kind(), { ids, ...opts(), dest: dest.value.trim(), name: name.value.trim() });
        const folder = r.files[0].replace(/[\\/][^\\/]*$/, '');
        setKids(msg, el('div', { class: 'ok', text: `Saved ${r.files.length === 1 ? r.files[0].split(/[\\/]/).pop() : `${r.files.length} files`}` }),
          el('div', { class: 'row' }, el('button', { class: 'btn small', text: 'Show the folder', onclick: () => { d.close(); app.openPath(folder); } }),
            el('button', { class: 'btn small', text: 'Copy path', onclick: () => navigator.clipboard?.writeText(r.files[0]) })));
      } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
    } }],
    onClose: () => { ctrl?.abort(); if (prevImg.src) URL.revokeObjectURL(prevImg.src); },
  });
  render();
  preview();
}
