// Print views in the viewer:
//   Inks  - the C, M, Y, K plates of a CMYK picture (or of an RGB picture as the chosen CMYK proofing
//           profile would separate it), total ink coverage with a limit, gamut warning, soft proof,
//           and the ink amounts under the mouse.
//   Boxes - trim and bleed boxes of a PDF page drawn over the preview.

import { el, prefs, setKids } from './util.js';

const PLATE_RGB = [[0, 174, 239], [236, 0, 140], [255, 242, 0], [35, 31, 32]];

export class InkView {
  constructor(stage, viewer) {
    this.viewer = viewer;
    this.canvas = el('canvas', { class: 'inkview' });
    stage.append(this.canvas);
    this.g = this.canvas.getContext('2d', { willReadFrequently: false });
    this.view = 'composite';
    this.limit = prefs.get('ink.limit', 300);
    this.tinted = prefs.get('ink.tinted', true);
    this.data = null;
    this.readout = el('div', { class: 'ink-readout' });
    this.canvas.addEventListener('pointermove', (e) => this.hover(e));
    this.canvas.addEventListener('pointerleave', () => { this.readout.textContent = ''; });
    this.onChange = () => {};
  }

  async load(id) {
    this.clear();
    const token = (this.token = (this.token || 0) + 1);
    const r = await fetch(`/api/items/${id}/inks`);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch { /* binary */ }
      throw new Error(msg);
    }
    const buf = await r.arrayBuffer();
    if (token !== this.token) return;
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== 0x4b4e494b) throw new Error('bad ink data');   // 'KINK'
    const hl = dv.getUint32(4, true);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hl)));
    const n = head.width * head.height;
    this.head = head;
    this.cmyk = new Uint8Array(buf, 8 + hl, n * 4);
    this.gamut = head.gamut ? new Uint8Array(buf, 8 + hl + n * 4, n) : null;
    this.proof = null;
    if (head.source === 'proof') {
      const img = new Image();
      img.src = `/api/items/${id}/proof`;
      await img.decode().catch(() => {});
      if (token !== this.token) return;
      this.proof = img;
    }
    this.canvas.width = head.width;
    this.canvas.height = head.height;
    this.id = id;
    this.render();
    this.onChange();
  }

  clear() { this.token = (this.token || 0) + 1; this.head = null; this.cmyk = null; this.gamut = null; this.proof = null; this.id = null; }

  set on(v) { this._on = v; this.canvas.classList.toggle('on', !!v); if (v) this.render(); }
  get on() { return !!this._on; }

  render() {
    const h = this.head;
    if (!h || !this.on) return;
    const W = h.width, H = h.height, n = W * H, c = this.cmyk, g = this.g;
    if (this.view === 'composite' && this.proof) {
      g.drawImage(this.proof, 0, 0, W, H);
      return;
    }
    const img = g.createImageData(W, H), o = img.data;
    const lim = (this.limit / 100) * 255;
    if (this.view === 'composite') {   // CMYK file: a simple ink mix (the original preview uses its profile)
      for (let i = 0, j = 0; i < n; i++, j += 4) {
        const k = 1 - c[j + 3] / 255;
        o[j] = 255 * (1 - c[j] / 255) * k; o[j + 1] = 255 * (1 - c[j + 1] / 255) * k; o[j + 2] = 255 * (1 - c[j + 2] / 255) * k; o[j + 3] = 255;
      }
    } else if (this.view === 'total' || this.view === 'over') {
      for (let i = 0, j = 0; i < n; i++, j += 4) {
        const t = c[j] + c[j + 1] + c[j + 2] + c[j + 3];
        const v = 255 - Math.min(255, t / 4);
        if (t > lim) { o[j] = 255; o[j + 1] = 40; o[j + 2] = 40; } else { o[j] = v; o[j + 1] = v; o[j + 2] = v; }
        o[j + 3] = 255;
      }
    } else if (this.view === 'gamut' && this.gamut) {
      const base = this.proof ? this.readProof() : null;
      for (let i = 0, j = 0; i < n; i++, j += 4) {
        if (this.gamut[i]) { o[j] = 0; o[j + 1] = 255; o[j + 2] = 120; }
        else if (base) { const v = (base[j] + base[j + 1] + base[j + 2]) / 3 * 0.6 + 60; o[j] = v; o[j + 1] = v; o[j + 2] = v; }
        else { o[j] = o[j + 1] = o[j + 2] = 160; }
        o[j + 3] = 255;
      }
    } else {   // one plate
      const p = +this.view;
      const tint = PLATE_RGB[p];
      for (let i = 0, j = 0; i < n; i++, j += 4) {
        const a = c[j + p] / 255;
        if (this.tinted) { o[j] = 255 - (255 - tint[0]) * a; o[j + 1] = 255 - (255 - tint[1]) * a; o[j + 2] = 255 - (255 - tint[2]) * a; }
        else { const v = 255 - c[j + p]; o[j] = v; o[j + 1] = v; o[j + 2] = v; }
        o[j + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
  }

  readProof() {
    if (this._proofPx && this._proofFor === this.id) return this._proofPx;
    const t = document.createElement('canvas');
    t.width = this.head.width; t.height = this.head.height;
    const tg = t.getContext('2d');
    tg.drawImage(this.proof, 0, 0, t.width, t.height);
    this._proofPx = tg.getImageData(0, 0, t.width, t.height).data;
    this._proofFor = this.id;
    return this._proofPx;
  }

  hover(e) {
    const h = this.head;
    if (!h) return;
    const r = this.canvas.getBoundingClientRect();
    const x = Math.floor(((e.clientX - r.left) / r.width) * h.width), y = Math.floor(((e.clientY - r.top) / r.height) * h.height);
    if (x < 0 || y < 0 || x >= h.width || y >= h.height) { this.readout.textContent = ''; return; }
    const j = (y * h.width + x) * 4, c = this.cmyk;
    const pct = (v) => Math.round((v / 255) * 100);
    const t = pct(c[j]) + pct(c[j + 1]) + pct(c[j + 2]) + pct(c[j + 3]);
    this.readout.textContent = `C ${pct(c[j])}  M ${pct(c[j + 1])}  Y ${pct(c[j + 2])}  K ${pct(c[j + 3])}  ·  total ${t} %`
      + (this.gamut?.[y * h.width + x] ? '  ·  out of gamut' : '');
    this.readout.classList.toggle('over', t > this.limit);
  }

  controls() {
    const box = el('div', { class: 'mv-controls ink-controls' });
    const paint = () => {
      const h = this.head;
      if (!h) { setKids(box, el('div', { class: 'mv-hint', text: 'Reading the inks…' })); return; }
      const tab = (key, label, title) => el('button', { class: 'lit-tab' + (this.view === key ? ' on' : ''), type: 'button', text: label, title,
        onclick: () => { this.view = key; this.render(); paint(); } });
      const lim = el('input', { type: 'range', min: '180', max: '400', step: '5', value: String(this.limit) });
      const limLbl = el('span', { text: `${this.limit} %` });
      lim.oninput = () => { this.limit = +lim.value; limLbl.textContent = `${this.limit} %`; prefs.set('ink.limit', this.limit); if (this.view === 'total') this.render(); };
      const tint = el('input', { type: 'checkbox' });
      tint.checked = this.tinted;
      tint.onchange = () => { this.tinted = tint.checked; prefs.set('ink.tinted', this.tinted); this.render(); };
      setKids(box,
        el('div', { class: 'mv-hint', text: h.source === 'proof' ? `Proof: ${h.profile || 'CMYK profile'}` : `CMYK${h.profile ? ` · ${h.profile}` : ''}` }),
        el('div', { class: 'lit-tabs' },
          tab('composite', h.source === 'proof' ? 'Proof' : 'Inks', h.source === 'proof' ? 'How it prints with the proofing profile' : 'The four inks mixed'),
          tab('0', 'C', 'Cyan plate'), tab('1', 'M', 'Magenta plate'), tab('2', 'Y', 'Yellow plate'), tab('3', 'K', 'Black plate'),
          tab('total', 'Total ink', 'Total area coverage; red: over the limit'),
          this.gamut ? tab('gamut', 'Gamut', 'Green: colours the printer can\'t reach') : null),
        el('label', { class: 'lit-slider', title: 'Typical limits: 300 % coated, 260–280 % uncoated, 240 % newsprint' }, el('span', { text: 'Ink limit' }), lim),
        el('div', { class: 'mv-hint' }, limLbl),
        el('label', { class: 'chk' }, tint, 'Plates in their ink colour'),
        this.readout);
    };
    this.onChange = paint;
    paint();
    return box;
  }
}

/** Trim and bleed boxes of a PDF page, as an overlay that follows the viewer's image. */
export class BoxOverlay {
  constructor(stage) {
    this.root = el('div', { class: 'pdfboxes' });
    stage.append(this.root);
  }

  show(rel) {
    const colours = { media: '#8890a0', bleed: '#ff5a5a', trim: '#3ddc84' };
    setKids(this.root, Object.entries(rel || {}).map(([k, [x, y, w, h]]) => el('div', {
      class: `pbox pbox-${k}`, title: `${k} box`,
      style: { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%`, borderColor: colours[k] || '#fff' } },
    el('span', { text: k }))));
  }

  set on(v) { this.root.classList.toggle('on', !!v); }
}
