// Full-screen viewer / quick look. Browsing here never changes a multi-selection.

import { originalUrl, thumbUrl } from './api.js';
import { el, extOf, fmtDate, fmtNum, prefs, VIEWABLE } from './util.js';

const BGS = ['dark', 'gray', 'light', 'checker'];

export class Viewer {
  constructor(app) {
    this.app = app;
    this.img = el('img', { class: 'v-img', draggable: 'false', alt: '' });
    this.stage = el('div', { class: 'v-stage' }, this.img);
    this.title = el('div', { class: 'v-title' });
    this.tagsEl = el('div', { class: 'v-tags' });
    this.info = el('div', { class: 'v-info' });
    this.selBadge = el('button', { class: 'v-sel', title: 'Toggle selection (X)', onclick: () => this.toggleSel() });
    this.zoomLbl = el('div', { class: 'v-zoom' });
    this.bar = el('div', { class: 'v-bar' }, this.selBadge, this.title, this.tagsEl, this.info, this.zoomLbl,
      el('button', { class: 'v-btn', title: 'Background (B)', text: 'BG', onclick: () => this.cycleBg() }),
      el('button', { class: 'v-btn', title: 'Pixel-art zoom (N)', text: 'PX', onclick: () => this.togglePixel() }),
      el('button', { class: 'v-btn', title: 'Fit (0)', text: 'Fit', onclick: () => this.fitView() }),
      el('button', { class: 'v-btn', title: '100% (Z)', text: '1:1', onclick: () => this.zoomTo(1) }),
      el('button', { class: 'v-btn', title: 'Close (Esc)', text: '✕', onclick: () => this.close() }));
    this.prev = el('button', { class: 'v-nav v-prev', text: '‹', title: 'Previous (←)', onclick: () => this.step(-1) });
    this.next = el('button', { class: 'v-nav v-next', text: '›', title: 'Next (→)', onclick: () => this.step(1) });
    this.root = el('div', { class: 'viewer', tabindex: '-1' }, this.stage, this.bar, this.prev, this.next);
    document.body.append(this.root);
    this.bg = prefs.get('viewer.bg', 'dark');
    this.pixel = prefs.get('viewer.pixel', false);
    this.isOpen = false;
    this.quick = false;
    this.s = 1; this.tx = 0; this.ty = 0;
    this.w = 0; this.h = 0;
    this.applyBg();
    this.bindPointer();
    this.img.addEventListener('load', () => this.onLoad());
    window.addEventListener('resize', () => { if (this.isOpen && this.fitted) this.fitView(); });
  }

  get items() { return this.app.items; }

  open(i, { quick = false } = {}) {
    if (!this.items || i < 0 || i >= this.items.ids.length) return;
    this.isOpen = true;
    this.quick = quick;
    this.root.classList.add('open');
    this.root.classList.toggle('quick', quick);
    this.show(i);
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.root.classList.remove('open');
    this.img.removeAttribute('src');
    this.app.onViewerClosed(this.i);
  }

  show(i) {
    const it = this.items;
    this.i = i;
    this.app.setFocus(i, { scroll: false });
    this.w = it.w[i]; this.h = it.h[i];
    this.full = false;
    this.fitted = true;
    const ext = extOf(it.n[i]);
    this.viewable = VIEWABLE.has(ext) && it.v[i] !== 'e';
    // Animated GIF/WebP/APNG: load the original so the animation plays. Otherwise the 1600px preview.
    const animated = this.viewable && (it.f?.[i] || ext === 'gif');
    const src = animated ? originalUrl(it, i) : thumbUrl(it, i, 1600);
    this.full = !!animated;
    this.img.classList.remove('ready');
    this.img.src = src;
    if (this.w && this.h) this.fitView();
    this.title.textContent = it.n[i];
    this.info.textContent = `${fmtNum(i + 1)} / ${fmtNum(it.ids.length)} · ${this.w && this.h ? `${this.w}×${this.h} · ` : ''}${fmtDate(it.d[i])}`;
    this.paintSel();
    this.paintTags();
    this.root.classList.toggle('alpha', !!it.a[i]);
    this.preload(i + 1); this.preload(i - 1);
  }

  preload(i) {
    if (i < 0 || i >= this.items.ids.length || this.items.v[i] === 'e') return;
    const im = new Image();
    im.decoding = 'async';
    im.src = thumbUrl(this.items, i, 1600);
  }

  onLoad() {
    if (!this.w || !this.h) { // dimensions unknown yet (not indexed): use what we got
      this.w = this.img.naturalWidth; this.h = this.img.naturalHeight;
      this.fitView();
    }
    this.img.classList.add('ready');
  }

  step(d) {
    const n = this.items.ids.length;
    const j = Math.min(n - 1, Math.max(0, this.i + d));
    if (j !== this.i) this.show(j);
  }

  paintSel() {
    const sel = this.app.selected.has(this.items.ids[this.i]);
    this.selBadge.classList.toggle('on', sel);
    this.selBadge.textContent = sel ? '✓ Selected' : 'Select';
  }

  paintTags() {
    if (!this.isOpen) return;
    const ts = this.items.t?.[this.i] || [];
    const tags = this.app.tags;
    this.tagsEl.replaceChildren(...ts.map((t) => el('span', { class: 'chip small', style: { '--c': tags.color(t) },
      text: tags.byId.get(t)?.name.split('/').pop() || '?' })));
  }

  toggleSel() {
    this.app.toggleSelect(this.i);
    this.paintSel();
  }

  // -- zoom / pan ---------------------------------------------------------
  fitView() {
    const r = this.stage.getBoundingClientRect();
    if (!this.w || !this.h || !r.width) return;
    const maxUp = this.pixel ? 16 : 4;
    this.s = Math.min((r.width - 40) / this.w, (r.height - 40) / this.h, maxUp);
    this.tx = (r.width - this.w * this.s) / 2;
    this.ty = (r.height - this.h * this.s) / 2;
    this.fitted = true;
    this.apply();
  }

  zoomTo(s, cx, cy) {
    const r = this.stage.getBoundingClientRect();
    if (cx == null) { cx = r.width / 2; cy = r.height / 2; }
    s = Math.min(64, Math.max(0.02, s));
    // keep the point under the cursor fixed
    this.tx = cx - (cx - this.tx) * (s / this.s);
    this.ty = cy - (cy - this.ty) * (s / this.s);
    this.s = s;
    this.fitted = false;
    this.apply();
  }

  apply() {
    const im = this.img;
    im.style.width = `${this.w}px`;
    im.style.height = `${this.h}px`;
    im.style.transform = `translate(${this.tx}px,${this.ty}px) scale(${this.s})`;
    this.zoomLbl.textContent = `${Math.round(this.s * 100)}%`;
    // Need more pixels than the 1600 preview has? Switch to the original file.
    if (!this.full && this.viewable && Math.max(this.w, this.h) * this.s > 1700 && Math.max(this.w, this.h) > 1600) {
      this.full = true;
      const it = this.items;
      const pre = new Image();
      const i = this.i;
      pre.onload = () => { if (this.isOpen && this.i === i) this.img.src = pre.src; };
      pre.src = `/f/${it.ids[i]}`;
    }
  }

  bindPointer() {
    this.stage.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = this.stage.getBoundingClientRect();
      const f = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015));
      this.zoomTo(this.s * f, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let drag = null;
    this.stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, tx: this.tx, ty: this.ty, moved: false };
      this.stage.setPointerCapture(e.pointerId);
    });
    this.stage.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      this.tx = drag.tx + dx; this.ty = drag.ty + dy;
      this.fitted = false;
      this.apply();
    });
    this.stage.addEventListener('pointerup', () => { drag = null; });
    this.stage.addEventListener('dblclick', (e) => {
      const r = this.stage.getBoundingClientRect();
      if (this.fitted) this.zoomTo(1, e.clientX - r.left, e.clientY - r.top); else this.fitView();
    });
  }

  cycleBg() {
    this.bg = BGS[(BGS.indexOf(this.bg) + 1) % BGS.length];
    prefs.set('viewer.bg', this.bg);
    this.applyBg();
  }

  applyBg() {
    for (const b of BGS) this.root.classList.toggle('bg-' + b, b === this.bg);
    this.root.classList.toggle('pixel', this.pixel);
  }

  togglePixel() {
    this.pixel = !this.pixel;
    prefs.set('viewer.pixel', this.pixel);
    this.applyBg();
    if (this.fitted) this.fitView();
  }

  /** Returns true if the key was handled. */
  onKey(e) {
    const k = e.key;
    if (k === 'Escape' || k === 'Enter') { this.close(); return true; }
    if (k === ' ' && !e.ctrlKey) { if (!e.repeat) this.close(); return true; }
    if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown') { this.step(1); return true; }
    if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') { this.step(-1); return true; }
    if (k === 'Home') { this.show(0); return true; }
    if (k === 'End') { this.show(this.items.ids.length - 1); return true; }
    if (k === 'x' || k === 'X' || (k === ' ' && e.ctrlKey)) { this.toggleSel(); return true; }
    if (k === 'b' || k === 'B') { this.cycleBg(); return true; }
    if (k === 'n' || k === 'N') { this.togglePixel(); return true; }
    if (k === '0') { this.fitView(); return true; }
    if (k === 'z' || k === 'Z') { if (this.fitted) this.zoomTo(1); else this.fitView(); return true; }
    if (/^[1-9]$/.test(k) && !e.ctrlKey && !e.metaKey) {  // tag hotkeys apply to the shown image only
      const t = this.app.tags.byHotkey.get(+k);
      if (t) this.app.applyTag(t.id, { targets: [this.items.ids[this.i]] }).then(() => this.paintTags());
      else this.app.toast(`No tag on hotkey ${k} yet`);
      return true;
    }
    if (k === 't' || k === 'T') { this.app.tags.quickTag([this.items.ids[this.i]]); return true; }
    if (k === '+' || k === '=') { this.zoomTo(this.s * 1.25); return true; }
    if (k === '-') { this.zoomTo(this.s / 1.25); return true; }
    return false;
  }
}
