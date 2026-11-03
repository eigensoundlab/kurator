// Projects as the top filter.
//
// Pick a project (toolbar switcher, or click it in the sidebar) and the whole app works inside it:
// folders, tags, types and search filter the project's files, with counts for the project.
// Two modes:
//   In project        - only the project's files
//   Add from library  - the whole library, the project's files marked ◆; add with + / drag, remove with −
// A project can include things automatically (live): tags, folders and other projects.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtBytes, fmtNum, prefs, setKids } from './util.js';

export class Projects {
  constructor(app) {
    this.app = app;
    this.list = [];
    this.active = prefs.get('project.id', null);
    this.mode = prefs.get('project.mode', 'in');
    this.button = el('button', { class: 'btn projbtn', title: 'Work inside a project (everything else filters within it)',
      onclick: (e) => { e.stopPropagation(); this.toggleMenu(); } });
    this.menu = el('div', { class: 'projmenu' });
    this.wrap = el('div', { class: 'projswitch' }, this.button, this.menu);
    document.addEventListener('pointerdown', (e) => { if (!this.wrap.contains(e.target)) this.menu.classList.remove('open'); }, true);
    this.applyQuery();
    this.paint();
  }

  get current() { return this.list.find((p) => p.id === this.active) || null; }

  async refresh() {
    try { this.list = await api.projects(); } catch { return; }
    if (this.active && !this.current) { this.leave(); return; }
    this.paint();
  }

  applyQuery() {
    const q = this.app.query;
    q.project = this.active && this.mode === 'in' ? this.active : '';
    q.member_of = this.active && this.mode === 'add' ? this.active : '';
    document.body.classList.toggle('in-project', !!this.active);
  }

  paint() {
    const p = this.current;
    setKids(this.button,
      el('span', { class: 'pdot', style: { background: p?.color || 'transparent', borderColor: p ? p.color : 'var(--muted)' } }),
      el('span', { text: p ? p.name : 'All projects' }), el('span', { class: 'muted', text: ' ▾' }));
    this.button.classList.toggle('on', !!p);
  }

  toggleMenu() {
    if (this.menu.classList.toggle('open')) this.renderMenu();
  }

  renderMenu() {
    const rows = this.list.filter((p) => !p.name.includes('/') && (!p.archived || p.id === this.active));
    setKids(this.menu,
      el('button', { class: 'pm-row' + (this.active ? '' : ' on'), onclick: () => { this.menu.classList.remove('open'); this.leave(); } },
        el('span', { class: 'pdot' }), el('span', { class: 'grow', text: 'Whole library (no project)' })),
      rows.length ? el('div', { class: 'pm-sep' }) : null,
      rows.map((p) => el('button', { class: 'pm-row' + (p.id === this.active ? ' on' : ''), title: this.describe(p),
        onclick: () => { this.menu.classList.remove('open'); this.activate(p.id); } },
        el('span', { class: 'pdot', style: { background: p.color } }), el('span', { class: 'grow', text: p.name }),
        p.sources.length ? el('span', { class: 'muted small', text: '⟲ ', title: 'Includes things automatically' }) : null,
        el('span', { class: 'ct', text: fmtNum(p.count) }))),
      el('div', { class: 'pm-sep' }),
      el('button', { class: 'pm-row', onclick: () => { this.menu.classList.remove('open'); this.edit(null); } }, el('span', { class: 'pdot' }), el('span', { text: '+ New project…' })),
      this.current ? el('button', { class: 'pm-row', onclick: () => { this.menu.classList.remove('open'); this.edit(this.active); } },
        el('span', { class: 'pdot' }), el('span', { text: `Edit "${this.current.name}"…` })) : null);
  }

  describe(p) {
    const s = p.sources.map((x) => `${x.kind === 'folder' ? 'folder' : x.kind}: ${x.label}`).join('\n');
    return `${p.name}: ${fmtNum(p.count)} files (${fmtNum(p.by_hand)} added by hand)${s ? '\nIncludes automatically:\n' + s : ''}`;
  }

  /** Make a project the top filter. */
  activate(id, { mode = 'in', keepFilters = false } = {}) {
    if (this.active === id && this.mode === mode) return;
    this.active = id;
    this.mode = mode;
    prefs.set('project.id', id);
    prefs.set('project.mode', mode);
    const q = this.app.query;
    if (!keepFilters) {
      q.tag = ''; q.exclude = ''; q.missing = ''; q.untagged = false;   // start from the whole project
      if (q.folder) this.app.leaveFolder();
    }
    this.changed();
  }

  leave() {
    this.active = null;
    prefs.set('project.id', null);
    this.changed();
  }

  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    prefs.set('project.mode', mode);
    this.changed();
  }

  changed() {
    this.applyQuery();
    this.paint();
    this.app.sidebar.refresh();
    this.app.tags.refresh();
    this.app.onFilterChanged();
  }

  /** Path-bar part: ◆ Game A  [In project | Add from library]  Edit  × */
  pathBar() {
    const p = this.current;
    if (!p) return [];
    const seg = (mode, label, title) => el('button', { class: 'seg' + (this.mode === mode ? ' on' : ''), text: label, title, onclick: () => this.setMode(mode) });
    return [el('span', { class: 'projchip', style: { '--c': p.color }, 'data-tag-id': p.id, title: this.describe(p) + '\nDrop images here to add them' },
      el('span', { class: 'pdot', style: { background: p.color } }), el('span', { text: p.name }),
      el('span', { class: 'x', text: '×', title: 'Leave the project (whole library)', onclick: (e) => { e.stopPropagation(); this.leave(); } })),
    el('span', { class: 'segs' }, seg('in', 'In project', 'Only the files of this project'),
      seg('add', 'Add from library', 'The whole library; files already in the project are marked ◆. Select and press + (or drag onto the project) to add.')),
    el('button', { class: 'btn small', text: 'Edit…', title: 'Name, colour, and what the project includes automatically', onclick: () => this.edit(p.id) }),
    el('button', { class: 'btn small', text: 'Export…', title: 'Copy the project into a game folder (Unity, Godot, Unreal…), with the textures its models use', onclick: () => this.exportDialog(p.id) })];
  }

  /** + / − keys and the path-bar button. */
  async addSelection(op = 'add') {
    const p = this.current;
    if (!p) return;
    const ids = this.app.tagTargets();
    if (!ids.length) { this.app.toast('Select images first'); return; }
    const r = await this.app.applyTag(p.id, { op, targets: ids, quiet: true });
    if (!r) return;
    const items = this.app.items;
    if (items.m) {
      const idx = this.app.idIndex;
      for (const id of op === 'add' ? r.added : r.removed) {
        const i = idx.get(id);
        if (i != null) items.m[i] = op === 'add' ? 1 : 0;
      }
      this.app.grid.repaint();
    }
    if (op === 'add') {
      this.app.toast(r.added.length ? `Added ${fmtNum(r.added.length)} to ${p.name} · Ctrl+Z to undo` : `Already in ${p.name}`);
    } else {
      const auto = ids.length - r.removed.length;
      this.app.toast(r.removed.length
        ? `Removed ${fmtNum(r.removed.length)} from ${p.name}${auto ? ` · ${fmtNum(auto)} stay: included automatically (Edit…)` : ''} · R refreshes`
        : `${fmtNum(ids.length)} are included automatically - change that with Edit… on the project`, 3500);
    }
    this.refresh();
  }

  /** Copy the project (or the selection) into a game project's folder layout. Never moves anything. */
  async exportDialog(id) {
    const app = this.app;
    const p = this.list.find((x) => x.id === id);
    if (!p) return;
    let presets = {};
    try { presets = await api.exportPresets(); } catch (e) { app.toast(e.message); return; }
    const saved = prefs.get(`export.${id}`, {});
    const dest = el('input', { type: 'text', value: saved.dest || '', placeholder: 'Your game project folder, e.g. D:\\Games\\MyGame', spellcheck: 'false' });
    const preset = el('select', {}, Object.entries(presets).map(([k, label]) => el('option', { value: k, text: label })));
    preset.value = saved.preset || 'unity';
    const bySubj = el('input', { type: 'checkbox' }); bySubj.checked = !!saved.by_subject;
    const comps = el('input', { type: 'checkbox' }); comps.checked = saved.companions !== false;
    const nSel = app.selected.size;
    const onlySel = el('input', { type: 'checkbox', disabled: !nSel });
    const out = el('div', { class: 'move-preview' });
    const body = () => ({ project: id, dest: dest.value.trim(), preset: preset.value, by_subject: bySubj.checked,
      companions: comps.checked, ids: onlySel.checked ? [...app.selected] : undefined });
    const remember = () => prefs.set(`export.${id}`, { dest: dest.value.trim(), preset: preset.value, by_subject: bySubj.checked, companions: comps.checked });
    let planned = null;
    const preview = async () => {
      setKids(out, el('div', { class: 'p-hint small', text: 'Planning…' }));
      try { planned = await api.exportPlan(body()); } catch (e) { planned = null; setKids(out, el('div', { class: 'err', text: e.message })); d.buttons.Export.disabled = true; return; }
      const c = planned.counts;
      const label = { image: 'images', texture: 'textures', model: 'models', companion: 'textures/materials with models', audio: 'sounds', video: 'videos', source: 'work files', docs: 'documents' };
      setKids(out,
        el('div', { class: 'move-sum' }, el('b', { text: planned.copy ? `${fmtNum(planned.copy)} files to copy (${fmtBytes(planned.bytes)})` : 'Everything is already exported ✓' }),
          el('div', { class: 'muted small', text: Object.entries(c).map(([k, v]) => `${fmtNum(v)} ${label[k] || k}`).join(' · ') + (planned.unchanged ? ` · ${fmtNum(planned.unchanged)} unchanged` : '') }),
          el('div', { class: 'muted small', text: `into ${planned.dest}${planned.dest.includes('\\') ? '\\' : '/'}${planned.root}` })),
        el('div', { class: 'mv-list' }, planned.examples.slice(0, 200).map((e) => el('div', { class: 'mv' + (e.skip ? ' muted' : '') }, el('span', { class: 'to', text: `${e.skip ? '=' : '+'} ${e.rel}` })))));
      d.buttons.Export.disabled = !planned.copy;
    };
    for (const x of [dest, preset, bySubj, comps, onlySel]) x.addEventListener('change', () => { d.buttons.Export.disabled = true; setKids(out, el('div', { class: 'p-hint small', text: 'Press Preview to see what will be copied.' })); });
    const d = openDialog({
      title: `Export "${p.name}"`, width: 720,
      body: el('div', { class: 'projdlg' },
        el('div', { class: 'p-hint small', text: 'Copies the project\'s files into a game project, sorted by type. Models get the materials and textures they reference, at the same relative paths, so they import with their textures. Run it again later: only new or changed files are copied. Nothing in your library is moved or changed.' }),
        el('label', {}, 'Destination folder', dest),
        el('label', {}, 'Layout', preset),
        el('label', { class: 'chk' }, bySubj, 'Subfolders by subject (e.g. Sprites/Creatures/Dragons)'),
        el('label', { class: 'chk' }, comps, 'Bring the textures and materials that models use'),
        el('label', { class: 'chk' }, onlySel, nSel ? `Only the ${fmtNum(nSel)} selected files` : 'Only the selected files (select some first)'),
        out),
      buttons: [{ label: 'Close' }, { label: 'Preview', onClick: () => { remember(); preview(); } },
        { label: 'Export', primary: true, disabled: true, onClick: async () => {
          remember();
          try { await api.exportRun(body()); } catch (e) { setKids(out, el('div', { class: 'err', text: e.message })); return; }
          d.buttons.Export.disabled = true;
          const bar = el('div', { class: 'pbar' }, el('span'));
          const line = el('div', { class: 'small' });
          setKids(out, bar, line);
          const tick = async () => {
            let st;
            try { st = await api.exportJob(); } catch { return; }
            bar.firstChild.style.width = `${st.total ? Math.round((100 * st.done) / st.total) : 100}%`;
            line.textContent = `${fmtNum(st.done)} / ${fmtNum(st.total)} files · ${fmtBytes(st.bytes_done)}`;
            if (st.running) { setTimeout(tick, 400); return; }
            const r = st.result || {};
            setKids(out, el('div', { class: r.failed ? 'err' : 'ok', text: `${r.cancelled ? 'Stopped' : 'Done'}: ${fmtNum(r.copied)} copied, ${fmtNum(r.unchanged)} unchanged${r.failed ? `, ${r.failed} failed` : ''}` }),
              el('div', { class: 'small' }, 'Folder: ', el('code', { text: r.folder || '' }),
                el('button', { class: 'btn small', text: 'Copy path', onclick: () => navigator.clipboard?.writeText(r.folder || '') })),
              st.errors?.length ? el('div', { class: 'err small', text: st.errors.slice(0, 5).join(' · ') }) : null);
          };
          tick();
        } }],
    });
    if (dest.value) preview();
    return d;
  }

  /** New project, or edit one: name, colour, and live sources (tags, folders, other projects). */
  async edit(id) {
    const app = this.app;
    await this.refresh();
    const p = id ? this.list.find((x) => x.id === id) : null;
    const tag = id ? app.tags.byId.get(id) : null;
    const name = el('input', { type: 'text', value: p?.name || '', placeholder: 'e.g. Game A', spellcheck: 'false' });
    const color = el('input', { type: 'color', value: p?.color || '#0090ff' });
    let sources = (p?.sources || []).map((s) => ({ kind: s.kind, ref: s.ref, label: s.label, count: s.count }));
    const list = el('div', { class: 'srclist' });
    const msg = el('div');
    const label = (s) => s.kind === 'folder' ? s.ref : (app.tags.byId.get(+s.ref)?.name || s.label || '?');
    const icon = { tag: '#', folder: '📁', project: '◆' };
    const render = () => setKids(list, sources.length ? sources.map((s, i) => el('div', { class: 'srcrow' },
      el('span', { class: 'srcicon', text: icon[s.kind] }), el('span', { class: 'grow', text: label(s), title: label(s) }),
      s.count != null ? el('span', { class: 'muted small', text: `${fmtNum(s.count)} files` }) : null,
      el('button', { class: 'btn small', text: '×', title: 'Remove', onclick: () => { sources.splice(i, 1); render(); } })))
      : el('div', { class: 'muted small', text: 'Nothing yet - files are only in the project when you add them (drag, + key, hotkey, tag bar).' }));
    render();
    const tagSel = el('select', {}, el('option', { value: '', text: 'Add a tag…' }),
      app.tags.facets.filter((f) => f.key !== 'project').map((f) => el('optgroup', { label: f.label },
        app.tags.list.filter((t) => t.facet === f.key).map((t) => el('option', { value: String(t.id), text: `${t.name} (${fmtNum(t.count)})` })))));
    tagSel.onchange = () => { if (tagSel.value) { sources.push({ kind: 'tag', ref: +tagSel.value }); tagSel.value = ''; render(); } };
    const projSel = el('select', {}, el('option', { value: '', text: 'Add another project…' }),
      this.list.filter((x) => x.id !== id).map((x) => el('option', { value: String(x.id), text: `${x.name} (${fmtNum(x.count)})` })));
    projSel.onchange = () => { if (projSel.value) { sources.push({ kind: 'project', ref: +projSel.value }); projSel.value = ''; render(); } };
    const folder = el('input', { type: 'text', placeholder: 'Folder path, e.g. D:\\Game A\\Concepts', spellcheck: 'false', value: app.query.folder || '' });
    const addFolder = el('button', { class: 'btn small', text: '+ Folder', onclick: () => {
      const v = folder.value.trim();
      if (v) { sources.push({ kind: 'folder', ref: v }); folder.value = ''; render(); }
    } });
    const body = el('div', { class: 'projdlg' },
      el('div', { class: 'row' }, el('label', { class: 'grow' }, 'Name', name), el('label', {}, 'Colour', color)),
      el('h4', { text: 'Includes automatically' }),
      el('div', { class: 'p-hint small', text: 'Live: files with these tags, in these folders or in these projects are always part of the project - new ones too. Files you add by hand (drag, + key, hotkey) are in it as well.' }),
      list,
      el('div', { class: 'row' }, tagSel, projSel),
      el('div', { class: 'row' }, folder, addFolder),
      p ? el('div', { class: 'muted small', text: `${fmtNum(p.by_hand)} files added by hand · ${fmtNum(p.count)} in total` }) : null,
      msg);
    const d = openDialog({
      title: p ? `Project "${p.name}"` : 'New project', body, width: 620,
      buttons: [{ label: 'Cancel' }, { label: p ? 'Save' : 'Create', primary: true, onClick: async (close) => {
        try {
          let pid = id;
          const nm = name.value.trim();
          if (!nm) throw new Error('Give the project a name');
          if (!pid) pid = (await api.createTag(nm, { facet: 'project', color: color.value, auto_hotkey: true })).id;
          else if (nm !== tag?.name || color.value !== tag?.color) await api.updateTag(pid, { name: nm, color: color.value });
          await api.setProjectSources(pid, sources.map((s) => ({ kind: s.kind, ref: s.ref })));
          close();
          await app.tags.refresh();
          await this.refresh();
          if (!id) this.activate(pid); else this.changed();
        } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
      } }],
    });
    return d;
  }
}
