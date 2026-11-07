# Vendored files

## playhead-core.mjs

[Playhead](https://github.com/eigensoundlab/playhead) analysis and drawing library: a single ES
module without dependencies. Kurator imports it only when *Playhead analysis* is switched on in
⚙ Settings (`static/js/playhead.js`, `static/js/playhead-worker.js`).

| | |
|---|---|
| Version it reports | `0.3.0` (`Playhead.version`) |
| Source | https://github.com/eigensoundlab/playhead, tag `v0.3.0`, `node tools/build.js --dist` → `dist/playhead-core.mjs` |
| SHA-256 | `194cd22995a9c90c2c949c252a75a074a13b964bf13aed485e03715782f54236` |
| DOI of this version | [10.5281/zenodo.24043447](https://doi.org/10.5281/zenodo.24043447) (all versions: [10.5281/zenodo.23932625](https://doi.org/10.5281/zenodo.23932625)) |
| Licence | Apache-2.0, © 2026 Daniel Sandner (Playhead's NOTICE: `PLAYHEAD-NOTICE` here) |
| Paper | Sandner, D. (2026). *Playhead Beyond the Waveform: Cross-Field Descriptors for Sound Design* [Preprint]. Zenodo. https://doi.org/10.5281/zenodo.24008128 |

The file is kept **unchanged**; `.gitattributes` keeps git from changing its line endings, so the
checksum stays valid on every system.

This copy was made from the tagged sources by joining the four core files exactly as
`tools/build.js` does for `dist/playhead-core.mjs` (`coreESM()`), and the result was checked
against the checksum above.

### Updating

1. Build the new version: in a clone of the Playhead repository at the release tag, run
   `node tools/build.js --dist`. Or take `playhead-core.mjs` from that GitHub release.
2. Replace this file with `dist/playhead-core.mjs`. Don't edit it.
3. Update the version, the SHA-256 and the DOI above (`sha256sum playhead-core.mjs`, or
   `certutil -hashfile playhead-core.mjs SHA256` on Windows), and the line in `NOTICE`.
4. Run `python tools/ui_check_v16.py` (browser check of the Playhead view). Stored analyses are
   keyed by Playhead's version, so sounds are analysed again with the new build.

Kurator draws only through `Playhead.drawStrip()` and shows the legend it returns, so a new build
changes the look without changes in Kurator. One exception, from 0.3.0: looks that fold time into
rows (the bar raster, a row per bar) are kept out of the waveform view, whose cursor, selection, zoom
and rows keep time left to right (`phFoldsTime` in `static/js/playhead.js`); they draw as grid
thumbnails.
