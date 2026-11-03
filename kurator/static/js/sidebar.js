// Left sidebar: Pinned folders, Library tree, Tags, and a file-system browser for any folder.

import { api } from './api.js';
import { el, fmtNum, prefs } from './util.js';

function section(key, title, ...extra) {
  const body = el('div', { class: 'sec-body' });
  const head = el('div', { class: 'side-title' }, el('span', { class: 'chev' }), el('span', { text: title }), ...extra);
  const box = el('div', { class: 'side-sec' }, head, body);
  const collapsed = prefs.get('side.collapsed', {});
  box.classList.toggle('collapsed', !!collapsed[key]);
  head.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    box.classList.toggle('collapsed');
    const c = prefs.get('side.collapsed', {});
    c[key] = box.classList.contains('collapsed');
    prefs.set('side.collapsed', c);
  });
  return { box, body };
}

export class Sidebar {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.expanded = new Set(prefs.get('tree.expanded', []));
    this.pins = [];
    this.nodes = [];
    this.roots = [];

    this.pinSec = section('pins', 'Pinned');
    this.libSec = section('lib', 'Library');
    this.tagSec = section('tags', 'Tags', el('button', { class: 'mini', title: 'New tag', text: '+', onclick: () => app.tags.editDialog(null) }));
    this.fsSec = section('fs', 'Browse');

    this.input = el('input', { type: 'text', placeholder: 'Add library folder, e.g. D:\\Art', spellcheck: 'false' });
    this.msg = el('div', { class: 'side-msg' });
    this.libTree = el('div', { class: 'tree' });
    this.libSec.body.append(this.libTree,
      el('form', { class: 'add-root', onsubmit: (e) => { e.preventDefault(); this.addRoot(); } },
        this.input, el('button', { class: 'btn', type: 'submit', text: 'Add' })), this.msg);

    this.fsTree = el('div', { class: 'tree' });
    this.fsInput = el('input', { type: 'text', placeholder: 'Open any folder path…', spellcheck: 'false' });
    this.fsSec.body.append(
      el('form', { class: 'add-root', onsubmit: (e) => { e.preventDefault(); if (this.fsInput.value.trim()) app.openPath(this.fsInput.value.trim()); } },
        this.fsInput, el('button', { class: 'btn', type: 'submit', text: 'Open' })),
      this.fsTree);
    this.fsChildren = new Map();   // path -> [{name, path}]
    this.fsOpen = new Set();

    this.root.append(this.pinSec.box, this.libSec.box, this.tagSec.box, this.fsSec.box);
    this.loadPins();
    this.loadPlaces();
  }

  get tagContainer() { return this.tagSec.body; }

  // ---------------------------------------------------------------- pins
  async loadPins() {
    try { this.pins = await api.pins(); } catch { this.pins = []; }
    this.renderPins();
    this.app.renderPathBar();
  }

  isPinned(path) { return this.pins.includes(path); }

  async togglePin(path) {
    const next = this.isPinned(path) ? this.pins.filter((p) => p !== path) : [...this.pins, path];
    try { this.pins = await api.setPins(next); } catch (e) { this.app.toast(e.message); return; }
    this.renderPins();
    this.renderLibrary();
    this.app.renderPathBar();
  }

  renderPins() {
    const cur = this.app.query.folder;
    const rows = this.pins.map((p) => {
      const name = p.split(/[\\/]/).filter(Boolean).pop() || p;
      return el('div', { class: 'node' + (cur === p ? ' active' : ''), title: p, onclick: () => this.app.openPath(p) },
        el('span', { class: 'tw', text: '★' }), el('span', { class: 'nm', text: name }),
        el('span', { class: 'rm', title: 'Unpin', text: '×', onclick: (e) => { e.stopPropagation(); this.togglePin(p); } }));
    });
    if (!rows.length) rows.push(el('div', { class: 'p-hint small', text: 'Pin folders with ☆ for quick access.' }));
    this.pinSec.body.replaceChildren(...rows);
  }

  // ------------------------------------------------------------- library
  async refresh(roots) {
    this.roots = (roots || this.roots).filter((r) => r.kind !== 'browse');
    try { this.nodes = await api.folders(); } catch { return; }
    this.renderLibrary();
  }

  render() { this.renderLibrary(); this.renderPins(); }

  pinButton(path) {
    const on = this.isPinned(path);
    return el('span', { class: 'pin' + (on ? ' on' : ''), title: on ? 'Unpin' : 'Pin', text: on ? '★' : '☆',
      onclick: (e) => { e.stopPropagation(); this.togglePin(path); } });
  }

  renderLibrary() {
    const byParent = new Map();
    for (const n of this.nodes) {
      const k = n.parent ?? '';
      if (!byParent.has(k)) byParent.set(k, []);
      byParent.get(k).push(n);
    }
    const current = this.app.query.folder || null;
    const total = this.nodes.filter((n) => n.parent == null).reduce((a, n) => a + n.total, 0);
    const all = el('div', { class: 'node' + (current === null ? ' active' : ''), onclick: () => this.app.setFolder(null) },
      el('span', { class: 'tw' }), el('span', { class: 'nm', text: 'All library items' }), el('span', { class: 'ct', text: fmtNum(total) }));
    const build = (n, depth) => {
      const kids = byParent.get(n.path) || [];
      const open = this.expanded.has(n.path) || (n.parent == null && !this.expanded.has('!' + n.path));
      const isRoot = n.parent == null;
      const row = el('div', {
        class: 'node' + (current === n.path ? ' active' : '') + (isRoot ? ' rootnode' : ''),
        style: { paddingLeft: `${6 + depth * 14}px` }, title: n.path,
        onclick: () => this.app.setFolder(n.path),
      },
      el('span', { class: 'tw' + (kids.length ? '' : ' none'), text: kids.length ? (open ? '▾' : '▸') : '', onclick: (e) => { e.stopPropagation(); this.toggle(n, open); } }),
      el('span', { class: 'nm', text: n.name }),
      this.pinButton(n.path),
      el('span', { class: 'ct', text: fmtNum(n.total) }),
      isRoot ? el('span', { class: 'rm', title: 'Remove from library (files on disk are not touched)', text: '×', onclick: (e) => { e.stopPropagation(); this.removeRoot(n.path); } }) : null);
      const out = [row];
      if (open) for (const k of kids) out.push(...build(k, depth + 1));
      return out;
    };
    const rows = [all];
    for (const r of byParent.get('') || []) rows.push(...build(r, 0));
    if (!this.nodes.length) rows.push(el('div', { class: 'p-hint small', text: 'Add a folder with your images below, or open any folder under Browse.' }));
    this.libTree.replaceChildren(...rows);
  }

  toggle(n, open) {
    if (n.parent == null) {
      if (open) this.expanded.add('!' + n.path); else this.expanded.delete('!' + n.path);
    } else if (open) this.expanded.delete(n.path); else this.expanded.add(n.path);
    prefs.set('tree.expanded', [...this.expanded]);
    this.renderLibrary();
  }

  async addRoot(path) {
    const p = (path ?? this.input.value).trim();
    if (!p) return;
    this.msg.textContent = '';
    try {
      const r = await api.addRoot(p);
      this.input.value = '';
      await this.app.refreshState();
      this.app.toast(`Added to library: ${r.path}`);
      return r;
    } catch (e) {
      this.msg.textContent = e.message;
      this.app.toast(e.message);
    }
  }

  async removeRoot(path) {
    const r = this.roots.find((x) => x.path === path);
    if (!r) return;
    if (!window.confirm(`Remove "${path}" from the library?\n\nOnly the catalog entry is removed - your files stay on disk. Tags are remembered and come back if you add the folder again.`)) return;
    await api.removeRoot(r.id);
    if (this.app.query.folder && (this.app.query.folder + '').startsWith(path)) this.app.setFolder(null);
    this.app.refreshState();
  }

  // ---------------------------------------------------------- browse fs
  async loadPlaces() {
    let p;
    try { p = await api.places(); } catch { return; }
    this.places = [...p.places, ...p.drives.map((d) => ({ ...d, drive: true }))];
    this.renderFs();
  }

  async toggleFs(path) {
    if (this.fsOpen.has(path)) { this.fsOpen.delete(path); this.renderFs(); return; }
    this.fsOpen.add(path);
    if (!this.fsChildren.has(path)) {
      try { this.fsChildren.set(path, (await api.listDir(path)).dirs); } catch (e) { this.app.toast(e.message); this.fsOpen.delete(path); }
    }
    this.renderFs();
  }

  renderFs() {
    if (!this.places) return;
    const cur = this.app.query.folder;
    const rows = [];
    const build = (n, depth, label) => {
      const open = this.fsOpen.has(n.path);
      const kids = this.fsChildren.get(n.path);
      rows.push(el('div', { class: 'node' + (cur === n.path ? ' active' : ''), style: { paddingLeft: `${6 + depth * 14}px` }, title: n.path,
        onclick: () => this.app.openPath(n.path) },
      el('span', { class: 'tw' + (kids && !kids.length ? ' none' : ''), text: kids && !kids.length ? '' : (open ? '▾' : '▸'), onclick: (e) => { e.stopPropagation(); this.toggleFs(n.path); } }),
      el('span', { class: 'nm' + (n.drive ? ' drive' : ''), text: label || n.name }),
      this.pinButton(n.path)));
      if (open && kids) for (const k of kids) build(k, depth + 1);
    };
    for (const p of this.places) build(p, 0, p.name);
    this.fsTree.replaceChildren(...rows);
  }
}
