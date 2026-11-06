// Playhead analysis of sounds (optional, off by default; ⚙ Settings → Sounds).
//
// Playhead (https://github.com/eigensoundlab/playhead) analyses a sound into overview curves,
// events and their types, beats and sections, and draws them as a strip. Kurator only fetches the
// decoded samples, runs Playhead in a module worker, keeps the documents, and draws with
// Playhead.drawStrip() - never a look of its own - so a new Playhead build changes the look by
// replacing static/js/vendor/playhead-core.mjs.
//
// Names: Kurator's playback cursor is also called "playhead" (waveview.js heads(), app.playAt);
// everything here is "ph..." and the UI says "Playhead analysis".

import { api } from './api.js';
import { prefs } from './util.js';

export const PH_PAPER = 'https://doi.org/10.5281/zenodo.24008128';
export const PH_HOME = 'https://github.com/eigensoundlab/playhead';
const CHANGED = 'kurator-playhead-setting';
const GOT_DOC = 'kurator-playhead-doc';
const SHORT = 120;           // seconds: up to this, the file's own rate and up to 2 channels; longer: 22.05 kHz mono
const MAX_SECONDS = 600;     // only the first 10 minutes are analysed

/** Is Playhead analysis switched on (a setting of this browser)? */
export const phEnabled = () => !!prefs.get('playhead.enabled', false);

export function phSetEnabled(on) {
  prefs.set('playhead.enabled', !!on);
  if (!on && prefs.get('wave.mode', 'peaks') === 'playhead') prefs.set('wave.mode', 'peaks');
  window.dispatchEvent(new CustomEvent(CHANGED, { detail: { on: !!on } }));
}
export const phOnSetting = (fn) => window.addEventListener(CHANGED, fn);
/** A document became available: fn({ detail: { id, doc } }). */
export const phOnDoc = (fn) => window.addEventListener(GOT_DOC, fn);

// The library: loaded only when it is needed (no request while Playhead is off).
let libP = null;
let lib = null;
export function phLib() {
  if (!libP) libP = import('./vendor/playhead-core.mjs').then((m) => { lib = m.default; return lib; });
  return libP;
}
export const phLibNow = () => lib;

// -------------------------------------------------------------------------- documents

const mem = new Map();        // path \n mtime_ns \n Playhead version -> document
const pending = new Map();    // the same key -> { promise, progress: Set, owners: Set }
const MEM_MAX = 40;

const accept = (doc) => (doc && doc.schema === 'playhead-analysis' && doc.schemaVersion === 1 ? doc : null);

function remember(key, id, doc) {
  mem.delete(key);
  mem.set(key, doc);
  while (mem.size > MEM_MAX) mem.delete(mem.keys().next().value);
  window.dispatchEvent(new CustomEvent(GOT_DOC, { detail: { id, doc } }));
}

async function fileKey(id, P) {
  const d = await api.item(id);
  return { key: `${d.path}\n${d.mtime_ns}\n${P.version}`, name: (d.name || ''), duration: d.meta?.duration || 0 };
}

async function stored(id, P) {
  try {
    const r = await fetch(`/api/audio/${id}/playhead?v=${encodeURIComponent(P.version)}`);
    if (!r.ok) return null;
    const doc = accept(await r.json());
    return doc && doc.generator?.version === P.version ? doc : null;
  } catch { return null; }
}

function store(id, doc) {
  const v = doc.generator?.version;
  if (!v) return;
  fetch(`/api/audio/${id}/playhead?v=${encodeURIComponent(v)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc),
  }).catch(() => { /* the document still works in this session */ });
}

/** The document if one exists (memory or Kurator's store), without analysing. */
export async function phCached(id) {
  const P = await phLib();
  const f = await fileKey(id, P);
  if (mem.has(f.key)) return mem.get(f.key);
  const doc = await stored(id, P);
  if (doc) remember(f.key, id, doc);
  return doc;
}

/**
 * The Playhead document of a sound: from memory, then Kurator's store, then a new analysis in the
 * worker. `owner` is the view asking (a newer request from the same view replaces its waiting one);
 * onProgress(p): p < 0 while the sound is read, then 0..1.
 */
export async function phDoc(id, { owner = null, onProgress = null, duration = 0 } = {}) {
  const P = await phLib();
  const f = await fileKey(id, P);
  if (owner) replaceWaiting(owner, f.key);
  if (mem.has(f.key)) return mem.get(f.key);
  let p = pending.get(f.key);
  if (!p) {
    p = { progress: new Set(), owners: new Set() };
    pending.set(f.key, p);
    p.promise = (async () => {
      try {
        const kept = await stored(id, P);
        const doc = kept || await new Promise((resolve, reject) => {
          enqueue({ id, key: f.key, name: f.name, duration: duration || f.duration, entry: p, resolve, reject });
        });
        remember(f.key, id, doc);
        if (!kept) store(id, doc);
        return doc;
      } finally { pending.delete(f.key); }
    })();
  }
  if (onProgress) p.progress.add(onProgress);
  if (owner) p.owners.add(owner);
  return p.promise;
}

export class PhReplaced extends Error {
  constructor() { super('replaced by a newer request'); this.replaced = true; }
}

// -------------------------------------------------------------------------- worker queue (one job at a time)

let worker = null;
let running = null;
const waiting = [];
let nextJob = 1;

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL('./playhead-worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = (e) => {
      const { jobId, progress, doc, error } = e.data || {};
      const job = running;
      if (!job || job.jobId !== jobId) return;                  // a stale job
      if (progress != null) { for (const fn of job.entry.progress) fn(progress); return; }
      running = null;
      if (error) job.reject(new Error(error));
      else if (!accept(doc)) job.reject(new Error('not a Playhead analysis'));
      else job.resolve(doc);
      pump();
    };
    worker.onerror = (e) => {
      const job = running;
      running = null;
      worker.terminate();
      worker = null;
      job?.reject(new Error(e.message || 'the analysis stopped'));
      pump();
    };
  }
  return worker;
}

function enqueue(job) {
  waiting.push(job);
  pump();
}

/** A view asks for another sound: drop what it was waiting for, unless another view waits for it too. */
function replaceWaiting(owner, keepKey) {
  for (let i = waiting.length - 1; i >= 0; i--) {
    const job = waiting[i];
    if (job.key === keepKey || !job.entry.owners.has(owner)) continue;
    job.entry.owners.delete(owner);
    if (!job.entry.owners.size) {
      waiting.splice(i, 1);
      job.reject(new PhReplaced());
    }
  }
}

async function pump() {
  if (running || !waiting.length) return;
  const job = waiting.shift();
  running = job;
  job.jobId = nextJob++;
  try {
    for (const fn of job.entry.progress) fn(-1);
    const { sampleRate, channels } = await samples(job.id, job.duration);
    if (running !== job) return;
    for (const fn of job.entry.progress) fn(0);
    getWorker().postMessage({ jobId: job.jobId, sampleRate, channels, name: job.name }, channels.map((c) => c.buffer));
  } catch (e) {
    if (running === job) running = null;
    job.reject(e);
    pump();
  }
}

/** Decoded samples from Kurator (every format it reads), as Float32Array channels. */
async function samples(id, duration) {
  const short = duration > 0 && duration <= SHORT;
  const q = short ? 'rate=0&mono=0' : 'rate=22050&mono=1';
  const r = await fetch(`/api/audio/${id}/samples?${q}&max_seconds=${MAX_SECONDS}`);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  const sampleRate = parseFloat(r.headers.get('X-Samplerate'));
  const ch = Math.max(1, parseInt(r.headers.get('X-Channels'), 10) || 1);
  const all = new Float32Array(await r.arrayBuffer());
  if (!all.length) throw new Error('the sound is empty');
  if (ch === 1) return { sampleRate, channels: [all] };
  const n = Math.floor(all.length / ch);
  const L = new Float32Array(n), R = new Float32Array(n);
  for (let i = 0, k = 0; i < n; i++, k += ch) { L[i] = all[k]; R[i] = all[k + 1]; }
  return { sampleRate, channels: [L, R] };
}

// -------------------------------------------------------------------------- looks, theme, summary

/** The look chosen in the Look menu: a ready-made look's name, or a look code ("custom"). */
export const phLookPref = () => prefs.get('playhead.look', 'auto');
export const phSetLookPref = (v) => prefs.set('playhead.look', v || 'auto');

/** Where to tell the Playhead authors whether a look helps (an issue form; no server, no tracking). */
export const phFeedbackUrl = (code) =>
  `${PH_HOME}/issues/new?template=look-feedback.yml&look=${encodeURIComponent(code || 'auto')}&app=kurator`;

const hexRgb = (h) => { const v = parseInt(h.slice(1, 7), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; };
const BG = hexRgb('#1c1f26');
// the palette of Kurator's own spectrogram (audio_analysis._PALETTE), so the two read alike
const SPEC = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]];

/** Theme for drawStrip() from the wave palette (blue: short sounds, orange: long). No red: that means clipping. */
export function phTheme(pal) {
  const body = hexRgb(pal.body);
  return {
    background: null,
    wave: pal.outline,
    waveLow: body.map((v, i) => Math.round(BG[i] + (v - BG[i]) * 0.3)),   // calm passages faint
    waveHigh: body,                                                     // harsh passages strong
    line: '#ffd166', tick: '#ffffff', text: '#c8cede', rule: '#5c6372',
    lane: pal.line, laneDown: '#e8a33d', laneFlat: '#5c6372', series: ['#4fb39a', '#a285e0'],
    specRamp: SPEC, halo: '#14161b',
  };
}

const num = (v) => typeof v === 'number' && isFinite(v);
const fmtS = (t) => { const m = Math.floor(t / 60), s = Math.round(t - m * 60); return `${m}:${String(s).padStart(2, '0')}`; };

/** Rows [label, text, title?] for the side panel. */
export function phSummary(doc, duration = 0) {
  const rows = [];
  const d = doc.descriptors || {};
  const sfx = !doc.rhythm && (doc.profile === 'sfx' || doc.events);
  if (sfx && Array.isArray(doc.events)) {
    const words = new Map();
    for (const e of doc.events) {
      const w = [e.word, e.tag ? `(${e.tag})` : ''].filter(Boolean).join(' ') || 'event';
      words.set(w, (words.get(w) || 0) + 1);
    }
    const list = [...words].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([w, n]) => (n > 1 ? `${n} × ${w}` : w));
    rows.push(['Events', doc.events.length ? `${doc.events.length}: ${list.join(', ')}${words.size > 4 ? '…' : ''}` : 'none found',
      'Sound events and their Schaeffer-style types (impulse, iterative, sustained; tonal or noisy)']);
  }
  if (sfx && d.decay && d.decay.reliable !== false && (d.decay.material || num(d.decay.t60))) {
    rows.push(['Decay', [d.decay.material, num(d.decay.t60) ? `T60 ${d.decay.t60.toFixed(2)} s` : null,
      num(d.decay.logTauD) ? `log τd ${d.decay.logTauD.toFixed(2)}` : null].filter(Boolean).join(' · '),
    'How the sound rings out after its events, and the material class the damping suggests (Klatzky)']);
  }
  if (sfx && d.arrow && num(d.arrow.T20)) {
    const t = Math.abs(d.arrow.T20) < 0.005 ? 0 : d.arrow.T20;
    rows.push(['Time arrow', `${t > 0 ? '+' : ''}${t.toFixed(2)} ${t > 0.3 ? '(hit-like: sudden rise, slow fall)' : t < -0.3 ? '(swell-like: slow rise, sudden end)' : '(about even)'}`,
      'Time irreversibility of the level: positive for hits, negative for reversed or swelling sounds']);
  }
  if (sfx && d.events && num(d.events.burstiness)) {
    const b = Math.abs(d.events.burstiness) < 0.005 ? 0 : d.events.burstiness;
    rows.push(['Burstiness', `${b.toFixed(2)} ${b > 0.1 ? '(in bursts)' : b < -0.1 ? '(regular)' : '(random-like)'}`,
      'How events cluster in time: above 0 in bursts, about 0 at random, below 0 evenly spaced']);
  }
  if (doc.rhythm && num(doc.rhythm.tempo)) {
    rows.push(['Tempo', `${doc.rhythm.tempo.toFixed(1)} BPM${doc.rhythm.reliable ? '' : ' (no steady beat)'}`]);
  }
  if (Array.isArray(doc.sections) && doc.sections.length) {
    rows.push(['Sections', doc.sections.map((s) => s.label).join(''), 'A letter per section; a letter repeats when the material returns']);
  }
  if (doc.summary && num(doc.summary.loudnessRange) && !sfx) rows.push(['Loudness range', `${doc.summary.loudnessRange.toFixed(1)} LU`]);
  const dur = doc.source?.duration;
  if (num(dur) && duration > dur + 1) rows.push(['Analysed', `the first ${fmtS(dur)} of ${fmtS(duration)}`, 'Only the first 10 minutes are analysed']);
  return rows;
}
