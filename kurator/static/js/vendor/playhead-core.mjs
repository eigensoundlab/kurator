/*! Playhead core 0.2.0 | Apache-2.0 | (c) 2026 Daniel Sandner | https://github.com/eigensoundlab/playhead */
const module = undefined, require = undefined;
const __ph = {};
// ---- src/analysis.js
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Daniel Sandner
// Part of Playhead, https://github.com/eigensoundlab/playhead

(function (root) {
"use strict";

/* ===================================================================
   FFT: in-place complex forward transform, X[k] = sum x[t] e^(-2 pi i k t / n), n a power of two.
   Radix-2^2: after the bit-reversal (a precomputed list of swaps) each pass does two radix-2
   decimation-in-time stages at once, so the data is read and written half as often; the second
   stage's odd twiddle W^(j+L) is W^j times -i, and j = 0 needs no multiplications. About twice as
   fast as plain radix-2 here, with the same results to within 3e-16 (relative).
   =================================================================== */
const fftCache = {};
function getFFT(n) {
  if (fftCache[n]) return fftCache[n];
  const levels = Math.round(Math.log2(n));
  const sw = [];
  for (let i = 0; i < n; i++) { let r = 0, x = i; for (let b = 0; b < levels; b++) { r = (r << 1) | (x & 1); x >>= 1; } if (r > i) sw.push(i, r); }
  const swaps = Uint32Array.from(sw), odd = levels & 1, passes = [];
  const ang = k => -2 * Math.PI * k / n;
  for (let L = odd ? 2 : 1; 4 * L <= n; L *= 4) {
    const c2 = new Float64Array(L), s2 = new Float64Array(L), c4 = new Float64Array(L), s4 = new Float64Array(L);
    for (let j = 0; j < L; j++) { c2[j] = Math.cos(ang(j * n / (2 * L))); s2[j] = Math.sin(ang(j * n / (2 * L))); c4[j] = Math.cos(ang(j * n / (4 * L))); s4[j] = Math.sin(ang(j * n / (4 * L))); }
    passes.push({ L, c2, s2, c4, s4 });
  }
  const f = function (re, im) {
    for (let p = 0; p < swaps.length; p += 2) { const i = swaps[p], j = swaps[p + 1]; let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    if (odd) for (let i = 0; i < n; i += 2) { const ar = re[i], ai = im[i], br = re[i + 1], bi = im[i + 1]; re[i] = ar + br; im[i] = ai + bi; re[i + 1] = ar - br; im[i + 1] = ai - bi; }
    for (let q = 0; q < passes.length; q++) {
      const P = passes[q], L = P.L, L4 = 4 * L, c2 = P.c2, s2 = P.s2, c4 = P.c4, s4 = P.s4;
      for (let i = 0; i < n; i += L4) {
        {
          const a0 = i, a1 = a0 + L, a2 = a1 + L, a3 = a2 + L;
          const b0r = re[a0] + re[a1], b0i = im[a0] + im[a1], b1r = re[a0] - re[a1], b1i = im[a0] - im[a1];
          const b2r = re[a2] + re[a3], b2i = im[a2] + im[a3], b3r = re[a2] - re[a3], b3i = im[a2] - im[a3];
          re[a0] = b0r + b2r; im[a0] = b0i + b2i; re[a2] = b0r - b2r; im[a2] = b0i - b2i;
          re[a1] = b1r + b3i; im[a1] = b1i - b3r; re[a3] = b1r - b3i; im[a3] = b1i + b3r;
        }
        for (let j = 1; j < L; j++) {
          const a0 = i + j, a1 = a0 + L, a2 = a1 + L, a3 = a2 + L;
          const w2r = c2[j], w2i = s2[j], w4r = c4[j], w4i = s4[j];
          const x1r = re[a1] * w2r - im[a1] * w2i, x1i = re[a1] * w2i + im[a1] * w2r;
          const x3r = re[a3] * w2r - im[a3] * w2i, x3i = re[a3] * w2i + im[a3] * w2r;
          const b0r = re[a0] + x1r, b0i = im[a0] + x1i, b1r = re[a0] - x1r, b1i = im[a0] - x1i;
          const b2r = re[a2] + x3r, b2i = im[a2] + x3i, b3r = re[a2] - x3r, b3i = im[a2] - x3i;
          const t2r = b2r * w4r - b2i * w4i, t2i = b2r * w4i + b2i * w4r;
          const u3r = b3r * w4r - b3i * w4i, u3i = b3r * w4i + b3i * w4r;
          re[a0] = b0r + t2r; im[a0] = b0i + t2i; re[a2] = b0r - t2r; im[a2] = b0i - t2i;
          re[a1] = b1r + u3i; im[a1] = b1i - u3r; re[a3] = b1r - u3i; im[a3] = b1i + u3r;
        }
      }
    }
  };
  fftCache[n] = f;
  return f;
}
function hannP(n) { const w = new Float64Array(n); for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / n); return w; }

/* ===================================================================
   Constants
   =================================================================== */
const BARK_EDGES = [20, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500];
const NBARK = 24;
const NCOARSE = 12;                  // pairs of Bark bands
const NMEL = 40;
/* Tunable feature and detection parameters (tools/tune.js calibrates them on real recordings).
   melFloorDb: absolute floor of the mel spectrum; melRelDb > 0 replaces it by (file maximum - melRelDb), which makes every
   mel-based measure invariant to playback level. keepRaw stores the unfloored spectrum so the floor can be changed later. */
// melRelDb 80 and the recurrence range in descriptors.js were frozen by tools/tune.js on half of a 73-file corpus (held-out half: 94.1 % -> 95.8 %)
const FEAT = { melFloorDb: -80, melRelDb: 80, keepRaw: false };
const ONSET = { min: 0.6, madK: 1.5 };
function applyMelFloor(src, dst) {
  let mx = -Infinity; for (let i = 0; i < src.length; i++) if (src[i] > mx) mx = src[i];
  const fl = FEAT.melRelDb > 0 ? Math.max(-140, mx - FEAT.melRelDb) : FEAT.melFloorDb;
  for (let i = 0; i < src.length; i++) dst[i] = src[i] > fl ? src[i] : fl;
  return dst;
}
const CHROMA_EXP = 1.0;   // power-weighted peaks: suppresses weak harmonics
// Aures roughness carrier weighting g(z), z = 0..24 Bark (MoSQITo _gzi_weighting)
const G_AURES = [0.15, 0.26, 0.38, 0.47, 0.54, 0.65, 0.76, 0.83, 0.90, 0.98, 0.98, 0.90, 0.80, 0.70, 0.62, 0.54, 0.49, 0.43, 0.39, 0.35, 0.30, 0.30, 0.30, 0.30, 0.30];
// Tonal Interval Vector weights (Bernardes et al.)
const TIV_W = [3, 8, 11.5, 15, 14.5, 7.5];
const TIV_NORM = Math.sqrt(TIV_W.reduce((a, w) => a + w * w, 0));

function terhardtLTQ(f) {
  const k = Math.max(f, 20) / 1000;
  return 3.64 * Math.pow(k, -0.8) - 6.5 * Math.exp(-0.6 * (k - 3.3) * (k - 3.3)) + 1e-3 * Math.pow(k, 4);
}
const BARK_LTQ = [], BARK_LTQF = [];
for (let i = 0; i < NBARK; i++) {
  const fc = Math.sqrt(BARK_EDGES[i] * BARK_EDGES[i + 1]);
  const ltq = Math.min(60, terhardtLTQ(fc));
  BARK_LTQ.push(ltq);
  BARK_LTQF.push(0.0635 * Math.pow(10, 0.025 * ltq));
}
// DIN 45692 sharpness weighting at band centres
const SHARP_G = [];
for (let i = 0; i < NBARK; i++) { const z = i + 0.5; SHARP_G.push(z <= 15.8 ? 1 : 0.15 * Math.exp(0.42 * (z - 15.8)) + 0.85); }

function kFilterCoeffs(fs) {
  let f0 = 1681.974450955533, G = 3.999843853973347, Q = 0.7071752369554196;
  let K = Math.tan(Math.PI * f0 / fs);
  const Vh = Math.pow(10, G / 20), Vb = Math.pow(Vh, 0.4996667741545416);
  let a0 = 1 + K / Q + K * K;
  const s1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0];
  f0 = 38.13547087602444; Q = 0.5003270373238773; K = Math.tan(Math.PI * f0 / fs);
  a0 = 1 + K / Q + K * K;
  const s2 = [1, -2, 1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0];
  return [s1, s2];
}
function biquadInto(x, out, c) {
  const b0 = c[0], b1 = c[1], b2 = c[2], a1 = c[3], a2 = c[4];
  let z1 = 0, z2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = x[i];
    const y = b0 * v + z1;
    z1 = b1 * v - a1 * y + z2;
    z2 = b2 * v - a2 * y;
    out[i] = y;
  }
}

const tick = () => new Promise(r => setTimeout(r, 0));
const DT_REF = 512 / 44100;

/* ===================================================================
   Stage 1: frame features (the expensive pass, cached per file)
   =================================================================== */
async function extractFeatures(buf, onProgress, opts) {
  onProgress = onProgress || function () {};
  const sr = buf.sampleRate;
  const nch = Math.min(2, buf.numberOfChannels);
  const L = buf.getChannelData(0);
  const R = nch > 1 ? buf.getChannelData(1) : null;
  const len = L.length, dur = len / sr;
  const fine = !(opts && opts.coarse) && dur <= 6;
  const N = fine ? 1024 : 2048, H = fine ? 128 : (dur > 600 ? 1024 : 512);
  const nF = Math.max(1, Math.ceil(len / H));
  const dt = H / sr;
  const fft = getFFT(N);
  const win = hannP(N);
  let w2 = 0; for (let i = 0; i < N; i++) w2 += win[i] * win[i];
  const pnorm = 2 / (N * w2);
  const binHz = sr / N, half = N / 2;
  const nyq = sr / 2;

  /* --- K-weighted energy per block --- */
  const kz = new Float32Array(nF);
  const pk = new Float32Array(nF * 2);
  {
    const coeffs = kFilterCoeffs(sr);
    const chans = R ? [L, R] : [L];
    const tmp = new Float32Array(len), tmp2 = new Float32Array(len);
    for (let c = 0; c < chans.length; c++) {
      biquadInto(chans[c], tmp, coeffs[0]);
      biquadInto(tmp, tmp2, coeffs[1]);
      for (let m = 0; m < nF; m++) {
        const s0 = m * H, s1 = Math.min(len, s0 + H);
        let e = 0, p = 0;
        const src = chans[c];
        for (let s = s0; s < s1; s++) { e += tmp2[s] * tmp2[s]; const a = Math.abs(src[s]); if (a > p) p = a; }
        kz[m] += s1 > s0 ? e / (s1 - s0) : 0;
        pk[m * 2 + c] = p;
      }
    }
    if (!R) for (let m = 0; m < nF; m++) pk[m * 2 + 1] = pk[m * 2];
  }
  onProgress(0.05);
  await tick();

  /* --- band maps --- */
  const kLo = Math.max(1, Math.floor(20 / binHz));
  const barkBin = new Int16Array(half).fill(-1);
  for (let k = 1; k < half; k++) {
    const f = k * binHz;
    for (let b = 0; b < NBARK; b++) if (f >= BARK_EDGES[b] && f < BARK_EDGES[b + 1]) { barkBin[k] = b; break; }
  }
  const melLo = 40, melHi = Math.min(16000, nyq * 0.95);
  const mel = f => 2595 * Math.log10(1 + f / 700);
  const imel = m => 700 * (Math.pow(10, m / 2595) - 1);
  const melPts = [];
  for (let i = 0; i < NMEL + 2; i++) melPts.push(imel(mel(melLo) + (mel(melHi) - mel(melLo)) * i / (NMEL + 1)));
  const melW = [];   // per band: [kStart, weights...]
  for (let b = 0; b < NMEL; b++) {
    const f0 = melPts[b], f1 = melPts[b + 1], f2 = melPts[b + 2];
    const ks = Math.max(1, Math.floor(f0 / binHz)), ke = Math.min(half - 1, Math.ceil(f2 / binHz));
    const ws = [];
    for (let k = ks; k <= ke; k++) {
      const f = k * binHz;
      let w = f < f1 ? (f - f0) / (f1 - f0) : (f2 - f) / (f2 - f1);
      ws.push(Math.max(0, w));
    }
    let sum = 0; for (const w of ws) sum += w;
    if (sum < 1e-9) { ws.length = 0; ws.push(1); }
    melW.push({ ks, ws });
  }
  const sfmLo = Math.max(1, Math.floor(50 / binHz)), sfmHi = Math.min(half - 1, Math.floor(Math.min(16000, nyq * 0.95) / binHz));
  const chLo = Math.max(2, Math.floor(200 / binHz)), chHi = Math.min(half - 2, Math.floor(5000 / binHz));

  const melDb = new Float32Array(nF * NMEL);
  const bark = new Float32Array(nF * NBARK);
  const chroma = new Float32Array(nF * 12);
  const sfm = new Float32Array(nF);
  const re = new Float64Array(N), im = new Float64Array(N);
  const P = new Float64Array(half);

  for (let m = 0; m < nF; m++) {
    const start = m * H + (H >> 1) - (N >> 1);
    for (let i = 0; i < N; i++) {
      const s = start + i;
      if (s >= 0 && s < len) { re[i] = L[s] * win[i]; im[i] = R ? R[s] * win[i] : 0; }
      else { re[i] = 0; im[i] = 0; }
    }
    fft(re, im);
    if (R) {
      for (let k = 1; k < half; k++) {
        const j = N - k;
        P[k] = 0.5 * (re[k] * re[k] + im[k] * im[k] + re[j] * re[j] + im[j] * im[j]) * pnorm;
      }
    } else {
      for (let k = 1; k < half; k++) P[k] = (re[k] * re[k] + im[k] * im[k]) * pnorm;
    }
    // bark energies
    const bo = m * NBARK;
    for (let k = kLo; k < half; k++) { const b = barkBin[k]; if (b >= 0) bark[bo + b] += P[k]; }
    // mel dB
    const mo = m * NMEL;
    for (let b = 0; b < NMEL; b++) {
      const mw = melW[b];
      let e = 0;
      for (let i = 0; i < mw.ws.length; i++) e += mw.ws[i] * P[mw.ks + i];
      melDb[mo + b] = 10 * Math.log10(Math.max(e, 1e-14));
    }
    // spectral flatness (bin level)
    let ls = 0, as = 0, cnt = 0;
    for (let k = sfmLo; k <= sfmHi; k++) { const v = P[k] + 1e-14; ls += Math.log(v); as += v; cnt++; }
    sfm[m] = as > 1e-11 ? Math.exp(ls / cnt) / (as / cnt) : 0;
    // chroma from interpolated spectral peaks
    let pmax = 0;
    for (let k = chLo; k <= chHi; k++) if (P[k] > pmax) pmax = P[k];
    if (pmax > 1e-11) {
      const thr = pmax * 1e-5; // -50 dB
      const co = m * 12;
      for (let k = chLo; k <= chHi; k++) {
        const v = P[k];
        if (v > thr && v > P[k - 1] && v >= P[k + 1]) {
          const a = Math.log(P[k - 1] + 1e-20), b = Math.log(v + 1e-20), c = Math.log(P[k + 1] + 1e-20);
          const den = a - 2 * b + c;
          const d = den !== 0 ? 0.5 * (a - c) / den : 0;
          const f = (k + Math.max(-0.5, Math.min(0.5, d))) * binHz;
          const midi = 69 + 12 * Math.log2(f / 440);
          const pc = ((Math.round(midi) % 12) + 12) % 12;
          chroma[co + pc] += Math.pow(v, CHROMA_EXP);
        }
      }
    }
    if ((m & 255) === 255) { onProgress(0.05 + 0.6 * m / nF); await tick(); }
  }
  onProgress(0.65);
  await tick();

  /* --- fine envelope pass for roughness (30-150 Hz modulation) --- */
  const N2 = 256, H2 = 128;
  const fft2 = getFFT(N2), win2 = hannP(N2);
  let w22 = 0; for (let i = 0; i < N2; i++) w22 += win2[i] * win2[i];
  const pn2 = 2 / (N2 * w22), bin2 = sr / N2;
  const coarseEdges = [];
  for (let c = 0; c <= NCOARSE; c++) coarseEdges.push(BARK_EDGES[Math.min(c * 2, NBARK)]);
  const cBin = new Int8Array(N2 / 2).fill(-1);
  for (let k = 1; k < N2 / 2; k++) {
    const f = k * bin2;
    for (let c = 0; c < NCOARSE; c++) if (f >= coarseEdges[c] && f < coarseEdges[c + 1]) { cBin[k] = c; break; }
  }
  const nE = Math.max(1, Math.ceil(len / H2));
  const env = new Float32Array(nE * NCOARSE);
  const re2 = new Float64Array(N2), im2 = new Float64Array(N2);
  for (let n = 0; n < nE; n++) {
    const start = n * H2 + (H2 >> 1) - (N2 >> 1);
    for (let i = 0; i < N2; i++) {
      const s = start + i;
      if (s >= 0 && s < len) { re2[i] = L[s] * win2[i]; im2[i] = R ? R[s] * win2[i] : 0; }
      else { re2[i] = 0; im2[i] = 0; }
    }
    fft2(re2, im2);
    const eo = n * NCOARSE;
    for (let k = 1; k < N2 / 2; k++) {
      const c = cBin[k]; if (c < 0) continue;
      const j = N2 - k;
      const p = R ? 0.5 * (re2[k] * re2[k] + im2[k] * im2[k] + re2[j] * re2[j] + im2[j] * im2[j]) : (re2[k] * re2[k] + im2[k] * im2[k]);
      env[eo + c] += p * pn2;
    }
    if ((n & 2047) === 2047) { onProgress(0.65 + 0.2 * n / nE); await tick(); }
  }
  for (let i = 0; i < env.length; i++) env[i] = Math.sqrt(env[i]);
  const envRate = sr / H2;
  // modulation analysis blocks
  const LB = 64, HB = 16, MB = 128;
  const fftB = getFFT(MB), winB = hannP(LB);
  let wB2 = 0; for (let i = 0; i < LB; i++) wB2 += winB[i] * winB[i];
  const kR0 = Math.ceil(30 * MB / envRate), kR1 = Math.floor(150 * MB / envRate);
  const nB = Math.max(1, Math.floor((nE - LB) / HB) + 1);
  const rough = new Float32Array(nB * NCOARSE);
  const roughT = new Float32Array(nB);
  const reB = new Float64Array(MB), imB = new Float64Array(MB);
  for (let j = 0; j < nB; j++) {
    const e0 = j * HB;
    roughT[j] = ((e0 + LB / 2) * H2 + H2 / 2) / sr;
    for (let c = 0; c < NCOARSE; c++) {
      let mean = 0, n = 0;
      for (let i = 0; i < LB; i++) { const idx = e0 + i; if (idx < nE) { mean += env[idx * NCOARSE + c]; n++; } }
      mean = n ? mean / n : 0;
      if (mean < 3e-5) { rough[j * NCOARSE + c] = 0; continue; }
      reB.fill(0); imB.fill(0);
      for (let i = 0; i < LB; i++) { const idx = e0 + i; const v = idx < nE ? env[idx * NCOARSE + c] : mean; reB[i] = (v - mean) * winB[i]; }
      fftB(reB, imB);
      let pw = 0;
      for (let k = kR0; k <= kR1; k++) pw += reB[k] * reB[k] + imB[k] * imB[k];
      pw = pw * 2 / (MB * wB2);
      rough[j * NCOARSE + c] = pw / (mean * mean);
    }
  }
  onProgress(0.95);
  await tick();

  const melRaw = FEAT.keepRaw ? Float32Array.from(melDb) : null;
  applyMelFloor(melDb, melDb);
  return {
    sr, len, dur, H, N, nF, dt, nch, fine,
    kz, pk, melDb, melRaw, bark, chroma, sfm,
    rough, roughT, nB
  };
}

/* ===================================================================
   Helpers for stage 2
   =================================================================== */
function prefix(a) { const p = new Float64Array(a.length + 1); for (let i = 0; i < a.length; i++) p[i + 1] = p[i] + a[i]; return p; }
function movMean(a, half) {
  const p = prefix(a), n = a.length, o = new Float32Array(n);
  for (let i = 0; i < n; i++) { const s = Math.max(0, i - half), e = Math.min(n, i + half + 1); o[i] = (p[e] - p[s]) / (e - s); }
  return o;
}
function movMax(a, half) {
  const n = a.length, o = new Float32Array(n);
  for (let i = 0; i < n; i++) { let m = -Infinity; for (let j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) if (a[j] > m) m = a[j]; o[i] = m; }
  return o;
}
function decimatedStat(a, half, step, fn) {
  const n = a.length, o = new Float32Array(n);
  const pts = [];
  for (let c = 0; c < n; c += step) {
    const s = Math.max(0, c - half), e = Math.min(n, c + half + 1);
    pts.push([c, fn(a, s, e)]);
  }
  if (pts.length === 0) return o;
  if (pts[pts.length - 1][0] !== n - 1) { const c = n - 1; pts.push([c, fn(a, Math.max(0, c - half), n)]); }
  let j = 0;
  for (let i = 0; i < n; i++) {
    while (j < pts.length - 2 && pts[j + 1][0] < i) j++;
    const [x0, y0] = pts[j], [x1, y1] = pts[Math.min(j + 1, pts.length - 1)];
    o[i] = x1 === x0 ? y0 : y0 + (y1 - y0) * (i - x0) / (x1 - x0);
  }
  return o;
}
function sliceSortedQ(a, s, e, q) {
  const t = a.subarray(s, e).slice().sort();
  if (!t.length) return 0;
  return t[Math.min(t.length - 1, Math.floor(q * (t.length - 1) + 0.5))];
}
function runQuantile(a, half, step, q) { return decimatedStat(a, half, step, (arr, s, e) => sliceSortedQ(arr, s, e, q)); }
function runMAD(a, half, step) {
  return decimatedStat(a, half, step, (arr, s, e) => {
    const t = arr.subarray(s, e).slice().sort();
    if (!t.length) return 0;
    const med = t[t.length >> 1];
    const d = t.map(v => Math.abs(v - med)).sort();
    return d[d.length >> 1];
  });
}
function asym(a, dt, ta, tr) {
  const n = a.length, o = new Float32Array(n);
  const aa = 1 - Math.exp(-dt / ta), ar = 1 - Math.exp(-dt / tr);
  let y = a[0] || 0;
  for (let i = 0; i < n; i++) { const x = a[i]; y += (x > y ? aa : ar) * (x - y); o[i] = y; }
  return o;
}
function onePole(a, dt, tau) {
  const n = a.length, o = new Float32Array(n), k = 1 - Math.exp(-dt / tau);
  let y = 0; for (let i = 0; i < n; i++) { y += k * (a[i] - y); o[i] = y; }
  return o;
}
function quantileMasked(a, mask, q) {
  let k = 0;
  for (let i = 0; i < a.length; i++) if (!mask || mask[i]) k++;
  if (!k) return 0;
  const t = new Float32Array(k);
  k = 0;
  for (let i = 0; i < a.length; i++) if (!mask || mask[i]) t[k++] = a[i];
  t.sort();
  return t[Math.min(t.length - 1, Math.floor(q * (t.length - 1) + 0.5))];
}
function slopeCentered(y, half) {
  // least-squares slope per frame over [i-half, i+half]
  const n = y.length, o = new Float32Array(n);
  const Sy = new Float64Array(n + 1), Sxy = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) { Sy[i + 1] = Sy[i] + y[i]; Sxy[i + 1] = Sxy[i] + i * y[i]; }
  for (let i = 0; i < n; i++) {
    const s = Math.max(0, i - half), e = Math.min(n - 1, i + half), m = e - s + 1;
    if (m < 3) { o[i] = 0; continue; }
    const sx = (s + e) * m / 2;
    const sxx = (e * (e + 1) * (2 * e + 1) - (s - 1) * s * (2 * s - 1)) / 6;
    const sy = Sy[e + 1] - Sy[s], sxy = Sxy[e + 1] - Sxy[s];
    const den = m * sxx - sx * sx;
    o[i] = den > 0 ? (m * sxy - sx * sy) / den : 0;
  }
  return o;
}
function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
function interpTimes(t, v, frames, dt) {
  // v sampled at times t (ascending), returns per-frame values
  const o = new Float32Array(frames);
  if (!t.length) return o;
  let j = 0;
  for (let m = 0; m < frames; m++) {
    const tm = (m + 0.5) * dt;
    while (j < t.length - 2 && t[j + 1] < tm) j++;
    if (t.length === 1 || tm <= t[0]) { o[m] = v[0]; continue; }
    if (tm >= t[t.length - 1]) { o[m] = v[t.length - 1]; continue; }
    const t0 = t[j], t1 = t[j + 1];
    o[m] = v[j] + (v[j + 1] - v[j]) * (tm - t0) / (t1 - t0);
  }
  return o;
}

/* ===================================================================
   Asset classes (settings per class, from the report's matrix)
   =================================================================== */
const CLASSES = {
  rhythmic:   { label: "Rhythmic music",  lw: 0.4,  ta: 0.2,   tr: 1.5,  tension: true,  harmony: true,  surprise: true },
  orchestral: { label: "Orchestral / tonal", lw: 0.4, ta: 0.2, tr: 1.5,  tension: true,  harmony: true,  surprise: true },
  sfx:        { label: "One-shot SFX",    lw: 0.05, ta: 0.022, tr: 0.05, tension: false, harmony: false, surprise: false },
  ambience:   { label: "Ambience / drone", lw: 3.0, ta: 0.2,   tr: 1.5,  tension: true,  harmony: false, surprise: true }
};

/* Fixed reference ranges used when secondary curves are scaled against a
   library-wide reference instead of per asset. Tuned on the probe suite. */
const REF = { A: 1.2, ED: 8, D: 12, SFM: 0.35, HARM: [0.15, 0.45], F: 1.2, HF: 1.6 };

/* ===================================================================
   Stage 2: curves from features (fast; re-run when settings change)
   =================================================================== */
function scaler(mode, arr, gate, ref, lo, hi) {
  if (mode === "asset") {
    const a = quantileMasked(arr, gate, lo === undefined ? 0.10 : lo);
    const b = quantileMasked(arr, gate, hi === undefined ? 0.95 : hi);
    const span = Array.isArray(ref) ? ref[1] - ref[0] : ref;
    const d = Math.max(b - a, span * 0.15);
    return v => clamp01((v - a) / d);
  }
  if (Array.isArray(ref)) { const lo0 = ref[0], d0 = ref[1] - ref[0]; return v => clamp01((v - lo0) / d0); }
  return v => clamp01(v / ref);
}

/* class-independent intermediate results, cached on F */
function baseOf(F) {
  if (F._base) return F._base;
  const n = F.nF, dt = F.dt;
  const fr = s => Math.max(1, Math.round(s / dt));

  /* gate (momentary LUFS > -70) */
  const kzM = movMean(F.kz, fr(0.2));
  const gate = new Uint8Array(n);
  for (let m = 0; m < n; m++) gate[m] = (-0.691 + 10 * Math.log10(kzM[m] + 1e-15)) > -70 ? 1 : 0;

  /* specific loudness (simplified ISO 532-1 core on 24 Bark bands), sharpness (DIN 45692), band shares */
  const Nsone = new Float32Array(n), sharp = new Float32Array(n);
  const share = new Float32Array(n * NCOARSE);
  const kN = F.cal ? F.cal.kN : 1;
  const np = new Float64Array(NBARK);
  for (let m = 0; m < n; m++) {
    let Nt = 0, num = 0;
    const bo = m * NBARK;
    for (let b = 0; b < NBARK; b++) {
      const E = F.bark[bo + b];
      const d = 10 * Math.log10(E / 0.5 + 1e-20) + 100 - BARK_LTQ[b];
      let v = 0;
      if (d > 0) v = kN * BARK_LTQF[b] * (Math.pow(0.75 + 0.25 * Math.pow(10, 0.1 * d), 0.25) - 1);
      np[b] = v; Nt += v; num += v * SHARP_G[b] * (b + 0.5);
    }
    Nsone[m] = Nt;
    sharp[m] = Nt > 0.1 ? 0.11 * num / Nt : 0;
    for (let c = 0; c < NCOARSE; c++) share[m * NCOARSE + c] = Nt > 1e-6 ? (np[2 * c] + np[2 * c + 1]) / Nt : 0;
  }

  /* roughness R [asper]: 30-150 Hz envelope modulation share, Aures carrier weighting, calibrated */
  const kR = F.cal ? F.cal.kR : 1;
  const Rr = new Float32Array(n);
  {
    const rt = Array.from(F.roughT);
    const Rraw = [];
    for (let c = 0; c < NCOARSE; c++) {
      const arr = new Float32Array(F.nB);
      for (let j = 0; j < F.nB; j++) arr[j] = F.rough[j * NCOARSE + c];
      Rraw.push(interpTimes(rt, arr, n, dt));
    }
    for (let m = 0; m < n; m++) {
      let s = 0;
      for (let c = 0; c < NCOARSE; c++) s += G_AURES[Math.min(24, 2 * c + 1)] * share[m * NCOARSE + c] * Rraw[c][m];
      Rr[m] = gate[m] ? s * kR : 0;
    }
    // roughness is a sustained percept: a 0.5 s running median removes isolated clicks
    const rm = runQuantile(Rr, fr(0.25), Math.max(1, fr(0.05)), 0.5);
    for (let m = 0; m < n; m++) Rr[m] = Math.min(4, rm[m]);
  }

  /* fluctuation strength F [vacil]: 2-20 Hz band-envelope modulation, w_F weighting, calibrated */
  const kF = F.cal ? F.cal.kF : 1;
  const Fl = (function () {
    const dec = Math.max(1, Math.round(DT_REF / dt)), ddt = dt * dec, nd = Math.ceil(n / dec);
    const winLen = Math.min(nd, Math.max(1, Math.round(2.0 / ddt)));
    const M = 256;
    const fftM = getFFT(M), w = hannP(winLen);
    let ws2 = 0; for (let i = 0; i < winLen; i++) ws2 += w[i] * w[i];
    const rate = 1 / ddt;
    const k0 = Math.max(1, Math.ceil(2 * M / rate)), k1 = Math.min(M / 2 - 1, Math.floor(20 * M / rate));
    const wts = new Float64Array(M / 2);
    for (let k = k0; k <= k1; k++) { const f = k * rate / M; wts[k] = 1 / (f / 4 + 4 / f); }
    const env = [];
    for (let c = 0; c < NCOARSE; c++) {
      const e = new Float32Array(nd);
      for (let j = 0; j < nd; j++) {
        let acc = 0, k = 0;
        for (let m = j * dec; m < Math.min(n, (j + 1) * dec); m++) { acc += F.bark[m * NBARK + 2 * c] + F.bark[m * NBARK + 2 * c + 1]; k++; }
        e[j] = Math.sqrt(k ? acc / k : 0);
      }
      env.push(e);
    }
    const step = Math.max(1, Math.round(0.1 / ddt));
    const ts = [], vs = [];
    const reM = new Float64Array(M), imM = new Float64Array(M);
    for (let c0d = 0; c0d < nd; c0d += step) {
      const c0 = Math.min(n - 1, c0d * dec + (dec >> 1));
      const s = Math.max(0, Math.min(nd - winLen, c0d - (winLen >> 1)));
      let tot = 0;
      if (gate[c0]) for (let c = 0; c < NCOARSE; c++) {
        const e = env[c];
        let mean = 0;
        for (let i = 0; i < winLen; i++) mean += e[s + i];
        mean /= winLen;
        if (mean < 3e-5 || share[c0 * NCOARSE + c] < 0.01) continue;
        reM.fill(0); imM.fill(0);
        for (let i = 0; i < winLen && i < M; i++) reM[i] = (e[s + i] - mean) * w[i];
        fftM(reM, imM);
        let pw = 0;
        for (let k = k0; k <= k1; k++) pw += wts[k] * (reM[k] * reM[k] + imM[k] * imM[k]);
        pw = pw * 2 / (M * ws2);
        tot += share[c0 * NCOARSE + c] * pw / (mean * mean);
      }
      ts.push((c0 + 0.5) * dt); vs.push(tot * kF);
    }
    return interpTimes(ts, vs, n, dt);
  })();

  /* harshness factor (Zwicker-Fastl psychoacoustic annoyance form) */
  const N95 = runQuantile(Nsone, fr(1.0), Math.max(1, fr(0.1)), 0.95);
  const hfac = new Float32Array(n);
  for (let m = 0; m < n; m++) {
    if (!gate[m]) continue;
    const S = sharp[m], Nn = Math.max(N95[m], 0.05);
    const wS = S > 1.75 ? (S - 1.75) * 0.25 * Math.log10(Nn + 10) : 0;
    const wFR = (2.18 / Math.pow(Nn, 0.4)) * (0.4 * Fl[m] + 0.6 * Rr[m]);
    hfac[m] = Math.sqrt(wS * wS + wFR * wFR);
  }

  /* flatness */
  const sfmS = movMean(F.sfm, fr(0.25));
  for (let m = 0; m < n; m++) if (!gate[m]) sfmS[m] = 0;

  /* activity: noise-robust log-mel flux + event density */
  const B = NMEL;
  // smoothing span and difference lag are fixed in time (≈ one reference hop), so the
  // flux has the same scale at the fine resolution used for short clips
  const lagF = Math.max(1, Math.round(DT_REF / dt));
  const sm = new Float32Array(n * B);
  for (let m = 0; m < n; m++) {
    const c = m * B, a0 = Math.max(0, m - lagF), a1 = Math.min(n - 1, m + lagF), cnt = a1 - a0 + 1;
    for (let b = 0; b < B; b++) { let v = 0; for (let k = a0; k <= a1; k++) v += F.melDb[k * B + b]; sm[c + b] = v / cnt; }
  }
  const o = new Float32Array(n);
  for (let m = lagF; m < n; m++) {
    if (!gate[m]) continue;
    let s = 0;
    const p = (m - lagF) * B, q = m * B;
    for (let b = 0; b < B; b++) {
      let ref = sm[p + b];
      if (b > 0 && sm[p + b - 1] > ref) ref = sm[p + b - 1];
      if (b < B - 1 && sm[p + b + 1] > ref) ref = sm[p + b + 1];
      const d = sm[q + b] - ref;
      if (d > 0) s += d;
    }
    o[m] = s / B;
  }
  const oFloor = runQuantile(o, fr(0.5), Math.max(1, fr(0.05)), 0.25);
  const op = new Float32Array(n);
  for (let m = 0; m < n; m++) op[m] = Math.max(0, o[m] - oFloor[m]);
  const A = movMean(op, fr(0.05));
  const mad = runMAD(o, fr(1.5), Math.max(1, fr(0.1)));
  const minGap = fr(0.05);
  const onsets = [];
  for (let m = 2; m < n - 2; m++) {
    const v = op[m];
    if (v <= 0) continue;
    if (v < op[m - 1] || v < op[m - 2] || v <= op[m + 1] || v < op[m + 2]) continue;
    if (v < Math.max(ONSET.madK * mad[m] * 1.4826, ONSET.min)) continue;
    if (onsets.length && m - onsets[onsets.length - 1] < minGap) {
      if (v > op[onsets[onsets.length - 1]]) onsets[onsets.length - 1] = m;
      continue;
    }
    onsets.push(m);
  }
  const oc = new Float32Array(n + 1);
  { let j = 0; for (let m = 0; m < n; m++) { oc[m + 1] = oc[m]; while (j < onsets.length && onsets[j] === m) { oc[m + 1]++; j++; } } }
  const ED = new Float32Array(n);
  const edH = fr(1.0);
  for (let m = 0; m < n; m++) { const s = Math.max(0, m - edH), e = Math.min(n, m + edH + 1); ED[m] = (oc[e] - oc[s]) / ((e - s) * dt); }
  const actFixed = new Float32Array(n);
  for (let m = 0; m < n; m++) actFixed[m] = gate[m] ? 0.5 * clamp01(A[m] / REF.A) + 0.5 * clamp01(ED[m] / REF.ED) : 0;

  /* loudness fluctuation (SD of 50 ms level over 1.5 s) */
  const kz50 = movMean(F.kz, Math.max(1, fr(0.025)));
  const ldb = new Float32Array(n);
  for (let m = 0; m < n; m++) ldb[m] = Math.max(-70, -0.691 + 10 * Math.log10(kz50[m] + 1e-15));
  const D = new Float32Array(n);
  {
    const h = fr(0.75);
    const p1 = prefix(ldb), sq = new Float32Array(n);
    for (let m = 0; m < n; m++) sq[m] = ldb[m] * ldb[m];
    const p2 = prefix(sq);
    for (let m = 0; m < n; m++) {
      const s = Math.max(0, m - h), e = Math.min(n, m + h + 1), k = e - s;
      const mu = (p1[e] - p1[s]) / k;
      D[m] = gate[m] ? Math.sqrt(Math.max(0, (p2[e] - p2[s]) / k - mu * mu)) : 0;
    }
  }

  /* harmonic tension: Tonal Interval Vector on chroma */
  const tonal = new Float32Array(n);
  const actS = movMean(actFixed, fr(0.5));
  for (let m = 0; m < n; m++) tonal[m] = gate[m] ? clamp01(1 - sfmS[m] / 0.3) * (1 - 0.8 * actS[m]) : 0;
  const harm = new Float32Array(n), harmD = new Float32Array(n), harmK = new Float32Array(n), harmL = new Float32Array(n);
  {
    const h = fr(0.5);
    const chS = [];
    for (let p = 0; p < 12; p++) { const a = new Float32Array(n); for (let m = 0; m < n; m++) a[m] = F.chroma[m * 12 + p]; chS.push(movMean(a, h)); }
    const cosT = new Float64Array(72), sinT = new Float64Array(72);
    for (let k = 1; k <= 6; k++) for (let p = 0; p < 12; p++) { const a = -2 * Math.PI * k * p / 12; cosT[(k - 1) * 12 + p] = Math.cos(a); sinT[(k - 1) * 12 + p] = Math.sin(a); }
    const Tr = new Float32Array(n * 6), Ti = new Float32Array(n * 6), has = new Uint8Array(n);
    for (let m = 0; m < n; m++) {
      let sum = 0; for (let p = 0; p < 12; p++) sum += chS[p][m];
      if (!(sum > 1e-14) || !gate[m]) continue;
      has[m] = 1;
      for (let k = 0; k < 6; k++) {
        let r = 0, i = 0;
        for (let p = 0; p < 12; p++) { const v = chS[p][m] / sum; r += v * cosT[k * 12 + p]; i += v * sinT[k * 12 + p]; }
        Tr[m * 6 + k] = TIV_W[k] * r; Ti[m * 6 + k] = TIV_W[k] * i;
      }
    }
    const ak = 1 - Math.exp(-dt / 15);
    const kr = new Float64Array(6), ki = new Float64Array(6);
    let init = false;
    const lag = fr(1.0);
    for (let m = 0; m < n; m++) {
      if (!has[m]) continue;
      let norm2 = 0;
      for (let k = 0; k < 6; k++) norm2 += Tr[m * 6 + k] ** 2 + Ti[m * 6 + k] ** 2;
      const Dv = 1 - Math.sqrt(norm2) / TIV_NORM;
      if (!init) { for (let k = 0; k < 6; k++) { kr[k] = Tr[m * 6 + k]; ki[k] = Ti[m * 6 + k]; } init = true; }
      let dk = 0;
      for (let k = 0; k < 6; k++) dk += (Tr[m * 6 + k] - kr[k]) ** 2 + (Ti[m * 6 + k] - ki[k]) ** 2;
      const Kv = Math.sqrt(dk) / TIV_NORM;
      for (let k = 0; k < 6; k++) { kr[k] += ak * (Tr[m * 6 + k] - kr[k]); ki[k] += ak * (Ti[m * 6 + k] - ki[k]); }
      const a = Math.max(0, m - lag), b = Math.min(n - 1, m + lag);
      let dl = 0;
      if (has[a] && has[b]) for (let k = 0; k < 6; k++) dl += (Tr[b * 6 + k] - Tr[a * 6 + k]) ** 2 + (Ti[b * 6 + k] - Ti[a * 6 + k]) ** 2;
      const Lv = Math.sqrt(dl) / TIV_NORM;
      harmD[m] = Dv; harmK[m] = Kv; harmL[m] = Lv;
      harm[m] = tonal[m] * (0.40 * Dv + 0.35 * Kv + 0.25 * Lv);
    }
    const sm6 = movMean(harm, fr(0.5));
    for (let m = 0; m < n; m++) harm[m] = gate[m] ? sm6[m] : 0;
  }

  /* surprise: online diagonal Gaussian on 40 log-mel bands at ~10 Hz */
  const sStep = Math.max(1, fr(0.1));
  const G = Math.ceil(n / sStep);
  const sur = new Float32Array(G), sgate = new Uint8Array(G);
  {
    const mu = new Float64Array(B), va = new Float64Array(B).fill(25);
    let init = false;
    const al = 1 - Math.exp(-(sStep * dt) / 5);
    const x = new Float64Array(B);
    for (let i = 0; i < G; i++) {
      const s = i * sStep, e = Math.min(n, s + sStep);
      let gsum = 0;
      x.fill(0);
      for (let m = s; m < e; m++) { gsum += gate[m]; const q = m * B; for (let b = 0; b < B; b++) x[b] += F.melDb[q + b]; }
      for (let b = 0; b < B; b++) x[b] /= (e - s);
      sgate[i] = gsum * 2 >= (e - s) ? 1 : 0;
      if (!sgate[i]) continue;
      if (!init) { for (let b = 0; b < B; b++) mu[b] = x[b]; init = true; continue; }
      let su = 0;
      for (let b = 0; b < B; b++) { const d = x[b] - mu[b]; su += d * d / va[b]; }
      sur[i] = Math.log1p(0.5 * su / B);
      for (let b = 0; b < B; b++) { const d = x[b] - mu[b]; mu[b] += al * d; va[b] = Math.max((1 - al) * va[b] + al * d * d, 4); }
    }
  }

  F._base = { gate, Nsone, sharp, share, Rr, Fl, N95, hfac, sfmS, o, op, A, ED, onsets, actFixed, D, ldb, tonal, harm, harmD, harmK, harmL, sur, sgate, sStep, G };
  return F._base;
}

function computeCurves(F, cls, opt) {
  opt = opt || {};
  const scaleMode = opt.scale || "fixed";
  const C = CLASSES[cls] || CLASSES.rhythmic;
  const n = F.nF, dt = F.dt;
  const fr = s => Math.max(1, Math.round(s / dt));
  const X = baseOf(F);
  const gate = X.gate;

  /* C1 perceptual loudness: K-weighted, class window, one-pole in the dB domain */
  const lwHalf = Math.max(0, Math.round(C.lw / dt / 2));
  const kzW = lwHalf > 0 ? movMean(F.kz, lwHalf) : Float32Array.from(F.kz);
  const lraw = new Float32Array(n);
  for (let m = 0; m < n; m++) lraw[m] = Math.max(-70, -0.691 + 10 * Math.log10(kzW[m] + 1e-15));
  const lufs = asym(lraw, dt, C.ta * 0.5, C.tr * 0.5);
  const c1 = new Float32Array(n);
  for (let m = 0; m < n; m++) c1[m] = clamp01((lufs[m] + 60) / 60);

  /* C4 harshness-weighted loudness: C1 plus the annoyance uplift, in loudness-level units */
  const c4 = new Float32Array(n);
  for (let m = 0; m < n; m++) c4[m] = gate[m] ? clamp01(c1[m] + 10 * Math.log2(1 + X.hfac[m]) / 60) : 0;

  /* C2 activity */
  const scA = scaler(scaleMode, X.A, gate, REF.A);
  const scED = scaler(scaleMode, X.ED, gate, REF.ED);
  const c2 = new Float32Array(n);
  for (let m = 0; m < n; m++) c2[m] = gate[m] ? 0.5 * scA(X.A[m]) + 0.5 * scED(X.ED[m]) : 0;

  /* C3 arousal composite */
  const scD = scaler(scaleMode, X.D, gate, REF.D);
  const scS = scaler(scaleMode, X.sfmS, gate, REF.SFM);
  const c3raw = new Float32Array(n);
  for (let m = 0; m < n; m++) c3raw[m] = gate[m] ? 0.50 * c1[m] + 0.15 * scD(X.D[m]) + 0.20 * c2[m] + 0.15 * scS(X.sfmS[m]) : 0;
  const c3s = asym(c3raw, dt, C.ta * 0.5, C.tr * 0.5);
  const c3 = new Float32Array(n);
  for (let m = 0; m < n; m++) c3[m] = gate[m] ? clamp01((c3s[m] - 0.1) / 0.6) : 0;

  /* C6 harmonic tension */
  const c6 = new Float32Array(n);
  if (C.harmony) {
    const sc6 = scaler(scaleMode, X.harm, gate, REF.HARM);
    for (let m = 0; m < n; m++) c6[m] = gate[m] ? sc6(X.harm[m]) : 0;
  }

  /* C5 trend-salience tension (Farbood-style integrated slopes) */
  const c5 = new Float32Array(n);
  if (C.tension) {
    const h05 = fr(0.25);
    // features in fixed perceptual units (not per-asset z-scores), so a static
    // texture integrates to almost nothing instead of being stretched to full range
    const unit = (a, u) => { const o2 = movMean(a, h05); for (let m = 0; m < n; m++) o2[m] /= u; return o2; };
    const feats = [[unit(lufs, 6), 0.45, 3], [unit(X.sharp, 0.4), 0.20, 3], [unit(X.Rr, 0.25), 0.20, 3]];
    if (C.harmony) feats.push([unit(X.harm, 0.08), 0.15, 7]);
    let wsum = 0; for (const f of feats) wsum += f[1];
    const sp = new Float32Array(n);
    for (const [f, w, win] of feats) {
      const s = slopeCentered(f, fr(win / 2));
      const lagF = fr(win);
      for (let m = 0; m < n; m++) {
        const prev = m - lagF >= 0 ? s[m - lagF] : 0;
        const beta = (s[m] > 0 && prev > 0) || (s[m] < 0 && prev < 0) ? 5 : 1;
        sp[m] += (w / wsum) * beta * s[m] / dt;
      }
    }
    for (let m = 0; m < n; m++) if (!gate[m]) sp[m] = 0;
    const S = onePole(sp, dt, 1.5);
    const T = new Float32Array(n);
    const lam = Math.exp(-dt / 25);
    let t = 0;
    for (let m = 0; m < n; m++) { t = lam * t + dt * S[m]; T[m] = t; }
    const lo = quantileMasked(T, gate, 0.05), hi = quantileMasked(T, gate, 0.95);
    const d = Math.max(hi - lo, 1.5);
    for (let m = 0; m < n; m++) c5[m] = gate[m] ? clamp01(0.6 * clamp01((T[m] - lo) / d) + 0.4 * c1[m]) : 0;
  }

  /* C7 surprise, residualised against loudness */
  const c7 = new Float32Array(n);
  const ticks = [];
  if (C.surprise) {
    const { sur, sgate, sStep, G } = X;
    const gl = new Float32Array(G);
    for (let i = 0; i < G; i++) { const s = i * sStep, e = Math.min(n, s + sStep); let t = 0; for (let m = s; m < e; m++) t += c1[m]; gl[i] = t / (e - s); }
    let sx = 0, sy = 0, sxx = 0, sxy = 0, k = 0;
    for (let i = 0; i < G; i++) if (sgate[i]) { sx += gl[i]; sy += sur[i]; sxx += gl[i] * gl[i]; sxy += gl[i] * sur[i]; k++; }
    const den = k * sxx - sx * sx;
    const bb = den > 1e-9 ? (k * sxy - sx * sy) / den : 0, aa = k ? (sy - bb * sx) / k : 0;
    const warm = Math.min(Math.round(4 / (sStep * dt)), Math.round(0.10 * G));
    const u = new Float32Array(G), mask = new Uint8Array(G);
    for (let i = 0; i < G; i++) { mask[i] = sgate[i] && i >= warm ? 1 : 0; u[i] = mask[i] ? sur[i] - (aa + bb * gl[i]) : 0; }
    const p95 = quantileMasked(u, mask, 0.95), p50 = quantileMasked(u, mask, 0.5), p99 = quantileMasked(u, mask, 0.99);
    const dd = Math.max(p99 - p50, 0.6);
    const cand = [];
    for (let i = 1; i < G - 1; i++) if (mask[i] && u[i] > p95 && u[i] >= u[i - 1] && u[i] >= u[i + 1] && u[i] - p50 > 0.35) cand.push(i);
    cand.sort((a, b) => u[b] - u[a]);
    const gap = Math.round(3 / (sStep * dt));
    const maxT = Math.max(1, Math.round(F.dur / 10));
    const chosen = [];
    for (const i of cand) { if (chosen.length >= maxT) break; if (chosen.every(j => Math.abs(j - i) >= gap)) chosen.push(i); }
    chosen.sort((a, b) => a - b);
    for (const i of chosen) ticks.push(Math.min(n - 1, i * sStep + (sStep >> 1)));
    const p75 = quantileMasked(u, mask, 0.75), d75 = Math.max(p99 - p75, 0.5);
    for (let m = 0; m < n; m++) { const i = Math.min(G - 1, Math.floor(m / sStep)); c7[m] = mask[i] ? clamp01((u[i] - p75) / d75) : 0; }
    const c7s = movMean(c7, fr(0.15));
    for (let m = 0; m < n; m++) c7[m] = c7s[m];
  }

  /* colour channels */
  const scHF = scaler(scaleMode, X.hfac, gate, REF.HF);
  const scFl = scaler(scaleMode, X.Fl, gate, REF.F);
  const harshC = new Float32Array(n), flC = new Float32Array(n);
  for (let m = 0; m < n; m++) { harshC[m] = gate[m] ? scHF(X.hfac[m]) : 0; flC[m] = gate[m] ? scFl(X.Fl[m]) : 0; }

  return {
    cls, gate, lufs, onsets: X.onsets, ticks,
    curves: { c1, c2, c3, c4, c5, c6, c7 },
    colour: { activity: c2, harsh: harshC, fluct: flC, harmony: c6 },
    raw: { A: X.A, ED: X.ED, D: X.D, sfm: X.sfmS, sharp: X.sharp, Rr: X.Rr, Fl: X.Fl, Nsone: X.Nsone, hfac: X.hfac, c6: X.harm, tonal: X.tonal, harmD: X.harmD, harmK: X.harmK, harmL: X.harmL }
  };
}

/* ===================================================================
   Rhythm: onset envelope, tempo, beats (Ellis DP), local pulse (PLP),
   pulse clarity, downbeats. Class-independent, cached on F.
   =================================================================== */
const RHYTHM = { bpmMin: 40, bpmMax: 240, priorBpm: 120, priorOct: 1.0, tightness: 100, acWin: 8, acHop: 0.5, plpWin: 4.5, plpBand: 0.18, clarityMin: 0.4, strengthRef: 0.03, gamma: 300, lowWeight: 1, smooth: 0.014, smoothT: 0.03, timeOffset: 0 };

function nextPow2(v) { let p = 1; while (p < v) p <<= 1; return p; }
function logPrior(bpm) { const z = Math.log2(bpm / RHYTHM.priorBpm) / RHYTHM.priorOct; return -0.5 * z * z; }
function parabolic(a, i) {
  if (i <= 0 || i >= a.length - 1) return 0;
  const y0 = a[i - 1], y1 = a[i], y2 = a[i + 1], den = y0 - 2 * y1 + y2;
  return den !== 0 ? Math.max(-0.5, Math.min(0.5, 0.5 * (y0 - y2) / den)) : 0;
}

function rhythmOf(F) {
  if (F._rhythm) return F._rhythm;
  const X = baseOf(F);
  const n = F.nF, dt = F.dt;
  const fr = s => Math.max(1, Math.round(s / dt));
  const frameTime = m => (m + 0.5) * dt + RHYTHM.timeOffset;

  /* onset envelope for rhythm: log(1 + γE) compressed mel energies (quiet bands count
     little), SuperFlux max filter across bands, local mean removed, rectified, unit RMS */
  const B = NMEL;
  const fmax = new Float32Array(n);
  for (let m = 0; m < n; m++) { let mx = 0; for (let b = 0; b < B; b++) { const e = Math.pow(10, F.melDb[m * B + b] / 10); if (e > mx) mx = e; } fmax[m] = mx; }
  const Eglob = Math.max(1e-12, quantileMasked(fmax, X.gate, 0.95));
  // local reference (running max over ±2 s): quiet passages are compressed like loud ones
  const Eloc = runQuantile(fmax, fr(2.0), Math.max(1, fr(0.1)), 1.0);
  const Cc = new Float32Array(n * B);
  for (let m = 0; m < n; m++) {
    const er = Math.max(Eloc[m], Eglob * 1e-4);
    for (let b = 0; b < B; b++) Cc[m * B + b] = Math.log1p(RHYTHM.gamma * Math.pow(10, F.melDb[m * B + b] / 10) / er);
  }
  // three regions with equal say: low (< 200 Hz), mid (200 Hz - 2 kHz), high (> 2 kHz)
  const melC = b => { const ml = 2595 * Math.log10(1 + 40 / 700), mh = 2595 * Math.log10(1 + Math.min(16000, F.sr * 0.475) / 700); const mm = ml + (mh - ml) * (b + 1) / (B + 1); return 700 * (Math.pow(10, mm / 2595) - 1); };
  const reg = new Int8Array(B), regN = [0, 0, 0];
  for (let b = 0; b < B; b++) { const f = melC(b); reg[b] = f < 200 ? 0 : f < 2000 ? 1 : 2; regN[reg[b]]++; }
  const bw = new Float32Array(B); for (let b = 0; b < B; b++) bw[b] = (reg[b] === 0 ? RHYTHM.lowWeight : 1) / Math.max(1, regN[reg[b]]);
  const oR = new Float32Array(n);
  const lagR = Math.max(1, Math.round(DT_REF / dt));
  for (let m = lagR; m < n; m++) {
    if (!X.gate[m]) continue;
    let sum = 0;
    const p = (m - lagR) * B, q = m * B;
    for (let b = 0; b < B; b++) {
      let ref = Cc[p + b];
      if (b > 0 && Cc[p + b - 1] > ref) ref = Cc[p + b - 1];
      if (b < B - 1 && Cc[p + b + 1] > ref) ref = Cc[p + b + 1];
      const d = Cc[q + b] - ref;
      if (d > 0) sum += bw[b] * d;
    }
    oR[m] = sum;
  }
  const lm = movMean(oR, fr(0.5));
  const env = new Float32Array(n);
  let s2 = 0;
  const envAbs = new Float32Array(n);
  {
    const raw = new Float32Array(n);
    for (let m = 0; m < n; m++) raw[m] = Math.max(0, oR[m] - lm[m]);
    const sg = Math.max(0.6, RHYTHM.smooth / dt), K = Math.ceil(3 * sg);
    const gk = []; let gs = 0; for (let k = -K; k <= K; k++) { const v = Math.exp(-0.5 * (k / sg) * (k / sg)); gk.push(v); gs += v; }
    for (let m = 0; m < n; m++) { let v = 0; for (let k = -K; k <= K; k++) { const j = m + k; if (j >= 0 && j < n) v += gk[k + K] * raw[j]; } v /= gs; env[m] = v; envAbs[m] = v; s2 += v * v; }
  }
  const rms = Math.sqrt(s2 / Math.max(1, n)) || 1;
  for (let m = 0; m < n; m++) env[m] /= rms;

  /* extra-smoothed copy for the tempogram and clarity (tolerates tempo drift) */
  const envT = new Float32Array(n);
  {
    const sg = Math.max(0.6, RHYTHM.smoothT / dt), K = Math.ceil(3 * sg);
    const gk = []; let gs = 0; for (let k = -K; k <= K; k++) { const v = Math.exp(-0.5 * (k / sg) * (k / sg)); gk.push(v); gs += v; }
    for (let m = 0; m < n; m++) { let v = 0; for (let k = -K; k <= K; k++) { const j = m + k; if (j >= 0 && j < n) v += gk[k + K] * env[j]; } envT[m] = v / gs; }
  }
  /* autocorrelation tempogram: 8 s windows every 0.5 s */
  const W = Math.min(n, fr(RHYTHM.acWin)), hopT = fr(RHYTHM.acHop);
  const NF = nextPow2(2 * W), fftA = getFFT(NF), winA = hannP(W);
  const lagMin = Math.max(2, Math.floor(60 / (RHYTHM.bpmMax * dt)));
  const lagMax = Math.min(W - 2, Math.ceil(60 / (RHYTHM.bpmMin * dt)));
  const nL = Math.max(0, lagMax - lagMin + 1);
  const centers = [];
  for (let c = 0; c < n; c += hopT) centers.push(c);
  if (!centers.length) centers.push(0);
  const NW = centers.length;
  const acn = new Float32Array(NW * nL);   // raw, normalised by lag 0 (tempo)
  const acm = new Float32Array(NW * nL);   // mean removed (clarity)
  const wE = new Float32Array(NW), wS = new Float32Array(NW);
  const reA = new Float64Array(NF), imA = new Float64Array(NF);
  const acInto = (dst, w) => {
    fftA(reA, imA);
    for (let k = 0; k < NF; k++) { reA[k] = reA[k] * reA[k] + imA[k] * imA[k]; imA[k] = 0; }
    fftA(reA, imA);
    const a0 = reA[0];
    if (!(a0 > 0)) return;
    for (let l = 0; l < nL; l++) dst[w * nL + l] = reA[lagMin + l] / a0;
  };
  for (let w = 0; w < NW; w++) {
    const s0 = centers[w] - (W >> 1);
    let mean = 0, cnt = 0, sa = 0, ws = 0;
    for (let k = 0; k < W; k++) { const m = s0 + k; if (m >= 0 && m < n) { mean += envT[m]; cnt++; sa += winA[k] * envAbs[m] * envAbs[m]; ws += winA[k]; } }
    mean = cnt ? mean / cnt : 0;
    wS[w] = ws > 0 ? Math.sqrt(sa / ws) : 0;
    if (nL === 0) continue;
    reA.fill(0); imA.fill(0);
    let e = 0;
    for (let k = 0; k < W; k++) { const m = s0 + k; const v = m >= 0 && m < n ? envT[m] : 0; reA[k] = v * winA[k]; e += reA[k] * reA[k]; }
    wE[w] = e;
    if (e < 1e-6) continue;
    acInto(acn, w);
    reA.fill(0); imA.fill(0);
    for (let k = 0; k < W; k++) { const m = s0 + k; const v = (m >= 0 && m < n ? envT[m] : mean) - mean; reA[k] = v * winA[k]; }
    acInto(acm, w);
  }
  const lagBpm = l => 60 / ((lagMin + l) * dt);

  /* pulse clarity per window: max normalised autocorrelation in the tempo range */
  const clarW = new Float32Array(NW);
  for (let w = 0; w < NW; w++) {
    let mx = 0; for (let l = 0; l < nL; l++) if (acm[w * nL + l] > mx) mx = acm[w * nL + l];
    clarW[w] = Math.min(1, mx) * (1 - Math.exp(-wS[w] / RHYTHM.strengthRef));
  }

  /* global tempo: mean tempogram over active windows + log-normal prior (librosa style) */
  const mean = new Float64Array(nL);
  let act = 0;
  for (let w = 0; w < NW; w++) if (wE[w] > 1e-6) { act++; for (let l = 0; l < nL; l++) mean[l] += acn[w * nL + l]; }
  let tempo = 0, bestL = -1;
  if (act && nL) {
    for (let l = 0; l < nL; l++) mean[l] /= act;
    let best = -Infinity;
    for (let l = 0; l < nL; l++) { const sc = Math.log(Math.max(mean[l], 1e-6)) + logPrior(lagBpm(l)); if (sc > best) { best = sc; bestL = l; } }
    const d = parabolic(mean, bestL);
    tempo = 60 / ((lagMin + bestL + d) * dt);
  }

  /* local tempo: Viterbi over tempogram lags, penalising log-tempo jumps */
  const ltW = new Float32Array(NW);
  if (nL && NW) {
    const lam = 12;
    const lp = new Float64Array(nL); for (let l = 0; l < nL; l++) lp[l] = logPrior(lagBpm(l));
    const lg = new Float64Array(nL); for (let l = 0; l < nL; l++) lg[l] = Math.log2(lagMin + l);
    let prev = new Float64Array(nL), cur = new Float64Array(nL);
    const bp = new Int16Array(NW * nL);
    for (let l = 0; l < nL; l++) prev[l] = Math.log(Math.max(acn[l], 1e-3)) + lp[l];
    for (let w = 1; w < NW; w++) {
      for (let l = 0; l < nL; l++) {
        let b = -Infinity, bi = 0;
        for (let k = 0; k < nL; k++) { const d = lg[l] - lg[k]; const v = prev[k] - lam * d * d; if (v > b) { b = v; bi = k; } }
        cur[l] = b + Math.log(Math.max(acn[w * nL + l], 1e-3)) + lp[l];
        bp[w * nL + l] = bi;
      }
      const t = prev; prev = cur; cur = t;
    }
    let bl = 0; for (let l = 1; l < nL; l++) if (prev[l] > prev[bl]) bl = l;
    for (let w = NW - 1; w >= 0; w--) {
      const row = acn.subarray(w * nL, (w + 1) * nL);
      const d = parabolic(row, bl);
      ltW[w] = 60 / ((lagMin + bl + d) * dt);
      if (w > 0) bl = bp[w * nL + bl];
    }
  }
  const tW = centers.map(c => (c + 0.5) * dt);
  const clarity = interpTimes(tW, Array.from(clarW), n, dt);
  const ltempo = interpTimes(tW, Array.from(ltW), n, dt);
  for (let m = 0; m < n; m++) if (!X.gate[m]) clarity[m] = 0;

  /* Ellis (2007) dynamic-programming beat tracker, librosa defaults */
  let beats = [];
  if (tempo > 0) {
    const p = 60 / (tempo * dt), P = Math.max(1, Math.round(p));
    const gw = new Float64Array(2 * P + 1);
    for (let k = -P; k <= P; k++) gw[k + P] = Math.exp(-0.5 * Math.pow(k * 32 / p, 2));
    const ls = new Float64Array(n);
    for (let m = 0; m < n; m++) { let s = 0; for (let k = -P; k <= P; k++) { const j = m + k; if (j >= 0 && j < n) s += env[j] * gw[k + P]; } ls[m] = s; }
    let lsMax = 0; for (let m = 0; m < n; m++) if (ls[m] > lsMax) lsMax = ls[m];
    const wS = -Math.round(2 * p), wEnd = -Math.round(p / 2);
    const txwt = []; for (let o = wS; o <= wEnd; o++) txwt.push(-RHYTHM.tightness * Math.pow(Math.log(-o / p), 2));
    const cum = new Float64Array(n), back = new Int32Array(n);
    let first = true;
    for (let i = 0; i < n; i++) {
      let best = -Infinity, bo = 0;
      for (let q = 0; q < txwt.length; q++) { const j = i + wS + q; const v = txwt[q] + (j >= 0 ? cum[j] : 0); if (v > best) { best = v; bo = wS + q; } }
      cum[i] = ls[i] + best;
      if (first && ls[i] < 0.01 * lsMax) back[i] = -1;
      else { back[i] = i + bo >= 0 ? i + bo : -1; first = false; }
    }
    const maxes = [];
    for (let i = 1; i < n - 1; i++) if (cum[i] > cum[i - 1] && cum[i] >= cum[i + 1]) maxes.push(i);
    if (maxes.length) {
      const sorted = maxes.map(i => cum[i]).sort((a, b) => a - b);
      const med = sorted[sorted.length >> 1];
      let last = -1;
      for (const i of maxes) if (cum[i] * 2 > med) last = i;
      if (last >= 0) {
        const bt = [last];
        while (back[bt[bt.length - 1]] >= 0) bt.push(back[bt[bt.length - 1]]);
        bt.reverse();
        // trim weak leading and trailing beats (librosa __trim_beats)
        const hw = [0.5, 1, 0.5];
        const sm = bt.map((b, i) => { let s = 0; for (let k = -1; k <= 1; k++) { const j = i + k; if (j >= 0 && j < bt.length) s += hw[k + 1] * ls[bt[j]]; } return s; });
        const thr = 0.5 * Math.sqrt(sm.reduce((a, v) => a + v * v, 0) / Math.max(1, sm.length));
        let a = 0, b = bt.length - 1;
        while (a <= b && sm[a] <= thr) a++;
        while (b >= a && sm[b] <= thr) b--;
        beats = bt.slice(a, b + 1);
      }
    }
  }

  /* PLP (Grosche & Müller 2011): Fourier tempogram peak per window, unit-magnitude
     sinusoids overlap-added, half-wave rectified */
  const plp = new Float32Array(n);
  const plpTempoF = new Float32Array(n);
  {
    const Wp = Math.min(n, Math.max(8, fr(RHYTHM.plpWin)));
    const hp = Math.max(1, fr(0.023));
    const NP = nextPow2(Wp) * 2, fftP = getFFT(NP), wp = hannP(Wp);
    const kMin = Math.max(1, Math.floor(RHYTHM.bpmMin / 60 * NP * dt)), kMax = Math.min(NP / 2 - 2, Math.ceil(RHYTHM.bpmMax / 60 * NP * dt));
    const pri = new Float64Array(NP / 2);
    for (let k = kMin; k <= kMax; k++) pri[k] = logPrior(Math.max(1e-3, k / (NP * dt) * 60));
    const acc = new Float64Array(n), nrm = new Float64Array(n), tacc = new Float64Array(n);
    const re = new Float64Array(NP), im = new Float64Array(NP), mag = new Float64Array(NP / 2);
    for (let c = 0; c < n; c += hp) {
      const s0 = c - (Wp >> 1);
      let mu = 0, cnt = 0;
      for (let k = 0; k < Wp; k++) { const m = s0 + k; if (m >= 0 && m < n) { mu += env[m]; cnt++; } }
      mu = cnt ? mu / cnt : 0;
      re.fill(0); im.fill(0);
      let e = 0;
      for (let k = 0; k < Wp; k++) { const m = s0 + k; const v = ((m >= 0 && m < n) ? env[m] : mu) - mu; re[k] = v * wp[k]; e += re[k] * re[k]; }
      if (e < 1e-8) continue;
      const seg = Float64Array.from(re.subarray(0, Wp));
      fftP(re, im);
      const lt = ltempo[Math.min(n - 1, Math.max(0, c))];
      let k0 = kMin, k1 = kMax;
      if (lt > 0) {
        k0 = Math.max(kMin, Math.floor(lt * (1 - RHYTHM.plpBand) / 60 * NP * dt));
        k1 = Math.min(kMax, Math.ceil(lt * (1 + RHYTHM.plpBand) / 60 * NP * dt));
        if (k1 <= k0 + 1) { k0 = Math.max(kMin, k0 - 1); k1 = Math.min(kMax, k1 + 1); }
      }
      for (let k = Math.max(kMin, k0 - 1); k <= Math.min(kMax, k1 + 1); k++) mag[k] = Math.sqrt(re[k] * re[k] + im[k] * im[k]);
      let bk = k0, bs = -Infinity;
      for (let k = k0; k <= k1; k++) { const sc = Math.log1p(1e6 * mag[k]) + pri[k]; if (sc > bs) { bs = sc; bk = k; } }
      const lm2 = k => Math.log(mag[k] + 1e-12);
      let d = 0;
      if (bk > Math.max(kMin, k0 - 1) && bk < Math.min(kMax, k1 + 1)) { const y0 = lm2(bk - 1), y1 = lm2(bk), y2 = lm2(bk + 1), den = y0 - 2 * y1 + y2; d = den !== 0 ? Math.max(-0.5, Math.min(0.5, 0.5 * (y0 - y2) / den)) : 0; }
      const om = 2 * Math.PI * (bk + d) / NP;
      let xr = 0, xi = 0;
      for (let k = 0; k < Wp; k++) { xr += seg[k] * Math.cos(om * k); xi -= seg[k] * Math.sin(om * k); }
      const ph = Math.atan2(xi, xr);
      const bpmHere = (bk + d) / (NP * dt) * 60;
      for (let k = 0; k < Wp; k++) {
        const m = s0 + k; if (m < 0 || m >= n) continue;
        acc[m] += wp[k] * Math.cos(om * k + ph);
        nrm[m] += wp[k] * wp[k];
        tacc[m] += wp[k] * wp[k] * bpmHere;
      }
    }
    let mx = 0;
    for (let m = 0; m < n; m++) { const v = nrm[m] > 1e-9 ? Math.max(0, acc[m] / nrm[m]) : 0; plp[m] = v; if (v > mx) mx = v; plpTempoF[m] = nrm[m] > 1e-9 ? tacc[m] / nrm[m] : 0; }
    if (mx > 0) for (let m = 0; m < n; m++) plp[m] /= mx;
  }
  const plpBeats = [];
  for (let m = 1; m < n - 1; m++) {
    if (plp[m] < 0.45 || plp[m] < plp[m - 1] || plp[m] < plp[m + 1] || clarity[m] < 0.5 * RHYTHM.clarityMin) continue;
    const per = plpTempoF[m] > 0 ? 60 / (plpTempoF[m] * dt) : fr(0.5);
    if (plpBeats.length && m - plpBeats[plpBeats.length - 1] < 0.6 * per) { if (plp[m] > plp[plpBeats[plpBeats.length - 1]]) plpBeats[plpBeats.length - 1] = m; continue; }
    plpBeats.push(m);
  }

  /* downbeats (heuristic, 4/4 assumed): low-band onsets plus chord change at the beat */
  let downbeats = [], downConf = 0, downPhase = 0;
  if (beats.length >= 8) {
    const B = NMEL;
    const low = new Float32Array(n);
    for (let m = 1; m < n; m++) { let s = 0; for (let b = 0; b < 2; b++) s += Math.max(0, F.melDb[m * B + b] - F.melDb[(m - 1) * B + b]); low[m] = s; }
    const chromaMean = (a, b) => {
      const v = new Float64Array(12); a = Math.max(0, a); b = Math.min(n, b);
      for (let m = a; m < b; m++) for (let p = 0; p < 12; p++) v[p] += F.chroma[m * 12 + p];
      let s = 0; for (let p = 0; p < 12; p++) s += v[p] * v[p];
      s = Math.sqrt(s) || 1; for (let p = 0; p < 12; p++) v[p] /= s;
      return v;
    };
    const lowS = [], chg = [];
    for (let i = 0; i < beats.length; i++) {
      const b = beats[i];
      let lv = 0; for (let m = Math.max(0, b - 2); m <= Math.min(n - 1, b + 3); m++) if (low[m] > lv) lv = low[m];
      lowS.push(lv);
      const a0 = i >= 2 ? beats[i - 2] : Math.max(0, b - 2 * Math.round(60 / (tempo * dt)));
      const a1 = i + 2 < beats.length ? beats[i + 2] : Math.min(n, b + 2 * Math.round(60 / (tempo * dt)));
      const u = chromaMean(a0, b), v = chromaMean(b, a1);
      let dot = 0; for (let p = 0; p < 12; p++) dot += u[p] * v[p];
      chg.push(1 - dot);
    }
    const z = a => { const mu = a.reduce((x, y) => x + y, 0) / a.length; const sd = Math.sqrt(a.reduce((x, y) => x + (y - mu) * (y - mu), 0) / a.length) || 1; return a.map(v => (v - mu) / sd); };
    const zl = z(lowS), zc = z(chg);
    const ph = [0, 0, 0, 0], cn = [0, 0, 0, 0];
    for (let i = 0; i < beats.length; i++) { ph[i % 4] += 0.6 * zl[i] + zc[i]; cn[i % 4]++; }
    const sc = ph.map((v, i) => cn[i] ? v / cn[i] : -Infinity);
    const order = [0, 1, 2, 3].sort((a, b) => sc[b] - sc[a]);
    downPhase = order[0];
    downConf = sc[order[0]] - sc[order[1]];
    for (let i = downPhase; i < beats.length; i += 4) downbeats.push(beats[i]);
  }

  /* summary */
  const gated = []; for (let m = 0; m < n; m++) if (X.gate[m]) gated.push(clarity[m]);
  gated.sort((a, b) => a - b);
  const medClar = gated.length ? gated[gated.length >> 1] : 0;
  const ltG = []; for (let m = 0; m < n; m++) if (X.gate[m] && clarity[m] >= RHYTHM.clarityMin) ltG.push(ltempo[m]);
  ltG.sort((a, b) => a - b);
  const q = p => ltG.length ? ltG[Math.min(ltG.length - 1, Math.floor(p * (ltG.length - 1)))] : 0;

  F._rhythm = {
    env, clarity, ltempo, plp, tempo, medClar,
    reliable: medClar >= RHYTHM.clarityMin && F.dur >= 10,
    tempoRange: [q(0.05), q(0.95)],
    beats, plpBeats, downbeats, downConf, downPhase,
    beatTimes: beats.map(frameTime), plpTimes: plpBeats.map(frameTime), downTimes: downbeats.map(frameTime),
    onsetTimes: X.onsets.map(m => m * dt)
  };
  return F._rhythm;
}

/* ===================================================================
   Events: where the sound starts and where it has decayed
   =================================================================== */
function eventsOf(F) {
  if (F._events) return F._events;
  const X = baseOf(F), n = F.nF, dt = F.dt;
  let pk = -Infinity, pm = 0;
  for (let m = 0; m < n; m++) if (X.ldb[m] > pk) { pk = X.ldb[m]; pm = m; }
  let start = -1, end30 = -1, end60 = -1;
  for (let m = 0; m < n; m++) if (X.ldb[m] >= pk - 30) { start = m; break; }
  for (let m = n - 1; m >= 0; m--) if (X.ldb[m] >= pk - 30) { end30 = m; break; }
  const fl = Math.max(pk - 60, -70);
  for (let m = n - 1; m >= 0; m--) if (X.ldb[m] > fl) { end60 = m; break; }
  F._events = {
    peakLufs: pk, peakTime: (pm + 0.5) * dt,
    start: start >= 0 ? start * dt : 0, end30: end30 >= 0 ? (end30 + 1) * dt : F.dur, end60: end60 >= 0 ? (end60 + 1) * dt : F.dur,
    onsetTimes: X.onsets.map(m => m * dt)
  };
  return F._events;
}

/* ===================================================================
   Structure: Foote novelty on a self-similarity matrix, repeated-section labels
   =================================================================== */
const STRUCT = { kMin: 4, kMax: 16, labelSim: 0.62, thrAdd: 0.05, minNov: 0.2, medFrac: 0.5, sigFloor: 0.5 };
let DCT_T = null;
function dctTable() {
  if (DCT_T) return DCT_T;
  DCT_T = new Float64Array(12 * NMEL);
  for (let c = 1; c <= 12; c++) for (let b = 0; b < NMEL; b++) DCT_T[(c - 1) * NMEL + b] = Math.cos(Math.PI * c * (b + 0.5) / NMEL) * Math.sqrt(2 / NMEL);
  return DCT_T;
}
function structureOf(F) {
  if (F._struct) return F._struct;
  const X = baseOf(F), R = rhythmOf(F);
  const n = F.nF, dt = F.dt, dur = F.dur;
  const useBeats = R.reliable && R.beats.length >= 16;
  const blk = useBeats ? 60 / R.tempo : Math.min(0.5, Math.max(0.05, dur / 160));
  const blkF = Math.max(1, Math.round(blk / dt));
  const starts = [];
  if (useBeats) {
    const b = R.beats;
    for (let m = 0; m + blkF / 2 < b[0]; m += blkF) starts.push(m);
    for (const m of b) if (!starts.length || m > starts[starts.length - 1]) starts.push(m);
    for (let m = b[b.length - 1] + blkF; m + blkF / 2 < n; m += blkF) starts.push(m);
  } else for (let m = 0; m < n; m += blkF) starts.push(m);
  starts.push(n);
  const U = starts.length - 1;
  if (U < 12) {
    const nov = new Float32Array(n);
    F._struct = { units: U, useBeats, blk, L: 0, nov, bounds: [], segs: [{ t0: 0, t1: dur, label: "A" }], tooShort: true };
    return F._struct;
  }

  /* features per unit: MFCC 1-12, chroma, level, activity */
  const D = 26, feat = new Float64Array(U * D);
  const T = dctTable();
  const mel = new Float64Array(NMEL);
  for (let u = 0; u < U; u++) {
    const a = starts[u], b = Math.max(a + 1, starts[u + 1]);
    mel.fill(0);
    const ch = new Float64Array(12);
    let lv = 0, ac = 0;
    for (let m = a; m < b; m++) {
      for (let k = 0; k < NMEL; k++) mel[k] += F.melDb[m * NMEL + k];
      for (let p = 0; p < 12; p++) ch[p] += F.chroma[m * 12 + p];
      lv += X.ldb[m]; ac += X.actFixed[m];
    }
    const cnt = b - a;
    for (let k = 0; k < NMEL; k++) mel[k] /= cnt;
    const o = u * D;
    for (let c = 0; c < 12; c++) { let s = 0; for (let k = 0; k < NMEL; k++) s += T[c * NMEL + k] * mel[k]; feat[o + c] = s; }
    let cs = 0; for (let p = 0; p < 12; p++) cs += ch[p];
    for (let p = 0; p < 12; p++) feat[o + 12 + p] = cs > 1e-12 ? Math.sqrt(ch[p] / cs) : 0;
    feat[o + 24] = lv / cnt; feat[o + 25] = ac / cnt;
  }
  const gw = [];
  for (let d = 0; d < D; d++) gw.push(d < 12 ? 1 / Math.sqrt(12) : d < 24 ? 1 / Math.sqrt(12) : 0.6 / Math.sqrt(2));
  for (let d = 0; d < D; d++) {
    let mu = 0; for (let u = 0; u < U; u++) mu += feat[u * D + d]; mu /= U;
    let sd = 0; for (let u = 0; u < U; u++) sd += (feat[u * D + d] - mu) ** 2; sd = Math.sqrt(sd / U);
    // noise floors in real units so a static sound is not stretched into fake contrast
    const floor = d < 12 ? 0.6 : d < 24 ? 0.04 : d === 24 ? 1.5 : 0.05;
    const s = gw[d] / Math.max(sd, floor);
    for (let u = 0; u < U; u++) feat[u * D + d] = (feat[u * D + d] - mu) * s;
  }
  /* median filter each feature along time (removes chord-level and bar-level changes) */
  const L = Math.max(STRUCT.kMin, Math.min(STRUCT.kMax, Math.round(U / 16)));
  {
    const mh = Math.max(1, Math.round(L * STRUCT.medFrac));
    const col = new Float64Array(U), tmp = [];
    for (let d = 0; d < D; d++) {
      for (let u = 0; u < U; u++) col[u] = feat[u * D + d];
      for (let u = 0; u < U; u++) {
        tmp.length = 0;
        for (let k = Math.max(0, u - mh); k <= Math.min(U - 1, u + mh); k++) tmp.push(col[k]);
        tmp.sort((a, b) => a - b);
        feat[u * D + d] = tmp[tmp.length >> 1];
      }
    }
  }
  /* self-similarity, Gaussian kernel on Euclidean distance */
  const S = new Float32Array(U * U);
  const d2s = [];
  for (let i = 0; i < U; i++) for (let j = i; j < U; j++) {
    let s = 0; for (let d = 0; d < D; d++) { const v = feat[i * D + d] - feat[j * D + d]; s += v * v; }
    S[i * U + j] = s; S[j * U + i] = s;
    if (j > i && (d2s.length < 40000 || ((i * 31 + j) % 7 === 0))) d2s.push(s);
  }
  d2s.sort((a, b) => a - b);
  const sig2 = Math.max(STRUCT.sigFloor, d2s.length ? d2s[d2s.length >> 1] : 1);
  for (let k = 0; k < U * U; k++) S[k] = Math.exp(-S[k] / sig2);

  /* Foote novelty: Gaussian-tapered checkerboard, edges replicated */
  const g = new Float64Array(2 * L);
  for (let a = -L; a < L; a++) g[a + L] = Math.exp(-0.5 * Math.pow((a + 0.5) / (0.5 * L), 2));
  const cl = v => v < 0 ? 0 : v >= U ? U - 1 : v;
  const nv = new Float64Array(U);
  for (let u = 1; u < U; u++) {
    let s = 0;
    for (let a = -L; a < L; a++) {
      const ia = cl(u + a), sa = a < 0 ? -1 : 1, ga = g[a + L];
      for (let b = -L; b < L; b++) s += sa * (b < 0 ? -1 : 1) * ga * g[b + L] * S[ia * U + cl(u + b)];
    }
    nv[u] = Math.max(0, s);
  }
  let ksum = 0; for (let a = 0; a < 2 * L; a++) for (let b = 0; b < 2 * L; b++) ksum += g[a] * g[b];
  for (let u = 0; u < U; u++) nv[u] = Math.min(1, nv[u] / (0.5 * ksum));

  /* peak picking: local max within ±L/2, above a running median + margin */
  const half = Math.max(2, Math.floor(L / 2));
  const bnd = [];
  for (let u = 1; u < U; u++) {
    let isMax = true;
    for (let k = Math.max(1, u - half); k <= Math.min(U - 1, u + half); k++) if (nv[k] > nv[u]) { isMax = false; break; }
    if (!isMax || nv[u] < STRUCT.minNov || u < half || U - u < half) continue;
    const w = []; for (let k = Math.max(0, u - 2 * L); k <= Math.min(U - 1, u + 2 * L); k++) w.push(nv[k]);
    w.sort((a, b) => a - b);
    if (nv[u] < w[w.length >> 1] + STRUCT.thrAdd) continue;
    if (bnd.length && u - bnd[bnd.length - 1] < half) continue;
    bnd.push(u);
  }

  /* labels: block similarity between segments, average-linkage merging */
  const segU = [0, ...bnd, U];
  const K = segU.length - 1;
  const Bm = new Float64Array(K * K);
  for (let i = 0; i < K; i++) for (let j = i; j < K; j++) {
    let s = 0, c = 0;
    for (let a = segU[i]; a < segU[i + 1]; a++) for (let b = segU[j]; b < segU[j + 1]; b++) { s += S[a * U + b]; c++; }
    Bm[i * K + j] = Bm[j * K + i] = c ? s / c : 0;
  }
  const rr = (i, j) => Bm[i * K + j] / Math.sqrt(Math.max(1e-9, Bm[i * K + i] * Bm[j * K + j]));
  let clusters = []; for (let i = 0; i < K; i++) clusters.push([i]);
  for (;;) {
    let best = -1, bi = -1, bj = -1;
    for (let a = 0; a < clusters.length; a++) for (let b = a + 1; b < clusters.length; b++) {
      let s = 0, c = 0;
      for (const i of clusters[a]) for (const j of clusters[b]) { s += rr(i, j); c++; }
      const v = s / c;
      if (v > best) { best = v; bi = a; bj = b; }
    }
    if (bi < 0 || best < STRUCT.labelSim) break;
    clusters[bi] = clusters[bi].concat(clusters[bj]);
    clusters.splice(bj, 1);
  }
  const lab = new Array(K);
  clusters.forEach((cl2, ci) => cl2.forEach(i => { lab[i] = ci; }));
  const order = []; for (let i = 0; i < K; i++) if (!order.includes(lab[i])) order.push(lab[i]);
  const letter = i => String.fromCharCode(65 + (order.indexOf(i) % 26));
  const tOf = u => u >= U ? dur : starts[u] * dt;
  const segs = [];
  for (let i = 0; i < K; i++) segs.push({ t0: tOf(segU[i]), t1: tOf(segU[i + 1]), label: letter(lab[i]), cls: order.indexOf(lab[i]) });

  /* novelty per frame */
  const tc = [], vc = [];
  for (let u = 0; u < U; u++) { tc.push(starts[u] * dt); vc.push(nv[u]); }
  const nov = interpTimes(tc, vc, n, dt);
  F._struct = { units: U, useBeats, blk, L, nov, bounds: bnd.map(u => starts[u] * dt), segs, tooShort: false };
  return F._struct;
}

/* ===================================================================
   Beat evaluation: F-measure with ±70 ms tolerance (MIREX / mir_eval)
   =================================================================== */
function beatF(est, ref, regions, tol) {
  tol = tol || 0.07;
  const inR = t => !regions || regions.some(r => t >= r[0] && t <= r[1]);
  const E = est.filter(inR), Rf = ref.filter(inR);
  if (!Rf.length) return null;
  const used = new Uint8Array(Rf.length);
  let tp = 0, j = 0;
  const errs = [];
  for (const t of E) {
    while (j < Rf.length && Rf[j] < t - tol) j++;
    let best = -1, bd = Infinity;
    for (let k = j; k < Rf.length && Rf[k] <= t + tol; k++) if (!used[k] && Math.abs(Rf[k] - t) < bd) { bd = Math.abs(Rf[k] - t); best = k; }
    if (best >= 0) { used[best] = 1; tp++; errs.push(t - Rf[best]); }
  }
  const P = E.length ? tp / E.length : 0, Rc = tp / Rf.length;
  errs.sort((a, b) => a - b);
  return { f: P + Rc > 0 ? 2 * P * Rc / (P + Rc) : 0, p: P, r: Rc, n: E.length, ref: Rf.length, medErr: errs.length ? errs[errs.length >> 1] : 0 };
}

/* ===================================================================
   Short sound effects: envelope phases, sound objects (Schaeffer-style
   tags), NMF layers, pitch and centroid motion, space, asset checks,
   spectral silhouette bands. Needs the decoded buffer; cached on F.
   =================================================================== */
const SFX = {
  yinWin: 2048, yinThr: 0.15, fmin: 50, fmax: 4000, maxPitchPts: 900,
  tonalAper: 0.25, tonalFrac: 0.5, glideSemis: 4, rangeSemis: 5, rangeOct: 1.2, glideOct: 0.8,
  impulseDur: 0.2, grainGap: 0.4, grainDb: 12, grainOct: 2.0, grainGlideOct: 0.5, hpShare: 0.25, iterMin: 3, iterDepth: 0.35, contRise: 5,
  // riseDb/bandRiseDb 8 (was 6): picked on the tuning half in 19 of 20 repeated splits by tools/tune.js --grid
  eventFloor: 40, riseDb: 8, bandRiseDb: 8, nmfK: 3, nmfIter: 150, nmfCols: 400
};
const SFX_WORDS = {
  N: { impulse: "tonal hit", sustained: "tone", iterative: "tonal flutter" },
  X: { impulse: "noise hit", sustained: "noise", iterative: "grit" },
  Y: { impulse: "chirp", sustained: "sweep", iterative: "creak" }
};

function linSlope(xs, ys) {
  const n = xs.length; if (n < 2) return 0;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxx += xs[i] * xs[i]; sxy += xs[i] * ys[i]; }
  const d = n * sxx - sx * sx;
  return d > 1e-12 ? (n * sxy - sx * sy) / d : 0;
}
function pctl(arr, q) { if (!arr.length) return 0; const t = arr.slice().sort((a, b) => a - b); return t[Math.min(t.length - 1, Math.max(0, Math.round(q * (t.length - 1))))]; }

function sfxOf(F, buf, name) {
  if (F._sfx) return F._sfx;
  const X = baseOf(F);
  const n = F.nF, dt = F.dt, sr = F.sr, H = F.H, B = NMEL;
  const L = buf.getChannelData(0), R = buf.numberOfChannels > 1 ? buf.getChannelData(1) : L;
  const len = Math.min(L.length, R.length);
  const fr = s => Math.max(1, Math.round(s / dt));

  /* ---------- envelope ---------- */
  // ~14 ms smoothing: removes pitch-period ripple of low voices (80 Hz = 12.5 ms) at the fine hop
  const kzS = movMean(F.kz, Math.max(1, Math.round(0.007 / dt)));
  const lev = new Float32Array(n), amp = new Float32Array(n);
  let pk = -Infinity, pm = 0, maxA = 0;
  for (let m = 0; m < n; m++) {
    lev[m] = Math.max(-100, -0.691 + 10 * Math.log10(kzS[m] + 1e-15));
    amp[m] = Math.sqrt(kzS[m]);
    if (lev[m] > pk) { pk = lev[m]; pm = m; }
    if (amp[m] > maxA) maxA = amp[m];
  }
  const tOf = m => (m + 0.5) * dt;
  let attackFix = dt;
  let eff = 0, tcN = 0, tcD = 0;
  for (let m = 0; m < n; m++) { if (amp[m] >= 0.4 * maxA) eff += dt; if (amp[m] >= 0.15 * maxA) { tcN += tOf(m) * amp[m]; tcD += amp[m]; } }
  const tcent = tcD > 0 ? tcN / tcD : 0;
  let t20 = null, t40 = null;
  for (let m = pm; m < n; m++) { if (t20 === null && lev[m] <= pk - 20) t20 = tOf(m) - tOf(pm); if (lev[m] <= pk - 40) { t40 = tOf(m) - tOf(pm); break; } }

  /* ---------- pitch (YIN, FFT-based difference function) ---------- */
  const f0 = new Float32Array(n), aper = new Float32Array(n).fill(1);
  {
    const W = Math.min(SFX.yinWin, 1 << Math.floor(Math.log2(Math.max(256, len))));
    const NF = 2 * W, fft = getFFT(NF);
    const tMin = Math.max(2, Math.floor(sr / SFX.fmax)), tMax = Math.min(W >> 1, Math.ceil(sr / SFX.fmin));
    const step = Math.max(1, Math.ceil(n / SFX.maxPitchPts));
    const re = new Float64Array(NF), im = new Float64Array(NF), seg = new Float64Array(W), E = new Float64Array(W + 1);
    const d = new Float64Array(tMax + 2), dn = new Float64Array(tMax + 2);
    const pts = [];
    const mono = new Float32Array(len), hp = new Float32Array(len);
    for (let i = 0; i < len; i++) mono[i] = 0.5 * (L[i] + R[i]);
    { const w0 = 2 * Math.PI * 250 / sr, al = Math.sin(w0) / (2 * 0.7071), cw = Math.cos(w0), a0 = 1 + al;
      biquadInto(mono, hp, [(1 + cw) / 2 / a0, -(1 + cw) / a0, (1 + cw) / 2 / a0, -2 * cw / a0, (1 - al) / a0]); }
    for (let m = 0; m < n; m += step) {
      if (lev[m] < pk - 45) { pts.push([m, 0, 1]); continue; }
      const c = m * H + (H >> 1), s0 = c - (W >> 1);
      let eFull = 0;
      for (let j = 0; j < W; j++) { const i = s0 + j; seg[j] = i >= 0 && i < len ? hp[i] : 0; const v = i >= 0 && i < len ? mono[i] : 0; eFull += v * v; }
      E[0] = 0; for (let j = 0; j < W; j++) E[j + 1] = E[j] + seg[j] * seg[j];
      const hpShare = eFull > 0 ? E[W] / eFull : 0;
      if (E[W] < 1e-10) { pts.push([m, 0, 1]); continue; }
      re.fill(0); im.fill(0);
      for (let j = 0; j < W; j++) re[j] = seg[j];
      fft(re, im);
      for (let k = 0; k < NF; k++) { re[k] = re[k] * re[k] + im[k] * im[k]; im[k] = 0; }
      fft(re, im);
      let run = 0;
      for (let t = 1; t <= tMax; t++) {
        const r = re[t] / NF;
        d[t] = Math.max(0, E[W - t] + (E[W] - E[t]) - 2 * r);
        run += d[t];
        dn[t] = run > 0 ? d[t] * t / run : 1;
      }
      let best = -1;
      for (let t = tMin; t <= tMax; t++) if (dn[t] < SFX.yinThr) { while (t + 1 <= tMax && dn[t + 1] < dn[t]) t++; best = t; break; }
      if (best < 0) { let mv = Infinity; for (let t = tMin; t <= tMax; t++) if (dn[t] < mv) { mv = dn[t]; best = t; } }
      let tau = best;
      if (best > tMin && best < tMax) { const y0 = dn[best - 1], y1 = dn[best], y2 = dn[best + 1], den = y0 - 2 * y1 + y2; if (den > 0) tau = best + 0.5 * (y0 - y2) / den; }
      pts.push([m, sr / tau, hpShare >= SFX.hpShare ? dn[best] : Math.max(dn[best], 0.99)]);
    }
    let j = 0;
    for (let m = 0; m < n; m++) {
      while (j < pts.length - 1 && pts[j + 1][0] <= m) j++;
      f0[m] = pts[j][1]; aper[m] = pts[j][2];
    }
  }

  /* ---------- spectral centroid (log-frequency, from mel bands) ---------- */
  const melC = b => { const ml = 2595 * Math.log10(1 + 40 / 700), mh = 2595 * Math.log10(1 + Math.min(16000, sr * 0.475) / 700); const mm = ml + (mh - ml) * (b + 1) / (B + 1); return 700 * (Math.pow(10, mm / 2595) - 1); };
  const lf = []; for (let b = 0; b < B; b++) lf.push(Math.log2(melC(b)));
  const cent = new Float32Array(n);
  for (let m = 0; m < n; m++) {
    let s = 0, w = 0;
    for (let b = 0; b < B; b++) { const e = Math.pow(10, F.melDb[m * B + b] / 10); s += e * lf[b]; w += e; }
    cent[m] = w > 0 ? s / w : 0;
  }

  /* ---------- events: SFX onsets -> drop continuations -> group grains ---------- */
  const floorDb = pk - SFX.eventFloor;
  let first = -1, last = -1;
  for (let m = 0; m < n; m++) if (lev[m] >= floorDb) { if (first < 0) first = m; last = m; }
  const endF = last >= 0 ? last + 1 : n;
  const levS = lev;
  const kzP = movMean(F.kz, 1), levP = new Float32Array(n);
  for (let m = 0; m < n; m++) levP[m] = Math.max(-100, -0.691 + 10 * Math.log10(kzP[m] + 1e-15));
  const peakAfter = m => { let v = -Infinity; for (let k = m; k < Math.min(n, m + fr(0.03)); k++) v = Math.max(v, levP[k]); return v; };
  // (a) level re-attacks: >= 6 dB within 15 ms
  const w15 = Math.max(1, fr(0.015)), cand = [];
  {
    const dl = new Float32Array(n), lo = new Int32Array(n);
    for (let m = 0; m < n; m++) { let v = Infinity, at = m; for (let k = Math.max(0, m - w15); k <= m; k++) if (levS[k] < v) { v = levS[k]; at = k; } dl[m] = levS[m] - v; lo[m] = at; }
    const wn = Math.max(1, fr(0.01));
    for (let m = 1; m < n - 1; m++) {
      if (dl[m] < SFX.riseDb || levS[m] < floorDb) continue;
      let isMax = true; for (let k = Math.max(0, m - wn); k <= Math.min(n - 1, m + wn); k++) if (dl[k] > dl[m]) { isMax = false; break; }
      if (isMax) cand.push(lo[m]);
    }
  }
  // (b) spectral-flux onsets (new layers that barely change the level)
  const lagO = Math.max(1, Math.round(DT_REF / dt));
  const bandRise = m => {
    let fm = -Infinity; for (let b = 0; b < B; b++) fm = Math.max(fm, F.melDb[Math.min(n - 1, m + fr(0.006)) * B + b]);
    let r = 0;
    for (let b = 0; b < B; b++) {
      let mx = -Infinity; for (let k = m; k < Math.min(n, m + fr(0.012)); k++) mx = Math.max(mx, F.melDb[k * B + b]);
      if (mx < fm - 40) continue;
      r = Math.max(r, mx - F.melDb[Math.max(0, m - lagO) * B + b]);
    }
    return r;
  };
  for (const m of X.onsets) if (peakAfter(m) >= floorDb && bandRise(m) >= SFX.bandRiseDb) cand.push(m);
  cand.sort((p, q) => p - q);
  let ons = [];
  for (const m of cand) { if (m < first || m >= endF - fr(0.02)) continue; if (ons.length && m - ons[ons.length - 1] < fr(0.025)) continue; ons.push(m); }
  if (first >= 0 && (!ons.length || ons[0] - first > fr(0.03))) ons.unshift(first);
  // a sound already present at the first frame starts at 0: rise detectors need past frames and would report it a window late
  else if (first >= 0 && first <= 1 && ons.length && ons[0] > first) ons[0] = first;
  // what changed at each onset: level, and the centroid of the energy that was added
  const onInfo = ons.map(m => {
    let num = 0, den = 0;
    for (let b = 0; b < B; b++) {
      let mx = -Infinity; for (let k = m; k < Math.min(n, m + fr(0.012)); k++) mx = Math.max(mx, F.melDb[k * B + b]);
      const before = F.melDb[Math.max(0, m - lagO) * B + b];
      if (mx - before < 3) continue;
      const dd = Math.pow(10, mx / 10) - Math.pow(10, before / 10);
      if (dd > 0) { num += dd * lf[b]; den += dd; }
    }
    return { m, pk: peakAfter(m), fc: den > 0 ? num / den : cent[m] };
  });
  // continuation: no dip before it and still rising -> same event (fade-ins, swells)
  for (let i = 1; i < onInfo.length;) {
    const p = onInfo[i - 1], q = onInfo[i];
    let pmax = -Infinity; for (let k = p.m; k < q.m; k++) pmax = Math.max(pmax, levP[k]);
    let dip = Infinity; for (let k = Math.max(p.m, q.m - fr(0.01)); k <= q.m; k++) dip = Math.min(dip, levS[k]);
    const fa = Math.min(n - 1, p.m + fr(0.03)), fb = Math.min(n - 1, q.m + fr(0.03));
    const pitchJump = aper[fa] <= SFX.tonalAper && aper[fb] <= SFX.tonalAper && f0[fa] > 0 && f0[fb] > 0 && Math.abs(12 * Math.log2(f0[fb] / f0[fa])) >= 1;
    const swell = q.pk >= pmax + 3;
    if (pmax - dip < 6 && q.pk >= pmax - 1 && (swell || Math.abs(q.fc - p.fc) < 0.5) && !pitchJump) onInfo.splice(i, 1); else i++;
  }
  // grains: runs of >= 3 onsets close together, with similar level and similar rising bands
  const groups = [];
  const like = (p, q) => (q.m - p.m) * dt <= SFX.grainGap && Math.abs(p.pk - q.pk) <= SFX.grainDb && Math.abs(p.fc - q.fc) <= SFX.grainOct;
  for (let i = 0; i < onInfo.length;) {
    let j = i;
    const nxt = onInfo.slice(i + 1, i + 4).map(o => o.pk);
    const strongHead = nxt.length > 0 && onInfo[i].pk - pctl(nxt, 0.5) >= 10;
    if (!strongHead) {
      const pks = [onInfo[i].pk];
      while (j + 1 < onInfo.length) {
        const q = onInfo[j + 1], med = pctl(pks, 0.5);
        if ((q.m - onInfo[j].m) * dt <= SFX.grainGap && Math.abs(q.pk - med) <= SFX.grainDb && Math.abs(q.fc - onInfo[j].fc) <= SFX.grainOct) { j++; pks.push(q.pk); } else break;
      }
    }
    const bEnd = j + 1 < onInfo.length ? onInfo[j + 1].m : endF;
    if (j - i + 1 >= SFX.iterMin) { groups.push({ a: onInfo[i].m, b: bEnd, grains: onInfo.slice(i, j + 1).map(x => x.m), chain: true }); i = j + 1; }
    else { groups.push({ a: onInfo[i].m, b: i + 1 < onInfo.length ? onInfo[i + 1].m : endF, grains: [onInfo[i].m], chain: false }); i++; }
  }
  for (let i = 0; i + 1 < groups.length;) {
    const g = groups[i], h = groups[i + 1];
    if (g.chain) {
      const lastI = onInfo.find(o => o.m === g.grains[g.grains.length - 1]), hI = onInfo.find(o => o.m === h.a);
      if (lastI && hI && like(lastI, hI)) { g.grains.push(...h.grains); g.b = h.b; groups.splice(i + 1, 1); continue; }
    }
    i++;
  }
  const segDesc = (a, b) => {
    let ep = -Infinity, epm = a;
    for (let m = a; m < b; m++) if (lev[m] > ep) { ep = lev[m]; epm = m; }
    let effE = 0; for (let m = a; m < b; m++) if (lev[m] >= ep - 12) effE += dt;
    return { ep, epm, eff: effE };
  };
  const events = [];
  for (const gp of groups) {
    const a = gp.a, b = gp.b;
    const sd = segDesc(a, b), ep = sd.ep, epm = sd.epm, effE = sd.eff;
    const tonal = []; let wAll = 0, wTon = 0;
    for (let m = a; m < b; m++) if (lev[m] >= ep - 12) { const w = amp[m]; wAll += w; if (aper[m] <= SFX.tonalAper && f0[m] >= 60) { wTon += w; tonal.push(m); } }
    for (let m = a; m < b; m++) if (lev[m] < ep - 12 && lev[m] >= ep - 25 && aper[m] <= SFX.tonalAper && f0[m] >= 60) tonal.push(m);
    tonal.sort((p, q) => p - q);
    const tonalFrac = wAll > 0 ? wTon / wAll : 0;
    // pitch: net change (first vs last third) and range, edges trimmed
    const trim = fr(0.015);
    const tt = tonal.filter(m => m >= a + trim && m < b - trim);
    let semis = 0, sRange = 0;
    if (tt.length >= 4) {
      const lp = tt.map(m => 12 * Math.log2(f0[m])), k3 = Math.max(1, Math.floor(lp.length / 3));
      semis = pctl(lp.slice(-k3), 0.5) - pctl(lp.slice(0, k3), 0.5);
      sRange = pctl(lp, 0.9) - pctl(lp, 0.1);
    }
    // brightness: net change and range over the body (within 12 dB of the event peak)
    const cb = []; for (let m = a; m < b; m++) if (lev[m] >= ep - 12) cb.push(m);
    let cOct = 0, cRange = 0;
    if (cb.length >= 3) {
      const cv = cb.map(m => cent[m]), k3 = Math.max(1, Math.floor(cv.length / 3));
      cOct = pctl(cv.slice(-k3), 0.5) - pctl(cv.slice(0, k3), 0.5);
      cRange = pctl(cv, 0.9) - pctl(cv, 0.1);
    }
    // amplitude modulation 8-40 Hz inside a sustained event
    let depth = 0;
    if (effE >= SFX.impulseDur && b - a >= fr(0.15)) {
      const s1 = movMean(amp.subarray(a, b), Math.max(1, Math.round(0.04 / dt)));
      const s2 = movMean(amp.subarray(a, b), Math.max(1, Math.round(0.008 / dt)));
      let q2 = 0, sm = 0, k = 0;
      for (let m = a; m < b; m++) if (lev[m] >= ep - 20) { const v = s2[m - a] - s1[m - a]; q2 += v * v; sm += amp[m]; k++; }
      depth = k && sm > 0 ? Math.sqrt(q2 / k) / (sm / k) : 0;
    }
    const iterative = gp.chain || depth >= SFX.iterDepth;
    const facture = iterative ? "iterative" : effE < SFX.impulseDur ? "impulse" : "sustained";
    // dynamic profile (Peeters & Deruty categories)
    const atk = (epm - a) * dt, pos = (epm - a) / Math.max(1, b - a);
    let lo = Infinity, hi = -Infinity; for (let m = a; m < b; m++) if (lev[m] >= ep - 30) { lo = Math.min(lo, lev[m]); hi = Math.max(hi, lev[m]); }
    const profile = iterative && hi - lo < 15 && pos > 0.1 && pos < 0.9 ? "stable" : hi - lo < 6 ? "stable" : atk < 0.03 ? "impulsive" : pos > 0.6 ? "ascending" : pos > 0.25 ? "ascending/descending" : "descending";
    let gTrend = 0;
    if (gp.chain && gp.grains.length >= 6) {
      const fcs = gp.grains.map(m => { const o = onInfo.find(x => x.m === m); return o ? o.fc : cent[m]; }), k3 = Math.max(1, Math.floor(fcs.length / 3));
      const rk = a => { const ix = a.map((v, i) => i).sort((p, q) => a[p] - a[q]); const r = new Array(a.length); ix.forEach((v, i) => { r[v] = i; }); return r; };
      const rho = pearson(Float32Array.from(rk(fcs)), Float32Array.from(fcs.map((_, i) => i)), null);
      gTrend = Math.abs(rho) >= 0.6 ? pctl(fcs.slice(-k3), 0.5) - pctl(fcs.slice(0, k3), 0.5) : 0;
    }
    let mass, dir = "";
    if (iterative && gp.chain && Math.abs(gTrend) >= SFX.grainGlideOct) { mass = "Y"; dir = gTrend > 0 ? "up" : "down"; }
    else if (tonalFrac >= SFX.tonalFrac) {
      if (!iterative && Math.abs(semis) >= SFX.glideSemis) { mass = "Y"; dir = semis > 0 ? "up" : "down"; }
      else if (facture === "sustained" && sRange >= SFX.rangeSemis) { mass = "Y"; dir = "up and down"; }
      else mass = "N";
    } else if (facture === "sustained" && profile !== "impulsive" && profile !== "descending" && (Math.abs(cOct) >= SFX.glideOct || cRange >= (effE >= 0.4 ? 0.6 : 0.8))) {
      mass = "Y"; dir = Math.abs(cOct) >= 0.4 ? (cOct > 0 ? "up" : "down") : "up and down";
    } else mass = "X";
    const tag = mass + (facture === "impulse" ? "′" : facture === "iterative" ? "″" : "");
    let endA = b; for (let m = epm; m < b; m++) if (lev[m] < ep - 35) { endA = m; break; }
    events.push({
      t0: a * dt, t1: b * dt, tEnd: endA * dt, peakT: tOf(epm), peakDb: ep, eff: effE, tag, mass, facture, dir, profile,
      word: SFX_WORDS[mass][facture] + (dir ? " " + dir : ""),
      grains: gp.grains.map(m => m * dt), tonalFrac, semis, sRange, cOct, cRange, depth,
      f0: tt.length ? Math.pow(2, pctl(tt.map(m => Math.log2(f0[m])), 0.5)) : 0
    });
  }
  // attack of the loudest event (simplified Timbre Toolbox: 20% -> 90% of peak amplitude)
  {
    const ev = events.find(e => e.t0 <= tOf(pm) && e.t1 >= tOf(pm) - dt) || events[0];
    const a = ev ? Math.round(ev.t0 / dt) : 0;
    let x20 = a, x90 = pm;
    for (let m = a; m <= pm; m++) if (amp[m] >= 0.2 * maxA) { x20 = m; break; }
    for (let m = x20; m <= pm; m++) if (amp[m] >= 0.9 * maxA) { x90 = m; break; }
    attackFix = Math.max(dt, (x90 - x20) * dt);
  }

  /* ---------- envelope phases per frame: 0 silent, 1 attack, 2 body, 3 tail ---------- */
  const phase = new Uint8Array(n);
  for (let m = 0; m < n; m++) phase[m] = lev[m] < pk - 45 ? 0 : lev[m] >= pk - 12 ? 2 : 3;
  for (const e of events) {
    const a = Math.round(e.t0 / dt), p = Math.round((e.peakT) / dt - 0.5);
    for (let m = a; m <= p && m < n; m++) if (phase[m]) phase[m] = 1;
  }

  /* ---------- layers: KL-NMF on mel magnitudes, 3 components, band-split init ---------- */
  const layers = [];
  const nmfRaw = { W: [], H: [], times: [] };
  {
    const K = SFX.nmfK, g = Math.max(1, Math.ceil(n / SFX.nmfCols)), T = Math.ceil(n / g);
    const V = new Float64Array(B * T);
    for (let t = 0; t < T; t++) for (let b = 0; b < B; b++) {
      let s = 0, k = 0;
      for (let m = t * g; m < Math.min(n, (t + 1) * g); m++) { s += Math.pow(10, F.melDb[m * B + b] / 10); k++; }
      V[b * T + t] = Math.sqrt(k ? s / k : 0) + 1e-9;
    }
    const W = new Float64Array(B * K), Hh = new Float64Array(K * T);
    const ctr = [B * 0.12, B * 0.45, B * 0.8], sig = B * 0.14;
    for (let k = 0; k < K; k++) {
      let sw = 0;
      for (let b = 0; b < B; b++) { const v = Math.exp(-0.5 * Math.pow((b - ctr[k]) / sig, 2)) + 0.02; W[b * K + k] = v; sw += v; }
      for (let b = 0; b < B; b++) W[b * K + k] /= sw;
      for (let t = 0; t < T; t++) { let s = 0; for (let b = 0; b < B; b++) s += W[b * K + k] * V[b * T + t]; Hh[k * T + t] = s + 1e-9; }
    }
    const WH = new Float64Array(B * T), Q = new Float64Array(B * T);
    const recon = () => { for (let b = 0; b < B; b++) for (let t = 0; t < T; t++) { let s = 0; for (let k = 0; k < K; k++) s += W[b * K + k] * Hh[k * T + t]; WH[b * T + t] = s + 1e-12; Q[b * T + t] = V[b * T + t] / WH[b * T + t]; } };
    for (let it = 0; it < SFX.nmfIter; it++) {
      recon();
      for (let k = 0; k < K; k++) {
        let ws = 0; for (let b = 0; b < B; b++) ws += W[b * K + k];
        for (let t = 0; t < T; t++) { let s = 0; for (let b = 0; b < B; b++) s += W[b * K + k] * Q[b * T + t]; Hh[k * T + t] *= s / (ws + 1e-12); }
      }
      recon();
      for (let k = 0; k < K; k++) {
        let hs = 0; for (let t = 0; t < T; t++) hs += Hh[k * T + t];
        for (let b = 0; b < B; b++) { let s = 0; for (let t = 0; t < T; t++) s += Hh[k * T + t] * Q[b * T + t]; W[b * K + k] *= s / (hs + 1e-12); }
      }
      for (let k = 0; k < K; k++) {
        let sw = 0; for (let b = 0; b < B; b++) sw += W[b * K + k];
        if (sw > 0) { for (let b = 0; b < B; b++) W[b * K + k] /= sw; for (let t = 0; t < T; t++) Hh[k * T + t] *= sw; }
      }
    }
    let tot = 0; const sums = [];
    for (let k = 0; k < K; k++) { let s = 0; for (let t = 0; t < T; t++) s += Hh[k * T + t]; sums.push(s); tot += s; }
    let hmax = 0; for (let i = 0; i < K * T; i++) if (Hh[i] > hmax) hmax = Hh[i];
    for (let k = 0; k < K; k++) {
      let c = 0; for (let b = 0; b < B; b++) c += W[b * K + k] * lf[b];
      const hz = Math.pow(2, c);
      const actT = new Float32Array(T); let km = 0;
      for (let t = 0; t < T; t++) { actT[t] = Hh[k * T + t]; if (actT[t] > km) km = actT[t]; }
      const times = []; for (let t = 0; t < T; t++) times.push(Math.min(F.dur, (t * g + g / 2) * dt));
      const act = interpTimes(times, Array.from(actT, v => v > 0 ? Math.max(0, 1 + 20 * Math.log10(v / hmax) / 40) : 0), n, dt);
      let s0 = null, s1 = null;
      for (let t = 0; t < T; t++) if (actT[t] >= km * 0.1) { if (s0 === null) s0 = times[t]; s1 = times[t]; }
      const nm = hz < 150 ? "sub" : hz < 400 ? "low" : hz < 1500 ? "mid" : hz < 5000 ? "high" : "air";
      layers.push({ hz, share: tot > 0 ? sums[k] / tot : 0, act, span: [s0 || 0, s1 || 0], name: nm, spec: Array.from({ length: B }, (_, b) => W[b * K + k]), comps: [k] });
      nmfRaw.W.push(Float64Array.from({ length: B }, (_, b) => W[b * K + k])); nmfRaw.H.push(actT); nmfRaw.times = times;
    }
    layers.sort((p, q) => p.hz - q.hz);
    for (let i = 0; i + 1 < layers.length;) {
      const r = pearson(layers[i].act, layers[i + 1].act, null);
      if (r > 0.8 || (r > 0.6 && layers[i + 1].hz / layers[i].hz < 1.5)) {
        const p = layers[i], q = layers[i + 1], sh = p.share + q.share;
        const act = new Float32Array(n); for (let m = 0; m < n; m++) act[m] = Math.max(p.act[m], q.act[m]);
        const hz = Math.pow(2, (p.share * Math.log2(p.hz) + q.share * Math.log2(q.hz)) / Math.max(1e-9, sh));
        layers.splice(i, 2, { hz, share: sh, act, span: [Math.min(p.span[0], q.span[0]), Math.max(p.span[1], q.span[1])], name: hz < 150 ? "sub" : hz < 400 ? "low" : hz < 1500 ? "mid" : hz < 5000 ? "high" : "air", spec: p.spec.map((v, b) => (v * p.share + q.spec[b] * q.share) / Math.max(1e-9, sh)), comps: p.comps.concat(q.comps) });
      } else i++;
    }
    const keep = layers.filter(l => l.share >= 0.05);
    // components of dropped layers go to the nearest kept layer, so a remix keeps all energy
    if (keep.length) for (const l of layers) if (!keep.includes(l)) {
      let best = keep[0]; for (const q of keep) if (Math.abs(Math.log2(q.hz / l.hz)) < Math.abs(Math.log2(best.hz / l.hz))) best = q;
      best.comps = best.comps.concat(l.comps);
    }
    layers.length = 0; (keep.length ? keep : []).forEach(l => layers.push(l));
  }

  /* ---------- space: pan and width per frame ---------- */
  const stereo = buf.numberOfChannels > 1;
  const pan = new Float32Array(n), width = new Float32Array(n);
  let identical = stereo;
  {
    const eL = new Float64Array(n), eR = new Float64Array(n), eX = new Float64Array(n);
    for (let m = 0; m < n; m++) {
      const s0 = m * H, s1 = Math.min(len, s0 + H);
      let a = 0, b = 0, c = 0;
      for (let i = s0; i < s1; i++) { a += L[i] * L[i]; b += R[i] * R[i]; c += L[i] * R[i]; if (identical && Math.abs(L[i] - R[i]) > 1e-5) identical = false; }
      eL[m] = a; eR[m] = b; eX[m] = c;
    }
    const h = Math.max(1, Math.round(0.01 / dt));
    const sL = movMean(Float32Array.from(eL), h), sR = movMean(Float32Array.from(eR), h), sX = movMean(Float32Array.from(eX), h);
    for (let m = 0; m < n; m++) {
      const t = sL[m] + sR[m];
      pan[m] = t > 1e-12 ? (sR[m] - sL[m]) / t : 0;
      const cr = sL[m] > 1e-12 && sR[m] > 1e-12 ? sX[m] / Math.sqrt(sL[m] * sR[m]) : 1;
      width[m] = Math.max(0, Math.min(2, 1 - cr));
    }
  }

  /* ---------- asset checks ---------- */
  const issues = [], marks = [];
  let pkS = 0; for (let i = 0; i < len; i++) { const v = Math.max(Math.abs(L[i]), Math.abs(R[i])); if (v > pkS) pkS = v; }
  const pkDb = 20 * Math.log10(pkS + 1e-12);
  const chans = stereo ? [L, R] : [L];
  // clipping runs
  let clipN = 0; const clipT = [];
  for (const ch of chans) {
    let run = 0;
    for (let i = 0; i < len; i++) {
      if (Math.abs(ch[i]) >= 0.9999) run++;
      else { if (run >= 3) { clipN += run; const t = (i - run) / sr; if (!clipT.length || t - clipT[clipT.length - 1] > 0.01) clipT.push(t); } run = 0; }
    }
  }
  if (clipN) { issues.push({ sev: "bad", text: "Clipped: " + clipN + " samples in " + clipT.length + (clipT.length === 1 ? " place" : " places") }); clipT.forEach(t => marks.push({ t, kind: "clip" })); }
  // DC offset
  let dc = 0;
  for (const ch of chans) { let s = 0; for (let i = 0; i < len; i++) s += ch[i]; dc = Math.max(dc, Math.abs(s / len)); }
  if (dc > 0.005) issues.push({ sev: "warn", text: "DC offset " + (20 * Math.log10(dc)).toFixed(0) + " dBFS" });
  // leading and trailing silence (below -60 dBFS)
  let li = len, ti = -1;
  for (let i = 0; i < len; i++) if (Math.max(Math.abs(L[i] - dc), Math.abs(R[i] - dc)) > 0.001) { li = i; break; }
  for (let i = len - 1; i >= 0; i--) if (Math.max(Math.abs(L[i] - dc), Math.abs(R[i] - dc)) > 0.001) { ti = i; break; }
  const lead = li / sr, trail = ti >= 0 ? (len - 1 - ti) / sr : F.dur;
  if (lead > 0.02) issues.push({ sev: "warn", text: "Leading silence " + (lead * 1000).toFixed(0) + " ms" });
  if (trail > 0.1) issues.push({ sev: "info", text: "Trailing silence " + (trail * 1000).toFixed(0) + " ms" });
  // abrupt start and end (a one-shot that starts on a non-zero sample clicks; a cut tail clicks)
  const isLoop = !!(name && /loop|(^|[_\- ])lp([_\- .]|$)/i.test(name));
  const rmsAt = (a, b) => { let s = 0, k = 0; for (let i = Math.max(0, a); i < Math.min(len, b); i++) { const v = 0.5 * (L[i] + R[i]) - dc; s += v * v; k++; } return Math.sqrt(k ? s / k : 0); };
  const endRms = rmsAt(len - Math.round(0.005 * sr), len);
  const abruptStart = !isLoop && Math.max(Math.abs(L[0]), Math.abs(R[0])) > 0.003;
  const abruptEnd = !isLoop && endRms > pkS * Math.pow(10, -35 / 20);
  if (abruptStart) { issues.push({ sev: "warn", text: "First sample is not zero (click risk)" }); marks.push({ t: 0, kind: "start" }); }
  if (abruptEnd) { issues.push({ sev: "warn", text: "Ends abruptly: tail cut at " + (20 * Math.log10(endRms / (pkS + 1e-12))).toFixed(0) + " dB below peak" }); marks.push({ t: F.dur, kind: "end" }); }
  if (pkDb > -0.1 && !clipN) issues.push({ sev: "info", text: "Peak " + pkDb.toFixed(1) + " dBFS (no headroom)" });
  if (identical) issues.push({ sev: "info", text: "Stereo file with identical channels: could be mono" });
  // loop seam, only for files named as loops
  let seam = null;
  if (isLoop) {
    const jump = Math.max(Math.abs(L[len - 1] - L[0]), Math.abs(R[len - 1] - R[0])) / (pkS + 1e-12);
    // compare the spectra either side of the wrap with the file's own window-to-window change
    // (skip edge frames that are half zero-padded)
    const k = Math.max(1, fr(0.05)), e = Math.ceil((F.N / 2) / F.H);
    const meanWin = (m0) => { const v = new Float64Array(B); for (let m = m0; m < m0 + k; m++) for (let b = 0; b < B; b++) v[b] += F.melDb[Math.min(n - 1, Math.max(0, m)) * B + b] / k; return v; };
    const dist = (p, q) => { let s2 = 0; for (let b = 0; b < B; b++) s2 += Math.abs(p[b] - q[b]); return s2 / B; };
    const sd = n > 2 * (e + k) + 2 ? dist(meanWin(n - e - k), meanWin(e)) : 0;
    const internal = [];
    for (let m = e; m + 2 * k < n - e; m += k) internal.push(dist(meanWin(m), meanWin(m + k)));
    const ref = Math.max(2, internal.length ? pctl(internal, 0.9) : 2);
    seam = { jump, specDiff: sd, ref, ok: jump < 0.05 && sd <= ref };
    issues.push(seam.ok ? { sev: "ok", text: "Loop wraps cleanly" } : { sev: "warn", text: "Loop seam: " + (jump >= 0.05 ? "sample jump " + (jump * 100).toFixed(0) + "% of peak" : "change at the wrap " + sd.toFixed(1) + " dB, file's typical " + ref.toFixed(1) + " dB") });
  }

  /* ---------- silhouette bands: 6 groups of Bark-pair loudness shares ---------- */
  const bands6 = [];
  for (let k = 0; k < 6; k++) bands6.push(new Float32Array(n));
  for (let m = 0; m < n; m++) for (let k = 0; k < 6; k++) bands6[k][m] = X.share[m * NCOARSE + 2 * k] + X.share[m * NCOARSE + 2 * k + 1];

  F._sfx = {
    lev, pk, peakT: tOf(pm), attack: attackFix, eff, tcent, t20, t40, phase, events, f0, aper, cent, layers,
    pan, width, stereo, identical, issues, marks, lead, trail, pkDb, dc, seam, bands6, nmfRaw, melCenters: Array.from({ length: B }, (_, b) => melC(b)), sound: [first >= 0 ? first * dt : 0, endF * dt]
  };
  return F._sfx;
}

/* ===================================================================
   Asset-class guess
   =================================================================== */
function guessClass(F) {
  if (F.dur < 5) return "sfx";
  const cur = computeCurves(F, "rhythmic", { scale: "fixed" });
  const g = cur.gate;
  const ed = quantileMasked(cur.raw.ED, g, 0.5);
  const ton = quantileMasked(cur.raw.tonal, g, 0.5);
  if (ed >= 1.5) return "rhythmic";
  if (ton >= 0.5) return "orchestral";
  return "ambience";
}

/* ===================================================================
   Calibration (roughness 1 asper, fluctuation 1 vacil, loudness 1 sone)
   =================================================================== */
const calCache = {};
function synthBuf(sr, len, fn) {
  const d = new Float32Array(len);
  for (let i = 0; i < len; i++) d[i] = fn(i / sr);
  return { sampleRate: sr, length: len, numberOfChannels: 1, duration: len / sr, getChannelData: () => d };
}
async function calibrate(sr) {
  if (calCache[sr]) return calCache[sr];
  const midStat = (a, f) => { const s = Math.floor(a.length * 0.3), e = Math.floor(a.length * 0.7); let t = 0; for (let i = s; i < e; i++) t += a[i]; return t / Math.max(1, e - s); };
  // 1 sone: 1 kHz tone at 40 dB SPL (full-scale sine = 100 dB)
  const fN = await extractFeatures(synthBuf(sr, Math.round(sr * 1), t => 0.001 * Math.sin(2 * Math.PI * 1000 * t)), null, { coarse: true });
  fN.cal = { kN: 1, kR: 1, kF: 1 };
  const cN = computeCurves(fN, "sfx", {});
  const kN = 1 / Math.max(1e-6, midStat(cN.raw.Nsone));
  // roughness: 1 kHz, 60 dB, 100% AM at 70 Hz
  const fR = await extractFeatures(synthBuf(sr, Math.round(sr * 1.5), t => 0.01 * (1 + Math.cos(2 * Math.PI * 70 * t)) / 1.0 * Math.sin(2 * Math.PI * 1000 * t)), null, { coarse: true });
  fR.cal = { kN, kR: 1, kF: 1 };
  const cR = computeCurves(fR, "sfx", {});
  const kR = 1 / Math.max(1e-6, midStat(cR.raw.Rr));
  // fluctuation: 1 kHz, 60 dB, 100% AM at 4 Hz
  const fF = await extractFeatures(synthBuf(sr, Math.round(sr * 4), t => 0.01 * (1 + Math.cos(2 * Math.PI * 4 * t)) * Math.sin(2 * Math.PI * 1000 * t)), null, { coarse: true });
  fF.cal = { kN, kR, kF: 1 };
  const cF = computeCurves(fF, "rhythmic", {});
  const kF = 1 / Math.max(1e-6, midStat(cF.raw.Fl));
  calCache[sr] = { kN, kR, kF };
  return calCache[sr];
}

async function analyse(buf, onProgress) {
  const cal = await calibrate(buf.sampleRate);
  const F = await extractFeatures(buf, onProgress);
  F.cal = cal;
  F.guess = guessClass(F);
  return F;
}

/* ===================================================================
   Column pooling and diagnostics
   =================================================================== */
function pool(arr, n, cols, mode, dt, dur) {
  const o = new Float32Array(cols);
  const fpc = n / cols;
  for (let c = 0; c < cols; c++) {
    const a = c * fpc, b = (c + 1) * fpc;
    let s = Math.floor(a), e = Math.ceil(b);
    if (e - s <= 1) {
      const x = Math.min(n - 1, Math.max(0, (a + b) / 2 - 0.5));
      const i = Math.floor(x), f = x - i;
      o[c] = arr[i] * (1 - f) + arr[Math.min(n - 1, i + 1)] * f;
      continue;
    }
    e = Math.min(e, n);
    if (mode === "max") { let v = -Infinity; for (let i = s; i < e; i++) if (arr[i] > v) v = arr[i]; o[c] = v; }
    else { let v = 0; for (let i = s; i < e; i++) v += arr[i]; o[c] = v / (e - s); }
  }
  return o;
}
function pearson(a, b, mask) {
  let n = 0, sa = 0, sb = 0;
  for (let i = 0; i < a.length; i++) if (!mask || mask[i]) { n++; sa += a[i]; sb += b[i]; }
  if (n < 5) return NaN;
  const ma = sa / n, mb = sb / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < a.length; i++) if (!mask || mask[i]) { const x = a[i] - ma, y = b[i] - mb; num += x * y; da += x * x; db += y * y; }
  return da > 1e-12 && db > 1e-12 ? num / Math.sqrt(da * db) : NaN;
}
function diffCorr(a, b, mask) {
  const n = a.length - 1;
  const da = new Float32Array(n), db = new Float32Array(n), mk = new Uint8Array(n);
  for (let i = 0; i < n; i++) { da[i] = a[i + 1] - a[i]; db[i] = b[i + 1] - b[i]; mk[i] = mask ? (mask[i] && mask[i + 1] ? 1 : 0) : 1; }
  return pearson(da, db, mk);
}
function disagreements(curve, ref, mask, k, minSepFrac) {
  const n = curve.length;
  const z = (a) => {
    let s = 0, s2 = 0, c = 0;
    for (let i = 0; i < n; i++) if (mask[i]) { s += a[i]; s2 += a[i] * a[i]; c++; }
    const mu = c ? s / c : 0, sd = c ? Math.sqrt(Math.max(1e-12, s2 / c - mu * mu)) : 1;
    return Array.from(a, v => (v - mu) / sd);
  };
  const za = z(curve), zb = z(ref);
  const d = za.map((v, i) => mask[i] ? v - zb[i] : -Infinity);
  const idx = d.map((v, i) => i).filter(i => isFinite(d[i]) && d[i] > 0.75).sort((x, y) => d[y] - d[x]);
  const sep = Math.max(1, Math.round(n * minSepFrac));
  const out = [];
  for (const i of idx) { if (out.every(j => Math.abs(j - i) >= sep)) out.push(i); if (out.length >= k) break; }
  return out.sort((x, y) => x - y).map(i => ({ i, d: d[i] }));
}

// re-derive everything downstream of the features after a parameter change (calibration)
function resetDerived(F) { F.melDb = F.melRaw ? applyMelFloor(F.melRaw, F.melDb) : F.melDb; delete F._base; delete F._rhythm; delete F._struct; delete F._events; delete F._sfx; delete F._desc; F.guess = guessClass(F); return F; }
const api = { FEAT, ONSET, applyMelFloor, resetDerived, extractFeatures, computeCurves, analyse, calibrate, guessClass, pool, pearson, diffCorr, disagreements, CLASSES, REF, quantileMasked, kFilterCoeffs, rhythmOf, eventsOf, structureOf, beatF, RHYTHM, STRUCT, sfxOf, SFX, getFFT, hannP, movMean, interpTimes, biquadInto, NMEL };
if (typeof module !== "undefined" && module.exports) module.exports = api;
root.WL = api;
})(__ph);

// ---- src/descriptors.js
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Daniel Sandner
// Part of Playhead, https://github.com/eigensoundlab/playhead
//
// Cross-field descriptors: measures borrowed from machine diagnostics, seismology, ecoacoustics,
// voice science, optimal transport, information dynamics, recurrence analysis and impact acoustics.
// Most have an analytic reference value (white noise, a steady tone, a periodic train, time reversal),
// which the test suite checks instead of thresholds tuned on test signals.
//
//   PHD.describe(F, buf)  ->  { curves (one value per analysis frame, NaN where undefined), events, decays, summary }
//
// Depends on analysis.js (WL) for the FFT and the shared features F.
(function (root) {
"use strict";
const WL = root.WL || (typeof require !== "undefined" ? require("./analysis.js") : null);

const DESC = {
  kurtWin: 0.5, kurtWinShort: 0.25, kurtStep: 0.05,
  kurtBands: [[100, 500], [500, 1000], [1000, 2000], [2000, 4000], [4000, 8000], [8000, 16000]],
  envBands: [[50, 300], [300, 1500], [1500, 5000], [5000, 16000]],
  repWin: 6, repStep: 0.5, repMin: 0.5, repMax: 150, repProm: 10,
  staS: 0.010, staL: 0.5, trigger: 3, detrigger: 1.5, eventFloorDb: 50,
  velSmooth: 0.3, velGamma: 0.5, velRange: 50,
  cppFmin: 60, cppFmax: 1000,
  arrowLags: [0.005, 0.02, 0.08], arrowWin: 1.0,
  symRate: 20, symK: 12, symMergeDist: 10, forget: 0.995, alpha0: 0.5,
  recRate: 10, recMax: 1500, recTheiler: 1.0, recEpsLo: 1.5, recEpsHi: 3, recBlock: 32, recLmin: 3, symMerge: 3.0, repDepthDb: 2,
  decayMin: 0.12, materialEdges: [1.3, 2.38]
};

/* ---------------- helpers ---------------- */
const LOG10E = Math.log10(Math.E);
function nextPow2(v) { let p = 1; while (p < v) p <<= 1; return p; }
function monoOf(buf) {
  const n = buf.length, ch = buf.numberOfChannels, o = new Float32Array(n);
  for (let c = 0; c < ch; c++) { const d = buf.getChannelData(c); for (let i = 0; i < n; i++) o[i] += d[i] / ch; }
  return o;
}
function quant(arr, p) {
  const s = []; for (const v of arr) if (isFinite(v)) s.push(v);
  if (!s.length) return NaN;
  s.sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.max(0, Math.floor(p * (s.length - 1))))];
}
function meanF(arr) { let s = 0, k = 0; for (const v of arr) if (isFinite(v)) { s += v; k++; } return k ? s / k : NaN; }
function rng(seed) { let a = seed >>> 0; return () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
// values at arbitrary times -> one value per analysis frame (linear; NaN stays NaN)
// many short values (one per short frame) -> analysis frames, keeping the largest in each frame
function toFramesMax(vals, times, F) {
  const o = new Float32Array(F.nF).fill(NaN);
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i]; if (!isFinite(v)) continue;
    const m = Math.min(F.nF - 1, Math.max(0, Math.floor(times[i] / F.dt)));
    if (!(o[m] >= v)) o[m] = v;
  }
  return o;
}
function toFrames(vals, times, F) {
  const o = new Float32Array(F.nF).fill(NaN), n = vals.length;
  if (!n) return o;
  let j = 0;
  for (let m = 0; m < F.nF; m++) {
    const t = (m + 0.5) * F.dt;
    while (j < n - 2 && times[j + 1] < t) j++;
    if (n === 1 || t <= times[0]) { o[m] = t >= times[0] - 1.5 * (times[1] - times[0] || 1) ? vals[0] : NaN; continue; }
    if (t >= times[n - 1]) { o[m] = t - times[n - 1] <= 1.5 * (times[n - 1] - times[n - 2]) ? vals[n - 1] : NaN; continue; }
    const a = vals[j], b = vals[j + 1], u = (t - times[j]) / (times[j + 1] - times[j]);
    o[m] = isFinite(a) && isFinite(b) ? a + (b - a) * u : (u < 0.5 ? a : b);
  }
  return o;
}
function smoothNaN(a, half) {
  const n = a.length, o = new Float32Array(n);
  for (let i = 0; i < n; i++) { if (!isFinite(a[i])) { o[i] = NaN; continue; } let s = 0, k = 0; for (let j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) if (isFinite(a[j])) { s += a[j]; k++; } o[i] = s / k; }
  return o;
}
// per-frame sound gate on the shared features: K-weighted level above −70 LUFS and within 45 dB of the loud part
function frameGate(F) {
  const L = new Float32Array(F.nF);
  for (let m = 0; m < F.nF; m++) L[m] = -0.691 + 10 * Math.log10(F.kz[m] + 1e-20);
  const ref = quant(L, 0.95), thr = Math.max(-70, ref - 45), g = new Uint8Array(F.nF);
  for (let m = 0; m < F.nF; m++) g[m] = L[m] > thr ? 1 : 0;
  return g;
}
function gateShare(gate, F, t0, t1) { const a = Math.max(0, Math.floor(t0 / F.dt)), b = Math.min(F.nF, Math.max(a + 1, Math.ceil(t1 / F.dt))); let s = 0; for (let m = a; m < b; m++) s += gate[m]; return s / (b - a); }
function melCentre(F, b) { const B = 40, ml = 2595 * Math.log10(1 + 40 / 700), mh = 2595 * Math.log10(1 + Math.min(16000, F.sr * 0.475) / 700); const mm = ml + (mh - ml) * (b + 1) / (B + 1); return 700 * (Math.pow(10, mm / 2595) - 1); }
/** NaN-aware pooling of a frame curve into display columns. mode: "mean" | "max" | "absmax" */
function poolNaN(arr, cols, mode) {
  const n = arr.length, o = new Float32Array(cols).fill(NaN);
  for (let c = 0; c < cols; c++) {
    const a = Math.floor(c * n / cols), b = Math.max(a + 1, Math.floor((c + 1) * n / cols));
    let s = 0, k = 0, best = NaN;
    for (let m = a; m < b && m < n; m++) { const v = arr[m]; if (!isFinite(v)) continue; s += v; k++; if (!isFinite(best) || (mode === "absmax" ? Math.abs(v) > Math.abs(best) : v > best)) best = v; }
    if (k) o[c] = mode === "mean" ? s / k : best;
  }
  return o;
}

/* ---------------- 1. short STFT: spectral kurtosis (machine diagnostics) and band envelopes ---------------- */
// Spectral kurtosis K(f) = <|X|^4>/<|X|^2>^2 - 2 over M frames: 0 for Gaussian noise (sd 2/sqrt(M)), -1 for a steady tone,
// positive for impulsive or intermittent sound. Computed per bin from block sums, then power-weighted per band.
function shortStft(x, sr, dur) {
  const N = Math.max(128, nextPow2(Math.round(256 * sr / 44100))), H = N >> 1, half = N >> 1, fft = WL.getFFT(N), w = WL.hannP(N);
  const T = Math.max(1, Math.floor((x.length - N) / H) + 1), rate = sr / H, binHz = sr / N;
  const blkF = Math.max(1, Math.round(DESC.kurtStep * rate)), nBlk = Math.ceil(T / blkF);
  const S1 = new Float64Array(nBlk * (half + 1)), S2 = new Float64Array(nBlk * (half + 1)), cnt = new Uint16Array(nBlk);
  const env = DESC.envBands.map(() => new Float32Array(T)), bandOf = new Int8Array(half + 1).fill(-1), pw = new Float32Array(T);
  for (let k = 1; k <= half; k++) { const f = k * binHz; DESC.envBands.forEach(([lo, hi], b) => { if (f >= lo && f < hi) bandOf[k] = b; }); }
  const re = new Float64Array(N), im = new Float64Array(N);
  const acc = (t, k, p) => { const bi = Math.floor(t / blkF), o = bi * (half + 1); S1[o + k] += p; S2[o + k] += p * p; pw[t] += p; if (bandOf[k] >= 0) env[bandOf[k]][t] += p; };
  // two real frames per complex FFT: z = a + i b, A[k] = (Z[k] + conj Z[N-k]) / 2, B[k] = (Z[k] - conj Z[N-k]) / 2i
  for (let t = 0; t < T; t += 2) {
    const tb = t + 1 < T ? t + 1 : -1, sa = t * H, sb = tb >= 0 ? tb * H : 0;
    for (let i = 0; i < N; i++) { re[i] = x[sa + i] * w[i]; im[i] = tb >= 0 ? x[sb + i] * w[i] : 0; }
    fft(re, im);
    cnt[Math.floor(t / blkF)]++; if (tb >= 0) cnt[Math.floor(tb / blkF)]++;
    for (let k = 1; k <= half; k++) {
      const m = (N - k) & (N - 1), zr = re[k], zi = im[k], yr = re[m], yi = im[m];
      const ar = (zr + yr) / 2, ai = (zi - yi) / 2; acc(t, k, ar * ar + ai * ai);
      if (tb >= 0) { const br = (zi + yi) / 2, bim = (yr - zr) / 2; acc(tb, k, br * br + bim * bim); }
    }
  }
  return { N, H, half, T, rate, binHz, blkF, nBlk, S1, S2, cnt, env, pw };
}
function kurtosisOf(st, F, gate, dur) {
  const { half, binHz, blkF, nBlk, S1, S2, cnt, rate, pw, T, N, H } = st;
  const win = Math.max(1, Math.round((dur <= 6 ? DESC.kurtWinShort : DESC.kurtWin) * rate / blkF));
  const bands = DESC.kurtBands.map(([lo, hi]) => [Math.max(1, Math.ceil(lo / binHz)), Math.min(half, Math.floor(hi / binHz))]);
  const times = [], kmax = [], khz = [], kall = [];
  // where the impulses are: each window's kurtosis given to its frames in proportion to their power, so a curve drawn
  // from it rises at an impulse, not half a window before it (the window values stay the statistic for summaries)
  const loc = new Float32Array(T).fill(NaN);
  const a1 = new Float64Array(half + 1), a2 = new Float64Array(half + 1);
  let maxPow = 0; for (let i = 0; i < S1.length; i++) if (S1[i] > maxPow) maxPow = S1[i];
  const bandK = (A1, A2, M) => {
    const out = [];
    let tot = 0; for (let k = 1; k <= half; k++) tot += A1[k];
    for (const [k0, k1] of bands) {
      let s = 0, wsum = 0, pw = 0;
      for (let k = k0; k <= k1; k++) { const m1 = A1[k] / M; if (m1 <= 0) continue; const K = (A2[k] / M) / (m1 * m1) - 2; s += K * A1[k]; wsum += A1[k]; pw += A1[k]; }
      out.push({ K: wsum > 0 ? s / wsum : NaN, share: tot > 0 ? pw / tot : 0 });
    }
    let sAll = 0, wAll = 0; for (let k = 1; k <= half; k++) { const m1 = A1[k] / M; if (m1 <= 0) continue; sAll += ((A2[k] / M) / (m1 * m1) - 2) * A1[k]; wAll += A1[k]; }
    return { bands: out, all: wAll > 0 ? sAll / wAll : NaN };
  };
  for (let b0 = 0; b0 + 1 <= nBlk; b0++) {
    const b1 = Math.min(nBlk, b0 + win); if (b1 - b0 < Math.min(win, nBlk)) break;
    a1.fill(0); a2.fill(0); let M = 0;
    for (let b = b0; b < b1; b++) { const o = b * (half + 1); M += cnt[b]; for (let k = 1; k <= half; k++) { a1[k] += S1[o + k]; a2[k] += S2[o + k]; } }
    const tc = ((b0 + b1) / 2) * blkF / rate, open = gateShare(gate, F, b0 * blkF / rate, b1 * blkF / rate) >= 0.1;
    let tot = 0; for (let k = 1; k <= half; k++) tot += a1[k];
    times.push(tc);
    if (!open || tot / M < maxPow / blkF * 1e-7) { kmax.push(NaN); khz.push(NaN); kall.push(NaN); continue; }
    const r = bandK(a1, a2, M);
    let best = NaN, bh = NaN;
    r.bands.forEach((b, i) => { if (b.share >= 0.02 && isFinite(b.K) && (!isFinite(best) || b.K > best)) { best = b.K; bh = Math.sqrt(DESC.kurtBands[i][0] * DESC.kurtBands[i][1]); } });
    kmax.push(best); khz.push(bh); kall.push(r.all);
    if (isFinite(best)) {
      const f0 = b0 * blkF, f1 = Math.min(T, b1 * blkF); let pmax = 0;
      for (let f = f0; f < f1; f++) if (pw[f] > pmax) pmax = pw[f];
      for (let f = f0; f < f1; f++) { const v = best > 0 && pmax > 0 ? best * pw[f] / pmax : best; if (!(loc[f] >= v)) loc[f] = v; }
    }
  }
  const locT = Array.from({ length: T }, (_, f) => (f * H + N / 2) / (rate * H));
  // whole file
  a1.fill(0); a2.fill(0); let M = 0;
  for (let b = 0; b < nBlk; b++) { const o = b * (half + 1); M += cnt[b]; for (let k = 1; k <= half; k++) { a1[k] += S1[o + k]; a2[k] += S2[o + k]; } }
  const whole = bandK(a1, a2, Math.max(1, M));
  let wb = -1; whole.bands.forEach((b, i) => { if (b.share >= 0.02 && isFinite(b.K) && (wb < 0 || b.K > whole.bands[wb].K)) wb = i; });
  const Mw = win * blkF;
  return {
    curve: toFrames(kmax, times, F), hz: toFrames(khz, times, F), all: toFrames(kall, times, F), local: toFramesMax(loc, locT, F),
    sig: 1.645 * 2 / Math.sqrt(Mw),
    summary: { K: whole.all, Kmax: wb >= 0 ? whole.bands[wb].K : NaN, band: wb >= 0 ? DESC.kurtBands[wb] : null, bands: whole.bands.map(b => b.K) }
  };
}

/* ---------------- 2. squared-envelope spectrum: repetition rate (machine diagnostics), speech-likeness ---------------- */
// Welch average of Hann-windowed periodograms (segments of segS seconds, 50 % overlap) of the envelope in dB, each segment
// linearly detrended so decays and fades do not read as slow repetition. Also returns the modulation depth scale.
function envSpectrum(e, rate, lo, hi, segS, rawAmp) {
  const n = e.length, Ls = Math.min(n, Math.max(8, Math.round((segS || n / rate) * rate))), hop = Math.max(1, Ls >> 1);
  const NF = nextPow2(Ls) * 2, re = new Float64Array(NF), im = new Float64Array(NF), fft = WL.getFFT(NF), P = new Float64Array(NF / 2);
  let mean = 0; for (let i = 0; i < n; i++) mean += e[i]; mean /= n;
  const fl = Math.max(1e-30, mean * 1e-6), y = new Float64Array(Ls);
  let k = 0, wsum = 0; for (let i = 0; i < Ls; i++) wsum += 0.5 - 0.5 * Math.cos(2 * Math.PI * (i + 0.5) / Ls);
  for (let a = 0; a + Ls <= n; a += hop) {
    for (let i = 0; i < Ls; i++) y[i] = rawAmp ? e[a + i] : 10 * Math.log10(e[a + i] + fl);
    // remove the best straight line
    let sx = 0, sy = 0, sxx = 0, sxy = 0; for (let i = 0; i < Ls; i++) { sx += i; sy += y[i]; sxx += i * i; sxy += i * y[i]; }
    const bb = (Ls * sxy - sx * sy) / Math.max(1e-12, Ls * sxx - sx * sx), aa = (sy - bb * sx) / Ls;
    re.fill(0); im.fill(0);
    for (let i = 0; i < Ls; i++) re[i] = (y[i] - aa - bb * i) * (0.5 - 0.5 * Math.cos(2 * Math.PI * (i + 0.5) / Ls));
    fft(re, im);
    for (let q = 0; q < NF / 2; q++) P[q] += re[q] * re[q] + im[q] * im[q];
    k++;
  }
  const df = rate / NF;
  // amplitude of a sinusoidal component from its peak: A = 2 sqrt(P/k) / sum(window)
  return { P, df, k0: Math.max(1, Math.ceil(Math.max(lo, 3 / (Ls / rate)) / df)), k1: Math.min(NF / 2 - 1, Math.floor(hi / df)), segs: k, ampOf: v => 2 * Math.sqrt(v / Math.max(1, k)) / wsum };
}
function peakRate(sp) {
  const { P, df, k0, k1 } = sp; if (k1 - k0 < 4) return null;
  const v = []; for (let k = k0; k <= k1; k++) v.push(P[k]); v.sort((a, b) => a - b);
  const med = v[v.length >> 1] + 1e-30;
  let best = k0; for (let k = k0; k <= k1; k++) if (P[k] > P[best]) best = k;
  const prom = k => 10 * Math.log10((P[k] + 1e-30) / med), thr = repThr(sp);
  // prefer the fundamental repetition: a subharmonic nearly as prominent wins
  for (let it = 0; it < 2; it++) { const h = Math.round(best / 2); if (h >= k0) { let hb = h; for (let k = Math.max(k0, h - 2); k <= Math.min(k1, h + 2); k++) if (P[k] > P[hb]) hb = k; if (prom(hb) >= prom(best) - 6 && prom(hb) >= thr) best = hb; } }
  const y0 = P[best - 1] || P[best], y1 = P[best], y2 = P[best + 1] || P[best], den = y0 - 2 * y1 + y2;
  const off = den !== 0 ? Math.max(-0.5, Math.min(0.5, 0.5 * (y0 - y2) / den)) : 0;
  const depth = sp.ampOf ? sp.ampOf(P[best]) : Infinity;
  return { hz: (best + off) * df, prom: prom(best), depth, clear: prom(best) >= thr && depth >= DESC.repDepthDb };
}
// band envelopes in dB, computed once (floor 60 dB below each band's mean power)
function dbEnvelopes(env) {
  return env.map(e => { let m = 0; for (let i = 0; i < e.length; i++) m += e[i]; m /= Math.max(1, e.length); const fl = Math.max(1e-30, m * 1e-6), y = new Float32Array(e.length); for (let i = 0; i < e.length; i++) y[i] = 10 * Math.log10(e[i] + fl); return y; });
}
function detrend(y, a, n, out) {
  let sx = 0, sy = 0, sxx = 0, sxy = 0; for (let i = 0; i < n; i++) { const v = y[a + i]; sx += i; sy += v; sxx += i * i; sxy += i * v; }
  const b = (n * sxy - sx * sy) / Math.max(1e-12, n * sxx - sx * sx), c = (sy - b * sx) / n;
  for (let i = 0; i < n; i++) out[i] = y[a + i] - c - b * i;
  return out;
}
const hannCache = {};
function hannS(L) { if (!hannCache[L]) { const w = new Float64Array(L); for (let i = 0; i < L; i++) w[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * (i + 0.5) / L); hannCache[L] = w; } return hannCache[L]; }
// Welch spectrum of a dB envelope over [a, b): detrended Hann segments of Ls samples at hop Ls/2. Segment periodograms are
// cached by start sample, so overlapping analysis windows on the same segment grid share them.
function welchDb(y, a, b, Ls, rate, lo, hi, cache) {
  const hop = Math.max(1, Ls >> 1), NF = nextPow2(Ls) * 2, w = hannS(Ls), P = new Float64Array(NF / 2);
  let k = 0, wsum = 0; for (let i = 0; i < Ls; i++) wsum += w[i];
  for (let s = a; s + Ls <= b; s += hop) {
    let seg = cache ? cache.get(s) : null;
    if (!seg) {
      const re = new Float64Array(NF), im = new Float64Array(NF); detrend(y, s, Ls, re); for (let i = 0; i < Ls; i++) re[i] *= w[i];
      WL.getFFT(NF)(re, im); seg = new Float64Array(NF / 2); for (let q = 0; q < NF / 2; q++) seg[q] = re[q] * re[q] + im[q] * im[q];
      if (cache) cache.set(s, seg);
    }
    for (let q = 0; q < NF / 2; q++) P[q] += seg[q];
    k++;
  }
  const df = rate / NF;
  return { P, df, k0: Math.max(1, Math.ceil(Math.max(lo, 3 / (Ls / rate)) / df)), k1: Math.min(NF / 2 - 1, Math.floor(hi / df)), segs: k, ampOf: v => 2 * Math.sqrt(v / Math.max(1, k)) / wsum };
}
// a spectral peak counts as repetition only if the detrended dB envelope correlates with itself one and two periods later
function periodicAtDb(y, a, b, rate, hz) {
  const n = b - a, L1 = Math.round(rate / hz), L2 = 2 * L1, d = detrend(y, a, n, new Float64Array(n));
  const acf = L => { let s = 0, s1 = 0, s2 = 0; for (let i = 0; i + L < n; i++) { s += d[i] * d[i + L]; s1 += d[i] * d[i]; s2 += d[i + L] * d[i + L]; } return s1 > 0 && s2 > 0 ? s / Math.sqrt(s1 * s2) : 0; };
  if (L2 >= n * 0.75 || L1 < 1) return L1 < n * 0.5 ? acf(L1) >= 0.5 : false;
  return acf(L1) >= 0.3 && acf(L2) >= 0.2;
}
// a single periodogram has chance peaks about 10 dB above its median; averaging segments lowers that
function repThr(sp) { return sp.segs >= 3 ? DESC.repProm : DESC.repProm + 5; }
function repetitionOf(st, F, gate, dur) {
  const { env, rate } = st, T = env[0].length, yDb = dbEnvelopes(env);
  const pre = env.map(e => { const p = new Float64Array(e.length + 1); for (let i = 0; i < e.length; i++) p[i + 1] = p[i] + e[i]; return p; });
  const W = Math.min(T, Math.max(Math.round(1 * rate), Math.round(Math.min(DESC.repWin, dur) * rate)));
  const Ls = Math.min(W, Math.round(Math.max(2, W / rate / 3) * rate)), hopS = Math.max(1, Ls >> 1);
  const step = hopS * Math.max(1, Math.round(DESC.repStep * rate / hopS));            // on the segment grid, so segments are shared
  const hi = Math.min(DESC.repMax, rate * 0.45), caches = env.map(() => new Map());
  const one = (a, b, L, cached) => {
    let best = null;
    env.forEach((e, bi) => {
      if (pre[bi][b] - pre[bi][a] <= 0) return;
      const r = peakRate(welchDb(yDb[bi], a, b, L, rate, DESC.repMin, hi, cached ? caches[bi] : null));
      if (r && r.clear) { const h = (b - a) >> 1, two = h > 2.5 * rate / r.hz; if (!periodicAtDb(yDb[bi], a, b, rate, r.hz) || (two && (!periodicAtDb(yDb[bi], a, a + h, rate, r.hz) || !periodicAtDb(yDb[bi], a + h, b, rate, r.hz)))) r.clear = false; }
      if (r && (!best || (r.clear && !best.clear) || (r.clear === best.clear && r.prom > best.prom))) best = r;
    });
    return best;
  };
  const times = [], hz = [], prom = [];
  for (let a = 0; a + W <= T; a += step) {
    times.push((a + W / 2) / rate);
    const r = gateShare(gate, F, a / rate, (a + W) / rate) >= 0.1 ? one(a, a + W, Ls, true) : null;
    hz.push(r && r.clear ? r.hz : NaN); prom.push(r ? r.prom : NaN);
  }
  // whole file (up to 30 s from the middle)
  const L = Math.min(T, Math.round(30 * rate)), a0 = Math.max(0, Math.floor((T - L) / 2));
  const whole = L >= Math.round(0.8 * rate) ? one(a0, a0 + L, Math.min(L, Math.round(Math.max(2, L / rate / 3) * rate)), false) : null;
  // speech-likeness (Ding et al. 2017): modulation energy 3–7 Hz over 0.5–2.5 Hz of the 300–5000 Hz amplitude envelope
  let speech = NaN;
  if (dur >= 3) {
    const e = new Float32Array(T); for (let i = 0; i < T; i++) e[i] = Math.sqrt(env[1][i] + env[2][i]);
    const sp = envSpectrum(e, rate, 0.5, 7, Math.min(dur, 6), true), band = (lo, hi) => { let s = 0, c = 0; for (let k = Math.ceil(lo / sp.df); k <= Math.floor(hi / sp.df); k++) { s += sp.P[k] * k * sp.df; c++; } return s / Math.max(1, c); };
    // energy per octave (f·P), so a 1/f modulation background reads 0 dB. Experimental: Ding et al.'s 4–5 Hz speech peak is a
    // population average and did not separate single speech clips from music in our tests.
    speech = 10 * Math.log10((band(3, 7) + 1e-30) / (band(0.5, 2.5) + 1e-30));
  }
  const cycles = whole && whole.clear ? dur * whole.hz : NaN;
  return {
    hz: toFrames(hz, times, F), prom: toFrames(prom, times, F),
    summary: { hz: whole && whole.clear ? whole.hz : NaN, prom: whole ? whole.prom : NaN, cycles, cycleError: isFinite(cycles) ? Math.abs(cycles - Math.round(cycles)) : NaN, speech }
  };
}

/* ---------------- 3. STA/LTA events (seismology), burstiness, b-value, temporal entropy (ecoacoustics) ---------------- */
function blocks(x, sr, ms) {
  const B = Math.max(1, Math.round(sr * ms / 1000)), n = Math.floor(x.length / B), amp = new Float32Array(n), en = new Float32Array(n);
  for (let i = 0; i < n; i++) { let a = 0, e = 0; for (let j = i * B; j < (i + 1) * B; j++) { const v = x[j]; a += Math.abs(v); e += v * v; } amp[i] = a / B; en[i] = e / B; }
  return { amp, en, rate: sr / B };
}
function staLta(amp, rate) {
  let pk = 0; for (const a of amp) if (a > pk) pk = a;
  const floor = pk * Math.pow(10, -DESC.eventFloorDb / 20), ks = 1 / Math.max(1, DESC.staS * rate), kl = 1 / Math.max(1, DESC.staL * rate);
  let sta = floor, lta = floor, on = false, s0 = 0, peak = 0;
  const ev = [], ratio = new Float32Array(amp.length);
  for (let i = 0; i < amp.length; i++) {
    sta += (amp[i] - sta) * ks; lta += (amp[i] - lta) * kl;
    const r = sta / Math.max(lta, floor * 0.5); ratio[i] = r;
    if (!on) { if (r >= DESC.trigger && sta >= floor * 3) { on = true; s0 = i; peak = amp[i]; } }
    else { if (amp[i] > peak) peak = amp[i]; if (r < DESC.detrigger) { ev.push({ t0: s0 / rate, t1: i / rate, peak }); on = false; } }
    if (ev.length > 20000) break;
  }
  if (on) ev.push({ t0: s0 / rate, t1: amp.length / rate, peak });
  return ev.map(e => ({ t0: e.t0, t1: e.t1, peakDb: 20 * Math.log10(e.peak * Math.PI / 2 + 1e-12) }));
}
/** Burstiness B = (sigma - mu)/(sigma + mu) of inter-event times (Goh & Barabási): -1 periodic, ~0 Poisson, > 0 clustered */
function burstiness(times) {
  if (times.length < 4) return NaN;
  const d = []; for (let i = 1; i < times.length; i++) d.push(times[i] - times[i - 1]);
  const mu = d.reduce((a, b) => a + b, 0) / d.length, sd = Math.sqrt(d.reduce((a, b) => a + (b - mu) * (b - mu), 0) / d.length);
  return (sd - mu) / (sd + mu);
}
/** Gutenberg–Richter b-value by Aki's maximum likelihood, magnitudes m = log10(amplitude); completeness at the 20th percentile */
function bValue(mags) {
  if (mags.length < 20) return { b: NaN, n: mags.length };
  const mc = quant(mags, 0.2), m = mags.filter(v => v >= mc);
  const mean = m.reduce((a, b) => a + b, 0) / m.length;
  return { b: mean > mc ? LOG10E / (mean - mc) : NaN, n: m.length };
}
/** Temporal entropy Ht (scikit-maad): normalised Shannon entropy of the energy envelope; < 0.7 transient, ~1 steady */
function temporalEntropy(en, a, b) {
  let s = 0; for (let i = a; i < b; i++) s += en[i]; if (s <= 0 || b - a < 2) return NaN;
  let h = 0; for (let i = a; i < b; i++) { const p = en[i] / s; if (p > 0) h -= p * Math.log(p); }
  return h / Math.log(b - a);
}

/* ---------------- 4. spectral motion: 1-D optimal transport between frames (Wasserstein-1 on a log-frequency axis) ---------------- */
function motionOf(F, gate) {
  const B = 40, n = F.nF, u = new Float64Array(B); for (let b = 0; b < B; b++) u[b] = Math.log2(melCentre(F, b));
  const du = new Float64Array(B); for (let b = 0; b < B - 1; b++) du[b] = u[b + 1] - u[b];
  const P = new Float64Array(n * B);
  let fileMax = -Infinity; for (let i = 0; i < n * B; i++) if (F.melDb[i] > fileMax) fileMax = F.melDb[i];
  for (let m = 0; m < n; m++) {
    let mx = -Infinity; for (let b = 0; b < B; b++) mx = Math.max(mx, F.melDb[m * B + b]);
    let s = 0; for (let b = 0; b < B; b++) { const d = F.melDb[m * B + b]; const v = d < mx - DESC.velRange || d < fileMax - 60 ? 0 : Math.pow(10, DESC.velGamma * (d - mx) / 10); P[m * B + b] = v; s += v; }
    if (s <= 0) { for (let b = 0; b < B; b++) P[m * B + b] = NaN; continue; }
    let c = 0; for (let b = 0; b < B; b++) { c += P[m * B + b] / s; P[m * B + b] = c; }
  }
  const vel = new Float32Array(n).fill(NaN), w1 = new Float32Array(n).fill(NaN);
  for (let m = 1; m < n; m++) {
    if (!gate[m] || !gate[m - 1] || isNaN(P[m * B]) || isNaN(P[(m - 1) * B])) continue;
    let sv = 0, sw = 0; for (let b = 0; b < B - 1; b++) { const d = P[m * B + b] - P[(m - 1) * B + b]; sv -= d * du[b]; sw += Math.abs(d) * du[b]; }
    vel[m] = sv / F.dt; w1[m] = sw / F.dt;
  }
  const half = Math.max(1, Math.round(DESC.velSmooth / F.dt / 2));
  const v = smoothNaN(vel, half), W = smoothNaN(w1, half), reshape = new Float32Array(n);
  for (let m = 0; m < n; m++) reshape[m] = isFinite(W[m]) ? Math.max(0, W[m] - Math.abs(v[m])) : NaN;
  // net glide: centroid on the log axis, first vs last fifth of the sounding frames
  const cent = [], g = []; for (let m = 0; m < n; m++) if (gate[m] && !isNaN(P[m * B])) { let s = 0; for (let b = 0; b < B - 1; b++) s += (1 - P[m * B + b]) * du[b]; cent.push(u[0] + s); g.push(m); }
  const k = Math.max(1, Math.floor(cent.length / 5)), net = cent.length >= 5 ? meanF(cent.slice(-k)) - meanF(cent.slice(0, k)) : NaN;
  return { vel: v, reshape, summary: { medianAbs: quant(Array.from(v).map(Math.abs), 0.5), up: quant(v, 0.95), down: quant(v, 0.05), net } };
}

/* ---------------- 5. periodicity without a pitch tracker: cepstral peak prominence (voice science) and phase coherence ---------------- */
function cppOf(x, sr, F, gate, dur) {
  const N = nextPow2(Math.round(2048 * sr / 44100)), H = N >> 1, half = N >> 1, fft = WL.getFFT(N), w = WL.hannP(N);
  const T = Math.max(0, Math.floor((x.length - N) / H) + 1);
  const qLo = Math.max(2, Math.floor(sr / DESC.cppFmax)), qHi = Math.min(half - 1, Math.ceil(sr / DESC.cppFmin)), q0 = Math.max(1, Math.round(0.001 * sr));
  const zr = new Float64Array(N), zi = new Float64Array(N), cr = new Float64Array(N), ci = new Float64Array(N);
  // phase coherence compares frames one window apart (independent for noise): with H = N/2 that is 2 frames back
  const sp = Math.max(1, Math.round(N / H)), RING = 2 * sp + 1;
  const rRe = Array.from({ length: RING }, () => new Float64Array(half + 1)), rIm = Array.from({ length: RING }, () => new Float64Array(half + 1)), rMg = Array.from({ length: RING }, () => new Float64Array(half + 1));
  const times = new Array(T), cpp = new Array(T).fill(NaN), pci = new Array(T).fill(NaN);
  let sx = 0, sxx = 0, nq = 0; for (let q = q0; q < half; q++) { sx += q; sxx += q * q; nq++; }
  const cepOf = (C) => {   // CPP from a cepstrum (C[q] * N = the transform of the dB spectrum)
    let sy = 0, sxy = 0, pq = qLo, pv = -Infinity;
    for (let q = q0; q < half; q++) { const c = C[q] / N, v = 10 * Math.log10(c * c + 1e-12); sy += v; sxy += q * v; if (q >= qLo && q <= qHi && v > pv) { pv = v; pq = q; } }
    const beta = (nq * sxy - sx * sy) / (nq * sxx - sx * sx), alpha = (sy - beta * sx) / nq;
    return pv - (alpha + beta * pq);
  };
  for (let t = 0; t < T; t += 2) {
    const tb = t + 1 < T ? t + 1 : -1, sa = t * H, sb = tb >= 0 ? tb * H : 0;
    for (let i = 0; i < N; i++) { zr[i] = x[sa + i] * w[i]; zi[i] = tb >= 0 ? x[sb + i] * w[i] : 0; }
    fft(zr, zi);
    const live = [false, false];
    cr.fill(0); ci.fill(0);
    for (let slot = 0; slot < 2; slot++) {
      const tt = slot ? tb : t; if (tt < 0) continue;
      const ix = tt % RING, Re = rRe[ix], Im = rIm[ix], Mg = rMg[ix]; let mx = 0;
      for (let k = 0; k <= half; k++) {
        const m = (N - k) & (N - 1), ar = zr[k], ai = zi[k], br = zr[m], bi = zi[m];
        const xr = slot ? (ai + bi) / 2 : (ar + br) / 2, xi = slot ? (br - ar) / 2 : (ai - bi) / 2;
        Re[k] = xr; Im[k] = xi; const mg = Math.sqrt(xr * xr + xi * xi); Mg[k] = mg; if (mg > mx) mx = mg;
      }
      times[tt] = (tt * H + N / 2) / sr;
      const mF = Math.min(F.nF - 1, Math.floor(times[tt] / F.dt));
      if (!gate[mF] || mx <= 0) continue;
      if (tt >= 2 * sp) {
        // magnitude-weighted unit phasor of the second phase difference: |X0| e^{i(p0 - 2 p1 + p2)} = X0 conj(X1)^2 X2 / (|X1|^2 |X2|)
        const i1 = (tt - sp) % RING, i2 = (tt - 2 * sp) % RING, R1 = rRe[i1], I1 = rIm[i1], M1 = rMg[i1], R2 = rRe[i2], I2 = rIm[i2], M2 = rMg[i2];
        let cR = 0, cI = 0, cW = 0;
        for (let k = 1; k < half; k++) {
          const m0 = Mg[k], m1 = M1[k], m2 = M2[k]; if (m0 < mx * 1e-3 || m1 <= 0 || m2 <= 0) continue;
          const c1r = R1[k] * R1[k] - I1[k] * I1[k], c1i = -2 * R1[k] * I1[k];
          const pr = Re[k] * c1r - Im[k] * c1i, pim = Re[k] * c1i + Im[k] * c1r, s = 1 / (m1 * m1 * m2);
          cR += (pr * R2[k] - pim * I2[k]) * s; cI += (pr * I2[k] + pim * R2[k]) * s; cW += m0;
        }
        pci[tt] = cW > 0 ? Math.hypot(cR, cI) / cW : NaN;
      }
      // the dB spectrum is real and even, so two of them share one FFT: real part = first cepstrum, imaginary = second
      const D = slot ? ci : cr, fl = mx * mx * 1e-10;
      for (let k = 0; k <= half; k++) { const L = 10 * Math.log10(Mg[k] * Mg[k] + fl); D[k] = L; if (k > 0 && k < half) D[N - k] = L; }
      live[slot] = true;
    }
    if (!live[0] && !live[1]) continue;
    fft(cr, ci);
    if (live[0]) cpp[t] = cepOf(cr);
    if (live[1]) cpp[tb] = cepOf(ci);
  }
  const cppF = smoothNaN(toFrames(cpp, times, F), Math.max(1, Math.round(0.02 / F.dt))), pciF = smoothNaN(toFrames(pci, times, F), Math.max(1, Math.round(0.02 / F.dt)));
  return { cpp: cppF, pci: pciF, summary: { cpp: quant(cpp, 0.5), cppP90: quant(cpp, 0.9), pci: quant(pci, 0.5) } };
}

/* ---------------- 6. arrow of time: third-moment asymmetry of the log envelope (time-irreversibility) ---------------- */
// T(tau) = <D^3>/<D^2>^(3/2), D = e(t+tau) - e(t): 0 for any linear Gaussian process, > 0 for impacts, < 0 for reversed sounds.
// Central moments: a steady decay or swell inside a window (constant slope) reads 0; only rare large rises (onsets) or falls (cuts)
// tilt the measure, and it stays exactly antisymmetric under time reversal.
function irreversibility(e, lag, a, b, floorDb) {
  let m1 = 0, k = 0;
  for (let i = a; i + lag < b; i++) { if (e[i] < floorDb + 3 && e[i + lag] < floorDb + 3) continue; m1 += e[i + lag] - e[i]; k++; }
  if (k < 20) return { T: NaN, k };
  m1 /= k;
  let m2 = 0, m3 = 0;
  for (let i = a; i + lag < b; i++) { if (e[i] < floorDb + 3 && e[i + lag] < floorDb + 3) continue; const d = e[i + lag] - e[i] - m1; m2 += d * d; m3 += d * d * d; }
  if (m2 <= 0) return { T: NaN, k };
  m2 /= k; m3 /= k;
  if (m2 < 1) return { T: NaN, k };                                  // level changes under 1 dB rms: too steady to have a direction
  return { T: m3 / Math.pow(m2, 1.5), k };
}
function arrowOf(bl, F, gate) {
  const en = bl.en, n = en.length, rate = bl.rate; let pk = 0; for (const v of en) if (v > pk) pk = v;
  // playback starts and ends at the file's own quiet level: 50 ms lead-in and lead-out at its 10th percentile let an onset at
  // sample 0 or a cut at the last sample count, and keep the measure exactly antisymmetric under time reversal
  const floorDb = 10 * Math.log10(pk * 1e-8 + 1e-30), pre = Math.round(0.05 * rate), e = new Float32Array(n + 2 * pre);
  for (let i = 0; i < n; i++) e[pre + i] = 10 * Math.log10(en[i] + pk * 1e-8 + 1e-30);
  const quiet = quant(e.subarray(pre, pre + n), 0.1); e.fill(quiet, 0, pre); e.fill(quiet, pre + n);
  const lags = DESC.arrowLags.map(s => Math.max(1, Math.round(s * rate)));
  const whole = lags.map(l => irreversibility(e, l, 0, n + 2 * pre, floorDb).T);
  const W = Math.round(DESC.arrowWin * rate), step = Math.max(1, Math.round(0.05 * rate)), times = [], vals = [];
  for (let a = 0; a + W <= n || (a === 0 && n > lags[1] * 4); a += step) {
    const b = Math.min(n, a + W), tc = (a + b) / 2 / rate, mF = Math.min(F.nF - 1, Math.floor(tc / F.dt));
    const r = irreversibility(e, lags[1], a + pre, b + pre, floorDb);
    times.push(tc); vals.push(gate[mF] && r.k >= 0.3 * (b - a - lags[1]) ? r.T : NaN);
    if (b >= n) break;
  }
  return { curve: toFrames(vals, times, F), summary: { T5: whole[0], T20: whole[1], T80: whole[2] } };
}

/* ---------------- 7. information dynamics (Abdallah & Plumbley): surprisal, predictive information, model information ---------------- */
function kmeans(X, dim, K, seed) {
  const n = X.length / dim, r = rng(seed), C = new Float64Array(K * dim), lab = new Int32Array(n), d2 = new Float64Array(n);
  const dist = (i, c) => { let s = 0; for (let j = 0; j < dim; j++) { const d = X[i * dim + j] - C[c * dim + j]; s += d * d; } return s; };
  // k-means++ initialisation
  let first = Math.floor(r() * n); for (let j = 0; j < dim; j++) C[j] = X[first * dim + j];
  for (let c = 1; c < K; c++) {
    let tot = 0; for (let i = 0; i < n; i++) { let m = Infinity; for (let q = 0; q < c; q++) m = Math.min(m, dist(i, q)); d2[i] = m; tot += m; }
    let pick = r() * tot, i = 0; while (i < n - 1 && pick > d2[i]) { pick -= d2[i]; i++; }
    for (let j = 0; j < dim; j++) C[c * dim + j] = X[i * dim + j];
  }
  for (let it = 0; it < 20; it++) {
    let moved = 0;
    for (let i = 0; i < n; i++) { let bc = 0, bd = Infinity; for (let c = 0; c < K; c++) { const d = dist(i, c); if (d < bd) { bd = d; bc = c; } } if (lab[i] !== bc) { lab[i] = bc; moved++; } }
    const S = new Float64Array(K * dim), N = new Float64Array(K);
    for (let i = 0; i < n; i++) { N[lab[i]]++; for (let j = 0; j < dim; j++) S[lab[i] * dim + j] += X[i * dim + j]; }
    for (let c = 0; c < K; c++) if (N[c]) for (let j = 0; j < dim; j++) C[c * dim + j] = S[c * dim + j] / N[c];
    if (!moved && it) break;
  }
  return lab;
}
// agglomerative merge of k-means clusters whose centroids are closer than dmin; returns labels 0..K'-1
function mergeClusters(X, dim, lab, K, dmin) {
  const n = lab.length, C = Array.from({ length: K }, () => new Float64Array(dim)), N = new Float64Array(K), map = Array.from({ length: K }, (_, i) => i);
  for (let i = 0; i < n; i++) { N[lab[i]]++; for (let j = 0; j < dim; j++) C[lab[i]][j] += X[i * dim + j]; }
  for (let c = 0; c < K; c++) if (N[c]) for (let j = 0; j < dim; j++) C[c][j] /= N[c];
  const alive = new Set(); for (let c = 0; c < K; c++) if (N[c]) alive.add(c);
  for (;;) {
    let ba = -1, bb = -1, bd = Infinity; const L = [...alive];
    for (let x = 0; x < L.length; x++) for (let y = x + 1; y < L.length; y++) { let d = 0; for (let j = 0; j < dim; j++) d += (C[L[x]][j] - C[L[y]][j]) ** 2; d = Math.sqrt(d); if (d < bd) { bd = d; ba = L[x]; bb = L[y]; } }
    if (bd >= dmin || ba < 0) break;
    for (let j = 0; j < dim; j++) C[ba][j] = (C[ba][j] * N[ba] + C[bb][j] * N[bb]) / (N[ba] + N[bb]);
    N[ba] += N[bb]; alive.delete(bb); for (let c = 0; c < K; c++) if (map[c] === bb) map[c] = ba;
  }
  const order = [...alive].sort((a, b) => a - b), re = new Map(order.map((c, i) => [c, i])), out = new Int32Array(n);
  for (let i = 0; i < n; i++) out[i] = re.get(map[lab[i]]);
  return out;
}
/** Online first-order Markov model with Dirichlet counts and forgetting. Per step: surprisal -ln a(x_t|x_t-1),
    predictive information KL(a(.|x_t) || a2(.|x_t-1)) (its mean is the PIR h(a^2) - h(a)), and model information
    KL(posterior row || prior row) for the row that was updated. opts: { beta (1 = no forgetting), alpha0 } */
function markovInfo(sym, K, opts) {
  const beta = opts && opts.beta !== undefined ? opts.beta : DESC.forget, a0 = opts && opts.alpha0 !== undefined ? opts.alpha0 : DESC.alpha0;
  const C = new Float64Array(K * K).fill(a0), rowS = new Float64Array(K).fill(a0 * K);
  const n = sym.length, sur = new Float32Array(n).fill(NaN), pi = new Float32Array(n).fill(NaN), info = new Float32Array(n).fill(NaN);
  const a2 = new Float64Array(K), row = new Float64Array(K);
  for (let t = 1; t < n; t++) {
    const i = sym[t - 1], j = sym[t];
    if (i < 0 || j < 0) continue;
    sur[t] = -Math.log(C[i * K + j] / rowS[i]);
    // two-step prediction from x_{t-1}
    a2.fill(0);
    for (let m = 0; m < K; m++) { const p = C[i * K + m] / rowS[i]; if (p < 1e-9) continue; for (let q = 0; q < K; q++) a2[q] += p * C[m * K + q] / rowS[m]; }
    let kl = 0; for (let q = 0; q < K; q++) { const p = C[j * K + q] / rowS[j]; if (p > 0) kl += p * Math.log(p / Math.max(1e-12, a2[q])); }
    pi[t] = Math.max(0, kl);
    // update row i with forgetting towards the prior
    for (let q = 0; q < K; q++) row[q] = C[i * K + q] / rowS[i];
    if (beta < 1) for (let m = 0; m < K; m++) { let s = 0; for (let q = 0; q < K; q++) { C[m * K + q] = a0 + beta * (C[m * K + q] - a0); s += C[m * K + q]; } rowS[m] = s; }
    C[i * K + j] += 1; rowS[i] += 1;
    let ki = 0; for (let q = 0; q < K; q++) { const p = C[i * K + q] / rowS[i]; if (p > 0) ki += p * Math.log(p / Math.max(1e-12, row[q])); }
    info[t] = ki;
  }
  return { surprisal: sur, pi, info };
}
function mfccFrames(F, rate, gate) {
  const B = 40, n = F.nF, step = Math.max(1, Math.round(1 / rate / F.dt)), T = Math.ceil(n / step), D = 12;
  const out = new Float32Array(T * D), sil = new Uint8Array(T), times = new Float32Array(T), mel = new Float64Array(B);
  for (let t = 0; t < T; t++) {
    const a = t * step, b = Math.min(n, a + step); mel.fill(0); let g = 0;
    for (let m = a; m < b; m++) { g += gate[m]; for (let k = 0; k < B; k++) mel[k] += F.melDb[m * B + k] / (b - a); }
    sil[t] = g < (b - a) / 2 ? 1 : 0; times[t] = ((a + b) / 2) * F.dt;
    for (let d = 1; d <= D; d++) { let s = 0; for (let k = 0; k < B; k++) s += mel[k] * Math.cos(Math.PI * d * (k + 0.5) / B); out[t * D + d - 1] = s * Math.sqrt(2 / B); }
  }
  return { X: out, D, T, sil, times, step };
}
function infoOf(F, gate) {
  const M = mfccFrames(F, DESC.symRate, gate), idx = []; for (let t = 0; t < M.T; t++) if (!M.sil[t]) idx.push(t);
  if (idx.length < 20) return { surprisal: new Float32Array(F.nF).fill(NaN), pir: new Float32Array(F.nF).fill(NaN), info: new Float32Array(F.nF).fill(NaN), summary: { pir: NaN, surprisal: NaN, info: NaN, K: 0 } };
  const K = Math.max(2, Math.min(DESC.symK, Math.floor(idx.length / 4)));
  const Xs = new Float32Array(idx.length * M.D); idx.forEach((t, i) => Xs.set(M.X.subarray(t * M.D, (t + 1) * M.D), i * M.D));
  const lab0 = kmeans(Xs, M.D, K, 11), lab = mergeClusters(Xs, M.D, lab0, K, DESC.symMergeDist), K2 = Math.max(...lab) + 1, sym = new Int32Array(M.T).fill(K2);
  idx.forEach((t, i) => { sym[t] = lab[i]; });
  const r = markovInfo(sym, K2 + 1, {});
  const half = Math.max(1, Math.round(0.25 * DESC.symRate));
  const sm = a => smoothNaN(a.map((v, t) => M.sil[t] ? NaN : v), half);
  const times = Array.from(M.times);
  const s = sm(r.surprisal), p = sm(r.pi), inf = sm(r.info);
  return { surprisal: toFrames(s, times, F), pir: toFrames(p, times, F), info: toFrames(inf, times, F), summary: { pir: meanF(r.pi.filter((_, t) => !M.sil[t])), surprisal: meanF(r.surprisal.filter((_, t) => !M.sil[t])), info: meanF(r.info.filter((_, t) => !M.sil[t])), K: K2 } };
}

// k-th smallest value (Hoare selection on a copy), O(n) instead of a full sort
function kth(src, k) {
  const a = Float64Array.from(src); let lo = 0, hi = a.length - 1;
  while (lo < hi) {
    const p = a[(lo + hi) >> 1]; let i = lo, j = hi;
    while (i <= j) { while (a[i] < p) i++; while (a[j] > p) j--; if (i <= j) { const t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; } }
    if (k <= j) hi = j; else if (k >= i) lo = i; else return a[k];
  }
  return a[k];
}
/* ---------------- 8. recurrence quantification on the self-similarity of timbre and harmony ---------------- */
function recurrenceOf(F, gate, dur) {
  const rate = Math.min(DESC.recRate, DESC.recMax / Math.max(1, dur)), M = mfccFrames(F, rate, gate), n = M.T, D = M.D;
  const step = M.step, chroma = new Float32Array(n * 12);
  for (let t = 0; t < n; t++) { let s = 0; for (let m = t * step; m < Math.min(F.nF, (t + 1) * step); m++) for (let p = 0; p < 12; p++) { chroma[t * 12 + p] += F.chroma[m * 12 + p]; s += F.chroma[m * 12 + p]; } if (s > 0) for (let p = 0; p < 12; p++) chroma[t * 12 + p] /= s; }
  const dist = (i, j) => { let s = 0; for (let d = 0; d < D; d++) { const v = (M.X[i * D + d] - M.X[j * D + d]) / 10; s += v * v; } for (let p = 0; p < 12; p++) { const v = chroma[i * 12 + p] - chroma[j * 12 + p]; s += v * v; } return Math.sqrt(s); };
  const w = Math.max(1, Math.round(DESC.recTheiler * rate)), Dm = new Float32Array(n * n);
  let off = new Float64Array(Math.max(1, n * (n - 1) / 2)), no = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { const d = dist(i, j); Dm[i * n + j] = d; Dm[j * n + i] = d; if (j - i >= w && !M.sil[i] && !M.sil[j]) off[no++] = d; }
  off = off.subarray(0, no);
  const nan = () => new Float32Array(F.nF).fill(NaN);
  if (n < 2 * w + 4 || off.length < 50) return { density: nan(), lam: nan(), summary: { RR: NaN, DET: NaN, LAM: NaN, TT: NaN, eps: NaN } };
  const eps = Math.min(DESC.recEpsHi, Math.max(kth(off, Math.floor(0.1 * (off.length - 1))), DESC.recEpsLo));
  const R = new Uint8Array(n * n); for (let i = 0; i < n * n; i++) R[i] = Dm[i] <= eps ? 1 : 0;
  for (let t = 0; t < n; t++) if (M.sil[t]) for (let j = 0; j < n; j++) { R[t * n + j] = 0; R[j * n + t] = 0; }
  const dens = new Float32Array(n).fill(NaN);
  for (let i = 0; i < n; i++) { if (M.sil[i]) continue; let s = 0, k = 0; for (let j = 0; j < n; j++) { if (Math.abs(i - j) < w || M.sil[j]) continue; s += R[i * n + j]; k++; } if (k) dens[i] = s / k; }
  // line statistics on a sub-square [a,b): DET on diagonals at lag >= minLag, LAM and TT on vertical lines (main diagonal excluded)
  const lines = (a, b, minLag) => {
    const L = DESC.recLmin; let rec = 0, det = 0, recV = 0, lamP = 0, vSum = 0, vN = 0;
    for (let k = minLag; k < b - a; k++) { let run = 0; for (let i = a; i + k < b; i++) { const r = R[i * n + i + k]; if (r) { run++; rec++; } if (!r || i + k === b - 1) { if (run >= L) det += run; run = 0; } } }
    for (let j = a; j < b; j++) { let run = 0; for (let i = a; i < b; i++) { const r = i !== j && R[i * n + j]; if (r) { run++; recV++; } if (!r || i === b - 1) { if (run >= L) { lamP += run; vSum += run; vN++; } run = 0; } } }
    return { DET: rec ? det / rec : NaN, LAM: recV ? lamP / recV : NaN, TT: vN ? vSum / vN / rate : NaN, rec };
  };
  const whole = lines(0, n, w);
  let RR = 0, RN = 0; for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (Math.abs(i - j) >= w && !M.sil[i] && !M.sil[j]) { RR += R[i * n + j]; RN++; }
  const Wb = Math.min(n, DESC.recBlock), sb = Math.max(1, Wb >> 2), lt = [], lv = [];
  for (let a = 0; a + Wb <= n; a += sb) { const r = lines(a, a + Wb, 1); lt.push((a + Wb / 2) / rate); let silent = 0; for (let t = a; t < a + Wb; t++) silent += M.sil[t]; lv.push(silent > Wb / 2 ? NaN : r.LAM); }
  return { density: toFrames(Array.from(dens), Array.from(M.times), F), lam: toFrames(lv, lt, F), summary: { RR: RN ? RR / RN : NaN, DET: whole.DET, LAM: whole.LAM, TT: whole.TT, eps } };
}

/* ---------------- 9. decay and material: Schroeder band decays after events (room and impact acoustics) ---------------- */
// Per band T60 from the backward-integrated energy (-5 to -25 dB), amplitude damping b = 60/(8.686*T60) and
// Klatzky's frequency-independent tau_d = f/b; log10(tau_d) sorts damped (rubber, wood) < glass < metal. Reverb reads as metal.
const MAT_NAMES = ["damped (rubber, wood)", "glass-like", "metal-like"];
function decaysOf(F, events, gate) {
  const B = 40, groups = [[40, 250], [250, 500], [500, 1000], [1000, 2000], [2000, 4000], [4000, 8000], [8000, 16000]];
  const gb = groups.map(([lo, hi]) => { const ks = []; for (let b = 0; b < B; b++) { const c = melCentre(F, b); if (c >= lo && c < hi) ks.push(b); } return ks; });
  const out = [];
  const ev = events.filter(e => e.peakDb > -60);
  for (let q = 0; q < ev.length && out.length < 200; q++) {
    const a = Math.floor(ev[q].t0 / F.dt), end = Math.min(F.nF, q + 1 < ev.length ? Math.floor(ev[q + 1].t0 / F.dt) : F.nF);
    if ((end - a) * F.dt < DESC.decayMin) continue;
    const res = []; let fastBands = 0;
    // impact-like: the broadband peak comes within 60 ms of the start, and the level falls at least 20 dB before the next event
    { let pm = a, pv = -1; for (let m = a; m < end; m++) if (F.kz[m] > pv) { pv = F.kz[m]; pm = m; } let lo = Infinity; for (let m = pm; m < end; m++) lo = Math.min(lo, F.kz[m]); if ((pm - a) * F.dt > 0.06 || !(lo < pv * 0.01)) continue; }
    gb.forEach((ks, gi) => {
      if (!ks.length) return; const fb0 = fastBands;
      const E = new Float64Array(end - a); for (let m = a; m < end; m++) { let s = 0; for (const b of ks) s += Math.pow(10, F.melDb[m * B + b] / 10); E[m - a] = s; }
      let p = 0; for (let i = 0; i < Math.min(E.length, Math.round(0.06 / F.dt) + 1); i++) if (E[i] > E[p]) p = i;
      const tail0 = E.slice(p), nT0 = tail0.length; if (nT0 < 4) return;
      const floor = quant(Array.from(tail0.slice(Math.floor(nT0 * 0.85))), 0.5);
      if (!(tail0[0] > floor * 300)) return;                       // needs 25 dB above the band's floor
      // truncate where the band first reaches 6 dB above its floor (Lundeby-style), so noise does not stretch the decay
      let cut = nT0; for (let i = 1; i < nT0 - 1; i++) if (tail0[i] < floor * 4 && tail0[i + 1] < floor * 4) { cut = i; break; }
      const tail = tail0.slice(0, cut), nT = tail.length;
      const edc = new Float64Array(nT); let s = 0; for (let i = nT - 1; i >= 0; i--) { s += Math.max(0, tail[i] - floor); edc[i] = s; }
      if (edc[0] <= 0) return;
      const pts = []; for (let i = 0; i < nT; i++) { const d = 10 * Math.log10(edc[i] / edc[0] + 1e-30); if (d <= -5 && d >= -25) pts.push([i * F.dt, d]); if (d < -25) break; }
      let slope;
      if (pts.length >= 4) {
        const mx = meanF(pts.map(v => v[0])), my = meanF(pts.map(v => v[1]));
        let sxy = 0, sxx = 0, syy = 0; for (const [x, y] of pts) { sxy += (x - mx) * (y - my); sxx += (x - mx) * (x - mx); syy += (y - my) * (y - my); }
        slope = sxy / sxx;
        if (sxy * sxy / (sxx * syy) < 0.9) return;                 // not a single exponential decay
      } else if (cut < nT0 && cut <= 4) {
        slope = 10 * Math.log10(floor * 4 / tail0[0]) / Math.max(F.dt, cut * F.dt);   // faster than the frame rate: bound from the floor crossing
        fastBands++;
      } else return;
      // the band's energy centroid at the peak stands for its mode frequency (fixed band centres would jump when a mode crosses an edge)
      let ce = 0, cw = 0; for (const bb of ks) { const e = Math.pow(10, F.melDb[(a + p) * B + bb] / 10); ce += e * melCentre(F, bb); cw += e; }
      const t60 = -60 / slope, bAmp = -slope / (20 * LOG10E), fc = cw > 0 ? ce / cw : Math.sqrt(groups[gi][0] * groups[gi][1]);
      res.push({ fc, t60, logTau: Math.log10(fc / bAmp), w: tail[0], b: bAmp, fast: fastBands > fb0 });
    });
    if (!res.length) continue;
    const wmed = key => { const v = res.slice().sort((x, y) => x[key] - y[key]), tot = v.reduce((s, r) => s + r.w, 0); let c = 0; for (const r of v) { c += r.w; if (c >= tot / 2) return r[key]; } return v[v.length - 1][key]; };
    const t60 = wmed("t60");
    // modal damping grows with frequency (b = pi tan(phi) f); a flat b(f) is a room, a noise burst or an envelope, not a material
    const slow = res.filter(r => !r.fast);
    let expo = NaN; if (slow.length >= 3) { const xs = slow.map(r => Math.log10(r.fc)), ys = slow.map(r => Math.log10(r.b)), mx = meanF(xs), my = meanF(ys); let sxy = 0, sxx = 0; xs.forEach((x, i) => { sxy += (x - mx) * (ys[i] - my); sxx += (x - mx) * (x - mx); }); expo = sxy / sxx; }
    let logTau = NaN, cls = -1;
    if (slow.length === 0) cls = 0;                                    // everything decays faster than we can resolve: very damped
    else if (slow.length < 3 || expo >= 0.5) { const v = slow.slice().sort((x, y) => x.logTau - y.logTau), tot = v.reduce((a, r) => a + r.w, 0); let c = 0; for (const r of v) { c += r.w; if (c >= tot / 2) { logTau = r.logTau; break; } } cls = logTau < DESC.materialEdges[0] ? 0 : logTau < DESC.materialEdges[1] ? 1 : 2; }
    out.push({ t: ev[q].t0, peakDb: ev[q].peakDb, t60, logTau, cls, expo, bands: res.length });
  }
  const tot = out.reduce((s, d) => s + Math.pow(10, d.peakDb / 10), 0);
  const wq = key => { const v = out.slice().sort((x, y) => x[key] - y[key]); let c = 0; for (const d of v) { c += Math.pow(10, d.peakDb / 10); if (c >= tot / 2) return d[key]; } return NaN; };
  const mod = out.filter(d => d.cls >= 0), counts = [0, 0, 0]; for (const d of mod) counts[d.cls] += Math.pow(10, d.peakDb / 10);
  const top = mod.length ? counts.indexOf(Math.max(...counts)) : -1, lt = out.filter(d => isFinite(d.logTau));
  const logTau = lt.length ? (() => { const v = lt.slice().sort((x, y) => x.logTau - y.logTau), tt = v.reduce((a, d) => a + Math.pow(10, d.peakDb / 10), 0); let c = 0; for (const d of v) { c += Math.pow(10, d.peakDb / 10); if (c >= tt / 2) return d.logTau; } return NaN; })() : NaN;
  return { decays: out, summary: { n: out.length, t60: out.length ? wq("t60") : NaN, logTau, material: top >= 0 ? MAT_NAMES[top] : (out.length ? "not modal" : ""), modal: mod.length } };
}

/* ---------------- all together ---------------- */
async function describe(F, buf) {
  if (F._desc) return F._desc;
  const tick = () => new Promise(r => setTimeout(r, 0));
  // per-stage timing (ms), reported in D.timing for tools/profile.js
  const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now()), T = {}; let t0 = now();
  const lap = k => { const t = now(); T[k] = (T[k] || 0) + t - t0; t0 = t; };
  const x0 = monoOf(buf), sr = buf.sampleRate, dur = buf.duration, gate = frameGate(F);
  const x = x0.length >= 8192 ? x0 : (() => { const p = new Float32Array(8192); p.set(x0); return p; })();
  const st = shortStft(x, sr, dur); lap("shortStft"); await tick(); t0 = now();
  const kurt = kurtosisOf(st, F, gate, dur); lap("kurtosis");
  const rep = repetitionOf(st, F, gate, dur); lap("repetition"); await tick(); t0 = now();
  const bl = blocks(x0, sr, 1);
  const events = staLta(bl.amp, bl.rate);
  const starts = events.map(e => e.t0), bv = bValue(events.map(e => e.peakDb / 20));
  const bl10 = blocks(x0, sr, 10), htW = Math.round(2 * bl10.rate), htT = [], htV = [];
  for (let a = 0; a < bl10.en.length; a += Math.max(1, Math.round(0.1 * bl10.rate))) { const b = Math.min(bl10.en.length, a + htW); htT.push((a + b) / 2 / bl10.rate); htV.push(gate[Math.min(F.nF - 1, Math.floor((a + b) / 2 / bl10.rate / F.dt))] ? temporalEntropy(bl10.en, a, b) : NaN); if (b >= bl10.en.length) break; }
  let act = 0; for (const e of events) act += e.t1 - e.t0;
  lap("events");
  const evSum = { n: events.length, rate: events.length / Math.max(1e-6, dur), meanDur: events.length ? act / events.length : NaN, active: act / Math.max(1e-6, dur), B: burstiness(starts), bValue: bv.b, bN: bv.n, Ht: temporalEntropy(bl10.en, 0, bl10.en.length) };
  await tick(); t0 = now();
  const mot = motionOf(F, gate); lap("motion");
  const cp = cppOf(x, sr, F, gate, dur); lap("cpp"); await tick(); t0 = now();
  const arr = arrowOf(bl, F, gate); lap("arrow");
  const inf = infoOf(F, gate); lap("info"); await tick(); t0 = now();
  const rec = recurrenceOf(F, gate, dur); lap("recurrence"); await tick(); t0 = now();
  const dec = decaysOf(F, events, gate); lap("decay");
  const D = {
    curves: { kurt: kurt.curve, kurtLocal: kurt.local, kurtHz: kurt.hz, repHz: rep.hz, repProm: rep.prom, ht: toFrames(htV, htT, F), vel: mot.vel, reshape: mot.reshape, cpp: cp.cpp, pci: cp.pci, arrow: arr.curve, surprisal: inf.surprisal, pir: inf.pir, info: inf.info, recDensity: rec.density, lam: rec.lam },
    events, decays: dec.decays, kurtSig: kurt.sig, timing: T,
    summary: { kurtosis: kurt.summary, repetition: rep.summary, events: evSum, motion: mot.summary, periodicity: cp.summary, arrow: arr.summary, info: inf.summary, recurrence: rec.summary, decay: dec.summary }
  };
  F._desc = D;
  return D;
}

const api = { describe, DESC, poolNaN, burstiness, bValue, temporalEntropy, irreversibility, markovInfo, kmeans, staLta, blocks, MAT_NAMES };
if (typeof module !== "undefined" && module.exports) module.exports = api;
root.PHD = api;
})(__ph);

// ---- src/effects.js
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Daniel Sandner
// Part of Playhead, https://github.com/eigensoundlab/playhead

/* ===================================================================
   Playhead effects bench: processing driven by the analysis.
   Every tool takes a decoded buffer plus its analysis and returns new
   buffers; the app re-analyses them so the views show before and after.
   =================================================================== */
(function (root) {
"use strict";
const WL = root.WL || (typeof require !== "undefined" ? require("./analysis.js") : null);
const tick = () => new Promise(r => setTimeout(r, 0));

function mkBuf(sr, chans) {
  const L = chans[0];
  return { sampleRate: sr, length: L.length, numberOfChannels: chans.length, duration: L.length / sr, getChannelData: c => chans[Math.min(c, chans.length - 1)] };
}
function chansOf(buf) { const out = []; for (let c = 0; c < buf.numberOfChannels; c++) out.push(Float32Array.from(buf.getChannelData(c))); return out; }
function peakOf(chans) { let p = 0; for (const ch of chans) for (let i = 0; i < ch.length; i++) { const a = Math.abs(ch[i]); if (a > p) p = a; } return p; }
function scaleTo(chans, peak) { const p = peakOf(chans); if (p <= 0) return 1; const g = peak / p; for (const ch of chans) for (let i = 0; i < ch.length; i++) ch[i] *= g; return g; }
function rng(seed) { let a = seed >>> 0; return () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const db = v => 20 * Math.log10(Math.max(v, 1e-12));
// integrated loudness, ITU-R BS.1770 (400 ms blocks, 75% overlap, -70 LUFS absolute and -10 LU relative gates)
function lufsOf(chans, sr) {
  const kc = WL.kFilterCoeffs(sr), len = chans[0].length, blk = Math.round(0.4 * sr), hop = Math.round(0.1 * sr);
  const zs = chans.map(c => { const a = new Float32Array(len), b = new Float32Array(len); WL.biquadInto(c, a, kc[0]); WL.biquadInto(a, b, kc[1]); return b; });
  const P = new Float64Array(len + 1);
  for (let i = 0; i < len; i++) { let s = 0; for (const z of zs) s += z[i] * z[i]; P[i + 1] = P[i] + s; }
  const blocks = [];
  if (len < blk) blocks.push(P[len] / Math.max(1, len));
  for (let s0 = 0; s0 + blk <= len; s0 += hop) blocks.push((P[s0 + blk] - P[s0]) / blk);
  const L = z => -0.691 + 10 * Math.log10(z + 1e-20);
  let g = blocks.filter(z => L(z) > -70);
  if (!g.length) return -70;
  const rel = L(g.reduce((a, b) => a + b, 0) / g.length) - 10;
  g = g.filter(z => L(z) > rel);
  return L(g.reduce((a, b) => a + b, 0) / Math.max(1, g.length));
}
function finish(src, chans, mode, report) {
  const sr = src.sampleRate;
  if (mode === "loudness") {
    const a = lufsOf(chansOf(src), sr), b = lufsOf(chans, sr);
    if (isFinite(a) && isFinite(b) && b > -70) { const g = undb(a - b); for (const c of chans) for (let i = 0; i < c.length; i++) c[i] *= g; report.push("Loudness matched to the original (" + (a - b >= 0 ? "+" : "") + (a - b).toFixed(1) + " dB)"); }
  } else if (mode === "peak") { scaleTo(chans, peakOf(chansOf(src))); report.push("Peak matched to the original"); }
  const p = peakOf(chans);
  if (p > 0.999) { scaleTo(chans, 0.999); report.push("Lowered " + db(p / 0.999).toFixed(1) + " dB to avoid clipping"); }
  return chans;
}
const undb = v => Math.pow(10, v / 20);

/* ---------- STFT engine: Hann analysis and synthesis, hop N/4, WOLA normalisation.
   gainFn(frame, time, magMono, gains) fills per-bin real gains shared by all channels. ---------- */
async function stftApply(chans, sr, N, gainFn, onProgress) {
  const H = N >> 2, half = N >> 1, fft = WL.getFFT(N), w = WL.hannP(N);
  const len = chans[0].length, nCh = chans.length, pad = N;
  const outs = chans.map(() => new Float64Array(len + 2 * pad)), norm = new Float64Array(len + 2 * pad);
  // channels go two per complex FFT (one in the real part, one in the imaginary part): the gains are real and
  // symmetric, so they act on both at once, and each channel's spectrum is split out only for the magnitudes
  const nP = (nCh + 1) >> 1, re = Array.from({ length: nP }, () => new Float64Array(N)), im = Array.from({ length: nP }, () => new Float64Array(N));
  const mag = new Float64Array(half + 1), g = new Float32Array(half + 1);
  const nF = Math.ceil((len + N) / H);
  for (let f = 0; f < nF; f++) {
    const start = f * H - half;
    for (let p = 0; p < nP; p++) {
      const x = chans[2 * p], y = chans[2 * p + 1], r = re[p], q = im[p];
      for (let i = 0; i < N; i++) { const s = start + i, ok = s >= 0 && s < len; r[i] = ok ? x[s] * w[i] : 0; q[i] = ok && y ? y[s] * w[i] : 0; }
      fft(r, q);
    }
    for (let k = 0; k <= half; k++) {
      let e = 0; const kk = (N - k) % N;
      for (let p = 0; p < nP; p++) {
        const r = re[p], q = im[p];
        if (2 * p + 1 < nCh) { const ar = r[k] + r[kk], ai = q[k] - q[kk], br = q[k] + q[kk], bi = r[kk] - r[k]; e += 0.25 * (ar * ar + ai * ai + br * br + bi * bi); }
        else e += r[k] * r[k] + q[k] * q[k];
      }
      mag[k] = Math.sqrt(e / nCh);
    }
    g.fill(1);
    gainFn(f, (start + half) / sr, mag, g);
    for (let p = 0; p < nP; p++) {
      const r = re[p], q = im[p], two = 2 * p + 1 < nCh;
      for (let k = 0; k <= half; k++) { r[k] *= g[k]; q[k] *= g[k]; if (k > 0 && k < half) { r[N - k] *= g[k]; q[N - k] *= g[k]; } }
      for (let i = 0; i < N; i++) q[i] = -q[i];
      fft(r, q);
      const o = outs[2 * p], o2 = two ? outs[2 * p + 1] : null;
      for (let i = 0; i < N; i++) { const s = start + i + pad; o[s] += (r[i] / N) * w[i]; if (two) o2[s] -= (q[i] / N) * w[i]; }
    }
    for (let i = 0; i < N; i++) norm[start + i + pad] += w[i] * w[i];
    if ((f & 127) === 127) { if (onProgress) onProgress(f / nF); await tick(); }
  }
  return outs.map(o => { const y = new Float32Array(len); for (let i = 0; i < len; i++) { const nv = norm[i + pad]; y[i] = nv > 1e-6 ? o[i + pad] / nv : 0; } return y; });
}
// two passes: collect mono magnitudes first (for filters that look ahead), then apply a gain matrix
async function stftMagnitudes(chans, sr, N) {
  const H = N >> 2, half = N >> 1, fft = WL.getFFT(N), w = WL.hannP(N), len = chans[0].length, nCh = chans.length;
  const nF = Math.ceil((len + N) / H), M = new Float32Array(nF * (half + 1));
  const re = new Float64Array(N), im = new Float64Array(N);
  for (let f = 0; f < nF; f++) {
    const start = f * H - half;
    for (let c = 0; c < nCh; c++) {
      const x = chans[c];
      for (let i = 0; i < N; i++) { const s = start + i; re[i] = s >= 0 && s < len ? x[s] * w[i] : 0; im[i] = 0; }
      fft(re, im);
      for (let k = 0; k <= half; k++) M[f * (half + 1) + k] += (re[k] * re[k] + im[k] * im[k]) / nCh;
    }
    if ((f & 255) === 255) await tick();
  }
  for (let i = 0; i < M.length; i++) M[i] = Math.sqrt(M[i]);
  return { M, nF, bins: half + 1, H };
}
function frameN(sr, dur) { const base = dur <= 6 ? 1024 : 2048; return Math.round(base * sr / 44100) >= 1536 ? 2048 : 1024; }

/* ---------- 1. Clean-up for export (driven by the asset checks) ---------- */
async function cleanup(src, an, opt) {
  const sr = src.sampleRate, X = an.X;
  let ch = chansOf(src);
  const report = [];
  // find clipped runs on the original samples (|x| at full scale for 3+ samples)
  const runs = ch.map(c => { const r = []; for (let i = 0; i < c.length; i++) { if (Math.abs(c[i]) < 0.9999) continue; let j = i; while (j + 1 < c.length && Math.abs(c[j + 1]) >= 0.9999 && Math.sign(c[j + 1]) === Math.sign(c[i])) j++; if (j - i + 1 >= 3) r.push([i, j]); i = j; } return r; });
  // DC offset
  let dcMax = 0;
  for (const c of ch) { let s = 0; for (let i = 0; i < c.length; i++) s += c[i]; const m = s / c.length; dcMax = Math.max(dcMax, Math.abs(m)); for (let i = 0; i < c.length; i++) c[i] -= m; }
  if (dcMax > 0.002) report.push("Removed DC offset of " + db(dcMax).toFixed(0) + " dBFS");
  // rebuild clipped runs with a cubic Hermite through the neighbours (restores the overshoot)
  let fixed = 0;
  ch.forEach((c, ci) => {
    for (const [i, j] of runs[ci]) {
      if (i < 2 || j + 2 >= c.length) continue;
      const p0 = c[i - 1], p1 = c[j + 1], m0 = c[i - 1] - c[i - 2], m1 = c[j + 2] - c[j + 1], L = j - i + 2;
      for (let k = i; k <= j; k++) { const t = (k - i + 1) / L, t2 = t * t, t3 = t2 * t; c[k] = (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * m0 * L + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * m1 * L; }
      fixed += j - i + 1;
    }
  });
  if (fixed) report.push("Rebuilt " + fixed + " clipped samples");
  // trim silence (-60 dBFS), keep 5 ms before and 30 ms after
  const thr = 0.001, len = ch[0].length;
  let li = len, ti = -1;
  for (let i = 0; i < len; i++) if (ch.some(c => Math.abs(c[i]) > thr)) { li = i; break; }
  for (let i = len - 1; i >= 0; i--) if (ch.some(c => Math.abs(c[i]) > thr)) { ti = i; break; }
  if (ti > li) {
    const a = Math.max(0, li - Math.round(0.005 * sr)), b = Math.min(len, ti + 1 + Math.round(0.03 * sr));
    if (a > 0 || b < len) { report.push("Trimmed " + (a / sr * 1000).toFixed(0) + " ms at the start and " + ((len - b) / sr * 1000).toFixed(0) + " ms at the end"); ch = ch.map(c => c.slice(a, b)); }
  }
  // fades: short fade-in always; a longer fade-out when the tail was cut
  const n = ch[0].length, fin = Math.min(n >> 2, Math.round(0.002 * sr));
  for (const c of ch) for (let i = 0; i < fin; i++) c[i] *= 0.5 - 0.5 * Math.cos(Math.PI * i / fin);
  let pk = peakOf(ch), endRms = 0; { const k = Math.min(n, Math.round(0.005 * sr)); let s = 0; for (const c of ch) for (let i = n - k; i < n; i++) s += c[i] * c[i]; endRms = Math.sqrt(s / (k * ch.length)); }
  const cut = endRms > pk * undb(-35);
  const fout = Math.min(n >> 1, Math.round((cut ? Math.min(0.08, 0.15 * n / sr) : 0.002) * sr));
  for (const c of ch) for (let i = 0; i < fout; i++) c[n - 1 - i] *= 0.5 - 0.5 * Math.cos(Math.PI * i / fout);
  if (cut) report.push("Faded the cut tail over " + (fout / sr * 1000).toFixed(0) + " ms");
  // identical channels -> mono
  if (opt.mono && ch.length === 2) { let same = true; for (let i = 0; i < n && same; i++) if (Math.abs(ch[0][i] - ch[1][i]) > 1e-5) same = false; if (same) { ch = [ch[0]]; report.push("Folded identical channels to mono"); } }
  // peak normalise
  const target = undb(opt.peakDb);
  pk = peakOf(ch);
  if (pk > 0 && Math.abs(db(pk) - opt.peakDb) > 0.1) { scaleTo(ch, target); report.push("Normalised peak from " + db(pk).toFixed(1) + " to " + opt.peakDb.toFixed(1) + " dBFS"); }
  if (!report.length) report.push("Nothing to fix");
  return { outputs: [{ suffix: "clean", buf: mkBuf(sr, ch) }], report };
}

/* ---------- 2. De-harsh: resonance suppression gated by the harshness model ---------- */
async function deharsh(src, an, opt, onProgress) {
  const sr = src.sampleRate, F = an.F, cur = an.cur, ch = chansOf(src);
  const N = frameN(sr, src.duration), half = N >> 1, binHz = sr / N;
  const hf = cur.raw.hfac, sh = cur.raw.sharp, dtF = F.dt, nFr = F.nF;
  const at = (arr, t) => arr[Math.min(nFr - 1, Math.max(0, Math.round(t / dtF - 0.5)))];
  // log-frequency smoothing ranges (±1/6 octave) and weights
  const lo = new Int32Array(half + 1), hi = new Int32Array(half + 1), wRes = new Float32Array(half + 1), wTilt = new Float32Array(half + 1);
  for (let k = 0; k <= half; k++) {
    const f = Math.max(binHz, k * binHz);
    lo[k] = Math.max(0, Math.floor(f * Math.pow(2, -1 / 6) / binHz)); hi[k] = Math.min(half, Math.ceil(f * Math.pow(2, 1 / 6) / binHz));
    const z = Math.log2(f / 800) / Math.log2(10000 / 800);
    wRes[k] = z <= 0 || z >= 1 ? 0 : 0.5 - 0.5 * Math.cos(2 * Math.PI * z);
    wTilt[k] = f < 3000 ? 0 : Math.min(1, Math.log2(f / 3000) / Math.log2(10000 / 3000));
  }
  const Ld = new Float64Array(half + 1), P = new Float64Array(half + 2), state = new Float32Array(half + 1).fill(0), gdb = new Float32Array(half + 1);
  const relA = 1 - Math.exp(-(N / 4) / sr / 0.06);
  // gate: absolute (annoyance scale), relative (this file's own P50..P95 range), or always open
  const mode = opt.mode || "absolute";
  const gated = a => { const o = []; for (let m = 0; m < nFr; m++) if (cur.gate[m]) o.push(a[m]); return o.sort((x, y) => x - y); };
  const qs = (s, p) => s.length ? s[Math.floor(p * (s.length - 1))] : 0;
  const hs = gated(hf), ss = gated(sh);
  const gLo = mode === "relative" ? qs(hs, 0.5) : opt.threshold, gW = mode === "relative" ? Math.max(0.03, qs(hs, 0.9) - gLo) : 0.6;
  const S0 = mode === "absolute" ? 1.75 : Math.min(1.75, qs(ss, 0.5));
  // detector: the harshness model (default), or the level in the same frequency region, as a conventional
  // dynamic EQ would use; the level gate always opens between this file's own P50 and P90 (in dB)
  const byLevel = opt.sidechain === "level" && mode !== "always";
  let lev = null, lLo = 0, lW = 1;
  if (byLevel) {
    const A = await stftMagnitudes(ch, sr, N); lev = new Float32Array(A.nF);
    for (let f = 0; f < A.nF; f++) { let e = 0; for (let k = 0; k <= half; k++) { const m = A.M[f * A.bins + k]; e += (wRes[k] + wTilt[k]) * m * m; } lev[f] = 10 * Math.log10(e + 1e-20); }
    const top = lev.reduce((a, b) => Math.max(a, b), -Infinity), snd = Array.from(lev).filter(v => v > top - 50).sort((a, b) => a - b);
    lLo = qs(snd, 0.5); lW = Math.max(1, qs(snd, 0.9) - lLo);
  }
  let gateSum = 0, gateN = 0, maxCut = 0, cutSum = 0, cutN = 0;
  const trace = opt.trace ? { t: [], gate: [] } : null;
  const outs = await stftApply(ch, sr, N, (f, t, mag, g) => {
    const h = at(hf, t), S = at(sh, t);
    const gate = mode === "always" ? 1 : byLevel ? Math.max(0, Math.min(1, (lev[Math.min(lev.length - 1, f)] - lLo) / lW)) : Math.max(0, Math.min(1, (h - gLo) / gW));
    gateSum += gate; gateN++; if (trace) { trace.t.push(t); trace.gate.push(gate); }
    for (let k = 0; k <= half; k++) Ld[k] = 20 * Math.log10(mag[k] + 1e-9);
    P[0] = 0; for (let k = 0; k <= half; k++) P[k + 1] = P[k] + Ld[k];
    for (let k = 0; k <= half; k++) {
      const sm = (P[hi[k] + 1] - P[lo[k]]) / (hi[k] - lo[k] + 1);
      const excess = Math.min(12, Math.max(0, Ld[k] - sm - 2));
      let target = -opt.amount * gate * (wRes[k] * excess + wTilt[k] * Math.max(0, S - S0) * 4);
      target = Math.max(target, -opt.maxCut);
      // instant attack, 60 ms release
      state[k] = target < state[k] ? target : state[k] + (target - state[k]) * relA;
    }
    let fs = 0, fw = 0;
    for (let k = 0; k <= half; k++) {
      const a = state[Math.max(0, k - 1)], b = state[k], c = state[Math.min(half, k + 1)]; gdb[k] = (a + b + c) / 3; if (-gdb[k] > maxCut) maxCut = -gdb[k]; g[k] = undb(gdb[k]);
      if (wRes[k] > 0 || wTilt[k] > 0) { const e = mag[k] * mag[k]; fs -= gdb[k] * e; fw += e; }
    }
    if (fw > 0) { cutSum += fs / fw; cutN++; }
  }, onProgress);
  const report = [(mode === "always" ? "Always on" : byLevel ? "Level detector (" + lW.toFixed(1) + " dB range above this file's median level)" : (mode === "relative" ? "Relative gate (harshness " + gLo.toFixed(2) + " to " + (gLo + gW).toFixed(2) + ")" : "Absolute gate")) + (mode === "always" ? "" : ": open " + Math.round(100 * gateSum / Math.max(1, gateN)) + "% of the time"),
    "Average cut above 0.8 kHz " + (cutSum / Math.max(1, cutN)).toFixed(1) + " dB, deepest " + maxCut.toFixed(1) + " dB"];
  // what was removed: dry minus wet before loudness matching, raised for listening
  let removed = null;
  if (opt.delta) {
    const dry = chansOf(src), d = dry.map((c, i) => { const o = new Float32Array(c.length); for (let n = 0; n < c.length; n++) o[n] = c[n] - outs[i][n]; return o; });
    const lr = lufsOf(d, sr), ld = lufsOf(dry, sr);
    if (lr > -69 && ld - lr <= 40 && peakOf(d) > 0) { scaleTo(d, undb(-6)); removed = mkBuf(sr, d); report.push("Removed part is " + (ld - lr).toFixed(0) + " LU below the original (raised to −6 dBFS peak for listening)"); }
    else report.push(lr > -69 ? "Removed part is " + (ld - lr).toFixed(0) + " LU below the original, too quiet to hear, so it is not rendered" : "Nothing measurable was removed");
  }
  finish(src, outs, opt.match ? "loudness" : "none", report);
  const outputs = [{ suffix: byLevel ? "deharsh-level" : "deharsh", buf: mkBuf(sr, outs) }];
  if (removed) outputs.push({ suffix: (byLevel ? "deharsh-level" : "deharsh") + "-removed", buf: removed });
  return { outputs, report, stats: { open: gateSum / Math.max(1, gateN), avgCut: cutSum / Math.max(1, cutN), maxCut }, trace };
}

/* ---------- 3. Layer remix: Wiener masks from the NMF layers ---------- */
function layerMasker(an, sr, N, gainsDb) {
  const X = an.X, raw = X.nmfRaw, B = X.melCenters.length, half = N >> 1, binHz = sr / N;
  const K = raw.W.length, gLin = new Float32Array(K).fill(1);
  X.layers.forEach((l, i) => { for (const k of l.comps) gLin[k] = undb(gainsDb[i] || 0); });
  const mel = f => 2595 * Math.log10(1 + f / 700);
  const mc = X.melCenters.map(mel);
  const pos = new Float32Array(half + 1);
  for (let k = 0; k <= half; k++) {
    const m = mel(k * binHz);
    let b = 0; while (b < B - 1 && mc[b + 1] < m) b++;
    pos[k] = m <= mc[0] ? 0 : m >= mc[B - 1] ? B - 1 : b + (m - mc[b]) / (mc[b + 1] - mc[b]);
  }
  const times = raw.times, T = times.length, C = new Float64Array(K * B);
  return (f, t, mag, g) => {
    let j = 0; while (j < T - 1 && times[j + 1] < t) j++;
    const u = T > 1 && t > times[j] ? Math.min(1, (t - times[j]) / Math.max(1e-9, times[Math.min(T - 1, j + 1)] - times[j])) : 0;
    for (let k = 0; k < K; k++) {
      const h = raw.H[k][j] * (1 - u) + raw.H[k][Math.min(T - 1, j + 1)] * u;
      for (let b = 0; b < B; b++) C[k * B + b] = raw.W[k][b] * h;
    }
    for (let i = 0; i <= half; i++) {
      const p = pos[i], b0 = Math.floor(p), b1 = Math.min(B - 1, b0 + 1), fr = p - b0;
      let num = 0, den = 1e-20;
      for (let k = 0; k < K; k++) { const c = C[k * B + b0] * (1 - fr) + C[k * B + b1] * fr, e = c * c; num += e * gLin[k]; den += e; }
      g[i] = num / den;
    }
  };
}
async function layerMix(src, an, opt, onProgress) {
  const sr = src.sampleRate, N = frameN(sr, src.duration);
  if (!an.X || !an.X.nmfRaw || !an.X.nmfRaw.W.length) return { outputs: [], report: ["No layers found"] };
  const outs = await stftApply(chansOf(src), sr, N, layerMasker(an, sr, N, opt.gains), onProgress);
  const report = an.X.layers.map((l, i) => l.name + " " + (l.hz >= 1000 ? (l.hz / 1000).toFixed(1) + " kHz" : Math.round(l.hz) + " Hz") + ": " + ((opt.gains[i] || 0) >= 0 ? "+" : "") + (opt.gains[i] || 0).toFixed(0) + " dB");
  finish(src, outs, opt.match ? "loudness" : "none", report);
  return { outputs: [{ suffix: "layers", buf: mkBuf(sr, outs) }], report };
}

/* ---------- 4. Attack and body: median-filter HPSS (Fitzgerald 2010), soft masks ---------- */
// running median over a sorted window: each step swaps the outgoing value for the incoming one and moves it into
// place, so there is no allocation or full sort per sample (same values as sorting every window; edges repeat)
function medianFilter1D(src, off, stride, n, half, out, win) {
  const w = 2 * half + 1, at = k => src[off + Math.min(n - 1, Math.max(0, k)) * stride];
  for (let k = -half; k <= half; k++) win[k + half] = at(k);
  win.subarray(0, w).sort(); out[0] = win[half];
  for (let i = 1; i < n; i++) {
    const old = at(i - half - 1), nv = at(i + half);
    if (old === nv) { out[i] = win[half]; continue; }
    let lo = 0, hi = w - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (win[m] < old) lo = m + 1; else hi = m; }
    let j = lo;
    if (nv > old) { while (j + 1 < w && win[j + 1] < nv) { win[j] = win[j + 1]; j++; } }
    else { while (j > 0 && win[j - 1] > nv) { win[j] = win[j - 1]; j--; } }
    win[j] = nv; out[i] = win[half];
  }
}
async function hpss(src, an, opt, onProgress) {
  const sr = src.sampleRate, ch = chansOf(src), N = frameN(sr, src.duration);
  const { M, nF, bins } = await stftMagnitudes(ch, sr, N);
  const Hm = new Float32Array(M.length), Pm = new Float32Array(M.length), tmp = new Float32Array(Math.max(nF, bins)), win = new Float32Array(17);
  for (let k = 0; k < bins; k++) { medianFilter1D(M, k, bins, nF, 8, tmp, win); for (let i = 0; i < nF; i++) Hm[i * bins + k] = tmp[i]; if ((k & 255) === 255) await tick(); }
  for (let i = 0; i < nF; i++) { medianFilter1D(M, i * bins, 1, bins, 8, Pm.subarray(i * bins, (i + 1) * bins), win); if ((i & 511) === 511) await tick(); }
  const gA = undb(opt.attackDb), gB = undb(opt.bodyDb);
  let pShare = 0, tot = 0;
  for (let i = 0; i < M.length; i++) { const h = Hm[i] * Hm[i], p = Pm[i] * Pm[i]; pShare += p / (h + p + 1e-20) * M[i] * M[i]; tot += M[i] * M[i]; }
  const outs = await stftApply(ch, sr, N, (f, t, mag, g) => {
    const o = Math.min(nF - 1, f) * bins;
    for (let k = 0; k < bins; k++) { const h = Hm[o + k] * Hm[o + k], p = Pm[o + k] * Pm[o + k], d = h + p + 1e-20; g[k] = gB * h / d + gA * p / d; }
  }, onProgress);
  const report = ["Percussive share of energy " + Math.round(100 * pShare / Math.max(1e-20, tot)) + "%", "Attack " + (opt.attackDb >= 0 ? "+" : "") + opt.attackDb + " dB, body " + (opt.bodyDb >= 0 ? "+" : "") + opt.bodyDb + " dB"];
  finish(src, outs, opt.match ? "loudness" : "none", report);
  return { outputs: [{ suffix: "attack-body", buf: mkBuf(sr, outs) }], report };
}

/* ---------- 5. Tension shaper: gain and brightness automation from the tension curve ---------- */
async function tensionShape(src, an, opt, onProgress) {
  const sr = src.sampleRate, F = an.F, cur = an.cur, ch = chansOf(src);
  const c5 = cur.curves.c5, c1 = cur.curves.c1, n = F.nF, dtF = F.dt;
  const T = new Float32Array(n);
  for (let m = 0; m < n; m++) T[m] = cur.gate[m] ? Math.max(0, Math.min(1, (c5[m] - 0.4 * c1[m]) / 0.6)) : 0.5;
  const Ts = WL.movMean(T, Math.max(1, Math.round(0.5 / dtF)));
  const sorted = Array.from(Ts).filter((_, m) => cur.gate[m]).sort((a, b) => a - b), med = sorted.length ? sorted[sorted.length >> 1] : 0.5;
  const N = frameN(sr, src.duration), half = N >> 1, binHz = sr / N;
  const shelf = new Float32Array(half + 1); for (let k = 0; k <= half; k++) { const f = Math.max(1, k * binHz); shelf[k] = 1 / (1 + Math.pow(2500 / f, 2)); }
  const pk0 = peakOf(ch);
  let gMin = 0, gMax = 0;
  const outs = await stftApply(ch, sr, N, (f, t, mag, g) => {
    const v = Ts[Math.min(n - 1, Math.max(0, Math.round(t / dtF - 0.5)))] - med;
    const gainDb = opt.contrast * v * 12, tiltDb = opt.bright * v * 8;
    gMin = Math.min(gMin, gainDb); gMax = Math.max(gMax, gainDb);
    for (let k = 0; k <= half; k++) g[k] = undb(gainDb + tiltDb * shelf[k]);
  }, onProgress);
  void pk0;
  const report = ["Gain automation from " + gMin.toFixed(1) + " to +" + gMax.toFixed(1) + " dB around the median tension", "Brightness follows tension by up to " + Math.abs(opt.bright * 4).toFixed(1) + " dB"];
  finish(src, outs, "peak", report);
  return { outputs: [{ suffix: "tension", buf: mkBuf(sr, outs) }], report };
}

/* ---------- 6. Loop maker: most similar start and end, zero crossings, equal-power seam ---------- */
async function loopMaker(src, an, opt) {
  const sr = src.sampleRate, F = an.F, n = F.nF, dt = F.dt, B = WL.NMEL, ch = chansOf(src), len = ch[0].length;
  const R = an.R, D = 20;
  const feat = new Float32Array(n * (D + 1));
  for (let m = 0; m < n; m++) {
    for (let d = 0; d < D; d++) feat[m * (D + 1) + d] = 0.5 * (F.melDb[m * B + 2 * d] + F.melDb[m * B + 2 * d + 1]);
    feat[m * (D + 1) + D] = 3 * 10 * Math.log10(F.kz[m] + 1e-12);
  }
  const ctx = Math.max(2, Math.round(0.06 / dt)), minLen = opt.minLen * F.dur;
  const s0 = an.X ? an.X.sound[0] : 0, s1 = an.X ? an.X.sound[1] : F.dur;
  let candA = [], candB = [], aligned = false;
  if (R && R.reliable && R.downTimes.length >= 4) { candA = R.downTimes.filter(t => t < F.dur * 0.4); candB = R.downTimes.filter(t => t > F.dur * 0.4); aligned = candA.length > 0 && candB.length > 0; }
  if (!aligned) {
    const step = Math.max(1, Math.round(0.01 / dt));
    for (let m = Math.round(Math.max(s0 + 0.05, opt.xfadeMs / 1000 + ctx * dt) / dt); m < n * 0.35; m += step) candA.push(m * dt);
    for (let m = Math.round(n * 0.5); m < Math.min(n - ctx, Math.round(s1 / dt)); m += step) candB.push(m * dt);
  }
  let best = null;
  for (const ta of candA) for (const tb of candB) {
    if (tb - ta < minLen) continue;
    const ma = Math.round(ta / dt), mb = Math.round(tb / dt);
    let s = 0, c = 0;
    for (let k = -ctx; k <= ctx; k++) {
      const a = ma + k, b = mb + k; if (a < 0 || b < 0 || a >= n || b >= n) continue;
      for (let d = 0; d <= D; d++) { const v = feat[a * (D + 1) + d] - feat[b * (D + 1) + d]; s += v * v; } c++;
    }
    const score = c ? s / c : Infinity;
    if (!best || score < best.score) best = { score, ta, tb };
  }
  if (!best) return { outputs: [], report: ["File too short for the requested loop length"] };
  // refine to rising zero crossings within ±4 ms
  const mono = i => { let v = 0; for (const c of ch) v += c[i]; return v / ch.length; };
  const zc = t => { const c = Math.round(t * sr), r = Math.round(0.004 * sr); let bi = c, bd = Infinity; for (let i = Math.max(1, c - r); i < Math.min(len - 1, c + r); i++) if (mono(i - 1) <= 0 && mono(i) > 0) { const dd = Math.abs(i - c); if (dd < bd) { bd = dd; bi = i; } } return bi; };
  let a = zc(best.ta), b = zc(best.tb);
  let xf = Math.min(Math.round(opt.xfadeMs / 1000 * sr), a, Math.floor((b - a) / 3));
  const L = b - a, out = ch.map(() => new Float32Array(L));
  ch.forEach((c, ci) => {
    const o = out[ci];
    for (let i = 0; i < L; i++) o[i] = c[a + i];
    for (let i = 0; i < xf; i++) { const u = (i + 0.5) / xf, j = L - xf + i; o[j] = c[a + j] * Math.cos(u * Math.PI / 2) + c[a - xf + i] * Math.sin(u * Math.PI / 2); }
  });
  const rep = ["Loop " + (a / sr).toFixed(3) + "–" + (b / sr).toFixed(3) + " s (" + (L / sr).toFixed(2) + " s)" + (aligned ? ", on downbeats" : ""), "Equal-power seam over " + (xf / sr * 1000).toFixed(0) + " ms", "Similarity distance " + best.score.toFixed(1)];
  return { outputs: [{ suffix: "loop", buf: mkBuf(sr, out) }], report: rep };
}

/* ---------- 7. Variations: layer balance, grain order and pitch, for repeated game sounds ---------- */
function resample(ch, ratio) {
  const n = Math.max(2, Math.floor(ch.length / ratio)), o = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = i * ratio, j = Math.floor(x), t = x - j;
    const p0 = ch[Math.max(0, j - 1)], p1 = ch[j] || 0, p2 = ch[Math.min(ch.length - 1, j + 1)] || 0, p3 = ch[Math.min(ch.length - 1, j + 2)] || 0;
    o[i] = p1 + 0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)));
  }
  return o;
}
function shuffleGrains(chans, sr, ev, r) {
  const marks = ev.grains.map(t => Math.round(t * sr)).concat([Math.round(ev.tEnd * sr)]);
  const segs = [];
  for (let i = 0; i + 1 < marks.length; i++) if (marks[i + 1] - marks[i] > 64) segs.push([marks[i], marks[i + 1]]);
  if (segs.length < 3) return false;
  const order = segs.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); const t = order[i]; order[i] = order[j]; order[j] = t; }
  const xf = Math.round(0.005 * sr);
  for (const c of chans) {
    const src = c.slice(), start = segs[0][0];
    let pos = start;
    for (const oi of order) {
      const [a, b] = segs[oi];
      for (let i = 0; i < b - a; i++) {
        const v = src[a + i], k = pos + i; if (k >= c.length) break;
        if (i < xf && pos > start) { const u = i / xf; c[k] = c[k] * (1 - u) + v * u; } else c[k] = v;
      }
      pos += b - a;
    }
  }
  return true;
}
async function variations(src, an, opt, onProgress) {
  const sr = src.sampleRate, N = frameN(sr, src.duration), outputs = [], report = [];
  const X = an.X;
  for (let v = 1; v <= opt.count; v++) {
    const r = rng(1000 + v * 7919);
    const gains = X && X.layers ? X.layers.map(() => (r() * 2 - 1) * opt.layerDb) : [];
    let ch = gains.length ? await stftApply(chansOf(src), sr, N, layerMasker(an, sr, N, gains)) : chansOf(src);
    let shuffled = 0;
    if (opt.grains && X) for (const ev of X.events) if (ev.facture === "iterative" && ev.grains.length >= 3 && shuffleGrains(ch, sr, ev, r)) shuffled++;
    const semis = (r() * 2 - 1) * opt.pitch;
    if (Math.abs(semis) > 0.01) ch = ch.map(c => resample(c, Math.pow(2, semis / 12)));
    finish(src, ch, "none", []);
    outputs.push({ suffix: "var" + v, buf: mkBuf(sr, ch) });
    report.push("Variation " + v + ": pitch " + (semis >= 0 ? "+" : "") + semis.toFixed(2) + " st" + (gains.length ? ", layers " + gains.map(g => (g >= 0 ? "+" : "") + g.toFixed(1)).join("/") + " dB" : "") + (shuffled ? ", grains reordered in " + shuffled + " event" + (shuffled > 1 ? "s" : "") : ""));
    if (onProgress) onProgress(v / opt.count);
    await tick();
  }
  return { outputs, report };
}

/* ---------- WAV encoder (16-bit PCM or 32-bit float) ---------- */
function encodeWav(buf, float32) {
  const nCh = buf.numberOfChannels, n = buf.length, sr = buf.sampleRate, bps = float32 ? 4 : 2;
  const dataLen = n * nCh * bps, ab = new ArrayBuffer(44 + dataLen), v = new DataView(ab);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + dataLen, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, float32 ? 3 : 1, true); v.setUint16(22, nCh, true); v.setUint32(24, sr, true); v.setUint32(28, sr * nCh * bps, true);
  v.setUint16(32, nCh * bps, true); v.setUint16(34, bps * 8, true); str(36, "data"); v.setUint32(40, dataLen, true);
  const chs = []; for (let c = 0; c < nCh; c++) chs.push(buf.getChannelData(c));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < nCh; c++) {
    const s = chs[c][i];
    if (float32) v.setFloat32(o, s, true); else v.setInt16(o, Math.max(-32768, Math.min(32767, Math.round(s * 32767))), true);
    o += bps;
  }
  return ab;
}


/* ---------- 8. Event-masked tools: processing that follows the sound's events ----------
   An EQ, dynamic or not, can only turn existing frequencies up or down. These three do what it cannot: add a reverb
   tail that starts after each hit's peak (new energy, smeared in time), saturate only the hits (new harmonics), and
   change how long each event rings (its decay, relative to its own peak). The events come from the sound-object
   analysis (start, peak, end of decay). */
function eventsOf(an, sr, n) {
  const ev = (an.X && an.X.events) || [];
  return ev.map(e => ({ a: Math.max(0, Math.round(e.t0 * sr)), p: Math.max(0, Math.round(e.peakT * sr)), e: Math.min(n, Math.round(Math.max(e.tEnd || e.t1, e.t1) * sr)) }))
    .filter(e => e.e > e.a).sort((x, y) => x.a - y.a);
}
// a 0..1 mask with linear ramps of r samples; spans: [[start, end], ...]
function spanMask(n, spans, r) {
  const m = new Float32Array(n);
  for (const [a, b] of spans) {
    const s0 = Math.max(0, a), s1 = Math.min(n, b); if (s1 <= s0) continue;
    for (let i = Math.max(0, s0 - r); i < Math.min(n, s1 + r); i++) { const u = i < s0 ? 1 - (s0 - i) / r : i >= s1 ? 1 - (i - s1 + 1) / r : 1; if (u > m[i]) m[i] = u; }
  }
  return m;
}
// stereo-friendly reverb: four damped combs and two all-passes per channel (Schroeder), comb lengths offset per channel
function reverb(x, sr, t60, toneHz, chan) {
  const base = [1557, 1617, 1491, 1422].map(l => Math.round((l + chan * 23) * sr / 44100)), ap = [225, 556].map(l => Math.round((l + chan * 17) * sr / 44100));
  const n = x.length, y = new Float32Array(n), damp = Math.exp(-2 * Math.PI * toneHz / sr);
  for (const L of base) {
    const g = Math.pow(10, -3 * L / (t60 * sr)), buf = new Float32Array(L); let idx = 0, lp = 0;
    for (let i = 0; i < n; i++) { const out = buf[idx]; lp = out * (1 - damp) + lp * damp; buf[idx] = x[i] + lp * g; idx = idx + 1 === L ? 0 : idx + 1; y[i] += out / base.length; }
  }
  for (const L of ap) {
    const buf = new Float32Array(L); let idx = 0;
    for (let i = 0; i < n; i++) { const b = buf[idx], v = y[i] + 0.5 * b; buf[idx] = v; y[i] = b - 0.5 * v; idx = idx + 1 === L ? 0 : idx + 1; }
  }
  return y;
}
async function tailVerb(src, an, opt) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length, ev = eventsOf(an, sr, n);
  if (!ev.length) return { outputs: [{ suffix: "tailverb", buf: mkBuf(sr, ch) }], report: ["No events found: nothing was sent to the reverb"] };
  const hold = Math.round(opt.holdMs / 1000 * sr), r = Math.round(0.02 * sr);
  // send: each event from its peak (plus the hold) to the end of its decay; duck: the wet signal leaves each attack dry
  const send = spanMask(n, ev.map(e => [e.p + hold, e.e]), r), duck = opt.dryAttacks ? spanMask(n, ev.map(e => [e.a, e.p + Math.round(0.01 * sr)]), Math.round(0.005 * sr)) : null;
  const wetG = undb(opt.wetDb), tail = Math.round(Math.min(6, opt.t60) * sr * 0.6), len = n + tail;
  const outs = ch.map((x, c) => {
    const xin = new Float32Array(len); for (let i = 0; i < n; i++) xin[i] = x[i] * send[i];
    const w = reverb(xin, sr, opt.t60, opt.toneHz, c), o = new Float32Array(len);
    for (let i = 0; i < len; i++) o[i] = (i < n ? x[i] : 0) + w[i] * wetG * (duck && i < n ? 1 - duck[i] : 1);
    return o;
  });
  // fade the added tail out over its last 30%, so the file ends cleanly
  const f0 = n + Math.round(tail * 0.7); for (const o of outs) for (let i = f0; i < len; i++) o[i] *= 1 - (i - f0) / (len - f0);
  let on = 0; for (let i = 0; i < n; i++) on += send[i] > 0.5 ? 1 : 0;
  const report = ["Reverb sent from " + ev.length + " event tail" + (ev.length === 1 ? "" : "s") + ", " + Math.round(100 * on / n) + "% of the time" + (opt.dryAttacks ? "; attacks kept dry" : ""), "Decay " + opt.t60 + " s, " + (len - n > 0 ? (tail / sr).toFixed(1) + " s added at the end" : "")];
  finish(src, outs, "peak", report);
  return { outputs: [{ suffix: "tailverb", buf: mkBuf(sr, outs) }], report };
}
async function driveHits(src, an, opt) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length, ev = eventsOf(an, sr, n);
  if (!ev.length) return { outputs: [{ suffix: "drive", buf: mkBuf(sr, ch) }], report: ["No events found: nothing was driven"] };
  const at = Math.round(0.03 * sr), spans = ev.map(e => opt.where === "attack" ? [e.a, e.p + at] : opt.where === "body" ? [e.p + at, e.e] : [e.a, e.e]);
  const m = spanMask(n, spans, Math.round(0.005 * sr)), k = undb(opt.driveDb), mix = opt.mix / 100, pk = Math.max(1e-6, peakOf(ch));
  // the saturated signal is brought back to the dry signal's level every few milliseconds, so what changes is the
  // waveform's shape (new harmonics), not its loudness contour (which a dynamic EQ or a compressor could copy)
  const W = Math.max(16, Math.round(0.004 * sr));
  const outs = ch.map(x => {
    const d = new Float32Array(n); for (let i = 0; i < n; i++) d[i] = Math.tanh(k * x[i] / pk) * pk;
    const cx = new Float64Array(n + 1), cd = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) { cx[i + 1] = cx[i] + x[i] * x[i]; cd[i + 1] = cd[i] + d[i] * d[i]; }
    const o = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = Math.max(0, i - W), b = Math.min(n, i + W), ex = cx[b] - cx[a], ed = cd[b] - cd[a], g = ed > 1e-20 ? Math.sqrt(ex / ed) : 0, w = mix * m[i];
      o[i] = x[i] * (1 - w) + d[i] * g * w;
    }
    return o;
  });
  let on = 0; for (let i = 0; i < n; i++) on += m[i] > 0.5 ? 1 : 0;
  const report = ["Saturated the " + (opt.where === "attack" ? "attacks" : opt.where === "body" ? "bodies" : "whole events") + " of " + ev.length + " event" + (ev.length === 1 ? "" : "s") + " (" + Math.round(100 * on / n) + "% of the time)", "Drive +" + opt.driveDb + " dB, mix " + opt.mix + "%"];
  finish(src, outs, opt.match ? "loudness" : "peak", report);
  return { outputs: [{ suffix: "drive", buf: mkBuf(sr, outs) }], report };
}
async function ringTime(src, an, opt, onProgress) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length, ev = eventsOf(an, sr, n);
  if (!ev.length) return { outputs: [{ suffix: "ring", buf: mkBuf(sr, ch) }], report: ["No events found: nothing was changed"] };
  const N = frameN(sr, src.duration), { M, nF, bins, H } = await stftMagnitudes(ch, sr, N), binHz = sr / N, fr = t => Math.round(t / H);
  // each event's decay: from its peak to the next event's start (or the end of the file); the reference is the
  // loudest frame of each bin around the peak, and every later frame is raised or lowered so the decay stretches
  const ref = new Float32Array(nF * bins).fill(0), owner = new Int32Array(nF).fill(-1);
  ev.forEach((e, i) => {
    const p0 = Math.max(0, fr(e.p) - 1), p1 = Math.min(nF, fr(e.p) + 3), end = Math.min(nF, i + 1 < ev.length ? fr(ev[i + 1].a) : nF);
    const r = new Float32Array(bins); for (let f = p0; f < p1; f++) for (let k = 0; k < bins; k++) r[k] = Math.max(r[k], M[f * bins + k]);
    for (let f = fr(e.p) + 1; f < end; f++) { owner[f] = i; ref.set(r, f * bins); }
  });
  const maxUp = undb(opt.maxBoostDb), floor = undb(-70);
  let gSum = 0, gN = 0;
  const outs = await stftApply(ch, sr, N, (f, t, mag, g) => {
    if (f >= nF || owner[f] < 0) return;
    for (let k = 0; k < bins; k++) {
      const R = ref[f * bins + k], m = M[f * bins + k]; if (R <= 0 || m <= R * floor) continue;
      const ratio = opt.ratio * Math.pow(Math.max(50, k * binHz) / 1000, opt.tilt), e = 1 / ratio - 1;
      const u = Math.min(1, m / R), v = Math.min(maxUp, Math.max(1 / maxUp, Math.pow(u, e)));
      g[k] = v; gSum += Math.log10(v) * 20; gN++;
    }
  }, onProgress);
  const report = ["Decay of " + ev.length + " event" + (ev.length === 1 ? "" : "s") + " ×" + opt.ratio + (opt.tilt ? ", high partials ×" + rnd(opt.ratio * Math.pow(8, opt.tilt), 2) + " at 8 kHz" : "") + (opt.ratio > 1 ? " (rings longer)" : opt.ratio < 1 ? " (damped)" : ""), "Average change in the tails " + (gN ? (gSum / gN >= 0 ? "+" : "") + (gSum / gN).toFixed(1) : "0") + " dB"];
  finish(src, outs, opt.match ? "peak" : "none", report);
  return { outputs: [{ suffix: "ring", buf: mkBuf(sr, outs) }], report };
}

/* ---------- 9. Level the events: compression that leaves every event's shape alone ----------
   A compressor turns the gain down while a hit is loud and up again after it, so it flattens attacks and the
   background pumps. This evens out the events instead: each event's loudness (K-weighted, its first 200 ms, about the
   ear's integration time for short sounds) is moved part of the way to the median event, by one gain for the whole
   event, from 20 ms before its start to the end of its decay; between events the gain returns to 0 dB, so the
   background neither pumps nor jumps. Nothing inside an event changes shape. */
function eventLoudness(zs, a, b) {
  let s = 0; for (const z of zs) for (let i = a; i < b; i++) s += z[i] * z[i];
  return -0.691 + 10 * Math.log10(s / Math.max(1, b - a) + 1e-20);
}
function kWeighted(chans, sr) {
  const kc = WL.kFilterCoeffs(sr);
  return chans.map(c => { const a = new Float32Array(c.length), b = new Float32Array(c.length); WL.biquadInto(c, a, kc[0]); WL.biquadInto(a, b, kc[1]); return b; });
}
// the gain per sample (dB) that levels the events; exported for the comparison in experiments/compression
function eventLevelGain(chans, sr, ev, opt) {
  const n = chans[0].length, zs = kWeighted(chans, sr), win = Math.round(0.2 * sr);
  const L = ev.map((e, i) => eventLoudness(zs, e.a, Math.min(n, e.a + win, i + 1 < ev.length ? Math.max(e.a + 1, ev[i + 1].a) : n)));
  const sorted = L.slice().sort((x, y) => x - y), target = opt.target === "loudest" ? sorted[sorted.length - 1] : opt.target === "quietest" ? sorted[0] : sorted[sorted.length >> 1];
  const k = Math.max(0, Math.min(1, opt.amount / 100)), lim = opt.maxDb;
  const g = L.map(l => Math.max(-lim, Math.min(lim, (target - l) * k)));
  // each event's gain holds from 20 ms before its start until its decay sinks to 6 dB over the background (or 40 dB
  // under its peak); then the gain glides back to 0 dB over 60 ms, so the background between events keeps its level
  // instead of jumping with every event
  const hop = Math.max(1, Math.round(0.005 * sr)), nE = Math.ceil(n / hop), env = new Float32Array(nE);
  for (let h = 0; h < nE; h++) { let e = 0; for (const z of zs) for (let i = h * hop; i < Math.min(n, (h + 1) * hop); i++) e += z[i] * z[i]; env[h] = 10 * Math.log10(e / (hop * zs.length) + 1e-20); }
  const bed = Array.from(env).sort((x, y) => x - y)[Math.floor(0.1 * (nE - 1))];
  const gdb = new Float32Array(n), pre = Math.round(0.02 * sr), back = Math.round(0.06 * sr);
  for (let i = 0; i < ev.length; i++) {
    const a = ev[i].a, next = i + 1 < ev.length ? ev[i + 1].a - pre : n;
    let hp = Math.floor(a / hop), top = -Infinity; for (let h = hp; h < Math.min(nE, hp + Math.round(0.2 * sr / hop)); h++) if (env[h] > top) { top = env[h]; hp = h; }
    let he = hp; while (he < nE - 1 && he * hop < next && env[he] > Math.max(top - 40, bed + 6)) he++;
    const hold = Math.min(next, Math.max(a, he * hop));
    const from = i === 0 ? 0 : gdb[Math.max(0, a - pre - 1)];
    for (let j = Math.max(0, a - pre); j < a && j < n; j++) gdb[j] = from + (g[i] - from) * (j - (a - pre)) / pre;
    for (let j = Math.max(0, a); j < Math.min(n, hold); j++) gdb[j] = g[i];
    for (let j = hold; j < Math.min(n, next); j++) gdb[j] = g[i] * Math.max(0, 1 - (j - hold) / back);
  }
  return { gdb, levels: L, gains: g, target };
}
async function levelEvents(src, an, opt) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length, ev = eventsOf(an, sr, n);
  if (ev.length < 2) return { outputs: [{ suffix: "level", buf: mkBuf(sr, ch) }], report: ["Fewer than two events: nothing to even out"] };
  const { gdb, levels, gains } = eventLevelGain(ch, sr, ev, opt);
  const outs = ch.map(x => { const o = new Float32Array(n); for (let i = 0; i < n; i++) o[i] = x[i] * undb(gdb[i]); return o; });
  const sd = a => { const m = a.reduce((x, y) => x + y, 0) / a.length; return Math.sqrt(a.reduce((x, y) => x + (y - m) * (y - m), 0) / a.length); };
  const after = levels.map((l, i) => l + gains[i]);
  const report = ["Evened out " + ev.length + " events: their loudness spread " + sd(levels).toFixed(1) + " → " + sd(after).toFixed(1) + " dB (standard deviation)",
    "Gains from " + Math.min(...gains).toFixed(1) + " to +" + Math.max(...gains).toFixed(1) + " dB, each held for a whole event, so attacks and decays keep their shape"];
  finish(src, outs, opt.match ? "loudness" : "none", report);
  return { outputs: [{ suffix: "level", buf: mkBuf(sr, outs) }], report };
}

/* ---------- stereo and mono: fold with the phase lined up, widen around the attacks ---------- */
// STFT of two channels into one: frameFn(f, centreSample, Lre, Lim, Rre, Rim, Yre, Yim) writes bins 0..N/2 of the
// output spectrum. Hann analysis and synthesis, hop N/4, WOLA normalisation (as stftApply).
async function stftPair(L, R, N, frameFn, onProgress) {
  const H = N >> 2, half = N >> 1, fft = WL.getFFT(N), w = WL.hannP(N), len = L.length, pad = N;
  const out = new Float64Array(len + 2 * pad), norm = new Float64Array(len + 2 * pad);
  const lr = new Float64Array(N), li = new Float64Array(N), rr = new Float64Array(N), ri = new Float64Array(N), yr = new Float64Array(N), yi = new Float64Array(N);
  const nF = Math.ceil((len + N) / H);
  for (let f = 0; f < nF; f++) {
    const start = f * H - half;
    for (let i = 0; i < N; i++) { const s = start + i, ok = s >= 0 && s < len; lr[i] = ok ? L[s] * w[i] : 0; rr[i] = ok ? R[s] * w[i] : 0; li[i] = 0; ri[i] = 0; }
    fft(lr, li); fft(rr, ri);
    yr.fill(0); yi.fill(0);
    frameFn(f, start + half, lr, li, rr, ri, yr, yi);
    yi[0] = 0; yi[half] = 0;
    for (let k = 1; k < half; k++) { yr[N - k] = yr[k]; yi[N - k] = -yi[k]; }
    for (let i = 0; i < N; i++) yi[i] = -yi[i];
    fft(yr, yi);
    for (let i = 0; i < N; i++) { out[start + i + pad] += (yr[i] / N) * w[i]; norm[start + i + pad] += w[i] * w[i]; }
    if ((f & 127) === 127) { if (onProgress) onProgress(f / nF); await tick(); }
  }
  const y = new Float32Array(len);
  for (let i = 0; i < len; i++) { const nv = norm[i + pad]; y[i] = nv > 1e-6 ? out[i + pad] / nv : 0; }
  return y;
}
const FOLD_BANDS = [[0, 200, "below 200 Hz"], [200, 800, "200–800 Hz"], [800, 3000, "0.8–3 kHz"], [3000, 8000, "3–8 kHz"], [8000, 1e9, "above 8 kHz"]];
// Fold to mono. A plain sum (L+R)/2 cancels where the channels are out of phase (spaced microphones, wide synths,
// phase-flipped layers) and drops uncorrelated content by 3 dB. Here, per frequency bin, the right channel is turned
// to the left channel's phase (the cross-spectrum smoothed over 40 ms gives the angle) before summing, and the sum is
// brought back to the channels' mean energy (at most maxBoostDb). Per-bin changes smear across an STFT frame, which
// would put a faint pre-echo before each hit; so each attack the analysis finds is folded in the time domain instead,
// with that attack's own delay and polarity between the channels (cross-correlation within ±3 ms), and cross-faded in.
async function foldMono(src, an, opt, onProgress) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length;
  if (ch.length < 2) return { outputs: [{ suffix: "mono", buf: mkBuf(sr, [ch[0]]) }], report: ["Already mono: nothing to fold"] };
  const L = ch[0], R = ch[1];
  let diff = 0; for (let i = 0; i < n; i++) diff = Math.max(diff, Math.abs(L[i] - R[i]));
  if (diff < 1e-6) return { outputs: [{ suffix: "mono", buf: mkBuf(sr, [Float32Array.from(L)]) }], report: ["The channels are identical: the mono file is one of them, unchanged"] };
  let sl = 0, sr2 = 0, slr = 0; for (let i = 0; i < n; i++) { sl += L[i] * L[i]; sr2 += R[i] * R[i]; slr += L[i] * R[i]; }
  const corr = slr / Math.sqrt(sl * sr2 + 1e-30);
  const ev = eventsOf(an, sr, n), N = sr > 60000 ? 2048 : 1024, H = N >> 2, half = N >> 1, maxG = undb(opt.maxBoostDb), a = Math.exp(-H / (0.04 * sr));
  const Cr = new Float64Array(half + 1), Ci = new Float64Array(half + 1), Sll = new Float64Array(half + 1), Srr = new Float64Array(half + 1), Smm = new Float64Array(half + 1);
  const band = Int8Array.from({ length: half + 1 }, (_, k) => FOLD_BANDS.findIndex(b => k * sr / N >= b[0] && k * sr / N < b[1]));
  // energy bookkeeping for the report, per band: the channels' mean energy (target), the plain sum, this fold; also per event
  const nb = FOLD_BANDS.length, tgt = new Float64Array(nb), pln = new Float64Array(nb);
  const evT = ev.map(() => new Float64Array(nb)), evP = ev.map(() => new Float64Array(nb));
  let capped = 0, bins = 0, ei = 0;
  const y = await stftPair(L, R, N, (f, c, lr, li, rr, ri, yr, yi) => {
    while (ei < ev.length && ev[ei].e < c) ei++;
    const inEv = ei < ev.length && ev[ei].a <= c ? ei : -1;
    for (let k = 0; k <= half; k++) {
      const xr = lr[k], xi = li[k], zr = rr[k], zi = ri[k];
      Cr[k] = a * Cr[k] + (1 - a) * (xr * zr + xi * zi); Ci[k] = a * Ci[k] + (1 - a) * (xi * zr - xr * zi);
      Sll[k] = a * Sll[k] + (1 - a) * (xr * xr + xi * xi); Srr[k] = a * Srr[k] + (1 - a) * (zr * zr + zi * zi);
    }
    for (let k = 0; k <= half; k++) {
      let ur = 1, ui = 0;
      if (opt.align) {
        let cr = 0, ci = 0; for (let j = Math.max(0, k - 1); j <= Math.min(half, k + 1); j++) { cr += Cr[j]; ci += Ci[j]; }
        const m = Math.hypot(cr, ci); if (m > 1e-20) { ur = cr / m; ui = ci / m; }
      }
      const zr = rr[k] * ur - ri[k] * ui, zi = rr[k] * ui + ri[k] * ur, mr = 0.5 * (lr[k] + zr), mi = 0.5 * (li[k] + zi);
      Smm[k] = a * Smm[k] + (1 - a) * (mr * mr + mi * mi);
      let g = 1;
      if (opt.keepEnergy) { const t = 0.5 * (Sll[k] + Srr[k]); g = Smm[k] > 1e-24 ? Math.sqrt(t / Smm[k]) : 1; if (g > maxG) { g = maxG; capped++; } if (g < 1) g = 1; }
      bins++;
      yr[k] = mr * g; yi[k] = mi * g;
      const b = band[k], te = 0.5 * (lr[k] * lr[k] + li[k] * li[k] + rr[k] * rr[k] + ri[k] * ri[k]), pr = 0.5 * (lr[k] + rr[k]), pi = 0.5 * (li[k] + ri[k]), pe = pr * pr + pi * pi;
      tgt[b] += te; pln[b] += pe;
      if (inEv >= 0) { evT[inEv][b] += te; evP[inEv][b] += pe; }
    }
  }, onProgress);
  // the attacks, in the time domain: from 15 ms before each onset to 30 ms after its peak
  const spans = [];
  if (opt.align) for (const e of ev) {
    let top = 0; for (let i = e.a; i < e.e; i++) top = Math.max(top, Math.abs(L[i]), Math.abs(R[i]));
    let on = e.a; while (on < e.e && Math.abs(L[on]) < 0.01 * top && Math.abs(R[on]) < 0.01 * top) on++;
    const i0 = Math.max(0, on - Math.round(0.015 * sr)), i1 = Math.min(n, Math.max(e.p, on) + Math.round(0.03 * sr)), w0 = on, w1 = Math.min(n, on + Math.round(0.03 * sr)), ml = Math.round(0.003 * sr);
    let best = 0, lag = 0;
    for (let d = -ml; d <= ml; d++) { let c = 0; for (let i = w0; i < w1; i++) { const j = i + d; if (j >= 0 && j < n) c += L[i] * R[j]; } if (Math.abs(c) > Math.abs(best)) { best = c; lag = d; } }
    const sg = best < 0 ? -1 : 1, seg = new Float32Array(i1 - i0);
    let el = 0, er = 0, es = 0;
    for (let i = i0; i < i1; i++) { const j = i + lag, rv = j >= 0 && j < n ? sg * R[j] : 0, v = 0.5 * (L[i] + rv); seg[i - i0] = v; el += L[i] * L[i]; er += R[i] * R[i]; es += v * v; }
    const g = opt.keepEnergy && es > 0 ? Math.min(maxG, Math.max(1, Math.sqrt(0.5 * (el + er) / es))) : 1;
    for (let k = 0; k < seg.length; k++) seg[k] *= g;
    spans.push({ i0, i1, seg, lag, sg });
  }
  if (spans.length) {
    const r = Math.round(0.005 * sr), m = spanMask(n, spans.map(q => [q.i0 + r, q.i1 - r]), r);
    for (const q of spans) for (let i = q.i0; i < q.i1; i++) if (m[i] > 0) y[i] = (1 - m[i]) * y[i] + m[i] * q.seg[i - q.i0];
  }
  const plain = Float32Array.from(L, (v, i) => 0.5 * (v + R[i]));
  const dB = (a, b) => (b > 0 && a > 0 ? 10 * Math.log10(a / b) : 0), total = a => a.reduce((x, v) => x + v, 0);
  const lossAll = dB(total(pln), total(tgt)), worst = FOLD_BANDS.map((b, i) => [dB(pln[i], tgt[i]), b[2], tgt[i]]).filter(v => v[2] > 1e-3 * total(tgt)).sort((p, q) => p[0] - q[0])[0];
  // how close the result is to the channels' mean energy, measured on the result itself
  const bandE = async ch => { const { M, nF, bins } = await stftMagnitudes(ch, sr, N), E = new Float64Array(nb); for (let f = 0; f < nF; f++) for (let k = 0; k < bins; k++) E[band[k]] += M[f * bins + k] * M[f * bins + k]; return E; };
  const eL = await bandE([L]), eR = await bandE([R]), eY = await bandE([y]), tg2 = eL.map((v, i) => 0.5 * (v + eR[i]));
  const dev = FOLD_BANDS.map((b, i) => tg2[i] > 1e-3 * total(tg2) ? Math.abs(dB(eY[i], tg2[i])) : 0);
  const report = ["Channel correlation " + corr.toFixed(2) + " (1 = the same, 0 = unrelated, below 0 = opposite)",
    "A plain sum (L+R)/2 loses " + (-lossAll).toFixed(1) + " dB of the channels' energy" + (worst && worst[0] < -1 ? ", most at " + worst[1] + " (" + worst[0].toFixed(1) + " dB)" : "")];
  const evWorst = ev.map((e, i) => { let m = 0, bn = ""; FOLD_BANDS.forEach((b, j) => { if (evT[i][j] > 1e-3 * total(evT[i])) { const l = dB(evP[i][j], evT[i][j]); if (l < m) { m = l; bn = b[2]; } } }); return [m, i, bn]; })
    .filter(v => v[0] < -3).sort((p, q) => p[0] - q[0]).slice(0, 3);
  if (!evWorst.length && (!worst || worst[0] > -1)) report.push("Mono-compatible: a plain sum would do as well here");
  if (evWorst.length) report.push("Events that cancel most in a plain sum: " + evWorst.map(([m, i, bn]) => "at " + (ev[i].a / sr).toFixed(2) + " s " + m.toFixed(1) + " dB (" + bn + ")").join(", "));
  report.push((opt.align ? "Phase lined up" : "Phase left as it is") + (opt.keepEnergy ? ", energy kept: every band within " + Math.max(...dev).toFixed(1) + " dB of the channels' mean energy" : "") +
    (opt.keepEnergy && capped > 0.01 * bins ? " (the repair reached its " + opt.maxBoostDb + " dB limit in " + Math.round(100 * capped / bins) + "% of the bins)" : ""));
  if (spans.length) { const lags = spans.map(q => Math.abs(q.lag) / sr * 1000), flips = spans.filter(q => q.sg < 0).length;
    report.push(spans.length + " attack" + (spans.length === 1 ? "" : "s") + " folded in the time domain, each with its own delay between the channels (" + (Math.max(...lags) < 0.05 ? "none found" : "up to " + Math.max(...lags).toFixed(1) + " ms") + ")" + (flips ? ", " + flips + " with the polarity reversed" : "") + ": no pre-echo"); }
  report.push("The level per channel is kept. Loudness meters read the mono file 3 dB lower than the stereo one, because they add the channels (as they do for the plain sum)");
  const outs = [{ suffix: "mono", buf: mkBuf(sr, finish(src, [y], "none", report)) }];
  if (opt.plain) outs.push({ suffix: "plain-sum", buf: mkBuf(sr, finish(src, [plain], "none", [])) });
  return { outputs: outs, report };
}
// A velvet-noise decorrelator (Alary, Politis and Välimäki 2017): 30 ms of sparse ±1 taps with a decaying envelope,
// unit energy. Cheap, flat on average, and it smears little because the taps decay.
function velvet(x, sr, seed) {
  const R = rng(seed), Td = sr / 1500, Lm = Math.round(0.03 * sr), taps = [];
  for (let m = 0; m * Td < Lm; m++) { const d = Math.min(Lm - 1, Math.round(m * Td + R() * (Td - 1))); taps.push([d, (R() < 0.5 ? -1 : 1) * Math.exp(-3 * d / Lm)]); }
  const e = Math.sqrt(taps.reduce((s, t) => s + t[1] * t[1], 0)); for (const t of taps) t[1] /= e;
  const n = x.length, y = new Float32Array(n);
  for (const [d, g] of taps) for (let i = d; i < n; i++) y[i] += g * x[i - d];
  return y;
}
// Widen around the attacks. The mid (the sound itself, or (L+R)/2) stays as it is; a decorrelated copy of it is added
// to the side only where chosen: the tails and background (default), only the tails, or everywhere for comparison.
// Lows stay centred. Because only the side changes, (L+R)/2 gives the mid back exactly: the result folds to mono
// without any change, and a mono sound comes back unchanged.
async function widenTails(src, an, opt) {
  const sr = src.sampleRate, ch = chansOf(src), n = ch[0].length, stereo = ch.length > 1;
  const M = stereo ? Float32Array.from(ch[0], (v, i) => 0.5 * (v + ch[1][i])) : Float32Array.from(ch[0]);
  const S = stereo ? Float32Array.from(ch[0], (v, i) => 0.5 * (v - ch[1][i])) : new Float32Array(n);
  const ev = eventsOf(an, sr, n), hold = Math.round(opt.holdMs / 1000 * sr), r = Math.round(0.01 * sr), pre = Math.round(0.005 * sr);
  let mask;
  if (opt.where === "all" || (!ev.length && opt.where === "rest")) mask = new Float32Array(n).fill(1);
  else if (opt.where === "tails") mask = spanMask(n, ev.map(e => [e.p + hold, e.e]), r);
  else { const a = spanMask(n, ev.map(e => [e.a - pre, e.p + hold]), r); mask = a.map(v => 1 - v); }
  let on = 0; for (let i = 0; i < n; i++) on += mask[i]; on /= n;
  if (on < 1e-3) return { outputs: [{ suffix: "wide", buf: mkBuf(sr, stereo ? ch : [ch[0], Float32Array.from(ch[0])]) }], report: ["No events found: there are no tails to widen"] };
  const D = velvet(M, sr, 7), Dh = new Float32Array(n), Dh2 = new Float32Array(n);
  { const w0 = 2 * Math.PI * Math.min(opt.lowHz, 0.45 * sr) / sr, al = Math.sin(w0) / (2 * Math.SQRT1_2), cw = Math.cos(w0), a0 = 1 + al;
    const c = [(1 + cw) / 2 / a0, -(1 + cw) / a0, (1 + cw) / 2 / a0, -2 * cw / a0, (1 - al) / a0]; WL.biquadInto(D, Dh, c); WL.biquadInto(Dh, Dh2, c);
    // the decorrelator's response is uneven on tonal sounds (a ringing blade can come out louder than it went in), so the
    // added side follows the high-passed sound's own level over 30 ms: width is then the same at every moment
    const Mh = new Float32Array(n), Mh2 = new Float32Array(n); WL.biquadInto(M, Mh, c); WL.biquadInto(Mh, Mh2, c);
    const W = Math.max(8, Math.round(0.015 * sr)), cm = new Float64Array(n + 1), cd = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) { cm[i + 1] = cm[i] + Mh2[i] * Mh2[i]; cd[i + 1] = cd[i] + Dh2[i] * Dh2[i]; }
    for (let i = 0; i < n; i++) { const a = Math.max(0, i - W), b = Math.min(n, i + W), em = cm[b] - cm[a], ed = cd[b] - cd[a]; Dh2[i] *= ed > 1e-20 ? Math.min(4, Math.sqrt(em / ed)) : 0; } }
  const w = opt.width / 100, Lo = new Float32Array(n), Ro = new Float32Array(n);
  let sll = 0, srr = 0, slr = 0;
  for (let i = 0; i < n; i++) {
    const s = S[i] + w * Dh2[i] * mask[i]; Lo[i] = M[i] + s; Ro[i] = M[i] - s;
    if (mask[i] > 0.5) { sll += Lo[i] * Lo[i]; srr += Ro[i] * Ro[i]; slr += Lo[i] * Ro[i]; }
  }
  const report = ["Width " + opt.width + "% added " + (opt.where === "tails" ? "to the tails" : opt.where === "all" ? "everywhere (attacks too, for comparison)" : "around the attacks") + ", " + Math.round(100 * on) + "% of the time; lows below " + opt.lowHz + " Hz stay centred",
    "Channel correlation where widened: " + (slr / Math.sqrt(sll * srr + 1e-30)).toFixed(2) + " (1 = mono, 0 = unrelated)"];
  if (opt.width > 0 && slr / Math.sqrt(sll * srr + 1e-30) > 0.9) report.push("Little changed: most of this sound outside the attacks is below " + opt.lowHz + " Hz, which stays centred. Lower \"Lows centred below\" to widen more");
  const lowered = peakOf([Lo, Ro]) > 0.999, outs = finish(src, [Lo, Ro], "none", report);
  report.push((stereo ? "(L+R)/2 of the result is the original's (L+R)/2" : "Folded back to mono with (L+R)/2, the result is the original sound") + (lowered ? ", lowered as noted" : ", sample for sample"));
  return { outputs: [{ suffix: "wide", buf: mkBuf(sr, outs) }], report };
}

const TOOLS = [
  { id: "cleanup", name: "Clean up for export", kind: "Game audio",
    desc: "Fixes what the asset checks find: removes DC offset, rebuilds clipped peaks, trims silence, adds fades where the file clicks or the tail was cut, folds identical channels to mono, and normalises the peak.",
    params: [{ id: "peakDb", label: "Peak", min: -12, max: 0, step: 0.5, value: -1, unit: "dBFS" }, { id: "mono", label: "Fold identical channels to mono", type: "bool", value: true }],
    run: cleanup },
  { id: "deharsh", name: "De-harsh", kind: "Mixing",
    desc: "Suppresses narrow resonances between 0.8 and 10 kHz and tilts down the top end while the harshness model (sharpness, roughness and fluctuation in Zwicker's annoyance form) is high. \"Harshest moments\" acts on the top of this file's own harshness range, so it works on mixes; \"Only harsh sounds\" uses the absolute scale, where most music never qualifies; \"Always\" ignores the model. The detector can be switched to the level in the same frequency range, which makes it a conventional dynamic EQ, for comparison. The second result is what was removed, raised for listening.",
    params: [{ id: "mode", label: "Act on", type: "select", value: "relative", options: [["relative", "Harshest moments of this file"], ["absolute", "Only harsh sounds (absolute)"], ["always", "Always"]] },
      { id: "sidechain", label: "Detector", type: "select", value: "harshness", options: [["harshness", "Harshness model"], ["level", "Band level (conventional dynamic EQ)"]] },
      { id: "amount", label: "Amount", min: 0, max: 1, step: 0.05, value: 0.6 }, { id: "threshold", label: "Absolute threshold", min: 0, max: 1.5, step: 0.05, value: 0.15, unit: "harshness" }, { id: "maxCut", label: "Deepest cut", min: 3, max: 18, step: 1, value: 9, unit: "dB" }, { id: "match", label: "Match loudness", type: "bool", value: true }, { id: "delta", label: "Also render what was removed", type: "bool", value: true }],
    run: deharsh },
  { id: "layers", name: "Layer remix", kind: "Sound design",
    desc: "Rebalances the layers found by NMF (row X2) with soft Wiener masks: lift the casing ring, drop the sub, and so on.",
    params: "layers", run: layerMix },
  { id: "hpss", name: "Attack and body", kind: "Sound design",
    desc: "Splits each moment into percussive and sustained energy with median filtering (Fitzgerald 2010) and sets their levels separately: a transient shaper that follows content, not the envelope.",
    params: [{ id: "attackDb", label: "Attack", min: -18, max: 12, step: 1, value: 4, unit: "dB" }, { id: "bodyDb", label: "Body", min: -18, max: 12, step: 1, value: -3, unit: "dB" }, { id: "match", label: "Match loudness", type: "bool", value: true }],
    run: hpss },
  { id: "tension", name: "Tension shaper", kind: "Mastering",
    desc: "Turns the tension curve (C5) into gain and brightness automation. Positive contrast makes build-ups rise and releases fall further; negative contrast flattens them.",
    params: [{ id: "contrast", label: "Contrast", min: -1, max: 1, step: 0.05, value: 0.5 }, { id: "bright", label: "Brightness follow", min: -1, max: 1, step: 0.05, value: 0.4 }],
    needs: "tension", run: tensionShape },
  { id: "loop", name: "Loop maker", kind: "Game audio",
    desc: "Finds the start and end points whose surroundings match best (on downbeats when the beat is clear), moves them to zero crossings and renders an equal-power seam, so the result wraps without a click.",
    params: [{ id: "xfadeMs", label: "Seam", min: 10, max: 400, step: 10, value: 120, unit: "ms" }, { id: "minLen", label: "Minimum length", min: 0.2, max: 0.9, step: 0.05, value: 0.5, unit: "of file" }],
    run: loopMaker },
  { id: "vars", name: "Variations", kind: "Game audio",
    desc: "Makes copies that differ the way repeated game sounds should: random layer balance, reordered grains inside iterative events, and a small pitch change.",
    params: [{ id: "count", label: "Copies", min: 2, max: 6, step: 1, value: 4 }, { id: "layerDb", label: "Layer spread", min: 0, max: 6, step: 0.5, value: 3, unit: "dB" }, { id: "pitch", label: "Pitch spread", min: 0, max: 3, step: 0.1, value: 1, unit: "st" }, { id: "grains", label: "Reorder grains", type: "bool", value: true }],
    run: variations },
  { id: "tailverb", name: "Reverb on the tails", kind: "Sound design",
    desc: "Sends only the tails of the events into a reverb: each hit from its peak to the end of its decay, so the attack stays dry and punchy and the tail blooms. An EQ cannot do this: it adds new energy after the sound and smears it in time.",
    params: [{ id: "wetDb", label: "Reverb level", min: -30, max: 0, step: 1, value: -8, unit: "dB" }, { id: "t60", label: "Decay", min: 0.3, max: 6, step: 0.1, value: 1.6, unit: "s" },
      { id: "holdMs", label: "Start after the peak", min: 0, max: 300, step: 5, value: 30, unit: "ms" }, { id: "toneHz", label: "Tone", min: 1500, max: 16000, step: 500, value: 6000, unit: "Hz" },
      { id: "dryAttacks", label: "Keep the attacks dry", type: "bool", value: true }],
    run: tailVerb },
  { id: "drive", name: "Drive the hits", kind: "Sound design",
    desc: "Saturates only the events (their attacks, their bodies, or all of them) and leaves the rest clean. The saturated part is held at the original level, so what changes is the harmonics, which no EQ can add, not the loudness.",
    params: [{ id: "where", label: "Where", type: "select", value: "attack", options: [["attack", "Attacks"], ["body", "Bodies (after the peak)"], ["whole", "Whole events"]] },
      { id: "driveDb", label: "Drive", min: 0, max: 30, step: 1, value: 18, unit: "dB" }, { id: "mix", label: "Mix", min: 0, max: 100, step: 5, value: 70, unit: "%" },
      { id: "match", label: "Match loudness", type: "bool", value: true }],
    run: driveHits },
  { id: "ring", name: "Ring time", kind: "Sound design",
    desc: "Changes how long each event rings, relative to its own peak: longer for metal and glass, shorter for wood and cardboard. Higher partials can ring longer or shorter than lower ones, which moves the material read by the decay descriptor (N9). An EQ sets the same gain at every level; this sets it by how far each partial has decayed since its event's peak.",
    params: [{ id: "ratio", label: "Ring time", min: 0.25, max: 4, step: 0.05, value: 2 }, { id: "tilt", label: "High partials", min: -0.5, max: 0.5, step: 0.05, value: 0 },
      { id: "maxBoostDb", label: "Largest lift", min: 6, max: 36, step: 1, value: 18, unit: "dB" }, { id: "match", label: "Keep the peak", type: "bool", value: true }],
    run: ringTime },
  { id: "level", name: "Level the events", kind: "Mixing",
    desc: "Evens out how loud the events are (footsteps, impacts, a burst of shots, debris) with one gain per event, changed only in the moment before each event starts. A compressor reaching the same evenness turns attacks down and makes the background pump; this leaves every event's attack and decay as it was.",
    params: [{ id: "amount", label: "Evenness", min: 0, max: 100, step: 5, value: 70, unit: "%" }, { id: "target", label: "Towards", type: "select", value: "median", options: [["median", "The middle event"], ["loudest", "The loudest event"], ["quietest", "The quietest event"]] },
      { id: "maxDb", label: "Largest change", min: 3, max: 24, step: 1, value: 12, unit: "dB" }, { id: "match", label: "Match loudness", type: "bool", value: true }],
    run: levelEvents },
  { id: "mono", name: "Fold to mono", kind: "Game audio",
    desc: "Folds stereo to mono without the losses of a plain (L+R)/2: per frequency, the right channel is turned to the left channel's phase before summing, and the sum is brought back to the channels' energy. Each attack the analysis finds is folded in the time domain with its own delay and polarity between the channels, so there is no pre-echo. The report names the bands and events that cancel in a plain sum, which doubles as a mono-compatibility check. The second result is the plain sum, for comparison.",
    params: [{ id: "align", label: "Line up the phase", type: "bool", value: true }, { id: "keepEnergy", label: "Keep the channels' energy", type: "bool", value: true },
      { id: "maxBoostDb", label: "Largest repair", min: 3, max: 18, step: 1, value: 9, unit: "dB" }, { id: "plain", label: "Also render the plain sum", type: "bool", value: true }],
    run: foldMono },
  { id: "widen", name: "Widen the tails", kind: "Sound design",
    desc: "Makes stereo from mono (or widens stereo) by adding a decorrelated copy of the sound (a velvet-noise decorrelator) to the side, only after the attacks the analysis finds: attacks stay centred and punchy, tails and background open up, and the lows stay centred. Only the side changes, so (L+R)/2 gives back the original exactly: the result is fully mono-compatible.",
    params: [{ id: "width", label: "Width", min: 0, max: 100, step: 5, value: 60, unit: "%" }, { id: "where", label: "Where", type: "select", value: "rest", options: [["rest", "Tails and background (not the attacks)"], ["tails", "Only the tails"], ["all", "Everywhere (attacks too, for comparison)"]] },
      { id: "holdMs", label: "Centred after each peak", min: 0, max: 200, step: 5, value: 25, unit: "ms" }, { id: "lowHz", label: "Lows centred below", min: 40, max: 400, step: 10, value: 150, unit: "Hz" }],
    run: widenTails }
];

/* ---------- comparison with a static EQ ----------
   How much of a tool's spectrogram change could the single best fixed EQ reproduce? The EQ is the per-bin gain that
   matches the output's long-term spectrum; "notEq" is the power-weighted mean |dB| difference left after applying it. */
const rnd = (v, d) => (typeof v === "number" && isFinite(v)) ? Number(v.toFixed(d)) : null;
const dbv = v => 20 * Math.log10(Math.max(1e-12, v));
async function eqExplained(dry, wet, sr) {
  const N = 2048, A = await stftMagnitudes(dry, sr, N), B = await stftMagnitudes(wet, sr, N);
  const K = A.bins, T = Math.min(A.nF, B.nF), pa = new Float64Array(K), pb = new Float64Array(K);
  let mx = 0;
  for (let f = 0; f < T; f++) for (let k = 0; k < K; k++) { const x = A.M[f * K + k], y = B.M[f * K + k]; pa[k] += x * x; pb[k] += y * y; if (x > mx) mx = x; if (y > mx) mx = y; }
  const G = new Float64Array(K); for (let k = 0; k < K; k++) G[k] = pa[k] > 0 ? Math.sqrt(pb[k] / pa[k]) : 1;
  let st = 0, sr2 = 0, w = 0; const floor = mx * 1e-3;
  for (let f = 0; f < T; f++) for (let k = 1; k < K; k++) {
    const x = A.M[f * K + k], y = B.M[f * K + k]; if (x < floor && y < floor) continue;
    const ww = Math.max(x, y) ** 2, dt = Math.abs(dbv(y + 1e-9) - dbv(x + 1e-9)), de = Math.abs(dbv(y + 1e-9) - dbv(x * G[k] + 1e-9));
    st += dt * ww; sr2 += de * ww; w += ww;
  }
  // the static EQ in third-octave bands (what a fixed EQ would have to do)
  const bands = []; for (let fc = 31.5; fc < sr / 2 * 0.9; fc *= Math.pow(2, 1 / 3)) {
    const lo = Math.ceil(fc * Math.pow(2, -1 / 6) * N / sr), hi = Math.floor(fc * Math.pow(2, 1 / 6) * N / sr); let a = 0, b = 0;
    for (let k = Math.max(1, lo); k <= Math.min(K - 1, hi); k++) { a += pa[k]; b += pb[k]; }
    if (a > 0) bands.push([Math.round(fc), rnd(10 * Math.log10(b / a), 1)]);
  }
  const change = st / w, resid = sr2 / w;
  // Two time-varying EQs on top of the static one, both fitted to this very output (upper bounds, so they flatter the EQ):
  //   dynamic    each third-octave band's gain is any function of that band's own input level (2 dB steps), with
  //              instant attack and release: the best a conventional dynamic EQ or multiband compressor could do
  //   automation each band's gain may follow any curve at 0.1 s resolution: the best an engineer automating
  //              a 30-band EQ could do
  const nb = Math.max(1, Math.round(3 * Math.log2(sr / 2 * 0.9 / 31.5))), band = new Int16Array(K);
  for (let k = 0; k < K; k++) band[k] = Math.max(0, Math.min(nb - 1, Math.round(3 * Math.log2(Math.max(1, k * sr / N) / 31.5))));
  const Eg = new Float64Array(T * nb), Ew = new Float64Array(T * nb), Ed = new Float64Array(T * nb);
  for (let f = 0; f < T; f++) for (let k = 1; k < K; k++) { const x = A.M[f * K + k], y = B.M[f * K + k], i = f * nb + band[k]; Eg[i] += (x * G[k]) ** 2; Ew[i] += y * y; Ed[i] += x * x; }
  const eFloor = (floor * floor) * 4, tgt = new Float64Array(T * nb), wt = new Float64Array(T * nb);
  for (let i = 0; i < T * nb; i++) { const m = Math.max(Eg[i], Ew[i]); if (m > eFloor) { tgt[i] = 10 * Math.log10((Ew[i] + 1e-20) / (Eg[i] + 1e-20)); wt[i] = m; } }
  const dyn = new Float64Array(T * nb), aut = new Float64Array(T * nb);
  for (let b = 0; b < nb; b++) {
    let top = -Infinity; for (let f = 0; f < T; f++) if (wt[f * nb + b] > 0) top = Math.max(top, 10 * Math.log10(Ed[f * nb + b] + 1e-20));
    if (!isFinite(top)) continue;
    const sum = new Float64Array(52), sw = new Float64Array(52), bin = new Int16Array(T);
    for (let f = 0; f < T; f++) { const i = f * nb + b; bin[f] = Math.min(51, Math.floor(Math.max(0, top - 10 * Math.log10(Ed[i] + 1e-20)) / 2)); if (wt[i] > 0) { sum[bin[f]] += tgt[i] * wt[i]; sw[bin[f]] += wt[i]; } }
    for (let f = 0; f < T; f++) dyn[f * nb + b] = sw[bin[f]] > 0 ? sum[bin[f]] / sw[bin[f]] : 0;
    const hw = Math.max(1, Math.round(0.05 * sr / A.H));
    for (let f = 0; f < T; f++) { let s = 0, q = 0; for (let j = Math.max(0, f - hw); j <= Math.min(T - 1, f + hw); j++) { const i = j * nb + b; s += tgt[i] * wt[i]; q += wt[i]; } aut[f * nb + b] = q > 0 ? s / q : 0; }
  }
  let rd = 0, ra = 0;
  for (let f = 0; f < T; f++) for (let k = 1; k < K; k++) {
    const x = A.M[f * K + k], y = B.M[f * K + k]; if (x < floor && y < floor) continue;
    const ww = Math.max(x, y) ** 2, i = f * nb + band[k], yd = dbv(y + 1e-9);
    rd += Math.abs(yd - dbv(x * G[k] * Math.pow(10, dyn[i] / 20) + 1e-9)) * ww; ra += Math.abs(yd - dbv(x * G[k] * Math.pow(10, aut[i] / 20) + 1e-9)) * ww;
  }
  const share = r => rnd(change > 0.05 ? Math.max(0, 1 - r / change) : NaN, 2);
  return { change: rnd(change, 2), notEq: rnd(resid, 2), eqShare: share(resid), dynShare: share(rd / w), autoShare: share(ra / w), eqBands: bands };
}

/* ---------- store-only ZIP (for hosts that accept .zip but not .wav downloads) ---------- */
let CRC_T = null;
function crc32(u8) {
  if (!CRC_T) { CRC_T = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; CRC_T[n] = c >>> 0; } }
  let c = 0xFFFFFFFF; for (let i = 0; i < u8.length; i++) c = CRC_T[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
function zipStore(files) {
  const enc = new TextEncoder(), parts = [], cd = [];
  let off = 0;
  const d = new Date(), dosT = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1), dosD = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  for (const f of files) {
    const name = enc.encode(f.name), data = f.data instanceof Uint8Array ? f.data : new Uint8Array(f.data), crc = crc32(data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(8, 0, true);
    lh.setUint16(10, dosT, true); lh.setUint16(12, dosD, true); lh.setUint32(14, crc, true); lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true);
    lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true);
    ch.setUint16(12, dosT, true); ch.setUint16(14, dosD, true); ch.setUint32(16, crc, true); ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true);
    ch.setUint16(28, name.length, true); ch.setUint32(42, off, true);
    parts.push(new Uint8Array(lh.buffer), name, data); cd.push(new Uint8Array(ch.buffer), name);
    off += 30 + name.length + data.length;
  }
  const cdSize = cd.reduce((s, p) => s + p.length, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true); end.setUint32(12, cdSize, true); end.setUint32(16, off, true);
  const all = parts.concat(cd, [new Uint8Array(end.buffer)]), out = new Uint8Array(off + cdSize + 22);
  let p = 0; for (const a of all) { out.set(a, p); p += a.length; }
  return out;
}

const api = { TOOLS, eqExplained, encodeWav, zipStore, crc32, mkBuf, chansOf, resample, lufsOf, stftApply, stftMagnitudes, _stftMag: stftMagnitudes, cleanup, deharsh, layerMix, hpss, tensionShape, loopMaker, variations, tailVerb, driveHits, ringTime, levelEvents, eventLevelGain, kWeighted, eventsOf, foldMono, widenTails, velvet };
if (typeof module !== "undefined" && module.exports) module.exports = api;
root.WLFX = api;
})(__ph);

// ---- src/playhead.js
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Daniel Sandner
// Part of Playhead, https://github.com/eigensoundlab/playhead
//
// Public API for other applications. One call analyses a sound and returns a plain JSON document
// (schema "playhead-analysis", version 1) that can be stored, sent between processes or read by other tools.
//
//   const doc = await Playhead.analyze({ sampleRate, channels: [Float32Array, ...] }, { profile: "auto" });
//   Playhead.drawStrip(canvas.getContext("2d"), doc, { curve: "activity", colour: "harshness" });
//   const marks = Playhead.toMarkers(doc);
//   const v = Playhead.curveAt(doc, "tension", 12.5);                     // curves as modulators
//
// Profiles (what is computed):
//   fast   features, curves C1-C7, onsets, loudness summary, waveform overview. For browsing large libraries.
//   sfx    fast + events with Schaeffer-style tags, NMF layers, asset checks, cross-field descriptors N1-N9.
//   music  sfx + beats, downbeats and sections.
//   auto   sfx for sounds up to 10 s, music for longer ones.
//   const r = await Playhead.process(sound, "deharsh", { amount: 0.6 });   // the effects bench tools
// Depends on analysis.js (WL) and descriptors.js (PHD), and for process() on effects.js; loaded after them, or bundled in dist/.
(function (root) {
"use strict";
const WL = root.WL || (typeof require !== "undefined" ? require("./analysis.js") : null);
const PHD = root.PHD || (typeof require !== "undefined" ? require("./descriptors.js") : null);

const VERSION = "0.2.0";            // kept equal to package.json by the test suite
const SCHEMA = "playhead-analysis", SCHEMA_VERSION = 1;
const PROFILES = ["fast", "sfx", "music", "auto"];

/* ---------------- input ---------------- */
// Accepts an AudioBuffer (or anything with sampleRate, numberOfChannels, length, getChannelData), or
// { sampleRate, channels: [Float32Array, ...] }. Mono and stereo are analysed; further channels are ignored.
function toBuffer(input) {
  if (!input) throw new Error("Playhead.analyze: no audio");
  if (typeof input.getChannelData === "function") return input;
  const chs = (input.channels || []).map(c => c instanceof Float32Array ? c : Float32Array.from(c));
  if (!chs.length || !input.sampleRate) throw new Error("Playhead.analyze: expected { sampleRate, channels: [Float32Array, ...] }");
  const n = Math.min(...chs.map(c => c.length));
  return { sampleRate: input.sampleRate, numberOfChannels: chs.length, length: n, duration: n / input.sampleRate, getChannelData: i => chs[i] };
}

/* ---------------- JSON helpers ---------------- */
const rnd = (v, d) => (typeof v === "number" && isFinite(v)) ? Number(v.toFixed(d)) : null;
// plain JSON copy: typed arrays to arrays, NaN and Infinity to null, numbers rounded
function plain(v, d) {
  if (v == null) return null;
  if (typeof v === "number") return rnd(v, d);
  if (typeof v === "string" || typeof v === "boolean") return v;
  if (ArrayBuffer.isView(v) || Array.isArray(v)) return Array.from(v, x => plain(x, d));
  if (typeof v === "object") { const o = {}; for (const k of Object.keys(v)) o[k] = plain(v[k], d); return o; }
  return null;
}
// base64 for byte arrays, without Buffer or btoa (works the same in Node, pages, workers and modules)
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function toB64(u8) {
  let out = "";
  for (let i = 0; i < u8.length; i += 3) {
    const a = u8[i], b = i + 1 < u8.length ? u8[i + 1] : 0, c = i + 2 < u8.length ? u8[i + 2] : 0, n = (a << 16) | (b << 8) | c;
    out += B64[n >> 18 & 63] + B64[n >> 12 & 63] + (i + 1 < u8.length ? B64[n >> 6 & 63] : "=") + (i + 2 < u8.length ? B64[n & 63] : "=");
  }
  return out;
}
function fromB64(str) {
  const clean = String(str).replace(/[^A-Za-z0-9+/]/g, ""), n = Math.floor(clean.length * 3 / 4), out = new Uint8Array(n);
  let o = 0;
  for (let i = 0; i < clean.length; i += 4) {
    const v = (B64.indexOf(clean[i]) << 18) | (B64.indexOf(clean[i + 1]) << 12) | ((i + 2 < clean.length ? B64.indexOf(clean[i + 2]) : 0) << 6) | (i + 3 < clean.length ? B64.indexOf(clean[i + 3]) : 0);
    if (o < n) out[o++] = v >> 16 & 255; if (o < n) out[o++] = v >> 8 & 255; if (o < n) out[o++] = v & 255;
  }
  return out;
}
// mel spectrogram for the strip's spectrogram layer: per column the loudest frame in each band, 80 dB below the
// file's top mapped to 0..255, column after column, lowest band first; base64 to keep the JSON small
function spectrumOf(F, cols) {
  const B = Math.round(F.melDb.length / F.nF), n = F.nF, top = (() => { const v = Array.from(F.melDb).sort((a, b) => a - b); return v[Math.floor(0.999 * (v.length - 1))]; })();
  const lo = top - 80, u8 = new Uint8Array(cols * B);
  for (let c = 0; c < cols; c++) {
    const a = Math.floor(c * n / cols), e = Math.max(a + 1, Math.floor((c + 1) * n / cols));
    for (let b = 0; b < B; b++) {
      let mx = -Infinity; for (let m = a; m < e && m < n; m++) { const v = F.melDb[m * B + b]; if (v > mx) mx = v; }
      u8[c * B + b] = Math.max(0, Math.min(255, Math.round((mx - lo) / 80 * 255)));
    }
  }
  const nyq = F.sr / 2;
  return { scale: "mel", bands: B, fmin: 40, fmax: rnd(Math.min(16000, nyq * 0.95), 1), columns: cols, dbTop: rnd(top, 2), dbRange: 80, encoding: "base64-u8", data: toB64(u8) };
}
// frame curve (one value per analysis frame) -> fixed-rate curve; silent columns become null
function resample(arr, F, cols, gate, mode) {
  const out = new Array(cols), n = F.nF;
  for (let c = 0; c < cols; c++) {
    const a = Math.floor(c * n / cols), b = Math.max(a + 1, Math.floor((c + 1) * n / cols));
    let s = 0, k = 0, mx = -Infinity;
    for (let m = a; m < b && m < n; m++) { const v = arr[m]; if (!isFinite(v) || (gate && !gate[m])) continue; s += v; k++; if (v > mx) mx = v; }
    out[c] = k ? rnd(mode === "max" ? mx : s / k, 4) : null;
  }
  return out;
}
function waveOverview(buf, cols) {
  const n = buf.length, chs = []; for (let c = 0; c < Math.min(2, buf.numberOfChannels); c++) chs.push(buf.getChannelData(c));
  const mn = new Array(cols), mx = new Array(cols), rms = new Array(cols);
  for (let c = 0; c < cols; c++) {
    const a = Math.floor(c * n / cols), b = Math.max(a + 1, Math.floor((c + 1) * n / cols));
    let lo = 0, hi = 0, s = 0, k = 0;
    for (const ch of chs) for (let i = a; i < b && i < n; i++) { const v = ch[i]; if (v < lo) lo = v; if (v > hi) hi = v; s += v * v; k++; }
    mn[c] = rnd(lo, 4); mx[c] = rnd(hi, 4); rms[c] = rnd(k ? Math.sqrt(s / k) : 0, 4);
  }
  return { columns: cols, min: mn, max: mx, rms };
}

/* ---------------- analysis ---------------- */
async function analyze(input, opts) {
  opts = opts || {};
  const t0 = Date.now(), buf = toBuffer(input);
  const profileIn = opts.profile || "auto";
  if (!PROFILES.includes(profileIn)) throw new Error("Playhead.analyze: unknown profile " + profileIn + " (" + PROFILES.join(", ") + ")");
  const prog = typeof opts.onProgress === "function" ? opts.onProgress : () => {};
  const F = await WL.analyse(buf, p => prog(0.6 * p));
  return build(F, buf, opts, t0, prog);
}
/**
 * The document for a WL.analyse() result that already exists, without analysing again (the Playhead app uses it for
 * its composer). input: the same audio; opts as for analyze().
 */
async function fromAnalysis(F, input, opts) {
  opts = opts || {};
  if (!PROFILES.includes(opts.profile || "auto")) throw new Error("Playhead.fromAnalysis: unknown profile " + opts.profile);
  return build(F, toBuffer(input), opts, Date.now(), typeof opts.onProgress === "function" ? opts.onProgress : () => {});
}
async function build(F, buf, opts, t0, prog) {
  const profileIn = opts.profile || "auto";
  const profile = profileIn === "auto" ? (F.dur <= 10 ? "sfx" : "music") : profileIn;
  const cls = opts.assetClass || (profile === "sfx" && F.dur <= 10 ? "sfx" : F.guess);
  const cur = WL.computeCurves(F, cls, { scale: "fixed" });
  const rate = opts.curveRate || (F.dur <= 10 ? 100 : 20), cols = Math.max(1, Math.round(F.dur * rate));
  const g = cur.gate;
  const curves = {
    loudness: resample(cur.curves.c1, F, cols, g), activity: resample(cur.curves.c2, F, cols, g), arousal: resample(cur.curves.c3, F, cols, g),
    harshness: resample(cur.curves.c4, F, cols, g), tension: resample(cur.curves.c5, F, cols, g), harmonicTension: resample(cur.curves.c6, F, cols, g),
    surprise: resample(cur.curves.c7, F, cols, g), lufs: resample(cur.lufs, F, cols, g)
  };
  const ev = WL.eventsOf(F);
  const lufsSorted = []; for (let m = 0; m < F.nF; m++) if (g[m] && isFinite(cur.lufs[m])) lufsSorted.push(cur.lufs[m]);
  lufsSorted.sort((a, b) => a - b);
  const q = p => lufsSorted.length ? lufsSorted[Math.floor(p * (lufsSorted.length - 1))] : NaN;
  prog(0.65);
  const doc = {
    schema: SCHEMA, schemaVersion: SCHEMA_VERSION,
    generator: { name: "playhead", version: VERSION },
    source: { name: opts.name || null, sampleRate: buf.sampleRate, channels: buf.numberOfChannels, duration: rnd(F.dur, 4) },
    profile, assetClass: cls, guessedClass: F.guess,
    summary: { peakLufs: rnd(ev.peakLufs, 2), loudnessRange: rnd(q(0.95) - q(0.1), 2), soundStart: rnd(ev.start, 4), soundEnd: rnd(ev.end60, 4) },
    curves: { rate: rnd(cols / F.dur, 6), start: 0, scale: "0..1 on fixed library-wide scales; lufs in LUFS; null where silent", ...curves },
    wave: waveOverview(buf, opts.waveColumns || 1000),
    onsets: ev.onsetTimes.map(t => rnd(t, 4)),
    noveltyTicks: cur.ticks.map(m => rnd(m * F.dt, 4)),
    rhythm: null, sections: null, events: null, layers: null, issues: null, descriptors: null
  };
  // a small mel spectrogram for the spectrogram layer (sfx and music profiles; opts.spectrum false leaves it out)
  if (opts.spectrum !== false && (profile !== "fast" || opts.spectrum === true)) doc.spectrum = spectrumOf(F, cols);
  if (profile !== "fast") {
    const X = WL.sfxOf(F, buf, opts.name || "");
    prog(0.75);
    doc.summary.peakDb = rnd(X.pkDb, 2); doc.summary.attack = rnd(X.attack, 4);
    doc.events = X.events.map(e => ({ start: rnd(e.t0, 4), end: rnd(e.t1, 4), decayEnd: rnd(e.tEnd, 4), peak: rnd(e.peakT, 4), peakDb: rnd(e.peakDb, 2),
      tag: e.tag, word: e.word, facture: e.facture, mass: e.mass, direction: e.dir || null, f0: e.f0 ? rnd(e.f0, 1) : null, grains: e.grains.map(t => rnd(t, 4)) }));
    doc.layers = X.layers.map(l => ({ name: l.name, hz: rnd(l.hz, 1), share: rnd(l.share, 3), start: rnd(l.span[0], 4), end: rnd(l.span[1], 4) }));
    doc.issues = X.issues.filter(i => i.sev !== "ok").map(i => ({ severity: i.sev, text: i.text }));
    const D = await PHD.describe(F, buf);
    prog(0.9);
    Object.assign(doc.curves, {
      n1Kurtosis: resample(D.curves.kurtLocal || D.curves.kurt, F, cols, null, "max"), n2RepetitionHz: resample(D.curves.repHz, F, cols, null), n4Velocity: resample(D.curves.vel, F, cols, null),
      n5Cpp: resample(D.curves.cpp, F, cols, null), n5Coherence: resample(D.curves.pci, F, cols, null), n6Arrow: resample(D.curves.arrow, F, cols, null),
      n7Surprisal: resample(D.curves.surprisal, F, cols, null), n8Recurrence: resample(D.curves.recDensity, F, cols, null)
    });
    // onset strength (spectral flux, the beat tracker's input): where the hits and attacks are, scaled to its 99th percentile
    const Rr = WL.rhythmOf(F), envS = Array.from(Rr.env).filter(v => v > 0).sort((a, b) => a - b), envTop = envS.length ? envS[Math.floor(0.99 * (envS.length - 1))] || 1 : 1;
    doc.curves.onsetStrength = resample(Rr.env.map(v => Math.min(1, v / envTop)), F, cols, null, "max");
    const s = D.summary;
    doc.descriptors = plain({
      kurtosis: { K: s.kurtosis.K, Kmax: s.kurtosis.Kmax, band: s.kurtosis.band },
      repetition: { hz: s.repetition.hz, prominence: s.repetition.prom, speechIndexDb: s.repetition.speech },
      events: { rate: s.events.rate, meanDuration: s.events.meanDur, active: s.events.active, burstiness: s.events.B, bValue: s.events.bN >= 20 ? s.events.bValue : NaN, temporalEntropy: s.events.Ht },
      motion: { up: s.motion.up, down: s.motion.down, net: s.motion.net, medianAbs: s.motion.medianAbs },
      periodicity: { cpp: s.periodicity.cpp, cppP90: s.periodicity.cppP90, coherence: s.periodicity.pci },
      arrow: { T5: s.arrow.T5, T20: s.arrow.T20, T80: s.arrow.T80 },
      interest: { pir: s.info.pir, surprisal: s.info.surprisal, learning: s.info.info, symbols: s.info.K },
      recurrence: { RR: s.recurrence.RR, DET: s.recurrence.DET, LAM: s.recurrence.LAM, TT: s.recurrence.TT },
      decay: { n: s.decay.n, t60: s.decay.t60, logTauD: s.decay.logTau, material: s.decay.material, reliable: profile === "sfx" }
    }, 4);
  }
  if (profile === "music") {
    const R = WL.rhythmOf(F), St = WL.structureOf(F);
    doc.rhythm = { tempo: rnd(R.tempo, 2), reliable: !!R.reliable, clarity: rnd(R.medClar, 3), beats: R.beatTimes.map(t => rnd(t, 4)), downbeats: R.downTimes.map(t => rnd(t, 4)) };
    doc.sections = St.segs.map(sg => ({ start: rnd(sg.t0, 3), end: rnd(sg.t1, 3), label: sg.label }));
    // structural change (Foote novelty) and the local pulse (PLP, scaled by its clarity), for lanes and modulation
    const pulse = new Float32Array(F.nF);
    for (let m = 0; m < F.nF; m++) pulse[m] = R.plp[m] * Math.min(1, R.clarity[m] / 0.6);
    doc.curves.structureNovelty = resample(St.nov, F, cols, null, "max");
    doc.curves.localPulse = resample(pulse, F, cols, null, "max");
  }
  doc.timing = { ms: Date.now() - t0 };
  prog(1);
  return doc;
}

/* ---------------- the analysis-driven tools (effects bench) ---------------- */
// Runs one of the tools in effects.js on a sound: analysis, render, report. params default to the tool's own defaults;
// for "layers", params.gains is one gain in dB per layer (lowest layer first).
// Returns { tool, outputs: [{ suffix, sampleRate, channels: [Float32Array, ...] }], report: [string] }.
const FXroot = () => root.WLFX || (typeof require !== "undefined" ? require("./effects.js") : null);
function tools() {
  const FX = FXroot(); if (!FX) return [];
  return FX.TOOLS.map(t => ({ id: t.id, name: t.name, kind: t.kind, description: t.desc, params: Array.isArray(t.params) ? t.params.map(p => ({ id: p.id, label: p.label, type: p.type || "number", min: p.min, max: p.max, step: p.step, value: p.value, unit: p.unit || null, options: p.options || null })) : t.params }));
}
async function process(input, toolId, params, opts) {
  const FX = FXroot(); if (!FX) throw new Error("Playhead.process: effects.js is not loaded");
  const tool = FX.TOOLS.find(t => t.id === toolId); if (!tool) throw new Error("Playhead.process: unknown tool " + toolId + " (" + FX.TOOLS.map(t => t.id).join(", ") + ")");
  const buf = toBuffer(input), F = await WL.analyse(buf), cls = (opts && opts.assetClass) || (F.dur <= 10 ? "sfx" : F.guess);
  const an = { F, cur: WL.computeCurves(F, cls, { scale: "fixed" }), X: WL.sfxOf(F, buf, (opts && opts.name) || ""), R: WL.rhythmOf(F) };
  const p = {}; if (Array.isArray(tool.params)) for (const d of tool.params) p[d.id] = d.value;
  if (tool.params === "layers") p.gains = an.X.layers.map(() => 0);
  Object.assign(p, params || {});
  const r = await tool.run(buf, an, p, opts && opts.onProgress);
  return { tool: toolId, params: p, outputs: r.outputs.map(o => ({ suffix: o.suffix, sampleRate: o.buf.sampleRate, channels: Array.from({ length: o.buf.numberOfChannels }, (_, c) => o.buf.getChannelData(c)) })), report: r.report };
}

/* ---------------- curves as modulators ---------------- */
// Value of a document curve at time t (seconds), linearly interpolated; fallback (default null) where it is silent.
// For driving generator and effect parameters: Playhead.curveAt(doc, "activity", t) or curveAt(doc, "n4Velocity", t, 0).
function curveAt(doc, name, t, fallback) {
  const c = doc && doc.curves && doc.curves[name], fb = fallback === undefined ? null : fallback;
  if (!c || !c.length) return fb;
  const x = Math.max(0, Math.min(c.length - 1, t * doc.curves.rate - 0.5)), i = Math.floor(x), f = x - i, a = c[i], b = c[Math.min(c.length - 1, i + 1)];
  if (a == null && b == null) return fb;
  if (a == null) return f >= 0.5 ? b : fb;
  if (b == null) return f < 0.5 ? a : fb;
  return a + (b - a) * f;
}

/* ---------------- markers for editors and engines ---------------- */
// { kind: "region" | "marker", start, end?, name } sorted by time: sections as regions, downbeats and events as markers
function toMarkers(doc, opts) {
  const kinds = (opts && opts.kinds) || ["sections", "events"];
  const out = [];
  if (kinds.includes("sections") && doc.sections) for (const s of doc.sections) out.push({ kind: "region", start: s.start, end: s.end, name: s.label });
  if (kinds.includes("events") && doc.events) for (const e of doc.events) out.push({ kind: "marker", start: e.start, name: e.tag + " " + e.word });
  if (kinds.includes("downbeats") && doc.rhythm && doc.rhythm.reliable) doc.rhythm.downbeats.forEach((t, i) => out.push({ kind: "marker", start: t, name: "bar " + (i + 1) }));
  if (kinds.includes("onsets") && doc.onsets) for (const t of doc.onsets) out.push({ kind: "marker", start: t, name: "onset" });
  return out.sort((a, b) => a.start - b.start);
}

/* ---------------- strip renderer: layers and looks ---------------- */
// A strip is a list of layers drawn on one time axis, earlier layers behind later ones:
//   { type: "wave", colour }          the waveform; colour names a 0..1 curve that tints each column along the theme's
//                                      ramp (waveLow -> waveHigh), or null for the plain waveform. Over a spectrogram
//                                      it is drawn as an outline
//   { type: "spec" }                  a mel spectrogram (40 bands, 40 Hz to 16 kHz), low frequencies at the bottom
//   { type: "line", curve, range }    a curve drawn over the waveform; range [lo, hi] maps to the strip height
//                                      (default [0, 1]; the 5th-95th percentile for the descriptor curves n1-n8)
//   { type: "lane", curve, signed }   a curve in its own lane under the waveform. signed: for change curves such as
//                                      structureNovelty; drawn up where the level rises across the change (an entry),
//                                      down where it falls (a drop, a break into silence) and centred where the level
//                                      stays and only the material changes, so a break never reads as "more"
//   { type: "sections" }              boundaries and letters (music profile; a letter repeats when material returns)
//   { type: "ticks", of: [...] }      marks under the waveform: "events", "onsets", "beats", "downbeats", and from the
//                                      top edge "novelty" (the strongest spectral surprises: new sounds, drops, entries)
//   { type: "outline" }               the waveform's outline (min and max), over everything drawn before it
//   { type: "sculpt", curve }         reshapes the waveform layer: its envelope follows the curve while its fine
//                                      structure stays, e.g. "sculpt:loudness" for a waveform as loud as it sounds (the
//                                      outline layer keeps the ordinary peaks, for comparison)
//   { type: "form", value }           how lanes are arranged: "lanes" (each in its own band under the waveform, the
//                                      default) or "overlay" (all of them over the waveform, in one band)
//   { type: "shape", value }          how a lane's curve is drawn: "base" (filled from the bottom, the default),
//                                      "mirror" (symmetric around the middle, like a waveform), "horizon" (folded into
//                                      three bands of rising strength, compact at small heights) or "line"
// Without a waveform or spectrogram the lanes use the whole strip, so curves can stand in for the waveform.
// A look is a named list of layers (LOOKS). A look code is the same list as short text, for menus, saved settings
// and feedback: "wave:harshness,line:activity,ticks:events" or "shape:mirror,form:overlay,wave,lane:activity".
const DEFAULT_THEME = {
  background: null, wave: "#9aa5ac", waveLow: [214, 226, 240], waveHigh: [24, 79, 149], line: "#eb6834", tick: "#13181b", text: "#13181b", rule: "#4e5960",
  lane: null, laneDown: null, laneFlat: null,     // lanes: rises (default: line), drops (default: rule), level-neutral changes (default: wave)
  series: null,                                   // colours of the 2nd and 3rd lane (default below)
  outline: null,                                  // the outline layer (default: wave)
  halo: null,                                     // outline behind letters, for reading them over a spectrogram (none by default)
  specRamp: [[0, 0, 4], [66, 10, 104], [147, 38, 103], [221, 81, 58], [252, 165, 10], [252, 255, 164]]   // spectrogram, quiet -> loud
};
const SERIES = ["#2f9378", "#7e60c4"];
const CURVE_LABELS = {
  loudness: "loudness", activity: "activity", arousal: "arousal", harshness: "harshness", tension: "tension", harmonicTension: "harmonic tension",
  surprise: "novelty (spectral surprise)", lufs: "loudness (LUFS)", structureNovelty: "structural change", localPulse: "local pulse", onsetStrength: "onset strength (hits)",
  n1Kurtosis: "impulsiveness", n2RepetitionHz: "repetition rate", n4Velocity: "spectral motion", n5Cpp: "periodicity", n5Coherence: "phase coherence",
  n6Arrow: "time arrow", n7Surprisal: "surprisal", n8Recurrence: "recurrence"
};
const TICK_KINDS = ["events", "onsets", "beats", "downbeats", "novelty"], FORMS = ["lanes", "overlay"], SHAPES = ["base", "mirror", "horizon", "line"];
const LOOKS = {
  auto: { name: "Automatic", desc: "Sound effects: harshness as colour, activity as the line, events. Music: activity as colour, tension as the line, sections, downbeats.", code: null },
  plain: { name: "Waveform only", desc: "The waveform without analysis, for comparison.", code: "wave" },
  spectrum: { name: "Spectrogram", desc: "A standard spectrogram (mel scale, 40 Hz to 16 kHz), for comparison.", code: "spec" },
  structure: { name: "Structure", desc: "Sections, structural change (up: entry, down: drop or break) and the local pulse under a plain waveform.", code: "wave,sections,lane:structureNovelty~signed,lane:localPulse,ticks:downbeats" },
  rhythm: { name: "Rhythm", desc: "Activity as colour, the local pulse, beats and downbeats.", code: "wave:activity,lane:localPulse,ticks:beats+downbeats" },
  tension: { name: "Tension", desc: "Harshness as colour, tension as the line, sections.", code: "wave:harshness,sections,line:tension,ticks:downbeats" },
  events: { name: "Events", desc: "Harshness as colour, activity in a lane, events marked.", code: "wave:harshness,lane:activity,ticks:events" },
  lanes: { name: "Separate lanes", desc: "Plain waveform with loudness, activity and harshness each in its own lane (no colour mixing).", code: "wave,lane:loudness,lane:activity,lane:harshness" },
  shapes: { name: "Curves as waveforms", desc: "No waveform: loudness, activity and harshness each drawn like a waveform, mirrored around its middle.", code: "shape:mirror,lane:loudness,lane:activity,lane:harshness" },
  overlay: { name: "Curves over the waveform", desc: "Activity and harshness drawn like waveforms on top of the waveform itself, to compare their shapes directly.", code: "form:overlay,shape:mirror,wave,lane:activity,lane:harshness" },
  horizon: { name: "Horizon lanes", desc: "Loudness, activity and harshness as horizon charts: compact lanes that keep detail at small heights.", code: "shape:horizon,wave,lane:loudness,lane:activity,lane:harshness" },
  specstructure: { name: "Spectrogram and structure", desc: "The spectrogram with sections, structural change and downbeats.", code: "spec,sections,lane:structureNovelty~signed,ticks:downbeats" },
  overview: { name: "Music overview", desc: "Curves only, overlaid and mirrored like a waveform: local pulse, activity and recurrence, with sections, bar lines and novelty marks.", code: "form:overlay,shape:mirror,sections,lane:localPulse,lane:activity,lane:n8Recurrence,ticks:downbeats+novelty" },
  character: { name: "Sound character", desc: "For sound effects. Curves only, overlaid and mirrored like a waveform: where the hits are (onset strength), harshness and impulsiveness (clicks, cracks, crackle), with onsets marked.", code: "form:overlay,shape:mirror,lane:onsetStrength,lane:harshness,lane:n1Kurtosis,ticks:onsets" },
  surprise: { name: "Hits and surprise", desc: "For sound effects. As Sound character, with spectral surprise (where the sound turns into something new) in place of impulsiveness.", code: "form:overlay,shape:mirror,lane:onsetStrength,lane:harshness,lane:n7Surprisal,ticks:onsets" },
  hits: { name: "Hits and pulse", desc: "The waveform with onset strength (where the hits are) and the local pulse as lanes, beats and bar lines.", code: "wave,lane:onsetStrength,lane:localPulse,ticks:beats+downbeats" },
  sculpted: { name: "Waveform as loud as it sounds", desc: "The waveform's envelope follows perceived loudness (a dB-like scale) while its detail stays; colour shows harshness, with the ordinary peak outline for comparison.", code: "sculpt:loudness,wave:harshness,outline,ticks:events+novelty" }
};
const isSfxDoc = doc => doc.profile === "sfx" || doc.assetClass === "sfx";
// the layers the renderer drew before looks existed; still the default, and still driven by the old options
function legacyLayers(doc, opts) {
  const sfx = isSfxDoc(doc);
  const L = [{ type: "wave", colour: opts.colour === undefined ? (sfx ? "harshness" : "activity") : opts.colour }];
  if (opts.sections !== false) L.push({ type: "sections" });
  L.push({ type: "line", curve: opts.curve || (sfx ? "activity" : "tension"), range: opts.lineRange });
  const of = []; if (opts.beats !== false) of.push("downbeats"); if (opts.events !== false) of.push("events");
  L.push({ type: "ticks", of, small: true });
  return L;
}
/** Look code -> layers. Accepts a look name ("structure") or a code; unknown parts are skipped. */
function parseLook(code) {
  if (Array.isArray(code)) return code;
  const c = String(code || "").trim();
  if (LOOKS[c]) return LOOKS[c].code ? parseLook(LOOKS[c].code) : null;
  const out = [];
  for (const tok of c.split(",").map(t => t.trim()).filter(Boolean)) {
    const [main, ...flags] = tok.split("~"), i = main.indexOf(":"), type = i < 0 ? main : main.slice(0, i), arg = i < 0 ? "" : main.slice(i + 1);
    if (type === "wave") out.push({ type, colour: arg || null });
    else if (type === "spec" || type === "outline") out.push({ type });
    else if (type === "sculpt" && arg) out.push({ type, curve: arg });
    else if (type === "line" && arg) out.push({ type, curve: arg });
    else if (type === "lane" && arg) out.push({ type, curve: arg, signed: flags.includes("signed") });
    else if (type === "sections") out.push({ type });
    else if (type === "ticks") { const of = arg.split("+").filter(k => TICK_KINDS.includes(k)); if (of.length) out.push({ type, of }); }
    else if (type === "form" && FORMS.includes(arg)) out.push({ type, value: arg });
    else if (type === "shape" && SHAPES.includes(arg)) out.push({ type, value: arg });
  }
  return out;
}
/** Layers -> look code. */
function lookCode(layers) {
  return (layers || []).map(l => l.type === "wave" ? "wave" + (l.colour ? ":" + l.colour : "") : l.type === "line" ? "line:" + l.curve
    : l.type === "lane" ? "lane:" + l.curve + (l.signed ? "~signed" : "") : l.type === "ticks" ? (l.of && l.of.length ? "ticks:" + l.of.join("+") : "")
    : l.type === "form" || l.type === "shape" ? l.type + ":" + l.value : l.type === "sculpt" ? "sculpt:" + l.curve : l.type).filter(Boolean).join(",");
}
const optionOf = (layers, type, dflt) => { const l = (layers || []).find(x => x.type === type); return l ? l.value : dflt; };
/** Plain-language description of layers, for a legend beside the strip. */
function describeLook(layers, doc) {
  const lab = k => CURVE_LABELS[k] || k, has = k => !doc || (doc.curves && Array.isArray(doc.curves[k]));
  const parts = [], form = optionOf(layers, "form", "lanes"), shape = optionOf(layers, "shape", "base");
  const lanes = (layers || []).filter(l => l.type === "lane");
  for (const l of layers || []) {
    if (l.type === "wave") parts.push(l.colour && has(l.colour) ? "colour: " + lab(l.colour) : "waveform");
    else if (l.type === "spec") parts.push(!doc || doc.spectrum ? "spectrogram (mel, low at the bottom)" : "spectrogram (not in this analysis)");
    else if (l.type === "outline") parts.push("outline: peak waveform");
    else if (l.type === "sculpt") parts.push(!(layers || []).some(x => x.type === "wave") ? "height: " + lab(l.curve) + " (shapes the waveform; add it to see)" : has(l.curve) ? "height: " + lab(l.curve) : "height: " + lab(l.curve) + " (not in this analysis)");
    else if (l.type === "line" && has(l.curve)) parts.push("line: " + lab(l.curve));
    else if (l.type === "lane") parts.push((form === "overlay" ? ((layers || []).some(x => x.type === "wave" || x.type === "spec") ? "over the waveform: " : "overlaid: ") : "lane: ") + lab(l.curve) + (has(l.curve) ? (l.signed ? " (up: entry, down: drop or break, centred: same level)" : "") : " (not in this analysis)"));
    else if (l.type === "sections" && (!doc || doc.sections)) parts.push("letters: sections");
    else if (l.type === "ticks" && l.of) {
      const rh = r => r === "beats" || r === "downbeats";
      const ok = r => !doc || (r === "events" ? !!doc.events : r === "onsets" ? !!doc.onsets : r === "novelty" ? !!(doc.noveltyTicks && doc.noveltyTicks.length) : !!doc.rhythm);
      const of = l.of.filter(ok).map(r => r === "downbeats" ? "bar lines" : r);
      if (of.length) parts.push("marks: " + of.join(", ") + (doc && doc.rhythm && !doc.rhythm.reliable && l.of.some(rh) ? " (no steady beat: beats faint)" : ""));
      const missing = l.of.filter(r => !ok(r)).map(r => r === "downbeats" ? "bar lines" : r);
      if (missing.length) parts.push(missing.join(", ") + ": not in this analysis" + (missing.some(m => m === "beats" || m === "bar lines") ? " (music only)" : ""));
    }
  }
  if (lanes.length && shape !== "base") parts.push(shape === "mirror" ? "curves mirrored like a waveform" : shape === "horizon" ? "horizon charts (darker = higher)" : "curves as lines");
  return parts.join(" · ");
}
function autoRange(name, arr) {
  const v = arr.filter(x => x != null).sort((a, b) => a - b);
  if (!v.length) return [0, 1];
  let r;
  if (/^n\d/.test(name)) r = [v[Math.floor(0.05 * (v.length - 1))], v[Math.floor(0.95 * (v.length - 1))]];
  else if (name === "structureNovelty" || name === "localPulse") r = [0, Math.max(0.05, v[Math.floor(0.99 * (v.length - 1))])];
  else return [0, 1];
  if (r[1] - r[0] < 1e-9) r = [r[0] - 1, r[0] + 1];
  return r;
}
/** Whether a look shows something for this document: every curve it uses is there and not empty, and a spectrogram
 *  layer has data. For menus that hide looks a file cannot use (structure needs music, for example). */
function lookFits(doc, look) {
  const layers = parseLook(look) || legacyLayers(doc, {}), curves = doc && doc.curves || {};
  const live = k => Array.isArray(curves[k]) && curves[k].some(v => v != null && v !== 0);
  for (const l of layers) {
    if ((l.type === "lane" || l.type === "line" || l.type === "sculpt") && !live(l.curve)) return false;
    if (l.type === "wave" && l.colour && !live(l.colour)) return false;
    if (l.type === "spec" && !doc.spectrum) return false;
  }
  return true;
}
const specCache = typeof WeakMap !== "undefined" ? new WeakMap() : null;
function specBytes(sp) {
  if (!sp || !sp.data) return null;
  if (specCache && specCache.has(sp)) return specCache.get(sp);
  const u8 = fromB64(sp.data);
  if (specCache) specCache.set(sp, u8);
  return u8;
}
const rampAt = (ramp, u) => { const x = Math.max(0, Math.min(1, u)) * (ramp.length - 1), i = Math.min(ramp.length - 2, Math.floor(x)), f = x - i; return ramp[i].map((v, k) => Math.round(v + (ramp[i + 1][k] - v) * f)); };

// Draws a strip into any Canvas 2D context (page canvas, OffscreenCanvas in a worker).
// opts: width, height (default: the context's canvas), from, to (seconds, for zoom), theme, and one of
//   look    a look name from LOOKS or a look code
//   layers  a list of layers
//   (none)  the automatic look, adjustable with the older options curve, colour, lineRange, events, sections, beats
// Returns { code, layers, legend, items }: what was drawn as a look code, a plain-language legend, and one
// { label, colour } per coloured element for hosts that draw their own key.
function drawStrip(ctx, doc, opts) {
  opts = opts || {};
  const th = Object.assign({}, DEFAULT_THEME, opts.theme || {});
  th.lane = th.lane || th.line; th.laneDown = th.laneDown || th.rule; th.laneFlat = th.laneFlat || th.wave;
  const series = [th.lane].concat(th.series || SERIES);
  th.outline = th.outline || th.wave;
  const layers = opts.layers ? parseLook(opts.layers) : opts.look ? (parseLook(opts.look) || legacyLayers(doc, opts)) : legacyLayers(doc, opts);
  const W = opts.width || (ctx.canvas && ctx.canvas.width) || 600, H = opts.height || (ctx.canvas && ctx.canvas.height) || 64;
  const dur = doc.source.duration, t0 = Math.max(0, opts.from || 0), t1 = Math.min(dur, opts.to || dur), span = Math.max(1e-6, t1 - t0);
  const curves = doc.curves, rate = curves.rate, xOf = t => (t - t0) / span * W;
  const form = optionOf(layers, "form", "lanes"), shape = optionOf(layers, "shape", "base");
  const hasMain = layers.some(l => l.type === "wave" || (l.type === "spec" && doc.spectrum));
  // lanes take the bottom of the strip (or all of it without a waveform); overlaid curves share the main band
  const lanes = layers.filter(l => l.type === "lane" && Array.isArray(curves[l.curve]));
  const stacked = form === "lanes" && lanes.length > 0;
  const laneH = !stacked ? 0 : hasMain ? Math.max(6, Math.round(H * (lanes.length === 1 ? 0.28 : lanes.length === 2 ? 0.22 : 0.18))) : H / lanes.length;
  const mainH = hasMain ? Math.max(4, H - laneH * (stacked ? lanes.length : 0)) : (stacked ? 0 : H);
  const items = [];
  if (th.background) { ctx.fillStyle = th.background; ctx.fillRect(0, 0, W, H); }
  // value of a curve over [ta, tb): the mean of its samples there, or linear interpolation when a sample spans several pixels
  const valAt = (arr, ta, tb) => {
    if (!arr || !arr.length) return null;
    const ia = ta * rate, ib = tb * rate, n = arr.length;
    if (ib - ia >= 1) { let s = 0, k = 0; for (let i = Math.max(0, Math.floor(ia)); i < Math.min(n, Math.ceil(ib)); i++) if (arr[i] != null) { s += arr[i]; k++; } return k ? s / k : null; }
    const x = Math.max(0, Math.min(n - 1, (ia + ib) / 2 - 0.5)), i = Math.floor(x), f = x - i, a = arr[i], b = arr[Math.min(n - 1, i + 1)];
    return a == null ? (f > 0.5 ? b : null) : b == null ? (f < 0.5 ? a : null) : a + (b - a) * f;
  };
  const mix = (a, b, u) => "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * u)).join(",") + ")";
  const specBefore = l => { const i = layers.indexOf(l); return doc.spectrum && layers.some((x, j) => x.type === "spec" && j < i); };
  const sculptL = layers.find(l => l.type === "sculpt" && Array.isArray(curves[l.curve])), sculpt = sculptL ? curves[sculptL.curve] : null;
  // the waveform per pixel column as [top, bottom] (fractions of the half-height, + up); with a sculpt curve the
  // envelope (a running maximum over about 1% of the width) is replaced by the curve and the detail inside it is kept
  let colsCache = null;
  const waveCols = () => {
    if (colsCache) return colsCache;
    const wv = doc.wave, wc = wv.columns, hi = new Float32Array(W), lo = new Float32Array(W);
    let peak = 1e-9; for (let c = 0; c < wc; c++) peak = Math.max(peak, Math.abs(wv.min[c] || 0), Math.abs(wv.max[c] || 0));
    for (let x = 0; x < W; x++) {
      const ta = t0 + x / W * span, tb = t0 + (x + 1) / W * span, ca = Math.floor(ta / dur * wc), cb = Math.max(ca + 1, Math.ceil(tb / dur * wc));
      let a = 0, b = 0; for (let c = ca; c < cb && c < wc; c++) { a = Math.min(a, wv.min[c] || 0); b = Math.max(b, wv.max[c] || 0); }
      hi[x] = b / peak; lo[x] = a / peak;
    }
    if (sculpt) {
      const r = Math.max(2, Math.round(W / 100)), amp = x => Math.max(hi[x], -lo[x]), env = new Float32Array(W);
      for (let x = 0; x < W; x++) { let m = 0; for (let k = Math.max(0, x - r); k <= Math.min(W - 1, x + r); k++) m = Math.max(m, amp(k)); env[x] = m; }
      for (let x = 0; x < W; x++) {
        const v = valAt(sculpt, t0 + x / W * span, t0 + (x + 1) / W * span), g = v == null || env[x] < 1e-4 ? 0 : Math.max(0, Math.min(1, v)) / env[x];
        hi[x] *= g; lo[x] *= g;
      }
    }
    return (colsCache = { hi, lo });
  };
  const draw = {
    wave(l) {
      if (!mainH) return;
      const H = mainH, mid = H / 2, wv = doc.wave, wc = wv.columns;
      let peak = 1e-9; for (let c = 0; c < wc; c++) peak = Math.max(peak, Math.abs(wv.min[c] || 0), Math.abs(wv.max[c] || 0));
      const colour = l.colour && curves[l.colour] ? curves[l.colour] : null, sc = sculpt ? waveCols() : null;
      const outline = specBefore(l);
      if (colour) items.push({ label: CURVE_LABELS[l.colour] || l.colour, ramp: [th.waveLow, th.waveHigh] });
      if (outline) { ctx.strokeStyle = th.wave; ctx.lineWidth = 1; ctx.globalAlpha = 0.8; }
      const top = [], bot = [];
      for (let x = 0; x < W; x++) {
        const ta = t0 + x / W * span, tb = t0 + (x + 1) / W * span;
        const ca = Math.floor(ta / dur * wc), cb = Math.max(ca + 1, Math.ceil(tb / dur * wc));
        let lo = 0, hi = 0; for (let c = ca; c < cb && c < wc; c++) { lo = Math.min(lo, wv.min[c] || 0); hi = Math.max(hi, wv.max[c] || 0); }
        if (sc) { hi = sc.hi[x] * peak; lo = sc.lo[x] * peak; }
        const yTop = mid - hi / peak * (mid - 1), yBot = mid - lo / peak * (mid - 1);
        if (outline) { top.push(yTop); bot.push(yBot); continue; }
        const u = colour ? valAt(colour, ta, tb) : null;
        ctx.fillStyle = u == null ? th.wave : mix(th.waveLow, th.waveHigh, Math.max(0, Math.min(1, u)));
        ctx.fillRect(x, yTop, 1, Math.max(1, yBot - yTop));
      }
      if (outline) {
        for (const ys of [top, bot]) { ctx.beginPath(); ys.forEach((y, x) => x ? ctx.lineTo(x + 0.5, y) : ctx.moveTo(0.5, y)); ctx.stroke(); }
        ctx.globalAlpha = 1;
      }
    },
    outline() {
      // the ordinary peak waveform as a contour, so a sculpted or curve-only strip can be read against it
      const Hm = mainH || H, mid = Hm / 2, wv = doc.wave, wc = wv.columns;
      let peak = 1e-9; for (let c = 0; c < wc; c++) peak = Math.max(peak, Math.abs(wv.min[c] || 0), Math.abs(wv.max[c] || 0));
      const top = [], bot = [];
      for (let x = 0; x < W; x++) {
        const ta = t0 + x / W * span, tb = t0 + (x + 1) / W * span, ca = Math.floor(ta / dur * wc), cb = Math.max(ca + 1, Math.ceil(tb / dur * wc));
        let lo = 0, hi = 0; for (let c = ca; c < cb && c < wc; c++) { lo = Math.min(lo, wv.min[c] || 0); hi = Math.max(hi, wv.max[c] || 0); }
        top.push(mid - hi / peak * (mid - 1)); bot.push(mid - lo / peak * (mid - 1));
      }
      ctx.strokeStyle = th.outline; ctx.lineWidth = 1; ctx.globalAlpha = 0.85;
      for (const ys of [top, bot]) { ctx.beginPath(); ys.forEach((y, x) => x ? ctx.lineTo(x + 0.5, y) : ctx.moveTo(0.5, y)); ctx.stroke(); }
      ctx.globalAlpha = 1;
      items.push({ label: "peak waveform", colour: th.outline });
    },
    spec() {
      const sp = doc.spectrum, u8 = specBytes(sp);
      if (!u8 || !mainH) return;
      const B = sp.bands, C = sp.columns, ramp = th.specRamp, c0 = Math.max(0, Math.floor(t0 / dur * C)), c1 = Math.min(C, Math.ceil(t1 / dur * C)), nc = Math.max(1, c1 - c0);
      const make = (w, h) => typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(w, h) : typeof document !== "undefined" && document.createElement ? Object.assign(document.createElement("canvas"), { width: w, height: h }) : null;
      const img = ctx.drawImage && make(nc, B), ig = img && img.getContext("2d");
      const sx = (c0 - t0 / dur * C) / (t1 - t0) * dur / C * W, cw = W / ((t1 - t0) / dur * C);     // where column c0 starts, and a column's width
      if (ig && ig.createImageData) {
        const id = ig.createImageData(nc, B);
        for (let c = 0; c < nc; c++) for (let b = 0; b < B; b++) { const rgb = rampAt(ramp, u8[(c0 + c) * B + b] / 255), o = ((B - 1 - b) * nc + c) * 4; id.data[o] = rgb[0]; id.data[o + 1] = rgb[1]; id.data[o + 2] = rgb[2]; id.data[o + 3] = 255; }
        ig.putImageData(id, 0, 0);
        const sm = ctx.imageSmoothingEnabled; ctx.imageSmoothingEnabled = true;
        ctx.drawImage(img, 0, 0, nc, B, sx, 0, nc * cw, mainH);
        ctx.imageSmoothingEnabled = sm;
      } else {      // no canvas to build an image on (tests, some workers): one rectangle per cell
        for (let c = 0; c < nc; c++) for (let b = 0; b < B; b++) { ctx.fillStyle = "rgb(" + rampAt(ramp, u8[(c0 + c) * B + b] / 255).join(",") + ")"; ctx.fillRect(sx + c * cw, (B - 1 - b) / B * mainH, cw + 0.5, mainH / B + 0.5); }
      }
      if (mainH >= 48 && W >= 200) {       // a few frequencies along the left edge
        const mel = f => 2595 * Math.log10(1 + f / 700), lo = sp.fmin, hi = sp.fmax, mlo = mel(lo), mhi = mel(hi);
        const yOf = f => { const k = (mel(f) - mlo) / (mhi - mlo) * (B + 1) - 1; return (1 - (k + 0.5) / B) * mainH; };
        ctx.fillStyle = th.text; ctx.globalAlpha = 0.65; ctx.font = "9px sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
        for (const [f, t] of [[100, "100"], [1000, "1k"], [10000, "10k"]]) { if (f <= lo || f >= hi) continue; const y = yOf(f); if (y > 6 && y < mainH - 6) ctx.fillText(t, W - 3, y); }
        ctx.globalAlpha = 1; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      }
    },
    sections() {
      if (!doc.sections) return;
      ctx.strokeStyle = th.rule; ctx.fillStyle = th.text; ctx.lineWidth = 1; ctx.font = Math.max(9, Math.round((mainH || H) / 6)) + "px sans-serif";
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";     // hosts may have left other settings behind
      if (ctx.setLineDash) ctx.setLineDash([2, 3]);
      for (const s of doc.sections) {
        if (s.start < t0 || s.start > t1) continue;
        const x = Math.round(xOf(s.start)) + 0.5;
        if (s.start > 0.05) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
        const ty = Math.max(9, Math.round((mainH || H) / 6));
        if (th.halo && ctx.strokeText) { ctx.save && ctx.save(); ctx.setLineDash && ctx.setLineDash([]); ctx.strokeStyle = th.halo; ctx.lineWidth = 3; ctx.strokeText(s.label, x + 3, ty); ctx.restore && ctx.restore(); }
        ctx.fillText(s.label, x + 3, ty);
      }
      if (ctx.setLineDash) ctx.setLineDash([]);
    },
    line(l) {
      const line = curves[l.curve];
      if (!line) return;
      const lr = l.range || autoRange(l.curve, line), Hm = mainH || H;
      items.push({ label: CURVE_LABELS[l.curve] || l.curve, colour: th.line });
      ctx.strokeStyle = th.line; ctx.lineWidth = Math.max(1, Hm / 40); ctx.beginPath(); let pen = false;
      for (let x = 0; x < W; x++) {
        const v = valAt(line, t0 + x / W * span, t0 + (x + 1) / W * span);
        if (v == null) { pen = false; continue; }
        const y = Hm - 2 - Math.max(0, Math.min(1, (v - lr[0]) / (lr[1] - lr[0]))) * (Hm - 4);
        if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
      }
      ctx.stroke();
    },
    lane(l) {
      const arr = curves[l.curve], k = lanes.indexOf(l);
      if (!arr || k < 0) return;
      const colour = series[k % series.length], over = !stacked;
      const y0 = over ? 0 : mainH + k * laneH, bandH = over ? (mainH || H) : laneH, lr = l.range || autoRange(l.curve, arr), norm = v => Math.max(0, Math.min(1, (v - lr[0]) / (lr[1] - lr[0])));
      items.push({ label: CURVE_LABELS[l.curve] || l.curve, colour, down: l.signed ? th.laneDown : undefined });
      const sep = !over && (hasMain || k > 0);
      if (sep) { ctx.fillStyle = th.rule; ctx.globalAlpha = 0.35; ctx.fillRect(0, y0, W, 1); ctx.globalAlpha = 1; }
      const top = y0 + (sep ? 1 : 0), h = bandH - (sep ? 1 : 0), mid = top + h / 2;
      // direction of a change: level after minus level before, over a window that grows with the sound (0.25 to 3 s)
      const loud = curves.loudness, win = Math.max(0.25, Math.min(3, dur * 0.04));
      const lvl = (a, b) => { let s = 0, n = 0; const i0 = Math.max(0, Math.floor(a * rate)), i1 = Math.min(loud ? loud.length : 0, Math.ceil(b * rate)); for (let i = i0; i < i1; i++) { s += loud[i] == null ? 0 : loud[i]; n++; } return n ? s / n : 0; };
      const alpha = over ? (l.signed ? 0.55 : 0.2) : 1, edgeTop = [], edgeBot = [];     // overlaid curves: a light fill and a contour, so the waveform stays visible
      if (!l.signed && shape === "line") {
        ctx.strokeStyle = colour; ctx.lineWidth = Math.max(1.25, h / 30); ctx.beginPath(); let pen = false;
        for (let x = 0; x < W; x++) { const v = valAt(arr, t0 + x / W * span, t0 + (x + 1) / W * span); if (v == null) { pen = false; continue; } const y = top + h - 1 - norm(v) * (h - 2); if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; } }
        ctx.stroke();
      } else {
        ctx.globalAlpha = alpha;
        for (let x = 0; x < W; x++) {
          const ta = t0 + x / W * span, tb = t0 + (x + 1) / W * span, v = valAt(arr, ta, tb);
          if (v == null) continue;
          const u = norm(v);
          if (u <= 0 || (l.signed && u < 0.12)) continue;     // signed lanes: the weakest changes are left out, so boundaries stand out
          if (l.signed) {
            const tm = (ta + tb) / 2, d = loud ? lvl(tm, tm + win) - lvl(tm - win, tm) : 0, half = h / 2;
            if (d > 0.03) { ctx.fillStyle = colour; ctx.fillRect(x, mid - u * half, 1, Math.max(1, u * half)); }
            else if (d < -0.03) { ctx.fillStyle = th.laneDown; ctx.fillRect(x, mid, 1, Math.max(1, u * half)); }
            else { ctx.fillStyle = th.laneFlat; ctx.fillRect(x, mid - u * half / 2, 1, Math.max(1, u * half)); }
            continue;
          }
          ctx.fillStyle = colour;
          if (over) { edgeTop[x] = shape === "mirror" ? mid - u * h / 2 : top + h - u * h; if (shape === "mirror") edgeBot[x] = mid + u * h / 2; }
          if (shape === "mirror") ctx.fillRect(x, mid - u * h / 2, 1, Math.max(1, u * h));
          else if (shape === "horizon") {
            for (let j = 0; j < 3; j++) { const a = Math.max(0, Math.min(1, u * 3 - j)); if (a <= 0) break; ctx.globalAlpha = alpha * (0.35 + 0.3 * j); ctx.fillRect(x, top + h - a * h, 1, Math.max(1, a * h)); }
            ctx.globalAlpha = alpha;
          } else ctx.fillRect(x, top + h - u * h, 1, Math.max(1, u * h));
        }
        ctx.globalAlpha = 1;
        if (over && !l.signed && shape !== "horizon") {
          ctx.strokeStyle = colour; ctx.lineWidth = 1.25;
          for (const e of [edgeTop, edgeBot]) { if (!e.length) continue; ctx.beginPath(); let pen = false; for (let x = 0; x < W; x++) { if (e[x] == null) { pen = false; continue; } if (pen) ctx.lineTo(x + 0.5, e[x]); else { ctx.moveTo(x + 0.5, e[x]); pen = true; } } ctx.stroke(); }
        }
      }
      if (!over && h >= 11 && W >= 160) {
        ctx.fillStyle = th.text; ctx.globalAlpha = 0.7; ctx.font = Math.min(10, h - 2) + "px sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
        ctx.fillText(CURVE_LABELS[l.curve] || l.curve, 3, top + Math.min(10, h - 2)); ctx.globalAlpha = 1;
      }
    },
    ticks(l) {
      const of = l.of || [], Hm = mainH || H, r = doc.rhythm, ok = r && r.reliable, fade = ok ? 1 : 0.35;
      ctx.fillStyle = th.tick; ctx.strokeStyle = th.tick; ctx.lineWidth = 1;
      const vl = (ts, h, alpha, y0) => { if (alpha != null) ctx.globalAlpha = alpha; for (const t of ts) { if (t < t0 || t > t1) continue; const x = Math.round(xOf(t)) + 0.5; ctx.beginPath(); ctx.moveTo(x, y0 == null ? Hm - h : y0); ctx.lineTo(x, Hm); ctx.stroke(); } if (alpha != null) ctx.globalAlpha = 1; };
      // the default look keeps its small downbeat ticks; other looks draw beats and faint bar lines through the strip
      const legacyTicks = l.small === true;
      if (of.includes("beats") && r) vl(r.beats, Math.max(3, Math.round(Hm * 0.06)), 0.55 * fade);
      if (of.includes("onsets") && doc.onsets) vl(doc.onsets, 3, 0.6);
      if (of.includes("downbeats") && r) {
        if (legacyTicks) { if (ok) vl(r.downbeats, 4); }
        else { vl(r.downbeats, 0, 0.2 * fade, 0); vl(r.downbeats, Math.max(4, Math.round(Hm * 0.1)), fade); }
      }
      if (of.includes("novelty") && doc.noveltyTicks) for (const t of doc.noveltyTicks) {     // from the top edge, like the lab's novelty ticks
        if (t < t0 || t > t1) continue; const x = xOf(t);
        ctx.beginPath(); ctx.moveTo(x, 1); ctx.lineTo(x - 3, 7); ctx.lineTo(x + 3, 7); ctx.closePath(); ctx.fill();
      }
      if (of.includes("events") && doc.events) for (const e of doc.events) {
        if (e.start < t0 || e.start > t1) continue; const x = xOf(e.start);
        ctx.beginPath(); ctx.moveTo(x, Hm - 1); ctx.lineTo(x - 3, Hm - 6); ctx.lineTo(x + 3, Hm - 6); ctx.closePath(); ctx.fill();
      }
    }
  };
  for (const l of layers) if (draw[l.type]) draw[l.type](l);
  return { code: lookCode(layers), layers, legend: describeLook(layers, doc), items };
}

const api = { version: VERSION, schema: SCHEMA, schemaVersion: SCHEMA_VERSION, profiles: PROFILES, analyze, fromAnalysis, process, tools, toMarkers, curveAt, drawStrip, parseLook, lookCode, describeLook, lookFits, looks: LOOKS, curveLabels: CURVE_LABELS, forms: FORMS, shapes: SHAPES, theme: DEFAULT_THEME, internals: { WL, PHD, get FX() { return FXroot(); } } };
if (typeof module !== "undefined" && module.exports) module.exports = api;
root.Playhead = api;
})(__ph);

const Playhead = __ph.Playhead;
Playhead.internals = { WL: __ph.WL, PHD: __ph.PHD, FX: __ph.WLFX };
export default Playhead;
export const { version, schema, schemaVersion, profiles, analyze, process, tools, toMarkers, curveAt, drawStrip } = Playhead;
