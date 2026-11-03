# Kurator

A fast, local asset manager for artists and game developers. Browse thousands of sketches, renders,
icons and textures, then tag, sort and move them comfortably. Every file keeps its **original date
and metadata**.

> Status: **v0.7 (alpha).** Browsing, tagging with groups (subjects, projects, status, source), safe
> moving, move rules, XMP sidecars, live folder updates, backups to disks and to the cloud
> (Google Drive and others via rclone), vector/document/art-app previews, sound files, **3D models**
> (previews and a 3D viewer), and optional local AI (search by description, find similar, tag
> suggestions from a pool you choose) work. See the [Roadmap](#roadmap).

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

- **Install** creates `.venv`, installs the **newest compatible versions** of all dependencies, then
  runs [`pip-audit`](https://pypi.org/project/pip-audit/) to check them for known vulnerabilities.
- **Update** does `git pull` (if you cloned the repository) and then the same upgrade + audit. Once
  the AI features are installed, updates keep them.
- **`ai`** adds PyTorch and open_clip (2–3 GB). On Windows with an NVIDIA card, the script installs
  the CUDA build of PyTorch (the default one from PyPI is CPU-only there) and checks that the GPU
  works. On Linux the default PyTorch already includes CUDA; on Apple Silicon it uses Metal.
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
| Documents | **pdf** (first page, pdfium), **indd** (XMP thumbnail), afpub, sla | first page / embedded |
| Game textures | tga, dds, blp | full |
| 3D models | **glb gltf obj fbx stl ply dae 3mf 3ds off usda usdz vox**, **blend** (also .blend1), usd usdc ma mb max c4d ztl zpr abc 3dm skp lwo hip spp sbs sbsar | rendered by Kurator / Blender's saved thumbnail / embedded (3MF, SketchUp) / Windows |
| Audio | wav, mp3, ogg, oga, opus, flac, m4a, aac, aif/aiff/aifc, caf, w64, rf64, wma | waveform |

- **Licences:** everything is permissively licensed (pdfium BSD/Apache, resvg MIT, libheif via
  pi-heif LGPL as a separate library, libsndfile LGPL as a separate library). There is no GPL code.
  CorelDRAW, Affinity and InDesign files are never parsed beyond their embedded previews.
- **Windows thumbnails:** for formats Kurator cannot read itself, it asks Windows for the thumbnail
  that the installed application provides (e.g. Affinity, CorelDRAW), if any.
- SVG files are shown sandboxed (no scripts), even in the viewer.

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

Formats the browser cannot play (AIFF, CAF, WMA…) are still indexed and shown; *Open* plays them in
your default application. Reading M4A/AAC/WMA needs `ffmpeg` on the PATH; without it they're listed
without a waveform.

### AI tagging and search (optional, all local)

Install with `install.bat ai` / `./install.sh ai`, then use the **AI** button. Nothing is uploaded:
the models run on your computer. The first use of a model downloads it once from Hugging Face.

1. **Prepare images.** Kurator computes a compact "fingerprint" (an embedding from **SigLIP 2**)
   for each image, from the thumbnail cache. It is done once per image; a GPU makes it many times
   faster than a CPU. Choose *fast* (Base, 0.4 GB), *better* (Large, 1.8 GB) or *best* (So400m, 4.5 GB;
   fine on a 16 GB card).
2. **Search by description:** type `?` and a description in the search box, e.g.
   `?pencil sketch of a dragon with wings`. Results are ranked best first.
3. **Find similar:** select an image and press **S** (or *≈ similar* in the panel).
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
     - *Gemma via Ollama*: asks a local vision model such as `gemma3:12b` (`ollama pull gemma3:12b`)
       with a fixed answer list, so it can only answer with your tags or "none of these". It can
       also write a **caption** per image (for training datasets).
   - **Run on** the selection or the current view. In *one tag per image* mode, images that already
     have a tag from the pool are skipped.
5. **Review.** Suggestions are not tags until you accept them. They show as hollow dots in the grid
   and as dashed chips in the panel. *Review them* shows only images with suggestions; select and
   press **A** to accept or **Shift+A** to reject. Rejections are remembered, so the same wrong
   suggestion doesn't come back, and they teach *Learn from my tags* what a tag is not. You can also
   accept all suggestions above a score. Accepted tags are marked ✦ and can be undone with Ctrl+Z.

Embeddings are stored in `embeddings.db` (one per image content and model; duplicates share one),
next to the catalog.

### 3D models

3D files are catalogued like images: tag them, move them, back them up, filter with the **3D models**
group of the type filter.

- **Previews** are rendered by Kurator itself (no Blender or other 3D software needed): a 3/4 view with
  flat shading and outlines, using the materials' colours (glTF base colour and embedded textures,
  OBJ `.mtl` colours, vertex colours, MagicaVoxel palettes). A million triangles take a few seconds,
  once.
- **Blender** files show the thumbnail Blender saved with the file (Blender 2.5 to 5.x, also
  compressed files and `.blend1` backups), plus the number of objects, meshes and materials.
- **3MF** files from slicers and CAD keep their own thumbnail; **SketchUp** files their embedded
  preview. Maya, 3ds Max, Cinema 4D, ZBrush, Alembic, Rhino and Substance files show the thumbnail
  Windows has for them (when the application is installed), or a card.
- **3D viewer:** Enter or double-click opens a live view: drag to turn, right-drag (or Shift+drag) to
  move, wheel to zoom, 0 or double-click to reset. ← → go to the next file as usual.
- **Side panel:** triangles, vertices, dimensions and units, meshes, materials, animations, the
  application that wrote the file, and the **texture files** the model uses. FBX, COLLADA and 3MF
  creation dates count as the file's date.
- Draco- or meshopt-compressed glTF files and binary USD (`.usdc`) can't be read yet; they are
  listed with a card or the Windows thumbnail.

### Folders, tags and projects: how they combine

The sidebar has **places** (library folders, pinned and browsed folders) and **tags** (subjects,
projects, status, source). The path bar above the grid always shows what you're looking at.

| Action | Result |
|---|---|
| Click a folder | That folder (and its subfolders if *Subfolders* is on). Tag filters stay. |
| Click a tag or project | **Everything with it, in the whole library**, like opening an album |
| Ctrl+click a tag | Narrow the current view: *Game A* within this folder, *Icons* within *Game A* |
| Alt+click a tag | Hide items with it: everything **except** what's already in *Game A* |
| *No project*, *Untagged*… | What's left to sort in the whole library (Ctrl+click: in this folder) |
| × on a chip in the path bar | Remove just that filter; *Clear filters* removes all |

**Adding to and removing from a project:**

- **Drag** selected images onto a project (or any tag) in the sidebar or the tag bar.
- Or use the project's **hotkey (1–9)**, or click it in the **tag bar**: it adds, or removes it when
  all selected images already have it.
- In a project's view, select images and click **− Remove N from Game A** in the path bar. They stay
  visible (dimmed) until you press **R**, and Ctrl+Z puts them back.
- A typical round: open a folder of new work → *No project* (Ctrl+click) → select → drag onto the
  project → R.

### Tags

- **Tag groups** keep different kinds of information apart:

  | Group | Example | Per image |
  |---|---|---|
  | **Subjects** (what it shows) | Creatures, Equipment/Weapons, Icons | any number |
  | **Projects** (where it's used) | Game A, Client B, Artbook | any number |
  | **Status** | concept, WIP, final | one (setting *final* replaces *WIP*) |
  | **Source** | own, reference, AI-generated | one |

  - Filters combine across groups: click *Game A*, then Ctrl+click *Icons* to get the icons used in
    Game A (see [Folders, tags and projects](#folders-tags-and-projects-how-they-combine)).
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
- **Images are never modified**, and sidecars created by other programs are never overwritten.
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
    placeholder.py formats without a readable preview (Windows thumbnail or a card)
    common.py      embedded XMP/zip previews, Windows shell thumbnails, cards
  mesh3d/          3D geometry without a 3D library
    formats.py     readers: glTF/GLB, OBJ, FBX, STL, PLY, DAE, 3MF, 3DS, OFF, USDA, VOX
    render.py      numpy z-buffer rasteriser for thumbnails
  ai/              optional (install.bat ai)
    embed.py       SigLIP 2 / open_clip image+text embeddings, lazy load, GPU fp16, idle unload
    service.py     embeddings.db, search, similar, tag pools, training, suggestions, review
    vlm.py         Ollama client with a fixed-answer JSON schema
  static/          the UI: plain ES modules, no build step, no npm dependencies
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
- 3D files are parsed by Kurator's own readers with size limits; XML-based formats with a DTD are
  refused, and a model may only pull extra files (glTF buffers, OBJ materials) from its own folder.
- PDFs are rendered by pdfium without JavaScript; SVGs by resvg (no scripts, no external
  resources). The AI add-on talks only to Ollama on your own computer, and downloads model weights
  from Hugging Face on first use. The frontend has
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
4. ~~Move rules, XMP sidecars, keyword import, live folder watching, install/update scripts~~ ✅
   Still open in this area:
   - merging into sidecars written by other programs
   - optionally writing the date into the file itself (ExifTool), only when you choose to
   - saved searches; a source/licence field
5. ~~Backup to disks/folders: incremental, verified, several targets, tag-folder layout, restore~~ ✅
6. ~~Cloud backup via rclone (Google Drive, several accounts, quota check); tag groups and projects~~ ✅
   Next in this area: a backup reminder or schedule; export of a project's assets into a game
   project folder.
7. ~~File type filter; vector, PDF, HEIC, PSD and art-app previews~~ ✅
8. ~~AI (optional): SigLIP 2 search and find similar, tag pools (zero-shot, trained on your tags,
   Gemma via Ollama), captions, review queue~~ ✅
   Next: dataset export (images + caption `.txt`, dedupe, train/val split); near-duplicate finder;
   OpenAI-compatible endpoints (llama.cpp, LM Studio).
9. ~~Audio: waveforms, player, auto-play, tags, BWF/iXML, UCS, levels~~ ✅
   Next: LUFS, BPM and key; CLAP text-to-sound search; region export.
10. ~~3D: previews for glTF/FBX/OBJ/STL/3MF/VOX and more, Blender thumbnails, 3D viewer~~ ✅
    Next: texture sets (albedo/normal/roughness grouped with their model), Draco glTF, turntable
    on hover, Blender-rendered previews when Blender is installed.
11. **Video (optional, needs ffmpeg):** frame thumbnails, hover scrubbing, duration/codec/resolution,
    playback of browser-friendly formats (MP4/WebM).
12. **Desktop shell (Tauri):** native window, drag files out into Krita/Blender/Unity/Godot, installers.

Other ideas: version stacks (sketch → lineart → colour), near-duplicate finder, compare mode (2–4 up),
timeline scrubber, colour/aspect/transparency filters, contact sheets / PureRef export.

## License

Copyright 2026 Daniel Sandner. Licensed under the [Apache License, Version 2.0](LICENSE); see also
[NOTICE](NOTICE).
