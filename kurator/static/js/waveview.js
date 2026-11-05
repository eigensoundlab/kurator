// Waveform view for sounds: playhead, click / drag to seek, wheel to zoom (in the viewer), and
// several ways to look at a sound. Draws on a canvas from per-channel min / max / RMS data.
//
//   Peak + RMS   the classic editor look: peak outline, loudness body (default)
//   Min / max    the true, asymmetric signal range (DC offset, clipping, polarity show up)
//   Loudness dB  amplitude on a decibel scale: quiet tails and noise floors become visible
//   Channels     every channel in its own lane (stereo balance, phase problems, dead channels)
//   Spectrogram  frequencies over time (log scale, 30 Hz – Nyquist): hum, hiss, pitch, transients

import { api } from './api.js';
import { el, fmtDuration, prefs } from './util.js';

export const WAVE_MODES = [['peaks', 'Peak + RMS'], ['minmax', 'Min / max'], ['db', 'Loudness (dB)'], ['stereo', 'Channels'], ['spectrogram', 'Spectrogram']];
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
    this.mode = prefs.get('wave.mode', 'peaks');
    this.modeSel = el('select', { class: 'wavemode', title: 'How to draw the sound' }, WAVE_MODES.map(([k, t]) => el('option', { value: k, text: t })));
    this.modeSel.value = this.mode;
    this.modeSel.onchange = () => this.setMode(this.modeSel.value);
    this.modeSel.addEventListener('pointerdown', (e) => e.stopPropagation());
    this.selBar = el('div', { class: 'wave-selbar' });
    this.root = el('div', { class: 'waveview' + (ruler ? ' big' : '') }, this.canvas, this.modeSel, this.selBar);
    this.sel = null;          // selected region {a, b} in seconds (big view: drag to select)
    this.loopSel = prefs.get('wave.loop', false);
    this.id = null; this.data = null; this.spec = null; this.t0 = 0; this.t1 = 1; this.hoverX = null;
    this.bind();
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  async load(id, duration) {
    this.id = id;
    this.duration = duration || 0;
    this.data = null; this.spec = null; this.t0 = 0; this.t1 = 1;
    this.setSel(null);
    this.draw();
    try {
      const d = await api.audioWave(id, this.bins);
      if (this.id !== id) return;
      this.data = d;
      this.duration = d.duration || this.duration;
    } catch (e) { this.error = e.message; }
    if (this.mode === 'spectrogram') this.loadSpec();
    this.draw();
    this.loop();
  }

  loadSpec() {
    const id = this.id;
    const img = new Image();
    img.onload = () => { if (this.id === id) { this.spec = img; this.draw(); } };
    img.src = `/api/audio/${id}/spectrogram?w=${this.ruler ? 2400 : 1200}&h=${this.ruler ? 384 : 160}`;
  }

  setMode(m) {
    this.mode = m;
    prefs.set('wave.mode', m);
    this.modeSel.value = m;
    if (m === 'spectrogram' && !this.spec && this.id != null) this.loadSpec();
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

  timeAt(clientX) {
    const r = this.canvas.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    return (this.t0 + f * (this.t1 - this.t0)) * this.duration;
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

  bind() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !this.duration) return;
      e.stopPropagation();
      c.setPointerCapture(e.pointerId);
      const t = this.timeAt(e.clientX);
      if (!this.ruler) {   // small view: click and drag move the playhead
        drag = { scrub: true };
        this.app.playAt(this.id, t);
        this.loop();
        return;
      }
      // big view: click plays from there, drag selects a region, Shift+click extends the selection
      if (e.shiftKey && this.sel) {
        const anchor = Math.abs(t - this.sel.a) < Math.abs(t - this.sel.b) ? this.sel.b : this.sel.a;
        drag = { x: e.clientX, anchor, moved: true };
        this.setSel({ a: Math.min(anchor, t), b: Math.max(anchor, t) });
        return;
      }
      drag = { x: e.clientX, anchor: t, moved: false };
    });
    c.addEventListener('pointermove', (e) => {
      const r = c.getBoundingClientRect();
      this.hoverX = e.clientX - r.left;
      if (drag?.scrub && this.app.playingId === this.id) this.app.audio.currentTime = this.timeAt(e.clientX);
      else if (drag && (drag.moved || Math.abs(e.clientX - drag.x) > 4)) {
        drag.moved = true;
        const t = this.timeAt(e.clientX);
        this.sel = { a: Math.min(drag.anchor, t), b: Math.max(drag.anchor, t) };
        this.playSel = false;
      }
      this.draw();
    });
    c.addEventListener('pointerup', (e) => {
      if (drag && !drag.scrub) {
        if (drag.moved) { this.setSel(this.sel); this.freshSel = !!this.sel; }   // Space now plays the new selection
        else {
          const t = this.timeAt(e.clientX);
          this.playSel = !!(this.sel && t >= this.sel.a && t < this.sel.b);   // inside the selection: play just that
          this.app.playAt(this.id, t);
          this.loop();
        }
      }
      drag = null;
    });
    c.addEventListener('pointerleave', () => { this.hoverX = null; this.draw(); });
    c.addEventListener('click', (e) => e.stopPropagation());
    if (this.zoomable) {
      c.addEventListener('wheel', (e) => {
        e.preventDefault();
        const r = c.getBoundingClientRect();
        const f = (e.clientX - r.left) / r.width;
        const at = this.t0 + f * (this.t1 - this.t0);
        const span = Math.min(1, Math.max(0.002, (this.t1 - this.t0) * Math.exp(e.deltaY * 0.0015)));
        this.t0 = Math.max(0, Math.min(1 - span, at - f * span));
        this.t1 = this.t0 + span;
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
    const g = c.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = '#1c1f26';
    g.fillRect(0, 0, W, H);
    const top = this.ruler ? Math.round(20 * dpr) : 0;
    const pal = this.duration >= LONG ? PAL.orange : PAL.blue;
    const d = this.data;
    if (this.mode === 'spectrogram') {
      if (this.spec) {
        const iw = this.spec.naturalWidth;
        g.imageSmoothingEnabled = true;
        g.drawImage(this.spec, this.t0 * iw, 0, (this.t1 - this.t0) * iw, this.spec.naturalHeight, 0, top, W, H - top);
        if (this.ruler) this.freqLabels(g, top, W, H, dpr);
      } else this.note(g, W, H, 'Making the spectrogram…');
    } else if (d) {
      this.wave(g, d, pal, top, W, H, dpr);
    } else {
      this.note(g, W, H, this.error ? this.error : 'Reading the sound…');
    }
    if (this.ruler && this.duration) this.timeRuler(g, W, top, dpr);
    this.heads(g, W, H, top, dpr, pal);
  }

  note(g, W, H, text) {
    g.fillStyle = '#8890a0';
    g.font = `${Math.round(H / 12) + 8}px system-ui, sans-serif`;
    g.textAlign = 'center';
    g.fillText(text, W / 2, H / 2);
    g.textAlign = 'left';
  }

  /** Columns of the visible range, each the min / max / RMS of the bins under that pixel. */
  columns(d, ch, W) {
    const n = d.bins, b0 = this.t0 * n, b1 = this.t1 * n;
    const lo = new Float32Array(W), hi = new Float32Array(W), rm = new Float32Array(W);
    const chans = ch == null ? [...Array(d.channels).keys()] : [ch];
    for (let x = 0; x < W; x++) {
      const a = Math.floor(b0 + (x / W) * (b1 - b0)), b = Math.max(a + 1, Math.floor(b0 + ((x + 1) / W) * (b1 - b0)));
      let mn = 0, mx = 0, r = 0;
      for (const cc of chans) {
        const MN = d.min[cc], MX = d.max[cc], R = d.rms[cc];
        for (let k = a; k < b && k < n; k++) { if (MN[k] < mn) mn = MN[k]; if (MX[k] > mx) mx = MX[k]; if (R[k] > r) r = R[k]; }
      }
      lo[x] = mn; hi[x] = mx; rm[x] = r;
    }
    return { lo, hi, rm };
  }

  wave(g, d, pal, top, W, H, dpr) {
    const lanes = this.mode === 'stereo' ? d.channels : 1;
    const laneH = (H - top) / lanes;
    for (let L = 0; L < lanes; L++) {
      const y0 = top + L * laneH, mid = y0 + laneH / 2, amp = laneH * 0.46;
      const { lo, hi, rm } = this.columns(d, this.mode === 'stereo' ? L : null, W);
      g.strokeStyle = '#30353f';
      g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, mid); g.lineTo(W, mid); g.stroke();
      if (this.mode === 'db') {
        const dbY = (v) => { const db = 20 * Math.log10(Math.max(Math.abs(v), 1e-6)); return Math.max(0, Math.min(1, (db + 60) / 60)); };
        for (const ref of [-6, -12, -24, -48]) {   // guide lines
          const y = amp * ((ref + 60) / 60);
          g.strokeStyle = '#2a2e37';
          g.beginPath(); g.moveTo(0, mid - y); g.lineTo(W, mid - y); g.moveTo(0, mid + y); g.lineTo(W, mid + y); g.stroke();
          if (this.ruler && L === 0) { g.fillStyle = '#5c6372'; g.font = `${10 * dpr}px system-ui`; g.fillText(`${ref} dB`, 4 * dpr, mid - y - 2 * dpr); }
        }
        for (let x = 0; x < W; x++) {
          const p = Math.max(dbY(lo[x]), dbY(hi[x])) * amp, r = dbY(rm[x]) * amp;
          g.fillStyle = pal.outline; g.fillRect(x, mid - p, 1, 2 * p);
          g.fillStyle = pal.body; g.fillRect(x, mid - r, 1, 2 * r);
        }
      } else if (this.mode === 'minmax') {
        g.fillStyle = pal.line;
        for (let x = 0; x < W; x++) {
          const yA = mid - hi[x] * amp, yB = mid - lo[x] * amp;
          g.fillRect(x, yA, 1, Math.max(1, yB - yA));
        }
        const clip = 0.999;   // clipping shows in red
        g.fillStyle = '#ff5050';
        for (let x = 0; x < W; x++) if (hi[x] >= clip || lo[x] <= -clip) g.fillRect(x, y0, 1, 3 * dpr);
      } else {   // peaks (+ each lane of "Channels")
        for (let x = 0; x < W; x++) {
          const p = Math.max(Math.abs(lo[x]), Math.abs(hi[x])) * amp, r = rm[x] * amp;
          g.fillStyle = pal.outline; g.fillRect(x, mid - p, 1, Math.max(1, 2 * p));
          g.fillStyle = pal.body; g.fillRect(x, mid - r, 1, Math.max(1, 2 * r));
        }
      }
      if (lanes > 1) {
        g.fillStyle = '#9aa3b5';
        g.font = `${11 * dpr}px system-ui`;
        g.fillText(lanes === 2 ? (L ? 'R' : 'L') : `${L + 1}`, 6 * dpr, y0 + 14 * dpr);
        if (L) { g.strokeStyle = '#3a404c'; g.beginPath(); g.moveTo(0, y0); g.lineTo(W, y0); g.stroke(); }
      }
    }
  }

  timeRuler(g, W, top, dpr) {
    g.fillStyle = '#14161b';
    g.fillRect(0, 0, W, top);
    const span = (this.t1 - this.t0) * this.duration;
    const steps = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((s) => span / s <= W / (70 * dpr)) || 1200;
    const fine = step < 1;
    g.fillStyle = '#8890a0';
    g.strokeStyle = '#3a404c';
    g.font = `${10 * dpr}px system-ui`;
    const start = Math.ceil((this.t0 * this.duration) / step) * step;
    for (let t = start; t <= this.t1 * this.duration + 1e-9; t += step) {
      const x = ((t / this.duration - this.t0) / (this.t1 - this.t0)) * W;
      g.beginPath(); g.moveTo(x, top - 6 * dpr); g.lineTo(x, top); g.stroke();
      g.fillText(fmtTime(t, fine), x + 3 * dpr, top - 7 * dpr);
    }
  }

  freqLabels(g, top, W, H, dpr) {
    const sr = this.data?.samplerate ? Math.min(this.data.samplerate, 22050) : 22050;
    const lo = Math.log(30), hi = Math.log(sr / 2);
    g.fillStyle = '#ffffffb0';
    g.font = `${10 * dpr}px system-ui`;
    for (const f of [50, 100, 200, 500, 1000, 2000, 5000, 10000]) {
      if (f >= sr / 2) continue;
      const y = top + (1 - (Math.log(f) - lo) / (hi - lo)) * (H - top);
      g.fillText(f >= 1000 ? `${f / 1000}k` : `${f}`, 4 * dpr, y);
    }
  }

  heads(g, W, H, top, dpr, pal) {
    if (this.sel && this.duration) {   // the selected region
      const xa = ((this.sel.a / this.duration - this.t0) / (this.t1 - this.t0)) * W;
      const xb = ((this.sel.b / this.duration - this.t0) / (this.t1 - this.t0)) * W;
      g.fillStyle = '#7aa2ff2e';
      g.fillRect(xa, top, xb - xa, H - top);
      g.fillStyle = '#7aa2ff';
      g.fillRect(Math.round(xa), top, dpr, H - top);
      g.fillRect(Math.round(xb), top, dpr, H - top);
    }
    const mine = this.app.playingId === this.id;
    const t = mine ? this.app.audio.currentTime : null;
    if (t != null && this.duration) {
      const x = ((t / this.duration - this.t0) / (this.t1 - this.t0)) * W;
      if (x >= 0 && x <= W) {
        g.fillStyle = '#ffffff14';
        g.fillRect(0, top, x, H - top);
        g.fillStyle = '#ffffff';
        g.fillRect(Math.round(x) - dpr, top, 2 * dpr, H - top);
      }
    }
    if (this.hoverX != null && this.duration) {
      const x = this.hoverX * dpr;
      const ht = (this.t0 + (x / W) * (this.t1 - this.t0)) * this.duration;
      g.fillStyle = pal.line + '99';
      g.fillRect(Math.round(x), top, dpr, H - top);
      const label = fmtTime(ht, (this.t1 - this.t0) * this.duration < 20);
      g.font = `${11 * dpr}px system-ui`;
      const tw = g.measureText(label).width;
      const lx = Math.min(W - tw - 8 * dpr, x + 4 * dpr);
      g.fillStyle = '#000c';
      g.fillRect(lx - 3 * dpr, top + 3 * dpr, tw + 6 * dpr, 15 * dpr);
      g.fillStyle = '#fff';
      g.fillText(label, lx, top + 14 * dpr);
    }
    if (t != null) {   // current time, bottom right
      const label = `${fmtTime(t, this.duration < 20)} / ${fmtTime(this.duration, this.duration < 20)}`;
      g.font = `${11 * dpr}px system-ui`;
      const tw = g.measureText(label).width;
      g.fillStyle = '#000c';
      g.fillRect(W - tw - 12 * dpr, H - 20 * dpr, tw + 8 * dpr, 16 * dpr);
      g.fillStyle = '#fff';
      g.fillText(label, W - tw - 8 * dpr, H - 8 * dpr);
    }
  }
}
