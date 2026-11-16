// Recording plans: what to record for a project, taken to a phone as a link or QR code. The phone opens
// it in Kurator Capture (sounds, photos, textures) or Ballistic Foley Lab's Record view; what comes back
// (a session zip or a backup folder in the library, see kurator/sessions.py) counts against the shots.
// The plan travels in the part of the link after "#plan=", which browsers never send to the web server.

import { api } from './api.js';
import { ask, openDialog } from './dialog.js';
import { qrSvg } from './qr.js';
import { el, fmtNum } from './util.js';

const DEFAULT_URL = 'https://eigensoundlab.github.io/ballistic/';
const CAPTURE_URL = 'https://eigensoundlab.github.io/kurator-capture/';
const KIND_KEY = { sound: 's', photo: 'p', texture: 't' };
const tagList = (s) => String(s || '').split(/[,;]+/).map((t) => t.trim()).filter(Boolean);
const newId = () => 'plan-' + Date.now().toString(36);

/** The plan as Ballistic's #plan= value (the same as plans.pack in Python and packPlan in Ballistic). */
function pack(p) {
  const o = { v: 1, i: p.id, n: p.name };
  if (p.project) o.p = p.project;
  if (p.chips?.length) o.c = p.chips;
  o.s = p.shots.map((s) => {
    const x = { i: s.id, n: s.name };
    if (s.tags?.length) x.t = s.tags;
    if (s.takes > 1) x.k = s.takes;
    if (s.ucs) x.u = s.ucs;
    if (s.notes) x.o = s.notes;
    if (KIND_KEY[s.kind]) x.m = KIND_KEY[s.kind];
    return x;
  });
  let bin = '';
  for (const b of new TextEncoder().encode(JSON.stringify(o))) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Open the plans dialog; `project` (a project tag's name) starts a new plan for it when it has none. */
export async function openPlansDialog(app, { project = '' } = {}) {
  let list, address, capture;
  try { ({ plans: list, address, capture } = await api.plans()); } catch (e) { app.toast(e.message); return; }
  address = (address || DEFAULT_URL).split('#')[0];
  capture = (capture || CAPTURE_URL).split('#')[0];
  // which app the phone opens: Kurator Capture for photos and textures, either for sounds
  const appSel = el('select', { 'aria-label': 'Record with' },
    el('option', { value: 'capture', text: 'Kurator Capture (sounds, photos, textures)' }), el('option', { value: 'ballistic', text: 'Ballistic Foley Lab (sounds)' }));
  try { appSel.value = localStorage.getItem('kurator-plan-app') || 'capture'; } catch { appSel.value = 'capture'; }
  let cur = null;
  const blank = () => ({ id: newId(), name: project || 'New plan', project, chips: ['Nature', 'Objects', 'Ambience', 'Foley'],
    shots: [{ id: 's1', name: 'First shot', tags: [], takes: 3, ucs: '', notes: '', kind: '' }], progress: {}, fresh: true });
  const pick = project ? list.find((p) => p.project === project) : list[0];
  cur = pick ? structuredClone(pick) : blank();

  const sel = el('select', { 'aria-label': 'Plan' });
  const name = el('input', { type: 'text', spellcheck: 'false' });
  const proj = el('input', { type: 'text', spellcheck: 'false', list: 'plan-projects' });
  const chips = el('input', { type: 'text', spellcheck: 'false' });
  const projects = el('datalist', { id: 'plan-projects' },
    ...[...app.tags.byId.values()].filter((t) => t.facet === 'project' && !t.name.includes('/')).map((t) => el('option', { value: t.name })));
  const subjects = el('datalist', { id: 'plan-tags' },
    ...[...app.tags.byId.values()].filter((t) => (t.facet || 'subject') === 'subject').map((t) => el('option', { value: t.name })));
  const shots = el('div', { class: 'plan-shots' });
  const qr = el('div', { class: 'plan-qr', role: 'img', 'aria-label': 'QR code of the plan link' });
  const link = el('input', { type: 'text', readonly: true, spellcheck: 'false', 'aria-label': 'Plan link' });
  const msg = el('div', { class: 'p-hint small' });

  const refreshSelect = () => {
    sel.replaceChildren(...list.map((p) => el('option', { value: p.id, text: p.name })), ...(cur.fresh ? [el('option', { value: cur.id, text: cur.name + ' (new)' })] : []));
    sel.value = cur.id;
  };
  const drawLink = () => {
    const url = (appSel.value === 'ballistic' ? address : capture) + '#plan=' + pack(cur);
    link.value = url;
    const svg = qrSvg(url, '#000');
    if (svg) qr.innerHTML = svg; else qr.textContent = 'Too long for a QR code: send the link instead.';
  };
  const shotRow = (s, i) => {
    const n = el('input', { type: 'text', value: s.name, spellcheck: 'false', 'aria-label': 'Shot' });
    const t = el('input', { type: 'text', value: s.tags.join(', '), spellcheck: 'false', list: 'plan-tags', 'aria-label': 'Tags of the shot', placeholder: 'Nature/Birds' });
    const k = el('input', { type: 'text', inputmode: 'numeric', value: String(s.takes), 'aria-label': 'Takes' });
    const u = el('input', { type: 'text', value: s.ucs, spellcheck: 'false', 'aria-label': 'UCS category', placeholder: 'UCS' });
    const m = el('select', { 'aria-label': 'What the shot is' }, ...[['', 'Any'], ['sound', 'Sound'], ['photo', 'Photo'], ['texture', 'Texture']].map(([v, t]) => el('option', { value: v, text: t })));
    m.value = s.kind || '';
    m.onchange = () => { s.kind = m.value; drawLink(); };
    const done = cur.progress?.[s.id] || 0;
    n.oninput = () => { s.name = n.value.trim() || s.name; drawLink(); };
    t.onchange = () => { s.tags = tagList(t.value); drawLink(); };
    k.onchange = () => { s.takes = Math.max(1, Math.min(99, Math.round(+k.value) || 1)); k.value = String(s.takes); drawLink(); };
    u.onchange = () => { s.ucs = u.value.replace(/[^A-Za-z]/g, '').slice(0, 20); u.value = s.ucs; drawLink(); };
    return el('div', { class: 'plan-shot' }, n, m, t, k, u,
      el('span', { class: 'plan-prog' + (done >= s.takes ? ' done' : ''), text: `${fmtNum(done)} of ${fmtNum(s.takes)}`, title: 'Takes that came back in recording sessions' }),
      el('button', { class: 'btn small', type: 'button', text: '×', title: 'Remove the shot', 'aria-label': 'Remove ' + s.name, onclick: () => { cur.shots.splice(i, 1); drawShots(); } }));
  };
  const drawShots = () => {
    shots.replaceChildren(el('div', { class: 'plan-shot head' }, ...['Shot', 'What', 'Tags', 'Takes', 'UCS', 'Done', ''].map((h) => el('span', { text: h }))),
      ...cur.shots.map(shotRow));
    drawLink();
  };
  const load = (p) => {
    cur = structuredClone(p);
    name.value = cur.name; proj.value = cur.project; chips.value = cur.chips.join(', ');
    refreshSelect(); drawShots();
    msg.textContent = '';
  };
  name.oninput = () => { cur.name = name.value.trim() || 'Plan'; drawLink(); };
  proj.oninput = () => { cur.project = proj.value.trim(); drawLink(); };
  chips.onchange = () => { cur.chips = tagList(chips.value); drawLink(); };
  sel.onchange = () => { const p = list.find((x) => x.id === sel.value); if (p) load(p); };
  appSel.onchange = () => { try { localStorage.setItem('kurator-plan-app', appSel.value); } catch { /* this visit */ } drawLink(); };

  const save = async () => {
    try {
      const r = await api.savePlan({ id: cur.id, name: cur.name, project: cur.project, chips: cur.chips, shots: cur.shots });
      list = list.filter((p) => p.id !== r.id); list.unshift(r);
      load(r);
      msg.textContent = 'Saved.';
      return true;
    } catch (e) { msg.textContent = e.message; return false; }
  };
  const fromLink = () => {
    const inp = el('input', { type: 'text', spellcheck: 'false', placeholder: CAPTURE_URL + '#plan=…' });
    const err = el('div', { class: 'p-hint small' });
    openDialog({
      title: 'A plan from a link', width: 520,
      body: el('div', { class: 'form' }, el('label', {}, 'Paste a plan link (from Kurator Capture or Ballistic Foley Lab on another device)', inp), err),
      buttons: [{ label: 'Cancel' }, { label: 'Add the plan', primary: true, onClick: async (close) => {
        try {
          const r = await api.planFromLink(inp.value.trim());
          list = list.filter((p) => p.id !== r.id); list.unshift({ ...r, progress: {} });
          load(list[0]); msg.textContent = `Added the plan ${r.name}.`; close();
        } catch (e) { err.textContent = e.message; }
      } }],
    });
  };
  const saveFile = () => {
    const o = { format: 'ballistic-plan', version: 1, id: cur.id, name: cur.name, project: cur.project, chips: cur.chips, shots: cur.shots };
    const a = el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(o, null, 1)], { type: 'application/json' })), download: (cur.name.replace(/[\\/:*?"<>|]+/g, '-') || 'plan') + '.plan.json' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };

  load(cur);
  openDialog({
    title: 'Recording plans', width: 760,
    body: el('div', { class: 'form plan-dlg' },
      el('p', { class: 'p-hint small', text: 'What to get for a project, shot by shot: sounds, photos, textures. On a phone the plan opens in Kurator Capture (or Ballistic Foley Lab\'s Record view, for sounds): scan the code, or send the link. What is saved there as a session, or backed up to a Drive folder that syncs into the library, comes back here, tagged, and counts against its shots.' }),
      el('div', { class: 'row' }, el('label', { class: 'grow' }, 'Plan', sel),
        el('button', { class: 'btn', type: 'button', text: 'New plan', onclick: () => { load(blank()); name.focus(); name.select(); } }),
        el('button', { class: 'btn', type: 'button', text: 'Paste a link…', onclick: fromLink })),
      el('div', { class: 'row' }, el('label', { class: 'grow' }, 'Name', name), el('label', { class: 'grow' }, 'Project (goes with every take)', proj)),
      el('label', {}, 'Quick tags (commas; switched on while recording)', chips),
      projects, subjects,
      shots,
      el('div', { class: 'row' }, el('button', { class: 'btn small', type: 'button', text: 'Add a shot', onclick: () => {
        let k = cur.shots.length + 1;
        while (cur.shots.some((s) => s.id === 's' + k)) k++;
        cur.shots.push({ id: 's' + k, name: 'Shot ' + (cur.shots.length + 1), tags: [], takes: 3, ucs: '', notes: '', kind: '' });
        drawShots();
        shots.lastElementChild?.querySelector('input')?.select();
      } })),
      el('div', { class: 'plan-share' }, qr,
        el('div', { class: 'plan-linkbox' },
          el('label', {}, 'The phone records with', appSel),
          el('label', {}, 'Link for the phone', link),
          el('div', { class: 'row' },
            el('button', { class: 'btn small', type: 'button', text: 'Copy link', onclick: () => navigator.clipboard?.writeText(link.value).then(() => app.toast('Copied the plan link'), () => { link.select(); }) }),
            // on this computer Ballistic also learns where Kurator is, for Send to Kurator
            el('button', { class: 'btn small', type: 'button', text: 'Open on this computer', onclick: () => window.open(`${link.value}&kurator=${encodeURIComponent(location.origin)}`, '_blank', 'noopener') }),
            el('button', { class: 'btn small', type: 'button', text: 'Save plan file', onclick: saveFile })),
          el('div', { class: 'p-hint small', text: 'Kurator files recording sessions that arrive in a library folder, as zips or as backup folders (⚙ Settings → Recording sessions). Save the plan before sending it, so the takes count here.' }))),
      msg),
    buttons: [
      { label: 'Delete', danger: true, left: true, onClick: async () => {
        if (cur.fresh) { cur = list[0] ? structuredClone(list[0]) : blank(); load(cur); return; }
        if (!(await ask(`Delete the plan ${cur.name}?\n\nThe sounds stay; only the plan and its counts go.`, { ok: 'Delete', danger: true }))) return;
        try { await api.deletePlan(cur.id); list = list.filter((p) => p.id !== cur.id); cur = list[0] ? structuredClone(list[0]) : blank(); load(cur); msg.textContent = 'Deleted.'; }
        catch (e) { msg.textContent = e.message; }
      } },
      { label: 'Close' },
      { label: 'Save', primary: true, onClick: () => save() },
    ],
  });
}

/** The page's note when a recording session was unpacked (from the state the page asks for anyway). */
export function noteSessions(app, s) {
  if (!s) return;
  if (app.sessSeen == null) { app.sessSeen = s.n; return; }
  if (s.n <= app.sessSeen) return;
  const k = s.n - app.sessSeen;
  app.sessSeen = s.n;
  app.toast(k === 1 ? `Recording session unpacked: ${s.last}, ${fmtNum(s.sounds)} sound${s.sounds === 1 ? '' : 's'}, tagged`
    : `${fmtNum(k)} recording sessions unpacked (last: ${s.last})`, 6000);
}
