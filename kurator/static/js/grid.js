// Virtualized thumbnail grid.
// Only the visible rows (+ overscan) exist in the DOM; cells are recycled while scrolling,
// so 100k items cost the same as 100. Layout is pure arithmetic - no image has to load first.

import { originalUrl, thumbUrl } from './api.js';
import { phEnabled, phGridPref, phLib, phLibNow, phOnSetting, phTheme, phThumb } from './playhead.js';
import { AUDIO, el, extOf, fmtDuration, fmtMonth, MESHES, VIDEO, VIEWABLE } from './util.js';

const PAD = 12;
const GAP = 8;
const LABEL_H = 20;
const OVERSCAN = 3;
const BADGE_EXT = new Set(['gif', 'kra', 'ora', 'webp', 'tif', 'tiff', 'bmp', 'avif']);
// Playhead strips: the colours of Kurator's waveform thumbnails (long sounds orange, short ones blue)
const PH_PAL = { blue: { outline: '#4662a8', body: '#8cb0ff', line: '#7aa2ff' }, orange: { outline: '#a85c24', body: '#ffac5c', line: '#fa9646' } };

export class Grid {
  constructor(scrollEl, app) {
    this.el = scrollEl;
    this.app = app;
    this.spacer = el('div', { class: 'grid-spacer' });
    this.layer = el('div', { class: 'grid-layer' });
    this.badge = el('div', { class: 'scroll-badge' });
    this.empty = el('div', { class: 'grid-empty' });
    this.el.append(this.spacer, this.layer, this.empty);
    this.el.parentElement.append(this.badge);
    this.active = new Map(); // index -> cell
    this.free = [];
    this.items = null;
    this.size = 170;
    this.fit = 'contain';
    this.labels = false;
    this.cols = 1; this.cellW = 100; this.rowH = 100; this.rows = 0;
    this.dirty = true;
    this.raf = 0;
    this.badgeTimer = 0;
    // Playhead strips in place of waveform thumbnails (sounds with an analysis; ⚙ Settings → Sounds)
    const phPrefs = () => { const g = phGridPref(); this.phOn = phEnabled() && g.on; this.phLook = g.look || 'auto'; if (this.phOn) phLib().catch(() => {}); };
    phPrefs();
    phOnSetting(() => { phPrefs(); this.dirty = true; this.schedule(); });
    this.el.addEventListener('scroll', () => { this.schedule(); this.showBadge(); this.stopHover(); }, { passive: true });
    // Animated GIF/WebP/APNG play when hovered (the grid shows a still first frame).
    this.hovered = null;
    this.hoverTimer = 0;
    this.layer.addEventListener('pointerover', (e) => {
      const cell = e.target.closest('.cell');
      if (cell === this.hovered) return;
      this.stopHover();
      this.hovered = cell;
      if (!cell || !this.app.animateOnHover) return;
      const i = +cell.dataset.i;
      const ext = extOf(this.items.n[i]);
      if ((VIDEO.has(ext) || MESHES.has(ext)) && this.items.v[i] !== 'e') { this.startScrub(cell, i); return; }
      if (!this.items.f?.[i] || !VIEWABLE.has(extOf(this.items.n[i])) || this.items.v[i] === 'e') return;
      this.hoverTimer = setTimeout(() => {
        if (this.hovered !== cell || +cell.dataset.i !== i) return;
        cell._anim = true;
        cell._img.src = originalUrl(this.items, i);
      }, 250);
    });
    this.layer.addEventListener('pointerleave', () => this.stopHover());
    this.layer.addEventListener('pointermove', (e) => { if (this.hovered?._scrub) this.scrub(this.hovered, e); });
    new ResizeObserver(() => this.relayout()).observe(this.el);
  }

  /** Videos: moving the mouse across the thumbnail runs through a strip of frames.
   *  3D models: the same with a turntable - the mouse turns the model. */
  startScrub(cell, i) {
    const items = this.items;
    const url = `/t/${items.ids[i]}?s=1` + (items.v[i] ? `&v=${items.v[i]}` : '');
    const im = new Image();
    im.onload = () => {
      if (this.hovered !== cell || +cell.dataset.i !== i) return;
      const model = MESHES.has(extOf(items.n[i]));
      const w = model ? 1 : items.w[i] || 16, h = model ? 1 : items.h[i] || 9;
      const n = Math.max(1, Math.round(im.naturalWidth / (im.naturalHeight * (w / h))));
      if (!cell._strip) { cell._strip = el('div', { class: 'strip' }); cell._th.append(cell._strip); }
      cell._strip.style.backgroundImage = `url("${url}")`;
      cell._scrub = { n, aspect: w / h };
      cell.classList.add('scrubbing');
      if (this._lastMove) this.scrub(cell, this._lastMove);
    };
    im.src = url;
  }

  scrub(cell, e) {
    this._lastMove = e;
    const r = cell._th.getBoundingClientRect();
    const { n, aspect } = cell._scrub;
    const k = Math.min(n - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * n)));
    // the same "contain" box the thumbnail uses
    const fw = aspect >= 1 ? r.width : r.height * aspect, fh = aspect >= 1 ? r.width / aspect : r.height;
    Object.assign(cell._strip.style, { backgroundSize: `${fw * n}px ${fh}px`, backgroundPosition: `${(r.width - fw) / 2 - k * fw}px ${(r.height - fh) / 2}px` });
    cell._strip.dataset.k = `${k + 1}/${n}`;
  }

  stopHover() {
    clearTimeout(this.hoverTimer);
    const c = this.hovered;
    this.hovered = null;
    this._lastMove = null;
    if (c && c._scrub) { c._scrub = null; c.classList.remove('scrubbing'); }
    if (c && c._anim) {
      c._anim = false;
      if (c._img.dataset.url) c._img.src = c._img.dataset.url;
    }
  }

  get count() { return this.items ? this.items.ids.length : 0; }

  setItems(items, { keepScroll = true } = {}) {
    this.items = items;
    if (!keepScroll) this.el.scrollTop = 0;
    this.relayout();
  }

  setOptions({ size, fit, labels }) {
    const anchor = this.firstVisibleIndex();
    if (size != null) this.size = size;
    if (fit != null) this.fit = fit;
    if (labels != null) this.labels = labels;
    this.el.classList.toggle('fit-cover', this.fit === 'cover');
    this.el.classList.toggle('labels', this.labels);
    this.relayout();
    if (anchor >= 0) this.scrollToIndex(anchor, 'start');
  }

  relayout() {
    const w = this.el.clientWidth;
    const inner = Math.max(50, w - PAD * 2);
    this.cols = Math.max(1, Math.floor((inner + GAP) / (this.size + GAP)));
    this.cellW = Math.floor((inner - GAP * (this.cols - 1)) / this.cols);
    this.rowH = this.cellW + GAP + (this.labels ? LABEL_H : 0);
    this.rows = Math.ceil(this.count / this.cols);
    this.spacer.style.height = `${PAD * 2 + this.rows * this.rowH}px`;
    this.dpr = window.devicePixelRatio || 1;
    this.thumbSize = this.cellW * this.dpr > 280 ? 512 : 256;
    this.dirty = true;
    this.empty.style.display = this.count ? 'none' : '';
    this.render();
  }

  schedule() {
    if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); });
  }

  pos(i) {
    return [PAD + (i % this.cols) * (this.cellW + GAP), PAD + Math.floor(i / this.cols) * this.rowH];
  }

  visibleRange(overscan = OVERSCAN) {
    const n = this.count;
    if (!n) return [0, -1];
    const top = this.el.scrollTop;
    const h = this.el.clientHeight;
    const r0 = Math.max(0, Math.floor((top - PAD) / this.rowH) - overscan);
    const r1 = Math.min(this.rows - 1, Math.floor((top + h - PAD) / this.rowH) + overscan);
    return [r0 * this.cols, Math.min(n - 1, (r1 + 1) * this.cols - 1)];
  }

  firstVisibleIndex() {
    if (!this.count) return -1;
    const r = Math.max(0, Math.floor((this.el.scrollTop - PAD + this.rowH * 0.5) / this.rowH));
    return Math.min(this.count - 1, r * this.cols);
  }

  render() {
    const [i0, i1] = this.visibleRange();
    for (const [i, cell] of this.active) {
      if (i < i0 || i > i1 || i >= this.count) this.release(i, cell);
    }
    for (let i = i0; i <= i1; i++) {
      let cell = this.active.get(i);
      if (!cell) {
        cell = this.acquire();
        this.active.set(i, cell);
        this.fill(cell, i);
      } else if (this.dirty) {
        this.fill(cell, i);
      }
    }
    this.dirty = false;
  }

  acquire() {
    let cell = this.free.pop();
    if (cell) { cell.style.display = ''; return cell; }
    const img = el('img', { draggable: 'false', decoding: 'async', alt: '' });
    img.onload = () => img.classList.add('loaded');
    img.onerror = () => { if (img.getAttribute('src')) cell.classList.add('broken'); };
    const th = el('div', { class: 'th' }, img);
    cell = el('div', { class: 'cell' }, th, el('div', { class: 'ext' }), el('div', { class: 'check' }), el('div', { class: 'label' }), el('div', { class: 'dots' }),
      el('button', { class: 'play', type: 'button', title: 'Play / stop (Space)', tabindex: '-1' }),
      el('button', { class: 'stackbadge', type: 'button', title: 'Versions - click to see them all', tabindex: '-1' }));
    cell._img = img; cell._th = th; cell._ext = cell.children[1]; cell._label = cell.children[3]; cell._dots = cell.children[4];
    this.layer.append(cell);
    return cell;
  }

  release(i, cell) {
    this.active.delete(i);
    if (cell === this.hovered) this.stopHover();
    cell._anim = false;
    cell.style.display = 'none';
    // Dropping src cancels a download that is no longer needed (fast scrolling).
    cell._img.removeAttribute('src');
    cell._img.dataset.url = '';
    cell._img.classList.remove('loaded');
    this.free.push(cell);
  }

  fill(cell, i) {
    const it = this.items;
    const [x, y] = this.pos(i);
    cell.dataset.i = i;
    cell.style.transform = `translate(${x}px,${y}px)`;
    cell.style.width = `${this.cellW}px`;
    cell._th.style.height = `${this.cellW}px`;
    const c = it.c[i];
    cell._th.style.backgroundColor = c >= 0 ? `#${c.toString(16).padStart(6, '0')}` : '';
    cell.classList.toggle('alpha', !!it.a[i]);
    const broken = it.v[i] === 'e';
    cell.classList.toggle('broken', broken);
    const url = broken ? '' : thumbUrl(it, i, this.thumbSize);
    const img = cell._img;
    if (cell._anim && cell !== this.hovered) { cell._anim = false; img.dataset.url = ''; }
    if (img.dataset.url !== url && !cell._anim) {
      img.classList.remove('loaded');
      img.dataset.url = url;
      if (url) img.src = url; else img.removeAttribute('src');
    }
    const name = it.n[i];
    const ext = extOf(name);
    const anim = it.f?.[i];
    cell._ext.textContent = anim || VIDEO.has(ext) ? `▶ ${ext.toUpperCase()}` : BADGE_EXT.has(ext) ? ext.toUpperCase() : '';
    cell._ext.style.display = cell._ext.textContent ? '' : 'none';
    cell.classList.toggle('audio', AUDIO.has(ext) && VIEWABLE.has(ext));   // ▶ only where the browser can play it
    if (this.labels) cell._label.textContent = name;
    cell.title = name;
    this.paintPh(cell, i);
    this.paintState(cell, i);
  }

  /** A Playhead strip over a sound's waveform thumbnail, drawn by Playhead.drawStrip() from a light
   *  copy of its stored analysis; sounds without one keep the waveform. */
  paintPh(cell, i) {
    const it = this.items;
    const stamp = this.phOn ? it.p?.[i] || 0 : 0;
    const id = it.ids[i];
    const cover = this.el.classList.contains('fit-cover');
    const key = stamp ? `${id}:${stamp}:${this.cellW}:${this.phLook}:${cover}` : '';
    if (cell._phKey === key) return;
    cell._phKey = key;
    cell.classList.remove('ph-strip');
    if (!key) return;
    phThumb(id, stamp).then(async (t) => {
      const P = phLibNow() || await phLib().catch(() => null);
      if (cell._phKey !== key || !t || !P) return;
      if (!cell._ph) { cell._ph = el('canvas', { class: 'ph-thumb' }); cell._th.append(cell._ph); }
      const c = cell._ph;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = this.cellW, h = cover ? this.cellW : Math.round(this.cellW / 2);
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
      const g = c.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = '#1c1f26';
      g.fillRect(0, 0, w, h);
      const done = t.doc.source?.duration || 0, dur = Math.max(t.duration || 0, done);
      const sw = dur > done + 1 ? Math.max(1, Math.round((w * done) / dur)) : w;   // only the analysed first 10 minutes
      const look = this.phLook === 'auto' || !P.looks[this.phLook] || P.lookFits(t.doc, this.phLook) ? this.phLook : 'auto';
      try {
        P.drawStrip(g, t.doc, { width: sw, height: h, theme: phTheme(dur >= 30 ? PH_PAL.orange : PH_PAL.blue), look: look === 'auto' ? undefined : look });
      } catch { return; }
      if (sw < w) { g.fillStyle = '#ffffff0d'; g.fillRect(sw, 0, w - sw, h); }
      if (dur && w >= 90) {   // the length, as on the waveform thumbnails
        const label = fmtDuration(dur);
        g.font = '11px system-ui, sans-serif';
        const tw = g.measureText(label).width;
        g.fillStyle = '#000c';
        g.fillRect(w - tw - 10, h - 17, tw + 8, 14);
        g.fillStyle = '#e6ebf5';
        g.fillText(label, w - tw - 6, h - 6);
      }
      cell.classList.add('ph-strip');
    });
  }

  paintState(cell, i) {
    const app = this.app;
    const id = this.items.ids[i];
    cell.classList.toggle('sel', app.selected.has(id));
    cell.classList.toggle('focus', i === app.focusIndex);
    cell.classList.toggle('dim', !app.matchesFilter(i));
    cell.classList.toggle('playing', id === app.playingId);
    cell.classList.toggle('member', !!this.items.m?.[i]);
    const k = this.items.k?.[i] || 0;
    cell.classList.toggle('stacked', k > 1);
    if (k > 1) cell.lastChild.textContent = `▤ ${k}`;
    cell.classList.toggle('gB', !!(this.items.g && this.items.g[i] % 2));   // "similar images": every other group tinted   // "add from library": already in the project
    this.paintTags(cell, i);
  }

  paintTags(cell, i) {
    const ts = this.items.t?.[i] || [];
    const sg = this.items.s?.[i] || [];
    const key = ts.join(',') + '|' + sg.map((x) => x[0]).join(',');
    if (cell._tagKey === key) return;
    cell._tagKey = key;
    const tags = this.app.tags;
    const dots = ts.slice(0, 6).map((t) => el('span', { style: { background: tags.color(t) }, title: tags.byId.get(t)?.name || '' }));
    // AI suggestions: hollow dots until you accept them (A) or reject them (Shift+A)
    const sdots = sg.slice(0, 4).map(([t, score]) => el('span', { class: 'sug', style: { borderColor: tags.color(t) }, title: `AI suggests ${tags.byId.get(t)?.name || ''} (${Math.round(score * 100)}%)` }));
    cell._dots.replaceChildren(...dots, ...sdots);
  }

  /** Re-paint everything visible (after tag changes). */
  repaint() { this.dirty = true; for (const c of this.active.values()) c._tagKey = null; this.render(); }

  /** Re-paint selection/focus classes of visible cells (cheap, no layout). */
  refreshStates() {
    for (const [i, cell] of this.active) this.paintState(cell, i);
  }

  /** Index under a point in content coordinates (relative to the scroll content). */
  indexAt(cx, cy) {
    const col = Math.floor((cx - PAD) / (this.cellW + GAP));
    const row = Math.floor((cy - PAD) / this.rowH);
    if (col < 0 || col >= this.cols || row < 0) return -1;
    const lx = cx - PAD - col * (this.cellW + GAP);
    const ly = cy - PAD - row * this.rowH;
    if (lx > this.cellW || ly > this.rowH - GAP) return -1;
    const i = row * this.cols + col;
    return i < this.count ? i : -1;
  }

  /** All indices whose cells intersect a rectangle in content coordinates. */
  indicesInRect(x0, y0, x1, y1) {
    const out = [];
    if (!this.count) return out;
    const stepX = this.cellW + GAP;
    const c0 = Math.max(0, Math.floor((x0 - PAD) / stepX));
    const c1 = Math.min(this.cols - 1, Math.floor((x1 - PAD) / stepX));
    const r0 = Math.max(0, Math.floor((y0 - PAD) / this.rowH));
    const r1 = Math.min(this.rows - 1, Math.floor((y1 - PAD) / this.rowH));
    for (let r = r0; r <= r1; r++) {
      const cy0 = PAD + r * this.rowH, cy1 = cy0 + this.rowH - GAP;
      if (cy1 < y0 || cy0 > y1) continue;
      for (let c = c0; c <= c1; c++) {
        const cx0 = PAD + c * stepX, cx1 = cx0 + this.cellW;
        if (cx1 < x0 || cx0 > x1) continue;
        const i = r * this.cols + c;
        if (i < this.count) out.push(i);
      }
    }
    return out;
  }

  scrollToIndex(i, mode = 'nearest') {
    if (i < 0 || i >= this.count) return;
    const [, y] = this.pos(i);
    const top = this.el.scrollTop, h = this.el.clientHeight;
    if (mode === 'start') this.el.scrollTop = y - PAD;
    else if (mode === 'center') this.el.scrollTop = y - (h - this.rowH) / 2;
    else if (y < top + PAD) this.el.scrollTop = y - PAD;
    else if (y + this.rowH > top + h) this.el.scrollTop = y + this.rowH - h + PAD;
    this.render();
  }

  rowsPerPage() { return Math.max(1, Math.floor(this.el.clientHeight / this.rowH)); }

  showBadge() {
    if (!this.count) return;
    const i = this.firstVisibleIndex();
    const sort = this.app.query.sort;
    let text = '';
    if (sort === 'date') text = fmtMonth(this.items.d[i]);
    else if (sort === 'name') text = (this.items.n[i] || '?').slice(0, 1).toUpperCase();
    else if (sort === 'type') text = extOf(this.items.n[i]).toUpperCase();
    if (!text) return;
    this.badge.textContent = text;
    this.badge.classList.add('show');
    clearTimeout(this.badgeTimer);
    this.badgeTimer = setTimeout(() => this.badge.classList.remove('show'), 700);
  }
}
