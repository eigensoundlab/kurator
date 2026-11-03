// Compare 2-4 images side by side. Zoom (wheel) and pan (drag) move all of them together, so the
// same detail stays lined up. With two images, Swipe (S) lays one over the other with a divider.

import { originalUrl, thumbUrl } from './api.js';
import { el, extOf, fmtDate, VIEWABLE } from './util.js';

export class Compare {
  constructor(app) {
    this.app = app;
    this.root = el('div', { class: 'compare', tabindex: '-1' });
    document.body.append(this.root);
    this.isOpen = false;
    window.addEventListener('resize', () => { if (this.isOpen) this.layout(); });
  }

  open(indices) {
    const it = this.app.items;
    this.items = indices.slice(0, 4).map((i) => ({
      id: it.ids[i], name: it.n[i], date: it.d[i], w: it.w[i], h: it.h[i],
      src: VIEWABLE.has(extOf(it.n[i])) && !it.f?.[i] ? originalUrl(it, i) : thumbUrl(it, i, 1600),
    }));
    this.z = 1; this.u = 0.5; this.v = 0.5;   // shared zoom (1 = fit) and centre (0..1 of each image)
    this.swipe = false; this.split = 0.5;
    this.isOpen = true;
    this.root.classList.add('open');
    this.build();
    this.root.focus();
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.root.classList.remove('open');
    this.root.replaceChildren();
    this.app.grid.el.focus({ preventScroll: true });
  }

  build() {
    const bar = el('div', { class: 'cmp-bar' },
      el('button', { class: 'v-back', title: 'Back to the grid (Esc)', onclick: () => this.close() }, el('span', { text: '← Back' }), el('span', { class: 'v-key', text: 'Esc' })),
      el('span', { class: 'cmp-hint', text: 'wheel: zoom together · drag: move together · 0: fit · 1: 100%' + (this.items.length === 2 ? ' · S: swipe' : '') }),
      this.items.length === 2 ? el('button', { class: 'v-btn' + (this.swipe ? ' on' : ''), text: 'Swipe', title: 'One over the other with a divider (S)', onclick: () => this.toggleSwipe() }) : null,
      el('button', { class: 'v-btn', text: 'Fit', onclick: () => { this.z = 1; this.u = this.v = 0.5; this.paint(); } }));
    this.panes = [];
    const grid = el('div', { class: `cmp-grid n${this.swipe ? 1 : this.items.length}` });
    const list = this.swipe ? [this.items] : this.items.map((x) => [x]);
    for (const group of list) {
      const pane = el('div', { class: 'cmp-pane' });
      const imgs = group.map((x, k) => {
        const im = el('img', { class: 'cmp-img' + (k ? ' top' : ''), src: x.src, draggable: 'false', alt: '' });
        im.addEventListener('load', () => { if (!x.w || !x.h) { x.w = im.naturalWidth; x.h = im.naturalHeight; } this.paint(); });
        return im;
      });
      const labels = group.map((x, k) => el('div', { class: 'cmp-label' + (k ? ' right' : '') },
        el('b', { text: x.name }), el('span', { class: 'muted', text: ` · ${x.w && x.h ? `${x.w}×${x.h} · ` : ''}${fmtDate(x.date)}` })));
      const divider = this.swipe ? el('div', { class: 'cmp-divider' }) : null;
      pane.append(...imgs, ...labels, ...(divider ? [divider] : []));
      this.panes.push({ pane, imgs, group, divider });
      grid.append(pane);
      this.bind(pane);
    }
    this.root.replaceChildren(grid, bar);
    this.layout();
  }

  toggleSwipe() {
    if (this.items.length !== 2) return;
    this.swipe = !this.swipe;
    this.build();
  }

  layout() { this.paint(); }

  /** Place every image: fitted size x shared zoom, the shared centre in the middle of its pane. */
  paint() {
    for (const p of this.panes) {
      const r = p.pane.getBoundingClientRect();
      p.group.forEach((x, k) => {
        if (!x.w || !x.h) return;
        const fit = Math.min(r.width / x.w, r.height / x.h);
        const s = fit * this.z;
        const tx = r.width / 2 - this.u * x.w * s, ty = r.height / 2 - this.v * x.h * s;
        Object.assign(p.imgs[k].style, { width: `${x.w}px`, height: `${x.h}px`, transform: `translate(${tx}px,${ty}px) scale(${s})` });
      });
      if (p.divider) {
        const x = r.width * this.split;
        const it = p.group[1];   // the clip is in the image's own (unscaled) pixels
        if (it.w) {
          const s = Math.min(r.width / it.w, r.height / it.h) * this.z;
          const tx = r.width / 2 - this.u * it.w * s;
          p.imgs[1].style.clipPath = `inset(0 0 0 ${Math.max(0, (x - tx) / s)}px)`;
        }
        p.divider.style.left = `${x}px`;
      }
    }
  }

  bind(pane) {
    let drag = null;
    pane.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = pane.getBoundingClientRect();
      const p = this.panes.find((q) => q.pane === pane);
      const x = p.group[0];
      if (!x.w) return;
      const s = Math.min(r.width / x.w, r.height / x.h) * this.z;
      // the image point under the cursor stays under the cursor
      const iu = this.u + (e.clientX - r.left - r.width / 2) / (x.w * s);
      const iv = this.v + (e.clientY - r.top - r.height / 2) / (x.h * s);
      const nz = Math.min(64, Math.max(0.2, this.z * Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015))));
      const ns = s * (nz / this.z);
      this.u = iu - (e.clientX - r.left - r.width / 2) / (x.w * ns);
      this.v = iv - (e.clientY - r.top - r.height / 2) / (x.h * ns);
      this.z = nz;
      this.paint();
    }, { passive: false });
    pane.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const p = this.panes.find((q) => q.pane === pane);
      drag = { x: e.clientX, y: e.clientY, u: this.u, v: this.v, p };
      pane.setPointerCapture(e.pointerId);
    });
    pane.addEventListener('pointermove', (e) => {
      const p = this.panes.find((q) => q.pane === pane);
      if (p.divider && !drag) {   // swipe: the divider follows the mouse
        const r = pane.getBoundingClientRect();
        this.split = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        this.paint();
        return;
      }
      if (!drag) return;
      const x = drag.p.group[0];
      const r = pane.getBoundingClientRect();
      const s = Math.min(r.width / x.w, r.height / x.h) * this.z;
      this.u = drag.u - (e.clientX - drag.x) / (x.w * s);
      this.v = drag.v - (e.clientY - drag.y) / (x.h * s);
      this.paint();
    });
    pane.addEventListener('pointerup', () => { drag = null; });
    pane.addEventListener('dblclick', () => { this.z = this.z === 1 ? 2 : 1; if (this.z === 1) this.u = this.v = 0.5; this.paint(); });
  }

  /** 100% for the first image (others follow at the same relative zoom). */
  actualSize() {
    const p = this.panes[0], x = p.group[0];
    const r = p.pane.getBoundingClientRect();
    this.z = 1 / Math.min(r.width / x.w, r.height / x.h);
    this.paint();
  }

  onKey(e) {
    const k = e.key;
    if (k === 'Escape' || k === 'c' || k === 'C') { this.close(); return true; }
    if (k === 's' || k === 'S') { this.toggleSwipe(); return true; }
    if (k === '0') { this.z = 1; this.u = this.v = 0.5; this.paint(); return true; }
    if (k === '1') { this.actualSize(); return true; }
    if (k === '+' || k === '=') { this.z = Math.min(64, this.z * 1.25); this.paint(); return true; }
    if (k === '-') { this.z = Math.max(0.2, this.z / 1.25); this.paint(); return true; }
    return false;
  }
}
