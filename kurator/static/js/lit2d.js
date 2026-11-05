// Lit preview of 2D art with companion maps (hero.png + hero_normal / _glow / _ao / _depth / _sky ...):
// a point light follows the mouse, the normal map (or one made from the depth map) shades the
// picture, glow maps shine with a soft halo, AO darkens the ambient light, the depth map shifts
// the picture a little as the mouse moves (parallax), background layers sit behind it.
// WebGL2 on a canvas that lies exactly over the viewer's image (same size and transform).

import { el, prefs, setKids } from './util.js';

const VS = `#version 300 es
in vec2 aPos;
out vec2 vUV;
void main() { vUV = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5); gl_Position = vec4(aPos, 0.0, 1.0); }`;

const FS = `#version 300 es
precision highp float;
in vec2 vUV;
uniform sampler2D uBase, uNormal, uEmis, uAO, uDepth, uSpec, uBg, uShow;
uniform int uHas[6];           // normal, emissive, ao, depth, specular, background
uniform int uView;             // 0 lit, 1 a single map (uShow)
uniform vec3 uLight;           // x, y in picture coordinates (0..1, y down), z = height
uniform vec3 uLightCol;
uniform float uRadius, uAmbient, uGlow, uParallax, uFlipG, uAspect;
uniform vec2 uOff;             // mouse offset from the centre (-1..1) for parallax
uniform vec2 uTexel;
out vec4 o;
float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
void main() {
  if (uView == 1) { o = texture(uShow, vUV); return; }
  vec2 uv = vUV;
  if (uHas[3] == 1 && uParallax > 0.0) uv -= (texture(uDepth, vUV).r - 0.5) * uParallax * 0.04 * uOff;
  vec4 base = texture(uBase, uv);
  vec3 n = vec3(0.0, 0.0, 1.0);
  if (uHas[0] == 1) {
    n = texture(uNormal, uv).xyz * 2.0 - 1.0;
    n.y *= -uFlipG;                                     // maps are y-up (OpenGL); the picture is y-down
  } else if (uHas[3] == 1) {
    float dx = texture(uDepth, uv + vec2(uTexel.x, 0.0)).r - texture(uDepth, uv - vec2(uTexel.x, 0.0)).r;
    float dy = texture(uDepth, uv + vec2(0.0, uTexel.y)).r - texture(uDepth, uv - vec2(0.0, uTexel.y)).r;
    n = vec3(-dx * 6.0, -dy * 6.0, 1.0);
  }
  n = normalize(n);
  vec2 d = (uLight.xy - uv) * vec2(uAspect, 1.0);
  vec3 L = vec3(d, uLight.z);
  float dist = length(d);
  float att = 1.0 / (1.0 + pow(dist / max(uRadius, 0.001), 2.0));
  vec3 Ln = normalize(L);
  float diff = max(dot(n, Ln), 0.0) * att;
  float occ = uHas[2] == 1 ? texture(uAO, uv).r : 1.0;
  vec3 lit = base.rgb * (uAmbient * occ + uLightCol * diff * 1.4);
  if (uHas[4] == 1) {
    vec3 H = normalize(Ln + vec3(0.0, 0.0, 1.0));
    lit += uLightCol * texture(uSpec, uv).r * pow(max(dot(n, H), 0.0), 40.0) * att;
  }
  float a = base.a;
  if (uHas[5] == 1) {                                    // background layer: further away, moves less
    vec4 bg = texture(uBg, vUV - uOff * uParallax * 0.01);
    vec3 bgLit = bg.rgb * (uAmbient + uLightCol * att * 0.6);
    lit = mix(bgLit, lit, a);
    a = max(a, bg.a);
  }
  if (uHas[1] == 1) {
    vec3 e = texture(uEmis, uv).rgb;
    vec3 halo = textureLod(uEmis, uv, 3.0).rgb * 0.6 + textureLod(uEmis, uv, 5.0).rgb * 0.5;
    vec3 g = (e + halo) * uGlow;
    lit += g;
    a = max(a, clamp(lum(halo * uGlow) * 1.5, 0.0, 1.0));
  }
  o = vec4(lit * a, a);                                 // premultiplied
}`;

const SLOT = { normal: 1, emissive: 2, ao: 3, depth: 4, specular: 5, background: 6 };
const HAS = ['normal', 'emissive', 'ao', 'depth', 'specular', 'background'];
const LIGHT_COLS = { warm: [1.0, 0.86, 0.68], white: [1, 1, 1], cool: [0.7, 0.82, 1.0], torch: [1.0, 0.62, 0.3] };

export class Lit2D {
  constructor(stage, viewer) {
    this.viewer = viewer;
    this.canvas = el('canvas', { class: 'lit2d' });
    stage.append(this.canvas);
    this.gl = this.canvas.getContext('webgl2', { premultipliedAlpha: true, alpha: true });
    this.ok = !!this.gl;
    this.tex = {};
    this.group = null;
    this.view = 'lit';
    const P = prefs.get('lit2d', {});
    this.opt = { height: P.height ?? 0.25, radius: P.radius ?? 0.6, ambient: P.ambient ?? 0.35, glow: P.glow ?? 1.0,
      parallax: P.parallax ?? 1.0, flip: !!P.flip, color: P.color || 'warm' };
    this.light = [0.35, 0.35];
    this.offset = [0, 0];
    this.onChange = () => {};
    if (this.ok) this.init();
    this.canvas.addEventListener('pointermove', (e) => this.mouse(e));
    stage.addEventListener('pointermove', (e) => { if (this.on) this.mouse(e); });
  }

  init() {
    const gl = this.gl;
    const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    this.prog = p;
    const U = (n) => gl.getUniformLocation(p, n);
    this.u = Object.fromEntries(['uHas', 'uView', 'uLight', 'uLightCol', 'uRadius', 'uAmbient', 'uGlow', 'uParallax', 'uFlipG',
      'uAspect', 'uOff', 'uTexel'].map((n) => [n, U(n)]));
    this.samplers = ['uBase', 'uNormal', 'uEmis', 'uAO', 'uDepth', 'uSpec', 'uBg', 'uShow'].map(U);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    const b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(p, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
    this.blank = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.blank);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
  }

  savePrefs() { prefs.set('lit2d', this.opt); }

  /** Show the maps of a picture (group from /api/items/{id}/maps2d). */
  async load(group) {
    if (!this.ok) throw new Error('This browser has no WebGL 2');
    this.clear();
    this.group = group;
    const token = (this.token = (this.token || 0) + 1);
    const want = [{ key: 'base', url: group.main.url }, ...group.maps.map((m) => ({ key: `m${m.id}`, url: m.url, role: m.role }))];
    await Promise.all(want.map((w) => this.texture(w.key, w.url, token)));
    if (token !== this.token) return;
    const b = this.tex.base;
    if (!b) throw new Error('the picture could not be loaded');
    const k = Math.min(1, 2048 / Math.max(b.w, b.h));
    this.canvas.width = Math.round(b.w * k);
    this.canvas.height = Math.round(b.h * k);
    this.draw();
    this.onChange();
  }

  texture(key, url, token) {
    return new Promise((res) => {
      const img = new Image();
      img.onload = () => {
        if (token !== this.token) { res(); return; }
        const gl = this.gl;
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        this.tex[key] = { t, w: img.naturalWidth, h: img.naturalHeight };
        res();
      };
      img.onerror = () => res();
      img.src = url;
    });
  }

  clear() {
    this.token = (this.token || 0) + 1;
    if (this.ok) for (const v of Object.values(this.tex)) this.gl.deleteTexture(v.t);
    this.tex = {};
    this.group = null;
    this.view = 'lit';
  }

  set on(v) { this._on = v; this.canvas.classList.toggle('on', !!v); if (v) this.draw(); }
  get on() { return !!this._on; }

  roleTex(role) {
    const m = this.group?.maps.find((x) => x.role === role && this.tex[`m${x.id}`]);
    return m ? this.tex[`m${m.id}`] : null;
  }

  mouse(e) {
    if (!this.on || !this.group) return;
    const r = this.canvas.getBoundingClientRect();
    if (!r.width) return;
    this.light = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    const s = this.viewer.stage.getBoundingClientRect();
    this.offset = [((e.clientX - s.left) / s.width) * 2 - 1, ((e.clientY - s.top) / s.height) * 2 - 1];
    this.draw();
  }

  draw() {
    if (!this.ok || this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
  }

  render() {
    const gl = this.gl, b = this.tex.base;
    if (!b || !this.on) return;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.prog);
    const bind = (slot, t) => { gl.activeTexture(gl.TEXTURE0 + slot); gl.bindTexture(gl.TEXTURE_2D, t ? t.t : this.blank); };
    bind(0, b);
    const has = HAS.map((role) => { const t = this.roleTex(role); bind(SLOT[role], t); return t ? 1 : 0; });
    const show = this.view === 'lit' ? null : this.view === 'base' ? b : this.tex[`m${this.view}`];
    bind(7, show);
    this.samplers.forEach((s, k) => gl.uniform1i(s, k));
    const u = this.u, o = this.opt;
    gl.uniform1iv(u.uHas, has);
    gl.uniform1i(u.uView, show ? 1 : 0);
    gl.uniform3f(u.uLight, this.light[0], this.light[1], o.height);
    gl.uniform3fv(u.uLightCol, LIGHT_COLS[o.color] || LIGHT_COLS.warm);
    gl.uniform1f(u.uRadius, o.radius);
    gl.uniform1f(u.uAmbient, o.ambient);
    gl.uniform1f(u.uGlow, o.glow);
    gl.uniform1f(u.uParallax, o.parallax);
    gl.uniform1f(u.uFlipG, o.flip ? -1 : 1);
    gl.uniform1f(u.uAspect, b.w / b.h);
    gl.uniform2f(u.uOff, this.offset[0], this.offset[1]);
    gl.uniform2f(u.uTexel, 1 / b.w, 1 / b.h);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindVertexArray(null);
  }

  set(k, v) { this.opt[k] = v; this.savePrefs(); this.draw(); }

  /** Controls: which map to look at, and the light. */
  controls() {
    const box = el('div', { class: 'mv-controls lit-controls' });
    const paint = () => {
      const g = this.group;
      if (!g) { setKids(box); return; }
      const tab = (key, label, title) => el('button', { class: 'lit-tab' + (this.view === key ? ' on' : ''), type: 'button', text: label, title,
        onclick: () => { this.view = key; this.draw(); paint(); } });
      const slider = (k, label, min, max, step) => {
        const i = el('input', { type: 'range', min: String(min), max: String(max), step: String(step), value: String(this.opt[k]) });
        i.oninput = () => this.set(k, +i.value);
        return el('label', { class: 'lit-slider' }, el('span', { text: label }), i);
      };
      const has = (role) => g.maps.some((m) => m.role === role);
      const flip = el('input', { type: 'checkbox' });
      flip.checked = this.opt.flip;
      flip.onchange = () => this.set('flip', flip.checked);
      const col = el('select', {}, Object.keys(LIGHT_COLS).map((k) => el('option', { value: k, text: `${k[0].toUpperCase()}${k.slice(1)} light` })));
      col.value = this.opt.color;
      col.onchange = () => this.set('color', col.value);
      setKids(box,
        el('div', { class: 'lit-tabs' }, tab('lit', 'Lit', 'All maps together, light at the mouse'), tab('base', 'Base', g.main.name),
          g.maps.map((m) => tab(String(m.id), m.label, m.name))),
        el('div', { class: 'mv-row' }, col),
        slider('height', 'Light height', 0.02, 1, 0.01), slider('radius', 'Light reach', 0.05, 3, 0.01),
        slider('ambient', 'Ambient', 0, 1, 0.01),
        has('emissive') ? slider('glow', 'Glow', 0, 3, 0.01) : null,
        has('depth') || has('background') ? slider('parallax', 'Parallax', 0, 3, 0.01) : null,
        has('normal') ? el('label', { class: 'chk', title: 'For normal maps made for DirectX (green channel down)' }, flip, 'Flip green (DirectX normal map)') : null,
        el('div', { class: 'mv-hint', text: has('normal') || has('depth') ? 'Move the mouse: the light follows it.' : 'No normal or depth map: the light only brightens.' }));
    };
    this.onChange = paint;
    paint();
    return box;
  }
}
