// Tags in groups (Subjects, Projects, Status, Source): sidebar (filtering), tag bar (applying),
// quick-tag (T) and edit dialogs.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { debounce, el, fmtNum, prefs, setKids } from './util.js';

const DEFAULT_FACETS = [
  { key: 'subject', label: 'Subjects', single: false, none: 'Untagged' },
  { key: 'project', label: 'Projects', single: false, none: 'No project' },
  { key: 'status', label: 'Status', single: true, none: 'No status' },
  { key: 'source', label: 'Source', single: true, none: 'No source' },
];
const EXAMPLES = { subject: 'Creatures, Equipment, Icons, Characters', project: 'a game, a client, a book', status: 'concept, WIP, final', source: 'own, reference, AI-generated' };

export class Tags {
  constructor(app) {
    this.app = app;
    this.list = [];
    this.byId = new Map();
    this.byHotkey = new Map();
    this.facets = DEFAULT_FACETS;
    this.missing = {};
    this.untagged = 0;
    this.showArchived = new Set();
    this.bar = document.querySelector('#tagbar');
    this.refreshSoon = debounce(() => this.refresh(), 400);
  }

  async refresh() {
    let r;
    try { r = await api.tags(); } catch { return; }
    this.list = r.tags;
    this.untagged = r.untagged;
    this.facets = r.facets || DEFAULT_FACETS;
    this.missing = r.missing || {};
    this.byId = new Map(this.list.map((t) => [t.id, t]));
    this.byHotkey = new Map(this.list.filter((t) => t.hotkey).map((t) => [t.hotkey, t]));
    this.renderSidebar();
    this.renderBar();
    this.app.onTagsChanged();
  }

  color(id) { return this.byId.get(id)?.color || '#888'; }
  facetOf(id) { return this.byId.get(id)?.facet || 'subject'; }
  facetIndex(key) { return Math.max(0, this.facets.findIndex((f) => f.key === key)); }
  facetLabel(key) { return this.facets.find((f) => f.key === key)?.label || key; }

  /** The tag itself plus all tags below it (Creatures -> Creatures/Dragons ...). */
  descendants(id) {
    const t = this.byId.get(id);
    if (!t) return new Set([id]);
    const pre = t.name.toLowerCase() + '/';
    return new Set([id, ...this.list.filter((x) => x.name.toLowerCase().startsWith(pre)).map((x) => x.id)]);
  }

  /** Sort tag ids by group, then name - for chips. */
  sortIds(ids) {
    return [...ids].sort((a, b) => this.facetIndex(this.facetOf(a)) - this.facetIndex(this.facetOf(b))
      || (this.byId.get(a)?.name || '').localeCompare(this.byId.get(b)?.name || ''));
  }

  // ------------------------------------------------------------ sidebar
  renderSidebar() {
    const box = this.app.sidebar.tagContainer;
    const active = new Set((this.app.query.tag || '').split(',').filter(Boolean).map(Number));
    const rows = [];
    for (const f of this.facets) {
      const mine = this.list.filter((t) => t.facet === f.key);
      const live = mine.filter((t) => !t.archived);
      const archived = mine.filter((t) => t.archived);
      rows.push(el('div', { class: 'facet-head' }, el('span', { text: f.label }),
        el('button', { class: 'mini', title: `New ${f.label.toLowerCase()} tag`, text: '+', onclick: () => this.editDialog(null, f.key) })));
      if (!mine.length) {
        rows.push(el('div', { class: 'p-hint small', text: `e.g. ${EXAMPLES[f.key] || ''}` }));
        continue;
      }
      rows.push(el('div', { class: 'node' + (this.app.query.missing === f.key ? ' active' : ''), title: `Images without any ${f.label.toLowerCase()} tag`,
        onclick: () => this.app.setMissing(this.app.query.missing === f.key ? '' : f.key) },
      el('span', { class: 'tw', text: '○' }), el('span', { class: 'nm', text: f.none }), el('span', { class: 'ct', text: fmtNum(this.missing[f.key] ?? 0) })));
      const tagRow = (t) => {
        const depth = t.name.split('/').length - 1;
        return el('div', {
          class: 'node tagnode' + (active.has(t.id) ? ' active' : '') + (t.archived ? ' archived' : ''), style: { paddingLeft: `${6 + depth * 14}px` },
          title: `${t.name}\nClick: show only this tag · Ctrl+click: combine (e.g. a project + a subject)`,
          onclick: (e) => this.app.setTagFilter(t.id, e.ctrlKey || e.metaKey),
        },
        el('span', { class: 'dot', style: { background: t.color || '#888' } }),
        el('span', { class: 'nm fit', text: t.name.split('/').pop() }),
        t.hotkey ? el('span', { class: 'hk', title: `Hotkey ${t.hotkey}`, text: t.hotkey }) : null,
        el('span', { class: 'grow' }),
        el('span', { class: 'ct', text: fmtNum(t.count) }),
        el('span', { class: 'rm edit', title: 'Edit tag', text: '✎', onclick: (e) => { e.stopPropagation(); this.editDialog(t); } }));
      };
      rows.push(...live.map(tagRow));
      if (archived.length) {
        const open = this.showArchived.has(f.key);
        rows.push(el('div', { class: 'node subtle', onclick: () => { if (open) this.showArchived.delete(f.key); else this.showArchived.add(f.key); this.renderSidebar(); } },
          el('span', { class: 'tw', text: open ? '▾' : '▸' }), el('span', { class: 'nm', text: `Archived (${archived.length})` })));
        if (open) rows.push(...archived.map(tagRow));
      }
    }
    setKids(box, rows);
  }

  // ------------------------------------------------------------ tag bar
  renderBar() {
    const targets = this.app.tagTargets();
    const n = targets.length;
    const have = new Map();
    if (n && n <= 20000) {
      const all = this.app.allItems, idx = this.app.allIndex();
      for (const id of targets) {
        const ts = all.t[idx.get(id)];
        if (ts) for (const t of ts) have.set(t, (have.get(t) || 0) + 1);
      }
    }
    const chip = (t) => {
      const h = have.get(t.id) || 0;
      return el('button', { class: 'chip' + (h && h === n ? ' full' : h ? ' partial' : ''), title: `${this.facetLabel(t.facet)}: ${t.name}${t.hotkey ? ` (${t.hotkey})` : ''} - click to add/remove for ${n} item(s)`,
        style: { '--c': t.color || '#888' }, onclick: () => this.app.applyTag(t.id) },
      t.hotkey ? el('span', { class: 'hk', text: t.hotkey }) : null, el('span', { text: t.name.split('/').pop() }),
      h && h < n ? el('span', { class: 'part', text: `${h}/${n}` }) : null);
    };
    const groups = [];
    for (const f of this.facets) {
      const shown = this.list.filter((t) => t.facet === f.key && ((t.hotkey && !t.archived) || have.get(t.id)))
        .sort((a, b) => (a.hotkey || 99) - (b.hotkey || 99) || a.name.localeCompare(b.name));
      if (shown.length) groups.push(el('span', { class: 'tb-group', text: f.label }), ...shown.map(chip));
    }
    setKids(this.bar,
      el('span', { class: 'tb-label', text: n ? `Tag ${fmtNum(n)} item${n > 1 ? 's' : ''}:` : 'Select images to tag' }),
      groups,
      el('button', { class: 'chip add', title: 'Tag with any name, in any group (T)', text: '+ Tag…', onclick: () => this.quickTag() }));
  }

  // ------------------------------------------------------------ dialogs
  quickTag(targets = this.app.tagTargets()) {
    if (!targets.length) { this.app.toast('Select or focus an image first'); return; }
    const input = el('input', { type: 'text', list: 'tag-names', placeholder: 'e.g. Creatures/Dragons, or a project name', spellcheck: 'false', autocomplete: 'off' });
    const dl = el('datalist', { id: 'tag-names' }, this.list.map((t) => el('option', { value: t.name, label: this.facetLabel(t.facet) })));
    const facet = el('select', {}, this.facets.map((f) => el('option', { value: f.key, text: f.label })));
    facet.value = prefs.get('quicktag.facet', 'subject');
    const groups = this.facets.map((f) => {
      const ts = this.list.filter((t) => t.facet === f.key && !t.archived).slice(0, 30);
      return ts.length ? el('div', { class: 'chip-list' }, el('span', { class: 'tb-group', text: f.label }),
        ts.map((t) => el('button', { class: 'chip', style: { '--c': t.color }, text: t.name, type: 'button', onclick: () => { input.value = t.name; go(); } }))) : null;
    });
    let d;
    const go = async () => {
      const name = input.value.trim();
      if (!name) return;
      d.close();
      let t = this.list.find((x) => x.name.toLowerCase() === name.toLowerCase());
      try {
        if (!t) { t = await api.createTag(name, { facet: facet.value }); prefs.set('quicktag.facet', facet.value); }
      } catch (e) { this.app.toast(e.message); return; }
      await this.app.applyTag(t.id, { op: 'add', targets });
      this.refresh();
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    d = openDialog({
      title: `Tag ${fmtNum(targets.length)} item${targets.length > 1 ? 's' : ''}`,
      body: el('div', { class: 'form' }, el('div', { class: 'row' }, input, el('label', { class: 'chk small' }, 'new tags go to', facet)), dl, groups,
        el('div', { class: 'p-hint small', text: 'Type a new name to create a tag. Use / for groups inside a tag, e.g. Equipment/Weapons. Status and Source allow one tag per image.' })),
      buttons: [{ label: 'Cancel' }, { label: 'Add tag', primary: true, onClick: go }],
    });
  }

  editDialog(tag, facetKey = 'subject') {
    const isNew = !tag;
    const name = el('input', { type: 'text', value: tag?.name || '', placeholder: facetKey === 'project' ? 'e.g. Game A' : 'e.g. Creatures', spellcheck: 'false' });
    const color = el('input', { type: 'color', value: tag?.color || '#0090ff' });
    const hk = el('select', {}, el('option', { value: '', text: 'none' }),
      [1, 2, 3, 4, 5, 6, 7, 8, 9].map((k) => el('option', { value: String(k), text: `${k}${this.byHotkey.get(k) && this.byHotkey.get(k).id !== tag?.id ? ` (now ${this.byHotkey.get(k).name})` : ''}` })));
    hk.value = tag?.hotkey ? String(tag.hotkey) : (isNew ? String([1, 2, 3, 4, 5, 6, 7, 8, 9].find((k) => !this.byHotkey.has(k)) || '') : '');
    const facet = el('select', { title: 'Tag group' }, this.facets.map((f) => el('option', { value: f.key, text: f.label })));
    facet.value = tag?.facet || facetKey;
    if (tag && tag.name.includes('/')) { facet.disabled = true; facet.title = 'Sub-tags stay in the group of their top-level tag'; }
    const archived = el('input', { type: 'checkbox' });
    archived.checked = !!tag?.archived;
    const save = async (close) => {
      try {
        if (isNew) {
          await api.createTag(name.value, { color: color.value, hotkey: hk.value ? +hk.value : null, auto_hotkey: false, facet: facet.value });
        } else {
          await api.updateTag(tag.id, { name: name.value, color: color.value, hotkey: hk.value ? +hk.value : null, clear_hotkey: !hk.value,
            facet: facet.disabled ? null : facet.value, archived: archived.checked });
        }
        close();
        this.refresh();
      } catch (e) { this.app.toast(e.message); }
    };
    name.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(d.close); } });
    const buttons = [{ label: 'Cancel' }, { label: isNew ? 'Create' : 'Save', primary: true, onClick: save }];
    if (!isNew) buttons.unshift({ label: 'Delete tag', danger: true, left: true, onClick: async (close) => {
      if (!window.confirm(`Delete tag "${tag.name}"${this.list.some((x) => x.name.toLowerCase().startsWith(tag.name.toLowerCase() + '/')) ? ' and its sub-tags' : ''}?\n\nIt is removed from ${fmtNum(tag.count)} item(s). Files are not touched.`)) return;
      try { await api.deleteTag(tag.id); } catch (e) { this.app.toast(e.message); return; }
      close();
      if ((this.app.query.tag || '').split(',').includes(String(tag.id))) this.app.clearFilters();
      await this.refresh();
      this.app.reload();
    } });
    const d = openDialog({
      title: isNew ? `New tag` : 'Edit tag',
      body: el('div', { class: 'form' },
        el('label', {}, 'Name', name),
        el('div', { class: 'row' }, el('label', {}, 'Group', facet), el('label', {}, 'Colour', color), el('label', {}, 'Hotkey', hk)),
        isNew ? null : el('label', { class: 'chk' }, archived, 'Archived (finished project: hidden from the tag bar, still filterable)'),
        el('div', { class: 'p-hint small', text: 'Groups keep different kinds of tags apart: what an image shows (Subjects), where it is used (Projects), how far it is (Status), where it comes from (Source). Use / for sub-tags (Items/Weapons).' })),
      buttons,
      width: 460,
    });
  }
}
