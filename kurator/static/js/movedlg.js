// "Move to folder" / "Copy to folder" dialog: choose destination + options, preview exactly what
// happens, then move or copy. Moves stay inside the library; copies may go anywhere, also "for
// delivery" (private details out, author and licence in, one zip). Settings are remembered per project.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { destField } from './folderpick.js';
import { el, fmtBytes, fmtNum, prefs, projPrefs, setKids } from './util.js';

export function openMoveDialog(app, ids) { openTransferDialog(app, ids, 'move'); }
export function openCopyDialog(app, ids) { openTransferDialog(app, ids, 'copy'); }

function openTransferDialog(app, ids, mode) {
  const copy = mode === 'copy';
  const verb = copy ? 'Copy' : 'Move';
  if (!ids.length) { app.toast(`Select images to ${mode} first`); return; }
  const pp = projPrefs(app);
  const recent = pp.get(`${mode}.recent`, []);
  const dest = el('input', { type: 'text', spellcheck: 'false',
    placeholder: copy ? 'Any folder, e.g. D:\\Delivery\\Client A (inside the library the copies keep their tags)' : 'Folder inside your library, e.g. D:\\Art\\Creatures',
    value: recent[0] || app.query.folder || '' });
  const chk = (key, d) => { const c = el('input', { type: 'checkbox' }); c.checked = pp.get(key, d); return c; };
  const byYear = chk(`${mode}.byYear`, false);
  const prefix = chk(`${mode}.prefix`, false);
  const keepTags = chk('copy.keepTags', true);
  const clean = chk('copy.clean', false);
  const credit = chk('copy.credit', false);
  const zip = chk('copy.zip', false);
  const zipName = el('input', { type: 'text', spellcheck: 'false', value: pp.get('copy.zipName', ''), placeholder: 'zip name, e.g. Client A - round 2', style: { width: '260px' } });
  const quick = el('div', { class: 'chip-list' }, [...new Set([...recent.slice(0, 5), ...app.sidebar.pins])].slice(0, 10).map((p) =>
    el('button', { class: 'chip', type: 'button', title: p, text: p.split(/[\\/]/).filter(Boolean).pop() || p,
      onclick: () => { dest.value = p; invalidate(); } })));
  const out = el('div', { class: 'move-preview' });
  let plan = null;

  const invalidate = () => {
    plan = null;
    zipName.disabled = !zip.checked;
    keepTags.disabled = zip.checked;
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = verb;
    out.replaceChildren(el('div', { class: 'p-hint small', text: `Press Preview to see exactly what will happen. Nothing is ${copy ? 'copied' : 'moved'} before you confirm.` }));
  };
  const body = () => ({ ids, dest: dest.value.trim(), by_year: byYear.checked, date_prefix: prefix.checked,
    ...(copy ? { keep_tags: keepTags.checked, clean: clean.checked, credit: credit.checked, zip: zip.checked ? zipName.value.trim() : null } : {}) });

  const preview = async () => {
    if (!dest.value.trim()) { dest.focus(); return; }
    out.replaceChildren(el('div', { class: 'p-hint small', text: 'Planning…' }));
    try { plan = await (copy ? api.planCopy(body()) : api.planMove(body())); } catch (e) { out.replaceChildren(el('div', { class: 'err', text: e.message })); return; }
    const entries = copy ? plan.copies : plan.moves;
    const rel = (p) => (p.startsWith(plan.dest) ? p.slice(plan.dest.length).replace(/^[\\/]/, '') : p);
    const base = (p) => p.split(/[\\/]/).pop();
    const list = el('div', { class: 'move-list' }, entries.map((m) =>
      el('div', { class: 'mv' }, el('span', { class: 'from', text: base(m.from) }), el('span', { class: 'arrow', text: copy ? '⇒' : '→' }),
        el('span', { class: 'to' + (m.renamed && !zip.checked ? ' renamed' : ''), text: zip.checked ? rel(m.want || m.to) : rel(m.to) }))));
    const reasons = {};
    for (const s of plan.skipped) reasons[s.reason] = (reasons[s.reason] || 0) + 1;
    const delivery = [clean.checked && 'private details removed', credit.checked && 'author and licence written in'].filter(Boolean).join(', ');
    const how = copy
      ? el('div', { class: 'ok', text: zip.checked
        ? `One zip archive "${zipName.value.trim() || 'delivery_…'}.zip" in this folder${delivery ? ` (${delivery}: JPEG, PNG, WebP)` : ''}. Ctrl+Z deletes it.`
        : delivery ? `Copies for delivery: ${delivery} (JPEG, PNG and WebP - the picture itself is not re-encoded; other files are copied as they are; no sidecars).`
          : plan.in_library
            ? `Inside the library: the copies show up at once${keepTags.checked ? ', with the tags, source, author and licence of the originals' : ' (without tags)'}. Dates are kept, every copy is checked byte-for-byte.`
            : 'Outside the library: plain copies, dates kept, every copy checked byte-for-byte. XMP sidecars come along.' })
      : plan.cross_device ? el('div', { class: 'warn', text: 'Other disk: files are copied, checked byte-for-byte, then removed from the source. Dates are kept.' })
        : el('div', { class: 'ok', text: 'Same disk: instant move, all dates kept.' });
    setKids(out,
      el('div', { class: 'move-sum' },
        el('b', { text: `${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'} will be ${copy ? (zip.checked ? 'zipped' : 'copied') : 'moved'}` }), ` · ${fmtBytes(plan.bytes)} · into ${plan.dest}`,
        how,
        Object.keys(reasons).length ? el('div', { class: 'muted', text: 'Skipped: ' + Object.entries(reasons).map(([r, c]) => `${c} ${r}`).join(', ') }) : null,
        !zip.checked && entries.some((m) => m.renamed) ? el('div', { class: 'muted', text: 'Highlighted names are changed (date prefix or " (2)" to avoid overwriting).' }) : null),
      list,
      plan.count > entries.length ? el('div', { class: 'muted', text: `… and ${fmtNum(plan.count - entries.length)} more` }) : null);
    d.buttons.go.disabled = plan.count === 0;
    d.buttons.go.textContent = `${verb} ${fmtNum(plan.count)} file${plan.count === 1 ? '' : 's'}${zip.checked ? ' into a zip' : ''}`;
  };

  const go = async (close) => {
    if (!plan) return;
    d.buttons.go.disabled = true;
    d.buttons.go.textContent = copy ? 'Copying…' : 'Moving…';
    let r;
    try { r = await (copy ? api.copy(body()) : api.move(body())); } catch (e) { app.toast(e.message); d.buttons.go.disabled = false; return; }
    pp.set(`${mode}.recent`, [plan.dest, ...recent.filter((p) => p !== plan.dest)].slice(0, 10));
    pp.set(`${mode}.byYear`, byYear.checked);
    pp.set(`${mode}.prefix`, prefix.checked);
    if (copy) {
      for (const [k, c] of [['keepTags', keepTags], ['clean', clean], ['credit', credit], ['zip', zip]]) pp.set(`copy.${k}`, c.checked);
      pp.set('copy.zipName', zipName.value.trim());
    }
    close();
    if (copy) app.onCopied(r); else app.onMoved(r);
  };

  dest.addEventListener('input', () => invalidate());
  dest.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); plan ? go(d.close) : preview(); } });
  for (const c of [byYear, prefix, keepTags, clean, credit, zip]) c.onchange = invalidate;
  zipName.addEventListener('input', invalidate);

  const d = openDialog({
    title: `${verb} ${fmtNum(ids.length)} item${ids.length > 1 ? 's' : ''} to folder`,
    width: 700,
    body: el('div', { class: 'form' },
      el('label', {}, 'Destination (created if missing)', destField(app, dest, { title: `${verb} to…`, extra: [...prefs.get(`${copy ? 'move' : 'copy'}.recent`, []), ...app.sidebar.nodes.map((n) => n.path)] })),
      quick,
      el('label', { class: 'chk' }, byYear, 'Year subfolders (…/2019/)'),
      el('label', { class: 'chk' }, prefix, 'Put the date in the file name (2019-04-12_name.png) when the name has no date yet'),
      copy ? el('label', { class: 'chk' }, keepTags, 'Copies in the library keep the tags, source, author and licence') : null,
      copy ? el('details', { class: 'delivery', open: clean.checked || credit.checked || zip.checked || null },
        el('summary', { text: 'For delivery' }),
        el('label', { class: 'chk' }, clean, 'Remove private details: location, serial numbers, owner, editing history, AI prompts and workflows'),
        el('label', { class: 'chk' }, credit, 'Write the author and licence into the files (from Source & licence)'),
        el('label', { class: 'chk' }, zip, 'As one zip archive', zipName),
        el('div', { class: 'p-hint small', text: 'JPEG, PNG and WebP are changed in their metadata only - colours, profile, orientation and dates stay. Other files are copied as they are.' })) : null,
      out),
    buttons: [
      { label: 'Cancel' },
      { label: 'Preview', onClick: preview },
      { label: verb, id: 'go', primary: true, disabled: true, onClick: go },
    ],
  });
  invalidate();
}
