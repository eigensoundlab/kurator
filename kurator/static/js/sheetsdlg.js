// Contact sheets and joined images from the selection (or the view). Live preview; saves new files.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { debounce, el, fmtNum, prefs, setKids } from './util.js';

export function openSheetsDialog(app, tab = prefs.get('sheets.tab', 'join')) {
  if (!['join', 'contact', 'atlas'].includes(tab)) tab = 'join';
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
  // sprite sheet / texture atlas
  const aMode = sel([['atlas', 'Texture atlas (packed tightly)'], ['frames', 'Sprite sheet (equal cells, animation frames)']], P.amode || 'atlas');
  const aCols = num(P.acolumns, 0, 200, 'auto');
  aCols.style.width = '60px';
  const aCW = num(P.cell_w, 0, 8192, 'auto'); const aCH = num(P.cell_h, 0, 8192, 'auto');
  const aPad = num(P.padding ?? 2, 0, 64);
  const aExt = num(P.extrude ?? 0, 0, 8);
  const aTrim = chk(P.trim !== false);
  const aPot = chk(P.pot);
  const aMax = sel([['1024', '1024'], ['2048', '2048'], ['4096', '4096'], ['8192', '8192'], ['16384', '16384']], String(P.max_side || 4096));
  const aScale = sel([['1', '100 %'], ['0.5', '50 %'], ['0.25', '25 %'], ['2', '200 %']], String(P.scale || 1));
  const aExp = chk(P.expand !== false);
  const aGodot = chk(P.godot);
  const aFps = num(P.fps ?? 12, 1, 120);
  aFps.style.width = '60px';
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

  const atlasBox = el('div', {}, field('Kind', aMode),
    el('div', { class: 'row frames-only' }, field('Columns', aCols), field('Cell', aCW), field('×', aCH)),
    el('div', { class: 'row' }, field('Padding (px)', aPad), field('Extrude', aExt)),
    el('div', { class: 'p-hint small sheet-hint', text: 'Padding: empty pixels between sprites. Extrude: repeats the edge pixels around each sprite so filtering never bleeds in its neighbours.' }),
    el('label', { class: 'chk atlas-only' }, aTrim, 'Trim transparent borders (offsets kept in the JSON)'),
    el('div', { class: 'row' }, field('Max size', aMax), field('Scale', aScale)),
    el('label', { class: 'chk' }, aPot, 'Power-of-two size (older engines, mipmaps)'),
    el('label', { class: 'chk' }, aExp, 'Split animated GIF / WebP / APNG into frames'),
    el('div', { class: 'row' }, el('label', { class: 'chk', title: 'SpriteFrames .tres with the animations (AnimatedSprite2D); for atlases also one AtlasTexture .tres per sprite. Inside a Godot project the paths are res:// paths.' }, aGodot, 'Godot 4 resources (.tres)'),
      field('Frames per second', aFps)),
    el('div', { class: 'p-hint small', text: 'Saves a PNG and a JSON file (TexturePacker JSON Hash) next to each other - Phaser, PixiJS, Godot and Unity importers read it. Frames of one animation are listed under "animations".' }));
  const kind = () => tab;
  const opts = () => (tab === 'atlas'
    ? { mode: aMode.value, columns: +aCols.value || 0, cell_w: +aCW.value || null, cell_h: +aCH.value || null, padding: +aPad.value || 0,
      extrude: +aExt.value || 0, trim: aTrim.checked, pot: aPot.checked, max_side: +aMax.value, scale: +aScale.value, expand: aExp.checked,
      godot: aGodot.checked, fps: +aFps.value || 12 }
    : tab === 'join'
    ? { layout: jLayout.value, columns: +jCols.value, size: jSize.value, gap: +jGap.value || 0, target_width: +jTW.value || null,
      target_height: +jTH.value || null, background: jBg.value, transparent: jTr.checked, format: jFmt.value }
    : { page: cPage.value, landscape: cLand.checked, dpi: +cDpi.value, width: +cW.value, height: +cH.value, columns: +cCols.value,
      caption: cCap.value, title: cTitle.value, background: cBg.value, page_numbers: cNum.checked, format: cFmt.value });
  const remember = () => {
    const o = opts();
    const keep = tab === 'atlas'
      ? { amode: o.mode, acolumns: o.columns || '', cell_w: o.cell_w, cell_h: o.cell_h, padding: o.padding, extrude: o.extrude, trim: o.trim, pot: o.pot, max_side: o.max_side, scale: o.scale, expand: o.expand, godot: o.godot, fps: o.fps }
      : tab === 'join'
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
      const sprites = r.headers.get('X-Sprites'), fill = r.headers.get('X-Fill');
      info.textContent = sprites
        ? `${fmtNum(+sprites)} sprites · ${size} px${fill ? ` · ${Math.round(fill * 100)} % filled` : ''}`
        : `${fmtNum(ids.length)} images · ${size} px${pages ? ` · ${pages} page${pages === '1' ? '' : 's'} (preview: page 1)` : ''}`;
      setKids(msg);
    } catch (e) { if (e.name !== 'AbortError') { info.textContent = ''; setKids(msg, el('div', { class: 'err', text: e.message })); } }
  }, 350);

  const render = () => {
    setKids(tabs, [['join', 'Join images'], ['contact', 'Contact sheet'], ['atlas', 'Sprites / atlas']].map(([k, t]) =>
      el('button', { class: 'seg' + (tab === k ? ' on' : ''), type: 'button', text: t, onclick: () => { tab = k; prefs.set('sheets.tab', k); render(); preview(); } })));
    setKids(body, tab === 'join' ? joinBox : tab === 'atlas' ? atlasBox : sheetBox);
    prevImg.classList.toggle('checker', tab === 'atlas' || (tab === 'join' && jTr.checked));
    for (const x of atlasBox.querySelectorAll('.frames-only')) x.style.display = aMode.value === 'frames' ? '' : 'none';
    for (const x of atlasBox.querySelectorAll('.atlas-only')) x.style.display = aMode.value === 'atlas' ? '' : 'none';
    const custom = cPage.value === 'custom';
    cW.parentElement.parentElement.style.display = custom ? '' : 'none';
    cLand.parentElement.style.display = custom ? 'none' : '';
    cDpi.parentElement.style.display = custom ? 'none' : '';
  };
  for (const x of [jLayout, jCols, jSize, jGap, jTW, jTH, jBg, jTr, cPage, cLand, cDpi, cW, cH, cCols, cCap, cTitle, cBg, cNum,
    aMode, aCols, aCW, aCH, aPad, aExt, aTrim, aPot, aMax, aScale, aExp, aGodot]) {
    x.addEventListener(x.tagName === 'INPUT' && x.type !== 'checkbox' ? 'input' : 'change', () => { render(); preview(); });
  }

  const d = openDialog({
    title: `Join, contact sheet, sprites - ${fmtNum(ids.length)} ${app.selected.size ? 'selected' : 'images in view'}`, width: 980,
    body: el('div', { class: 'sheetdlg' },
      el('div', { class: 'sheetcol' }, tabs, body, field('Save into', dest), field('Name', name), msg),
      el('div', { class: 'sheetcol prevcol' }, prevImg, info)),
    buttons: [{ label: 'Close' }, { label: 'Save', primary: true, onClick: async () => {
      remember();
      try {
        const r = await api.sheet(kind(), { ids, ...opts(), dest: dest.value.trim(), name: name.value.trim() });
        const folder = r.files[0].replace(/[\\/][^\\/]*$/, '');
        setKids(msg, el('div', { class: 'ok', text: `Saved ${r.files.length <= 2 ? r.files.map((f) => f.split(/[\\/]/).pop()).join(' + ') : `${r.files.length} files`}` }),
          el('div', { class: 'row' }, el('button', { class: 'btn small', text: 'Show the folder', onclick: () => { d.close(); app.openPath(folder); } }),
            el('button', { class: 'btn small', text: 'Copy path', onclick: () => navigator.clipboard?.writeText(r.files[0]) })));
      } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
    } }],
    onClose: () => { ctrl?.abort(); if (prevImg.src) URL.revokeObjectURL(prevImg.src); },
  });
  render();
  preview();
}
