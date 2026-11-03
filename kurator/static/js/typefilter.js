// Type filter: groups (Images, Work files, Vector, Documents, Game textures, Audio) with
// per-extension checkboxes and presets. The choice is remembered.

import { el, prefs, setKids } from './util.js';

const PRESETS = [
  ['All files', null],
  ['Finished images', ['jpg', 'jpeg', 'jfif', 'jpe', 'png', 'webp']],
  ['All images', 'image'],
  ['Work files', 'work'],
  ['Vector', 'vector'],
  ['Audio', 'audio'],
];

export class TypeFilter {
  constructor(app) {
    this.app = app;
    this.types = { groups: [] };
    this.selected = new Set(prefs.get('types', []));   // empty = everything
    this.button = el('button', { class: 'btn', title: 'Show only some file types', onclick: (e) => { e.stopPropagation(); this.toggle(); } });
    this.panel = el('div', { class: 'typepanel' });
    this.wrap = el('div', { class: 'typefilter' }, this.button, this.panel);
    document.addEventListener('pointerdown', (e) => { if (!this.wrap.contains(e.target)) this.close(); }, true);
    this.panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); this.close(); this.button.focus(); } });
    this.paintButton();
  }

  setTypes(types) {
    if (!types) return;
    this.types = types;
    const known = new Set(types.groups.flatMap((g) => g.exts));
    for (const e of [...this.selected]) if (!known.has(e)) this.selected.delete(e);
    this.paintButton();
  }

  /** Value for the items API (comma-separated extensions) or '' for everything. */
  get param() { return [...this.selected].join(','); }

  allExts() { return this.types.groups.flatMap((g) => g.exts); }

  paintButton() {
    const n = this.selected.size;
    let label = 'All types';
    if (n) {
      const full = this.types.groups.filter((g) => g.exts.every((e) => this.selected.has(e)));
      const covered = new Set(full.flatMap((g) => g.exts));
      const rest = [...this.selected].filter((e) => !covered.has(e));
      label = [...full.map((g) => g.label.split(' ')[0]), ...rest.slice(0, 4).map((e) => e.toUpperCase())].join(', ') + (rest.length > 4 ? '…' : '');
    }
    this.button.textContent = `${label} ▾`;
    this.button.classList.toggle('on', n > 0);
  }

  toggle() { this.panel.classList.contains('open') ? this.close() : this.open(); }
  close() { this.panel.classList.remove('open'); }

  open() {
    this.render();
    this.panel.classList.add('open');
  }

  apply() {
    prefs.set('types', [...this.selected]);
    this.paintButton();
    this.app.query.ext = this.param;
    this.app.reload({ keepScroll: false });
  }

  render() {
    const presets = el('div', { class: 'tp-presets' }, PRESETS.map(([label, what]) =>
      el('button', { class: 'chip', type: 'button', text: label, onclick: () => {
        this.selected = new Set(what === null ? [] : Array.isArray(what) ? what : (this.types.groups.find((g) => g.key === what)?.exts || []));
        this.render(); this.apply();
      } })));
    const groups = this.types.groups.map((g) => {
      const on = g.exts.filter((e) => this.selected.has(e)).length;
      const box = el('input', { type: 'checkbox' });
      box.checked = on === g.exts.length;
      box.indeterminate = on > 0 && on < g.exts.length;
      box.onchange = () => {
        for (const e of g.exts) { if (box.checked) this.selected.add(e); else this.selected.delete(e); }
        this.render(); this.apply();
      };
      const exts = g.exts.map((e) => {
        const c = el('input', { type: 'checkbox' });
        c.checked = this.selected.has(e);
        c.onchange = () => { if (c.checked) this.selected.add(e); else this.selected.delete(e); this.render(); this.apply(); };
        return el('label', { class: 'tp-ext' }, c, e);
      });
      return el('div', { class: 'tp-group' }, el('label', { class: 'tp-head' }, box, g.label), el('div', { class: 'tp-exts' }, exts));
    });
    setKids(this.panel, presets, groups,
      el('div', { class: 'p-hint small', text: 'Nothing ticked = all types. Your choice is remembered.' }));
  }
}
