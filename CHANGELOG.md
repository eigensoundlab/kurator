# Changelog

Full release notes for each version are in [`docs/releases`](docs/releases).

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
