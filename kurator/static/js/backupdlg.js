// Backups: sets (what / how / where), plan preview with free space per target, progress, restore.

import { api } from './api.js';
import { ask, openDialog } from './dialog.js';
import { el, fmtBytes, fmtDate, fmtNum, setKids } from './util.js';

const LAYOUT_PRESETS = ['{tagpath}', '{tagpath}/{year}', '{project}/{tagpath}', '{tag}', '{tag}/{year}'];

export async function openBackupDialog(app) {
  const body = el('div', { class: 'form backup' });
  const d = openDialog({ title: 'Backup', width: 820, body, buttons: [{ label: 'Close' }], onClose: () => clearInterval(timer) });
  let timer = 0;

  const footer = (...btns) => el('div', { class: 'dlg-inline-buttons' }, ...btns);
  const btn = (label, onclick, cls = '') => el('button', { class: 'btn ' + cls, type: 'button', text: label, onclick });
  const err = (e) => el('div', { class: 'err', text: e.message || String(e) });

  // ------------------------------------------------------------------ list
  async function showList() {
    clearInterval(timer);
    let data;
    try { data = await api.backups(); } catch (e) { setKids(body, err(e)); return; }
    const st = await api.job().catch(() => null);
    if (st && st.running) { showProgress(); return; }
    const cov = data.coverage;
    const covEl = cov.files === 0 ? null : (cov.not_backed_up || cov.outdated)
      ? el('div', { class: 'cov warn' }, `${fmtNum(cov.not_backed_up)} of ${fmtNum(cov.files)} library files are in no backup yet (${fmtBytes(cov.not_backed_up_bytes)})`,
        cov.outdated ? ` · ${fmtNum(cov.outdated)} changed since their last backup` : '')
      : el('div', { class: 'cov ok', text: `All ${fmtNum(cov.files)} library files are backed up ✓` });
    const cards = data.sets.map((s) => {
      const lr = s.last_result;
      return el('div', { class: 'bset' },
        el('div', { class: 'bset-head' }, el('b', { text: s.name }),
          el('span', { class: 'muted', text: ` · ${s.layout === 'tags' ? `tag folders (${s.tag_template})` : 'original folders'} · ${s.mode === 'mirror' ? 'mirror (removed files archived)' : 'keep everything'}${s.scope.tags ? ` · only ${s.scope.tags.length} tag(s)` : ''}` })),
        ...s.targets.map((t) => {
          const sp = t.space || {};
          const pct = sp.total ? Math.round(100 * (sp.total - sp.free) / sp.total) : 0;
          return el('div', { class: 'btarget' + (t.available ? '' : ' offline') },
            el('span', { class: 'loc', text: t.location, title: t.location }),
            t.available ? el('span', { class: 'bar' }, el('span', { style: { width: `${pct}%` } })) : el('span', { class: 'warn', text: 'not connected' }),
            el('span', { class: 'muted', text: t.available && sp.free != null ? `${fmtBytes(sp.free)} free` : '' }),
            el('span', { class: 'muted', text: `${fmtNum(t.files)} files · ${fmtBytes(t.bytes)}` }));
        }),
        el('div', { class: 'muted small' }, s.last_run ? `Last backup ${fmtDate(s.last_run)}${lr ? ` - ${fmtNum(lr.copied)} copied, ${fmtNum(lr.relocated || 0)} moved${lr.failed ? `, ${lr.failed} failed` : ''}${lr.cancelled ? ' (cancelled)' : ''}` : ''}` : 'Never run'),
        footer(btn('Back up now', () => showPlan(s), 'primary'), btn('Restore…', () => showRestore(s)), btn('Edit', () => showEditor(s)),
          btn('Delete', async () => {
            if (!(await ask(`Delete backup set "${s.name}"?\n\nOnly the set is forgotten - files already copied to the targets stay where they are.`, { ok: 'Delete', danger: true }))) return;
            try { await api.deleteBackup(s.id); } catch (e) { app.toast(e.message); }
            showList();
          }, 'danger')));
    });
    setKids(body, covEl,
      cards.length ? cards : el('div', { class: 'p-hint small', text: 'No backup sets yet. A set says what to back up, how to arrange it (original folders or tag folders) and where - one or more disks or folders. New files are spread over the targets by free space.' }),
      footer(btn('+ New backup set', () => showEditor(null), 'primary')));
  }

  // ---------------------------------------------------------------- editor
  async function showEditor(s) {
    const isNew = !s;
    let rc = await api.rclone().catch(() => ({ installed: false, remotes: [] }));
    const v = s ? JSON.parse(JSON.stringify(s)) : { name: 'My backup', layout: 'original', tag_template: '{tagpath}', policy: 'fill', mode: 'copy', verify: 1, dedupe: 1, scope: {}, targets: [] };
    if (!v.targets.length) v.targets.push({ location: '', reserve: 1024 ** 3 });
    const name = el('input', { type: 'text', value: v.name });
    const tlist = el('div', { class: 'targets' });
    const renderTargets = () => setKids(tlist, v.targets.map((t, i) => {
      t.kind = t.kind || 'local';
      const locked = !!t.files;  // a target that already holds files keeps its place
      const kind = el('select', { title: 'Target type', disabled: locked },
        el('option', { value: 'local', text: 'Folder / disk' }), el('option', { value: 'rclone', text: 'Cloud (rclone)' }));
      kind.value = t.kind;
      kind.onchange = () => { t.kind = kind.value; t.location = ''; renderTargets(); };
      let where;
      if (t.kind === 'rclone') {
        const [r0, ...rest] = (t.location || '').split(':');
        const remote = el('select', { disabled: locked }, el('option', { value: '', text: rc.remotes.length ? '— account —' : 'no accounts yet' }),
          rc.remotes.map((r) => el('option', { value: r.name, text: `${r.name} (${r.type})` })));
        remote.value = rc.remotes.some((r) => r.name === r0) ? r0 : '';
        const folder = el('input', { type: 'text', value: rest.join(':') || 'Kurator Backup', spellcheck: 'false', disabled: locked, title: 'Folder in the cloud account' });
        const sync = () => { t.location = remote.value ? `${remote.value}:${folder.value}` : ''; };
        remote.onchange = sync; folder.oninput = sync; sync();
        where = el('span', { class: 'cloudloc' }, remote, el('span', { class: 'muted', text: ':' }), folder);
      } else {
        const loc = el('input', { type: 'text', value: t.location || '', placeholder: 'E:\\Kurator Backup  or  G:\\My Drive\\Art backup', spellcheck: 'false', disabled: locked });
        loc.oninput = () => { t.location = loc.value; };
        where = loc;
      }
      const res = el('input', { type: 'number', min: '0', step: '0.5', value: String(Math.round(((t.reserve ?? 1024 ** 3) / 1024 ** 3) * 10) / 10), title: 'Always keep this much space free on the target' });
      res.oninput = () => { t.reserve = +res.value * 1024 ** 3; };
      return el('div', { class: 'trow' }, el('span', { class: 'rn', text: `${i + 1}.` }), kind, where,
        el('label', { class: 'chk small' }, 'keep free', res, 'GB'),
        locked ? null : el('button', { class: 'mini', type: 'button', text: '×', title: 'Remove target', onclick: () => { v.targets.splice(i, 1); renderTargets(); } }));
    }));
    const cloudBox = el('div', { class: 'cloudbox' });
    const renderCloud = () => {
      if (!rc.installed) {
        const pathIn = el('input', { type: 'text', placeholder: 'C:\\Tools\\rclone\\rclone.exe', spellcheck: 'false' });
        setKids(cloudBox,
          el('div', {}, el('b', { text: 'Cloud targets need rclone' }), ' (free, open source). Install it, then press "Check again":'),
          el('div', { class: 'muted small', text: 'Windows: winget install Rclone.Rclone   ·   macOS: brew install rclone   ·   Linux: your package manager, or https://rclone.org/downloads/' }),
          el('div', { class: 'row' }, el('span', { class: 'small', text: 'Or tell Kurator where rclone is:' }), pathIn,
            btn('Save path', async () => { try { await api.saveSettings({ rclone_path: pathIn.value }); } catch (e) { app.toast(e.message); return; } rc = await api.rclone(); renderCloud(); renderTargets(); }),
            btn('Check again', async () => { rc = await api.rclone(); renderCloud(); renderTargets(); })));
        return;
      }
      const name = el('input', { type: 'text', value: rc.remotes.some((r) => r.name === 'gdrive') ? `gdrive${rc.remotes.length + 1}` : 'gdrive', style: { width: '120px' }, spellcheck: 'false' });
      const status = el('span', { class: 'small' });
      const connect = btn('Connect Google Drive…', async () => {
        try { await api.rcloneConnect(name.value); } catch (e) { setKids(status, el('span', { class: 'err', text: e.message })); return; }
        connect.disabled = true;
        status.textContent = 'A Google sign-in page opens in your browser - sign in and allow access, then come back here…';
        const poll = setInterval(async () => {
          rc = await api.rclone().catch(() => rc);
          const c = rc.connecting || {};
          if (c.running) return;
          clearInterval(poll);
          connect.disabled = false;
          if (c.error) setKids(status, el('span', { class: 'err', text: c.error }));
          else { status.textContent = `Connected "${c.name}" ✓ - choose it as a cloud target above.`; renderTargets(); }
        }, 1500);
      });
      setKids(cloudBox,
        el('div', { class: 'small' }, el('b', { text: 'Cloud accounts' }), ` · ${rc.version} · `,
          rc.remotes.length ? rc.remotes.map((r) => `${r.name} (${r.type})`).join(', ') : 'none yet'),
        el('div', { class: 'row' }, el('span', { class: 'small', text: 'Account name' }), name, connect, status),
        el('div', { class: 'p-hint small', text: 'Connecting another Google account = another target, so a large library can be spread over several Drives. Kurator only gets access to the files it creates in your Drive (drive.file), and your login is stored by rclone, not Kurator. OneDrive, Dropbox, pCloud, a NAS…: run "rclone config" once in a terminal - they then appear here.' }));
    };
    renderCloud();
    renderTargets();
    const policy = el('select', {}, el('option', { value: 'fill', text: 'Fill targets in order' }), el('option', { value: 'free', text: 'Most free space first' }));
    policy.value = v.policy;
    const layoutOrig = el('input', { type: 'radio', name: 'layout' });
    const layoutTags = el('input', { type: 'radio', name: 'layout' });
    (v.layout === 'tags' ? layoutTags : layoutOrig).checked = true;
    const tmpl = el('input', { type: 'text', value: v.tag_template, list: 'layout-presets', spellcheck: 'false' });
    const presets = el('datalist', { id: 'layout-presets' }, LAYOUT_PRESETS.map((p) => el('option', { value: p })));
    const scopeSel = el('select', {}, el('option', { value: 'all', text: 'Whole library' }), el('option', { value: 'tags', text: 'Only files with these tags' }));
    scopeSel.value = v.scope.tags ? 'tags' : 'all';
    const chosen = new Set(v.scope.tags || []);
    const tagBox = el('div', { class: 'chip-list' }, app.tags.facets.map((f) => {
      const top = app.tags.list.filter((t) => t.facet === f.key && !t.name.includes('/'));
      return top.length ? [el('span', { class: 'tb-group', text: f.label }), ...top.map((t) => {
        const c = el('button', { class: 'chip' + (chosen.has(t.id) ? ' full' : ''), type: 'button', style: { '--c': t.color }, text: t.name });
        c.onclick = () => { if (chosen.has(t.id)) chosen.delete(t.id); else chosen.add(t.id); c.classList.toggle('full', chosen.has(t.id)); };
        return c;
      })] : [];
    }));
    const syncScope = () => { tagBox.style.display = scopeSel.value === 'tags' ? '' : 'none'; };
    scopeSel.onchange = syncScope;
    syncScope();
    const mode = el('select', {}, el('option', { value: 'copy', text: 'Keep everything (files removed from the library stay in the backup)' }),
      el('option', { value: 'mirror', text: 'Mirror (files removed from the library go to the backup archive folder)' }));
    mode.value = v.mode;
    const verify = el('input', { type: 'checkbox' });
    verify.checked = !!v.verify;
    // schedule: a reminder, or the backup runs by itself while Kurator is open
    const when = el('select', {}, el('option', { value: 'off', text: 'Only when I start it' }),
      el('option', { value: 'remind', text: 'Remind me every' }), el('option', { value: 'auto', text: 'Back up by itself every' }));
    when.value = v.every_days ? (v.auto ? 'auto' : 'remind') : 'off';
    const every = el('select', {}, [[1, 'day'], [2, '2 days'], [3, '3 days'], [7, 'week'], [14, '2 weeks'], [30, 'month']]
      .map(([d, t]) => el('option', { value: String(d), text: t })));
    every.value = String(v.every_days || 7);
    if (![...every.options].some((o) => o.value === every.value)) every.append(el('option', { value: every.value, text: `${every.value} days` }));
    const syncWhen = () => { every.style.display = when.value === 'off' ? 'none' : ''; };
    when.onchange = syncWhen;
    syncWhen();
    const dedupe = el('input', { type: 'checkbox' });
    dedupe.checked = v.dedupe == null ? true : !!v.dedupe;
    const msg = el('div');

    const save = async () => {
      const payload = {
        name: name.value, policy: policy.value, mode: mode.value, verify: verify.checked, dedupe: dedupe.checked,
        every_days: when.value === 'off' ? null : +every.value, auto: when.value === 'auto',
        layout: layoutTags.checked ? 'tags' : 'original', tag_template: tmpl.value || '{tagpath}',
        scope: scopeSel.value === 'tags' ? { tags: [...chosen] } : {},
        targets: v.targets.filter((t) => (t.location || '').trim()).map((t) => ({ id: t.id, kind: t.kind || 'local', location: t.location, reserve_gb: (t.reserve ?? 1024 ** 3) / 1024 ** 3 })),
      };
      try { isNew ? await api.createBackup(payload) : await api.updateBackup(s.id, payload); } catch (e) { setKids(msg, err(e)); return; }
      app.toast('Backup set saved');
      showList();
    };

    setKids(body,
      el('label', {}, 'Name', name),
      el('h3', { text: 'Where: targets' }),
      el('div', { class: 'p-hint small', text: 'Folders on external disks or a NAS, or cloud accounts (Google Drive and others, through rclone). New files go to the first target with room; add more targets - e.g. several Google accounts - to spread a large library.' }),
      tlist,
      el('div', { class: 'row' }, btn('+ Add target', () => { v.targets.push({ location: '', reserve: 1024 ** 3 }); renderTargets(); }), el('span', { class: 'grow' }), el('label', { class: 'chk' }, 'Spread new files:', policy)),
      cloudBox,
      el('h3', { text: 'How: folder layout in the backup' }),
      el('label', { class: 'chk' }, layoutOrig, 'Original folders (Library/Downloads/folder/…)'),
      el('label', { class: 'chk' }, layoutTags, 'Tag folders:', tmpl, presets),
      el('div', { class: 'p-hint small', text: 'e.g. Equipment/Weapons/sword.png, or Game A/Icons/… with {project}/{tagpath}. Tokens: {tagpath} {tag} {subtag} (from Subjects) {project} {status} {source} {year} {month}. A file with several tags goes to the folder of its first tag (move-rule order, then A–Z); untagged files go to _untagged/ in their original folders. Re-tagging later moves the backup copy - nothing is copied twice.' }),
      el('h3', { text: 'What' }),
      el('div', { class: 'row' }, scopeSel), tagBox,
      el('label', { class: 'chk' }, mode),
      el('label', { class: 'chk' }, verify, 'Verify every copy (read it back and compare checksums)'),
      el('label', { class: 'chk', title: 'The same image in several folders is stored once; restoring puts it back in every folder' }, dedupe,
        'Store identical files once (copies in other folders point to it)'),
      el('h3', { text: 'When' }),
      el('div', { class: 'row' }, when, every),
      el('div', { class: 'p-hint small', text: 'A reminder shows in the status bar when the backup is due. "By itself" starts the backup while Kurator is open, when the target disk is connected (otherwise you get the reminder). Each run only copies what changed.' }),
      msg,
      footer(btn('Back', showList), btn(isNew ? 'Create backup set' : 'Save', save, 'primary')));
  }

  // ------------------------------------------------------------------- plan
  async function showPlan(s) {
    setKids(body, el('div', { class: 'p-hint small', text: `Checking what needs to be backed up for "${s.name}"…` }));
    let p;
    try { p = await api.planBackup(s.id); } catch (e) { setKids(body, err(e), footer(btn('Back', showList))); return; }
    const c = p.counts;
    const work = c.new + c.changed + c.missing + c.relocate + c.archive;
    const rows = p.targets.map((t) => {
      const free = t.space?.free;
      const after = free != null ? free - t.needed : null;
      return el('tr', { class: t.available ? '' : 'offline' }, el('td', { text: t.location }),
        el('td', { text: t.available ? (free != null ? fmtBytes(free) : 'unknown') : 'not connected' }),
        el('td', { text: `${fmtBytes(t.needed)} (${fmtNum(t.files)} files)` }),
        el('td', { class: after != null && after < t.reserve ? 'warn' : '', text: after != null ? fmtBytes(after) : '—' }));
    });
    const reasons = {};
    for (const x of p.skipped) reasons[x.reason] = (reasons[x.reason] || 0) + 1;
    const ex = p.examples.slice(0, 200).map((o) => el('div', { class: 'mv' },
      el('span', { class: 'to', text: o.op === 'copy' ? `${o.reason === 'new' ? '+' : '↻'} ${o.dest}` : o.op === 'relocate' ? `→ ${o.from}  ⇒  ${o.to}` : o.op === 'alias' ? `= ${o.dest} (identical, stored once)` : `⌫ ${o.from} (to archive)` })));
    setKids(body,
      el('div', { class: 'move-sum' },
        el('b', { text: work ? `${fmtNum(c.new)} new · ${fmtNum(c.changed)} changed · ${fmtNum(c.missing)} missing in backup · ${fmtNum(c.relocate)} to move inside the backup${c.archive ? ` · ${fmtNum(c.archive)} to archive` : ''}` : 'Everything is up to date ✓' }),
        el('div', { text: `${fmtBytes(p.bytes)} to copy · ${fmtNum(c.unchanged)} unchanged${c.kept ? ` · ${fmtNum(c.kept)} kept from earlier (no longer in the library)` : ''}${c.relink ? ` · ${fmtNum(c.relink)} recognised after being moved outside Kurator` : ''}${c.same_as ? ` · ${fmtNum(c.same_as)} identical copies stored once` : ''}` }),
        p.same_disk?.length ? el('div', { class: 'warn', text: `On the same disk: ${p.same_disk.map((g) => g.join(' + ')).join('; ')} - they share one disk's free space, so spreading over them adds no room.` }) : null,
        p.unavailable.length ? el('div', { class: 'warn', text: `Not connected: ${p.unavailable.join(', ')} - files stored there are left as they are.` }) : null,
        Object.keys(reasons).length ? el('div', { class: 'warn', text: 'Skipped: ' + Object.entries(reasons).map(([r, n]) => `${fmtNum(n)} ${r}`).join(', ') + '. Add another target or free some space.' }) : null),
      el('table', { class: 'kv space' }, el('tr', {}, el('th', { text: 'Target' }), el('th', { text: 'Free now' }), el('th', { text: 'Will be written' }), el('th', { text: 'Free after' })), rows),
      ex.length ? el('div', { class: 'move-list' }, ex) : null,
      footer(btn('Back', showList), work ? btn(`Start backup (${fmtBytes(p.bytes)})`, async () => {
        try { await api.runBackup(s.id); } catch (e) { app.toast(e.message); return; }
        showProgress();
      }, 'primary') : null));
  }

  // ---------------------------------------------------------------- restore
  function showRestore(s) {
    const missing = el('input', { type: 'radio', name: 'rmode' });
    missing.checked = true;
    const toFolder = el('input', { type: 'radio', name: 'rmode' });
    const folder = el('input', { type: 'text', placeholder: 'D:\\Restored', spellcheck: 'false' });
    folder.oninput = () => { toFolder.checked = true; };
    const out = el('div');
    const mode = () => (missing.checked ? 'missing' : 'folder');
    const check = async () => {
      setKids(out, el('div', { class: 'p-hint small', text: 'Checking…' }));
      let p;
      try { p = await api.planRestore(s.id, mode(), folder.value); } catch (e) { setKids(out, err(e)); return; }
      setKids(out, el('div', { class: 'move-sum' },
        el('b', { text: p.count ? `${fmtNum(p.count)} files (${fmtBytes(p.bytes)}) will be restored` : 'Nothing to restore - all files are in place ✓' }),
        p.free != null ? el('div', { class: p.enough_space ? 'ok' : 'warn', text: `${fmtBytes(p.free)} free at the destination` }) : null,
        p.offline_targets.length ? el('div', { class: 'warn', text: `Connect these targets to restore everything: ${p.offline_targets.join(', ')}` }) : null,
        el('div', { class: 'muted', text: 'Existing files are never overwritten. Dates are restored too.' })),
      p.count ? el('div', { class: 'move-list' }, p.examples.slice(0, 200).map((i) => el('div', { class: 'mv' }, el('span', { class: 'to', text: i.to })))) : null,
      p.count && p.enough_space ? footer(btn(`Restore ${fmtNum(p.count)} files`, async () => {
        try { await api.runRestore(s.id, mode(), folder.value); } catch (e) { app.toast(e.message); return; }
        showProgress();
      }, 'primary')) : null);
    };
    setKids(body,
      el('h3', { text: `Restore from "${s.name}"` }),
      el('label', { class: 'chk' }, missing, 'Put back files that are missing from their original place'),
      el('label', { class: 'chk' }, toFolder, 'Copy the whole backup into a folder:', folder),
      footer(btn('Back', showList), btn('Check', check, 'primary')),
      out);
  }

  // --------------------------------------------------------------- progress
  function showProgress() {
    const bar = el('div', { class: 'pbar' }, el('span'));
    const line = el('div');
    const cur = el('div', { class: 'muted small cur' });
    const errs = el('div');
    const cancel = btn('Cancel', async () => { await api.cancelJob(); cancel.disabled = true; });
    const done = footer(cancel);
    setKids(body, el('h3', { text: 'Working…' }), bar, line, cur, errs, done);
    const tick = async () => {
      let st;
      try { st = await api.job(); } catch { return; }
      const pct = st.bytes_total ? Math.min(100, (100 * st.bytes_done) / st.bytes_total) : (st.files_total ? (100 * st.files_done) / st.files_total : 0);
      bar.firstChild.style.width = `${pct}%`;
      const secs = st.started ? (Date.now() / 1000 - st.started) : 0;
      const rate = secs > 1 ? st.bytes_done / secs : 0;
      const eta = rate > 0 && st.running ? Math.round((st.bytes_total - st.bytes_done) / rate) : null;
      line.textContent = `${st.kind === 'restore' ? 'Restoring' : 'Backing up'}${st.set ? ` "${st.set}"` : ''}: ${fmtNum(st.files_done)} / ${fmtNum(st.files_total)} files · ${fmtBytes(st.bytes_done)} / ${fmtBytes(st.bytes_total)}${rate ? ` · ${fmtBytes(rate)}/s` : ''}${eta != null ? ` · about ${eta > 90 ? Math.round(eta / 60) + ' min' : eta + ' s'} left` : ''}`;
      cur.textContent = st.current || '';
      if (st.errors.length) setKids(errs, el('div', { class: 'warn', text: `${st.errors.length} problem(s):` }), el('div', { class: 'move-list' }, st.errors.slice(0, 50).map((e) => el('div', { class: 'mv err', text: e }))));
      if (!st.running) {
        clearInterval(timer);
        const r = st.result || {};
        body.querySelector('h3').textContent = st.cancelled ? 'Cancelled' : 'Finished';
        cur.textContent = st.kind === 'restore'
          ? `${fmtNum(r.restored || 0)} restored${r.failed ? `, ${r.failed} failed` : ''}`
          : `${fmtNum(r.copied || 0)} copied · ${fmtNum(r.relocated || 0)} moved inside the backup · ${fmtNum(r.archived || 0)} archived${r.failed ? ` · ${r.failed} failed` : ''}${r.skipped ? ` · ${r.skipped} skipped (no space)` : ''}`;
        setKids(done, btn('Back to backups', showList, 'primary'));
        app.refreshState();
      }
    };
    clearInterval(timer);
    timer = setInterval(tick, 700);
    tick();
  }

  showList();
  return d;
}
