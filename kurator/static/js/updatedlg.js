// A newer Kurator: a note in the status bar, and how to update - in the desktop app with one button
// (it stops Kurator, runs update.bat / update.sh, starts it again).

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { appVersion, inShell, updateKurator } from './shell.js';
import { el, setKids } from './util.js';

const bare = (v) => String(v || '').replace(/^v\.?/i, '');

/** Ask (the server asks GitHub at most once a day) and show the note when there is something new. */
export async function checkForUpdate(app, force = false) {
  let u;
  try { u = await api.update(force); } catch { return null; }
  app.updateInfo = u;
  const show = u.newer && !u.skipped;
  app.stUpdate.style.display = show ? '' : 'none';
  if (show) {
    app.stUpdate.textContent = `⬆ Kurator ${bare(u.latest)}`;
    app.stUpdate.title = `Kurator ${bare(u.latest)} is out (you have ${u.current}): what is new, how to update`;
  }
  return u;
}

export async function openUpdateDialog(app) {
  const u = await checkForUpdate(app, true);
  if (!u) { app.toast('Could not ask GitHub for the newest version'); return; }
  const msg = el('div');
  const shellVersion = inShell() ? await appVersion() : null;
  const news = u.url ? el('a', { href: u.url, target: '_blank', rel: 'noopener', text: `What is new in ${bare(u.latest)}` }) : null;
  const how = u.app
    ? el('div', { class: 'p-hint small', text: inShell()
      ? 'Update now downloads the new version and its packages, and starts Kurator again - a minute or two. Your catalog and settings stay as they are.'
      : 'This Kurator was installed by the desktop app: update it from there (open the app, then Update now).' })
    : inShell()
    ? el('div', { class: 'p-hint small', text: `Update now stops Kurator, gets the new version${u.git ? ' (git pull)' : ''} and its packages with ${u.script}, and starts it again - a minute or two.` })
    : el('div', { class: 'p-hint small', text: `To update: close Kurator (its console window), run ${u.script} in ${u.folder}, then start Kurator again.${u.git ? '' : ' This folder is not a git clone: download the new release and unzip it over it first.'}` });
  const appNote = shellVersion && u.latest && bare(u.latest) !== shellVersion
    ? el('div', { class: 'p-hint small', text: `The desktop app itself (${shellVersion}): install the new one from the release page when you like - it works with the updated Kurator either way.` }) : null;
  const body = u.newer
    ? el('div', { class: 'form' }, el('div', { text: `Kurator ${bare(u.latest)} is out. You have ${u.current}.` }), news, how, appNote, msg)
    : el('div', { class: 'form' }, el('div', { text: u.latest ? `You have the newest Kurator (${u.current}).` : `Kurator ${u.current}.` }),
      u.error ? el('div', { class: 'p-hint small', text: `GitHub didn't answer: ${u.error}` }) : null, msg);
  const buttons = [{ label: 'Close' }];
  if (u.newer) {
    buttons.unshift({ label: 'Skip this version', onClick: async (close) => { await api.updateSkip(u.latest); await checkForUpdate(app); close(); } });
    if (inShell() && u.script_found) {
      buttons.push({ label: 'Update now', primary: true, onClick: async (close) => {
        try { await updateKurator(bare(u.latest)); close(); } catch (e) { setKids(msg, el('div', { class: 'err', text: String(e?.message || e) })); }
      } });
    }
  }
  openDialog({ title: u.newer ? 'A newer Kurator' : 'Updates', width: 520, body, buttons });
}
