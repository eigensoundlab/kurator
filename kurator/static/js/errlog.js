// Errors in the page go to Kurator's log too (the console window, or kurator-server.log in the desktop
// app), so a problem seen in the window can be found afterwards. A few per minute at most.

let sent = 0, since = Date.now();

export function reportError(where, e) {
  console.error(`Kurator (${where})`, e);
  if (Date.now() - since > 60000) { sent = 0; since = Date.now(); }
  if (++sent > 10) return;
  const text = `${where}: ${e?.stack || e?.message || e}`.slice(0, 2000);
  fetch('/api/client-log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, page: location.pathname }), keepalive: true }).catch(() => {});
}

export function installErrorLog() {
  addEventListener('error', (e) => reportError('error', e.error || e.message));
  addEventListener('unhandledrejection', (e) => reportError('promise', e.reason));
}
