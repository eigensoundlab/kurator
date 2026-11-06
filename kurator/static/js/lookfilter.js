// "Look" filter: colours, shape, transparency and size of pictures; tempo, key and length of sounds;
// with Playhead analysis switched on, the character of sounds (hit or swell, bursty, metal-like, harsh…).

import { api } from './api.js';
import { phEnabled, phOnBatch, phOnSetting } from './playhead.js';
import { openPhLibraryDialog } from './phlibdlg.js';
import { el, fmtNum, prefs, setKids } from './util.js';

const HUES = [['red', '#e5484d'], ['orange', '#f76b15'], ['yellow', '#ffd60a'], ['lime', '#a3d936'], ['green', '#30a46c'],
  ['teal', '#12a594'], ['cyan', '#00c2d7'], ['azure', '#0090ff'], ['blue', '#3e63dd'], ['violet', '#8e4ec6'],
  ['magenta', '#d6409f'], ['pink', '#ff8fb0'], ['black', '#111'], ['gray', '#888'], ['white', '#f4f4f4']];
const SHAPES = [['', 'any shape'], ['landscape', 'landscape'], ['portrait', 'portrait'], ['square', 'square'], ['panorama', 'panorama (2:1+)'], ['tall', 'tall (1:2+)']];
const SIDES = [['0', 'any size'], ['512', '512 px +'], ['1024', '1024 px +'], ['2048', '2048 px +'], ['4096', '4096 px +']];
const KEYS = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'].flatMap((n) => [`${n} major`, `${n} minor`]);
const KEYS_LIST = ['hues', 'shape', 'alpha', 'minside', 'bpm_min', 'bpm_max', 'key', 'len_min', 'len_max', 'tex', 'ph'];
// Playhead's sound character, row by row (labels and explanations come from the server)
const PH_ROWS = [['hit', 'swell', 'bursty', 'regular'], ['metal', 'glass', 'damped', 'ring', 'dry'],
  ['tonal', 'noisy', 'harsh', 'impulsive'], ['beat', 'sections', 'analysed', 'unanalysed']];

export class LookFilter {
  constructor(app) {
    this.app = app;
    const saved = prefs.get('look', {});
    for (const k of KEYS_LIST) app.query[k] = saved[k] ?? (k === 'hues' || k === 'minside' ? 0 : k === 'alpha' ? false : '');
    this.button = el('button', { class: 'btn', title: 'Colours, shape, transparency, size · tempo, key and length of sounds',
      onclick: (e) => { e.stopPropagation(); this.panel.classList.toggle('open'); if (this.panel.classList.contains('open')) this.render(); } });
    this.panel = el('div', { class: 'typepanel lookpanel' });
    this.wrap = el('div', { class: 'typefilter' }, this.button, this.panel);
    document.addEventListener('pointerdown', (e) => { if (!this.wrap.contains(e.target)) this.panel.classList.remove('open'); }, true);
    this.phFilters = null;    // token -> { group, label, title } (from /api/playhead/status)
    const refresh = () => { if (phEnabled()) this.loadPh(); this.paint(); app.renderPathBar?.(); if (this.panel.classList.contains('open')) this.render(); };
    phOnSetting(refresh);
    phOnBatch((e) => { if (!e.detail.running) refresh(); });
    if (phEnabled()) this.loadPh();
    this.paint();
  }

  loadPh() {
    return api.phStatus().then((st) => { this.phFilters = st.filters; this.phStatus = st; return st; }).catch(() => null);
  }

  get active() {
    const q = this.app.query;
    return KEYS_LIST.filter((k) => q[k] && q[k] !== '0' && (k !== 'ph' || phEnabled()));
  }

  paint() {
    const n = this.active.length;
    this.button.textContent = n ? `Look (${n}) ▾` : 'Look ▾';
    this.button.classList.toggle('on', n > 0);
  }

  set(k, v) {
    this.app.query[k] = v;
    const saved = {};
    for (const key of KEYS_LIST) saved[key] = this.app.query[key];
    prefs.set('look', saved);
    this.paint();
    this.app.renderPathBar();
    this.app.reload({ keepScroll: false });
  }

  reset() {
    for (const k of KEYS_LIST) this.app.query[k] = k === 'hues' || k === 'minside' ? 0 : k === 'alpha' ? false : '';
    prefs.set('look', {});
    this.paint();
    if (this.panel.classList.contains('open')) this.render();
  }

  summary() {
    const q = this.app.query, out = [];
    if (q.hues) out.push(HUES.filter((_, b) => (q.hues >> b) & 1).map(([n]) => n).join('/'));
    if (q.shape) out.push(q.shape);
    if (q.alpha) out.push('transparent');
    if (+q.minside) out.push(`≥${q.minside}px`);
    if (q.bpm_min !== '' || q.bpm_max !== '') out.push(`${q.bpm_min || 0}–${q.bpm_max || '∞'} BPM`);
    if (q.key) out.push(q.key);
    if (q.len_min !== '' || q.len_max !== '') out.push(`${q.len_min || 0}–${q.len_max || '∞'} s`);
    if (q.tex) out.push({ yes: 'textured 3D', no: 'untextured 3D', missing: 'missing textures' }[q.tex]);
    if (q.ph && phEnabled()) out.push(q.ph.split(',').map((t) => this.phFilters?.[t]?.label || t).join(' + '));
    return out.join(', ');
  }

  clear() {
    this.reset();
    this.app.renderPathBar();
    this.app.reload({ keepScroll: false });
  }

  render() {
    const q = this.app.query;
    const swatches = HUES.map(([name, css], bit) => el('button', {
      class: 'swatch' + ((q.hues >> bit) & 1 ? ' on' : ''), type: 'button', title: name, style: { background: css },
      onclick: () => { this.set('hues', q.hues ^ (1 << bit)); this.render(); } }));
    const sel = (opts, v, onch) => { const s = el('select', {}, opts.map(([k, t]) => el('option', { value: k, text: t }))); s.value = String(v ?? ''); s.onchange = () => onch(s.value); return s; };
    const num = (k, ph) => {
      const i = el('input', { type: 'number', min: '0', placeholder: ph, value: q[k] ?? '', style: { width: '70px' } });
      i.onchange = () => this.set(k, i.value === '' ? '' : +i.value);
      return i;
    };
    const alpha = el('input', { type: 'checkbox' });
    alpha.checked = !!q.alpha;
    alpha.onchange = () => this.set('alpha', alpha.checked);
    setKids(this.panel,
      el('div', { class: 'tp-head' }, 'Colours ', el('span', { class: 'muted small', text: '(any of them, covering a good part of the picture)' })),
      el('div', { class: 'swatches' }, swatches),
      el('div', { class: 'row' }, sel(SHAPES, q.shape, (v) => this.set('shape', v)), sel(SIDES, q.minside, (v) => this.set('minside', +v)),
        el('label', { class: 'chk' }, alpha, 'has transparency')),
      el('div', { class: 'tp-head' }, '3D models'),
      el('div', { class: 'row' }, sel([['', 'any'], ['yes', 'with textures'], ['no', 'without textures'], ['missing', 'textures missing on disk']], q.tex, (v) => this.set('tex', v))),
      el('div', { class: 'tp-head' }, 'Sounds'),
      el('div', { class: 'row' }, el('span', { class: 'small muted', text: 'BPM' }), num('bpm_min', 'from'), num('bpm_max', 'to'),
        sel([['', 'any key'], ...KEYS.map((k) => [k, k])], q.key, (v) => this.set('key', v))),
      el('div', { class: 'row' }, el('span', { class: 'small muted', text: 'Length (s)' }), num('len_min', 'from'), num('len_max', 'to')),
      phEnabled() ? this.phSection() : null,
      el('div', { class: 'row' }, el('button', { class: 'btn small', text: 'Clear', onclick: () => this.clear() }),
        el('span', { class: 'p-hint small', text: 'Tempo and key are estimated for sounds of 8 s and longer.' })));
  }

  /** Sound character from Playhead analysis: toggles (alternatives within a group, groups combined). */
  phSection() {
    const box = el('div', { class: 'ph-look-box' }, el('span', { class: 'small muted', text: '…' }));
    const paint = (st) => {
      const F = this.phFilters;
      if (!F) { setKids(box, el('span', { class: 'small muted', text: 'Playhead analysis isn\'t available.' })); return; }
      const on = new Set((this.app.query.ph || '').split(',').filter(Boolean));
      const toggle = (t) => {
        if (on.has(t)) on.delete(t); else on.add(t);
        this.set('ph', [...on].join(','));
        this.render();
      };
      const job = st?.job?.running ? ` · analysing ${fmtNum(st.job.done)} / ${fmtNum(st.job.total)}` : '';
      setKids(box,
        ...PH_ROWS.map((row) => el('div', { class: 'row ph-chips' }, row.filter((t) => F[t]).map((t) => el('button', {
          class: 'btn small' + (on.has(t) ? ' on' : ''), type: 'button', text: F[t].label, title: F[t].title, onclick: () => toggle(t) })))),
        el('div', { class: 'row' },
          el('span', { class: 'small muted', text: st ? `${fmtNum(st.analysed)} of ${fmtNum(st.sounds)} sounds analysed${job}` : '' }),
          el('button', { class: 'btn small', type: 'button', text: 'Analyse sounds…', title: 'Analyse many sounds with Playhead',
            onclick: () => { this.panel.classList.remove('open'); openPhLibraryDialog(this.app); } })));
    };
    this.loadPh().then(paint);
    return el('div', {}, el('div', { class: 'tp-head' }, 'Sound character ', el('span', { class: 'muted small', text: '(Playhead analysis)' })), box);
  }
}
