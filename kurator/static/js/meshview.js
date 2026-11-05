// 3D model viewer: WebGL2, no libraries. Drag = turn, right-drag or Shift+drag = move,
// wheel = zoom, double-click or 0 = reset, Alt+drag = turn the lights.
// Materials with base colour, normal, emissive, occlusion and metal/roughness maps; normal maps
// use tangents derived on the fly (screen-space derivatives), so models need no tangent data.
// View modes show single maps, normals or a UV checker; light presets and smooth/flat shading.

import { el, prefs, setKids } from './util.js';

const VS = `#version 300 es
in vec3 aPos; in vec4 aNrm; in vec2 aUV; in vec4 aCol;
uniform mat4 uView; uniform mat4 uProj;
out vec3 vPos; out vec3 vNrm; out vec2 vUV; out vec3 vCol;
void main() {
  vec4 p = uView * vec4(aPos, 1.0);
  vPos = p.xyz;
  vNrm = mat3(uView) * aNrm.xyz;
  vUV = aUV; vCol = aCol.rgb;
  gl_Position = uProj * p;
}`;

const FS = `#version 300 es
precision highp float;
in vec3 vPos; in vec3 vNrm; in vec2 vUV; in vec3 vCol;
uniform int uMode;          // 0 lit, 1 base colour, 2 normals, 3 UV checker, 4 clay, 5 single map
uniform int uSmooth;
uniform vec4 uColor; uniform vec3 uEmissive; uniform int uVCol;
uniform int uHas[5];        // base, normal, emissive, occlusion, metal_rough
uniform sampler2D uBase, uNormal, uEmis, uOcc, uMR;
uniform int uShowMap;       // for mode 5: which sampler
uniform float uNormalY;     // -1: OpenGL-style maps with glTF UVs (default), 1: DirectX-style
uniform vec3 uLightDir[3]; uniform vec3 uLightCol[3];
uniform vec3 uSky; uniform vec3 uGround; uniform float uExposure;
out vec4 o;

vec3 toLin(vec3 c) { return pow(c, vec3(2.2)); }
mat3 cotangentFrame(vec3 N, vec3 p, vec2 uv) {
  vec3 dp1 = dFdx(p), dp2 = dFdy(p);
  vec2 duv1 = dFdx(uv), duv2 = dFdy(uv);
  vec3 dp2perp = cross(dp2, N), dp1perp = cross(N, dp1);
  vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
  vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
  float m = max(dot(T, T), dot(B, B));
  if (m < 1e-20) return mat3(vec3(1, 0, 0), vec3(0, 1, 0), N);
  float inv = inversesqrt(m);
  return mat3(T * inv, B * inv, N);
}
void main() {
  vec3 flatN = normalize(cross(dFdx(vPos), dFdy(vPos)));
  vec3 N = uSmooth == 1 && dot(vNrm, vNrm) > 0.01 ? normalize(vNrm) : flatN;
  if (dot(N, -vPos) < 0.0) N = -N;                      // two-sided
  if (uMode == 3) {
    vec2 c = floor(vUV * 8.0);
    float k = mod(c.x + c.y, 2.0);
    vec3 col = mix(vec3(0.15, 0.17, 0.22), vec3(0.85, 0.55, 0.25), k);
    float l = 0.45 + 0.55 * max(dot(N, normalize(vec3(-0.3, 0.6, 0.8))), 0.0);
    o = vec4(col * l, 1.0); return;
  }
  if (uMode == 5) {
    vec3 t = uShowMap == 0 ? texture(uBase, vUV).rgb : uShowMap == 1 ? texture(uNormal, vUV).rgb :
             uShowMap == 2 ? texture(uEmis, vUV).rgb : uShowMap == 3 ? texture(uOcc, vUV).rrr : texture(uMR, vUV).rgb;
    o = vec4(uShowMap == 0 || uShowMap == 2 ? pow(t, vec3(1.0 / 2.2)) : t, 1.0); return;
  }
  if (uHas[1] == 1 && uMode != 4) {
    vec3 tn = texture(uNormal, vUV).xyz * 2.0 - 1.0;
    tn.y *= uNormalY;
    N = normalize(cotangentFrame(N, vPos, vUV) * tn);
  }
  if (uMode == 2) { o = vec4(N * 0.5 + 0.5, 1.0); return; }
  vec3 base = uMode == 4 ? vec3(0.62) : toLin(uColor.rgb) * (uVCol == 1 ? toLin(vCol) : vec3(1.0));
  if (uHas[0] == 1 && uMode != 4) base *= texture(uBase, vUV).rgb;   // sRGB texture: already linear
  if (uMode == 1) { o = vec4(pow(base, vec3(1.0 / 2.2)), 1.0); return; }
  float rough = 0.6, metal = 0.0, occ = 1.0;
  if (uHas[4] == 1 && uMode != 4) { vec3 mr = texture(uMR, vUV).rgb; rough = mr.g; metal = mr.b; }
  if (uHas[3] == 1 && uMode != 4) occ = texture(uOcc, vUV).r;
  vec3 V = normalize(-vPos);
  vec3 diffCol = base * (1.0 - metal);
  vec3 specCol = mix(vec3(0.04), base, metal);
  float shin = mix(256.0, 4.0, clamp(rough, 0.0, 1.0));
  vec3 col = mix(uGround, uSky, N.y * 0.5 + 0.5) * diffCol * occ;
  for (int i = 0; i < 3; i++) {
    vec3 L = normalize(uLightDir[i]);
    float nl = max(dot(N, L), 0.0);
    vec3 H = normalize(L + V);
    float sp = pow(max(dot(N, H), 0.0), shin) * (shin + 8.0) / 25.0;
    col += uLightCol[i] * nl * (diffCol + specCol * sp);
  }
  vec3 em = toLin(uEmissive);
  if (uHas[2] == 1 && uMode != 4) em = (dot(em, em) > 0.0 ? em : vec3(1.0)) * texture(uEmis, vUV).rgb;
  col = col * uExposure + em;
  col = col / (1.0 + col * 0.15);                        // soft shoulder, keeps highlights
  o = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
}`;

export const VIEW_MODES = [['lit', 'Lit'], ['base', 'Base colour'], ['clay', 'Clay (no textures)'], ['normals', 'Normals'], ['uv', 'UV checker']];
const MODE_ID = { lit: 0, base: 1, normals: 2, uv: 3, clay: 4, map: 5 };
const ROLE_SLOT = { base: 0, normal: 1, emissive: 2, occlusion: 3, metal_rough: 4 };
const ROLE_LABEL = { base: 'Base colour', normal: 'Normal', emissive: 'Emissive', occlusion: 'Occlusion', metal_rough: 'Metal / rough',
  roughness: 'Roughness', metallic: 'Metallic', opacity: 'Opacity', specular: 'Specular', height: 'Height' };
// Lights in world space (key, fill, rim): direction, colour × intensity; sky and ground ambient
export const LIGHTS = {
  studio: { name: 'Studio', dirs: [[-0.45, 0.75, 0.5], [0.7, 0.25, 0.55], [0.15, 0.45, -0.9]],
    cols: [[1.05, 1.0, 0.95], [0.32, 0.34, 0.38], [0.55, 0.55, 0.6]], sky: [0.34, 0.36, 0.4], ground: [0.16, 0.15, 0.14] },
  sun: { name: 'Sun and sky', dirs: [[0.35, 0.85, 0.4], [0, 1, 0], [0, -1, 0]],
    cols: [[1.6, 1.5, 1.3], [0, 0, 0], [0, 0, 0]], sky: [0.3, 0.38, 0.5], ground: [0.15, 0.12, 0.09] },
  rim: { name: 'Rim light', dirs: [[0.2, 0.3, -0.95], [-0.8, 0.3, -0.4], [-0.3, 0.5, 0.8]],
    cols: [[1.4, 1.35, 1.3], [0.7, 0.75, 0.9], [0.25, 0.25, 0.27]], sky: [0.12, 0.13, 0.16], ground: [0.05, 0.05, 0.05] },
  head: { name: 'Headlight', head: true, dirs: [[0.2, 0.25, 1], [0, 1, 0], [0, 0, 0]],
    cols: [[1.15, 1.15, 1.15], [0.15, 0.15, 0.15], [0, 0, 0]], sky: [0.22, 0.22, 0.24], ground: [0.12, 0.12, 0.12] },
  soft: { name: 'Soft (even)', dirs: [[0, 1, 0], [0, 0, 1], [0, 0, -1]],
    cols: [[0.35, 0.35, 0.35], [0.3, 0.3, 0.3], [0.3, 0.3, 0.3]], sky: [0.75, 0.75, 0.78], ground: [0.45, 0.44, 0.42] },
};

const mat = {
  persp(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  },
  // camera on a sphere around the target (yaw/pitch in radians); returns a view matrix
  orbit(target, dist, yaw, pitch) {
    const cp = Math.cos(pitch), sp = Math.sin(pitch), cy = Math.cos(yaw), sy = Math.sin(yaw);
    const eye = [target[0] + dist * cp * sy, target[1] + dist * sp, target[2] + dist * cp * cy];
    const z = norm(sub(eye, target));
    const x = norm(cross([0, 1, 0], z));
    const y = cross(z, x);
    return {
      eye, x, y, z,
      m: new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
        -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]),
    };
  },
};
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

export class MeshView {
  constructor(parent) {
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'meshview';
    parent.append(this.canvas);
    this.gl = this.canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: true });
    this.ok = !!this.gl;
    this.token = 0;
    this.n = 0;
    this.groups = [];
    this.mats = [];
    this.texs = [];
    const P = prefs.get('mesh', {});
    this.mode = P.mode || 'lit';
    this.lights = LIGHTS[P.lights] ? P.lights : 'studio';
    this.lightYaw = 0;
    this.smoothPref = P.smooth ?? null;   // null: from the file (smooth when it has normals)
    this.dxNormals = !!P.dx;
    this.showMap = null;
    this.onChange = () => {};
    if (this.ok) this.initGL();
    this.bind();
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.canvas);
  }

  savePrefs() { prefs.set('mesh', { mode: this.mode, lights: this.lights, smooth: this.smoothPref, dx: this.dxNormals }); }

  initGL() {
    const gl = this.gl;
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    this.prog = p;
    const U = (n) => gl.getUniformLocation(p, n);
    this.loc = { pos: gl.getAttribLocation(p, 'aPos'), nrm: gl.getAttribLocation(p, 'aNrm'), uv: gl.getAttribLocation(p, 'aUV'),
      col: gl.getAttribLocation(p, 'aCol'), view: U('uView'), proj: U('uProj'), mode: U('uMode'), smooth: U('uSmooth'),
      color: U('uColor'), emissive: U('uEmissive'), vcol: U('uVCol'), has: U('uHas'), showMap: U('uShowMap'), normalY: U('uNormalY'),
      lightDir: U('uLightDir'), lightCol: U('uLightCol'), sky: U('uSky'), ground: U('uGround'), exposure: U('uExposure'),
      samplers: ['uBase', 'uNormal', 'uEmis', 'uOcc', 'uMR'].map(U) };
    this.vao = gl.createVertexArray();
    this.bufs = { pos: gl.createBuffer(), nrm: gl.createBuffer(), uv: gl.createBuffer(), col: gl.createBuffer(), idx: gl.createBuffer() };
    this.white = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.white);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));
  }

  /** Fetch and show the geometry of a catalog item. Resolves to the header info. */
  async load(id, onProgress = () => {}) {
    const token = ++this.token;
    this.n = 0;
    this.freeTextures();
    this.draw();
    if (!this.ok) throw new Error('This browser has no WebGL 2 - the 3D view is not available.');
    onProgress(0, 0);   // 0 of unknown: the server is still reading (or Blender converting) the file
    const r = await fetch(`/api/mesh/${id}`);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch { /* binary */ }
      throw new Error(msg);
    }
    const total = +(r.headers.get('X-Mesh-Bytes') || 0);
    const parts = [];
    let got = 0;
    const reader = r.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (token !== this.token) { reader.cancel(); return null; }
      parts.push(value);
      got += value.length;
      onProgress(got, total);
    }
    const all = new Uint8Array(got);
    let o = 0;
    for (const p of parts) { all.set(p, o); o += p.length; }
    const buf = all.buffer;
    if (token !== this.token) return null;
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== 0x48534d4b) throw new Error('bad mesh data');   // 'KMSH'
    const hl = dv.getUint32(4, true);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hl)));
    const nv = head.vertices;
    let off = 8 + hl;
    const take = (Type, n) => { const a = new Type(buf, off, n); off += a.byteLength; return a; };
    const pos = take(Float32Array, nv * 3);
    const nrm = take(Int8Array, nv * 4);
    const uv = head.uv ? take(Float32Array, nv * 2) : null;
    const col = take(Uint8Array, nv * 4);
    const idx = take(Uint32Array, head.triangles * 3);
    const gl = this.gl;
    gl.bindVertexArray(this.vao);
    const attr = (b, loc, data, size, type, normalized) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, type, normalized, 0, 0);
    };
    attr(this.bufs.pos, this.loc.pos, pos, 3, gl.FLOAT, false);
    attr(this.bufs.nrm, this.loc.nrm, nrm, 4, gl.BYTE, true);
    attr(this.bufs.col, this.loc.col, col, 4, gl.UNSIGNED_BYTE, true);
    if (uv && this.loc.uv >= 0) attr(this.bufs.uv, this.loc.uv, uv, 2, gl.FLOAT, false);
    else if (this.loc.uv >= 0) { gl.disableVertexAttribArray(this.loc.uv); gl.vertexAttrib2f(this.loc.uv, 0, 0); }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.bufs.idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    gl.bindVertexArray(null);
    this.n = idx.length;
    this.head = head;
    this.id = id;
    this.mats = head.materials || [];
    this.groups = head.groups?.length ? head.groups : [{ start: 0, count: head.triangles, material: -1 }];
    this.texs = new Array(head.textures || 0).fill(null);
    this.smooth = this.smoothPref ?? head.normals === 'file';
    this.center = [0, 1, 2].map((k) => (head.min[k] + head.max[k]) / 2);
    this.radius = Math.max(1e-9, Math.hypot(...[0, 1, 2].map((k) => head.max[k] - head.min[k])) / 2);
    this.reset();
    if (head.uv) this.loadTextures(id, token);
    return head;
  }

  /** Textures arrive after the geometry: the model shows in plain colours first. */
  loadTextures(id, token) {
    const gl = this.gl;
    const roleOf = new Map();
    for (const m of this.mats) for (const [role, e] of Object.entries(m.maps || {})) if (e.tex != null) roleOf.set(e.tex, role);
    for (let k = 0; k < this.texs.length; k++) {
      const role = roleOf.get(k);
      if (!(role in ROLE_SLOT)) continue;   // maps the viewer doesn't use (height, specular...) aren't fetched
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        if (token !== this.token) return;
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        const srgb = role === 'base' || role === 'emissive';
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        gl.texImage2D(gl.TEXTURE_2D, 0, srgb ? gl.SRGB8_ALPHA8 : gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
        const ext = gl.getExtension('EXT_texture_filter_anisotropic');
        if (ext) gl.texParameterf(gl.TEXTURE_2D, ext.TEXTURE_MAX_ANISOTROPY_EXT, 8);
        this.texs[k] = t;
        this.draw();
        this.onChange();
      };
      img.onerror = () => { if (token === this.token) { this.texs[k] = false; this.onChange(); } };
      img.src = `/api/mesh/${id}/tex/${k}`;
    }
  }

  freeTextures() {
    if (!this.ok) return;
    for (const t of this.texs) if (t) this.gl.deleteTexture(t);
    this.texs = [];
  }

  texUrl(k, size = 256) { return `/api/mesh/${this.id}/tex/${k}?s=${size}`; }

  clear() { this.token++; this.n = 0; this.freeTextures(); this.draw(); }

  reset() {
    this.yaw = 0.61; this.pitch = 0.42;   // same 3/4 view as the thumbnails
    this.target = [...(this.center || [0, 0, 0])];
    this.dist = (this.radius || 1) / Math.sin((35 * Math.PI) / 360) * 1.05;
    this.draw();
  }

  set(opts) {
    if (opts.mode) { this.mode = opts.mode; this.showMap = null; }
    if (opts.showMap !== undefined) { this.showMap = opts.showMap; }
    if (opts.lights) this.lights = opts.lights;
    if (opts.smooth != null) { this.smooth = opts.smooth; this.smoothPref = opts.smooth; }
    if (opts.dx != null) this.dxNormals = opts.dx;
    this.savePrefs();
    this.draw();
    this.onChange();
  }

  cycleLights() {
    const keys = Object.keys(LIGHTS);
    this.set({ lights: keys[(keys.indexOf(this.lights) + 1) % keys.length] });
    return LIGHTS[this.lights].name;
  }

  draw() {
    if (!this.ok) return;
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
  }

  render() {
    const gl = this.gl, c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!this.n) return;
    gl.enable(gl.DEPTH_TEST);
    gl.useProgram(this.prog);
    const L = this.loc;
    const cam = mat.orbit(this.target, this.dist, this.yaw, this.pitch);
    this.cam = cam;
    const near = Math.max(this.dist - this.radius * 2, this.dist * 0.001);
    gl.uniformMatrix4fv(L.view, false, cam.m);
    gl.uniformMatrix4fv(L.proj, false, mat.persp((35 * Math.PI) / 180, w / h, near, this.dist + this.radius * 2));
    // lights: world space (turned by Alt+drag), or fixed to the camera for the headlight
    const preset = LIGHTS[this.lights];
    const cy = Math.cos(this.lightYaw), sy = Math.sin(this.lightYaw);
    const dirs = [], cols = [];
    for (let i = 0; i < 3; i++) {
      let d = preset.dirs[i];
      if (preset.head) d = norm(d);
      else {
        d = [d[0] * cy + d[2] * sy, d[1], -d[0] * sy + d[2] * cy];
        d = [dot(cam.x, d), dot(cam.y, d), dot(cam.z, d)];   // to view space
      }
      dirs.push(...d);
      cols.push(...preset.cols[i]);
    }
    gl.uniform3fv(L.lightDir, dirs);
    gl.uniform3fv(L.lightCol, cols);
    gl.uniform3fv(L.sky, preset.sky.map((x) => x ** 2.2 * 2.2));
    gl.uniform3fv(L.ground, preset.ground.map((x) => x ** 2.2 * 2.2));
    gl.uniform1f(L.exposure, 1.0);
    gl.uniform1i(L.mode, this.showMap != null ? MODE_ID.map : MODE_ID[this.mode] ?? 0);
    gl.uniform1i(L.smooth, this.smooth ? 1 : 0);
    gl.uniform1f(L.normalY, this.dxNormals ? 1 : -1);
    gl.uniform1i(L.showMap, ROLE_SLOT[this.showMap] ?? 0);
    L.samplers.forEach((s, k) => gl.uniform1i(s, k));
    gl.bindVertexArray(this.vao);
    for (const g of this.groups) {
      const m = this.mats[g.material] || null;
      const has = [0, 0, 0, 0, 0];
      for (const [role, slot] of Object.entries(ROLE_SLOT)) {
        const e = m?.maps?.[role];
        const t = e?.tex != null ? this.texs[e.tex] : null;
        gl.activeTexture(gl.TEXTURE0 + slot);
        gl.bindTexture(gl.TEXTURE_2D, t || this.white);
        has[slot] = t ? 1 : 0;
      }
      gl.uniform1iv(L.has, has);
      gl.uniform1i(L.vcol, m ? 0 : 1);   // per-face colours only where no material says otherwise
      const c = m?.color || [1, 1, 1, 1];
      gl.uniform4f(L.color, c[0], c[1], c[2], c[3] ?? 1);
      const e = m?.emissive || [0, 0, 0];
      gl.uniform3f(L.emissive, e[0], e[1], e[2]);
      gl.drawElements(gl.TRIANGLES, g.count * 3, gl.UNSIGNED_INT, g.start * 12);
    }
    gl.bindVertexArray(null);
  }

  /** The control strip: view mode, lights, shading, and the maps each material uses. */
  controls() {
    const box = el('div', { class: 'mv-controls' });
    const paint = () => {
      const head = this.head;
      if (!head) { setKids(box); return; }
      const sel = (opts, v, on) => { const s = el('select', {}, opts.map(([k, t]) => el('option', { value: k, text: t }))); s.value = v; s.onchange = () => on(s.value); return s; };
      const mapsRows = [];
      const seen = new Set();
      for (const m of this.mats) {
        for (const [role, e] of Object.entries(m.maps || {})) {
          const key = `${role}:${e.tex ?? e.name}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const usable = role in ROLE_SLOT && e.tex != null;
          const state = !e.found ? 'missing' : usable && this.texs[e.tex] === false ? 'unreadable' : '';
          mapsRows.push(el('button', {
            class: 'mv-map' + (this.showMap === role ? ' on' : '') + (state ? ' bad' : ''), type: 'button',
            title: `${ROLE_LABEL[role] || role}: ${e.name}${e.embedded ? ' (inside the model)' : ''}${state === 'missing' ? ' - not found next to the model' : state ? ' - this format can\'t be shown' : usable ? ' - click to show only this map' : ''}`,
            onclick: () => { if (usable) this.set({ showMap: this.showMap === role ? null : role }); } },
          e.found && e.tex != null && state === '' ? el('img', { src: this.texUrl(e.tex, 96), alt: '' }) : el('span', { class: 'mv-nomap', text: state === 'missing' ? '?' : '–' }),
          el('span', { class: 'mv-role', text: ROLE_LABEL[role] || role }), el('span', { class: 'mv-name', text: e.name })));
        }
      }
      setKids(box,
        el('div', { class: 'mv-row' },
          sel(VIEW_MODES, this.mode, (v) => this.set({ mode: v })),
          sel(Object.entries(LIGHTS).map(([k, l]) => [k, l.name]), this.lights, (v) => this.set({ lights: v }))),
        el('div', { class: 'mv-row' },
          el('label', { class: 'chk', title: 'Smooth shading with the normals of the file (or averaged ones)' },
            (() => { const c = el('input', { type: 'checkbox' }); c.checked = this.smooth; c.onchange = () => this.set({ smooth: c.checked }); return c; })(), 'Smooth'),
          mapsRows.some((r) => r.textContent.includes('Normal')) ? el('label', { class: 'chk', title: 'Normal maps made for DirectX / Unreal (green channel down)' },
            (() => { const c = el('input', { type: 'checkbox' }); c.checked = this.dxNormals; c.onchange = () => this.set({ dx: c.checked }); return c; })(), 'DirectX normals') : null),
        mapsRows.length ? el('div', { class: 'mv-maps' }, mapsRows)
          : el('div', { class: 'mv-hint', text: head.uv ? 'No texture maps assigned.' : 'No texture coordinates: shown in material colours.' }),
        el('div', { class: 'mv-hint', text: 'Alt+drag: turn the lights · L: next light' }));
    };
    this.onChange = paint;
    paint();
    return box;
  }

  bind() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.button === 1 || e.shiftKey, light: e.altKey,
        yaw: this.yaw, pitch: this.pitch, lightYaw: this.lightYaw, target: [...(this.target || [0, 0, 0])] };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (!drag || !this.n) return;
      e.stopPropagation();
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (drag.light) {
        this.lightYaw = drag.lightYaw + dx * 0.01;
      } else if (drag.pan) {
        const k = (2 * this.dist * Math.tan((35 * Math.PI) / 360)) / c.clientHeight;
        const { x, y } = this.cam;
        this.target = [0, 1, 2].map((i) => drag.target[i] - (x[i] * dx - y[i] * dy) * k);
      } else {
        this.yaw = drag.yaw - dx * 0.008;
        this.pitch = Math.max(-1.55, Math.min(1.55, drag.pitch + dy * 0.008));
      }
      this.draw();
    });
    const end = (e) => { if (drag) { e.stopPropagation(); drag = null; } };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', (e) => {
      e.preventDefault(); e.stopPropagation();
      this.dist *= Math.exp(e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015));
      this.dist = Math.max(this.radius * 0.05, Math.min(this.radius * 50, this.dist));
      this.draw();
    }, { passive: false });
    c.addEventListener('dblclick', (e) => { e.stopPropagation(); this.reset(); });
  }
}
