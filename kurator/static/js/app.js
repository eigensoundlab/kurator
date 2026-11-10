// Application state and interaction model.
//
// Key rule: the SELECTION (a set of ids) is separate from the FOCUS (the item you look at).
// Scrolling, filtering, previewing, opening the viewer or switching folders never clears it.
// Arrow keys move the focus; the selection follows the focus only while it is a single item.
// Ctrl+Z undoes selection changes, tag changes and file moves (one shared history).

import { api } from './api.js';
import { openAIDialog } from './aidlg.js';
import { Compare } from './compare.js';
import { openDialog } from './dialog.js';
import { openBackupDialog } from './backupdlg.js';
import { Grid } from './grid.js';
import { LookFilter } from './lookfilter.js';
import { Timeline } from './timeline.js';
import { openDupesDialog } from './dupesdlg.js';
import { installContextMenu } from './ctxmenu.js';
import { installDrop } from './dropfiles.js';
import { openCopyDialog, openMoveDialog } from './movedlg.js';
import { Panel } from './panel.js';
import { phEnabled, phGridPref, phOnBatch, phOnSetting } from './playhead.js';
import { Projects } from './projects.js';
import { openRenameDialog } from './renamedlg.js';
import { openResampleDialog } from './resampledlg.js';
import { openRulesDialog } from './rulesdlg.js';
import { openSendMenu, openSlotEditor } from './senddlg.js';
import { openSettingsDialog } from './settingsdlg.js';
import { noteArrivals } from './sfxdlg.js';
import { openSheetsDialog } from './sheetsdlg.js';
import { dragOut, inShell } from './shell.js';
import { installErrorLog, reportError } from './errlog.js';
import { checkForUpdate, openUpdateDialog } from './updatedlg.js';
import { Sidebar } from './sidebar.js';
import { Tags } from './tags.js';
import { TypeFilter } from './typefilter.js';
import { AUDIO, MESHES, VIDEO, VIEWABLE, extOf } from './util.js';
import { $, debounce, digitKey, el, fmtNum, isTyping, prefs, setKids } from './util.js';
import { Viewer } from './viewer.js';

const EMPTY = { count: 0, ids: [], v: [], c: [], d: [], n: [], w: [], h: [], ds: [], a: [], t: [], f: [], s: [] };
const HISTORY_MAX = 100;

const SORTS = { date: 'Date', name: 'Name', folder: 'Folder', type: 'Type', size: 'File size', dimensions: 'Dimensions',
  length: 'Length', bpm: 'Tempo', loudness: 'Loudness' };
// with Playhead analysis switched on (sounds without an analysis go last)
const PH_SORTS = { harshness: 'Harshness', impulsiveness: 'Impulsiveness', ring: 'Ring time', burstiness: 'Burstiness', dynamics: 'Loudness range' };

class App {
  constructor() {
    this.query = {
      folder: prefs.get('folder', null),
      recursive: prefs.get('recursive', true),
      q: '',
      ext: prefs.get('types', []).join(','),
      sort: prefs.get('sort', 'date'),
      order: prefs.get('order', 'desc'),
      guessed: false,
      tag: '',
      untagged: false,
      missing: '',
      suggested: false,
      stacks: prefs.get('stacks', 'collapse'),
      stack: '',
    };
    this.aiView = null;  // {kind: 'search', q} | {kind: 'similar', id, name}
    this.animateOnHover = prefs.get('animHover', true);
    this.autoPlay = prefs.get('autoPlay', true);   // sounds play when you click them or arrow onto them
    this.audio = new Audio();
    this.audio.preload = 'none';
    this.playingId = null;
    this.audio.addEventListener('ended', () => { this.playingId = null; this.panel.paintPlayer?.(); this.grid?.refreshStates(); this.panel.wave?.draw(); this.viewer.wave?.draw(); });
    this.audio.addEventListener('timeupdate', () => this.panel.paintPlayer?.());
    this.audio.addEventListener('play', () => { this.panel.wave?.loop(); this.viewer.wave?.loop(); });
    this.audio.addEventListener('pause', () => { this.panel.wave?.draw(); this.viewer.wave?.draw(); });
    this.typeFilter = new TypeFilter(this);
    this.lookFilter = new LookFilter(this);
    this.filterSets = [];
    this.allItems = EMPTY;
    this.items = EMPTY;
    this.idIndex = new Map();
    this.selected = new Set();      // replaced, never mutated in place (cheap undo + anchors)
    this.history = [];
    this.future = [];
    this.focusIndex = -1;
    this.focusId = null;
    this.anchor = -1;
    this.anchorBase = this.selected;
    this.showSel = false;
    this.scanVersion = -1;
    this.lastReload = 0;

    this.grid = new Grid($('#grid'), this);
    this.timeline = new Timeline(this.grid, this);
    this.panel = new Panel($('#panel'), this);
    this.tags = new Tags(this);
    this.pathbar = $('#pathbar');
    this.sidebar = new Sidebar($('#sidebar'), this);
    this.viewer = new Viewer(this);
    this.projects = new Projects(this);
    this.comparer = new Compare(this);
    this.grid.setOptions({ size: prefs.get('size', 170), fit: prefs.get('fit', 'contain'), labels: prefs.get('labels', false) });

    this.buildToolbar();
    this.buildStatus();
    this.buildHelp();
    this.bindGridPointer();
    this.bindKeys();
    installDrop(this);   // files dropped from Explorer / Finder
    installErrorLog();          // errors in the page go to Kurator's log as well
    installContextMenu(this);   // right-click: thumbnails, folders, tags, the grid
    this.installHistory();      // Back / Forward: the viewer, then the folders and tags you visited
    this.installWakeUp();       // the status line catches up when the window comes back
    setTimeout(() => checkForUpdate(this), 15000);   // a newer Kurator? (asks GitHub once a day; Settings → off)
    this.tags.refresh();
    this.projects.refresh();
    this.refreshState().then(async () => {
      if (this.query.folder) {  // re-open the last folder (registers it again if it was a browsed one)
        try { await api.browse(this.query.folder); } catch { this.query.folder = null; prefs.set('folder', null); }
      }
      this.renderPathBar();
      this.reload({ keepScroll: false });
    });
    this.poll();
  }

  // ---------------------------------------------------------------- data
  indexOf(id) { const i = this.idIndex.get(id); return i === undefined ? -1 : i; }

  async reload({ keepScroll = true } = {}) {
    this.navRecord();
    if (this.reloadCtrl) this.reloadCtrl.abort();
    this.reloadCtrl = new AbortController();
    const q = { ...this.query, recursive: this.query.recursive ? 1 : 0, guessed: this.query.guessed ? 1 : 0 };
    if (!phEnabled()) { q.ph = ''; if (PH_SORTS[q.sort]) q.sort = 'date'; }   // Playhead switched off: its filters and sorts rest
    else if (phGridPref().on) q.phinfo = 1;                                      // which sounds get a Playhead strip
    let data;
    try {
      if (this.aiView?.kind === 'search') data = await api.aiSearch(this.aiView.q);
      else if (this.aiView?.kind === 'similar') data = await api.aiSimilar(this.aiView.id);
      else data = await api.items(q, this.reloadCtrl.signal);
    } catch (e) {
      if (e.name !== 'AbortError') { this.toast(e.message, 4000); if (this.aiView) { this.aiView = null; this.renderPathBar(); } }
      return;
    }
    this.lastReload = performance.now();
    this.allItems = data;
    this._allIndex = null;
    this.applyView({ keepScroll });
  }

  /** id -> index in the unfiltered list (built lazily). */
  allIndex() {
    if (!this._allIndex) {
      const m = new Map(), ids = this.allItems.ids;
      for (let i = 0; i < ids.length; i++) m.set(ids[i], i);
      this._allIndex = m;
    }
    return this._allIndex;
  }

  applyView({ keepScroll = true } = {}) {
    let it = this.allItems;
    if (this.showSel) {
      const keep = [];
      for (let i = 0; i < it.ids.length; i++) if (this.selected.has(it.ids[i])) keep.push(i);
      const sub = {};
      for (const k of Object.keys(EMPTY)) if (Array.isArray(it[k])) sub[k] = keep.map((i) => it[k][i]);
      sub.count = keep.length;
      it = sub;
    }
    this.items = it;
    this.idIndex = new Map();
    for (let i = 0; i < it.ids.length; i++) this.idIndex.set(it.ids[i], i);
    this.focusIndex = this.focusId != null ? this.indexOf(this.focusId) : -1;
    this.viewer?.relocate();   // the list changed under the open viewer (a saved frame or render, indexing)
    this.anchor = Math.min(this.anchor, it.ids.length - 1);
    this.grid.setItems(it, { keepScroll });
    this.timeline.update();
    this.panel.show(this.focusIndex);
    this.updateStatus();
  }

  async refreshState() {
    let st;
    try { st = await api.state(); } catch { this.setOffline(true); return; }
    this.setOffline(false);
    this.state = st;
    if (st.types) {
      this.typeFilter.setTypes(st.types);
      VIEWABLE.clear(); for (const e of st.types.viewable) VIEWABLE.add(e);
      AUDIO.clear(); for (const e of st.types.audio) AUDIO.add(e);
      if (st.types.meshes) { MESHES.clear(); for (const e of st.types.meshes) MESHES.add(e); }
      if (st.types.video) { VIDEO.clear(); for (const e of st.types.video) VIDEO.add(e); }
    }
    const scan = st.scan;
    if (scan.version !== this.scanVersion) {
      const first = this.scanVersion === -1;
      this.scanVersion = scan.version;
      this.sidebar.refresh(st.roots);
      this.tags.refreshSoon();
      this.projects.refresh();
      // While indexing, refresh the grid at most every 2.5 s; selection/scroll/focus are kept.
      if (!first && (scan.phase === 'idle' || performance.now() - this.lastReload > 2500)) this.reload();
    }
    this.updateScanStatus(scan);
    const aj = st.ai_job || {};
    const j = aj.running ? { running: true, kind: 'ai', files_done: aj.done, files_total: aj.total, bytes_total: aj.total, bytes_done: aj.done } : (st.job || {});
    if (j.running) {
      const pct = j.bytes_total ? Math.round((100 * j.bytes_done) / j.bytes_total) : 0;
      this.stJob.textContent = `${j.kind === 'ai' ? 'AI' : j.kind === 'restore' ? 'Restoring' : 'Backing up'} ${pct}% · ${fmtNum(j.files_done)}/${fmtNum(j.files_total)}`;
    } else this.stJob.textContent = '';
    this.paintBackupDue(st.backup_due || [], j.running);
    noteArrivals(this, st.sfx);
  }

  /** "Backup due" reminder in the status bar (sets with a schedule, see Backup → set → When). */
  paintBackupDue(due, running) {
    const show = !running && due.length;
    this.stDue.style.display = show ? '' : 'none';
    if (!show) return;
    const d = due[0];
    const when = d.days == null ? 'never backed up' : `last backup ${Math.floor(d.days)} day${Math.floor(d.days) === 1 ? '' : 's'} ago`;
    this.stDue.textContent = `⚠ Backup due: ${d.name}${due.length > 1 ? ` (+${due.length - 1})` : ''}`;
    this.stDue.title = `${when}${!d.reachable ? ' - connect the backup disk' : d.auto ? ' - starts by itself in a few minutes' : ''}. Click to open Backup.`;
  }

  poll() {
    const busy = this.state && (this.state.scan.phase !== 'idle' || this.state.job?.running || this.state.ai_job?.running);
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(async () => {
      // an error while painting the state must not end the polling: the status line would freeze
      try { await this.refreshState(); } catch (e) { reportError('status', e); } finally { this.poll(); }
    }, busy ? 1000 : 4000);
  }

  /** Back to the window (it may have been hidden, and its timers slowed down): the state now. */
  installWakeUp() {
    const wake = () => { if (document.visibilityState === 'visible') { this.refreshState().catch((e) => reportError('status', e)); this.poll(); } };
    document.addEventListener('visibilitychange', wake);
    addEventListener('focus', wake);
  }

  setFolder(path) {
    this.query.folder = path;
    prefs.set('folder', path);
    this.sidebar.render();
    this.sidebar.renderFs();
    this.renderPathBar();
    this.reload({ keepScroll: false });
  }

  /** Open any folder: inside the library it's a filter, elsewhere it is browsed (not added). */
  async openPath(path) {
    let r;
    try { r = await api.browse(path); } catch (e) { this.toast(e.message); return; }
    this.setFolder(r.folder);
  }

  isLibraryPath(p) {
    const sep = this.state?.sep || '/';
    return (this.state?.roots || []).some((r) => r.kind !== 'browse' && (p === r.path || p.startsWith(r.path.replace(/[\\/]$/, '') + sep)));
  }

  parentOf(p) {
    const sep = this.state?.sep || '/';
    const trimmed = p.length > 3 ? p.replace(/[\\/]+$/, '') : p;
    const i = trimmed.lastIndexOf(sep);
    if (i < 0) return null;
    let parent = trimmed.slice(0, i);
    if (sep === '\\' && /^[A-Za-z]:$/.test(parent)) parent += '\\';
    if (!parent) parent = sep === '/' ? '/' : null;
    return parent === p ? null : parent;
  }

  // ------------------------------------------------------------- filters
  /**
   * Sidebar tag clicks:  click = go to everything with this tag, in the whole library (like opening
   * an album); Ctrl+click = narrow the current view (keeps the folder and other filters);
   * Alt+click = hide items with this tag.
   */
  setTagFilter(id, add = false, { exclude = false } = {}) {
    const list = (s) => (s || '').split(',').filter(Boolean).map(Number);
    let ids = list(this.query.tag), ex = list(this.query.exclude);
    if (exclude) {
      ex = ex.includes(id) ? ex.filter((x) => x !== id) : [...ex, id];
      ids = ids.filter((x) => x !== id);
    } else if (add) {
      ids = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
      ex = ex.filter((x) => x !== id);
    } else {
      const same = ids.length === 1 && ids[0] === id && !this.query.folder;
      ids = same ? [] : [id];
      ex = [];
      this.query.missing = '';
      if (this.query.folder && !same) this.leaveFolder();
    }
    this.query.tag = ids.join(',');
    this.query.exclude = ex.join(',');
    this.onFilterChanged();
  }

  /** Back to "All library items" without reloading twice (the caller reloads). */
  leaveFolder() {
    this.query.folder = null;
    prefs.set('folder', null);
    this.sidebar.render();
    this.sidebar.renderFs();
  }

  /** Show images without any tag of a group: '' | 'subject' | 'project' | 'status' | 'source'. */
  setMissing(facet, { here = false } = {}) {
    if (facet && !here && this.query.folder) this.leaveFolder();
    this.query.missing = facet;
    if (facet) this.query.tag = (this.query.tag || '').split(',').filter((t) => t && this.tags.facetOf(+t) !== facet).join(',');
    this.onFilterChanged();
  }

  setUntagged(on) { this.setMissing(on ? 'subject' : ''); }

  /** The filters a saved view remembers. */
  viewQuery() {
    const q = this.query;
    return { folder: q.folder, recursive: q.recursive, q: q.q, ext: q.ext, sort: q.sort, order: q.order, guessed: q.guessed,
      tag: q.tag, exclude: q.exclude, missing: q.missing, project: this.projects.active || '', dupes: q.dupes, similar: q.similar,
      hues: q.hues, shape: q.shape, alpha: q.alpha, minside: q.minside, bpm_min: q.bpm_min, bpm_max: q.bpm_max, key: q.key, len_min: q.len_min, len_max: q.len_max, tex: q.tex, ph: q.ph };
  }

  saveView() {
    const q = this.viewQuery();
    const input = el('input', { type: 'text', value: this.sidebar.describeView(q).slice(0, 60), spellcheck: 'false' });
    const save = async (close) => {
      const name = input.value.trim();
      if (!name) return;
      try { await this.sidebar.loadViews(await api.saveView(name, q)); close(); this.toast(`Saved view "${name}"`); } catch (e) { this.toast(e.message); }
    };
    const d = openDialog({ title: 'Save view', width: 440,
      body: el('div', {}, el('label', {}, 'Name', input),
        el('div', { class: 'p-hint small', text: `Remembers: ${this.sidebar.describeView(q)} (and the sort order). It appears under Saved views; a view with the same name is updated.` })),
      buttons: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: save }] });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(d.close); });
    setTimeout(() => input.select(), 0);
  }

  openSavedView(v) {
    this.activeView = v.id;
    this.activeViewKey = null;   // taken from the first path-bar render after applying
    this.applyViewQuery(v.query || {});
    this.sidebar.renderViews();
  }

  /** Show a remembered view: a saved one, or one Back / Forward returns to. */
  applyViewQuery(q, ai = null) {
    Object.assign(this.query, { folder: q.folder || null, recursive: q.recursive ?? this.query.recursive, q: q.q || '', ext: q.ext || '',
      sort: q.sort || this.query.sort, order: q.order || this.query.order, guessed: !!q.guessed, tag: q.tag || '', exclude: q.exclude || '',
      missing: q.missing || '', dupes: !!q.dupes, similar: +q.similar || 0, untagged: false, suggested: false,
      hues: +q.hues || 0, shape: q.shape || '', alpha: !!q.alpha, minside: +q.minside || 0, bpm_min: q.bpm_min ?? '', bpm_max: q.bpm_max ?? '',
      key: q.key || '', len_min: q.len_min ?? '', len_max: q.len_max ?? '', tex: q.tex || '', ph: q.ph || '' });
    this.lookFilter.paint();
    prefs.set('folder', this.query.folder);
    this.typeFilter.setSelected?.(this.query.ext);
    const s = $('#search');
    if (s) s.value = this.query.q;
    this.aiView = ai;
    if ((q.project || null) !== (this.projects.active || null)) {
      if (q.project) this.projects.activate(+q.project, { keepFilters: true }); else this.projects.leave();
    } else {
      this.sidebar.render(); this.sidebar.renderFs(); this.onFilterChanged();
    }
  }

  // ------------------------------------------------------- back / forward
  // Every change of view (folder, tag, project, search, filters) is a step in the history, and so is
  // opening the viewer: Back (the mouse's back button, Alt+Left) closes the viewer, then goes back
  // through the folders and tags you visited; Forward returns.

  /** What Back returns to: the view's filters (the sort order stays as it is). */
  navSnapshot() {
    const q = this.viewQuery();
    delete q.sort;
    delete q.order;
    return JSON.parse(JSON.stringify({ ...q, ai: this.aiView || null }));
  }

  navRecord() {
    const snap = this.navSnapshot();
    const st = history.state?.kurator ? history.state : null;
    const kind = st?.kurator === 'viewer' ? st : { kurator: 'view' };
    if (!st || this.navRestoring) {   // the first view, or the one Back / Forward just went to
      this.navRestoring = false;
      history.replaceState({ ...kind, view: snap }, '');
      return;
    }
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    if (same(st.view, snap)) return;
    // typing a search: one step for the search, not one per letter
    const searchOnly = same({ ...st.view, q: '' }, { ...snap, q: '' }) && st.view.q !== '';
    if (searchOnly || st.kurator === 'viewer') history.replaceState({ ...kind, view: snap }, '');
    else history.pushState({ kurator: 'view', view: snap }, '');
  }

  installHistory() {
    addEventListener('popstate', (e) => {
      const st = e.state;
      if (!st?.kurator) return;
      this.navPopping = true;
      try {
        if (st.kurator === 'viewer') {   // Forward into the viewer again
          const i = this.indexOf(st.id);
          if (i >= 0 && !this.viewer.isOpen) this.viewer.open(i);
          return;
        }
        if (this.viewer.isOpen) this.viewer.close();
        if (JSON.stringify(st.view) !== JSON.stringify(this.navSnapshot())) {
          this.navRestoring = true;
          this.applyViewQuery(st.view, st.view.ai || null);
        }
      } finally { this.navPopping = false; }
    });
    // the desktop app on macOS and Linux: WebKit leaves the mouse's back / forward buttons alone
    if (inShell() && !window.chrome) {
      addEventListener('mouseup', (e) => {
        if (e.button === 3) { e.preventDefault(); history.back(); } else if (e.button === 4) { e.preventDefault(); history.forward(); }
      });
    }
  }

  /** The viewer opened (not a quick look): a step Back closes it again. */
  navViewerOpened(id) {
    if (this.navPopping) return;
    history.pushState({ kurator: 'viewer', view: this.navSnapshot(), id }, '');
  }

  navViewerClosed() {
    if (this.navPopping || history.state?.kurator !== 'viewer') return;
    history.back();   // its step goes; the view under it stays as it is
  }

  /** Similar images: groups of near-identical pictures (dist = how many of 64 bits may differ). */
  async setSimilar(dist) {
    this.query.similar = dist;
    if (dist) {
      this.query.dupes = false;
      if (this.query.folder && !this.isLibraryPath(this.query.folder)) this.leaveFolder();
      const wait = async () => {
        let st;
        try { st = await api.similarStatus(); } catch { return; }
        if (!this.query.similar) return;
        if (st.pending) {
          this.toast(`Preparing ${fmtNum(st.pending)} pictures for comparison…`, 1600);
          setTimeout(wait, 1500);
          this._similarWaited = true;
        } else if (this._similarWaited) { this._similarWaited = false; this.reload(); }
      };
      wait();
    }
    this.sidebar.renderLibrary();
    this.onFilterChanged();
  }

  /** Compare the 2-4 selected images (or the focused one with the selected one). */
  compare() {
    const idx = [...this.selected].map((id) => this.indexOf(id)).filter((i) => i >= 0);
    if (idx.length === 1 && this.focusIndex >= 0 && !idx.includes(this.focusIndex)) idx.unshift(this.focusIndex);
    if (idx.length < 2) { this.toast('Select 2 to 4 images to compare'); return; }
    if (idx.length > 4) { this.toast('Compare works with up to 4 images - select fewer'); return; }
    idx.sort((a, b) => a - b);
    this.comparer.open(idx);
  }

  // ----------------------------------------------------------- version stacks
  async stackSelection(byName = false) {
    const ids = this.tagTargets();
    if (ids.length < 2) { this.toast('Select two or more versions first'); return; }
    try {
      const r = await api.stack(ids, byName);
      this.toast(byName ? (r.stacks ? `${fmtNum(r.stacks)} stacks made from the names` : 'No versions found by name') : 'Stacked - the newest is shown on top');
      this.reload();
    } catch (e) { this.toast(e.message); }
  }

  async unstackSelection() {
    const ids = this.tagTargets();
    try { const r = await api.unstack(ids); this.toast(r.dissolved ? `Unstacked ${fmtNum(r.dissolved)}` : 'Not in a stack'); this.reload(); } catch (e) { this.toast(e.message); }
  }

  /** Show all versions of a stack (by stack id, or of the stack a file is in). */
  async openStack(stackId, name, fileId) {
    if (stackId == null && fileId != null) {
      try { stackId = (await api.item(fileId)).stack?.id; } catch { return; }
    }
    if (!stackId) return;
    this.query.stack = stackId;
    this.stackName = name;
    this.onFilterChanged();
  }

  setStacks(collapse) {
    this.query.stacks = collapse ? 'collapse' : 'expand';
    prefs.set('stacks', this.query.stacks);
    this.stacksBtn?.classList.toggle('on', collapse);
    this.reload({ keepScroll: true });
  }

  /** Exact duplicates: files whose content exists in more than one place, next to each other. */
  setDupes(on) {
    this.query.dupes = on;
    if (on) this.query.similar = 0;
    if (on && this.query.folder) this.leaveFolder();
    this.sidebar.renderLibrary();
    this.onFilterChanged();
  }

  clearFilters() {
    this.query.tag = '';
    this.query.exclude = '';
    this.query.dupes = false;
    this.query.similar = 0;
    this.query.stack = '';
    this.query.untagged = false;
    this.query.missing = '';
    this.query.suggested = false;
    this.aiView = null;
    this.query.q = '';
    this.lookFilter.reset();
    const s = $('#search');
    if (s) s.value = '';
    this.onFilterChanged();
  }

  setSuggestedFilter(on) {
    this.query.suggested = on;
    this.aiView = null;
    this.onFilterChanged();
  }

  setAIView(v) {
    this.aiView = v;
    this.renderPathBar();
    this.reload({ keepScroll: false });
  }

  async aiReview(accept) {
    const ids = this.tagTargets();
    if (!ids.length) { this.toast('Select images with suggestions first'); return; }
    try {
      if (accept) this.onAIAccepted(await api.aiAccept({ ids }));
      else { const r = await api.aiReject({ ids }); this.toast(`Rejected ${fmtNum(r.rejected)} suggestion(s)`); this.reload(); }
    } catch (e) { this.toast(e.message); }
  }

  onAIAccepted(r) {
    for (const ch of r.changes || []) {
      this.updateLocalTags(ch.tag_id, ch.added, []);
      for (const a of ch.also_removed || []) this.updateLocalTags(a.tag_id, [], a.ids);
      if (ch.added.length) this.pushHistory({ kind: 'tag', tagId: ch.tag_id, added: ch.added, removed: [], also: ch.also_removed || [] });
    }
    this.toast(`Accepted ${fmtNum(r.accepted)} suggestion(s) · Ctrl+Z to undo`);
    this.tags.refreshSoon();
    this.reload();
  }

  onFilterChanged() {
    this.computeFilterSets();
    this.tags.renderSidebar();
    this.renderPathBar();
    this.reload({ keepScroll: false });
  }

  computeFilterSets() {
    this.filterSets = (this.query.tag || '').split(',').filter(Boolean).map((t) => this.tags.descendants(+t));
    this.excludeSets = (this.query.exclude || '').split(',').filter(Boolean).map((t) => this.tags.descendants(+t));
  }

  /** Does item i still match the tag filter? (Items you just re-tagged stay visible but dimmed.) */
  matchesFilter(i) {
    const ts = this.items.t?.[i];
    if (this.query.untagged) return !ts || !ts.length;
    if (this.query.missing && ts && ts.some((t) => this.tags.facetOf(t) === this.query.missing)) return false;
    for (const set of this.filterSets) {
      if (!ts || !ts.some((t) => set.has(t))) return false;
    }
    for (const set of this.excludeSets || []) {
      if (ts && ts.some((t) => set.has(t))) return false;
    }
    return true;
  }

  onTagsChanged() {
    this.computeFilterSets();
    this.grid.repaint();
    this.panel.renderTags();
    this.viewer.paintTags();
    this.renderPathBar();
  }

  // ---------------------------------------------------------------- tags
  /** What a tag hotkey applies to: the selection, or the focused image if nothing is selected. */
  tagTargets() {
    if (this.selected.size) return [...this.selected];
    return this.focusId != null && this.indexOf(this.focusId) >= 0 ? [this.focusId] : [];
  }

  async applyTag(tagId, { op = 'toggle', targets = this.tagTargets(), history = true, quiet = false, remember = true } = {}) {
    if (!targets.length) { this.toast('Select or focus an image first'); return null; }
    let r;
    try { r = await api.applyTag(tagId, targets, op); } catch (e) { this.toast(e.message); return null; }
    if (remember && op !== 'remove') this.tags.setLast(tagId);   // 0 / Q apply it again
    this.updateLocalTags(tagId, r.added, r.removed);
    for (const a of r.also_removed || []) this.updateLocalTags(a.tag_id, [], a.ids);  // e.g. "final" replaced "WIP"
    if (history && (r.added.length || r.removed.length || r.also_removed?.length)) {
      this.pushHistory({ kind: 'tag', tagId, added: r.added, removed: r.removed, also: r.also_removed || [] });
    }
    if (!quiet) {
      const name = r.tag.name;
      this.toast(r.added.length ? `Tagged ${fmtNum(r.added.length)} × ${name}` : r.removed.length ? `Removed ${name} from ${fmtNum(r.removed.length)}` : `Already tagged ${name}`);
    }
    this.tags.refreshSoon();
    return r;
  }

  /** 0 or Q: add/remove the last used tag, like its hotkey would. */
  applyLastTag(targets) {
    const t = this.tags.last;
    if (!t) { this.toast('No tag used yet - 0 and Q repeat the last tag you add (hotkey, tag bar, T, drag)'); return null; }
    return this.applyTag(t.id, targets ? { targets } : {});
  }

  updateLocalTags(tagId, added, removed) {
    const patch = (items, index) => {
      for (const id of added) {
        const i = index.get(id);
        if (i === undefined) continue;
        const cur = items.t[i] || [];
        if (!cur.includes(tagId)) items.t[i] = [...cur, tagId];
      }
      for (const id of removed) {
        const i = index.get(id);
        if (i === undefined) continue;
        const next = (items.t[i] || []).filter((t) => t !== tagId);
        items.t[i] = next.length ? next : 0;
      }
    };
    patch(this.allItems, this.allIndex());
    if (this.items !== this.allItems) patch(this.items, this.idIndex);
    this.grid.repaint();
    this.panel.renderTags();
    this.viewer.paintTags();
    this.tags.renderBar();
  }

  // --------------------------------------------------------------- moves
  moveDialog(ids = this.tagTargets()) { openMoveDialog(this, ids); }
  copyDialog(ids = this.tagTargets()) { openCopyDialog(this, ids); }
  resampleDialog(ids = null) { openResampleDialog(this, ids); }

  onCopied(r) {
    const extra = r.failed.length ? ` · ${r.failed.length} failed` : '';
    const where = r.indexed === r.copied ? '' : r.indexed ? ` (${fmtNum(r.indexed)} in the library)` : ' (outside the library)';
    if (r.zip) this.toast(`Zipped ${fmtNum(r.copied)} file${r.copied === 1 ? '' : 's'} into ${r.zip.split(/[\\/]/).pop()}${extra} · Ctrl+Z deletes it`, 4000);
    else this.toast(`Copied ${fmtNum(r.copied)} file${r.copied === 1 ? '' : 's'}${r.cleaned ? ` (${fmtNum(r.cleaned)} for delivery)` : ''}${where}${extra} · Ctrl+Z to undo`, 3500);
    if (r.failed.length) console.warn('Copy failures', r.failed);
    if (r.op_id) this.pushHistory({ kind: 'copy', opId: r.op_id, count: r.copied });
    if (r.indexed) { this.reload(); this.refreshState(); this.tags.refreshSoon(); }
  }

  renameDialog(ids = null) { openRenameDialog(this, ids); }

  onRenamed(r) {
    this.toast(`Renamed ${fmtNum(r.renamed)} file${r.renamed === 1 ? '' : 's'}${r.failed.length ? ` · ${r.failed.length} failed` : ''} · Ctrl+Z to undo`, 3500);
    if (r.failed.length) console.warn('Rename failures', r.failed);
    if (r.op_id) this.pushHistory({ kind: 'rename', opId: r.op_id, count: r.renamed });
    this.reload();
  }

  /** Delete (Del): to the system trash or Kurator's trash folder (⚙ Settings → Delete), after asking. */
  async deleteDialog(ids = this.tagTargets()) {
    if (!ids.length) { this.toast('Select files to delete first'); return; }
    let tr = { mode: 'system', days: 30 };
    try { tr = await api.trash(); } catch { /* the server says it when deleting */ }
    const where = tr.mode === 'kurator'
      ? `Kurator's trash folder${tr.days ? ` (kept ${tr.days} days)` : ''}` : 'the Recycle Bin / Trash';
    openDialog({
      title: `Delete ${fmtNum(ids.length)} file${ids.length === 1 ? '' : 's'}?`, width: 460,
      body: el('div', { class: 'form' },
        el('div', { text: `They go to ${where}, with their sidecars. Ctrl+Z brings them back${tr.mode === 'kurator' ? '' : ' (or restore them there - their tags come back by themselves)'}.` }),
        el('div', { class: 'p-hint small', text: 'Where deleted files go: ⚙ Settings → Delete.' })),
      buttons: [{ label: 'Cancel' }, { label: 'Delete', primary: true, danger: true, onClick: async (close) => {
        close();
        try { this.onDeleted(await api.deleteFiles(ids)); } catch (e) { this.toast(e.message, 5000); }
      } }],
    });
  }

  onDeleted(r, note = '') {
    const where = r.mode === 'kurator' ? "Kurator's trash" : 'the trash';
    const held = r.held_instead ? ` (${fmtNum(r.held_instead)} to Kurator's trash folder: that drive has no Recycle Bin)` : '';
    this.toast(`Deleted ${fmtNum(r.deleted)} file${r.deleted === 1 ? '' : 's'} to ${where}${held}${note}${r.failed.length ? ` · ${r.failed.length} failed: ${r.failed[0].error}` : ''} · Ctrl+Z to undo`, 4500);
    if (r.op_id) this.pushHistory({ kind: 'delete', opId: r.op_id, count: r.deleted });
    if (r.ids?.length && [...r.ids].some((id) => this.selected.has(id))) {
      const s = new Set(this.selected);
      for (const id of r.ids) s.delete(id);
      this.selected = s;
      this.onSelectionChanged();
    }
    this.reload();
    this.refreshState();
    this.tags.refreshSoon();
  }

  /** The Files ▾ menu in the status bar. */
  filesMenu(anchor) {
    const items = [
      ['Rename…', 'F2', () => this.renameDialog()],
      ['Copy to…', 'K', () => this.copyDialog()],
      ['Send to a quick destination…', 'D', () => openSendMenu(this)],
      ['Quick destinations…', '', () => openSlotEditor(this)],
      ['Resample…', 'I', () => this.resampleDialog()],
      ['Delete…', 'Del', () => this.deleteDialog()],
      ['Clean up duplicates…', '', () => (this.query.dupes ? openDupesDialog(this) : (this.setDupes(true), this.toast('These are the duplicates: Files ▾ → Clean up duplicates… again to remove copies'))) ],
    ];
    const menu = el('div', { class: 'popmenu' }, items.map(([label, key, fn]) => el('button', { type: 'button', class: 'pm-row', onclick: () => { close(); fn(); } },
      el('span', { class: 'grow', text: label }), key ? el('kbd', { text: key }) : null)));
    const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', esc, true); };
    const outside = (e) => { if (!menu.contains(e.target) && e.target !== anchor) close(); };
    const esc = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    document.body.append(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(r.left, innerWidth - menu.offsetWidth - 4))}px`;
    menu.style.top = `${r.top - menu.offsetHeight - 4}px`;
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', esc, true);
  }

  onResampled(r) {
    if (!r?.op_id) return;
    this.toast(`Made ${fmtNum(r.written)} resampled cop${r.written === 1 ? 'y' : 'ies'}${r.failed ? ` · ${r.failed} failed` : ''} · Ctrl+Z deletes them`, 3500);
    this.pushHistory({ kind: 'copy', opId: r.op_id, count: r.written });
    if (r.new_ids?.length) { this.reload(); this.refreshState(); this.tags.refreshSoon(); }
  }

  onMoved(r) {
    const extra = r.failed.length ? ` · ${r.failed.length} failed` : '';
    this.toast(`Moved ${fmtNum(r.moved)} file${r.moved === 1 ? '' : 's'}${r.copied ? ' (copied + verified)' : ''}${extra} · Ctrl+Z to undo`, 3500);
    if (r.failed.length) console.warn('Move failures', r.failed);
    if (r.op_id) this.pushHistory({ kind: 'move', opId: r.op_id, count: r.moved });
    this.reload();
    this.refreshState();
  }

  // ----------------------------------------------------------- selection
  pushHistory(entry) {
    this.history.push(entry);
    if (this.history.length > HISTORY_MAX) this.history.shift();
    this.future = [];
  }

  setSelection(next, { history = true } = {}) {
    if (next === this.selected) return;
    if (history) this.pushHistory({ kind: 'sel', sel: this.selected });
    this.selected = next;
    this.onSelectionChanged();
  }

  onSelectionChanged() {
    this.grid.refreshStates();
    this.updateStatus();
    this.scheduleTagBar();
    if (this.viewer.isOpen) this.viewer.paintSel();
    if (this.showSel) this.applyView();
  }

  scheduleTagBar() {
    if (this._barRaf) return;
    this._barRaf = requestAnimationFrame(() => {
      this._barRaf = 0;
      this.tags.renderBar();
      // the "− Remove N from <project>" button in the path bar follows the selection
      const single = (this.query.tag || '').split(',').filter(Boolean).length === 1;
      if (single || this.projects.active) this.renderPathBar();
    });
  }

  async undo() {
    const e = this.history.pop();
    if (!e) { this.toast('Nothing to undo'); return; }
    if (e.kind === 'sel') {
      this.future.push({ kind: 'sel', sel: this.selected });
      this.selected = e.sel;
      this.onSelectionChanged();
      this.toast('Selection undone');
    } else if (e.kind === 'tag') {
      try {
        if (e.added.length) await api.applyTag(e.tagId, e.added, 'remove');
        if (e.removed.length) await api.applyTag(e.tagId, e.removed, 'add');
        for (const a of e.also || []) await api.applyTag(a.tag_id, a.ids, 'add');
      } catch (err) { this.toast(err.message); return; }
      this.updateLocalTags(e.tagId, e.removed, e.added);
      for (const a of e.also || []) this.updateLocalTags(a.tag_id, a.ids, []);
      this.tags.refreshSoon();
      this.future.push(e);
      this.toast(`Tag change undone (${this.tags.byId.get(e.tagId)?.name || 'tag'})`);
    } else if (e.kind === 'move') {
      try {
        const r = await api.undoOp(e.opId);
        this.toast(`Moved ${fmtNum(r.moved)} file${r.moved === 1 ? '' : 's'} back${r.failed.length ? ` · ${r.failed.length} failed` : ''}`);
      } catch (err) { this.toast(err.message); return; }
      this.future = [];
      this.reload();
      this.refreshState();
    } else if (e.kind === 'drop') {   // files dropped onto the page: tags off, copied files deleted
      let r = null;
      try {
        for (const ch of e.changes) {
          if (ch.added.length) await api.applyTag(ch.tagId, ch.added, 'remove');
          for (const a of ch.also || []) await api.applyTag(a.tag_id, a.ids, 'add');
        }
        if (e.opId) r = await api.undoOp(e.opId);
      } catch (err) { this.toast(err.message); return; }
      this.toast(`Drop undone${r ? ` · ${fmtNum(r.removed)} copied file${r.removed === 1 ? '' : 's'} deleted` : ''}`);
      this.future = [];
      this.reload();
      this.refreshState();
      this.tags.refreshSoon();
    } else if (e.kind === 'rename') {
      try {
        const r = await api.undoOp(e.opId);
        this.toast(`Renamed ${fmtNum(r.renamed)} file${r.renamed === 1 ? '' : 's'} back${r.failed.length ? ` · ${r.failed.length} failed` : ''}`);
      } catch (err) { this.toast(err.message); return; }
      this.future = [];
      this.reload();
    } else if (e.kind === 'delete') {
      try {
        const r = await api.undoOp(e.opId);
        this.toast(`Brought back ${fmtNum(r.restored)} file${r.restored === 1 ? '' : 's'}${r.failed.length ? ` · ${r.failed.length} not: ${r.failed[0].error}` : ''}`, r.failed.length ? 6000 : 3000);
      } catch (err) { this.toast(err.message); return; }
      this.future = [];
      this.reload();
      this.refreshState();
      this.tags.refreshSoon();
    } else if (e.kind === 'copy') {
      try {
        const r = await api.undoOp(e.opId);
        const kept = r.kept.filter((k) => k.reason !== 'already gone');
        this.toast(`Deleted ${fmtNum(r.removed)} cop${r.removed === 1 ? 'y' : 'ies'}${kept.length ? ` · ${kept.length} kept (changed since)` : ''}`, 3500);
      } catch (err) { this.toast(err.message); return; }
      this.future = [];
      this.reload();
      this.refreshState();
      this.tags.refreshSoon();
    }
  }

  async redo() {
    const e = this.future.pop();
    if (!e) return;
    if (e.kind === 'sel') {
      this.history.push({ kind: 'sel', sel: this.selected });
      this.selected = e.sel;
      this.onSelectionChanged();
    } else if (e.kind === 'tag') {
      try {
        if (e.added.length) await api.applyTag(e.tagId, e.added, 'add');
        if (e.removed.length) await api.applyTag(e.tagId, e.removed, 'remove');
        for (const a of e.also || []) await api.applyTag(a.tag_id, a.ids, 'remove');
      } catch (err) { this.toast(err.message); return; }
      this.updateLocalTags(e.tagId, e.added, e.removed);
      for (const a of e.also || []) this.updateLocalTags(a.tag_id, [], a.ids);
      this.tags.refreshSoon();
      this.history.push(e);
    }
  }

  setAnchor(i) { this.anchor = i; this.anchorBase = this.selected; }

  rangeSet(base, a, b) {
    const s = new Set(base);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    for (let i = Math.max(0, lo); i <= hi; i++) s.add(this.items.ids[i]);
    return s;
  }

  toggleSelect(i) {
    const id = this.items.ids[i];
    if (id == null) return;
    const s = new Set(this.selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    this.setSelection(s);
    this.setAnchor(i);
  }

  selectAll() { this.setSelection(new Set([...this.selected, ...this.items.ids])); }

  invertSelection() {
    const s = new Set(this.selected);
    for (const id of this.items.ids) { if (s.has(id)) s.delete(id); else s.add(id); }
    this.setSelection(s);
  }

  clearSelection() { if (this.selected.size) this.setSelection(new Set()); }

  isAudio(i) { return i >= 0 && AUDIO.has(extOf(this.items.n[i] || '')); }

  /** Play / stop the audio item i (one player for the whole app). */
  togglePlay(i = this.focusIndex, { restart = false } = {}) {
    if (!this.isAudio(i)) return;
    const id = this.items.ids[i];
    const ext = extOf(this.items.n[i]);
    if (!VIEWABLE.has(ext)) { this.toast(`${ext.toUpperCase()} can't play in the browser - use Open to play it in your audio app`); return; }
    if (this.playingId === id && !this.audio.paused && !restart) { this.audio.pause(); this.playingId = null; }
    else {
      if (this.playingId !== id) { this.audio.src = `/f/${id}`; }
      if (restart) this.audio.currentTime = 0;
      this.playingId = id;
      this.audio.play().catch(() => {});
    }
    this.panel.paintPlayer?.();
    this.grid.refreshStates();
  }

  stopAudio() { if (!this.audio.paused) this.audio.pause(); this.playingId = null; this.panel.paintPlayer?.(); this.grid.refreshStates(); }

  /** Play sound `id` from `t` seconds (the waveform view: click / drag to seek). */
  playAt(id, t) {
    const i = this.indexOf(id);
    const name = i >= 0 ? this.items.n[i] : (this.panel.detail?.id === id ? this.panel.detail.name : '');
    if (!VIEWABLE.has(extOf(name))) { this.toast(`${extOf(name).toUpperCase()} can't play in the browser - use Open to play it in your audio app`); return; }
    const a = this.audio;
    const seek = () => { try { a.currentTime = t; } catch { /* not seekable yet */ } };
    if (this.playingId !== id) {
      a.src = `/f/${id}`;
      this.playingId = id;
      a.addEventListener('loadedmetadata', seek, { once: true });
    } else seek();
    a.play().catch(() => {});
    this.panel.paintPlayer?.();
    this.grid.refreshStates();
  }

  setAutoPlay(on) {
    this.autoPlay = on;
    prefs.set('autoPlay', on);
    if (!on) this.stopAudio();
    this.updateStatus();
  }

  setFocus(i, { scroll = true } = {}) {
    if (i < 0 || i >= this.items.ids.length) return;
    const changed = this.focusIndex !== i;
    this.focusIndex = i;
    this.focusId = this.items.ids[i];
    if (changed && this.autoPlay && this.isAudio(i) && VIEWABLE.has(extOf(this.items.n[i]))) this.togglePlay(i, { restart: true });
    else if (changed && this.playingId && !this.isAudio(i)) this.stopAudio();
    if (scroll) this.grid.scrollToIndex(i);
    this.grid.refreshStates();
    this.panel.show(i);
  }

  /** Arrow-key navigation. */
  moveFocus(i, e) {
    const n = this.items.ids.length;
    if (!n) return;
    i = Math.max(0, Math.min(n - 1, i));
    const prevId = this.focusId;
    if (e.shiftKey) {
      if (this.anchor < 0) this.setAnchor(this.focusIndex >= 0 ? this.focusIndex : i);
      this.setSelection(this.rangeSet(this.anchorBase, this.anchor, i), { history: false });
    } else if (!(e.ctrlKey || e.metaKey)) {
      // Follow mode: only while the selection is empty or just the previously focused item.
      const s = this.selected;
      if (s.size === 0 || (s.size === 1 && s.has(prevId))) this.setSelection(new Set([this.items.ids[i]]), { history: false });
      this.setAnchor(i);
    }
    this.setFocus(i);
  }

  // ------------------------------------------------------------ pointer
  bindGridPointer() {
    const g = this.grid;
    const scroller = g.el;
    const marquee = el('div', { class: 'marquee' });
    g.layer.append(marquee);
    let st = null;
    let raf = 0;
    const contentPt = (e) => {
      const r = scroller.getBoundingClientRect();
      return [e.clientX - r.left + scroller.scrollLeft, e.clientY - r.top + scroller.scrollTop];
    };
    const updateMarquee = () => {
      const [x0, y0] = [Math.min(st.x, st.cx), Math.min(st.y, st.cy)];
      const [x1, y1] = [Math.max(st.x, st.cx), Math.max(st.y, st.cy)];
      Object.assign(marquee.style, { display: 'block', transform: `translate(${x0}px,${y0}px)`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
      const idx = g.indicesInRect(x0, y0, x1, y1);
      const s = new Set(st.additive ? st.base : []);
      for (const i of idx) s.add(this.items.ids[i]);
      this.selected = s;  // history entry was pushed when the drag started
      this.onSelectionChanged();
    };
    const autoScroll = () => {
      raf = 0;
      if (!st || !st.marquee) return;
      const r = scroller.getBoundingClientRect();
      const edge = 48;
      let dy = 0;
      if (st.clientY < r.top + edge) dy = -Math.ceil((r.top + edge - st.clientY) / 3);
      else if (st.clientY > r.bottom - edge) dy = Math.ceil((st.clientY - (r.bottom - edge)) / 3);
      if (dy) {
        scroller.scrollTop += dy;
        st.cy += dy;
        updateMarquee();
        raf = requestAnimationFrame(autoScroll);
      }
    };

    scroller.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const badge = e.target.closest?.('.stackbadge');
      if (badge) {   // a version stack: show its versions
        const i = +badge.parentElement.dataset.i;
        e.preventDefault();
        this.openStack(null, this.items.n[i], this.items.ids[i]);
        return;
      }
      const play = e.target.closest?.('.play');
      if (play) {   // the ▶ on a sound: play/stop without touching the selection
        const i = +play.parentElement.dataset.i;
        e.preventDefault();
        if (this.playingId === this.items.ids[i]) this.stopAudio(); else this.togglePlay(i, { restart: true });
        return;
      }
      const r = scroller.getBoundingClientRect();
      if (e.clientX - r.left >= scroller.clientWidth) return; // scrollbar
      scroller.focus({ preventScroll: true });
      const [x, y] = contentPt(e);
      const hit = g.indexAt(x, y);
      st = { x, y, cx: x, cy: y, clientY: e.clientY, idx: hit, marquee: false,
             additive: e.ctrlKey || e.metaKey || e.shiftKey, ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey, base: this.selected,
             // dragging from a selected image carries the selection (drop it on a tag or project);
             // dragging from anywhere else draws a selection rectangle
             carry: hit >= 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && this.selected.has(this.items.ids[hit]) };
      scroller.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    scroller.addEventListener('pointermove', (e) => {
      if (!st) return;
      [st.cx, st.cy] = contentPt(e);
      st.clientY = e.clientY;
      if (st.carry) {
        if (!st.dragging && Math.abs(st.cx - st.x) + Math.abs(st.cy - st.y) > 8) {
          st.dragging = true;
          st.ghost = el('div', { class: 'dragghost' });
          document.body.append(st.ghost);
          document.body.classList.add('dragging-items');
          // in the desktop app the files can leave the window: into Photoshop, Blender, a folder...
          // (not on Linux yet: there the drop arrives without the files)
          if (inShell() && !/Linux/.test(navigator.userAgent)) st.paths = api.paths([...this.selected]).then((r) => r.paths, () => []);
        }
        if (st.dragging && st.paths && (e.clientX <= 0 || e.clientY <= 0 || e.clientX >= innerWidth - 1 || e.clientY >= innerHeight - 1)) {
          const s = st;
          st = null;
          dragOutside(s, e);
          return;
        }
        if (st.dragging) this.dragOver(st, e);
        return;
      }
      if (!st.marquee && Math.abs(st.cx - st.x) + Math.abs(st.cy - st.y) > 6) {
        st.marquee = true;
        this.pushHistory({ kind: 'sel', sel: this.selected });
      }
      if (st.marquee) {
        updateMarquee();
        if (!raf) raf = requestAnimationFrame(autoScroll);
      }
    });
    const dragOutside = async (s, e) => {
      try { scroller.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      s.ghost?.remove();
      s.target?.classList.remove('droptarget');
      document.body.classList.remove('dragging-items');
      const paths = await s.paths;
      if (!paths.length) return;
      try { await dragOut(paths, this.dragIcon(paths.length)); } catch (er) { this.toast(`Could not drag the files out: ${er.message || er}`, 4000); }
    };
    const end = () => {
      if (!st) return;
      const s = st;
      st = null;
      marquee.style.display = 'none';
      if (s.dragging) { this.dragDrop(s); return; }
      if (s.marquee) { this.setAnchor(this.focusIndex); return; }
      const i = s.idx;
      if (i < 0) return; // clicking empty space never clears the selection (Esc does)
      if (s.shift) {
        const a = this.anchor >= 0 ? this.anchor : i;
        this.setSelection(this.rangeSet(s.ctrl ? this.selected : this.anchorBase, a, i));
        this.setFocus(i, { scroll: false });
      } else if (s.ctrl) {
        this.setFocus(i, { scroll: false });
        this.toggleSelect(i);
      } else {
        this.setFocus(i, { scroll: false });
        const id = this.items.ids[i];
        if (!(this.selected.size === 1 && this.selected.has(id))) this.setSelection(new Set([id]));
        this.setAnchor(i);
      }
    };
    scroller.addEventListener('pointerup', end);
    scroller.addEventListener('pointercancel', end);
    scroller.addEventListener('dblclick', (e) => {
      const [x, y] = contentPt(e);
      const i = g.indexAt(x, y);
      if (i >= 0) this.openViewer(i);
    });
    scroller.addEventListener('wheel', (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      this.setSize(this.grid.size * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    }, { passive: false });
  }

  /** The picture under the pointer while files are dragged out of the desktop app: a PNG data URL. */
  dragIcon(n) {
    const c = el('canvas', { width: 112, height: 112 });
    const g = c.getContext('2d');
    const fi = this.focusIndex;
    const i = fi >= 0 && this.selected.has(this.items.ids[fi]) ? fi : this.items.ids.findIndex((id) => this.selected.has(id));
    const img = i >= 0 ? this.grid.layer.querySelector(`[data-i="${i}"] img`) : null;
    if (img?.complete && img.naturalWidth) {
      const k = Math.min(96 / img.naturalWidth, 96 / img.naturalHeight);
      const w = img.naturalWidth * k, h = img.naturalHeight * k;
      g.drawImage(img, (112 - w) / 2, (112 - h) / 2, w, h);
    } else {
      g.fillStyle = '#556'; g.fillRect(24, 16, 64, 80);
    }
    if (n > 1) {
      const t = fmtNum(n);
      g.font = 'bold 15px system-ui, sans-serif';
      const w = Math.max(26, g.measureText(t).width + 14);
      g.fillStyle = '#7aa2ff'; g.beginPath(); if (g.roundRect) g.roundRect(112 - w - 2, 2, w, 24, 12); else g.rect(112 - w - 2, 2, w, 24); g.fill();
      g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(t, 112 - 2 - w / 2, 14);
    }
    try { return c.toDataURL('image/png'); } catch { return el('canvas', { width: 1, height: 1 }).toDataURL('image/png'); }
  }

  /** The grid item under a point of the window (-1: none). */
  indexAtClient(x, y) {
    const r = this.grid.el.getBoundingClientRect();
    if (x < r.left || y < r.top || x >= r.left + this.grid.el.clientWidth || y >= r.bottom) return -1;
    return this.grid.indexAt(x - r.left + this.grid.el.scrollLeft, y - r.top + this.grid.el.scrollTop);
  }

  dragOver(st, e) {
    const n = this.selected.size;
    const under = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-tag-id]');
    if (st.target !== under) {
      st.target?.classList.remove('droptarget');
      st.target = under;
      under?.classList.add('droptarget');
    }
    const t = under && this.tags.byId.get(+under.dataset.tagId);
    st.ghost.textContent = t ? `${t.archived ? '' : '+ '}${fmtNum(n)} → ${t.name}` : `${fmtNum(n)} item${n === 1 ? '' : 's'} · drop on a tag or project`;
    st.ghost.style.transform = `translate(${e.clientX + 14}px, ${e.clientY + 10}px)`;
  }

  dragDrop(st) {
    st.ghost?.remove();
    st.target?.classList.remove('droptarget');
    document.body.classList.remove('dragging-items');
    const id = st.target ? +st.target.dataset.tagId : null;
    if (id) this.applyTag(id, { op: 'add', targets: [...this.selected] });
  }

  openViewer(i = this.focusIndex, opts = {}) {
    if (i < 0) i = 0;
    if (!this.items.ids.length) return;
    this.viewer.open(i, opts);
  }

  onViewerClosed(i) {
    this.navViewerClosed();
    if (i != null && i >= 0) { this.setFocus(i, { scroll: true }); }
    this.grid.el.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------ keyboard
  bindKeys() {
    let spaceDown = 0;
    document.addEventListener('keydown', (e) => {
      if (document.body.classList.contains('modal-open')) return;  // dialogs handle their own keys
      if (this.helpEl.classList.contains('open')) {
        if (e.key === 'Escape' || e.key === '?') { this.toggleHelp(false); e.preventDefault(); }
        return;
      }
      if (isTyping(e.target)) {
        if (e.key === 'Escape') e.target.blur();
        return;
      }
      if (this.comparer.isOpen) { if (this.comparer.onKey(e)) e.preventDefault(); return; }
      if (this.viewer.isOpen) {
        if (e.key === ' ' && !e.ctrlKey && this.viewer.quick && spaceDown) { e.preventDefault(); return; }
        if (this.viewer.onKey(e)) e.preventDefault();
        return;
      }
      const it = this.items, n = it.ids.length;
      const ctrl = e.ctrlKey || e.metaKey;
      const fi = this.focusIndex < 0 ? -1 : this.focusIndex;
      const cols = this.grid.cols;
      const k = e.key;
      let handled = true;
      if (k === 'ArrowRight') this.moveFocus(fi + 1, e);
      else if (k === 'ArrowLeft') this.moveFocus(Math.max(0, fi - 1), e);
      else if (k === 'ArrowDown') this.moveFocus(fi < 0 ? 0 : fi + cols, e);
      else if (k === 'ArrowUp') this.moveFocus(fi - cols < 0 ? (fi < 0 ? 0 : fi % cols) : fi - cols, e);
      else if (k === 'PageDown') this.moveFocus(Math.max(fi, 0) + cols * this.grid.rowsPerPage(), e);
      else if (k === 'PageUp') this.moveFocus(Math.max(fi, 0) - cols * this.grid.rowsPerPage(), e);
      else if (k === 'Home') this.moveFocus(0, e);
      else if (k === 'End') this.moveFocus(n - 1, e);
      else if (k === ' ' && ctrl) { if (fi >= 0) this.toggleSelect(fi); }
      else if (k === ' ' && this.isAudio(fi)) { if (!e.repeat) this.togglePlay(fi); }
      else if (k === ' ') { if (!e.repeat && n) { spaceDown = performance.now(); this.openViewer(fi, { quick: true }); } }
      else if ((k === 'p' || k === 'P') && !ctrl) { this.setAutoPlay(!this.autoPlay); this.toast(this.autoPlay ? 'Sounds play when selected: on' : 'Auto-play: off (Space plays)'); }
      else if ((k === 'a' || k === 'A') && !ctrl) this.aiReview(!e.shiftKey);
      else if ((k === 's' || k === 'S') && !ctrl) { if (fi >= 0) this.setAIView({ kind: 'similar', id: this.items.ids[fi], name: this.items.n[fi] }); }
      else if ((k === 'o' || k === 'O') && !ctrl) { if (fi >= 0) api.open(this.items.ids[fi]).catch((er) => this.toast(er.message)); }
      else if (k === 'Enter') this.openViewer(fi);
      else if (ctrl && (k === 'g' || k === 'G')) { if (e.shiftKey) this.unstackSelection(); else this.stackSelection(); }
      else if ((k === 'c' || k === 'C') && !ctrl) this.compare();
      else if ((k === 'x' || k === 'X') && !ctrl) { if (fi >= 0) this.toggleSelect(fi); }
      else if (ctrl && (k === 'a' || k === 'A')) this.selectAll();
      else if (ctrl && (k === 'i' || k === 'I')) this.invertSelection();
      else if (ctrl && !e.shiftKey && (k === 'z' || k === 'Z')) this.undo();
      else if (ctrl && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && e.shiftKey))) this.redo();
      else if (digitKey(e) != null && !ctrl && !e.altKey) {
        const dk = digitKey(e);
        const t = dk ? this.tags.byHotkey.get(dk) : null;
        if (!dk) this.applyLastTag();
        else if (t) this.applyTag(t.id); else this.toast(`No tag on hotkey ${dk} yet - create one with + in Tags${this.projects.active ? ' (or in this project: Edit…)' : ''}`);
      }
      else if ((k === 'q' || k === 'Q') && !ctrl && !e.altKey) this.applyLastTag();
      else if ((k === 't' || k === 'T') && !ctrl) this.tags.quickTag();
      else if (k === 'M' && e.shiftKey && !ctrl) openRulesDialog(this);
      else if ((k === 'b' || k === 'B') && !ctrl) openBackupDialog(this);
      else if ((k === 'j' || k === 'J') && !ctrl) openSheetsDialog(this);
      else if ((k === 'm' || k === 'M') && !ctrl) this.moveDialog();
      else if ((k === 'k' || k === 'K') && !ctrl) this.copyDialog();
      else if ((k === 'd' || k === 'D') && !ctrl && !e.altKey) openSendMenu(this);
      else if (k === 'F2') this.renameDialog();
      else if (k === 'Delete' && !ctrl) this.deleteDialog();
      else if ((k === 'i' || k === 'I') && !ctrl && !e.altKey) this.resampleDialog();
      else if ((k === 'r' || k === 'R') && !ctrl) { this.reload(); this.toast('Refreshed'); }
      else if (k === 'Backspace' || (e.altKey && k === 'ArrowUp')) { const p = this.query.folder && this.parentOf(this.query.folder); if (p) this.openPath(p); }
      else if (k === 'Escape') this.clearSelection();
      else if (k === '/' || (ctrl && (k === 'f' || k === 'F'))) $('#search').focus();
      else if ((k === '+' || k === '=') && this.projects.active) this.projects.addSelection('add');
      else if (k === '-' && this.projects.active) this.projects.addSelection('remove');
      else if (k === '+' || k === '=') this.setSize(this.grid.size * 1.15);
      else if (k === '-') this.setSize(this.grid.size / 1.15);
      else if (k === '?') this.toggleHelp(true);
      else handled = false;
      if (handled) e.preventDefault();
    });
    document.addEventListener('keyup', (e) => {
      if (e.key !== ' ' || !spaceDown) return;
      const held = performance.now() - spaceDown;
      spaceDown = 0;
      // Hold Space to peek, release to return. A short tap keeps the viewer open.
      if (this.viewer.isOpen && this.viewer.quick && held > 300) this.viewer.close();
      else if (this.viewer.isOpen && !this.viewer.navStep) {   // a tap: it stays open, and Back closes it
        this.viewer.navStep = true;
        this.navViewerOpened(this.items.ids[this.viewer.i]);
      }
    });
  }

  // ------------------------------------------------------------- toolbar
  buildToolbar() {
    const bar = $('#toolbar');
    const search = el('input', { id: 'search', type: 'search', placeholder: 'Search names…  ( / )', spellcheck: 'false' });
    search.value = this.query.q;
    search.placeholder = 'Search names…  ( / )   ?text = AI search';
    search.addEventListener('input', debounce(() => {
      const v = search.value.trim();
      if (v.startsWith('?')) return;  // AI search runs on Enter
      this.query.q = v;
      if (this.aiView?.kind === 'search') this.aiView = null;
      this.reload({ keepScroll: false });
    }, 200));
    search.addEventListener('keydown', (e) => {
      const v = search.value.trim();
      if (e.key === 'Enter' && v.startsWith('?') && v.length > 1) { this.query.q = ''; this.setAIView({ kind: 'search', q: v.slice(1).trim() }); }
    });

    const type = this.typeFilter.wrap;

    const dates = el('select', { title: 'Date filter' }, el('option', { value: '', text: 'All dates' }), el('option', { value: '1', text: 'Guessed dates only' }));
    dates.onchange = () => { this.query.guessed = dates.value === '1'; this.reload({ keepScroll: false }); };

    const sort = el('select', { title: 'Sort by' });
    const fillSorts = () => {
      setKids(sort, Object.entries(SORTS).map(([v, t]) => el('option', { value: v, text: t })),
        phEnabled() ? el('optgroup', { label: 'Playhead analysis' }, Object.entries(PH_SORTS).map(([v, t]) => el('option', { value: v, text: t }))) : null);
      sort.value = PH_SORTS[this.query.sort] && !phEnabled() ? 'date' : this.query.sort;
    };
    fillSorts();
    phOnSetting(() => { fillSorts(); this.reload({ keepScroll: true }); });
    phOnBatch((e) => { if (!e.detail.running) this.reload({ keepScroll: true }); });   // new analyses: filters and sorts change
    sort.onchange = () => { this.query.sort = sort.value; prefs.set('sort', sort.value); this.reload({ keepScroll: false }); };
    const order = el('button', { class: 'btn icon', title: 'Sort direction' });
    const paintOrder = () => { order.textContent = this.query.order === 'desc' ? '↓' : '↑'; };
    paintOrder();
    order.onclick = () => { this.query.order = this.query.order === 'desc' ? 'asc' : 'desc'; prefs.set('order', this.query.order); paintOrder(); this.reload({ keepScroll: false }); };

    const rec = el('label', { class: 'chk', title: 'Include images in subfolders' }, el('input', { type: 'checkbox' }), 'Subfolders');
    rec.firstChild.checked = this.query.recursive;
    rec.firstChild.onchange = () => { this.query.recursive = rec.firstChild.checked; prefs.set('recursive', this.query.recursive); this.reload({ keepScroll: false }); };

    this.sizeInput = el('input', { type: 'range', min: '64', max: '480', step: '1', title: 'Thumbnail size (Ctrl+wheel, + / -)' });
    this.sizeInput.value = this.grid.size;
    this.sizeInput.oninput = () => this.setSize(+this.sizeInput.value);

    const fit = el('button', { class: 'btn', title: 'Fit whole image / fill the cell' });
    const paintFit = () => { fit.textContent = this.grid.fit === 'cover' ? 'Fill' : 'Fit'; };
    paintFit();
    fit.onclick = () => { const f = this.grid.fit === 'cover' ? 'contain' : 'cover'; prefs.set('fit', f); this.grid.setOptions({ fit: f }); paintFit(); };

    this.stacksBtn = el('button', { class: 'btn' + (this.query.stacks === 'collapse' ? ' on' : ''), text: 'Stacks', title: 'Show version stacks as one item (newest on top). Ctrl+G stacks the selection, Ctrl+Shift+G unstacks.',
      onclick: () => this.setStacks(this.query.stacks !== 'collapse') });
    const labels = el('button', { class: 'btn', title: 'Show file names', text: 'Names' });
    labels.classList.toggle('on', this.grid.labels);
    labels.onclick = () => { const v = !this.grid.labels; prefs.set('labels', v); this.grid.setOptions({ labels: v }); labels.classList.toggle('on', v); };

    const rescan = el('button', { class: 'btn', title: 'Look for new / changed / deleted files', text: 'Rescan', onclick: () => api.scan().then(() => this.refreshState()) });
    const help = el('button', { class: 'btn icon', title: 'Keyboard shortcuts (?)', text: '?', onclick: () => this.toggleHelp(true) });
    const anim = el('button', { class: 'btn', title: 'Play animated GIF/WebP/PNG when the mouse is over them', text: '▶ Hover' });
    anim.classList.toggle('on', this.animateOnHover);
    anim.onclick = () => { this.animateOnHover = !this.animateOnHover; prefs.set('animHover', this.animateOnHover); anim.classList.toggle('on', this.animateOnHover); };

    bar.append(el('div', { class: 'brand', text: 'Kurator' }), this.projects.wrap, search, type, this.lookFilter.wrap, dates, sort, order, rec,
      el('div', { class: 'spacer' }), this.sizeInput, fit, labels, this.stacksBtn, anim, rescan,
      el('button', { class: 'btn', title: 'Back up to other disks, restore', text: 'Backup', onclick: () => openBackupDialog(this) }),
      el('button', { class: 'btn', title: 'AI tagging from tag pools, search by description, similar images', text: 'AI', onclick: () => openAIDialog(this) }),
      el('button', { class: 'btn icon', title: 'Settings: XMP sidecars, keyword import', text: '⚙', onclick: () => openSettingsDialog(this) }), help);
  }

  setSize(px) {
    px = Math.round(Math.max(64, Math.min(480, px)));
    this.sizeInput.value = px;
    prefs.set('size', px);
    this.grid.setOptions({ size: px });
  }

  // -------------------------------------------------------------- status
  buildStatus() {
    const bar = $('#statusbar');
    this.stCount = el('span', { class: 'st-count' });
    this.stScan = el('span', { class: 'st-scan' });
    this.stSel = el('span', { class: 'st-sel' });
    this.stJob = el('span', { class: 'st-job', title: 'Open Backup', onclick: () => openBackupDialog(this) });
    this.stDue = el('button', { class: 'btn small st-due', style: { display: 'none' }, onclick: () => openBackupDialog(this) });
    this.stUpdate = el('button', { class: 'btn small st-update', style: { display: 'none' }, onclick: () => openUpdateDialog(this) });
    this.autoPlayBtn = el('button', { class: 'btn small', title: 'Play sounds when you click them or move onto them with the arrow keys (P)',
      onclick: () => this.setAutoPlay(!this.autoPlay) });
    this.showSelBtn = el('button', { class: 'btn small', text: 'Show only selected', title: 'Temporarily show only the selected items',
      onclick: () => { this.showSel = !this.showSel; this.showSelBtn.classList.toggle('on', this.showSel); this.applyView({ keepScroll: false }); } });
    bar.append(this.stCount, this.stScan, this.stJob, this.stDue, this.stUpdate, el('div', { class: 'spacer' }), this.autoPlayBtn, this.stSel,
      el('button', { class: 'btn small', text: 'All', title: 'Select all (Ctrl+A)', onclick: () => this.selectAll() }),
      el('button', { class: 'btn small', text: 'Invert', title: 'Invert selection (Ctrl+I)', onclick: () => this.invertSelection() }),
      el('button', { class: 'btn small', text: 'Clear', title: 'Clear selection (Esc)', onclick: () => this.clearSelection() }),
      this.showSelBtn,
      el('button', { class: 'btn small', text: 'Undo', title: 'Undo last change - selection, tags, move or copy (Ctrl+Z)', onclick: () => this.undo() }),
      el('button', { class: 'btn small', text: 'Join / sheet…', title: 'Join the selected images into one, make a contact sheet, a sprite sheet or a texture atlas (J)', onclick: () => openSheetsDialog(this) }),
      el('button', { class: 'btn small', text: 'Resample…', title: 'Make resized copies of the selected pictures: smaller (or larger) versions next to the originals or in a folder (I)', onclick: () => this.resampleDialog() }),
      el('button', { class: 'btn small', text: 'Rules…', title: 'Move files into folders by tag (Shift+M)', onclick: () => openRulesDialog(this) }),
      el('button', { class: 'btn small', text: 'Files ▾', title: 'Rename (F2), send to a quick destination (D), delete (Del), clean up duplicates', onclick: (e) => this.filesMenu(e.currentTarget) }),
      el('button', { class: 'btn small', text: 'Copy to…', title: 'Copy the selected files to any folder - the originals stay (K)', onclick: () => this.copyDialog() }),
      el('button', { class: 'btn small primary', text: 'Move to…', title: 'Move the selected files to a folder (M)', onclick: () => this.moveDialog() }));
    this.toastEl = el('div', { class: 'toast' });
    document.body.append(this.toastEl);
  }

  updateStatus() {
    this.stCount.textContent = `${fmtNum(this.items.ids.length)} items`;
    if (this._audioItems !== this.items) {   // the auto-play switch only shows where there are sounds
      this._audioItems = this.items;
      this._hasAudio = this.items.n.some((n) => AUDIO.has(extOf(n)));
    }
    this.autoPlayBtn.style.display = this._hasAudio ? '' : 'none';
    this.autoPlayBtn.textContent = this.autoPlay ? '♪ Auto-play on' : '♪ Auto-play off';
    this.autoPlayBtn.classList.toggle('on', this.autoPlay);
    const n = this.selected.size;
    let hidden = 0;
    if (n) for (const id of this.selected) if (!this.idIndex.has(id)) hidden++;
    this.stSel.textContent = n ? `${fmtNum(n)} selected${hidden ? ` · ${fmtNum(hidden)} hidden by filter` : ''}` : 'No selection';
    this.stSel.classList.toggle('has', n > 0);
    this.panel.showSelection();
  }

  updateScanStatus(s) {
    if (s.phase === 'walking') this.stScan.textContent = `Scanning folders… ${fmtNum(s.found)} files found`;
    else if (s.phase === 'processing') {
      const slow = s.slow ? ` · working on ${s.slow.name} (${s.slow.secs >= 60 ? `${Math.floor(s.slow.secs / 60)} min ` : ''}${s.slow.secs % 60} s)` : '';
      this.stScan.textContent = `Indexing ${fmtNum(s.processed)} / ${fmtNum(s.pending)}${s.rate ? ` · ${s.rate}/s` : ''}${s.errors ? ` · ${s.errors} unreadable` : ''}${slow}`;
      this.stScan.title = s.slow ? 'Big videos, 3D models and layered files can take a while; the count moves on when they are done' : '';
    }
    else if (s.phase === 'error') this.stScan.textContent = `Indexing error: ${s.error}`;
    else this.stScan.textContent = s.errors ? `${s.errors} unreadable files` : '';
    this.stScan.classList.toggle('busy', s.phase === 'walking' || s.phase === 'processing');
  }

  setOffline(off) { document.body.classList.toggle('offline', off); }

  toast(msg, ms = 1800) {
    this.toastEl.textContent = msg;
    this.toastEl.classList.add('show');
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toastEl.classList.remove('show'), ms);
  }

  // ------------------------------------------------------------- path bar
  crumbs(path) {
    const sep = this.state?.sep || '/';
    const out = [];
    if (sep === '/') {
      out.push({ name: '/', path: '/' });
      let acc = '';
      for (const part of path.split('/').filter(Boolean)) { acc += '/' + part; out.push({ name: part, path: acc }); }
    } else {
      const parts = path.split('\\').filter(Boolean);
      let acc = '';
      parts.forEach((part, i) => {
        acc = i === 0 ? part + '\\' : (acc.endsWith('\\') ? acc + part : acc + '\\' + part);
        out.push({ name: part, path: acc });
      });
    }
    return out;
  }

  /** A saved view stays highlighted until the filters are changed by hand. */
  checkView() {
    if (!this.activeView) return;
    const key = JSON.stringify(Object.entries(this.viewQuery()).filter(([, v]) => v !== '' && v != null && v !== false));
    if (this.activeViewKey == null) this.activeViewKey = key;
    else if (key !== this.activeViewKey) { this.activeView = null; this.sidebar.renderViews(); }
  }

  renderPathBar() {
    if (!this.pathbar || !this.sidebar) return;
    this.checkView();
    const q = this.query;
    const left = [...this.projects.pathBar()];
    if (left.length && q.folder) left.push(el('span', { class: 'sep', text: '›' }));
    if (q.folder) {
      const cs = this.crumbs(q.folder);
      cs.forEach((c, i) => {
        if (i) left.push(el('span', { class: 'sep', text: '›' }));
        left.push(el('button', { class: 'crumb' + (i === cs.length - 1 ? ' last' : ''), text: c.name, title: c.path, 'data-folder': c.path, onclick: () => this.openPath(c.path) }));
      });
    } else if (!this.projects.active) {
      left.push(el('span', { class: 'crumb last', text: 'All library items' }));
    }
    const filters = [];
    for (const id of (q.tag || '').split(',').filter(Boolean).map(Number)) {
      const t = this.tags.byId.get(id);
      filters.push(el('span', { class: 'chip small', style: { '--c': t?.color || '#888' } }, el('span', { text: t?.name || 'tag' }),
        el('span', { class: 'x', text: '×', title: 'Remove filter', onclick: () => this.setTagFilter(id, true) })));
    }
    for (const id of (q.exclude || '').split(',').filter(Boolean).map(Number)) {
      const t = this.tags.byId.get(id);
      filters.push(el('span', { class: 'chip small not', style: { '--c': t?.color || '#888' }, title: 'Hidden: items with this tag (Alt+click a tag)' },
        el('span', { text: `not ${t?.name || 'tag'}` }),
        el('span', { class: 'x', text: '×', title: 'Remove filter', onclick: () => this.setTagFilter(id, false, { exclude: true }) })));
    }
    const only = (q.tag || '').split(',').filter(Boolean).map(Number);
    const proj = this.projects.current;
    if (proj && this.selected.size) {
      filters.push(this.projects.mode === 'add'
        ? el('button', { class: 'btn small primary', text: `+ Add ${fmtNum(this.selected.size)} to ${proj.name}`, title: 'Add the selection to the project (+)', onclick: () => this.projects.addSelection('add') })
        : el('button', { class: 'btn small', text: `− Remove ${fmtNum(this.selected.size)} from ${proj.name}`, title: 'Take the selection out of the project (−). R drops them from the view; Ctrl+Z undoes.', onclick: () => this.projects.addSelection('remove') }));
    } else if (only.length === 1 && this.selected.size) {
      const t = this.tags.byId.get(only[0]);
      if (t) filters.push(el('button', { class: 'btn small', title: `Remove the selected items from ${t.name} (Ctrl+Z undoes). Press R afterwards to drop them from this view.`,
        text: `− Remove ${fmtNum(this.selected.size)} from ${t.name.split('/').pop()}`,
        onclick: () => this.applyTag(t.id, { op: 'remove', targets: [...this.selected] }).then(() => this.renderPathBar()) }));
    }
    if (q.similar) {
      const lvl = el('select', { title: 'How alike pictures must be' },
        [['4', 'nearly identical'], ['8', 'very similar'], ['12', 'similar']].map(([v, t]) => el('option', { value: v, text: t })));
      lvl.value = String(q.similar);
      lvl.onchange = () => { this.query.similar = +lvl.value; this.reload({ keepScroll: false }); };
      filters.push(el('span', { class: 'chip small' }, el('span', { text: `≈ Similar images${this.items.groups != null ? ` · ${fmtNum(this.items.groups)} groups` : ''}` }), lvl,
        el('span', { class: 'x', text: '×', onclick: () => this.setSimilar(0) })));
    }
    if (q.stack) filters.push(el('span', { class: 'chip small' }, el('span', { text: `▤ Versions of ${this.stackName || 'stack'}` }),
      el('span', { class: 'x', text: '×', onclick: () => { this.query.stack = ''; this.onFilterChanged(); } })));
    if (q.dupes) {
      filters.push(el('span', { class: 'chip small' }, el('span', { text: '⧉ Duplicates' }), el('span', { class: 'x', text: '×', onclick: () => this.setDupes(false) })),
        el('button', { class: 'btn small', text: 'Clean up…', title: 'Keep one copy of each file, move the others to the trash (Ctrl+Z undoes)', onclick: () => openDupesDialog(this) }));
    }
    if (q.untagged) filters.push(el('span', { class: 'chip small' }, el('span', { text: 'Untagged' }), el('span', { class: 'x', text: '×', onclick: () => this.setUntagged(false) })));
    if (q.missing) {
      const f = this.tags.facets.find((x) => x.key === q.missing);
      filters.push(el('span', { class: 'chip small' }, el('span', { text: f ? f.none : q.missing }), el('span', { class: 'x', text: '×', onclick: () => this.setMissing('') })));
    }
    const right = [];
    if (q.folder) {
      const lib = this.isLibraryPath(q.folder);
      const pinned = this.sidebar.isPinned(q.folder);
      if (!lib) {
        right.push(el('span', { class: 'muted small', text: 'Browsing (not in library)' }),
          el('button', { class: 'btn small', text: '+ Add to library', title: 'Index this folder and all its subfolders', onclick: () => this.sidebar.addRoot(q.folder) }));
      }
      right.push(el('button', { class: 'btn small' + (pinned ? ' on' : ''), text: pinned ? '★ Pinned' : '☆ Pin', title: 'Pin this folder in the sidebar', onclick: () => this.sidebar.togglePin(q.folder) }));
      const parent = this.parentOf(q.folder);
      if (parent) right.push(el('button', { class: 'btn small', text: '↑', title: 'Parent folder (Backspace)', onclick: () => this.openPath(parent) }));
    }
    if (this.lookFilter?.active.length) filters.push(el('span', { class: 'chip small', title: 'Look filter: colours, shape, size, sound tempo/key/length' },
      el('span', { text: `◐ ${this.lookFilter.summary()}` }), el('span', { class: 'x', text: '×', onclick: () => this.lookFilter.clear() })));
    if (q.suggested) filters.push(el('span', { class: 'chip small ai' }, el('span', { text: 'AI suggestions' }), el('span', { class: 'x', text: '×', onclick: () => this.setSuggestedFilter(false) })));
    if (this.aiView) filters.push(el('span', { class: 'chip small ai' }, el('span', { text: this.aiView.kind === 'search' ? `AI search: ${this.aiView.q}` : `Similar to ${this.aiView.name}` }),
      el('span', { class: 'x', text: '×', onclick: () => this.setAIView(null) })));
    right.push(el('button', { class: 'btn small', text: '☆ Save view', title: 'Remember these filters (folder, tags, types, project, sort) in the sidebar', onclick: () => this.saveView() }));
    if (filters.length || q.q || q.missing || q.exclude || q.dupes || q.similar || q.stack) right.push(el('button', { class: 'btn small', text: 'Clear filters', onclick: () => this.clearFilters() }));
    right.push(el('button', { class: 'btn small', text: '⟳', title: 'Refresh the list (R) - drops items that no longer match the filter', onclick: () => this.reload() }));
    this.pathbar.replaceChildren(el('div', { class: 'crumbs' }, ...left), ...filters, el('div', { class: 'spacer' }), ...right);
  }

  // ---------------------------------------------------------------- help
  buildHelp() {
    const rows = [
      ['Browsing', ''],
      ['← → ↑ ↓, PgUp/PgDn, Home/End', 'Move focus (selection follows only when one item is selected)'],
      ['Ctrl + arrows', 'Move focus without touching the selection'],
      ['Space (tap / hold)', 'Quick look: tap to open, hold to peek and release to return'],
      ['Enter, double-click', 'Open viewer'],
      ['O', 'Open the file in its application (Krita, Photoshop, audio editor…)'],
      ['Click / arrows on a sound', 'Plays it (auto-play; switch with P or the ♪ button in the status bar)'],
      ['Space · ▶ on a sound', 'Play / stop (▶ doesn\'t change the selection)'],
      ['Ctrl + wheel, + / −', 'Thumbnail size (+ / − add to / remove from a project while one is open)'],
      ['Mouse over a video', 'Runs through its frames'],
      ['/  or  Ctrl+F', 'Search names'],
      ['Selecting', ''],
      ['Click / Ctrl+click / Shift+click', 'Select one / toggle / range'],
      ['Drag on the grid', 'Rectangle select (hold Ctrl or Shift to add)'],
      ['Drag selected images', 'Drop them on a tag or project in the sidebar or tag bar to add it'],
      ['X  or  Ctrl+Space', 'Toggle the focused item'],
      ['Shift + arrows', 'Extend selection'],
      ['Ctrl+A / Ctrl+I / Esc', 'Select all / invert / clear'],
      ['Ctrl+Z / Ctrl+Y', 'Undo / redo: selection, tags and moves (undoing a copy deletes the copies)'],
      ['Tagging & sorting', ''],
      ['1 … 9', 'Add/remove the tag on that hotkey (selection, or the focused image). A project can have its own keys: Edit… on the project'],
      ['0  or  Q', 'Add/remove the last tag you used again (shown first in the tag bar)'],
      ['T', 'Tag with any name (creates the tag if new)'],
      ['Projects', 'Pick one top left (or click it in the sidebar) to work inside it'],
      ['+ · −', 'With a project open: add the selection · take it out'],
      ['Export… (path bar)', 'Copy the open project into a Unity / Godot / Unreal folder, models with their textures'],
      ['☆ Save view', 'Remember the current filters under a name (sidebar: Saved views)'],
      ['Duplicates (sidebar)', 'Files whose exact content is in more than one place · Clean up… keeps one of each'],
      ['Similar images (sidebar)', 'Groups of pictures that look nearly the same'],
      ['Look ▾ (top bar)', 'Filter by colour, shape, transparency, size; sounds by tempo, key and length (with Playhead also by character: hit or swell, bursty, metal-like, long ring…)'],
      ['Timeline (right edge)', 'When sorted by date: drag along it to jump through years and months'],
      ['J', 'Join the selected images (side by side, stacked, grid), contact sheet, sprite sheet or texture atlas'],
      ['I', 'Resample: resized copies of the selected pictures (percent, longest side, fit, exact; pixel-art friendly)'],
      ['Ctrl+G · Ctrl+Shift+G', 'Stack the selection as versions (newest on top) · unstack'],
      ['▤ on a thumbnail', 'A version stack: click to see all versions'],
      ['C', 'Compare 2–4 selected images side by side, zooming together (S in compare: swipe A/B)'],
      ['Click a tag', 'Everything with it (in the whole library, or in the open project)'],
      ['Ctrl+click · Alt+click', 'Narrow the current view (e.g. a tag in this folder) · hide items with it'],
      ['M', 'Move selection to a folder (with preview, keeps dates, undoable)'],
      ['K', 'Copy selection to any folder (keeps dates; in the library the copies keep their tags; for delivery: private details out, author and licence in, zip; undoable)'],
      ['D, then 1 … 9', 'Send the selection to a quick destination (copy or move; global or per project; E in the menu edits them)'],
      ['F2', 'Rename: a pattern with {date} {tag} {n}…, find / replace, case; sidecars follow; undoable'],
      ['Del', 'Delete to the Recycle Bin / Trash, or Kurator\'s trash folder (⚙ Settings → Delete); Ctrl+Z brings files back'],
      ['Browse… (destination fields)', 'Click through folders, make a new one, or pick a recent destination'],
      ['Drop files from Explorer / Finder', 'Onto a tag or project: they get it; onto the grid: the open project and filtered tags. Files already in the library are tagged in place, new ones copied into a folder you choose. Ctrl+Z undoes'],
      ['Add… · Open… (sidebar)', 'With the field empty: click through folders to add to the library / to open; a typed or pasted path is used at once'],
      ['B', 'Backup (sets, plan with free space, restore)'],
      ['AI', ''],
      ['?text in the search box', 'Search by description, e.g. ?robot with a sword'],
      ['S', 'Find images (or sounds) similar to the focused one'],
      ['A · Shift+A', 'Accept · reject the AI suggestions of the selection'],
      ['Shift+M', 'Move rules: tag → folder, e.g. Creatures → Art\\Creatures\\{year}'],
      ['R', 'Refresh: drop items that no longer match the tag filter'],
      ['Backspace', 'Parent folder'],
      ['Viewer', ''],
      ['← →', 'Previous / next (selection is kept)'],
      ['X', 'Toggle selection of the shown image'],
      ['1 … 9, T, Q', 'Tag the shown image (Q: the last tag again)'],
      ['Wheel, drag, double-click', 'Zoom at cursor, pan, toggle fit/100%'],
      ['3D models', 'Drag: turn · right-drag or Shift+drag: move · wheel: zoom · 0 or double-click: reset · in the grid: hover to turn'],
      ['3D: L · Alt+drag', 'Next light preset · turn the lights (view modes and maps: panel top right)'],
      ['L on 2D art', 'Lit preview with its _normal / _glow / _depth maps: the light follows the mouse'],
      ['I', 'Inks: C, M, Y, K plates, total ink and ink limit (CMYK pictures, or RGB with a proofing profile)'],
      ['[ ]  PgUp PgDn', 'Pages of a PDF (in the viewer); zooming in renders the page sharper'],
      ['Videos', 'Space: play / pause · Shift+← →: 5 s back / forward · O: open in your player'],
      ['Sounds', 'Click the waveform to jump · wheel: zoom · Shift+wheel: move along · Space: play / pause · the menus switch waveform styles and row height (long sounds wrap onto rows)'],
      ['Sounds: drag · E', 'Select a region (Space plays it) · save the region as a new file'],
      ['0 / Z', 'Fit / toggle 100%'],
      ['B', 'Background: dark, gray, light, checkerboard'],
      ['N', 'Pixel-art mode (sharp pixels when zoomed)'],
      ['Esc / Enter / Space', 'Close'],
    ];
    const tbl = el('table', {}, rows.map(([a, b]) => b === ''
      ? el('tr', { class: 'h' }, el('td', { colspan: '2', text: a }))
      : el('tr', {}, el('td', {}, el('kbd', { text: a })), el('td', { text: b }))));
    const link = (href, text) => el('a', { href, text, target: '_blank', rel: 'noopener noreferrer' });
    this.aboutEl = el('div', { class: 'about' });
    this.helpEl = el('div', { class: 'help', onclick: (e) => { if (e.target === this.helpEl) this.toggleHelp(false); } },
      el('div', { class: 'help-box' }, el('h2', { text: 'Keyboard & mouse' }), tbl, this.aboutEl,
        el('div', { class: 'about-links' }, 'by Daniel Sandner · ', link('https://eigensound.com/', 'eigensound.com'), ' · ',
          link('https://sandner.art/', 'sandner.art'), ' · ', link('https://github.com/eigensoundlab/kurator', 'GitHub'),
          ' · Apache License 2.0'),
        el('button', { class: 'btn', text: 'Close', onclick: () => this.toggleHelp(false) })));
    document.body.append(this.helpEl);
  }

  toggleHelp(open) {
    if (open) {
      const w = this.state?.watch;
      this.aboutEl.textContent = `Kurator ${this.state?.version || ''} · live folder watching: ${w?.enabled ? `on (${w.watching} folders)` : 'off - use Rescan'}`;
    }
    this.helpEl.classList.toggle('open', open);
  }
}

window.kurator = new App();
