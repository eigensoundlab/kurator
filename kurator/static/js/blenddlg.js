// .blend files, looked into by Blender: what each material is made of - image textures (their size,
// missing files), procedural nodes, plain values - and what the 3D preview keeps of it (the glTF it
// sees carries image textures and values, not procedural nodes); the meshes as exported, UV maps, lights,
// camera and world.

import { openDialog } from './dialog.js';
import { el, fmtNum } from './util.js';

const INPUTS = { base_color: 'Base colour', metallic: 'Metallic', roughness: 'Roughness', normal: 'Normal', emission: 'Emission',
  alpha: 'Alpha', transmission: 'Transmission', subsurface: 'Subsurface', coat: 'Coat', sheen: 'Sheen', surface: 'Surface' };

const swatch = (v) => {
  const [r, g, b] = v.map((x) => Math.round(255 * Math.min(1, Math.max(0, x)) ** (1 / 2.2)));
  return el('span', { class: 'bl-swatch', style: { background: `rgb(${r},${g},${b})` }, title: v.join(', ') });
};

const imageText = (i) => `${i.file || i.name}${i.missing ? '' : ` ${i.size[0]}×${i.size[1]}`}${i.packed ? ' (packed)' : ''}`;

/** One input: what feeds it, and whether the preview shows it. */
function inputRow(key, d) {
  const what = [];
  let verdict = null;   // [class, text]
  if (d.kind === 'value') {
    what.push(Array.isArray(d.value) ? swatch(d.value) : el('span', { text: String(d.value) }));
  } else {
    for (const i of d.images || []) what.push(el('span', { class: i.missing ? 'bl-missing' : '', text: imageText(i), title: i.missing ? 'This picture is not where the .blend expects it' : i.colorspace }));
    if (d.procedural?.length) what.push(el('span', { class: 'bl-proc', text: d.procedural.join(', ') }));
    if (d.adjust?.length) what.push(el('span', { class: 'muted', text: `through ${d.adjust.join(', ')}` }));
    if (d.kind === 'vertex colours') what.push(el('span', { text: 'vertex colours' }));
    if (!what.length) what.push(el('span', { class: 'muted', text: 'nodes' }));
    const missing = (d.images || []).some((i) => i.missing);
    verdict = missing ? ['bad', 'missing file']
      : d.kind === 'procedural' ? ['bad', 'not in the preview - bake it to a texture']
        : d.kind === 'mixed' ? ['warn', 'the preview shows the texture only']
          : d.kind === 'texture' && d.adjust?.length ? ['warn', 'the preview shows the plain texture']
            : d.kind === 'texture' ? ['ok', 'in the preview'] : ['warn', 'the preview may differ'];
  }
  return el('tr', {},
    el('td', { class: 'muted', text: INPUTS[key] || key }),
    el('td', {}, ...what.flatMap((w, i) => (i ? [' · ', w] : [w]))),
    el('td', { class: `bl-verdict ${verdict ? verdict[0] : ''}`, text: verdict ? verdict[1] : '' }));
}

export function summary(r) {
  const mats = r.materials || [];
  const proc = mats.filter((m) => Object.values(m.inputs).some((d) => d.kind === 'procedural' || d.kind === 'mixed')).length;
  const parts = [`${fmtNum(mats.length)} material${mats.length === 1 ? '' : 's'}`];
  if (proc) parts.push(`${fmtNum(proc)} procedural`);
  if (r.missing_images?.length) parts.push(`${fmtNum(r.missing_images.length)} missing picture${r.missing_images.length === 1 ? '' : 's'}`);
  parts.push(`${fmtNum(r.triangles)} triangles`);
  return parts.join(' · ');
}

export function openBlendReport(app, name, r) {
  const mats = (r.materials || []).map((m) => el('div', { class: 'bl-mat' },
    el('div', { class: 'bl-mat-head' }, el('b', { text: m.name }),
      el('span', { class: 'muted', text: ` · ${m.shader || 'no shader'}${m.objects > 1 ? ` · ${fmtNum(m.objects)} objects` : ''}${m.blend && m.blend !== 'OPAQUE' ? ` · ${m.blend.toLowerCase()}` : ''}` }),
      m.shader && !m.principled ? el('span', { class: 'bl-verdict warn', text: ' the preview approximates this shader' }) : null),
    el('table', { class: 'bl-table' }, Object.entries(m.inputs).map(([k, d]) => inputRow(k, d)))));
  const meshes = el('table', { class: 'bl-table bl-meshes' },
    el('tr', {}, ...['Object', 'Triangles', 'Vertices', 'UV maps', 'Modifiers'].map((t) => el('th', { text: t }))),
    (r.meshes || []).map((m) => el('tr', {},
      el('td', { text: m.name }), el('td', { text: fmtNum(m.triangles) }), el('td', { text: fmtNum(m.vertices) }),
      el('td', { class: m.uv_maps.length ? '' : 'bl-missing', text: m.uv_maps.length ? m.uv_maps.join(', ') : 'none' }),
      el('td', { class: 'muted', text: m.modifiers.join(', ') }))));
  const scene = [
    r.camera ? `camera ${r.camera}` : 'no camera',
    `${fmtNum(r.lights)} light${r.lights === 1 ? '' : 's'}`,
    r.environment ? `world: ${r.environment}` : r.world ? `world: ${r.world}` : 'no world',
    r.armatures ? `${fmtNum(r.armatures)} armature${r.armatures === 1 ? '' : 's'}` : null,
    r.actions ? `${fmtNum(r.actions)} animation${r.actions === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' · ');
  openDialog({
    title: `Materials - ${name}`, width: 760,
    body: el('div', { class: 'bl-report' },
      el('div', { class: 'p-hint small', text: `${summary(r)} · read by Blender ${r.blender}. The 3D view shows what glTF carries: image textures and values. Procedural nodes need baking to a texture to show there - Render in Blender shows them as Blender does.` }),
      r.missing_images?.length ? el('div', { class: 'warn small', text: `Missing pictures: ${r.missing_images.join(', ')} - Blender shows them pink.` }) : null,
      mats.length ? mats : el('div', { class: 'muted', text: 'No materials.' }),
      el('h3', { text: 'Meshes (with modifiers, as exported)' }), meshes,
      el('div', { class: 'p-hint small', text: `${fmtNum(r.objects)} objects · ${scene}` })),
    buttons: [{ label: 'Close', primary: true }],
  });
}
