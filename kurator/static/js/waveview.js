// Waveform view for sounds: playhead, click / drag to seek, wheel to zoom (in the viewer), and
// several ways to look at a sound. Draws on a canvas from per-channel min / max / RMS data.
//
//   Peak + RMS   the classic editor look: peak outline, loudness body (default)
//   Min / max    the true, asymmetric signal range (DC offset, clipping, polarity show up)
//   Loudness dB  amplitude on a decibel scale: quiet tails and noise floors become visible
//   Channels     every channel in its own lane (stereo balance, phase problems, dead channels)
//   Spectrogram  frequencies over time (log scale, 30 Hz – Nyquist): hum, hiss, pitch, transients
//   Playhead analysis   (optional, ⚙ Settings) curves, events, sections drawn by Playhead itself
//                       (playhead.js); the Look menu chooses how. Not the playback cursor, which
//                       this file also calls the playhead (heads()).
//
// Rows: the Rows menu sets a row height, and a long sound wraps onto as many rows as fit, all on the same
// time scale (like lines of text; every row holds at least 5 seconds, so short sounds stay on one row).
// Shift+wheel or a sideways swipe moves along a zoomed sound. When rows or zoom need more detail than the
// whole-sound overview holds, the part on screen is read again, finer (waveform and spectrogram).

import { api } from './api.js';
import { PH_FOLDS_NOTE, phDoc, phEnabled, phFeedbackUrl, phFoldsTime, phLib, phLibNow, phLookPref, phOnSetting, phSetLookPref, phTheme } from './playhead.js';
import { el, fmtDuration, prefs } from './util.js';

export const WAVE_MODES = [['peaks', 'Peak + RMS'], ['minmax', 'Min / max'], ['db', 'Loudness (dB)'], ['stereo', 'Channels'], ['spectrogram', 'Spectrogram']];
const PH_MODE = ['playhead', 'Playhead analysis'];
/** The styles on offer: Playhead analysis only while it is switched on. */
export const waveModes = () => (phEnabled() ? [...WAVE_MODES, PH_MODE] : WAVE_MODES);
const ROWS = {   // row heights on offer (CSS px; 0: one row filling the view)
  big: [[0, 'One row'], [320, 'Rows of 320 px'], [240, 'Rows of 240 px'], [160, 'Rows of 160 px'], [120, 'Rows of 120 px'], [80, 'Rows of 80 px']],
  small: [[0, 'One row'], [140, 'Rows of 140 px'], [96, 'Rows of 96 px'], [64, 'Rows of 64 px']],
};
const ROW_SECONDS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 600, 900, 1200, 1800, 2700, 3600];
const MAX_ROWS = 24;
const fmtSpan = (s) => (s < 60 ? `${+s.toFixed(s < 10 ? 2 : 1)} s` : s % 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${s / 60} min`);
const LONG = 30;   // seconds: long sounds are orange, short ones blue (as in the thumbnails)
const PAL = {
  blue: { outline: '#4662a8', body: '#8cb0ff', line: '#7aa2ff' },
  orange: { outline: '#a85c24', body: '#ffac5c', line: '#fa9646' },
};

function fmtTime(t, fine) {
  if (!isFinite(t)) return '';
  if (fine) { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(2).padStart(5, '0')}`; }
  return fmtDuration(t);
}

export class WaveView {
  constructor(app, { ruler = false, zoom = false, bins = 1600 } = {}) {
    this.app = app;
    this.ruler = ruler;
    this.zoomable = zoom;
    this.bins = bins;
    this.canvas = el('canvas', { class: 'wavecanvas' });
    this.base = document.createElement('canvas');   // everything but the cursor, selection and hover
    this.baseKey = null;
    this.seq = 0;                                     // bumped whenever what the base shows changes
    this.mode = prefs.get('wave.mode', 'peaks');
    if (this.mode === 'playhead' && !phEnabled()) this.mode = 'peaks';
    this.modeSel = el('select', { class: 'wavemode', title: 'How to draw the sound' });
    this.fillModes();
    this.modeSel.onchange = () => this.setMode(this.modeSel.value);
    // rows: their height (the panel and the viewer remember their own)
    this.rowOpts = ROWS[ruler ? 'big' : 'small'];
    this.rowPref = ruler ? 'wave.rows.viewer' : 'wave.rows.panel';
    this.rowPx = prefs.get(this.rowPref, 0);
    if (!this.rowOpts.some(([v]) => v === this.rowPx)) this.rowPx = 0;
    this.rowTitle = 'Row height: smaller rows wrap a long sound onto more lines (at least 5 s each)';
    this.rowSel = el('select', { class: 'wave-rows', title: this.rowTitle }, ...this.rowOpts.map(([v, t]) => el('option', { value: String(v), text: t })));
    this.rowSel.value = String(this.rowPx);
    this.rowSel.onchange = () => this.setRows(+this.rowSel.value);
    // Playhead analysis: the Look menu, a field for a look code, the legend Playhead returns, feedback
    this.phLookSel = el('select', { class: 'ph-look', title: 'Look: how Playhead draws the analysis' });
    this.phLookSel.onchange = () => this.chooseLook(this.phLookSel.value);
    this.phCode = el('input', { class: 'ph-code', type: 'text', spellcheck: 'false', placeholder: 'look code, e.g. wave,lane:activity,ticks:events',
      title: 'A look code (Playhead docs: Looks). Enter to use it, Esc to go back.' });
    this.phCode.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') this.useCode(this.phCode.value);
      if (e.key === 'Escape') { this.phCode.classList.remove('bad'); this.paintLooks(); this.phCode.blur(); }
    });
    this.phFb = el('a', { class: 'ph-fb', target: '_blank', rel: 'noopener noreferrer', text: 'Tell us if this helps',
      title: 'Opens a short form in the Playhead repository with this look filled in (no data is sent from Kurator)' });
    this.phLegend = el('div', { class: 'ph-legend' });
    this.tools = el('div', { class: 'wave-tools' }, this.phLookSel, this.phCode, this.rowSel, this.modeSel);
    for (const x of [this.tools, this.phFb, this.phLegend]) {
      x.addEventListener('pointerdown', (e) => e.stopPropagation());
      x.addEventListener('dblclick', (e) => e.stopPropagation());
      x.addEventListener('wheel', (e) => e.stopPropagation());
      x.addEventListener('click', (e) => e.stopPropagation());
    }
    this.selBar = el('div', { class: 'wave-selbar' });
    this.root = el('div', { class: 'waveview' + (ruler ? ' big' : '') }, this.canvas, this.tools, el('div', { class: 'ph-info' }, this.phLegend, this.phFb), this.selBar);
    phOnSetting(() => {   // Playhead switched on or off in Settings
      this.fillModes();
      if (this.mode === 'playhead' && !phEnabled()) this.setMode('peaks');
    });
    this.sel = null;          // selected region {a, b} in seconds (big view: drag to select)
    this.loopSel = prefs.get('wave.loop', false);
    this.id = null; this.data = null; this.spec = null; this.t0 = 0; this.t1 = 1; this.hoverX = null; this.hoverY = null;
    this.det = null; this.specDet = null; this.detKey = null;
    this.phd = null; this.phP = null; this.phErr = null; this.phR = null;
    this.bind();
    this.root.classList.toggle('ph-mode', this.mode === 'playhead');
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  fillModes() {
    this.modeSel.replaceChildren(...waveModes().map(([k, t]) => el('option', { value: k, text: t })));
    this.modeSel.value = this.mode;
  }

  async load(id, duration) {
    this.id = id;
    this.duration = duration || 0;
    this.data = null; this.spec = null; this.specErr = null; this.error = null; this.t0 = 0; this.t1 = 1;
    this.det = null; this.specDet = null; this.detKey = null; clearTimeout(this.detT);
    this.phd = null; this.phP = null; this.phErr = null; this.phR = null;
    this.seq++;
    this.setSel(null);
    this.draw();
    if (this.mode === 'spectrogram') this.loadSpec();   // in parallel with the waveform data
    try {
      const d = await api.audioWave(id, this.bins);
      if (this.id !== id) return;
      this.data = d;
      this.duration = d.duration || this.duration;
    } catch (e) { if (this.id === id) this.error = e.message; }
    if (this.id !== id) return;
    this.seq++;
    if (this.mode === 'spectrogram') this.loadSpec();
    if (this.mode === 'playhead') this.loadPh();
    this.draw();
    this.loop();
  }

  setRows(px) {
    this.rowPx = px;
    prefs.set(this.rowPref, px);
    this.rowSel.value = String(px);
    this.draw();
  }

  /** The Playhead document of this sound (analysed in a worker when there is none yet). */
  loadPh() {
    const id = this.id;
    if (id == null || !phEnabled() || this.phd || this.phLoading === id) return;
    this.phLoading = id;
    this.phErr = null;
    this.phP = null;
    phLib().then(() => { this.paintLooks(); this.draw(); }).catch(() => {});
    phDoc(id, { owner: this, duration: this.duration, onProgress: (p) => { if (this.id === id) { this.phP = p; this.seq++; this.draw(); } } })
      .then((doc) => {
        if (this.id !== id) return;
        this.phd = doc;
        this.seq++;
        this.paintLooks();
        this.draw();
      })
      .catch((e) => { if (this.id === id && !e.replaced) { this.phErr = e.message; this.seq++; this.draw(); } })
      .finally(() => { if (this.phLoading === id) this.phLoading = null; });
  }

  /** The Look menu: Playhead's ready-made looks (those that don't fit this sound greyed out, and those that fold time
   *  into rows, which this view can't follow), and a custom code. */
  paintLooks() {
    const P = phLibNow();
    if (!P) return;
    const cur = phLookPref();
    const doc = this.phd;
    const opts = Object.entries(P.looks).map(([k, L]) => {
      if (phFoldsTime(P, k)) return el('option', { value: k, text: L.name + ' (grid thumbnails only)', title: PH_FOLDS_NOTE, disabled: k !== cur });
      const fits = !doc || k === 'auto' || P.lookFits(doc, k);
      return el('option', { value: k, text: L.name + (fits ? '' : ' (not for this sound)'), title: L.desc, disabled: !fits && k !== cur });
    });
    opts.push(el('option', { value: 'custom', text: 'Custom…', title: 'Type or paste a look code' }));
    this.phLookSel.replaceChildren(...opts);
    const custom = !P.looks[cur];
    this.phLookSel.value = custom ? 'custom' : cur;
    this.phCode.classList.toggle('show', custom);
    if (custom && document.activeElement !== this.phCode) this.phCode.value = cur;
  }

  chooseLook(v) {
    if (v === 'custom') {
      const P = phLibNow();
      const cur = phLookPref();
      this.phCode.classList.add('show');
      this.phCode.value = P?.looks[cur] ? (this.phR?.code || P.looks[cur].code || '') : cur;
      this.phCode.focus();
      this.phCode.select();
      return;
    }
    phSetLookPref(v);
    this.phCode.classList.remove('show');
    this.draw();
  }

  /** A pasted look code: used when Playhead can read it (Playhead.parseLook). */
  useCode(code) {
    const P = phLibNow();
    code = String(code || '').trim();
    const layers = P && code ? P.parseLook(code) : null;
    const ok = Array.isArray(layers) ? layers.length > 0 : !!(P && P.looks[code]);
    const folds = ok && phFoldsTime(P, code);
    this.phCode.classList.toggle('bad', !ok || folds);
    if (!ok) { this.phCode.title = 'Playhead can\'t read this look code'; return; }
    if (folds) { this.phCode.title = PH_FOLDS_NOTE; return; }
    this.phCode.title = '';
    phSetLookPref(code);
    this.paintLooks();
    this.draw();
  }

  /** A spectrogram picture of the sound, or of [start, end) seconds of it, and the part it really covers. */
  async fetchSpec(id, w, h, start = null, end = null, ref = null) {
    const q = new URLSearchParams({ w: String(w), h: String(h) });
    if (start != null) { q.set('start', start.toFixed(3)); q.set('end', end.toFixed(3)); }
    if (ref != null && isFinite(ref)) q.set('ref', ref.toFixed(3));
    const r = await fetch(`/api/audio/${id}/spectrogram?${q}`);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
      throw new Error(msg);
    }
    const img = await createImageBitmap(await r.blob());
    return { img, start: +r.headers.get('X-Start') || 0, end: +r.headers.get('X-End') || 0, ref: parseFloat(r.headers.get('X-Ref')), cols: img.width };
  }

  loadSpec() {
    const id = this.id;
    if (id == null || this.spec || this.specLoading === id) return;
    this.specLoading = id;
    this.fetchSpec(id, this.ruler ? 2400 : 1200, this.ruler ? 384 : 160)
      .then((s) => { if (this.id === id) { this.spec = s; this.seq++; this.draw(); } })
      .catch((e) => { if (this.id === id) { this.specErr = e.message; this.seq++; this.draw(); } })
      .finally(() => { if (this.specLoading === id) this.specLoading = null; });
  }

  setMode(m) {
    if (m === 'playhead' && !phEnabled()) m = 'peaks';
    this.mode = m;
    prefs.set('wave.mode', m);
    this.modeSel.value = m;
    this.root.classList.toggle('ph-mode', m === 'playhead');
    if (m === 'spectrogram' && this.id != null) this.loadSpec();
    if (m === 'playhead') this.loadPh();
    this.draw();
  }

  get playing() { return this.app.playingId === this.id && this.app.audio && !this.app.audio.paused; }

  /** Keep the playhead moving while this sound plays (and keep it inside a playing selection). */
  loop() {
    if (this._raf) return;
    const tick = () => {
      this._raf = 0;
      if (!this.root.isConnected) return;
      if (this.playSel && this.sel && this.playing && this.app.audio.currentTime >= this.sel.b) {
        if (this.loopSel) this.app.audio.currentTime = this.sel.a;
        else { this.app.audio.pause(); this.playSel = false; }
      }
      this.draw();
      if (this.playing) this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  }

  /** The rows on screen (device pixels): each a ruler band (big view) over a body; a, b are the row's
   *  start and end as fractions of the sound. Every row spans the same time; the last one may run past
   *  the end of the sound. */
  layout(W, H, dpr) {
    const rh = this.ruler ? Math.round(20 * dpr) : 0;
    const gap = Math.round((this.ruler ? 8 : 4) * dpr);
    const D = this.duration, span = (this.t1 - this.t0) * D;
    let n = 1, per = span;
    if (this.rowPx > 0 && D > 0) {
      const fit = Math.max(1, Math.min(MAX_ROWS, Math.floor((H + gap) / (this.rowPx * dpr + rh + gap))));
      if (fit > 1 && span > ROW_SECONDS[0] * 1.25) {
        per = ROW_SECONDS.find((s) => s >= span / fit) ?? Math.ceil(span / fit / 3600) * 3600;
        n = Math.max(1, Math.ceil(span / per - 1e-6));
        if (n === 1) per = span;
      }
    }
    const body = Math.max(1, (H - n * rh - (n - 1) * gap) / n);
    const rows = [];
    for (let k = 0; k < n; k++) {
      const ry = Math.round(k * (rh + body + gap));
      const a = D > 0 ? this.t0 + (k * per) / D : this.t0;
      rows.push({ k, ry, y: ry + rh, h: Math.max(1, Math.round(body)), gap, a, b: D > 0 ? a + per / D : this.t1, per });
    }
    return rows;
  }

  /** x (device pixels) of time t (seconds) on row R. */
  xOf(R, t, W) { return ((t / this.duration - R.a) / (R.b - R.a)) * W; }

  /** The row at y (device pixels from the top of the canvas): the nearest one. */
  rowAt(y) {
    const rows = this.rows || [];
    for (const R of rows) if (y < R.y + R.h + R.gap / 2) return R;
    return rows[rows.length - 1];
  }

  timeAt(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    const R = clientY == null || !this.rows ? null : this.rowAt((clientY - r.top) * (this.dpr || 1));
    const a = R ? R.a : this.t0, b = R ? R.b : this.t1;
    return Math.min(1, Math.max(0, a + f * (b - a))) * this.duration;
  }

  /** Play the selection from its start (or the sound from `t`). */
  playSelection() {
    if (!this.sel) return;
    this.freshSel = false;
    this.playSel = true;
    this.app.playAt(this.id, this.sel.a);
    this.loop();
  }

  setSel(sel) {
    this.sel = sel && sel.b - sel.a > 0.005 ? sel : null;
    if (!this.sel) this.playSel = false;
    this.paintSelBar();
    this.draw();
  }

  paintSelBar() {
    const s = this.sel;
    this.root.classList.toggle('has-sel', !!s);
    if (!s || !this.ruler) { this.selBar.replaceChildren(); return; }
    const fine = (s.b - s.a) < 60;
    const loop = el('button', { class: 'btn small' + (this.loopSel ? ' on' : ''), type: 'button', text: '⟳ Loop', title: 'Repeat the selection while playing',
      onclick: () => { this.loopSel = !this.loopSel; prefs.set('wave.loop', this.loopSel); this.paintSelBar(); } });
    this.selBar.replaceChildren(
      el('span', { class: 'sel-time', text: `${fmtTime(s.a, true)} – ${fmtTime(s.b, true)}` }),
      el('span', { class: 'muted', text: fine ? `${(s.b - s.a).toFixed(2)} s` : fmtDuration(s.b - s.a) }),
      el('button', { class: 'btn small', type: 'button', text: '▶ Play', title: 'Play the selection (Space)', onclick: () => this.playSelection() }),
      loop,
      el('button', { class: 'btn small', type: 'button', text: '⤢ Zoom to it', onclick: () => this.zoomTo(s.a, s.b) }),
      el('button', { class: 'btn small primary', type: 'button', text: 'Save as new file…', title: 'Cut this region out as a new sound file (E)', onclick: () => this.onExport?.(this) }),
      el('button', { class: 'btn small', type: 'button', text: '✕', title: 'Clear the selection', onclick: () => this.setSel(null) }));
    for (const b of this.selBar.querySelectorAll('button')) b.addEventListener('pointerdown', (e) => e.stopPropagation());
  }

  zoomTo(a, b) {
    const pad = (b - a) * 0.05;
    this.t0 = Math.max(0, (a - pad) / this.duration);
    this.t1 = Math.min(1, (b + pad) / this.duration);
    this.draw();
  }

  /** The shortest part zoom shows: a fraction of the sound (about 20 ms of a long one). */
  get minSpan() { return this.duration > 0 ? Math.min(0.002, 0.02 / this.duration) : 0.002; }

  bind() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !this.duration) return;
      e.stopPropagation();
      c.setPointerCapture(e.pointerId);
      const t = this.timeAt(e.clientX, e.clientY);
      if (!this.ruler) {   // small view: click and drag move the playhead
        drag = { scrub: true };
        this.app.playAt(this.id, t);
        this.loop();
        return;
      }
      // big view: click plays from there, drag selects a region, Shift+click extends the selection
      if (e.shiftKey && this.sel) {
        const anchor = Math.abs(t - this.sel.a) < Math.abs(t - this.sel.b) ? this.sel.b : this.sel.a;
        drag = { x: e.clientX, y: e.clientY, anchor, moved: true };
        this.setSel({ a: Math.min(anchor, t), b: Math.max(anchor, t) });
        return;
      }
      drag = { x: e.clientX, y: e.clientY, anchor: t, moved: false };
    });
    c.addEventListener('pointermove', (e) => {
      const r = c.getBoundingClientRect();
      this.hoverX = e.clientX - r.left;
      this.hoverY = e.clientY - r.top;
      if (drag?.scrub && this.app.playingId === this.id) this.app.audio.currentTime = this.timeAt(e.clientX, e.clientY);
      else if (drag && !drag.scrub && (drag.moved || Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 4)) {
        drag.moved = true;
        const t = this.timeAt(e.clientX, e.clientY);
        this.sel = { a: Math.min(drag.anchor, t), b: Math.max(drag.anchor, t) };
        this.playSel = false;
      }
      this.draw();
    });
    c.addEventListener('pointerup', (e) => {
      if (drag && !drag.scrub) {
        if (drag.moved) { this.setSel(this.sel); this.freshSel = !!this.sel; }   // Space now plays the new selection
        else {
          const t = this.timeAt(e.clientX, e.clientY);
          this.playSel = !!(this.sel && t >= this.sel.a && t < this.sel.b);   // inside the selection: play just that
          this.app.playAt(this.id, t);
          this.loop();
        }
      }
      drag = null;
    });
    c.addEventListener('pointerleave', () => { this.hoverX = null; this.hoverY = null; this.draw(); });
    c.addEventListener('click', (e) => e.stopPropagation());
    if (this.zoomable) {
      c.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.duration) return;
        const r = c.getBoundingClientRect();
        const span = this.t1 - this.t0;
        const pan = e.shiftKey ? (e.deltaX || e.deltaY) : (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : 0);
        if (pan) {   // Shift+wheel or a sideways swipe: move along the sound
          if (span >= 1) return;
          const rs = this.rows ? this.rows[0].b - this.rows[0].a : span;
          this.t0 = Math.max(0, Math.min(1 - span, this.t0 + (pan / r.width) * rs));
          this.t1 = this.t0 + span;
          this.draw();
          return;
        }
        const at = this.timeAt(e.clientX, e.clientY) / this.duration;   // keep the time under the pointer in place
        const f = Math.min(1, Math.max(0, (at - this.t0) / span));
        const ns = Math.min(1, Math.max(this.minSpan, span * Math.exp(e.deltaY * 0.0015)));
        this.t0 = Math.max(0, Math.min(1 - ns, at - f * ns));
        this.t1 = this.t0 + ns;
        this.draw();
      }, { passive: false });
      c.addEventListener('dblclick', () => { this.t0 = 0; this.t1 = 1; this.draw(); });
    }
  }

  draw() {
    const c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.max(1, Math.round(c.clientWidth * dpr)), H = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const rows = this.layout(W, H, dpr);
    this.rows = rows;
    this.dpr = dpr;
    const pal = this.duration >= LONG ? PAL.orange : PAL.blue;
    // the waveform, spectrogram or Playhead strip and the rulers only change with size, zoom, rows, style
    // or new data: drawn once into `base`, which playback copies 60 times a second
    const key = [W, H, dpr, this.mode, this.t0, this.t1, rows.length, rows[0].b - rows[0].a, this.seq,
      this.mode === 'playhead' ? `${phLookPref()}|${!!phLibNow()}` : ''].join('|');
    const b = this.base;
    if (this.baseKey !== key) {
      if (b.width !== W || b.height !== H) { b.width = W; b.height = H; }
      const bg = b.getContext('2d');
      bg.setTransform(1, 0, 0, 1, 0, 0);
      this.paintBase(bg, W, H, rows, dpr, pal);
      this.baseKey = key;
    }
    const g = c.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.drawImage(b, 0, 0);
    if (this.mode === 'playhead') this.paintLegend(this.phR);
    this.heads(g, W, H, rows, dpr, pal);
    this.wantDetail(rows, W, dpr);
    const title = rows.length > 1 ? `${rows.length} rows of ${fmtSpan(rows[0].per)}. ${this.rowTitle}` : this.rowTitle;
    if (this.rowSel.title !== title) this.rowSel.title = title;
  }

  paintBase(g, W, H, rows, dpr, pal) {
    g.fillStyle = rows.length > 1 ? '#16181e' : '#1c1f26';
    g.fillRect(0, 0, W, H);
    if (rows.length > 1) { g.fillStyle = '#1c1f26'; for (const R of rows) g.fillRect(0, R.y, W, R.h); }
    const D = this.duration;
    if (this.mode === 'playhead') {
      this.drawPh(g, W, H, rows, dpr, pal);
    } else if (this.mode === 'spectrogram') {
      if (this.spec && D) for (const R of rows) this.specRow(g, R, W, dpr);
      else this.note(g, W, H, this.specErr ? `No spectrogram: ${this.specErr}` : 'Making the spectrogram…');
    } else if (this.data && D) {
      for (const R of rows) this.wave(g, pal, R, W, dpr);
    } else {
      this.note(g, W, H, this.error ? this.error : this.data ? 'This sound has no samples' : 'Reading the sound…');
    }
    if (!D) return;
    for (const R of rows) {   // after the end of the sound (the last row of a wrapped sound)
      const xe = Math.max(0, this.xOf(R, D, W));
      if (xe < W - 0.5) { g.fillStyle = '#16181e'; g.fillRect(xe, R.y, W - xe, R.h); }
    }
    if (this.ruler) for (const R of rows) this.timeRuler(g, W, R, dpr);
  }

  /** Rows and zoom can need more detail than the whole-sound overview holds: then the part on screen
   *  (with some room either side) is read again at the resolution the rows need. */
  wantDetail(rows, W, dpr) {
    const D = this.duration, id = this.id;
    if (!D || id == null || this.mode === 'playhead') return;
    const spec = this.mode === 'spectrogram';
    const over = spec ? this.spec : this.data && { ...this.data, cols: this.data.bins };
    if (!over) return;
    const a = Math.max(0, rows[0].a * D), b = Math.min(D, rows[rows.length - 1].b * D);
    if (!(b > a)) return;
    const need = (W / dpr) * rows.length;                   // columns on screen (CSS pixels)
    const sr = this.data?.samplerate || 0;
    const enough = (s) => s && s.start <= a + 1e-6 && s.end >= b - 1e-6
      && (s.cols / (s.end - s.start) * (b - a) >= need * 0.75 || (!spec && sr && s.cols >= (s.end - s.start) * sr * 0.99));
    if (enough(over) || enough(spec ? this.specDet : this.det && { ...this.det, cols: this.det.bins })) return;
    const pad = (b - a) * 0.25, pa = Math.max(0, a - pad), pb = Math.min(D, b + pad);
    const n = Math.max(200, Math.min(spec ? 4000 : 16000, Math.ceil((need * (pb - pa)) / (b - a))));
    const h = spec ? (this.ruler ? Math.max(128, Math.min(512, rows[0].h)) : 160) : 0;
    const key = [id, spec, pa.toFixed(3), pb.toFixed(3), n, h].join('|');
    if (key === this.detKey) return;
    this.detKey = key;
    clearTimeout(this.detT);
    this.detT = setTimeout(async () => {
      try {
        if (spec) {
          const s = await this.fetchSpec(id, n, h, pa, pb, this.spec?.ref);
          if (this.id !== id || this.detKey !== key) return;
          this.specDet = s;
        } else {
          const d = await api.audioWave(id, n, pa.toFixed(3), pb.toFixed(3));
          if (this.id !== id || this.detKey !== key) return;
          this.det = d;
        }
        this.seq++;
        this.draw();
      } catch { /* keep the overview */ }
    }, 150);
  }

  /** The Playhead strip of each row: drawn by Playhead.drawStrip() for that row's part of the sound. */
  drawPh(g, W, H, rows, dpr, pal) {
    const P = phLibNow();
    const doc = this.phd;
    const D = this.duration;
    this.phR = null;
    if (!doc || !P || !D) {
      const p = this.phP;
      this.note(g, W, H, this.phErr ? `Playhead analysis failed: ${this.phErr}`
        : p == null ? 'Playhead analysis…' : p < 0 ? 'Playhead analysis: reading the sound…' : `Playhead analysis ${Math.round(p * 100)}%…`);
      return;
    }
    const look = phLookPref();
    if (phFoldsTime(P, look)) { this.note(g, W, H, 'The bar raster is for grid thumbnails only (⚙ Settings → Sounds)'); return; }
    const theme = phTheme(pal);
    const done = Math.min(D, doc.source?.duration || D);   // a long sound is analysed over its first 10 minutes
    let err = null;
    for (const R of rows) {
      const from = R.a * D, to = R.b * D, end = Math.min(to, done);
      let w = 0;
      if (end > from) {
        w = Math.round((W * (end - from)) / (to - from));
        g.save();
        g.translate(0, R.y);
        g.beginPath(); g.rect(0, 0, w, R.h); g.clip();
        g.scale(dpr, dpr);   // Playhead's text and line sizes are in CSS pixels
        try {
          const r = P.drawStrip(g, doc, { width: w / dpr, height: R.h / dpr, from, to: end, theme, look: look === 'auto' ? undefined : look });
          if (!this.phR) this.phR = r;
        } catch (e) { err = e.message; }
        g.restore();
      }
      const xe = Math.min(W, this.xOf(R, D, W));
      if (w < xe - 1) {   // after the analysed part
        g.fillStyle = '#ffffff08';
        g.fillRect(w, R.y, xe - w, R.h);
        g.fillStyle = '#8890a0';
        g.font = `${11 * dpr}px system-ui`;
        g.fillText('not analysed (after the first 10 minutes)', w + 8 * dpr, R.y + R.h / 2);
      }
    }
    if (err) this.note(g, W, H, `Playhead can't draw this look: ${err}`);
  }

  /** The legend comes from Playhead (drawStrip's return value); the feedback link carries the look code. */
  paintLegend(r) {
    const text = r?.legend || '';
    if (this.phLegend.textContent !== text) { this.phLegend.textContent = text; this.phLegend.title = text; }
    const href = phFeedbackUrl(r?.code || phLookPref());
    if (this.phFb.getAttribute('href') !== href) this.phFb.setAttribute('href', href);
    this.phFb.classList.toggle('show', !!r);
  }

  note(g, W, H, text) {
    g.fillStyle = '#8890a0';
    g.font = `${Math.round(Math.min(H, 300) / 12) + 8}px system-ui, sans-serif`;
    g.textAlign = 'center';
    g.fillText(text, W / 2, H / 2);
    g.textAlign = 'left';
  }

  /** Columns of [ta, tb] seconds across W pixels (the first Wc of them), each the min / max / RMS of the
   *  bins under that pixel. `d` covers d.start .. d.end seconds (the overview: the whole sound). */
  columns(d, ch, W, ta, tb, Wc = W) {
    const n = d.bins, s0 = d.start ?? 0, s1 = d.end ?? d.duration, k = n / ((s1 - s0) || 1);
    const b0 = (ta - s0) * k, b1 = (tb - s0) * k;
    const lo = new Float32Array(W), hi = new Float32Array(W), rm = new Float32Array(W);
    const chans = ch == null ? [...Array(d.channels).keys()] : [ch];
    for (let x = 0; x < Wc; x++) {
      const a = Math.max(0, Math.floor(b0 + (x / W) * (b1 - b0))), b = Math.max(a + 1, Math.floor(b0 + ((x + 1) / W) * (b1 - b0)));
      let mn = 0, mx = 0, r = 0;
      for (const cc of chans) {
        const MN = d.min[cc], MX = d.max[cc], R = d.rms[cc];
        for (let i = a; i < b && i < n; i++) { if (MN[i] < mn) mn = MN[i]; if (MX[i] > mx) mx = MX[i]; if (R[i] > r) r = R[i]; }
      }
      lo[x] = mn; hi[x] = mx; rm[x] = r;
    }
    return { lo, hi, rm };
  }

  wave(g, pal, R, W, dpr) {
    const D = this.duration;
    const ta = R.a * D, tb = R.b * D;
    const det = this.det;
    const d = det && det.start <= ta + 1e-6 && det.end >= Math.min(tb, D) - 1e-6 ? det : this.data;
    const Wc = Math.max(0, Math.min(W, Math.ceil(this.xOf(R, D, W))));   // columns with sound in them
    const lanes = this.mode === 'stereo' ? d.channels : 1;
    const laneH = R.h / lanes;
    for (let L = 0; L < lanes; L++) {
      const y0 = R.y + L * laneH, mid = y0 + laneH / 2, amp = laneH * 0.46;
      const { lo, hi, rm } = this.columns(d, this.mode === 'stereo' ? L : null, W, ta, tb, Wc);
      g.strokeStyle = '#30353f';
      g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, mid); g.lineTo(Wc, mid); g.stroke();
      if (this.mode === 'db') {
        const dbY = (v) => { const db = 20 * Math.log10(Math.max(Math.abs(v), 1e-6)); return Math.max(0, Math.min(1, (db + 60) / 60)); };
        for (const ref of [-6, -12, -24, -48]) {   // guide lines
          const y = amp * ((ref + 60) / 60);
          g.strokeStyle = '#2a2e37';
          g.beginPath(); g.moveTo(0, mid - y); g.lineTo(Wc, mid - y); g.moveTo(0, mid + y); g.lineTo(Wc, mid + y); g.stroke();
          if (this.ruler && L === 0 && R.k === 0 && laneH > 120 * dpr) { g.fillStyle = '#5c6372'; g.font = `${10 * dpr}px system-ui`; g.fillText(`${ref} dB`, 4 * dpr, mid - y - 2 * dpr); }
        }
        for (let x = 0; x < Wc; x++) {
          const p = Math.max(dbY(lo[x]), dbY(hi[x])) * amp, r = dbY(rm[x]) * amp;
          g.fillStyle = pal.outline; g.fillRect(x, mid - p, 1, 2 * p);
          g.fillStyle = pal.body; g.fillRect(x, mid - r, 1, 2 * r);
        }
      } else if (this.mode === 'minmax') {
        g.fillStyle = pal.line;
        for (let x = 0; x < Wc; x++) {
          const yA = mid - hi[x] * amp, yB = mid - lo[x] * amp;
          g.fillRect(x, yA, 1, Math.max(1, yB - yA));
        }
        const clip = 0.999;   // clipping shows in red
        g.fillStyle = '#ff5050';
        for (let x = 0; x < Wc; x++) if (hi[x] >= clip || lo[x] <= -clip) g.fillRect(x, y0, 1, 3 * dpr);
      } else {   // peaks (+ each lane of "Channels")
        for (let x = 0; x < Wc; x++) {
          const p = Math.max(Math.abs(lo[x]), Math.abs(hi[x])) * amp, r = rm[x] * amp;
          g.fillStyle = pal.outline; g.fillRect(x, mid - p, 1, Math.max(1, 2 * p));
          g.fillStyle = pal.body; g.fillRect(x, mid - r, 1, Math.max(1, 2 * r));
        }
      }
      if (lanes > 1) {
        g.fillStyle = '#9aa3b5';
        g.font = `${Math.min(11 * dpr, laneH * 0.4)}px system-ui`;
        g.fillText(lanes === 2 ? (L ? 'R' : 'L') : `${L + 1}`, 6 * dpr, y0 + Math.min(14 * dpr, laneH * 0.5));
        if (L) { g.strokeStyle = '#3a404c'; g.beginPath(); g.moveTo(0, y0); g.lineTo(W, y0); g.stroke(); }
      }
    }
  }

  /** One row of spectrogram: the overview picture, and over it a finer one of the part on screen when there is one. */
  specRow(g, R, W, dpr) {
    const D = this.duration;
    const ra = R.a * D, rb = R.b * D, end = Math.min(rb, D);
    let covered = 0;
    g.imageSmoothingEnabled = true;
    for (const s of [this.spec, this.specDet]) {
      if (!s) continue;
      const a = Math.max(ra, s.start), b = Math.min(end, s.end);
      if (!(b > a)) continue;
      const k = s.img.width / (s.end - s.start);
      const x0 = ((a - ra) / (rb - ra)) * W, x1 = ((b - ra) / (rb - ra)) * W;
      g.drawImage(s.img, (a - s.start) * k, 0, Math.max(0.5, (b - a) * k), s.img.height, x0, R.y, x1 - x0, R.h);
      covered = Math.max(covered, b);
    }
    if (covered < end - 0.01 * (rb - ra)) {   // sounds only ffmpeg reads get their spectrogram 10 minutes at a time
      const x0 = Math.max(0, ((covered - ra) / (rb - ra)) * W), x1 = ((end - ra) / (rb - ra)) * W;
      g.fillStyle = '#8890a0';
      g.font = `${11 * dpr}px system-ui`;
      g.textAlign = 'center';
      g.fillText('zoom in to see the spectrogram here', (x0 + x1) / 2, R.y + R.h / 2);
      g.textAlign = 'left';
    }
    if (this.ruler) this.freqLabels(g, R, W, dpr);
  }

  timeRuler(g, W, R, dpr) {
    const D = this.duration, top = R.y;
    g.fillStyle = '#14161b';
    g.fillRect(0, R.ry, W, top - R.ry);
    const ta = R.a * D, tb = R.b * D, span = tb - ta;
    const steps = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((s) => span / s <= W / (70 * dpr)) || 1200;
    const fine = step < 1;
    g.fillStyle = '#8890a0';
    g.strokeStyle = '#3a404c';
    g.font = `${10 * dpr}px system-ui`;
    const stop = Math.min(tb, D);
    for (let i = Math.ceil(ta / step - 1e-9); i * step <= stop + 1e-9; i++) {
      const t = i * step, x = ((t - ta) / span) * W;
      g.beginPath(); g.moveTo(x, top - 6 * dpr); g.lineTo(x, top); g.stroke();
      const ts = Math.round(t);
      g.fillText(fine ? fmtTime(t, true) : `${Math.floor(ts / 60)}:${String(ts % 60).padStart(2, '0')}`, x + 3 * dpr, top - 7 * dpr);
    }
  }

  freqLabels(g, R, W, dpr) {
    const sr0 = this.data?.samplerate || 44100;
    const nyq = sr0 / Math.max(1, Math.round(sr0 / 22050)) / 2;   // the spectrogram looks at about 22 kHz
    const lo = Math.log(30), hi = Math.log(nyq);
    g.fillStyle = '#ffffffb0';
    g.font = `${10 * dpr}px system-ui`;
    let last = Infinity;
    for (const f of [50, 100, 200, 500, 1000, 2000, 5000, 10000]) {
      if (f >= nyq) continue;
      const y = R.y + (1 - (Math.log(f) - lo) / (hi - lo)) * R.h;
      if (last - y < 14 * dpr || y < R.y + 10 * dpr) continue;   // short rows: fewer labels
      g.fillText(f >= 1000 ? `${f / 1000}k` : `${f}`, 4 * dpr, y);
      last = y;
    }
  }

  heads(g, W, H, rows, dpr, pal) {
    const D = this.duration;
    if (!D) return;
    if (this.sel) for (const R of rows) {   // the selected region, on every row it touches
      const a = Math.max(this.sel.a, R.a * D), b = Math.min(this.sel.b, R.b * D, D);
      if (!(b > a)) continue;
      const xa = this.xOf(R, a, W), xb = this.xOf(R, b, W);
      g.fillStyle = '#7aa2ff2e';
      g.fillRect(xa, R.y, xb - xa, R.h);
      g.fillStyle = '#7aa2ff';
      if (a === this.sel.a) g.fillRect(Math.round(xa), R.y, dpr, R.h);
      if (b === this.sel.b) g.fillRect(Math.round(xb), R.y, dpr, R.h);
    }
    const mine = this.app.playingId === this.id;
    const t = mine ? this.app.audio.currentTime : null;
    if (t != null && t >= rows[0].a * D && t <= Math.min(rows[rows.length - 1].b, 1) * D) {
      for (const R of rows) {   // played rows shaded, the cursor on its row
        if (t >= R.b * D) { g.fillStyle = '#ffffff14'; g.fillRect(0, R.y, Math.min(W, this.xOf(R, D, W)), R.h); }
        else if (t >= R.a * D) {
          const x = this.xOf(R, t, W);
          g.fillStyle = '#ffffff14';
          g.fillRect(0, R.y, x, R.h);
          g.fillStyle = '#ffffff';
          g.fillRect(Math.round(x) - dpr, R.y, 2 * dpr, R.h);
        }
      }
    }
    if (this.hoverX != null && this.hoverY != null) {
      const R = this.rowAt(this.hoverY * dpr);
      const x = this.hoverX * dpr;
      const ht = (R.a + (x / W) * (R.b - R.a)) * D;
      if (ht <= D) {
        g.fillStyle = pal.line + '99';
        g.fillRect(Math.round(x), R.y, dpr, R.h);
        const label = fmtTime(ht, (R.b - R.a) * D < 20);
        g.font = `${11 * dpr}px system-ui`;
        const tw = g.measureText(label).width;
        const lx = Math.min(W - tw - 8 * dpr, x + 4 * dpr);
        g.fillStyle = '#000c';
        g.fillRect(lx - 3 * dpr, R.y + 3 * dpr, tw + 6 * dpr, 15 * dpr);
        g.fillStyle = '#fff';
        g.fillText(label, lx, R.y + 14 * dpr);
      }
    }
    if (t != null) {   // current time, bottom right
      const label = `${fmtTime(t, D < 20)} / ${fmtTime(D, D < 20)}`;
      g.font = `${11 * dpr}px system-ui`;
      const tw = g.measureText(label).width;
      g.fillStyle = '#000c';
      g.fillRect(W - tw - 12 * dpr, H - 20 * dpr, tw + 8 * dpr, 16 * dpr);
      g.fillStyle = '#fff';
      g.fillText(label, W - tw - 8 * dpr, H - 8 * dpr);
    }
  }
}
