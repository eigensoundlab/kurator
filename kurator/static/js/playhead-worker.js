// Playhead analysis in a module worker, so the page stays responsive.
// Receives { jobId, sampleRate, channels: [Float32Array, ...], name } (the arrays are transferred),
// posts { jobId, progress } (0..1) while it works, then { jobId, doc } or { jobId, error }.

import Playhead from './vendor/playhead-core.mjs';

self.onmessage = async (e) => {
  const { jobId, sampleRate, channels, name } = e.data;
  try {
    const doc = await Playhead.analyze({ sampleRate, channels }, {
      profile: 'auto', name, onProgress: (p) => self.postMessage({ jobId, progress: p }),
    });
    self.postMessage({ jobId, doc });
  } catch (err) {
    self.postMessage({ jobId, error: String((err && err.message) || err) });
  }
};
