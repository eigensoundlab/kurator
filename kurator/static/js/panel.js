// Right-hand panel: preview + metadata of the focused item, and a summary of the selection.

import { api, originalUrl, thumbUrl } from './api.js';
import { el, extOf, setKids, fmtBytes, fmtDate, fmtDuration, fmtNum, SOURCE_LABEL, VIEWABLE } from './util.js';

const MAX_PRE = 20000;

function pre(text) {
  const t = String(text);
  return el('pre', {}, t.length > MAX_PRE ? t.slice(0, MAX_PRE) + `\n… (${fmtNum(t.length - MAX_PRE)} more characters)` : t);
}

function kvTable(obj) {
  const rows = Object.entries(obj).map(([k, v]) =>
    el('tr', {}, el('th', { text: k }), el('td', { text: Array.isArray(v) ? v.join(', ') : String(v) })));
  return el('table', { class: 'kv' }, rows);
}

function section(title, body, open = false) {
  return el('details', { class: 'sec', open }, el('summary', { text: title }), body);
}

export class Panel {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.selBox = el('div', { class: 'p-selection' });
    this.preview = el('img', { class: 'p-preview', alt: '', draggable: 'false' });
    this.previewBox = el('div', { class: 'p-preview-box', title: 'Open viewer (Enter)', onclick: () => app.openViewer() }, this.preview);
    this.body = el('div', { class: 'p-body' });
    this.root.append(this.selBox, this.previewBox, this.body);
    this.ctrl = null;
    this.timer = 0;
    this.shownId = null;
  }

  /** Called whenever focus changes; fetch is debounced so arrow-key browsing stays smooth. */
  show(i) {
    const it = this.app.items;
    clearTimeout(this.timer);
    if (!it || i < 0 || i >= it.ids.length) {
      this.shownId = null;
      this.preview.removeAttribute('src');
      this.previewBox.classList.add('empty');
      this.body.replaceChildren(el('div', { class: 'p-hint', text: 'Nothing focused. Click an image, or use the arrow keys.' }));
      return;
    }
    this.previewBox.classList.remove('empty');
    this.previewBox.classList.toggle('alpha', !!it.a[i]);
    const c = it.c[i];
    this.previewBox.style.backgroundColor = c >= 0 && !it.a[i] ? `#${c.toString(16).padStart(6, '0')}` : '';
    // Show the grid thumbnail instantly (already cached), then upgrade to 512.
    const small = thumbUrl(it, i, 256);
    if (this.preview.getAttribute('src') !== small) this.preview.src = small;
    const id = it.ids[i];
    this.timer = setTimeout(() => this.load(id, i), 90);
  }

  async load(id, i) {
    if (this.ctrl) this.ctrl.abort();
    this.ctrl = new AbortController();
    const it = this.app.items;
    const big = new Image();
    big.onload = () => { if (this.shownId === id) this.preview.src = big.src; };
    // Animated files play in the panel (original file); others get the 512px preview.
    const animated = it.f?.[i] && VIEWABLE.has(extOf(it.n[i])) && it.v[i] !== 'e';
    big.src = animated ? originalUrl(it, i) : thumbUrl(it, i, 512);
    this.shownIndex = i;
    this.shownId = id;
    let d;
    try { d = await api.item(id, this.ctrl.signal); } catch (e) { if (e.name !== 'AbortError') this.body.replaceChildren(el('div', { class: 'p-hint', text: String(e.message) })); return; }
    if (this.shownId !== id) return;
    this.render(d);
  }

  render(d) {
    const [trust, why] = SOURCE_LABEL[d.date_source] || ['guess', d.date_source];
    const m = d.meta || {};
    const plat = navigator.userAgent;
    const revealLabel = /Windows/.test(plat) ? 'Show in Explorer' : /Mac/.test(plat) ? 'Show in Finder' : 'Open folder';
    this.tagBox = el('div', { class: 'p-tags' });
    this.detail = d;
    const parts = [
      el('div', { class: 'p-name', text: d.name }),
      el('div', { class: 'p-dir', title: d.dir, text: d.dir }),
      el('div', { class: 'p-actions' },
        el('button', { class: 'btn small', text: 'Open', title: 'Open in its application (O)', onclick: () => api.open(d.id).catch((e) => this.app.toast(e.message)) }),
        el('button', { class: 'btn small', text: revealLabel, onclick: () => api.reveal(d.id).catch((e) => this.app.toast(e.message)) }),
        el('button', { class: 'btn small', text: 'Copy path', onclick: () => navigator.clipboard?.writeText(d.path).then(() => this.app.toast('Path copied')) }),
        el('button', { class: 'btn small', text: 'Open folder here', title: 'Show this folder in the grid', onclick: () => this.app.openPath(d.dir) })),
      m.audio ? this.player(d) : null,
      this.tagBox,
      el('table', { class: 'kv facts' },
        el('tr', {}, el('th', { text: 'Date' }), el('td', {}, fmtDate(d.date_ts), ' ',
          el('span', { class: `src src-${trust}`, title: why, text: trust === 'embedded' ? 'in file' : trust }))),
        m.audio
          ? el('tr', {}, el('th', { text: 'Length' }), el('td', { text: `${fmtDuration(m.duration)}${m.samplerate ? ` · ${(m.samplerate / 1000).toFixed(1)} kHz` : ''}${m.channels ? ` · ${m.channels === 1 ? 'mono' : m.channels === 2 ? 'stereo' : m.channels + ' ch'}` : ''}${m.bitdepth ? ` · ${m.bitdepth} bit` : ''}${m.bitrate && !m.bitdepth ? ` · ${Math.round(m.bitrate)} kbps` : ''}` }))
          : m.model?.triangles != null
            ? el('tr', {}, el('th', { text: 'Geometry' }), el('td', { text: `${fmtNum(m.model.triangles)} triangles · ${fmtNum(m.model.vertices)} vertices` }))
            : el('tr', {}, el('th', { text: 'Size' }), el('td', { text: d.width ? `${d.width} × ${d.height} px` : (m.canvas ? `${m.canvas} px` : '—') })),
        m.video_info?.duration ? el('tr', {}, el('th', { text: 'Length' }), el('td', { text: [fmtDuration(m.video_info.duration),
          m.video_info.fps ? `${m.video_info.fps} fps` : null, m.video_info.video_codec?.toUpperCase(),
          m.video_info.audio_codec ? `${m.video_info.audio_codec.toUpperCase()} audio` : 'no audio'].filter(Boolean).join(' · ') })) : null,
        m.preview_note ? el('tr', {}, el('th', { text: 'Preview' }), el('td', { class: 'muted', text: m.preview_note })) : null,
        m.model?.dimensions?.length === 3 ? el('tr', {}, el('th', { text: 'Dimensions' }), el('td', { text: `${m.model.dimensions.join(' × ')}${m.model.unit ? ' ' + ({ millimeter: 'mm', centimeter: 'cm', meter: 'm', inch: 'in' }[m.model.unit] || m.model.unit) : ''}` })) : null,
        m.pages ? el('tr', {}, el('th', { text: 'Pages' }), el('td', { text: String(m.pages) })) : null,
        m.peak_dbfs != null ? el('tr', {}, el('th', { text: 'Level' }), el('td', { text: `peak ${m.peak_dbfs} dBFS · RMS ${m.rms_dbfs ?? '—'} dBFS` })) : null,
        m.ucs_catid ? el('tr', {}, el('th', { text: 'UCS' }), el('td', { text: m.ucs_catid, title: 'Universal Category System CatID from the file name' })) : null,
        (m.placeholder || m.preview) && !m.preview_note && !m.video ? el('tr', {}, el('th', { text: 'Preview' }), el('td', { class: 'muted', text: m.placeholder ? 'not available for this format - use Open' : m.preview === 'windows' ? 'from Windows (installed app)' : m.preview === 'rendered' ? 'rendered by Kurator - Enter for the 3D view' : 'stored in the file' })) : null,
        d.twins?.length ? el('tr', {}, el('th', { text: 'Also in', title: 'The same file (identical content) in other places' }),
          el('td', {}, d.twins.slice(0, 6).map((t) => el('div', {}, el('a', { href: '#', class: 'twin', title: t.path, text: t.path,
            onclick: (e) => { e.preventDefault(); this.app.openPath(t.path.replace(/[\\/][^\\/]*$/, '')); } }))),
          d.twins.length > 6 ? el('div', { class: 'muted small', text: `… ${d.twins.length - 6} more` }) : null)) : null,
        el('tr', {}, el('th', { text: 'File' }), el('td', { text: `${fmtBytes(d.size)} · ${(m.format || d.ext.slice(1)).toUpperCase()}${m.mode ? ' · ' + m.mode : ''}${m.frames ? ` · ${m.frames} frames` : ''}` })),
        d.alpha ? el('tr', {}, el('th', { text: 'Alpha' }), el('td', { text: 'transparent pixels' })) : null,
        d.status === 2 ? el('tr', {}, el('th', { text: 'Error' }), el('td', { class: 'err', text: d.error || 'cannot read' })) : null,
        d.status === 0 ? el('tr', {}, el('th', { text: 'Status' }), el('td', { text: 'waiting for indexing' })) : null),
    ];
    if (m.generation) {
      const g = m.generation;
      const body = el('div', {}, el('div', { class: 'p-tool', text: g.tool }));
      if (g.parameters) body.append(pre(g.parameters));
      if (g.description) body.append(pre(g.description));
      if (g.prompt) body.append(section('Prompt graph (JSON)', pre(JSON.stringify(g.prompt, null, 1))));
      if (g.workflow) body.append(section(`Workflow (${(g.workflow.nodes || []).length} nodes)`, pre(JSON.stringify(g.workflow, null, 1))));
      parts.push(section('Generation', body, true));
    }
    if (m.document) parts.push(section('Document', kvTable(m.document), true));
    if (m.video_info) {
      const vi = { ...m.video_info };
      for (const k of ['duration', 'width', 'height', 'fps', 'video_codec', 'audio_codec']) delete vi[k];
      if (vi.bitrate_kbps) { vi.bitrate = `${fmtNum(vi.bitrate_kbps)} kbps`; delete vi.bitrate_kbps; }
      if (vi.audio_rate) { vi.audio_rate = `${(vi.audio_rate / 1000).toFixed(1)} kHz`; }
      if (Object.keys(vi).length) parts.push(section('Video', kvTable(vi), true));
    }
    if (m.model) {
      const mm = { ...m.model };
      delete mm.triangles; delete mm.vertices; delete mm.dimensions; delete mm.unit;
      if (mm.compression) mm.compression = `${mm.compression} (compressed .blend)`;
      if (mm.backup) mm.backup = 'Blender backup file (.blend1)';
      if (Object.keys(mm).length) parts.push(section('3D model', kvTable(mm), true));
    }
    const info = {};
    for (const k of ['title', 'artist', 'album', 'albumartist', 'genre', 'composer', 'year', 'bpm', 'key', 'initial_key', 'comment', 'copyright', 'publisher', 'author', 'creator', 'producer', 'editor']) if (m[k]) info[k] = m[k];
    if (Object.keys(info).length) parts.push(section('Info', kvTable(info), true));
    if (m.bwf) parts.push(section('Broadcast WAV', kvTable(m.bwf), true));
    if (d.captions?.length) parts.push(section('Caption (AI)', el('div', {}, d.captions.map((c) => el('div', { class: 'caption' }, el('div', { text: c.text }), el('div', { class: 'muted small', text: c.model })))), true));
    if (m.ixml) parts.push(section('iXML', pre(m.ixml)));
    if (m.exif) parts.push(section(`EXIF (${Object.keys(m.exif).length})`, kvTable(m.exif)));
    if (m.text) {
      const t = { ...m.text };
      if (m.generation) { delete t.parameters; delete t.prompt; delete t.workflow; }
      if (Object.keys(t).length) parts.push(section('Text chunks', kvTable(t)));
    }
    if (m.xmp) parts.push(section('XMP', pre(m.xmp)));
    this.relBox = el('div');
    parts.splice(parts.indexOf(this.tagBox) + 1, 0, this.relBox);   // texture set / what a model uses, under the tags
    setKids(this.body, ...parts);
    this.loadRelated(d);
    this.renderTags();
  }

  /** Texture maps of the same set, the files a model uses, models using a texture. */
  async loadRelated(d) {
    let r;
    try { r = await api.related(d.id); } catch { return; }
    if (this.detail !== d || !this.relBox) return;
    const app = this.app;
    const go = (id, name) => {
      if (id == null) return;
      const i = app.indexOf(id);
      if (i >= 0) app.setFocus(i); else app.toast(`${name} isn't in the current view`);
    };
    const chip = (x, label) => el('button', { class: 'chip small' + (x.id === d.id ? ' full' : ''), type: 'button', title: x.name,
      disabled: x.id == null, onclick: () => go(x.id, x.name) }, el('span', { text: label }));
    const rows = [];
    if (r.set) rows.push(el('div', { class: 'rel' }, el('span', { class: 'tb-group', text: `Texture set "${r.set.base}"` }),
      r.set.maps.map((m) => chip(m, m.role))));
    if (r.uses?.length) rows.push(el('div', { class: 'rel' }, el('span', { class: 'tb-group', text: 'Uses' }),
      r.uses.map((u) => chip(u, u.role ? `${u.role}: ${u.name}` : u.name))));
    if (r.models?.length) rows.push(el('div', { class: 'rel' }, el('span', { class: 'tb-group', text: 'Used by' }),
      r.models.map((m) => chip(m, m.name))));
    setKids(this.relBox, rows);
  }

  /** Small player for sounds: play/stop, seek, time. One shared audio element (app.audio). */
  player(d) {
    const app = this.app;
    const playable = VIEWABLE.has(extOf(d.name));
    this.pBtn = el('button', { class: 'btn small playbtn', text: '▶', title: 'Play / stop (Space)', disabled: !playable,
      onclick: () => { const i = app.indexOf(d.id); if (i >= 0) app.togglePlay(i); } });
    this.pSeek = el('input', { type: 'range', min: '0', max: '1000', value: '0', class: 'seek', disabled: !playable });
    this.pSeek.oninput = () => {
      if (app.playingId !== d.id) { const i = app.indexOf(d.id); if (i >= 0) app.togglePlay(i); }
      if (app.audio.duration) app.audio.currentTime = (this.pSeek.value / 1000) * app.audio.duration;
    };
    this.pTime = el('span', { class: 'muted small ptime', text: fmtDuration(d.meta.duration) });
    this.pId = d.id;
    this.pDur = d.meta.duration;
    this.paintPlayer();
    return el('div', { class: 'p-player' }, this.pBtn, this.pSeek, this.pTime,
      playable ? null : el('span', { class: 'muted small', text: 'plays in your audio app (Open)' }));
  }

  paintPlayer() {
    if (!this.pBtn || this.pId == null) return;
    const app = this.app, mine = app.playingId === this.pId, a = app.audio;
    this.pBtn.textContent = mine && !a.paused ? '■' : '▶';
    if (mine && a.duration) {
      this.pSeek.value = String(Math.round((a.currentTime / a.duration) * 1000));
      this.pTime.textContent = `${fmtDuration(a.currentTime)} / ${fmtDuration(a.duration)}`;
    } else if (!mine) {
      this.pSeek.value = '0';
      if (this.pDur != null) this.pTime.textContent = fmtDuration(this.pDur);
    }
  }

  /** Tag chips of the shown item (from the live client data, so they update instantly). */
  renderTags() {
    if (!this.tagBox || !this.detail) return;
    const app = this.app;
    const i = app.indexOf(this.detail.id);
    const ts = i >= 0 ? (app.items.t?.[i] || []) : this.detail.tags;
    const srcs = this.detail.tag_sources || {};
    const chips = app.tags.sortIds(ts || []).map((t) => {
      const tag = app.tags.byId.get(t);
      const src = srcs[String(t)];
      const isAI = src && src[0] === 'ai';
      return el('span', { class: 'chip small' + (isAI ? ' ai' : ''), style: { '--c': tag?.color || '#888' },
        title: `${app.tags.facetLabel(tag?.facet)}: ${tag?.name}${isAI ? ` · accepted AI suggestion (${Math.round((src[1] || 0) * 100)}%)` : ''}` },
        el('span', { text: tag?.name || '?' }),
        el('span', { class: 'x', title: 'Remove tag', text: '×', onclick: () => app.applyTag(t, { op: 'remove', targets: [this.detail.id] }) }));
    });
    const sg = i >= 0 ? (app.items.s?.[i] || []) : [];
    const sug = sg.map(([t, score]) => {
      const tag = app.tags.byId.get(t);
      return el('span', { class: 'chip small sugg', style: { '--c': tag?.color || '#888' }, title: 'AI suggestion' },
        el('span', { text: `${tag?.name || '?'} ${Math.round(score * 100)}%` }),
        el('span', { class: 'ok-x', text: '✓', title: 'Accept', onclick: () => api.aiAccept({ ids: [this.detail.id], tag_id: t }).then((r) => app.onAIAccepted(r)) }),
        el('span', { class: 'x', text: '✗', title: 'Reject', onclick: () => api.aiReject({ ids: [this.detail.id], tag_id: t }).then(() => app.reload()) }));
    });
    setKids(this.tagBox, ...chips,
      el('button', { class: 'chip small add', text: '+ tag', title: 'Add a tag to this image', onclick: () => app.tags.quickTag([this.detail.id]) }),
      ...(sug.length ? [el('span', { class: 'tb-group', text: 'AI suggests' }), ...sug] : []),
      this.detail.kind === 'image' && !this.detail.meta?.placeholder
        ? el('button', { class: 'chip small add', text: '≈ similar', title: 'Find similar images (S)', onclick: () => app.setAIView({ kind: 'similar', id: this.detail.id, name: this.detail.name }) })
        : null);
  }

  /** Selection summary on top of the panel. */
  showSelection() {
    const app = this.app;
    const n = app.selected.size;
    if (n < 2) { this.selBox.replaceChildren(); this.selBox.style.display = 'none'; return; }
    this.selBox.style.display = '';
    const it = app.items;
    let lo = Infinity, hi = -Infinity, visible = 0;
    for (const id of app.selected) {
      const i = app.indexOf(id);
      if (i < 0) continue;
      visible++;
      const ts = it.d[i];
      if (ts) { if (ts < lo) lo = ts; if (ts > hi) hi = ts; }
    }
    const strip = el('div', { class: 'sel-strip' });
    let k = 0;
    for (const id of app.selected) {
      const i = app.indexOf(id);
      if (i < 0) continue;
      strip.append(el('img', { src: thumbUrl(it, i, 256), alt: '', title: it.n[i], onclick: () => app.setFocus(i, { scroll: true }) }));
      if (++k >= 12) break;
    }
    setKids(this.selBox,
      el('div', { class: 'p-selhead' }, el('b', { text: `${fmtNum(n)} selected` }),
        visible < n ? el('span', { class: 'muted', text: ` · ${fmtNum(n - visible)} hidden by filter` }) : null),
      lo <= hi ? el('div', { class: 'muted', text: lo === hi ? fmtDate(lo) : `${fmtDate(lo)} – ${fmtDate(hi)}` }) : null,
      strip);
  }
}
