# Changelog

Full release notes for each version are in [`docs/releases`](docs/releases).

## Unreleased
- **Playhead 0.3.0** (the vendored library, unchanged; checksum in its README). New in the Look
  menu: *Tempogram* and *Repetition* for music, *Modulation spectrogram* and *Curve rhythms* for any
  sound, drawn in place of the waveform. The *Bar raster* (a row per bar) folds time into rows, so
  it is offered for the grid thumbnails only, and its look code is refused in the viewer with that
  reason. Sounds are analysed again with the new version when shown (stored analyses are kept per
  version).

## 0.17.0 – Rows for long sounds, Playhead across the library
- **Rows for long sounds:** a *Rows* menu sets a row height (viewer and side panel); a long sound
  wraps onto rows of equal, round time spans, each with its own ruler. Click, select (also across
  rows), hover, zoom and playback work on every row, in every style.
- **Side panel:** drag the bottom edge of the sound preview to change its height (remembered).
- **Detail where needed:** rows and zoom read the part on screen again, finer (waveform down to
  single samples; spectrogram with matching colours). Spectrograms cover the whole of long sounds.
  Shift+wheel moves along a zoomed sound. Playback redraws only the cursor.
- **Playhead across the library** (optional): *Analyse sounds…* with the Playhead command line in the
  background, or in the browser; summaries in the catalog; **Look ▾ → Sound character** (hit or swell,
  bursty, metal-like, long ring, tonal, harsh, impulsive…); sorts by harshness, impulsiveness, ring
  time, burstiness, loudness range; **Playhead strips as grid thumbnails**; **markers** for REAPER
  and Audacity.
- **Fixes:** blank thumbnails of M4A files with their index at the end; `0.00 s` on the ruler of long
  sounds; spectrogram frequency labels of 48 kHz files.

## 0.16.0 – Playhead analysis of sounds (optional)
- **Playhead analysis** ([Playhead](https://github.com/eigensoundlab/playhead) 0.2.0, off by
  default; ⚙ Settings → Sounds): a new waveform style that shows Playhead's curves, events,
  beats and sections, analysed in the browser. A **Look** menu chooses among Playhead's looks or a
  pasted look code. The legend comes from Playhead, and a feedback link carries the look.
- **Side panel → Playhead:** events and their types, decay and material, time arrow, burstiness;
  tempo, section letters and loudness range for music.
- Kurator decodes the samples (`/api/audio/{id}/samples`), so every format it reads can be
  analysed. Results are stored per file and Playhead version (`/api/audio/{id}/playhead`).
- The usual waveform styles stay the default; Kurator draws only through `Playhead.drawStrip()`.

## 0.15.0 – Basis Universal textures, HDR pictures, PDF pages, datasets, licences
- **Basis Universal KTX2** (ETC1S and UASTC, also inside glTF via `KHR_texture_basisu`) decoded by
  Kurator itself - a Python port of the official transcoder, checked pixel for pixel against it.
  Thumbnails use the smallest mip level that is big enough. The newest Basis formats (UASTC HDR,
  XUASTC) and HDR ASTC go through the official `basisu` tool when it is installed.
- **HDR pictures:** OpenEXR (`.exr`: uncompressed, RLE, ZIP; scanline and tiled; layers, depth) and
  Radiance `.hdr`, tone-mapped.
- **PDF pages** in the viewer: `[` `]` or Page Up/Down, type a page number; zooming in renders the
  page sharper.
- **Vision models through any OpenAI-compatible server** (llama.cpp, LM Studio, vLLM...) besides
  Ollama, for tag suggestions and captions.
- **Training datasets:** pictures + caption `.txt` + `metadata.jsonl` (kohya-ss, OneTrainer,
  ai-toolkit, diffusers), captions from AI captions and/or tags, trigger word, duplicates and
  near-duplicates left out, resizing, format, train/validation split.
- **Source, author and licence** per file: panel fields, search (`licence:none`, `author:...`),
  XMP sidecars, export and dataset manifests; they follow files moved outside Kurator.
- **XMP:** keywords can be merged into sidecars written by Lightroom, darktable, digiKam or Bridge
  (their keywords and everything else stay; a copy is kept first).
- **Backup schedule:** a reminder every N days, or backups that run by themselves while Kurator is
  open and the disk is connected.

## 0.14.0 – print views, text-to-sound search, Unity import settings, KTX2
- **Print:** previews through embedded ICC profiles (CMYK, Adobe RGB…); colour mode, profile, print
  size; PDF page/trim/bleed, PDF/X, output intent, spot colours, fonts; **◧ Inks** (C/M/Y/K
  plates, total ink with a limit, ink readout), **soft proofing** with a CMYK profile (proof,
  plates, gamut warning), **▭ Boxes** for PDF trim and bleed.
- **Text-to-sound search** with LAION CLAP (AI add-on): `?rain on a tin roof` finds sounds, together
  with pictures; **S** finds sounds that sound alike.
- **Unity sprite import settings** (.meta, keeps GUIDs, links _normal/_mask maps, undo); sprite
  sheets and atlases saved into Unity projects come pre-sliced.
- **KTX2 textures:** uncompressed, BC1–7, ETC2/EAC and ASTC, Zstd/zlib; Basis Universal shows a card.
- Fixes: a .blend in the side panel could raise *'int' object is not iterable*; the side preview no
  longer jumps when the sharper picture or the scrollbar arrives; macOS test on case-insensitive disks.

## 0.13.0 – compressed glTF, binary USD, turntables, sound regions, Godot resources
- **Meshopt-compressed glTF** read by Kurator itself; **Draco glTF** and **binary USD**
  (`.usd`/`.usdc`, USDZ) with the optional 3D add-on (`install.bat 3d`), decoded in a separate
  process. USD materials (UsdPreviewSurface) bring their textures, also from inside USDZ.
- **Turntable on hover:** move the mouse across a 3D thumbnail to turn the model.
- **Hole-free simplification** of very dense models for previews (vertex clustering).
- **Waveform selection:** drag to select, Space plays it, loop, zoom to it, **save as a new file**
  (WAV/FLAC/Ogg/MP3, fades, normalising).
- **Godot 4 resources** from the sprites dialog: SpriteFrames with the animations, AtlasTexture per
  sprite.
- CI installs the 3D add-on, so Draco and USD are tested on Windows, macOS and Linux.

## 0.12.0 – textured 3D, lights, 2D lit preview, SVG sizes, engine projects
- **3D viewer with textures:** base colour, normal, emissive, occlusion and metal/roughness maps
  from glTF/GLB, OBJ and FBX (also embedded); views *Lit*, *Base colour*, *Clay*, *Normals*,
  *UV checker* and single maps; light presets, Alt+drag turns the lights, smooth shading from the
  file's normals.
- **Textured thumbnails**, and **texture detection**: the panel lists each model's maps and the
  missing ones; *Look ▾* filters models with, without or with missing textures.
- **2D companion maps** (`_normal`, `_glow`, `_ao`, `_depth`, `_sky`…): lit preview with a light
  that follows the mouse, glow halo, parallax, background layers; stack the maps under the picture.
- **Fixed:** out-of-memory errors when indexing big GLB files on many-core machines; `.obj` files
  that are compiled code; Inkscape SVGs ("invalid size": mm units, zero-size pages).
- **SVG: show all objects** (setting) grows the page to take in objects outside it.
- **Engine projects and repositories:** generated folders (Unity `Library/`, Unreal
  `Intermediate/`, build output, virtual environments) aren't indexed; Unity `.meta` and Godot
  `.import` files move with their assets; no XMP sidecars inside projects.
- Files that failed to index are tried again once after an update, and after memory errors.

## 0.11.0 – waveform view, sound analysis, look filters, timeline, sprite sheets and atlases
- **Waveform view** for sounds: playhead, click to play from there, drag to scrub, zoom, time ruler;
  styles: peak + RMS, min/max with clipping, level in dB, stereo lanes, spectrogram.
- **Loudness (LUFS), tempo (BPM) and key** for sounds; sort and filter by them and by length.
- **Look ▾ filter:** colours, shape (landscape, portrait, square, panorama, tall), size, transparency.
  Main colours in the side panel.
- **Timeline** beside the grid when sorted by date: drag through years and months.
- **Sprite sheets and texture atlases** (J → *Sprites / atlas*): packing, trim, padding, extrusion,
  power-of-two; animated GIF/WebP/APNG split into frames; PNG + TexturePacker JSON.
- The toolbar wraps on narrower windows instead of pushing the side panel off screen; an empty
  *Save into* no longer saves into the start folder.

## 0.10.0 – similar images, version stacks, compare, join and contact sheets
- **Similar images:** groups of near-identical pictures (resized, re-saved, slightly edited), three
  strictness levels, inside any folder, project or filter.
- **Version stacks:** sketch → lineart → colour shown as one item; Ctrl+G, *Stack by name*, ▤ to see
  all versions, *put on top*.
- **Compare** 2–4 images with synced zoom and pan, plus an A/B swipe view.
- **Join images** (horizontal, vertical, grid; target size, gap, background) and **contact sheets**
  (PDF or PNG/JPEG pages; page sizes, columns, captions, title), with a live preview.
- Fixes for the test suite on macOS and Windows (case-insensitive disks, path separators); Blender
  conversion uses a script file instead of a long command line.

## 0.9.0 – export to games, texture sets, duplicates
- **Project export** into Unity, Godot or Unreal folder layouts (or by type); models bring their
  textures and materials at the same relative paths; re-runs copy only what changed.
- **Texture sets** recognised by name; the side panel shows sets, what a model uses, and the models
  that use a texture.
- **Duplicates view**, and backups store identical files once (restores put them back everywhere).
- **Saved views** in the sidebar.
- `.blend` files open in the 3D viewer when Blender is installed (scripts switched off).
- Loading progress for 3D models, a *Back* button in the viewer, orange waveforms for long sounds.
- Install/update: a clean vulnerability report without the "kurator could not be audited" line.

## 0.8.0 – projects you work inside, and video
- **Projects as the top filter:**
  - Pick a project in the switcher (or the sidebar) and all folders, tags and filters work inside it.
  - Two modes: *In project* and *Add from library* (members marked ◆, **+** / **−** to add and remove).
  - Projects can include tags, folders and other projects automatically (live).
  - Move rules, backup folders, backup scopes and *No project* count those automatic members too.
- **Video:**
  - MP4/MOV/WebM/MKV/AVI and more, with recording dates, length, resolution and codecs.
  - Frame thumbnails and hover scrubbing (with ffmpeg).
  - Playback in the viewer for MP4, WebM, Ogg and most MOV files.
  - MP4/MOV dates and sizes are read without ffmpeg too.
- Previews are made again when ffmpeg is installed later, and the browser doesn't keep stale ones.
- Videos are hashed in chunks, so they never fill the memory.

## 0.7.0 – 3D models
- Rendered previews and a WebGL 3D viewer for glTF/GLB, OBJ, FBX, STL, PLY, DAE, 3MF, 3DS, OFF,
  USDA/USDZ and VOX.
- Blender thumbnails (also compressed, 5.x and `.blend1`); embedded 3MF and SketchUp previews.
- Clear filter rules (click / Ctrl+click / Alt+click); drag selected images onto tags and projects;
  *Remove from project*.
- Sounds play on click and arrow keys (auto-play switch), ▶ on waveforms.
- Quitting no longer waits for indexing to finish.

## 0.6.0 – file types, sounds, local AI
- Type filter with groups and presets. New previews for HEIC, PSD, Procreate, Clip Studio, SVG,
  PDF, Illustrator, EPS, CorelDRAW and InDesign, with Windows thumbnails as the fallback.
- Sound files: waveforms, player, BWF/iXML/ID3 metadata, levels, UCS categories.
- Optional local AI: search by description, find similar, tag pools (image–text model, trained on
  your tags, or Gemma via Ollama), review queue.
- Requires Python 3.11+.

## Earlier versions (0.1 – 0.5)
- Fast virtual grid for 100,000+ images, selection separate from focus, viewer, undo for selections,
  tags and moves.
- Dates kept and resolved from EXIF/XMP/PNG/file names, with the source shown. Metadata including AI
  generation parameters.
- Hierarchical tags with hotkeys; tag groups (subjects, projects, status, source).
- Safe moves with preview and undo; move rules with date tokens.
- XMP sidecars and keyword import; live folder watching; browsing and pinning any folder.
- Backups to disks and the cloud (rclone: Google Drive and others, several accounts), spread
  over several targets, original or tag-folder layout, verified, restorable.
- Install / update scripts with their own environment and a vulnerability check.
