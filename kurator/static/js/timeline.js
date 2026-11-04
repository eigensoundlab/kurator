// Timeline scrubber: a strip beside the grid when it is sorted by date. Years and months are
// marked where their items start, a bar shows how many items each month holds, and dragging
// along the strip jumps through the list.

import { el } from './util.js';

const WIDTH = 64;
const monthName = new Intl.DateTimeFormat(undefined, { month: 'short' });
const monthYear = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });

export class Timeline {
  constructor(grid, app) {
    this.grid = grid;
    this.app = app;
    this.wrap = grid.el.parentElement;
    this.canvas = el('canvas', { class: 'timeline', title: '' });
    this.tip = el('div', { class: 'timeline-tip' });
    this.wrap.append(this.canvas, this.tip);
    this.segs = [];
    this.shown = false;
    this.dragging = false;
    new ResizeObserver(() => this.draw()).observe(this.canvas);
    grid.el.addEventListener('scroll', () => this.draw(), { passive: true });
    this.canvas.addEventListener('pointerdown', (e) => {
      this.dragging = true;
      this.canvas.setPointerCapture(e.pointerId);
      this.jump(e);
    });
    this.canvas.addEventListener('pointermove', (e) => { this.hover(e); if (this.dragging) this.jump(e); });
    this.canvas.addEventListener('pointerup', () => { this.dragging = false; });
    this.canvas.addEventListener('pointerleave', () => { if (!this.dragging) this.tip.classList.remove('show'); });
  }

  /** Rebuild month segments for the current list (call after the grid got new items). */
  update() {
    const it = this.grid.items, q = this.app.query;
    const n = it ? it.ids.length : 0;
    const want = q.sort === 'date' && !q.similar && !this.app.aiView && n > 0
      && n > this.grid.cols * this.grid.rowsPerPage() * 2;
    this.segs = [];
    if (want) {
      let cur = null;
      for (let i = 0; i < n; i++) {
        const ts = it.d[i];
        let key = -1, y = 0, m = 0;
        if (ts) { const dt = new Date(ts * 1000); y = dt.getFullYear(); m = dt.getMonth(); key = y * 12 + m; }
        if (!cur || cur.key !== key) { cur = { key, y, m, start: i, count: 0 }; this.segs.push(cur); }
        cur.count++;
      }
    }
    this.shown = want && this.segs.length > 1;
    if (this.wrap.classList.contains('has-timeline') !== this.shown) this.wrap.classList.toggle('has-timeline', this.shown);
    this.draw();
  }

  draw() {
    if (!this.shown) return;
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    if (!W || !H) return;
    if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const n = this.grid.count, top = 6, h = H - 12;
    const yOf = (i) => top + (i / n) * h;
    const css = getComputedStyle(document.documentElement);
    const fg = css.getPropertyValue('--fg').trim() || '#ddd', muted = css.getPropertyValue('--muted').trim() || '#888';
    const accent = css.getPropertyValue('--accent').trim() || '#4c8dff';
    const max = Math.max(...this.segs.map((s) => s.count));
    // density bars
    for (const s of this.segs) {
      const y0 = yOf(s.start), y1 = yOf(s.start + s.count);
      const len = 4 + (W - 30) * Math.sqrt(s.count / max);
      g.fillStyle = s.key < 0 ? '#8884' : '#8886';
      g.fillRect(W - 4 - len, y0, len, Math.max(1, y1 - y0 - 0.5));
    }
    // year labels, then month ticks where there is room
    g.font = '11px system-ui, sans-serif';
    g.textBaseline = 'top';
    let lastLabel = -100, lastYear = null;
    for (const s of this.segs) {
      const y = yOf(s.start);
      if (s.key >= 0 && s.y !== lastYear) {
        lastYear = s.y;
        g.fillStyle = fg;
        g.fillRect(2, y, W - 6, 1);
        if (y - lastLabel > 13) { g.fillText(String(s.y), 4, y + 1); lastLabel = y; }
      } else if (s.key >= 0 && y - lastLabel > 26 && yOf(s.start + s.count) - y > 3) {
        g.fillStyle = muted;
        g.fillText(monthName.format(new Date(s.y, s.m, 1)), 10, y + 1);
        lastLabel = y;
      } else if (s.key < 0 && y - lastLabel > 13) {
        g.fillStyle = muted;
        g.fillText('no date', 4, y + 1);
        lastLabel = y;
      }
    }
    // the part of the list on screen
    const el2 = this.grid.el, total = el2.scrollHeight || 1;
    const v0 = top + (el2.scrollTop / total) * h, v1 = top + ((el2.scrollTop + el2.clientHeight) / total) * h;
    g.fillStyle = accent;
    g.globalAlpha = 0.25;
    g.fillRect(0, v0, W, Math.max(3, v1 - v0));
    g.globalAlpha = 1;
    g.fillRect(0, v0, 3, Math.max(3, v1 - v0));
  }

  at(e) {
    const r = this.canvas.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientY - r.top - 6) / (r.height - 12)));
    const i = Math.min(this.grid.count - 1, Math.floor(f * this.grid.count));
    let s = this.segs[0];
    for (const x of this.segs) { if (x.start > i) break; s = x; }
    return { i, s, y: e.clientY - r.top };
  }

  hover(e) {
    if (!this.shown) return;
    const { s, y } = this.at(e);
    this.tip.textContent = `${s.key < 0 ? 'No date' : monthYear.format(new Date(s.y, s.m, 1))} · ${s.count.toLocaleString()} item${s.count === 1 ? '' : 's'}`;
    this.tip.style.top = `${Math.max(0, y - 10)}px`;
    this.tip.classList.add('show');
  }

  jump(e) {
    if (!this.shown) return;
    const { i, s } = this.at(e);
    // a click lands on the start of the month; dragging moves smoothly
    this.grid.scrollToIndex(this.dragging && e.type === 'pointermove' ? i : s.start, 'start');
    this.hover(e);
  }
}
