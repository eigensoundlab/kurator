// Open with: the programs Kurator found on this computer (Krita, Photoshop, Blender, Audacity...)
// and any others you add. Right-click a thumbnail → Open with.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { inShell, pickFile } from './shell.js';
import { el, setKids } from './util.js';

const KIND_LABEL = { '': 'All files', image: 'Pictures', audio: 'Sounds', video: 'Videos', model: '3D' };
const UA = navigator.userAgent;
const EXAMPLE = /Windows/.test(UA) ? 'C:\\Program Files\\MyApp\\myapp.exe' : /Mac/.test(UA) ? '/Applications/MyApp.app' : '/usr/bin/myapp';

export async function openAppsDialog(app) {
  let r;
  try { r = await api.apps(); } catch (e) { app.toast(e.message); return; }
  const found = r.apps.filter((a) => !a.custom);
  const shown = new Map(found.map((a) => { const c = el('input', { type: 'checkbox' }); c.checked = !a.hidden; return [a.id, c]; }));
  const rows = [];
  const list = el('div', { class: 'apps-own' });
  const msg = el('div');
  const addRow = (c = { name: '', path: '', kinds: [] }) => {
    const name = el('input', { type: 'text', value: c.name, placeholder: 'Name', spellcheck: 'false' });
    const path = el('input', { type: 'text', value: c.path, placeholder: EXAMPLE, spellcheck: 'false', class: 'grow' });
    const kind = el('select', { title: 'Offered for' }, Object.entries(KIND_LABEL).map(([k, v]) => el('option', { value: k, text: v })));
    kind.value = c.kinds?.length === 1 ? c.kinds[0] : '';
    const row = { name, path, kind };
    const browse = inShell() ? el('button', { class: 'btn small', type: 'button', text: 'Browse…', onclick: async () => {
      const p = await pickFile('Choose the program');
      if (p) { path.value = p; if (!name.value.trim()) name.value = p.split(/[\\/]/).pop().replace(/\.(exe|app)$/i, ''); }
    } }) : null;
    const line = el('div', { class: 'row apps-row' }, name, path, browse, kind,
      el('button', { class: 'btn small', type: 'button', text: '×', title: 'Remove', onclick: () => { rows.splice(rows.indexOf(row), 1); line.remove(); } }));
    rows.push(row);
    list.append(line);
    return row;
  };
  for (const c of r.apps.filter((a) => a.custom)) addRow({ name: c.name, path: c.path, kinds: c.kinds.length === r.kinds.length ? [] : c.kinds });
  openDialog({
    title: 'Open with: programs', width: 720,
    body: el('div', { class: 'form' },
      el('h3', { text: 'Found on this computer' }),
      found.length ? el('div', { class: 'apps-found' }, found.map((a) => el('label', { class: 'chk', title: a.path }, shown.get(a.id),
        el('span', {}, a.name, ' ', el('span', { class: 'muted small', text: a.kinds.map((k) => KIND_LABEL[k]).join(', ') })))))
        : el('div', { class: 'p-hint small', text: 'None of the programs Kurator knows (Krita, Photoshop, GIMP, Affinity, Aseprite, Inkscape, Blender, Audacity, REAPER, VLC…) were found where their installers put them. Add them below.' }),
      el('h3', { text: 'Your programs' }),
      list,
      el('div', { class: 'row' }, el('button', { class: 'btn small', type: 'button', text: '+ Add a program', onclick: () => addRow().path.focus() })),
      el('div', { class: 'p-hint small', text: inShell()
        ? 'The program itself: an .exe on Windows, an .app on macOS, the command\'s full path on Linux. "Offered for" decides which files show it.'
        : `The full path of the program, e.g. ${EXAMPLE}. "Offered for" decides which files show it.` }),
      msg),
    buttons: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async (close) => {
      const custom = rows.filter((x) => x.path.value.trim()).map((x) => ({ name: x.name.value.trim(), path: x.path.value.trim(), kinds: x.kind.value ? [x.kind.value] : [] }));
      const hidden = [...shown].filter(([, c]) => !c.checked).map(([id]) => id);
      try { await api.saveApps(custom, hidden); } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); return; }
      app.onAppsChanged?.();
      app.toast('Open with: saved');
      close();
    } }],
  });
}
