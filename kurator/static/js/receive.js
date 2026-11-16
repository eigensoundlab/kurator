// Kurator's end of "Send to Kurator" in Ballistic Foley Lab (see kurator/sessions.py).
//
// Ballistic opens this page in a window named "kurator" and posts a session zip to it, window to
// window, as it does with SFX Workbench: a page on the web cannot simply call Kurator on this
// computer (Safari refuses, Chrome and Firefox ask first), but it can open it. This page then hands
// the zip to Kurator from Kurator's own address. It talks only to the Ballistic Foley Lab address in
// Settings, and only to the window that opened it.
//
//   Ballistic -> here: { type: 'kurator:import', v: 1, id, name, what, bytes }   (bytes: the zip)
//   here -> Ballistic: { type: 'kurator:ready', v: 1 } once loaded,
//                      { type: 'kurator:imported', v: 1, id, files, skipped, folder, already, plan }
//                      or { type: 'kurator:failed', v: 1, id, error }

const $ = (id) => document.getElementById(id);
const base = (p) => String(p || '').split(/[\\/]/).filter(Boolean).pop() || '';
const n = (k, one, many = one + 's') => `${k} ${k === 1 ? one : many}`;
let info = null;

function status(text, cls = '') {
  const s = $('rcv-status');
  s.textContent = text;
  s.className = cls;
}

function note(text, cls) {
  const li = document.createElement('li');
  const t = document.createElement('span');
  t.className = 'muted';
  t.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' ';
  li.className = cls;
  li.append(t, text);
  $('rcv-list').prepend(li);
}

function result(j) {
  if (j.already) return 'Kurator had this session already: nothing new to add.';
  const where = base(j.folder);
  if (!j.files) return `All ${n(j.skipped, 'take')} of this session were in ${where} already.`;
  return `Added ${n(j.files, 'take')} to ${where}` + (j.more ? ' (more of a session sent before)' : '')
    + (j.skipped ? `; ${n(j.skipped, 'take')} ${j.skipped === 1 ? 'was' : 'were'} there already` : '')
    + (j.plan ? `, counted against the plan ${j.plan}` : '') + '.';
}

async function onMessage(e) {
  if (!info || e.origin !== info.origin || e.source !== window.opener) return;
  const d = e.data;
  if (!d || d.type !== 'kurator:import' || d.v !== 1 || typeof d.id !== 'string') return;
  const answer = (m) => { try { e.source.postMessage({ v: 1, id: d.id, ...m }, info.origin); } catch { /* Ballistic was closed */ } };
  if (!(d.bytes instanceof ArrayBuffer) || !d.bytes.byteLength) {
    answer({ type: 'kurator:failed', error: 'The session arrived empty.' });
    return;
  }
  status(`Receiving ${d.what || 'a session'}…`);
  try {
    const r = await fetch(`/api/ballistic/receive?name=${encodeURIComponent(String(d.name || 'session.zip'))}`,
      { method: 'POST', body: d.bytes, headers: { 'Content-Type': 'application/zip' } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Kurator answered ${r.status}. Try again.`);
    const text = result(j);
    status(text, 'ok');
    note(text, 'ok');
    answer({ type: 'kurator:imported', files: j.files, skipped: j.skipped, folder: j.folder || '', already: !!j.already, plan: j.plan || '' });
  } catch (err) {
    const text = err instanceof TypeError ? 'Kurator stopped answering. Start it again, then send again from Ballistic Foley Lab.' : err.message;
    status(text, 'err');
    note(text, 'err');
    answer({ type: 'kurator:failed', error: text });
  }
}

async function start() {
  try {
    info = await (await fetch('/api/ballistic/receive')).json();
  } catch {
    status('Kurator is not answering. Start it again, then send again from Ballistic Foley Lab.', 'err');
    return;
  }
  $('rcv-where').textContent = info.folder
    ? `Sessions go to ${info.folder}, each into a folder of its own; more takes of a session join its folder. Change it in Kurator: Settings, Recording sessions.`
    : 'Kurator has no library folder yet. Add one in Kurator, then send again.';
  if (!info.on) {
    status('Kurator does not take sessions from Ballistic Foley Lab right now. Switch it on in Settings, Recording sessions.', 'err');
    return;
  }
  if (!window.opener) {
    status('To send takes here, use Send to Kurator in Ballistic Foley Lab on this computer.');
    return;
  }
  window.addEventListener('message', onMessage);
  // only a page from Ballistic Foley Lab's address (Settings) hears this
  window.opener.postMessage({ type: 'kurator:ready', v: 1 }, info.origin);
  status('Ready for takes from Ballistic Foley Lab.');
}

start();
