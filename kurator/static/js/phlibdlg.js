// "Analyse sounds with Playhead": many sounds at once, so the Look filter's sound character and the
// Playhead sorts can use them. Two ways, whichever is available:
//   - the Playhead command line on Kurator's machine (in the background, a few at a time; this window
//     can be closed), when `playhead` is on the PATH or KURATOR_PLAYHEAD points to it;
//   - this browser (one at a time, in the same worker the waveform view uses; keep the tab open).
// Either way the documents land in Kurator's store and their summaries in the catalog.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { PH_HOME, phBatch, phBatchCancel, phBatchState, phLib, phOnBatch } from './playhead.js';
import { AUDIO, el, extOf, fmtNum, setKids } from './util.js';

const plural = (n, one, many) => `${fmtNum(n)} ${n === 1 ? one : many}`;

export async function openPhLibraryDialog(app) {
  let st;
  try { st = await api.phStatus(); } catch (e) { app.toast(e.message); return null; }
  const P = await phLib().catch(() => null);
  const it = app.items;
  const view = [];
  if (it) for (let i = 0; i < it.ids.length; i++) if (AUDIO.has(extOf(it.n[i]))) view.push([it.ids[i], 0, it.n[i]]);

  const radio = (name, value, label, { checked = false, disabled = false, hint = '' } = {}) => {
    const r = el('input', { type: 'radio', name, value });
    r.checked = checked;
    r.disabled = disabled;
    return el('label', { class: 'chk block' + (disabled ? ' muted' : '') }, r, el('span', { text: label }), hint ? el('div', { class: 'p-hint small', text: hint }) : null);
  };
  const scope = el('div', { class: 'ph-scope' },
    radio('phscope', 'missing', `Not analysed yet (${fmtNum(st.missing)})`, { checked: st.missing > 0, disabled: !st.missing }),
    radio('phscope', 'view', `The sounds in the current view (${fmtNum(view.length)})`, { checked: !st.missing && view.length > 0, disabled: !view.length }),
    radio('phscope', 'all', `All ${plural(st.sounds, 'sound', 'sounds')} again`, { disabled: !st.sounds }));
  const cli = st.cli;
  const differs = cli?.version && P?.version && cli.version !== P.version;
  const how = el('div', { class: 'ph-how' },
    radio('phhow', 'cli', cli ? `Playhead command line${cli.version ? ` ${cli.version}` : ''}` : 'Playhead command line (not installed)', {
      checked: !!cli, disabled: !cli,
      hint: cli ? `${cli.path} · in the background, a few sounds at a time; you can close this window.`
        + (differs ? ` This page draws with Playhead ${P.version}: the viewer analyses a sound again when it needs the strip.` : '')
        : 'Put the playhead executable on the PATH (or set KURATOR_PLAYHEAD to it) and open this again.' }),
    radio('phhow', 'browser', 'This browser', { checked: !cli, hint: 'One sound at a time, in the background of this page: keep the tab open.' }));
  const out = el('div', { class: 'ph-run' });
  const value = (box) => box.querySelector('input:checked')?.value;
  let unsubscribe = () => {};

  const d = openDialog({
    title: 'Analyse sounds with Playhead', width: 560,
    body: el('div', { class: 'projdlg phlibdlg' },
      el('div', { class: 'p-hint small' },
        `${fmtNum(st.analysed)} of ${plural(st.sounds, 'sound', 'sounds')} in the library are analysed. The analysis gives the Look filter its sound character `
        + '(hit or swell, bursty, metal-like, long ring, harsh…) and the sort menu harshness, impulsiveness, ring time, burstiness and loudness range. '
        + 'Sounds longer than 10 minutes are analysed over their first 10. ',
        el('a', { href: PH_HOME, target: '_blank', rel: 'noopener noreferrer', text: 'About Playhead' })),
      el('div', { class: 'tp-head', text: 'Which sounds' }), scope,
      el('div', { class: 'tp-head', text: 'How' }), how,
      out),
    buttons: [{ label: 'Close' }, { label: 'Start', primary: true, onClick: () => start() }],
    onClose: () => unsubscribe(),
  });

  const showRun = (s, stop, kind) => {
    const bar = el('div', { class: 'pbar' }, el('span', { style: { width: `${s.total ? Math.round((100 * (s.done + s.failed)) / s.total) : 0}%` } }));
    const now = kind === 'cli' ? (s.current || []).join(', ') : s.current;
    const line = `${fmtNum(s.done)} / ${fmtNum(s.total)} analysed${s.failed ? ` · ${fmtNum(s.failed)} failed` : ''}${s.running && now ? ` · now: ${now}` : ''}`;
    if (s.running) {
      setKids(out, bar, el('div', { class: 'small', text: line }),
        el('div', { class: 'dlg-inline-buttons' }, el('button', { class: 'btn small', text: 'Stop', onclick: stop })));
    } else {
      setKids(out, el('div', { class: s.failed ? 'err' : 'ok', text: `${s.cancelled ? 'Stopped' : 'Done'}: ${line}` }),
        s.errors?.length ? el('div', { class: 'err small', text: s.errors.slice(-5).join(' · ') }) : null);
    }
    d.buttons.Start.disabled = !!s.running;
  };

  // command line: poll the job
  const pollCli = async () => {
    let s;
    try { s = (await api.phStatus()).job; } catch { return; }
    if (!out.isConnected) return;
    showRun(s, () => api.phCancel(), 'cli');
    if (s.running) setTimeout(pollCli, 700);
    else app.reload({ keepScroll: true });
  };
  // this browser: follow the batch
  const onBatch = (e) => {
    if (!out.isConnected) return;
    const s = e.detail;
    showRun(s, phBatchCancel, 'browser');
    if (!s.running) app.reload({ keepScroll: true });
  };
  unsubscribe = phOnBatch(onBatch);

  async function start() {
    const sc = value(scope), hw = value(how);
    if (!sc || !hw) return;
    d.buttons.Start.disabled = true;
    try {
      if (hw === 'cli') {
        await api.phAnalyse(sc === 'view' ? { scope: 'ids', ids: view.map((v) => v[0]) } : { scope: sc });
        pollCli();
      } else {
        const items = sc === 'view' ? view : (await api.phTodo(sc)).items;
        phBatch(items).catch((e) => setKids(out, el('div', { class: 'err', text: e.message })));
      }
    } catch (e) {
      setKids(out, el('div', { class: 'err', text: e.message }));
      d.buttons.Start.disabled = false;
    }
  }

  // already running (this window was closed and opened again)
  if (st.job?.running) pollCli();
  else if (phBatchState().running) showRun(phBatchState(), phBatchCancel, 'browser');
  return d;
}
