// Send to SFX Workbench: the chosen sounds open in SFX Workbench (a web app for game sound effects),
// and what is made there comes back as new files - into one library folder, tagged "SFX Workbench"
// plus the open project and the tags the view is filtered by, with the files it was made from as
// its source. With no sounds chosen, SFX Workbench opens empty and new sounds come back the same way.
// The link is private to SFX Workbench and lasts a day after its last use (see kurator/sfxlink.py).

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { inShell } from './shell.js';
import { el, fmtNum, prefs } from './util.js';

const dirOf = (p) => p.replace(/[\\/][^\\/]*$/, '');
const tagLabel = (app, id) => {
  const t = app.tags.byId.get(id);
  return t ? `${t.facet === 'project' ? '◆' : '#'} ${t.name}` : '?';
};

/** The open project and the tags the view is filtered by. */
function viewTags(app) {
  const ids = [];
  if (app.projects.active) ids.push(app.projects.active);
  for (const t of (app.query.tag || '').split(',').filter(Boolean)) ids.push(+t);
  return [...new Set(ids)].filter((id) => app.tags.byId.has(id));
}

/** Where results go: next to the first sound, else the library folder open, else the last one used,
 * else "SFX Workbench" in the first library folder. */
async function defaultDest(app, ids) {
  if (ids.length) {
    try {
      const d = dirOf((await api.paths(ids.slice(0, 1))).paths[0] || '');
      if (d && app.isLibraryPath(d)) return d;
    } catch { /* gone: the other choices */ }
  }
  if (app.query.folder && app.isLibraryPath(app.query.folder)) return app.query.folder;
  const last = prefs.get('sfx.dest', '');
  if (last && app.isLibraryPath(last)) return last;
  const root = (app.state?.roots || []).find((r) => r.kind !== 'browse');
  return root ? `${root.path.replace(/[\\/]+$/, '')}${app.state?.sep || '/'}SFX Workbench` : '';
}

/** Send `ids` (sounds; none: start empty) to SFX Workbench. dest / tags: where results go and what they get. */
export async function sendToSfx(app, ids, { dest = null, tags = null, label = '' } = {}) {
  const tagIds = tags ?? viewTags(app);
  const d = dest ?? await defaultDest(app, ids);
  if (!prefs.get('sfx.ask', true) && d && app.isLibraryPath(d)) { go(app, ids, d, tagIds, label); return; }
  const n = ids.length;
  const destIn = el('input', { type: 'text', value: d, spellcheck: 'false' });
  const checks = tagIds.map((id) => { const c = el('input', { type: 'checkbox' }); c.checked = true; return [id, c]; });
  const never = el('input', { type: 'checkbox' });
  const msg = el('div');
  const dlg = openDialog({
    title: 'SFX Workbench', width: 560,
    body: el('div', { class: 'sfx-dlg' },
      el('p', { text: n ? `${n === 1 ? 'This sound opens' : `These ${fmtNum(n)} sounds open`} in SFX Workbench, in your browser. When you have made something, "Send to Kurator" there saves it here as a new file - your originals stay as they are.`
        : 'SFX Workbench opens in your browser, ready for new sounds. "Send to Kurator" there saves what you make here as new files.' }),
      el('div', { class: 'field' }, el('label', { text: 'Save what comes back into' }),
        destField(app, destIn, { title: 'Where sounds from SFX Workbench go' }),
        el('div', { class: 'p-hint small', text: 'A folder in the library, so the new sounds show here with their tags.' })),
      el('div', { class: 'field' }, el('label', { text: 'Tags for what comes back' }),
        el('label', { class: 'chk' }, el('input', { type: 'checkbox', checked: true, disabled: true }), el('span', { text: 'SFX Workbench (Source)' })),
        ...checks.map(([id, c]) => el('label', { class: 'chk' }, c, el('span', { text: tagLabel(app, id) })))),
      el('p', { class: 'p-hint small', text: 'Kurator gives SFX Workbench a private link for these sounds only. The first time, the browser asks whether SFX Workbench may connect to apps on this device: allow it. Use Chrome, Edge or Firefox - Safari does not allow web pages to reach apps on the computer.' }),
      el('label', { class: 'chk' }, never, el('span', { text: 'Don\'t ask again (⚙ Settings brings it back)' })),
      msg),
    buttons: [
      { label: 'Cancel' },
      { label: 'Open SFX Workbench', primary: true, onClick: (close) => {
        const dest2 = destIn.value.trim();
        if (!dest2 || !app.isLibraryPath(dest2)) {
          msg.className = 'err'; msg.textContent = 'Choose a folder in the library (or add the folder to the library first)';
          return;
        }
        if (never.checked) prefs.set('sfx.ask', false);
        close();
        go(app, ids, dest2, checks.filter(([, c]) => c.checked).map(([id]) => id), label);
      } },
    ],
  });
  return dlg;
}

async function go(app, ids, dest, tagIds, label) {
  // In a browser the window opens at once, while the click still counts (pop-up blockers), and
  // gets its address when Kurator has made the link; the desktop app hands the address to the
  // default browser.
  const win = inShell() ? null : window.open('', '_blank');
  try {
    const r = await api.sfxShare(ids, dest, tagIds, label);
    prefs.set('sfx.dest', dest);
    if (win) { win.opener = null; win.location.replace(r.url); } else window.open(r.url, '_blank');
    app.toast(`${r.files ? `Sent ${fmtNum(r.files)} sound${r.files === 1 ? '' : 's'}` : 'Opened'} to SFX Workbench - what comes back goes to ${dest.split(/[\\/]/).filter(Boolean).pop()}`, 6000);
  } catch (e) {
    win?.close();
    app.toast(e.message, 6000);
  }
}

/** The page's note when SFX Workbench sent something (from the status the page asks for anyway). */
export function noteArrivals(app, sfx) {
  if (!sfx) return;
  if (app.sfxSeen == null) { app.sfxSeen = sfx.n; return; }
  if (sfx.n <= app.sfxSeen) return;
  const k = sfx.n - app.sfxSeen;
  app.sfxSeen = sfx.n;
  app.toast(k === 1 ? `From SFX Workbench: ${sfx.last}` : `${fmtNum(k)} sounds from SFX Workbench (last: ${sfx.last})`, 5000);
}
