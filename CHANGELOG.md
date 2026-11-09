# Changelog

Full release notes for each version are in [`docs/releases`](docs/releases).

## Unreleased (0.19.0) – Desktop app, right-click menu, Open with
- **Desktop app** (optional, `desktop/`, Tauri 2, app id `com.eigensound.kurator`): Kurator in its
  own window. It finds your Kurator folder (or asks once), starts Kurator without a console window -
  or uses one already running - and stops it when closed, also if the app crashes. In the window:
  **file drops with paths** (library files found by path, new ones copied from where they are,
  **folders added to the library** by dropping them), **dragging files out** into other programs
  (Windows, macOS), the system's **right-click menu**, links in the default browser, downloads to
  Downloads, one window that remembers its size and place. See [docs/desktop.md](docs/desktop.md).
- **Right-click menu** (browser and app) on thumbnails, folders, the path bar, tags, projects and the
  empty grid: Open, Open with, Show in Explorer / Finder, Copy path, Copy to…, Move to…, Send to…,
  Rename…, Resample…, Delete…; copy or move the selection into a folder, pin, add to / remove from
  the library; add a tag to / remove it from the selection, edit it; Select all.
- **Open with:** Krita, Photoshop, GIMP, Affinity, Pixelmator Pro, Aseprite, Paint.NET, Inkscape,
  Blender, Audacity, REAPER and VLC are found where their installers put them and offered for the
  files they open; add any other program (⚙ Settings → Open with → Programs…).
- Yes/no questions (remove a library folder, delete a tag, a backup set, a tag pool, empty the trash
  folder) are asked in the page instead of the browser's box, which the macOS app doesn't have.
- `--quit-on-stdin-close`: Kurator quits when its standard input closes (how the app runs it).
- **CI:** the desktop app is built for Windows, macOS (Apple silicon + Intel) and Linux on every
  change to it, started against Kurator (`--selftest`), and attached to published releases; macOS is
  signed and notarized once the Apple secrets are set.

## 0.18.1 – Drop files onto Kurator; fixes
- **Dropping files onto Kurator** (from Explorer, Finder, a browser): onto a tag or project they get
  it; onto the grid, the open project and the filtered tags. Files already in the library are found
  by name, size and time and tagged in place; new ones are copied into a folder you choose (per
  project) with their time, then tagged. One confirmation with *Don't ask again* (⚙ Settings →
  Dropping files); Ctrl+Z.
- **Fix:** case-only renames (shot1.png → SHOT1.png) on case-insensitive disks (macOS) no longer get
  " (2)".
- **CI:** failed tests are summed up on the run's Summary page - every failing job's OS, Python,
  test names, messages and tracebacks on one page, ready to copy.

## 0.18.0 – Copy, resample, rename, delete: file tools, quick destinations, delivery
- **Copy to… (K):** the Move dialog for copies, to any folder (also outside the library). Dates
  kept, every copy verified, never overwrites; XMP sidecars come along, Unity `.meta` / Godot
  `.import` don't (a copy needs its own GUID). In the library the copies appear at once with the
  original's tags, source, author and licence. Ctrl+Z deletes the copies that are unchanged.
- **For delivery** (Copy to…): remove private details (GPS, serial numbers, owner, maker notes,
  EXIF thumbnail, XMP / IPTC history, PNG text with AI prompts and ComfyUI workflows), write the
  author and licence in, or make one zip - JPEG, PNG and WebP without re-encoding.
- **Resample… (I):** resized copies - longest side, percent, width, height, fit, exact size;
  Lanczos, bicubic, bilinear, area average or nearest neighbour (pixel art); as the original or
  PNG / JPEG / WebP; next to the originals, in a subfolder or in one folder; colour profile, EXIF
  date, file time and animation kept; tags carried over; background job; Ctrl+Z.
- **Rename (F2):** a pattern with `{name}` `{n}` `{date}` `{tag}` `{project}` `{folder}` `{w}` `{h}`…,
  find / replace (also regular expressions), case; live preview; swaps and renumbering in place;
  sidecars follow; Ctrl+Z.
- **Delete (Del):** to the system's Recycle Bin / Trash (default) or Kurator's trash folder on the
  same disk (⚙ Settings → Delete, emptied after N days). Ctrl+Z brings files back with their
  catalog entries and tags; files restored from the system trash by hand get their tags back. On
  Windows, drives without a Recycle Bin (network, USB) use Kurator's trash folder.
- **Clean up duplicates** (Duplicates view): keep the copy with the most tags, the oldest / newest,
  the shortest path, the one in a folder or in the open project; it gets the others' tags; Ctrl+Z.
- **Quick destinations (D, then 1–9):** numbered folders that copy or move the selection at once;
  global, and per project in their place.
- **Choosing folders:** *Browse…* next to every destination field (drives, folders, new folder) and
  one list of recent destinations for all of them (pin, forget). The sidebar's *Add* / *Open* use a
  typed or pasted path, or open the picker when the field is empty (*Add…* / *Open…*).
- **Tagging:** **0 or Q** repeats the last tag used (Q in the viewer; first chip in the tag bar);
  **hotkeys per project** (*Edit…* on a project) replace the global keys while it is open; number
  keys work without Shift on Czech, Slovak and similar keyboards.
- **Per-project settings:** Move / Copy, Resample, Join / sheet, Rename and Clean-up remember their
  settings and folders per project. **Files ▾** in the status bar gathers the file tools.
- **Playhead 0.3.0** (the vendored library, unchanged; checksum in its README): *Tempogram* and
  *Repetition* for music, *Modulation spectrogram* and *Curve rhythms* for any sound in the Look
  menu; the *Bar raster* is offered for grid thumbnails only.
- **Fixes:** after an update the browser could keep running old scripts (the UI's files now ask
  every time, a 304 when unchanged); Escape in a dialog opened from another closes only that one;
  the KTX2 zstd test on Python 3.14 and the CMYK profile test on macOS.
- Catalog schema 12 (migrated on start). New dependency: Send2Trash (BSD-3) - run update.bat /
  update.sh.

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
