# Kurator desktop app

The desktop app is a window for Kurator. Kurator itself is installed as before, with `install.bat` or
`install.sh` (see [Install and run](../README.md#install-and-run)). The app finds that folder, starts Kurator
in the background without a console window, shows it, and stops it when you close the window. If a
Kurator is already running (`kurator.bat`), the app uses that one and leaves it running.

The browser still works the same: the app is optional.

## What the app adds

- **Dropping files and folders** from Explorer or Finder: the app knows where they are. Files in the
  library are found by their path. New files are copied from where they are, so nothing is
  uploaded. **Folders can be added to the library** by dropping them.
- **Dragging files out:** drag selected thumbnails out of the window, into Photoshop, Krita,
  Blender, a folder in Explorer or Finder, a chat... This works on Windows and macOS. On Linux the
  files don't arrive yet, so there the drag stays inside Kurator.
- **Right-click** shows the system's own menu: Open, Open with, Show in Explorer / Finder, Copy path,
  Copy to…, Move to…, Rename…, Resample…, Delete… (on folders: copy or move the selected files
  there, pin, add to or remove from the library; on tags and projects: add to or remove from the
  selection, edit). The browser has the same menu, drawn by the page.
- **Links** open in your default browser; files the page saves (markers for REAPER / Audacity) go to
  Downloads.
- **One window:** starting the app again brings the open window to the front. Its size and place
  are remembered.

## Install

| | Download |
|---|---|
| Windows | `Kurator_<version>_x64-setup.exe` (installs for you only, no administrator needed) or the `.msi` |
| macOS (Apple silicon and Intel) | `Kurator_<version>_universal.dmg`, then drag Kurator to Applications |
| Linux | `Kurator_<version>_amd64.AppImage` (`chmod +x` it) or the `.deb` |

The installers are attached to each [release](https://github.com/eigensoundlab/kurator/releases).
Builds between releases are on the **Actions** tab: *Desktop app* → a run → *Artifacts* (you have to
be signed in to GitHub to download them).

**First start:** if the app doesn't find Kurator, it asks for the folder - the one with
`install.bat`, `install.sh` and `kurator.bat`. It remembers the folder. It looks by itself in
`Documents\GitHub\kurator`, `~/kurator`, `~/src/kurator` and similar places, and next to the app.

**Unsigned builds** (until the app is signed, see below):

- **Windows:** SmartScreen says *Windows protected your PC* → **More info** → **Run anyway**.
- **macOS:** *Apple could not verify "Kurator"…* → **Done**, then **System Settings → Privacy &
  Security** → scroll down → **Open Anyway** next to Kurator, and confirm. (Since macOS 15,
  Control-click → Open no longer skips the check.) Once is enough.
- **Linux:** packages are usually unsigned; nothing to do.

## Options

Set these as environment variables before starting the app:

| Variable | Meaning |
|---|---|
| `KURATOR_HOME` | the Kurator folder (instead of looking for it) |
| `KURATOR_ARGS` | options for Kurator, e.g. `--no-ai` or `--data-dir D:\KuratorData` |

The app's logs (`desktop.log`, and `kurator-server.log` with Kurator's own output) are in
`%LOCALAPPDATA%\com.eigensound.kurator\logs`, `~/Library/Logs/com.eigensound.kurator` or
`~/.local/share/com.eigensound.kurator/logs`. When Kurator doesn't start, the window shows the end
of its log and a button to open the folder.

## Building it yourself

You need [Rust](https://rustup.rs) and [Node.js](https://nodejs.org) (only for the Tauri command
line; the app has no JavaScript dependencies). On Linux also the WebKitGTK and GTK development
packages (Debian / Ubuntu: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev
librsvg2-dev libxdo-dev patchelf`).

```bash
cd desktop
npm ci
npm run tauri dev          # a window with the app, rebuilt when the Rust code changes
npm run tauri build        # installers in src-tauri/target/release/bundle/
```

A build inside the Kurator folder finds Kurator by itself. `kurator-desktop --selftest` (what CI runs)
starts Kurator, loads its page, checks that the page can use the app, then quits with 0 or 1.

## How it works

- `desktop/src-tauri/src/main.rs` is the whole app: find the Kurator folder, start
  `.venv` Python with `-m kurator --no-browser --port 8765 --quit-on-stdin-close`, wait until it
  answers, show it. Kurator quits when the app closes its standard input - also when the app
  crashes or is killed, so no Kurator is left behind. When port 8765 is taken by something else,
  the next free one is used.
- `desktop/ui/` is the start page shown while Kurator starts (or when it can't be found).
- The window shows only the start page and Kurator; any other page opens in your browser.
- Kurator's page is allowed exactly these: file drop events, dragging files out, the system's menu,
  writing to the clipboard and the system's Open dialog. They are granted at run time for
  `http://127.0.0.1:<port>` only. In the page, `kurator/static/js/shell.js` uses them when it runs
  in the app and falls back to the browser's way otherwise.

## Signing

### macOS (an Apple Developer account; no Mac needed)

The CI build on GitHub's Mac signs and notarizes the app when these secrets are set. Without them it
is signed "ad hoc": it runs, but macOS asks once (see above).

1. **A "Developer ID Application" certificate.** Only the account holder can create it. The
   certificates for iPhone apps (Apple Development, Apple Distribution) don't sign Mac apps for
   download. On Windows, OpenSSL comes with Git for Windows (`C:\Program Files\Git\usr\bin\openssl.exe`):

   ```bash
   openssl genrsa -out kurator-devid.key 2048
   openssl req -new -key kurator-devid.key -out kurator-devid.csr -subj "/emailAddress=you@example.com/CN=Your Name/C=CZ"
   ```

   On [developer.apple.com](https://developer.apple.com/account/resources/certificates/list) →
   Certificates → **+** → **Developer ID Application** → the **G2 Sub-CA** → upload
   `kurator-devid.csr` → download `developerID_application.cer`. Then:

   ```bash
   openssl x509 -inform DER -in developerID_application.cer -out kurator-devid.pem
   openssl x509 -in kurator-devid.pem -noout -subject      # CN = Developer ID Application: Your Name (TEAMID)
   openssl pkcs12 -export -legacy -inkey kurator-devid.key -in kurator-devid.pem -out kurator-devid.p12
   ```

   Choose an export password. (`-legacy`: the Mac keychain can't open OpenSSL 3's newer format; an
   older OpenSSL that doesn't know the option doesn't need it.) As one line of text, in PowerShell:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("kurator-devid.p12")) | Set-Content -NoNewline kurator-devid.p12.txt
   ```

2. **A key for notarization:** [App Store Connect](https://appstoreconnect.apple.com/access/integrations/api)
   → Users and Access → Integrations → App Store Connect API → Team Keys → **+** (access:
   Developer). Download `AuthKey_<KEY ID>.p8` - it can be downloaded only once - and note the
   **Key ID** and the **Issuer ID** above the list.

3. **GitHub:** the repository → Settings → Secrets and variables → Actions → *New repository secret*:

   | Secret | Value |
   |---|---|
   | `APPLE_CERTIFICATE` | the text in `kurator-devid.p12.txt` |
   | `APPLE_CERTIFICATE_PASSWORD` | the export password |
   | `APPLE_SIGNING_IDENTITY` | `Developer ID Application: Your Name (TEAMID)` (the CN above) |
   | `APPLE_API_ISSUER` | the Issuer ID |
   | `APPLE_API_KEY` | the Key ID |
   | `APPLE_API_KEY_P8` | the whole text of `AuthKey_<KEY ID>.p8` |

   (Instead of the API key, `APPLE_ID`, `APPLE_PASSWORD` - an app-specific password - and
   `APPLE_TEAM_ID` work too.) Keep `kurator-devid.key` and the `.p12` somewhere safe and delete the
   `.txt`.

The certificate is valid for 5 years and signs every release in that time. Apps signed and
notarized while it was valid keep opening after it expires; for new releases after that, make a
new certificate and replace the three certificate secrets.

### Windows

Not set up yet. Free for open-source projects: [SignPath Foundation](https://signpath.org). Azure
Artifact Signing (formerly Trusted Signing) is paid, and for individuals only in the USA and Canada.
Until then SmartScreen asks before the first start (see above).

## Releasing

1. Set the version in `pyproject.toml`, `kurator/__init__.py`, `desktop/package.json`,
   `desktop/src-tauri/Cargo.toml` and `desktop/src-tauri/tauri.conf.json` (the tests check that the
   desktop files agree).
2. Publish the GitHub release with the tag `v<version>`. The *Desktop app* workflow builds the three
   systems, checks the tag against the app's version and attaches the installers to the release
   (about 20 minutes).
