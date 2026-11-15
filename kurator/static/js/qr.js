// QR codes for plan links a phone can scan (Recording plans). Byte mode, error correction M (L when M does not
// fit), versions 1 to 40, the mask with the lowest penalty. The same code as Ballistic Foley Lab's src/engine.js,
// after Project Nayuki's QR Code generator (MIT): the same tables and steps, written small.

function utf8(s) { return new TextEncoder().encode(String(s)); }

var QR_ECC = { L: [1, [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25]],
  M: [0, [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49]] };
function qrRawModules(v) {
  var r = (16 * v + 128) * v + 64;
  if (v >= 2) { var na = Math.floor(v / 7) + 2; r -= (25 * na - 10) * na - 55; if (v >= 7) r -= 36; }
  return r;
}
function qrDataCodewords(v, e) { return Math.floor(qrRawModules(v) / 8) - QR_ECC[e][1][v] * QR_ECC[e][2][v]; }
function rsMul(x, y) { var z = 0, i; for (i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * 0x11D); z ^= ((y >>> i) & 1) * x; } return z; }
function rsDivisor(deg) {
  var r = [], root = 1, i, j;
  for (i = 0; i < deg - 1; i++) r.push(0);
  r.push(1);
  for (i = 0; i < deg; i++) {
    for (j = 0; j < r.length; j++) { r[j] = rsMul(r[j], root); if (j + 1 < r.length) r[j] ^= r[j + 1]; }
    root = rsMul(root, 2);
  }
  return r;
}
function rsRemainder(data, div) {
  var r = div.map(function () { return 0; });
  data.forEach(function (b) { var f = b ^ r.shift(); r.push(0); div.forEach(function (c, i) { r[i] ^= rsMul(c, f); }); });
  return r;
}
/** The QR code of a text (UTF-8): { size, dark(x, y) }, or null when it is too long for version 40. */
export function qrCode(text) {
  var bytes = Array.prototype.slice.call(utf8(text)), ecl = null, ver = 0, e, v;
  for (e = 0; e < 2 && !ecl; e++) {
    var lv = e ? 'L' : 'M';
    for (v = 1; v <= 40; v++) if (4 + (v < 10 ? 8 : 16) + 8 * bytes.length <= qrDataCodewords(v, lv) * 8) { ecl = lv; ver = v; break; }
  }
  if (!ecl) return null;
  var cap = qrDataCodewords(ver, ecl) * 8, bits = [], i, j;
  function put(val, n) { for (var k = n - 1; k >= 0; k--) bits.push((val >>> k) & 1); }
  put(4, 4); put(bytes.length, ver < 10 ? 8 : 16);
  bytes.forEach(function (b) { put(b, 8); });
  put(0, Math.min(4, cap - bits.length));
  put(0, (8 - bits.length % 8) % 8);
  for (var pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) put(pad, 8);
  var data = [];
  for (i = 0; i < bits.length; i += 8) { var by = 0; for (j = 0; j < 8; j++) by = (by << 1) | bits[i + j]; data.push(by); }
  /* blocks with their error correction, interleaved */
  var nb = QR_ECC[ecl][2][ver], el = QR_ECC[ecl][1][ver], raw = Math.floor(qrRawModules(ver) / 8), nShort = nb - raw % nb, shortLen = Math.floor(raw / nb);
  var blocks = [], div = rsDivisor(el), k = 0, all = [];
  for (i = 0; i < nb; i++) {
    var dat = data.slice(k, k + shortLen - el + (i < nShort ? 0 : 1));
    k += dat.length;
    var ecc = rsRemainder(dat, div);
    if (i < nShort) dat.push(0);
    blocks.push(dat.concat(ecc));
  }
  for (i = 0; i < blocks[0].length; i++) blocks.forEach(function (b, bj) { if (i !== shortLen - el || bj >= nShort) all.push(b[i]); });
  /* function patterns */
  var size = ver * 4 + 17, mod = [], fn = [];
  for (i = 0; i < size; i++) { mod.push(new Array(size).fill(false)); fn.push(new Array(size).fill(false)); }
  function setF(x, y, d) { mod[y][x] = d; fn[y][x] = true; }
  for (i = 0; i < size; i++) { setF(6, i, i % 2 === 0); setF(i, 6, i % 2 === 0); }
  [[3, 3], [size - 4, 3], [3, size - 4]].forEach(function (c) {
    for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++) {
      var d = Math.max(Math.abs(dx), Math.abs(dy)), xx = c[0] + dx, yy = c[1] + dy;
      if (xx >= 0 && xx < size && yy >= 0 && yy < size) setF(xx, yy, d !== 2 && d !== 4);
    }
  });
  var al = [];
  if (ver > 1) {
    var na = Math.floor(ver / 7) + 2, step = Math.floor((ver * 8 + na * 3 + 5) / (na * 4 - 4)) * 2;
    for (var pos = size - 7; al.length < na - 1; pos -= step) al.unshift(pos);
    al.unshift(6);
  }
  al.forEach(function (ax, ai) {
    al.forEach(function (ay, aj) {
      if ((ai === 0 && aj === 0) || (ai === 0 && aj === al.length - 1) || (ai === al.length - 1 && aj === 0)) return;
      for (var dy = -2; dy <= 2; dy++) for (var dx = -2; dx <= 2; dx++) setF(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    });
  });
  function format(mask) {
    var d = QR_ECC[ecl][0] << 3 | mask, r = d, q;
    for (q = 0; q < 10; q++) r = (r << 1) ^ ((r >>> 9) * 0x537);
    var b = (d << 10 | r) ^ 0x5412;
    function g(n) { return ((b >>> n) & 1) !== 0; }
    for (q = 0; q <= 5; q++) setF(8, q, g(q));
    setF(8, 7, g(6)); setF(8, 8, g(7)); setF(7, 8, g(8));
    for (q = 9; q < 15; q++) setF(14 - q, 8, g(q));
    for (q = 0; q < 8; q++) setF(size - 1 - q, 8, g(q));
    for (q = 8; q < 15; q++) setF(8, size - 15 + q, g(q));
    setF(8, size - 8, true);
  }
  format(0);
  if (ver >= 7) {
    var r = ver, q;
    for (q = 0; q < 12; q++) r = (r << 1) ^ ((r >>> 11) * 0x1F25);
    var vb = ver << 12 | r;
    for (q = 0; q < 18; q++) { var c = ((vb >>> q) & 1) !== 0, a = size - 11 + q % 3, bq = Math.floor(q / 3); setF(a, bq, c); setF(bq, a, c); }
  }
  /* the codewords, zigzag from the bottom right */
  var bi = 0;
  for (var right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (var vert = 0; vert < size; vert++) for (j = 0; j < 2; j++) {
      var x = right - j, up = ((right + 1) & 2) === 0, y = up ? size - 1 - vert : vert;
      if (!fn[y][x] && bi < all.length * 8) { mod[y][x] = ((all[bi >>> 3] >>> (7 - (bi & 7))) & 1) !== 0; bi++; }
    }
  }
  function maskOn(m, x, y) {
    switch (m) {
      case 0: return (x + y) % 2 === 0;
      case 1: return y % 2 === 0;
      case 2: return x % 3 === 0;
      case 3: return (x + y) % 3 === 0;
      case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
      case 5: return x * y % 2 + x * y % 3 === 0;
      case 6: return (x * y % 2 + x * y % 3) % 2 === 0;
      default: return ((x + y) % 2 + x * y % 3) % 2 === 0;
    }
  }
  function applyMask(m) { for (var yy = 0; yy < size; yy++) for (var xx = 0; xx < size; xx++) if (!fn[yy][xx] && maskOn(m, xx, yy)) mod[yy][xx] = !mod[yy][xx]; }
  /* penalty: runs of five or more, 2x2 blocks, finder-like patterns, imbalance of dark and light */
  function penalty() {
    var p = 0, dark = 0, x2, y2, line, run, prev, n;
    for (var pass = 0; pass < 2; pass++) for (var a2 = 0; a2 < size; a2++) {
      line = [];
      for (var b2 = 0; b2 < size; b2++) line.push(pass ? mod[b2][a2] : mod[a2][b2]);
      run = 1; prev = line[0];
      for (n = 1; n <= size; n++) {
        if (n < size && line[n] === prev) run++;
        else { if (run >= 5) p += run - 2; run = 1; prev = line[n]; }
      }
      var s = line.map(function (d) { return d ? '1' : '0'; }).join('');
      for (n = s.indexOf('1011101'); n >= 0; n = s.indexOf('1011101', n + 1)) {
        if (s.substr(n - 4, 4) === '0000' || s.substr(n + 7, 4) === '0000' || n < 4 || n + 11 > size) p += 40;
      }
    }
    for (y2 = 0; y2 < size; y2++) for (x2 = 0; x2 < size; x2++) {
      if (mod[y2][x2]) dark++;
      if (x2 < size - 1 && y2 < size - 1) { var c0 = mod[y2][x2]; if (c0 === mod[y2][x2 + 1] && c0 === mod[y2 + 1][x2] && c0 === mod[y2 + 1][x2 + 1]) p += 3; }
    }
    return p + Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
  }
  var best = 0, bestP = Infinity, m;
  for (m = 0; m < 8; m++) { applyMask(m); format(m); var pp = penalty(); if (pp < bestP) { bestP = pp; best = m; } applyMask(m); }
  applyMask(best); format(best);
  return { size: size, version: ver, ecl: ecl, dark: function (x, y) { return x >= 0 && y >= 0 && x < size && y < size && mod[y][x]; } };
}
/** The code as an SVG (a string), a quiet zone of 4 modules round it. */
export function qrSvg(text, color) {
  var q = qrCode(text);
  if (!q) return null;
  var d = '', n = q.size + 8, x, y;
  for (y = 0; y < q.size; y++) for (x = 0; x < q.size; x++) if (q.dark(x, y)) d += 'M' + (x + 4) + ',' + (y + 4) + 'h1v1h-1z';
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + n + ' ' + n + '" shape-rendering="crispEdges"><rect width="' + n + '" height="' + n + '" fill="#fff"/><path d="' + d + '" fill="' + (color || '#000') + '"/></svg>';
}

