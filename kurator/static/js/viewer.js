// Full-screen viewer / quick look. Browsing here never changes a multi-selection.

import { api, originalUrl, thumbUrl } from './api.js';
import { Lit2D } from './lit2d.js';
import { MeshView } from './meshview.js';
import { BoxOverlay, InkView } from './printview.js';
import { openRegionDialog } from './regiondlg.js';
import { WaveView } from './waveview.js';
import { AUDIO, digitKey, el, extOf, fmtBytes, fmtDate, fmtNum, MESHES, prefs, VIDEO, VIEWABLE } from './util.js';

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
      this.inkBtn = el('button', { class: 'v-btn v-inkbtn', title: 'Inks: C, M, Y, K plates, total ink, gamut (I)', text: '◧ Inks', onclick: () => this.toggleInks() }),
      this.boxBtn = el('button', { class: 'v-btn v-boxbtn', title: 'Trim and bleed boxes of the PDF page', text: '▭ Boxes', onclick: () => this.toggleBoxes() }),
      this.litBtn = el('button', { class: 'v-btn v-litbtn', title: 'Lit preview with the companion maps (L)', text: '☀ Lit', onclick: () => this.toggleLit() }),
      el('button', { class: 'v-btn', title: 'Background (B)', text: 'BG', onclick: () => this.cycleBg() }),
      el('button', { class: 'v-btn v-2d', title: 'Pixel-art zoom (N)', text: 'PX', onclick: () => this.togglePixel() }),
      el('button', { class: 'v-btn v-fit', title: 'Fit / reset view (0)', text: 'Fit', onclick: () => (this.is3d ? this.mv?.reset() : this.fitView()) }),
      el('button', { class: 'v-btn v-2d', title: '100% (Z)', text: '1:1', onclick: () => this.zoomTo(1) }),
      el('button', { class: 'v-btn', title: 'Close (Esc)', text: '✕', onclick: () => this.close() }));
    this.prev = el('button', { class: 'v-nav v-prev', text: '‹', title: 'Previous (←)', onclick: () => this.step(-1) });
    this.next = el('button', { class: 'v-nav v-next', text: '›', title: 'Next (→)', onclick: () => this.step(1) });
    this.back = el('button', { class: 'v-back', title: 'Back to the grid (Esc)', onclick: () => this.close() },
      el('span', { text: '← Back' }), el('span', { class: 'v-key', text: 'Esc' }));
    this.loading = el('div', { class: 'v-loading' }, el('div', { class: 'v-load-text' }), el('div', { class: 'pbar' }, el('span')));
    // pages of a PDF: ‹ page 3 / 12 ›
    this.pageLbl = el('input', { class: 'v-page-in', type: 'text', inputmode: 'numeric', title: 'Page (type a number, Enter)' });
    this.pageLbl.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { const n = parseInt(this.pageLbl.value, 10); if (n > 0) this.gotoPage(n - 1); this.pageLbl.blur(); }
      if (e.key === 'Escape') { this.paintPage(); this.pageLbl.blur(); }
    });
    this.pageTotal = el('span', { class: 'v-page-total' });
    this.pageBox = el('div', { class: 'v-pages' },
      el('button', { class: 'v-btn', text: '‹', title: 'Previous page ([ or Page Up)', onclick: () => this.gotoPage(this.page - 1) }),
      el('span', { text: 'Page' }), this.pageLbl, this.pageTotal,
      el('button', { class: 'v-btn', text: '›', title: 'Next page (] or Page Down)', onclick: () => this.gotoPage(this.page + 1) }));
    for (const ev of ['pointerdown', 'wheel', 'dblclick']) this.pageBox.addEventListener(ev, (e) => e.stopPropagation());
    this.root = el('div', { class: 'viewer', tabindex: '-1' }, this.stage, this.loading, this.back, this.bar, this.prev, this.next, this.pageBox);
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
    this.mv?.clear();
    this.setLit(false, true);
    this.setInks(false);
    this.setLoading(null);
    if (this.video) { this.video.pause(); this.video.removeAttribute('src'); this.video.load(); }
    this.isVideo = false;
    this.isSound = false;
    this.pdf = null;
    if (this.wave?.root.isConnected) this.wave.root.remove();
    this.root.classList.remove('mode3d', 'mesh-ready', 'mode-video', 'mode-sound', 'has-pages');
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
    const isVideo = VIDEO.has(ext);
    const animated = !isVideo && this.viewable && (it.f?.[i] || ext === 'gif');
    const src = animated ? originalUrl(it, i) : thumbUrl(it, i, 1600);
    this.full = !!animated;
    this.img.classList.remove('ready');
    this.img.src = src;
    this.show3d(MESHES.has(ext) && it.v[i] !== 'e' ? it.ids[i] : null);
    this.showVideo(isVideo && this.viewable ? it.ids[i] : null);
    this.showSound(AUDIO.has(ext) ? it.ids[i] : null);
    this.findMaps(!this.is3d && !isVideo && !AUDIO.has(ext) ? it.ids[i] : null);
    this.findPrint(!this.is3d && !isVideo && !AUDIO.has(ext) ? it.ids[i] : null);
    this.findPages((ext === 'pdf' || ext === 'ai') && it.v[i] !== 'e' ? it.ids[i] : null);
    if (this.w && this.h) this.fitView();
    this.title.textContent = it.n[i];
    this.info.textContent = `${fmtNum(i + 1)} / ${fmtNum(it.ids.length)} · ${this.w && this.h ? `${this.w}×${this.h} · ` : ''}${fmtDate(it.d[i])}`
      + (this.is3d ? ' · drag: turn · right-drag: move · wheel: zoom · 0: reset' : '')
      + (this.isVideo ? ' · Space: play/pause · Shift+← →: 5 s' : isVideo ? ' · this format plays in your video app (O)' : '')
      + (this.isSound ? ' · click: play from there · drag: select · wheel: zoom · Shift+wheel: move · double-click: whole sound · Space: play/pause' : '')
      + (ext === 'pdf' || ext === 'ai' ? ' · [ ]: pages' : '');
    this.paintSel();
    this.paintTags();
    this.root.classList.toggle('alpha', !!it.a[i]);
    this.preload(i + 1); this.preload(i - 1);
  }

  /** 2D art with companion maps (normal, glow, AO, depth...): the ☀ Lit button and the lit preview. */
  async findMaps(id) {
    this.maps2d = null;
    this.root.classList.remove('has-maps');
    if (id == null) { this.setLit(false, true); return; }
    this.mapsCache ||= new Map();
    let g = this.mapsCache.get(id);
    if (!g) {
      try { g = await (await fetch(`/api/items/${id}/maps2d`)).json(); } catch { g = { maps: [] }; }
      this.mapsCache.set(id, g);
      if (this.mapsCache.size > 200) this.mapsCache.delete(this.mapsCache.keys().next().value);
    }
    if (!this.isOpen || this.items.ids[this.i] !== id) return;
    if (!g.maps?.length) { this.setLit(false, true); return; }
    this.maps2d = g;
    this.root.classList.add('has-maps');
    this.litBtn.title = `Lit preview with ${g.maps.map((m) => m.label.toLowerCase()).join(', ')} (L)`;
    if (prefs.get('viewer.lit', false)) this.setLit(true, true);
  }

  /** Print views: inks for CMYK pictures (or any picture with a proofing profile), boxes for PDFs. */
  async findPrint(id) {
    this.root.classList.remove('has-inks', 'has-boxes');
    this.printFor = id;
    if (id == null) { this.setInks(false); this.setBoxes(null); return; }
    let d = this.app.panel.detail?.id === id ? this.app.panel.detail : null;
    if (!d) { try { d = await api.item(id); } catch { return; } }
    if (!this.isOpen || this.printFor !== id) return;
    const m = d.meta || {};
    const cmyk = m.mode === 'CMYK' || m.colour?.mode === 'CMYK';
    this.inksOk = d.kind === 'image' && (cmyk || !!this.app.state?.proof_profile);
    this.root.classList.toggle('has-inks', this.inksOk);
    const rel = m.print?.boxes_rel;
    const boxes = rel && (rel.trim || rel.bleed) ? rel : null;
    this.root.classList.toggle('has-boxes', !!boxes);
    this.setBoxes(boxes);
    if (this.inksOn && this.inksOk) this.setInks(true); else if (!this.inksOk) this.setInks(false, true);
  }

  /** PDFs (and PDF-based .ai): page through the document, re-rendered sharp when zoomed in. */
  async findPages(id) {
    this.pdf = null;
    this.page = 0;
    this.renderedSide = 1600;
    this.root.classList.remove('has-pages');
    if (id == null) return;
    let d;
    try { d = await (await fetch(`/api/items/${id}/pages`)).json(); } catch { return; }
    if (!this.isOpen || this.items.ids[this.i] !== id || !d?.count) return;
    this.pdf = { id, ...d };
    this.root.classList.toggle('has-pages', d.count > 1);
    if (d.count > 1 && this.fitted) this.fitView();
    this.paintPage();
  }

  paintPage() {
    if (!this.pdf) return;
    const lab = this.pdf.labels?.[this.page];
    this.pageLbl.value = lab || String(this.page + 1);
    this.pageTotal.textContent = `/ ${fmtNum(this.pdf.count)}`;
  }

  gotoPage(n) {
    const p = this.pdf;
    if (!p) return false;
    n = Math.max(0, Math.min(p.count - 1, n));
    if (n === this.page) { this.paintPage(); return true; }
    this.page = n;
    [this.w, this.h] = p.pages[n];
    this.renderedSide = 1600;
    this.img.classList.remove('ready');
    this.img.src = n === 0 ? thumbUrl(this.items, this.i, 1600) : `/api/items/${p.id}/page/${n}?s=1600`;
    this.fitView();
    this.paintPage();
    this.info.textContent = this.info.textContent.replace(/^([^·]+· )(\d+×\d+ · )?/, `$1${this.w}×${this.h} · `);
    fetch(`/api/items/${p.id}/page/${n}/info`).then((r) => r.json()).then((pi) => {
      if (!this.isOpen || this.pdf !== p || this.page !== n) return;
      const rel = pi.boxes_rel;
      const boxes = rel && (rel.trim || rel.bleed) ? rel : null;
      this.root.classList.toggle('has-boxes', !!boxes);
      this.setBoxes(boxes);
    }).catch(() => {});
    return true;
  }

  /** Zoomed in on a PDF page: render it again with more pixels (vector sharp), a moment after zooming stops. */
  sharpenPage() {
    const p = this.pdf;
    if (!p) return;
    const need = Math.min(8000, Math.ceil(Math.max(this.w, this.h) * this.s * (window.devicePixelRatio || 1)));
    if (need <= this.renderedSide * 1.15) return;
    clearTimeout(this.sharpTimer);
    this.sharpTimer = setTimeout(() => {
      if (!this.isOpen || this.pdf !== p) return;
      const n = this.page, side = Math.min(8000, Math.ceil(need / 256) * 256);
      const pre = new Image();
      pre.onload = () => { if (this.isOpen && this.pdf === p && this.page === n) { this.img.src = pre.src; this.renderedSide = side; } };
      pre.src = `/api/items/${p.id}/page/${n}?s=${side}`;
    }, 250);
  }

  toggleInks() {
    if (!this.inksOk) { this.app.toast('Inks: for CMYK pictures, or choose a CMYK proofing profile in ⚙ Settings'); return; }
    this.setInks(!this.inksOn);
  }

  setInks(on, keep = false) {
    if (on && !this.ink) {
      this.ink = new InkView(this.stage, this);
      this.inkBox = this.ink.controls();
      this.root.append(this.inkBox);
    }
    if (on && this.litOn) this.setLit(false);
    if (!keep) this.inksOn = !!on;
    this.root.classList.toggle('ink-on', !!on);
    this.inkBtn.classList.toggle('on', !!on);
    if (!this.ink) return;
    this.ink.on = !!on;
    if (!on) return;
    const id = this.items.ids[this.i];
    if (this.ink.id !== id) {
      this.ink.clear();
      this.ink.onChange();
      this.ink.load(id).then(() => this.apply()).catch((e) => { this.app.toast(`Inks: ${e.message}`, 5000); this.setInks(false); });
    }
    this.apply();
  }

  toggleBoxes() {
    this.boxesOn = !this.boxesOn;
    prefs.set('viewer.boxes', this.boxesOn);
    if (this.boxes) this.boxes.on = this.boxesOn;
    this.boxBtn.classList.toggle('on', this.boxesOn);
  }

  setBoxes(rel) {
    if (!rel) { if (this.boxes) this.boxes.on = false; return; }
    if (!this.boxes) this.boxes = new BoxOverlay(this.stage);
    this.boxes.show(rel);
    this.boxesOn = prefs.get('viewer.boxes', true);
    this.boxes.on = this.boxesOn;
    this.boxBtn.classList.toggle('on', this.boxesOn);
    this.apply();
  }

  toggleLit() {
    if (!this.maps2d) { this.app.toast('No companion maps (like name_normal.png, name_glow.png) next to this picture'); return; }
    this.setLit(!this.litOn);
    prefs.set('viewer.lit', this.litOn);
  }

  setLit(on, quiet = false) {
    if (on && !this.maps2d) on = false;
    if (on && this.inksOn) this.setInks(false);
    if (on && !this.lit) {
      this.lit = new Lit2D(this.stage, this);
      this.litBox = this.lit.controls();
      this.root.append(this.litBox);
    }
    this.litOn = !!on;
    this.root.classList.toggle('lit-on', this.litOn);
    this.litBtn.classList.toggle('on', this.litOn);
    if (!this.lit) return;
    this.lit.on = this.litOn;
    if (!this.litOn) { if (quiet) this.lit.clear(); this.lit.onChange(); return; }
    if (this.lit.group?.main.id !== this.maps2d.main.id) {
      this.lit.load(this.maps2d).then(() => this.apply()).catch((e) => { this.app.toast(`Lit preview: ${e.message}`); this.setLit(false); });
    }
    this.apply();
  }

  /** Sounds: a big waveform with time ruler, playhead and the display modes. */
  showSound(id) {
    this.isSound = id != null;
    this.root.classList.toggle('mode-sound', this.isSound);
    if (!this.isSound) { if (this.wave?.root.isConnected) this.wave.root.remove(); return; }
    if (!this.wave) {
      this.wave = new WaveView(this.app, { ruler: true, zoom: true, bins: 4000 });
      this.wave.onExport = (w) => openRegionDialog(this.app, w.id, w.sel);
    }
    if (!this.wave.root.isConnected) this.stage.append(this.wave.root);
    if (this.wave.id !== id) this.wave.load(id, 0);
    else this.wave.draw();
  }

  /** Videos the browser can play (MP4/H.264, WebM, Ogg; most MOV) play right here. */
  showVideo(id) {
    this.isVideo = id != null;
    this.root.classList.toggle('mode-video', this.isVideo);
    if (!this.video) {
      if (!this.isVideo) return;
      this.video = el('video', { class: 'v-video', controls: true, playsinline: true, preload: 'metadata' });
      for (const ev of ['pointerdown', 'wheel', 'dblclick']) this.video.addEventListener(ev, (e) => e.stopPropagation());
      this.video.addEventListener('error', () => { if (this.isVideo && this.video.getAttribute('src')) this.app.toast('This video can\'t play in the browser - press O to open it in your player', 4000); });
      this.stage.append(this.video);
    }
    if (!this.isVideo) { this.video.pause(); this.video.removeAttribute('src'); this.video.load(); return; }
    this.app.stopAudio?.();
    this.video.src = `/f/${id}`;
    this.video.play().catch(() => {});
  }

  /** Progress over the stage: text + bar (fraction 0..1, or null for "working…"). Null text hides it. */
  setLoading(text, frac) {
    this.loading.classList.toggle('on', !!text);
    if (!text) return;
    this.loading.firstChild.textContent = text;
    const bar = this.loading.lastChild;
    bar.classList.toggle('busy', frac == null);
    bar.firstChild.style.width = frac == null ? '' : `${Math.round(frac * 100)}%`;
  }

  /** 3D models: the rendered thumbnail shows until the geometry has loaded, then the live view. */
  show3d(id) {
    this.is3d = id != null;
    this.root.classList.toggle('mode3d', this.is3d);
    this.root.classList.remove('mesh-ready');
    if (!this.is3d) { this.mv?.clear(); this.setLoading(null); return; }
    if (!this.mv) {
      this.mv = new MeshView(this.stage);
      this.mvBox = this.mv.controls();
      this.root.append(this.mvBox);
    }
    const blend = /^blend\d?$/.test(extOf(this.items.n[this.i]));
    const mine = () => this.isOpen && this.items.ids[this.i] === id;
    this.setLoading(blend ? 'Converting with Blender…' : 'Reading the 3D model…', null);
    this.mv.load(id, (got, total) => {
      if (!mine()) return;
      if (!got) this.setLoading(blend ? 'Converting with Blender… (the first time takes a while)' : 'Reading the 3D model…', null);
      else this.setLoading(`Loading 3D model… ${total ? Math.round((100 * got) / total) + '%' : fmtBytes(got)}`, total ? got / total : null);
    }).then((head) => {
      if (head && mine()) {
        this.setLoading(null);
        this.root.classList.add('mesh-ready');
        this.zoomLbl.textContent = `${fmtNum(head.triangles)} triangles${head.thinned ? ' (thinned for the view)' : ''}`;
        this.mv.onChange();
      }
    }).catch((e) => {
      if (mine()) { this.setLoading(null); this.root.classList.remove('mode3d'); this.app.toast(`3D view: ${e.message}`, 5000); }
    });
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
    const top = this.root.classList.contains('has-pages') ? 36 : 0;   // room for the page bar
    this.s = Math.min((r.width - 40) / this.w, (r.height - 40 - top) / this.h, maxUp);
    this.tx = (r.width - this.w * this.s) / 2;
    this.ty = top + (r.height - top - this.h * this.s) / 2;
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
    for (const node of [this.lit && this.litOn && this.lit.canvas, this.ink && this.inksOn && this.ink.canvas, this.boxes?.root]) {
      if (!node) continue;
      const c = node.style;
      c.width = im.style.width; c.height = im.style.height; c.transform = im.style.transform;
    }
    if (!this.root.classList.contains('mesh-ready')) this.zoomLbl.textContent = `${Math.round(this.s * 100)}%`;
    if (this.pdf) this.sharpenPage();
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
    if (this.isVideo && k === ' ' && !this.quick) { if (this.video.paused) this.video.play(); else this.video.pause(); return true; }
    if (this.isSound && (k === 'e' || k === 'E') && this.wave?.sel) { openRegionDialog(this.app, this.wave.id, this.wave.sel); return true; }
    if (this.isSound && k === ' ' && !this.quick && this.wave?.sel && (this.wave.freshSel || !(this.app.playingId === this.items.ids[this.i] && !this.app.audio.paused))) {
      this.wave.playSelection();
      return true;
    }
    if (this.isSound && k === ' ' && !this.quick) {
      const a = this.app.audio, id = this.items.ids[this.i];
      if (this.app.playingId === id && !a.paused) a.pause();
      else if (this.app.playingId === id) a.play().catch(() => {});
      else this.app.playAt(id, 0);
      this.wave?.loop();
      return true;
    }
    if (this.isVideo && e.shiftKey && (k === 'ArrowLeft' || k === 'ArrowRight')) { this.video.currentTime += k === 'ArrowLeft' ? -5 : 5; return true; }
    if (k === ' ' && !e.ctrlKey) { if (!e.repeat) this.close(); return true; }
    if (this.pdf?.count > 1 && (k === ']' || k === 'PageDown')) { this.gotoPage(this.page + 1); return true; }
    if (this.pdf?.count > 1 && (k === '[' || k === 'PageUp')) { this.gotoPage(this.page - 1); return true; }
    if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown') { this.step(1); return true; }
    if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') { this.step(-1); return true; }
    if (k === 'Home') { this.show(0); return true; }
    if (k === 'End') { this.show(this.items.ids.length - 1); return true; }
    if (k === 'x' || k === 'X' || (k === ' ' && e.ctrlKey)) { this.toggleSel(); return true; }
    if (k === 'b' || k === 'B') { this.cycleBg(); return true; }
    if ((k === 'l' || k === 'L') && this.is3d && this.mv?.n) { this.app.toast(`Light: ${this.mv.cycleLights()}`); return true; }
    if ((k === 'l' || k === 'L') && !this.is3d && !this.isVideo && !this.isSound) { this.toggleLit(); return true; }
    if ((k === 'i' || k === 'I') && !this.is3d && !this.isVideo && !this.isSound) { this.toggleInks(); return true; }
    if (k === 'n' || k === 'N') { this.togglePixel(); return true; }
    const dk = e.ctrlKey || e.metaKey || e.altKey ? null : digitKey(e);
    if (k === '0' || dk === 0) { if (this.is3d) this.mv?.reset(); else this.fitView(); return true; }   // (0 is "fit" here; Q repeats the last tag)
    if (k === 'z' || k === 'Z') { if (this.fitted) this.zoomTo(1); else this.fitView(); return true; }
    if (dk) {  // tag hotkeys apply to the shown image only
      const t = this.app.tags.byHotkey.get(dk);
      if (t) this.app.applyTag(t.id, { targets: [this.items.ids[this.i]] }).then(() => this.paintTags());
      else this.app.toast(`No tag on hotkey ${dk} yet`);
      return true;
    }
    if (k === 'q' || k === 'Q') { this.app.applyLastTag([this.items.ids[this.i]])?.then(() => this.paintTags()); return true; }
    if (k === 't' || k === 'T') { this.app.tags.quickTag([this.items.ids[this.i]]); return true; }
    if (k === '+' || k === '=') { this.zoomTo(this.s * 1.25); return true; }
    if (k === '-') { this.zoomTo(this.s / 1.25); return true; }
    return false;
  }
}
