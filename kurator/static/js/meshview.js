// 3D model viewer: WebGL2, no libraries. Drag = turn, right-drag or Shift+drag = move,
// wheel = zoom, double-click or 0 = reset. Flat shading comes from screen-space derivatives,
// so the mesh needs no normals.

const VS = `#version 300 es
in vec3 aPos; in vec3 aCol;
uniform mat4 uView; uniform mat4 uProj;
out vec3 vPos; out vec3 vCol;
void main() {
  vec4 p = uView * vec4(aPos, 1.0);
  vPos = p.xyz; vCol = aCol;
  gl_Position = uProj * p;
}`;

const FS = `#version 300 es
precision highp float;
in vec3 vPos; in vec3 vCol;
uniform float uWire;
out vec4 o;
void main() {
  vec3 n = normalize(cross(dFdx(vPos), dFdy(vPos)));
  if (n.z < 0.0) n = -n;                       // two-sided
  vec3 key = normalize(vec3(-0.45, 0.75, 0.5));
  vec3 rim = normalize(vec3(0.7, 0.2, -0.3));
  float l = 0.30 + 0.62 * max(dot(n, key), 0.0) + 0.16 * max(dot(n, rim), 0.0) + 0.10 * n.z;
  o = vec4(vCol * l, 1.0);
}`;

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
      eye, x, y,
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
    if (this.ok) this.initGL();
    this.bind();
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.canvas);
  }

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
    this.loc = { pos: gl.getAttribLocation(p, 'aPos'), col: gl.getAttribLocation(p, 'aCol'),
      view: gl.getUniformLocation(p, 'uView'), proj: gl.getUniformLocation(p, 'uProj') };
    this.vao = gl.createVertexArray();
    this.bufs = { pos: gl.createBuffer(), col: gl.createBuffer(), idx: gl.createBuffer() };
  }

  /** Fetch and show the geometry of a catalog item. Resolves to the header info. */
  async load(id, onProgress = () => {}) {
    const token = ++this.token;
    this.n = 0;
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
    let off = 8 + hl;
    const pos = new Float32Array(buf, off, head.vertices * 3); off += pos.byteLength;
    const idx = new Uint32Array(buf, off, head.triangles * 3); off += idx.byteLength;
    const col = new Uint8Array(buf, off, head.vertices * 3);
    const gl = this.gl;
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.pos);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(this.loc.pos);
    gl.vertexAttribPointer(this.loc.pos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs.col);
    gl.bufferData(gl.ARRAY_BUFFER, col, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(this.loc.col);
    gl.vertexAttribPointer(this.loc.col, 3, gl.UNSIGNED_BYTE, true, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.bufs.idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    gl.bindVertexArray(null);
    this.n = idx.length;
    this.head = head;
    this.center = [0, 1, 2].map((k) => (head.min[k] + head.max[k]) / 2);
    this.radius = Math.max(1e-9, Math.hypot(...[0, 1, 2].map((k) => head.max[k] - head.min[k])) / 2);
    this.reset();
    return head;
  }

  clear() { this.token++; this.n = 0; this.draw(); }

  reset() {
    this.yaw = 0.61; this.pitch = 0.42;   // same 3/4 view as the thumbnails
    this.target = [...(this.center || [0, 0, 0])];
    this.dist = (this.radius || 1) / Math.sin((35 * Math.PI) / 360) * 1.05;
    this.draw();
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
    const cam = mat.orbit(this.target, this.dist, this.yaw, this.pitch);
    this.cam = cam;
    const near = Math.max(this.dist - this.radius * 2, this.dist * 0.001);
    gl.uniformMatrix4fv(this.loc.view, false, cam.m);
    gl.uniformMatrix4fv(this.loc.proj, false, mat.persp((35 * Math.PI) / 180, w / h, near, this.dist + this.radius * 2));
    gl.bindVertexArray(this.vao);
    gl.drawElements(gl.TRIANGLES, this.n, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
  }

  bind() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.button === 1 || e.shiftKey, yaw: this.yaw, pitch: this.pitch, target: [...(this.target || [0, 0, 0])] };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (!drag || !this.n) return;
      e.stopPropagation();
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (drag.pan) {
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
