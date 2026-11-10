// The first page of the desktop app: shown while Kurator starts, or when it can't be found.
'use strict';
const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);
const $ = (id) => document.getElementById(id);
let busy = false;

let showing = false;

function paint(s) {
  if (s.phase === 'ready' && s.url) {   // Kurator runs: the app shows it; still here a moment later, ask again
    if (!showing) { showing = true; setTimeout(() => invoke('show_kurator').catch(() => { showing = false; }), 1500); }
    return;
  }
  $('msg').textContent = s.message || 'Starting…';
  const d = $('detail');
  d.textContent = s.detail || '';
  d.classList.toggle('log', s.phase === 'error' && (s.detail || '').includes('\n'));
  const stuck = s.phase === 'error' || s.phase === 'need-home';
  $('bar').classList.toggle('off', stuck);
  $('actions').hidden = !stuck;
  $('help').hidden = s.phase !== 'need-home';
}

async function poll() {
  try { paint(await invoke('get_status')); } catch (e) { paint({ phase: 'error', message: 'The app could not ask for Kurator\'s state', detail: String(e) }); }
  setTimeout(poll, 400);
}

async function act(cmd) {
  if (busy) return;
  busy = true;
  try { await invoke(cmd); } catch (e) { paint({ phase: 'need-home', message: 'That is not a Kurator folder', detail: String(e) }); }
  busy = false;
}

$('choose').onclick = () => act('pick_home');
$('retry').onclick = () => act('retry');
$('logs').onclick = () => act('open_logs');
$('help').onclick = () => act('open_help');
poll();
