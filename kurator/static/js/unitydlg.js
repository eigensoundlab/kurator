// Unity sprite import settings for the selected pictures (inside a Unity project): writes their
// .meta files the way Unity's inspector would, keeping each file's GUID. Undo restores them.

import { api } from './api.js';
import { openDialog } from './dialog.js';
import { el, fmtNum, prefs, setKids } from './util.js';

export function openUnityDialog(app, ids) {
  if (!ids.length) { app.toast('Select some pictures first'); return; }
  const P = prefs.get('unity', {});
  const field = (label, input, hint) => el('label', { class: 'sf' }, el('span', { text: label }), input, hint ? el('small', { class: 'muted', text: hint }) : null);
  const sel = (opts, v) => { const s = el('select', {}, opts.map(([k, t]) => el('option', { value: k, text: t }))); s.value = v; return s; };
  const chk = (v) => { const c = el('input', { type: 'checkbox' }); c.checked = !!v; return c; };
  const type = sel([['sprite', 'Sprite (2D and UI)'], ['default', 'Default (texture)']], P.type || 'sprite');
  const ppu = el('input', { type: 'number', min: '1', max: '10000', value: String(P.ppu ?? 100), style: { width: '90px' } });
  const filter = sel([['bilinear', 'Bilinear'], ['point', 'Point (pixel art)'], ['trilinear', 'Trilinear']], P.filter || 'bilinear');
  const comp = sel([['normal', 'Normal quality'], ['high', 'High quality'], ['low', 'Low quality'], ['none', 'None (exact pixels)']], P.compression || 'normal');
  const mips = chk(P.mipmaps);
  const maps = chk(P.link_maps !== false);
  const pixel = el('button', { class: 'btn small', type: 'button', text: 'Pixel art preset', title: 'Point filter, no compression, no mip maps',
    onclick: () => { filter.value = 'point'; comp.value = 'none'; mips.checked = false; } });
  const msg = el('div');
  const d = openDialog({
    title: `Unity import settings - ${fmtNum(ids.length)} picture${ids.length === 1 ? '' : 's'}`, width: 560,
    body: el('div', { class: 'sheetcol' },
      el('div', { class: 'p-hint small', text: 'Writes the .meta file next to each picture inside a Unity project, the way the Inspector would. Existing .meta files keep their GUID (scenes and prefabs keep working) and all other settings. Unity re-imports when its window gets focus.' }),
      field('Texture type', type), field('Pixels per unit', ppu), field('Filter', filter), field('Compression', comp),
      el('label', { class: 'chk' }, mips, 'Generate mip maps'),
      el('label', { class: 'chk' }, maps, 'Link _normal / _mask maps as secondary textures (2D lights)'),
      el('div', { class: 'row' }, pixel), msg),
    buttons: [{ label: 'Close' }, { label: 'Apply', primary: true, onClick: async () => {
      const body = { ids, sprite: type.value === 'sprite', ppu: +ppu.value || 100, filter: filter.value, compression: comp.value,
        mipmaps: mips.checked, link_maps: maps.checked };
      prefs.set('unity', { type: type.value, ppu: body.ppu, filter: body.filter, compression: body.compression, mipmaps: body.mipmaps, link_maps: body.link_maps });
      try {
        const r = await api.unitySettings(body);
        setKids(msg, el('div', { class: 'ok', text: `${fmtNum(r.changed)} updated, ${fmtNum(r.created)} new .meta file${r.created === 1 ? '' : 's'}` }),
          r.outside ? el('div', { class: 'muted', text: `${fmtNum(r.outside)} not inside a Unity project - left alone` }) : null,
          r.undo ? el('button', { class: 'btn small', text: 'Undo', onclick: async () => {
            try { const u = await api.unityUndo(r.undo); setKids(msg, el('div', { class: 'ok', text: `Restored ${fmtNum(u.restored)} file(s)` })); } catch (e) { app.toast(e.message); }
          } }) : null);
      } catch (e) { setKids(msg, el('div', { class: 'err', text: e.message })); }
    } }],
  });
  return d;
}
