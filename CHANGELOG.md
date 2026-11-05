# Changelog

Full release notes for each version are in [`docs/releases`](docs/releases).

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
