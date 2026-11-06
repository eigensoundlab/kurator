# Kurator

A fast, local asset manager for artists and game developers. Browse thousands of sketches, renders,
icons and textures, then tag, sort and move them comfortably. Every file keeps its **original date
and metadata**.

> Status: **v0.16 (alpha).** Browsing, tagging with groups (subjects, projects, status, source), safe
> moving, move rules, XMP sidecars, live folder updates, backups to disks and to the cloud
> (Google Drive and others via rclone), vector/document/art-app previews, sound files with a
> **waveform view, loudness, tempo and key**, 3D models (previews and a 3D viewer), video, projects
> you work inside, similar images, version stacks, colour and shape filters, a timeline,
> sprite sheets and texture atlases, **textured 3D models with lights**, a **lit preview for 2D art
> with normal/glow/depth maps**, **print previews (CMYK inks, ink limit, proofing, PDF boxes and
> spot colours)**, **Unity sprite import settings**, **Basis Universal KTX2 and HDR (EXR) textures**,
> **PDF page browsing**, **source / author / licence** per file, **training-dataset export**,
> backup reminders, an optional **Playhead analysis** view of sounds, and optional local AI (search pictures *and sounds* by description, find
> similar, tag suggestions from a pool you choose, Ollama or any OpenAI-compatible vision server)
> work.
> See the [Roadmap](#roadmap) and the [Changelog](CHANGELOG.md).

**Author:** Daniel Sandner. See [eigensound.com](https://eigensound.com/) (sound research) and
[sandner.art](https://sandner.art/) (AI/ML techniques for art and science).
**License:** [Apache 2.0](LICENSE). **Repository:** https://github.com/eigensoundlab/kurator

## Install and run

You need **Python 3.11+** (3.12+ recommended; on Windows it's needed to read file creation dates).
Python 3.10 reached its end of life in October 2026 and is no longer supported.
Kurator installs into its **own virtual environment** in `.venv`, so your system Python and its
packages are never touched.

| | Windows | Linux / macOS |
|---|---|---|
| Install (or repair) | double-click `install.bat` | `./install.sh` |
| Start | `kurator.bat` | `./kurator.sh` |
| Start with a folder | `kurator.bat --library "D:\Art"` | `./kurator.sh --library ~/Art` |
| Update | `update.bat` | `./update.sh` |
| Add the optional AI features | `install.bat ai` | `./install.sh ai` |
| Add the optional 3D add-on | `install.bat 3d` | `./install.sh 3d` |

- **Install** creates `.venv`, installs the **newest compatible versions** of all dependencies, then
  runs [`pip-audit`](https://pypi.org/project/pip-audit/) to check them for known vulnerabilities
  (`tools/audit_deps.py`: every installed package except Kurator itself, which isn't on PyPI).
- **Update** does `git pull` (if you cloned the repository) and then the same upgrade + audit. Once
  the AI features are installed, updates keep them.
- **`ai`** adds PyTorch and open_clip (2–3 GB). On Windows with an NVIDIA card, the script installs
  the CUDA build of PyTorch (the default one from PyPI is CPU-only there) and checks that the GPU
  works. On Linux the default PyTorch already includes CUDA; on Apple Silicon it uses Metal.
- **`3d`** adds DracoPy and Pixar's usd-core (about 60 MB) to read **Draco-compressed glTF** and
  **binary USD** (`.usd`, `.usdc`, USDZ with binary content). Both run in a separate process (see
  Security). Meshopt-compressed glTF needs nothing extra.
- Get the code with `git clone https://github.com/eigensoundlab/kurator` or download a release ZIP.

The browser opens at `http://127.0.0.1:8765/`. Library folders are remembered, and you can add more
from the sidebar. Upgrading keeps your catalog: it is migrated automatically, nothing is re-indexed.

| Option | Meaning |
|---|---|
| `--library PATH` | add a library folder (repeatable) |
| `--data-dir PATH` | catalog + thumbnail cache location (default: `%LOCALAPPDATA%\Kurator`, `~/Library/Application Support/Kurator`, `~/.local/share/kurator`) |
| `--port N` | default 8765 |
| `--workers N` | indexing processes (default: CPU cores − 1) |
| `--no-scan` | don't rescan on start |
| `--no-watch` | don't watch folders for changes (use *Rescan* instead) |
| `--no-ai` | don't offer the AI features even if they are installed |

### Keeping the catalog up to date

**Only changed files are processed**, never the whole catalog:

- **Live:** Kurator watches your library folders. New, edited, renamed, moved and deleted images show
  up within a few seconds, and only the affected folders are re-checked. Copying 1,000 files results
  in one small update.
- **On start / Rescan:** every folder is compared with the catalog by file size and modification
  time. Unchanged files cost one quick check each (about 3,000 files in 0.1 s). Only new or edited
  files are decoded again.
- An **edited** file keeps its tags and gets a new thumbnail. A file **moved or renamed in
  Explorer** gets its tags back automatically (matched by content).
- Network shares and some file systems don't report changes. Use *Rescan* there. The ⚙ Settings
  dialog shows which folders are watched.

**Quitting:** close the window, or press Ctrl+C in it. Kurator stops within a second or two, even in
the middle of indexing: what's done is saved, the rest continues at the next start. (Windows then
asks *Terminate batch job (Y/N)?*; either answer is fine.)

**Your files are never modified** by indexing, browsing or tagging. Files only change place when you
use **Move to…**, which shows a preview first and can be undone. Removing a library folder only
removes it from the catalog.

## What it does now

- **Indexes in parallel** (one process per core) and shows thumbnails **immediately**: anything on
  screen that isn't indexed yet gets its thumbnail on demand, first.
- **Many formats** with previews, including vector files, PDFs, art-app documents and sound (see
  [File types](#file-types-and-the-type-filter)).
- **Dates:** each file gets the most trustworthy date, and the app remembers where it came from:

  | Source | Badge |
  |---|---|
  | EXIF DateTimeOriginal, XMP CreateDate, Krita document date, PNG "Creation Time", .xmp sidecar | `IN FILE` |
  | EXIF DateTime (last save) | `SAVED` |
  | Date in the file name (`2019-04-12 …`, `IMG_20190412_…`, `Screenshot 2021-11-03 at …`) | `FILENAME` |
  | File system created / modified time | `GUESS` |

  The *Guessed dates only* filter lists the files whose date could be lost when copying, so you can
  fix them before moving anything.
- **Metadata kept and shown:** EXIF (camera, software, artist…), XMP, PNG text chunks, and **AI
  generation parameters** (A1111/Forge/Fooocus `parameters`, ComfyUI `prompt`/`workflow`, InvokeAI,
  NovelAI).
- **Folder tree** with counts, *include subfolders* toggle, name search, type filter and sorting by
  date, name, folder, type, size or dimensions.
- **Browse any folder** on any drive (sidebar *Browse*, or type a path) without adding it to the
  library. Only that folder's files are catalogued, and it is forgotten after 14 days unused.
  **+ Add to library** in the path bar indexes it with all subfolders, reusing work already done.
- **Pinned folders**: ☆ on any folder (library or not) puts it at the top of the sidebar.
- **Animated GIF / WebP / APNG** play in the viewer, in the side panel, and on hover in the grid
  (toggle with *▶ Hover*). There are no licence issues: the GIF patents expired in 2004 and WebP is
  BSD-licensed.
- **Show in Explorer / Finder** and **Open folder here** from the side panel.

### File types and the type filter

The **All types ▾** button in the toolbar opens a list of every supported extension, in groups,
with checkboxes and presets (*Finished images* = JPG/PNG/WebP, *Work files*, *Vector*, *Audio*…).
Nothing ticked means all types. The choice is remembered.

| Group | Extensions | Preview |
|---|---|---|
| Images | jpg jpeg jfif jpe png webp gif bmp tif tiff avif **heic heif** jp2 j2k jpx qoi ico icns pcx sgi rgb ppm pgm pbm pnm | full (HEIC via pi-heif, decode only) |
| Work files | **psd**, kra, ora, **procreate**, **clip** (Clip Studio), afphoto, xcf, sai, sai2, mdp, pdn | the embedded preview the app saved; Affinity/SAI/… show a card unless Windows has a thumbnail for them |
| Vector | **svg svgz** (resvg), **ai** (PDF-based Illustrator files), **eps** (embedded preview; Ghostscript if installed), **cdr** (embedded thumbnail), afdesign, fig, sketch, xd | rendered or embedded |
| Documents | **pdf** (first page; the viewer pages through all of them, pdfium), **indd** (XMP thumbnail), afpub, sla | first page / embedded |
| Game textures | tga, dds, blp, **ktx2**, **exr**, **hdr** | full; KTX2: uncompressed, BC1–7, **Basis Universal ETC1S and UASTC** (Kurator's own decoder), ETC2/EAC and ASTC (3D add-on), Zstd/zlib supercompression, newer Basis formats through the `basisu` tool if installed; OpenEXR (none/RLE/ZIP, scanline or tiled) and Radiance HDR tone-mapped |
| Video | **mp4 m4v mov webm ogv** (play in Kurator), mkv avi wmv flv mpg mpeg m2ts mts ts 3gp mxf vob | a frame + hover scrubbing (needs ffmpeg) |
| 3D models | **glb gltf obj fbx stl ply dae 3mf 3ds off usda usdz vox**, **usd usdc** (3D add-on), **blend** (also .blend1), ma mb max c4d ztl zpr abc 3dm skp lwo hip spp sbs sbsar | rendered by Kurator / Blender's saved thumbnail / embedded (3MF, SketchUp) / Windows |
| Audio | wav, mp3, ogg, oga, opus, flac, m4a, aac, aif/aiff/aifc, caf, w64, rf64, wma | waveform |

- **Licences:** everything is permissively licensed (pdfium BSD/Apache, resvg MIT, libheif via
  pi-heif LGPL as a separate library, libsndfile LGPL as a separate library). There is no GPL code.
  CorelDRAW, Affinity and InDesign files are never parsed beyond their embedded previews.
- **Basis Universal textures:** ETC1S and UASTC need nothing extra. The newest Basis formats
  (UASTC HDR, XUASTC) and HDR ASTC need the official `basisu` tool from the Basis Universal
  releases on GitHub. Put it on the PATH, or set `KURATOR_BASISU` to its path.
- **Windows thumbnails:** for formats Kurator cannot read itself, it asks Windows for the thumbnail
  that the installed application provides (e.g. Affinity, CorelDRAW), if any.
- SVG files are shown sandboxed (no scripts), even in the viewer.
- SVG sizes in mm, cm, in or pt (Inkscape's default) render correctly. A drawing whose page has no
  size (width 0) shows everything it contains. **⚙ Settings → SVG: show all objects** grows the
  page of every drawing to take in objects lying outside it, in previews and in the viewer.

### Sound files

Sound files sit in the same grid as images, with a **waveform** as the thumbnail and the length in
the corner. The side panel has a player and shows length, sample rate, channels, bit depth, **peak and
RMS level**, tags (ID3, Vorbis, MP4), **Broadcast WAV** (bext: description, originator, date,
time reference) and **iXML**. A UCS category in the file name (`DOORWood_Creak…`) is recognised.
The BWF origination date counts as the file's date.

**Sounds play when you click them or move onto them with the arrow keys**, like in sound-library
apps, so you can audition a folder quickly. The **♪ Auto-play** switch in the status bar (shown when
the view has sounds) or **P** turns this off; then Space plays.

| Key / mouse | |
|---|---|
| Click, arrow keys | select and play |
| ▶ on a waveform | play / stop without changing the selection |
| Space | play / stop the focused sound |
| P | auto-play on / off |
| O | open in your audio editor |

**Waveform view:** clicking a sound shows its waveform with a **playhead** in the side panel; Enter
(or double-click) opens it full size with a time ruler. Click anywhere to play from there, drag to
scrub, use the wheel to zoom into a part and double-click to see the whole sound again. The menu in the
corner switches between styles:

| Style | Shows |
|---|---|
| Peak + RMS | the classic look: peaks, with the average level inside |
| Min / max, clipping | exact sample extremes; samples at full scale are marked red |
| Level (dB) | a logarithmic scale from −60 to 0 dBFS with guides, so quiet tails are visible |
| Stereo lanes | each channel in its own lane |
| Spectrogram | frequencies over time (log scale up to 10 kHz) |

| Playhead analysis | optional, see below |

The choice is remembered.

**Playhead analysis (optional, experimental).** [Playhead](https://github.com/eigensoundlab/playhead)
is an analysis library by the same author. It describes a sound with more than a waveform:
- curves for loudness, activity, arousal, harshness, tension and spectral surprise;
- sound events with Schaeffer-style types (impulse, iterative, sustained; tonal or noisy);
- beats, downbeats and sections for music;
- descriptors borrowed from other fields: impulsiveness (spectral kurtosis), time arrow,
  burstiness, and decay and material class.

The method is described in Sandner, D. (2026), *Playhead Beyond the Waveform: Cross-Field
Descriptors for Sound Design* [Preprint], Zenodo,
[doi:10.5281/zenodo.24008128](https://doi.org/10.5281/zenodo.24008128).

- **Switch it on** in ⚙ Settings → Sounds → *Playhead analysis (experimental)*. It is off by
  default. While it is off, Kurator behaves as before.
- **View:** *Playhead analysis* in the waveform's style menu. The sound is analysed in the browser
  (a module worker, so the page stays responsive). Expect about half a second for a short effect
  and 3–4 s per minute of music.
  - Sounds over two minutes are analysed at 22.05 kHz mono; only the first 10 minutes are
    analysed.
  - Kurator decodes the sound, so every format it reads works (also AIFF, WMA and others the
    browser can't open).
  - Results are kept in the data folder, keyed by the file and the Playhead version, so a new
    Playhead build analyses again.
- **Look ▾** next to the style menu chooses how Playhead draws the analysis: its ready-made looks
  (*Sound character* and *Hits and surprise* for effects, *Structure* and *Rhythm* for music…).
  Looks a sound can't use are greyed out. *Custom…* takes a look code such as
  `wave,sections,lane:structureNovelty~signed,lane:localPulse,ticks:downbeats`.
  - The legend beside the strip comes from Playhead.
  - *Tell us if this helps* opens a short form in the Playhead repository with the look filled in.
    Nothing is sent from Kurator.
- **Side panel → Playhead:**
  - effects: events and their types, decay and material, time arrow, burstiness;
  - music: tempo, the section letters (e.g. `ABACA`), loudness range.
  - *Analyse with Playhead* when there is no analysis yet. Opening the panel never starts one.
- Kurator draws only through Playhead's own `drawStrip()`, so a new Playhead build changes the look
  by replacing one file (`kurator/static/js/vendor/playhead-core.mjs`, see the README there).
  The usual waveform styles stay the default.

**Loudness, tempo and key:** every sound gets its integrated **loudness (LUFS, ITU-R BS.1770 /
EBU R128)** and its loudest moment. Sounds of 8 seconds and longer also get an estimated **tempo
(BPM)** and **key**, shown as *estimated* in the side panel (they are guesses from the audio, like in
DJ software). Sort by *Length*, *Tempo* or *Loudness*, and filter by tempo, key and length in
**Look ▾**.

**Cut a region out:** in the full-size view, **drag** across the waveform to select a region
(Shift+click moves its nearer edge). The bar under it plays the selection (**Space**), loops it,
zooms to it, and **Save as new file…** (**E**) writes it as a new sound next to the original or into
any folder: WAV 16/24/32-bit float, FLAC, Ogg Vorbis, MP3 or the original's format, with short
equal-power fades against clicks and optional peak normalising. The original is never changed.

**Long sounds are orange:** anything from 30 seconds up (music, ambiences, dialogue) gets a warm
orange waveform, short effects stay blue, so the two are easy to tell apart at a glance.

Formats the browser cannot play (AIFF, CAF, WMA…) are still indexed and shown; *Open* plays them in
your default application. Reading M4A/AAC/WMA needs `ffmpeg` on the PATH; without it they're listed
without a waveform.

### AI tagging and search (optional, all local)

Install with `install.bat ai` / `./install.sh ai`, then use the **AI** button. Nothing is uploaded:
the models run on your computer. The first use of a model downloads it once from Hugging Face.

1. **Prepare images and sounds.** Kurator computes a compact "fingerprint" (an embedding from
   **SigLIP 2**) for each image, from the thumbnail cache, and one from **LAION CLAP** for each
   sound (from up to three 10-second windows). It is done once per file; a GPU makes it many times
   faster than a CPU. Choose *fast* (Base, 0.4 GB), *better* (Large, 1.8 GB) or *best* (So400m, 4.5 GB;
   fine on a 16 GB card) for images, and the CLAP model (general sounds and music, or music and
   speech, 0.8 GB) or *off* for sounds.
2. **Search by description:** type `?` and a description in the search box, e.g.
   `?pencil sketch of a dragon with wings` or `?rain on a tin roof`, `?wooden door creak`,
   `?sad solo piano`. Pictures and sounds come in one list, best first (each ranked by how much it
   stands out among its own kind).
3. **Find similar:** select an image or a sound and press **S** (or *≈ similar* in the panel).
4. **Tag pools** answer the question "which of *these* tags fits?". A pool is a set of tags the AI
   may choose from (e.g. *human / robot / creature / icon* for a characters folder). It never
   invents other tags. For each pool you choose:
   - **Mode:** one tag per image (human *or* robot) or several allowed.
   - **Method:**
     - *Image–text model*: works right away, no examples needed. Short descriptions per tag
       ("a pencil sketch of a person; a character turnaround") help a lot with sketches.
     - *Learn from my tags*: trains a small classifier on the images you have already tagged with
       the pool's tags (a few dozen per tag are enough) and reports its accuracy. Retrain any time;
       it takes seconds. Sub-tags count for their parent.
     - *Vision-language model*: asks a local vision model such as Gemma 3 or Qwen-VL with a fixed
       answer list, so it can only answer with your tags or "none of these". It can also write a
       **caption** per image (for training datasets). The model runs in **Ollama**
       (`ollama pull gemma3:12b`) or any **OpenAI-compatible server**: llama.cpp's `llama-server`
       (with `--mmproj`), LM Studio's local server, vLLM, LocalAI, Jan. Choose it under
       *AI → Vision model server* (address, optional API key, *Check*).
   - **Run on** the selection or the current view. In *one tag per image* mode, images that already
     have a tag from the pool are skipped.
5. **Review.** Suggestions are not tags until you accept them. They show as hollow dots in the grid
   and as dashed chips in the panel. *Review them* shows only images with suggestions; select and
   press **A** to accept or **Shift+A** to reject. Rejections are remembered, so the same wrong
   suggestion doesn't come back, and they teach *Learn from my tags* what a tag is not. You can also
   accept all suggestions above a score. Accepted tags are marked ✦ and can be undone with Ctrl+Z.
6. **Training datasets** (*AI → Training dataset → Export…*, works without the AI add-on too):
   - the selection or the view becomes pictures with caption `.txt` files and a `metadata.jsonl`.
     This is the layout kohya-ss, OneTrainer, ai-toolkit, SimpleTuner and diffusers read.
   - **Captions:** the AI captions, tags (choose the tag groups), or both, with an optional
     trigger word.
   - **Duplicates:** identical files and near-duplicates go in once (the biggest copy).
   - **Size and format:** optional downscaling, PNG/JPEG/WebP, transparency kept or flattened.
   - **Split:** train/validation with a seed.
   - `kurator_dataset.json` records every file's origin, caption, source, author and licence.

Embeddings are stored in `embeddings.db` (one per image content and model; duplicates share one),
next to the catalog.

### Video

Videos are catalogued like everything else, with their **recording date** (phones and cameras store
it in the file), length, resolution, frame rate and codecs.

- **Thumbnails** show a frame and the length. **Move the mouse across a video** in the grid to run
  through 8 frames of it (hover scrubbing, uses the *▶ Hover* switch).
- **Enter** plays MP4 (H.264), WebM, Ogg and most MOV files in the viewer: Space pauses,
  Shift+← → jumps 5 s, ← → go to the next file. Other formats (MKV, AVI…) open in your player with
  **O**.
- Previews need **ffmpeg** (free): on Windows `winget install ffmpeg`, on macOS `brew install ffmpeg`,
  on Linux your package manager. Restart Kurator, then *Rescan*. Kurator finds it on the PATH, or set
  `KURATOR_FFMPEG` to its folder. Without ffmpeg, Windows still shows its own video thumbnails, and
  MP4/MOV files still get their length, size and recording date.
- Big videos are read in chunks, never loaded into memory as a whole.

### 3D models

3D files are catalogued like images: tag them, move them, back them up, filter with the **3D models**
group of the type filter.

- **Previews** are rendered by Kurator itself (no Blender or other 3D software needed): a 3/4 view with
  flat shading and outlines, **with the base-colour textures** of glTF/GLB, OBJ and FBX models
  (embedded or next to the model), material colours, vertex colours and MagicaVoxel palettes. A
  million triangles take a few seconds, once. Big models take turns while indexing (two or three
  at a time; `KURATOR_HEAVY_JOBS` changes that), so many processor cores no longer run out of
  memory; if memory still runs short, the preview is made with fewer triangles and the file is
  tried again on the next scan.
- **Blender** files show the thumbnail Blender saved with the file (Blender 2.5 to 5.x, also
  compressed files and `.blend1` backups), plus the number of objects, meshes and materials.
- **3MF** files from slicers and CAD keep their own thumbnail; **SketchUp** files their embedded
  preview. Maya, 3ds Max, Cinema 4D, ZBrush, Alembic, Rhino and Substance files show the thumbnail
  Windows has for them (when the application is installed), or a card.
- **3D viewer:** Enter or double-click opens a live view: drag to turn, right-drag (or Shift+drag) to
  move, wheel to zoom, 0 or double-click to reset. ← → go to the next file as usual. A progress
  bar shows while a big model loads; **← Back** (top left) or Esc returns to the grid.
  - **Textures:** base colour, normal, emissive, occlusion and metal/roughness maps (glTF; OBJ
    `map_Kd`, `map_Bump`/`norm`, `map_Ke`…; FBX diffuse, normal and emissive textures, also
    embedded ones). PNG, JPEG, WebP, TGA, DDS, TIFF, BMP, PSD, KTX2 (also Basis Universal, glTF
    `KHR_texture_basisu`), EXR and HDR textures work. The
    geometry shows first, the textures follow.
  - **View:** *Lit*, *Base colour*, *Clay* (no textures), *Normals*, *UV checker*. Click a map in
    the list to see only that map on the model.
  - **Lights:** Studio, Sun and sky, Rim light, Headlight, Soft. **Alt+drag** turns the lights,
    **L** switches to the next preset. *Smooth* uses the file's normals; *DirectX normals* flips
    the green channel of normal maps made for DirectX/Unreal.
- **Textures assigned or not:** the side panel lists the maps a model uses, in red the ones that
  aren't found. Textures referenced with a path from another computer (`C:\Users\artist\...`) or
  another extension are looked up by name next to the model and in `textures/`, `Textures/`,
  `maps/`, `materials/`. **Look ▾ → 3D models** filters *with textures*, *without textures* and
  *textures missing on disk*.
- **Blender files in 3D:** when Blender is installed (found on the PATH, in the usual install
  folders, or via `KURATOR_BLENDER`), Enter on a `.blend` file converts it once in the background
  to glTF and shows it in the 3D viewer; later views are instant. Blender runs with
  `--disable-autoexec`, so scripts stored in a .blend file never run.
- **Texture sets:** maps named like `rock_albedo.png`, `rock_normal_4k.png`, `rock_roughness.png`
  are recognised as one set. The side panel shows the set (click a map to jump to it), the files a
  model uses (OBJ materials and textures, glTF buffers and images, textures named in FBX/3DS/DAE),
  and the models that use a texture.
- **Side panel:** triangles, vertices, dimensions and units, meshes, materials, animations, the
  application that wrote the file, and the **texture files** the model uses. FBX, COLLADA and 3MF
  creation dates count as the file's date.
- **Compressed glTF:** meshopt (`EXT_meshopt_compression`, e.g. from gltfpack) is decoded by Kurator
  itself; **Draco** (`KHR_draco_mesh_compression`) and **binary USD** need the optional 3D add-on
  (`install.bat 3d`). Without it they're listed with a card and a note saying so.
- **Turntable on hover:** move the mouse across a 3D thumbnail and the model turns (12 views, made
  while indexing; uses the *▶ Hover* switch like videos). `KURATOR_TURNTABLE=0` skips them.
- Very dense models are simplified for previews (vertex clustering: the surface stays closed).
- `.obj` files that are compiled program code (Visual Studio and other compilers use the same
  extension) are recognised and shown as *program code, not 3D*; inside code projects their
  `obj/` folders aren't indexed at all.

### 2D art with normal, glow and depth maps

Sprites and illustrations for 2D lighting (Godot, Unity 2D lights, Sprite DLight, Laigter) come with
companion maps: `hero.png` + `hero_normal.png`, `hero_glow.png`, `hero_ao.png`, `hero_depth.png`,
`hero_sky.png`… Kurator recognises them when they sit in the same folder and have the picture's
proportions:

| Suffix | Used as |
|---|---|
| `_normal` `_nrm` `_n` | normal map (lighting) |
| `_glow` `_emission` `_emissive` `_e` | glow: shines with a soft halo |
| `_ao` `_occlusion` | ambient occlusion |
| `_depth` `_height` `_parallax` `_p` | depth: parallax, and lighting when there's no normal map |
| `_specular` `_spec` `_s` | shine |
| `_sky` `_bg` `_background` | a layer behind the picture |
| `_diffuse` `_albedo` `_color` | the picture itself (when there's no plain `hero.png`) |

Other suffixes count as extra layers when they match the picture's size exactly, but never version
names like `_v2`, `_final` or `_old`.

- The side panel shows **2D maps** (click one to jump to it), **☀ lit preview**, and **▤ stack
  maps** (the grid then shows only the picture, with its maps stacked under it).
- In the viewer, **☀ Lit** (or **L**) turns on the lit preview: a point light follows the mouse,
  the normal map shapes the light, glow shines, AO darkens the ambient light, the depth map shifts
  the picture as the mouse moves, a sky/background layer sits behind it. Sliders for light height,
  reach, ambient, glow and parallax; warm, white, cool or torch light; *Flip green* for DirectX
  normal maps. The tabs show each map on its own.

### Projects: work inside one

A project (*Game A*, *Client B*, *Artbook*) is a container that can span all your library folders.
Pick it in the **project switcher** at the top left (or click it under *Projects* in the sidebar)
and Kurator works **inside** it: the folder tree shows only folders with project files, tag counts
are counted inside the project, and every filter (folders, tags, types, dates, search, AI search)
narrows the project down. Click the project again, or × in the path bar, to leave it.

**What belongs to a project:**

- **Files you add by hand:** drag selected images onto the project (sidebar, tag bar or the
  project chip in the path bar), press **+**, use the project's hotkey (1–9) or click it in the tag
  bar.
- **Things it includes automatically** (*Edit…* on the project), kept up to date as you work:
  - **tags**: everything tagged *Creatures*, including *Creatures/Dragons*, now and later;
  - **folders**: everything in `D:\Game A\Concepts`, including new files;
  - **other projects**: *Game A* can include *Game A – DLC*, which keeps its own contents.

**Two modes**, switched in the path bar:

| Mode | Shows | Use it to |
|---|---|---|
| **In project** | only the project's files | browse, tag, sort and move what's in it; **−** takes the selection out |
| **Add from library** | the whole library, project files marked ◆ | find more files for it; **+** or dragging adds them |

Files that are in the project only because of an automatic rule can't be removed one by one; *Edit…*
the rule instead (Kurator says so when you press −). Undo (Ctrl+Z) works for adding and removing.
Move rules, backup folders (`{project}`), backup scopes and *No project* all count automatic members
too. XMP sidecars only list projects you added by hand.

A typical round: open *Game A* → *Add from library* → open a folder of new work → select → **+** →
back to *In project* → tag and sort.

### Exporting a project to a game

*Export…* on an open project copies its files into your game project, sorted by type:

| Layout | Goes to |
|---|---|
| Unity | `Assets/<Project>/Sprites`, `Textures`, `Models/<model>/`, `Audio`, `Video`, `_Source` |
| Godot | `assets/<project>/sprites`, `textures`, `models/<model>/`, … with lower-case names |
| Unreal | `SourceArt/<Project>/Textures`, `Meshes`, `Audio`, `Movies` (to import from) |
| By type | `<Project>/Images`, `Textures`, `Models`, `Audio`, `Video`, `Source`, `Documents` |

- **Models bring their textures and materials** (OBJ `.mtl` and maps, glTF buffers and images,
  textures named in FBX/3DS/DAE, a texture set named like the model) at the **same relative paths**,
  so they import with their textures.
- Optional subfolders by subject (`Sprites/Creatures/Dragons`), or only the selected files.
- **Preview first**, then copy. Run it again later: only new and changed files are copied. Identical
  files are copied once. A `kurator_export.json` lists every file and where it came from.
- Exports copy, never move. The destination can't be inside a library folder (the copies would
  be catalogued twice).

### Saved views, duplicates and similar images

- **☆ Save view** in the path bar remembers the current folder, project, tags, types, search and sort
  under a name in the sidebar (*Saved views*), e.g. "Game A – creatures without status".
- **Duplicates** (in the sidebar when there are any) lists files whose exact content exists in more
  than one place, next to each other. The side panel shows *Also in* with the other locations.
- **Similar images** (sidebar) groups pictures that look nearly the same: resized, re-saved as JPEG,
  slightly edited, exported twice. Groups are shown one after the other, every other group with an
  orange frame; choose *nearly identical*, *very similar* or *similar* in the path bar. It works inside
  the current folder, project and filters. (A 64-bit picture fingerprint is made while indexing;
  for an existing catalog Kurator makes them from the thumbnails the first time you open the view.)

### Look filter: colours, shape, size, transparency (and sounds)

**Look ▾** in the toolbar narrows any view:

- **Colours:** 12 hues plus black, gray and white. A picture matches when the colour covers a good
  part of it (pick several for *any of them*). The side panel shows each picture's main colours;
  click one to copy its hex code.
- **Shape:** landscape, portrait, square, panorama (2:1 and wider), tall (1:2 and taller).
- **Size:** at least 512 / 1024 / 2048 / 4096 px on the long side. **Has transparency.**
- **Sounds:** tempo from–to, key, length from–to (seconds).

The active look filters show as one chip in the path bar, are kept in saved views, and *Clear filters*
removes them. Colours of an existing catalog are filled in from the thumbnails in the background a
little after the start.

### Timeline

When the grid is sorted by **date**, a timeline appears on its right edge: years and months where
their pictures start, with a bar showing how many each month holds. Hover to see the month and count,
click to jump to the start of a month, drag to move through the years quickly.

### Version stacks

Sketch → lineart → colour, or v01 → v02 → final: **stack** them and the grid shows one item, the
newest on top, with ▤ and the number of versions.

- Select the versions and press **Ctrl+G** (or *Stack as versions* in the side panel). **Stack by
  name** finds the groups for you inside the selection, per folder: `hero_sketch`, `hero_lineart`,
  `hero_color`, `hero_v2`, `hero (1)`, `hero copy` belong together.
- Click **▤** on a thumbnail to see all versions, oldest first. The side panel lists the versions
  (click to jump) and has *put on top* to choose which one represents the stack.
- If a filter matches only an older version (e.g. a tag only the lineart has), that version is shown.
- **Ctrl+Shift+G** unstacks. *Stacks* in the toolbar switches between stacked and all files.
  Stacks only group what you see; files stay where they are.

### Compare

Select 2–4 images and press **C**: they appear side by side, and zooming (wheel) or moving (drag)
moves all of them together, so the same detail stays lined up. **S** (two images) switches to a
swipe view: one over the other, the divider follows the mouse. **0** fits, **1** is 100%, Esc goes back.

### Join images, contact sheets, sprite sheets and atlases (J)

**J** (or *Join / sheet…* in the status bar) works on the selection (or the whole view), in grid order:

- **Join images:** side by side, stacked, or in a grid; every image scaled to the smallest or largest
  height/width; gap; background colour or transparent; optional result width/height. PNG, JPEG or WebP.
- **Contact sheet:** A4/A3/A5/Letter/Tabloid (portrait or landscape, 100–300 dpi) or any pixel size;
  columns; captions (name, date, tags); title; page numbers; background. One PDF, or PNG/JPEG pages.
- **Sprites / atlas** (for games):
  - **Texture atlas:** the images packed tightly (MaxRects), transparent borders trimmed, padding and
    edge **extrusion** against bleeding, optional power-of-two size, maximum size and scale.
  - **Sprite sheet:** equal cells in a grid for animation frames (columns, a fixed cell size if you
    like). Animated GIF / WebP / APNG files are split into their frames.
  - Saves a **PNG and a JSON** file in the TexturePacker *JSON Hash* layout (frame rectangles, trim
    offsets, source sizes, and `animations` for frame groups), which Phaser, PixiJS, Godot and Unity
    importers read.
  - **Godot 4 resources (.tres):** a *SpriteFrames* resource with the animations (for
    AnimatedSprite2D, at the frame rate you set), and for atlases one *AtlasTexture* per sprite (trim
    margins included). Saved inside a Godot project, the paths are `res://` paths; elsewhere they
    are relative, so the files can be copied into a project together.
- A live preview shows the result before saving. New files never overwrite existing ones; save into
  a library folder and they appear in the grid.

### Filters: how they combine

The path bar above the grid always shows what you're looking at: the project, the folder, and one
chip per filter (× removes it, *Clear filters* removes all but the project).

| Action | Result |
|---|---|
| Click a folder | That folder (and its subfolders if *Subfolders* is on). Tag filters stay. |
| Click a tag | **Everything with it**, in the whole library (or in the open project) |
| Ctrl+click a tag | Narrow the current view: *Icons* within this folder |
| Alt+click a tag or project | Hide items with it: everything **except** what's already in *Game B* |
| *No project*, *Untagged*… | What's left to sort (Ctrl+click: only in this folder) |

### Tags

- **Tag groups** keep different kinds of information apart:

  | Group | Example | Per image |
  |---|---|---|
  | **Subjects** (what it shows) | Creatures, Equipment/Weapons, Icons | any number |
  | **Projects** (where it's used) | Game A, Client B, Artbook | any number |
  | **Status** | concept, WIP, final | one (setting *final* replaces *WIP*) |
  | **Source** | own, reference, AI-generated | one |

  - Filters combine across groups: open *Game A*, then click *Icons* to get the icons used in Game A
    (see [Projects](#projects-work-inside-one) and [Filters](#filters-how-they-combine)).
  - Each group has its own "none" view: *Untagged*, *No project*, *No status*.
  - Finished projects can be **archived**: hidden from the tag bar, still searchable.
  - Folder layouts use the Subjects group. Projects, status and source are available as the
    `{project}`, `{status}` and `{source}` tokens in move rules and backup folders, e.g.
    `{project}/{tagpath}`.
  - In sidecars they become `Projects|Game A`, `Status|final`, etc., and are imported back into
    the right group.
- **Hierarchical tags:** `Creatures`, `Creatures/Dragons`, `Items/Weapons`. Filtering by a parent
  includes its children, and renaming a parent renames its children.
- **Hotkeys 1–9** add or remove a tag on the selection (or the focused image, or the image open in
  the viewer). Each new tag gets the next free hotkey; change it with ✎ in the Tags list.
- **T** opens a quick dialog to tag with any name; new names are created as tags.
- **Tag bar** above the status bar: click a tag to apply it. Each chip shows whether *all*, *some* or
  *none* of the selection has the tag.
- **Untagged** in the sidebar lists what's left to sort. Images you tag there are dimmed instead of
  disappearing; press **R** to drop them. Ctrl+click tags to combine filters.
- **Tags survive moves done outside Kurator** (Explorer, other apps). If a file disappears from one
  place and the same content appears elsewhere, its tags are re-attached by content hash. Removing a
  library folder keeps its tags for when it comes back.

### Print and desktop publishing

- **Colour profiles:** pictures with an embedded ICC profile (CMYK print files, Adobe RGB, ProPhoto,
  Gray) are shown through that profile (LittleCMS), as they print or as Photoshop shows them.
- **Side panel:** colour mode, profile and bit depth; **print size** at the picture's own resolution
  and at 300 ppi. For PDF and Illustrator files: page size (trim box) and **bleed**, PDF version,
  **PDF/X** conformance and output intent, **spot colours** (e.g. *PANTONE 185 C*) and **fonts**.
- **◧ Inks** (viewer, **I**) on CMYK pictures: the **C, M, Y, K plates** (in their ink colour or
  grey), **total ink** with an adjustable **ink limit** (300 % coated, 260–280 % uncoated, 240 %
  newsprint; areas above it turn red), and the ink percentages under the mouse.
- **Soft proofing for RGB pictures:** choose a CMYK profile of your printing condition in ⚙ Settings
  → Print (Kurator lists the profiles installed by Adobe apps, ECI/FOGRA or printer drivers, or give
  a path). ◧ Inks then shows the *Proof* (how it prints), the plates as they'd be separated, total
  ink, and **Gamut** (green: colours the press can't reach).
- **▭ Boxes** draws the trim (green) and bleed (red) boxes over a PDF page in the viewer.
- **PDF pages:** the viewer pages through multi-page PDFs and Illustrator files (`[` `]` or Page
  Up/Down, or type a page number). Zooming in renders the page again, so it stays sharp.

### Source, author and licence

- The side panel's **Source & licence** holds where a file came from (a URL, a shop, a book), who
  made it, and its licence.
  - Common licences are suggested (CC0, CC BY…, Own work, All rights reserved…), with a link to the
    licence text.
  - With several files selected, *Copy to the N selected* sets them all.
- **Search:** `source:artstation`, `author:jane`, `licence:cc-by`, and `licence:none` for what still
  needs a licence. Plain words in the search box look in these fields too.
- The fields are stored in **XMP sidecars** (`dc:source`, `dc:creator`, `dc:rights`,
  `xmpRights:WebStatement`), in project-export and dataset manifests, and follow files moved
  outside Kurator.

### Game projects and code repositories

Kurator keeps its catalog and all previews in its own data folder (see ⚙ Settings → *About*). It
never puts thumbnails, caches or database files into your library folders, so adding a Unity,
Godot or Unreal project or a git repository to the library leaves it exactly as it was. (Only things
you do yourself write there: moves, and XMP sidecars if you turn them on.)

- **Generated folders aren't indexed:** Unity's `Library/`, `Temp/`, `Logs/`, `obj/`, `Build/`;
  Unreal's `Intermediate/`, `Saved/`, `DerivedDataCache/`, `Binaries/`; build output (`obj/`,
  `bin/`, `Debug/`, `Release/`, `target/`, `out/`) next to Visual Studio, CMake, Cargo, Gradle or
  Maven projects; Python virtual environments; hidden folders (`.git`, `.godot`, `.import`, `.vs`…).
  Live updates ignore them too, so an engine rewriting its cache costs nothing.
- **Unity sprite import settings:** for pictures inside a Unity project, *Unity import…* in the side
  panel writes their `.meta` files the way the Inspector would: texture type Sprite, pixels per unit,
  filter (Point for pixel art), compression, mip maps, and the picture's `_normal` / `_mask` maps
  linked as secondary textures for 2D lights. Existing `.meta` files keep their GUID and every other
  setting; **Undo** puts the old files back (they are kept in Kurator's data folder). Sprite sheets and
  atlases saved into a Unity project get a `.meta` with the sprites already sliced (Sprite Mode
  *Multiple*, trimmed sprites keep their pivot). Unity re-imports when its window gets focus.
- **Moves keep engine references working:** a file's Unity `.meta` (its GUID, which scenes and
  prefabs refer to) and Godot `.import`/`.uid` files move with it, like XMP sidecars, and undo
  brings them back.

### Moving files (M or "Move to…")

1. Choose a destination inside the library; missing folders are created. Optional:
   **year subfolders** (`…/Creatures/2019/`) and **date in the file name**
   (`2019-04-12_dragon.png`, only when the name has no date yet).
2. **Preview** lists every file with its new name and location. Nothing moves before you confirm.
3. **Move**:
   - Same disk: an instant rename, and every timestamp, including Windows creation time, is kept.
   - Other disk: the file is copied, its modified and creation times are restored, and the copy is
     verified byte-for-byte (hash) before the original is deleted.
   - Existing files are never overwritten: name clashes get ` (2)`.
   - `.xmp` sidecars move along.
   - Tags, dates and metadata stay attached, because the catalog entry is updated in place.
4. **Ctrl+Z** moves everything back.

### Move rules (Shift+M or "Rules…")

Sort by tag in one step. For example:

| Tag | Destination template |
|---|---|
| `Creatures` | `D:\Art\Creatures\{subtag}\{year}` |
| `Equipment` | `D:\Art\Equipment\{subtag}` |
| `Icons` | `D:\Art\Icons` |

- The **first rule** whose tag (or a sub-tag of it) a file has decides where it goes; reorder rules
  with ↑ ↓.
- Tokens: `{year}` `{month}` `{day}` (from the file's date), `{tag}`, and `{subtag}`
  (`Creatures/Dragons` → `Dragons`). Empty parts are dropped.
- Apply rules to the selection or the current view. You get the same preview as with Move, the same
  date preservation, and one Ctrl+Z to undo everything.

### XMP sidecars and keyword import (⚙ Settings)

- **Write sidecars:** `photo.jpg` gets `photo.jpg.xmp` holding its tags (`dc:subject`,
  Lightroom-style `lr:hierarchicalSubject`, digiKam `TagsList`) and its creation date. Lightroom,
  Bridge, darktable and digiKam can read them, so your tagging work is never locked into Kurator.
  You can write them on demand, or automatically whenever tags change.
- Source, author and licence go into the sidecar too.
- **Images are never modified.** Sidecars created by other programs (Lightroom, darktable,
  digiKam, Bridge) are left alone, unless you tick *Also update sidecars of other programs*:
  - Kurator then adds its keywords to their keyword lists;
  - their own keywords and everything else (develop settings, ratings, history) stay as they were;
  - source, author and licence are added only where the sidecar has none;
  - a copy of each such sidecar goes to `xmp-backup` in Kurator's data folder before its first
    change.
- No sidecars are written inside **Unity, Godot or Unreal projects or git repositories**: Unity would
  make `.meta` files for them and git would list them as new files. The dialog says how many were
  left out.
- **Dates survive copies:** when re-indexing a file whose own timestamps were lost (copy, cloud
  sync), its sidecar's date is used (badge: `IN FILE`, source "sidecar").
- **Import keywords:** keywords already stored with your files become tags. This covers embedded XMP
  (Photoshop, Lightroom…), **Windows Explorer tags**, and `.xmp` sidecars.

### Backup (B or the "Backup" button)

A **backup set** defines three things:

- **What:** the whole library, or only some tags.
- **How:** original folders, or **tag folders** (`Equipment/Weapons/sword.png`).
- **Where:** one or more **targets**, such as external disks, NAS folders, or **cloud accounts**
  (see below).

How runs work:

- **Plan first.** Every run shows how many files are new, changed, missing from the backup or need
  to move inside it. It also shows **free space now, bytes to write, and free space after, for each
  target**. You confirm before anything is copied.
- **Incremental.** Only new or changed files are copied. Nothing is copied twice: a re-tagged file
  is *moved* inside the backup, and a file moved in Explorer is recognised by its content.
- **Several drives.** New files go to the first target with room ("fill in order") or to the one
  with the most free space. A *keep free* reserve per target protects each disk. Kurator records
  which file is on which target, so later runs and restores work across all of them. The plan warns
  you if two targets are on the same physical disk.
- **Tag folders.** The template uses `{tagpath}` `{tag}` `{subtag}` `{year}` `{month}`, e.g.
  `{tagpath}/{year}`. A file with several tags goes to its first tag (move-rule order, then A–Z).
  Untagged files go to `_untagged/` with their original folders. Name clashes get a short,
  stable suffix.
- **Safe.**
  - Every copy is written under a temporary name and verified (read back, checksum compared).
  - Dates are kept, including the Windows creation date.
  - Nothing in a backup is ever deleted. Replaced versions go to `_kurator_archive/<date>/`, and
    in *mirror* mode so do files removed from the library.
  - An interrupted run resumes where it stopped.
- **Readable without Kurator.** Each target holds `_kurator/manifest.jsonl` (every file's original
  location, creation date and its source, tags and checksum) and a snapshot of the catalog. Your
  `.xmp` sidecars are copied along.
- **Restore** puts missing files back where they were, or copies a whole set into a folder. It never
  overwrites, and it restores dates too.
- **Coverage.** The Backup dialog shows how many library files are in no backup yet, or changed
  since their last one.
- **When:** a set can **remind you** every day, week or month. A *Backup due* button then shows in
  the status bar. A set can also **back up by itself** while Kurator is open, when its disk is
  connected.
- **Identical files are stored once.** If the same image sits in several folders, the backup holds
  one copy and the others point to it (in the manifest too); a restore puts it back in every
  folder. If one of them is edited later, it gets its own copy and the rest stay safe. The setting
  *Store identical files once* is on by default.

#### Cloud targets (Google Drive, OneDrive, Dropbox, NAS…)

Cloud targets go through [rclone](https://rclone.org) (free, MIT licence). Install it once:
`winget install Rclone.Rclone` on Windows, `brew install rclone` on macOS, or your package
manager on Linux. Then in *Backup → New backup set → target type "Cloud"*:

- **Connect Google Drive…** opens Google's sign-in page in your browser.
  - Kurator asks only for the `drive.file` permission, so it can see **only the files it creates**,
    not the rest of your Drive.
  - Your login is stored in rclone's config, never by Kurator.
- **Other services:** run `rclone config` once in a terminal (OneDrive, Dropbox, pCloud, B2,
  SFTP/NAS, …). They then appear in the list.
- **Several accounts:** connect each Google account (e.g. `gdrive`, `gdrive2`) and add each as a
  target of the same set. New files are spread over them by free space, and the quota of each
  account is checked before every run (`rclone about`).
- **How uploads work:**
  - 4 uploads run in parallel.
  - Each one is verified against the provider's checksum (MD5 on Google Drive).
  - Re-tagging moves files on the server side instead of uploading them again.
  - Restores download and verify the files.
- **Daily limit:** when Google's daily upload limit (750 GB) is reached, the run stops cleanly.
  Just run it again the next day.
- **Encryption:** for client-side encryption, create an rclone `crypt` remote and use it as the
  target. Google then can't read your files, but you can't browse them on the web either.

### Browsing and selection

The **selection** (a set of items) is separate from the **focus** (the item you are looking at).
Scrolling, filtering, previewing, opening the viewer or switching folders never clears your
selection. If a filter hides some selected items, the status bar says so: *"152 selected · 40 hidden
by filter"*.

| Keys / mouse | Action |
|---|---|
| Click · Ctrl+click · Shift+click | select one · toggle · range |
| Drag on the grid | rectangle select (Ctrl/Shift = add); auto-scrolls at the edges |
| Drag selected images | drop them on a tag or project to add it |
| Arrows, PgUp/PgDn, Home/End | move focus; the selection follows only if it's a single item |
| Ctrl+arrows | move focus without touching the selection |
| Shift+arrows | extend selection |
| X or Ctrl+Space | toggle the focused item |
| Ctrl+A · Ctrl+I · Esc | select all · invert · clear |
| **Ctrl+Z / Ctrl+Y** | **undo / redo: selection changes, tag changes and moves** |
| 1 … 9 · T | tag hotkeys · tag with any name |
| M · Shift+M | move selection to a folder · move rules |
| B | backup |
| O | open in the default application |
| S · A · Shift+A | AI: find similar · accept suggestions · reject suggestions |
| + · − | with a project open: add the selection to it · take it out (otherwise: thumbnail size) |
| P | auto-play sounds on focus |
| R | refresh (drop items that no longer match the filter) |
| Backspace | parent folder |
| Space | quick look: tap to open, **hold to peek** and release to return (on a sound: play/stop) |
| Enter / double-click | viewer |
| Ctrl+wheel, + / − | thumbnail size |
| / | search (start with `?` to search by description with AI) |
| **Viewer:** ← → · X · 1…9 / T · wheel/drag · 0 / Z · B · N | prev/next · toggle selection · tag the shown image · zoom/pan · fit / toggle 100% · background (dark/gray/light/checker) · pixel-art zoom |

*Show only selected* in the status bar works as a temporary basket view.

## Performance

Measured in a 2-core cloud VM with headless Chromium:

| | |
|---|---|
| Grid with **102,000 items** | 60 DOM cells, scroll median 16.8 ms/frame (60 fps) |
| Select all / invert / undo at 100k | 25 ms / 8 ms / <1 ms |
| Item list for 100k (sorted, filtered) | ~0.4 s, 0.9 MB gzipped |
| Indexing | ~100 files/s **per worker process** (scales with cores) |
| Re-scan of an unchanged library | 3,000 files in 0.08 s |
| First thumbnails on a never-indexed folder | 0.34 s |

How it does this:
- **Virtualized grid:** only visible rows exist in the page, cells are recycled, and the layout is
  arithmetic (no waiting for images).
- **Fast thumbnails:** JPEGs decode at reduced scale (libjpeg DCT scaling). Placeholders use the
  image's average colour.
- **Packed WebP thumbnails in SQLite**, keyed by content hash, instead of 100k small files.
  Moved, renamed or duplicate files reuse them. URLs are content-addressed, so the browser caches
  them forever.
- **Abandoned downloads are cancelled** when cells scroll out of view.

## Architecture

```
kurator/
  __main__.py      CLI, starts the server (loopback only)
  server.py        FastAPI: /api/state, /api/folders, /api/items, /api/items/{id}, /t/{id} thumbs, /f/{id} originals
  scanner.py       folder walk + parallel processing (process pool, falls back to threads)
  processing.py    per file: content hash (BLAKE2b), decode, metadata, date, WebP thumbnails
  dates.py         date resolution and its trust order
  db.py            catalog.db (files, roots, tags, operations) + thumbs.db (blob cache), WAL, migrations
  tags.py          tag hierarchy, hotkeys, apply/toggle
  fileops.py       safe moves + undo, Windows creation time, folder listing, reveal
  rules.py         move rules: tag -> destination template
  projects.py      projects with automatic sources (tags, folders, other projects)
  assets.py        texture sets, model companions, project export to game folders
  similar.py       picture fingerprints (dHash) and grouping of similar images
  stacks.py        version stacks
  sheets.py        contact sheets and joined images
  maps2d.py        companion maps of 2D art (normal, glow, AO, depth, layers)
  projectdirs.py   engine projects and repositories: generated folders, sidecar rules
  unity.py         Unity .meta files: sprite import settings, sliced sprite sheets, undo
  printinfo.py     ICC conversion, CMYK inks, soft proofing, PDF print facts
  pdfpages.py      PDF pages for the viewer (sizes, rendering at any size, page boxes)
  basisu.py        Basis Universal ETC1S + UASTC decoder (port of the official transcoder)
  fileinfo.py      source / author / licence per file, licence names and links, search terms
  dataset.py       training datasets: captions, dedupe, resize, train/val split
  atlas.py         sprite sheets and packed texture atlases (+ TexturePacker JSON)
  audio_analysis.py  loudness (BS.1770), tempo, key, waveform data and spectrograms
  blender.py       optional: .blend -> glTF through an installed Blender (no auto-run scripts)
  xmp.py           XMP sidecars (write), keyword import (XMP, Windows tags, sidecars)
  watcher.py       live folder watching (watchdog), debounced re-check of changed folders
  backup.py        backup sets, planner (layouts, spreading over targets, space), runner, restore
  backup_targets.py  targets: local folders, cloud (rclone remote control API)
  rclone.py        rclone detection, one background daemon (loopback + random password), Google Drive connect
  handlers/        one module per file family; add a module to support a new format
    image.py       Pillow formats (+ HEIC) + EXIF/XMP/PNG text/AI parameters
    krita.py       .kra / .ora
    artapps.py     Procreate, Clip Studio, CorelDRAW (embedded previews)
    vector.py      SVG (resvg), PDF/AI (pdfium), EPS, InDesign
    audio.py       sound: tags, BWF/iXML, levels, waveform (soundfile)
    model3d.py     3D: rendered previews, Blender thumbnails, embedded/Windows previews
    video.py       video: ffmpeg/ffprobe frames, hover strip, metadata; MP4 box reader
    placeholder.py formats without a readable preview (Windows thumbnail or a card)
    common.py      embedded XMP/zip previews, Windows shell thumbnails, cards
  mesh3d/          3D geometry without a 3D library
    formats.py     readers: glTF/GLB, OBJ, FBX, STL, PLY, DAE, 3MF, 3DS, OFF, USDA, VOX
    render.py      numpy z-buffer rasteriser for thumbnails (textures sampled per pixel)
    pack.py        geometry, materials and maps for the 3D viewer
    meshopt.py     EXT_meshopt_compression decoder (pure Python/numpy)
    (handlers/ktx2.py: KTX2 textures; handlers/hdr.py: OpenEXR and Radiance HDR)
    native.py      Draco and binary USD decoders, run in a separate process (optional add-on)
  ai/              optional (install.bat ai)
    embed.py       SigLIP 2 / open_clip image+text embeddings, lazy load, GPU fp16, idle unload
    sound.py       LAION CLAP sound+text embeddings (transformers), 10 s windows at 48 kHz
    service.py     embeddings.db, search, similar, tag pools, training, suggestions, review
    vlm.py         Ollama and OpenAI-compatible clients with a fixed-answer JSON schema
  static/          the UI: plain ES modules, no build step, no npm dependencies
    js/playhead.js         optional Playhead analysis: documents, worker queue, theme, panel rows
    js/playhead-worker.js  the analysis in a module worker
    js/vendor/             playhead-core.mjs (Playhead 0.2.0, unchanged; checksum in its README)
tests/             pytest: dates, indexing, API + security, tags, moves, rules, sidecars, watching
tools/             sample library generator, indexing benchmark, browser UI checks
install / update / kurator  (.bat for Windows, .sh for Linux/macOS)
.github/           CI (tests on Windows/Linux/macOS + pip-audit, weekly) and Dependabot
```

**Handlers** are the extension point. Each handler declares extensions and returns
`(image for thumbnail, metadata dict, embedded dates)` and belongs to a group for the type filter.
Audio handlers return a waveform as the thumbnail. 3D handlers (`.blend` embedded preview,
Blender-rendered previews for `.glb/.fbx/.obj`) will plug in the same way.

## Security

- The server binds to **127.0.0.1 only**.
- Requests with a foreign `Host` header are rejected (DNS-rebinding protection). So are state-changing
  requests without the app's own `Origin`, and anything marked `Sec-Fetch-Site: cross-site`. A website
  you visit cannot use the API.
- **Strict Content-Security-Policy** (`script-src 'self'`, no eval, no inline scripts), `nosniff`,
  and `frame-ancestors 'none'`. All file metadata is rendered as text, never as HTML.
- Only files in the catalog can be served. Size limits protect against decompression bombs (Pillow,
  400 MP) and zip bombs (`.kra`).
- **Dependencies:** FastAPI, Starlette, Uvicorn, Pillow (13.0+), h11, watchdog, numpy, pypdfium2,
  resvg-py, pi-heif, soundfile, tinytag and zstandard (plus PyTorch and open_clip for the AI add-on), at their
  newest versions as of November 2026; `pip-audit` reports no known vulnerabilities.
- Blender (optional) converts .blend files with `--factory-startup --disable-autoexec`: no add-ons,
  scripts or drivers from the file run.
- The optional Draco (DracoPy) and USD (usd-core) decoders are native code, so they run in a
  short-lived separate process (`python -I`, time limit). A malformed file can only end that process,
  never Kurator; the file is then listed with a note.
- 3D files are parsed by Kurator's own readers with size limits; XML-based formats with a DTD are
  refused, and a model may only pull extra files (glTF buffers, OBJ materials) from its own folder.
- PDFs are rendered by pdfium without JavaScript; SVGs by resvg (no scripts, no external
  resources). The AI add-on talks only to the vision model server you choose (Ollama or an
  OpenAI-compatible server, normally on your own computer; its API key is never sent to the
  browser), and downloads model weights from Hugging Face on first use.
- **Playhead analysis** (optional, off by default) runs in the browser. The Playhead library is a
  vendored, unchanged file with a recorded checksum, loaded only when the feature is on. Kurator
  sends it decoded samples from its own server and stores the results (checked against the
  `playhead-analysis` schema, at most 8 MB each) in its data folder. Nothing leaves your computer.
  The *Tell us if this helps* link only opens a GitHub form in your browser.
- Basis Universal, EXR and HDR files are decoded by Kurator's own Python code with size limits
  (a fuzz test feeds it damaged files). The optional `basisu` tool runs as a separate process
  with a time limit, on a temporary copy. The frontend has
  **zero** third-party dependencies.
- **Staying current:** `update.bat` / `update.sh` upgrade every dependency to its newest compatible
  version and run `pip-audit`. On GitHub, CI runs the audit on every push and weekly, and Dependabot
  proposes updates. See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Development

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # tests + pip-audit
pytest
python tools/make_sample_library.py /tmp/samplelib --count 3000   # messy test library
python tools/bench_index.py /tmp/samplelib /tmp/kdata               # indexing benchmark
kurator --data-dir /tmp/kdata --library /tmp/samplelib
KURATOR_RCLONE=/path/to/rclone pytest tests/test_rclone.py         # cloud tests (skipped without rclone)
pip install -e ".[ui-tests]" && playwright install chromium         # optional browser tests
python tools/ui_check.py 8765 /tmp/ui
pip install -e ".[ai]" && pytest tests/test_ai.py                    # AI tests (offline, random weights)
```

## Roadmap

1. ~~Core: indexer, dates, thumbnail cache, virtual grid, selection model, panel, viewer~~ ✅
2. ~~Tags: hierarchy, hotkeys, filters, untagged view, re-link after outside moves~~ ✅
3. ~~Moves with preview, date preservation across disks, undo; browse + pin folders~~ ✅
4. ~~Move rules, XMP sidecars, keyword import, live folder watching, install/update scripts~~ ✅;
   ~~saved searches (saved views), a source/licence field, merging into other programs' sidecars~~ ✅
   Still open: optionally writing the date into the file itself (ExifTool), only when you choose
   to.
5. ~~Backup to disks/folders: incremental, verified, several targets, tag-folder layout, restore~~ ✅
6. ~~Cloud backup via rclone (Google Drive, several accounts, quota check); tag groups and projects;
   project export into game folders; backup reminders and automatic backups~~ ✅
7. ~~File type filter; vector, PDF, HEIC, PSD and art-app previews~~ ✅
8. ~~AI (optional): SigLIP 2 search and find similar, tag pools (zero-shot, trained on your tags,
   vision-language models), captions, review queue; near-duplicates; dataset export;
   OpenAI-compatible servers (llama.cpp, LM Studio, vLLM)~~ ✅
9. ~~Audio: waveforms, player, auto-play, tags, BWF/iXML, UCS, levels~~ ✅
   LUFS, BPM and key and the waveform view followed in 0.11.
10. ~~3D: previews for glTF/FBX/OBJ/STL/3MF/VOX and more, Blender thumbnails, 3D viewer, texture
    sets, Draco glTF, turntables on hover~~ ✅
    Next: Blender-rendered previews when Blender is installed.
11. ~~Video: frame thumbnails, hover scrubbing, recording dates, playback~~ ✅;
    ~~projects as the top filter, with automatic sources~~ ✅
12. ~~Texture sets, project export to Unity/Godot/Unreal, saved views, exact duplicates (also stored
    once in backups), Blender files in 3D, long sounds in orange~~ ✅
13. ~~Similar images, version stacks, compare mode, join images and contact sheets~~ ✅
14. ~~Waveform view with playhead and styles, LUFS/BPM/key, colour / shape / transparency filters,
    timeline, sprite sheets and texture atlases~~ ✅
15. ~~Textured 3D viewer with lights and map views, texture detection, 2D lit preview with companion
    maps, SVG sizes, engine projects left untouched~~ ✅
16. ~~Meshopt and Draco glTF, binary USD, turntables on hover, cutting regions out of sounds,
    Godot SpriteFrames/AtlasTexture export~~ ✅
17. ~~KTX2 textures, text-to-sound search (CLAP), Unity sprite import settings, print views (inks,
    ink limit, soft proofing, PDF boxes, spot colours, fonts)~~ ✅
18. ~~Basis Universal (ETC1S/UASTC) KTX2 textures, OpenEXR and Radiance HDR, multi-page PDF
    browsing, source/author/licence, training datasets, OpenAI-compatible vision servers, merging
    into other programs' sidecars, backup schedule~~ ✅
    Next: PIZ-compressed EXR.
19. ~~Playhead analysis of sounds (optional): a Playhead view with looks, a Playhead section in the
    side panel, analyses kept per file and Playhead version~~ ✅
    Next: library-wide analysis with the `playhead` command-line tool (sorting and filtering by
    descriptors), Playhead mini-strips as grid thumbnails once a look for small sizes wins, and a
    small study of whether they help people find sounds. (PureRef's board format
    isn't open, so boards are best made by dragging images into PureRef.)
20. **Desktop shell (Tauri):** native window, drag files out into Krita/Blender/Unity/Godot, installers.

## License

Copyright 2026 Daniel Sandner. Licensed under the [Apache License, Version 2.0](LICENSE); see also
[NOTICE](NOTICE).
