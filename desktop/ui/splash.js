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
  const bar = $('bar');
  bar.classList.toggle('off', stuck);
  const known = typeof s.progress === 'number';   // a download: how far; otherwise just moving
  bar.classList.toggle('known', known);
  bar.firstElementChild.style.width = known ? `${Math.round(Math.min(1, Math.max(0, s.progress)) * 100)}%` : '';
  $('actions').hidden = !stuck;
  const fresh = s.phase === 'need-home' && !/set up in this folder/.test(s.message || '');
  $('install').hidden = !fresh;
  $('choose').classList.toggle('primary', s.phase === 'need-home' && !fresh);
  $('retry').hidden = s.phase === 'need-home';
  $('help').hidden = s.phase !== 'need-home';
}

async function poll() {
  try { paint(await invoke('get_status')); } catch (e) { paint({ phase: 'error', message: 'The app could not ask for Kurator\'s state', detail: String(e) }); }
  setTimeout(poll, 400);
}

async function act(cmd) {
  if (busy) return;
  busy = true;
  try { await invoke(cmd); } catch (e) { paint({ phase: 'need-home', message: cmd === 'pick_home' ? 'That is not a Kurator folder' : 'That did not work', detail: String(e) }); }
  busy = false;
}

$('install').onclick = () => { paint({ phase: 'installing', message: 'Getting ready to install…' }); act('install_kurator'); };
$('choose').onclick = () => act('pick_home');
$('retry').onclick = () => act('retry');
$('logs').onclick = () => act('open_logs');
$('help').onclick = () => act('open_help');
poll();
