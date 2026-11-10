// Installing Kurator from the app, for people without Python or a Kurator folder.
//
// The app downloads its own Python (a python-build-standalone build, the kind uv uses, checked against the
// SHA-256 in python-runtime.json) and the Kurator code of its own version from GitHub, then runs Kurator's
// own installer (install.bat / install.sh) with that Python. Everything lives in the app's data folder:
//
//   <app data>/runtime/python   the Python, used for nothing else
//   <app data>/kurator          Kurator's code and its .venv (marked .kurator-app)
//   <app data>/install.json     what is installed: {"kurator": "0.19.1", "python": "3.14.10"}
//
// Kurator's catalog and settings live in Kurator's own data folder, so installing again or updating never
// touches them. A newer app brings its Kurator up to its own version on start; "Update now" in Kurator
// installs the newest release the same way.

use std::fs::{self, File};
use std::io::{BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};

/// In a Kurator folder the app installed: Kurator's update button then asks the app.
pub const MARKER: &str = ".kurator-app";
const RUNTIME: &str = include_str!("../python-runtime.json");
const SOURCE: &str = "https://github.com/eigensoundlab/kurator/archive/refs/tags/v{version}.tar.gz";

#[derive(Deserialize)]
struct Runtime {
    version: String,
    assets: std::collections::HashMap<String, Asset>,
}

#[derive(Deserialize)]
struct Asset {
    url: String,
    sha256: String,
}

#[derive(Serialize, Deserialize, Default)]
pub struct Installed {
    pub kurator: String,
    pub python: String,
}

/// What the start page shows: a step, a line under it, how far (0..1) when known.
pub type Say<'a> = &'a dyn Fn(&str, &str, Option<f32>);

pub fn base(app: &AppHandle) -> PathBuf {
    app.path().app_local_data_dir().unwrap_or_else(|_| std::env::temp_dir().join("kurator-app"))
}

/// The Kurator folder the app installs into.
pub fn home(app: &AppHandle) -> PathBuf {
    base(app).join("kurator")
}

pub fn installed(app: &AppHandle) -> Option<Installed> {
    serde_json::from_str(&fs::read_to_string(base(app).join("install.json")).ok()?).ok()
}

pub fn version_tuple(v: &str) -> Vec<u64> {
    v.trim().trim_start_matches('v').split(['.', '-', '+']).take(3).map(|p| p.parse().unwrap_or(0)).collect()
}

/// The app's own Kurator is older than the app (or half installed): install the app's version first.
pub fn outdated(app: &AppHandle, want: &str) -> bool {
    if !home(app).join(MARKER).is_file() {
        return false;
    }
    match installed(app) {
        Some(i) => version_tuple(&i.kurator) < version_tuple(want),
        None => true,
    }
}

/// This computer's build of python-build-standalone.
fn target() -> Option<String> {
    let arch = match std::env::consts::ARCH {
        "x86_64" => "x86_64",
        "aarch64" => "aarch64",
        _ => return None,
    };
    let os = match std::env::consts::OS {
        "windows" => "pc-windows-msvc",
        "macos" => "apple-darwin",
        "linux" => "unknown-linux-gnu",
        _ => return None,
    };
    Some(format!("{arch}-{os}"))
}

pub fn python_exe(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        dir.join("python.exe")
    } else {
        dir.join("bin").join("python3")
    }
}

fn agent() -> ureq::Agent {
    use ureq::tls::{RootCerts, TlsConfig};
    ureq::Agent::config_builder()
        .tls_config(TlsConfig::builder().root_certs(RootCerts::PlatformVerifier).build())   // with company certificates
        .timeout_connect(Some(Duration::from_secs(30)))
        .user_agent(format!("Kurator-desktop/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

fn mb(n: u64) -> String {
    format!("{:.0} MB", n as f64 / 1_048_576.0)
}

/// Download url to `to` (whole or not at all), checking its SHA-256 when given.
fn download(url: &str, to: &Path, what: &str, sha256: Option<&str>, say: Say) -> Result<(), String> {
    say(&format!("Downloading {what}…"), url, None);
    let mut resp = agent().get(url).call().map_err(|e| format!("Could not download {what}: {e}"))?;
    let total = resp.body().content_length();
    let mut body = resp.body_mut().as_reader();
    let part = to.with_extension("part");
    let mut f = File::create(&part).map_err(|e| format!("{}: {e}", part.display()))?;
    let mut hash = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    let (mut got, mut shown) = (0u64, Instant::now());
    loop {
        let n = body.read(&mut buf).map_err(|e| format!("Downloading {what} stopped: {e}"))?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
        f.write_all(&buf[..n]).map_err(|e| format!("{}: {e}", part.display()))?;
        got += n as u64;
        if shown.elapsed() > Duration::from_millis(250) {
            shown = Instant::now();
            let line = match total { Some(t) => format!("{} of {}", mb(got), mb(t)), None => mb(got) };
            say(&format!("Downloading {what}…"), &line, total.map(|t| got as f32 / t.max(1) as f32));
        }
    }
    drop(f);
    if let Some(want) = sha256 {
        let have = format!("{:x}", hash.finalize());
        if !have.eq_ignore_ascii_case(want) {
            let _ = fs::remove_file(&part);
            return Err(format!("The {what} download is not the expected file (checksum {have}, expected {want}). Nothing was installed."));
        }
    }
    fs::rename(&part, to).map_err(|e| format!("{}: {e}", to.display()))
}

/// Unpack a .tar.gz into `into`. strip: drop the top folder (GitHub's kurator-0.19.1/). Only plain files,
/// folders and - in the checked Python - its links; nothing may land outside `into`.
fn unpack(archive: &Path, into: &Path, strip: bool, links: bool) -> Result<(), String> {
    let f = File::open(archive).map_err(|e| format!("{}: {e}", archive.display()))?;
    let mut ar = tar::Archive::new(flate2::read::GzDecoder::new(BufReader::new(f)));
    ar.set_preserve_permissions(true);
    ar.set_overwrite(true);
    fs::create_dir_all(into).map_err(|e| format!("{}: {e}", into.display()))?;
    let bad = |e: std::io::Error| format!("{} is damaged: {e}", archive.display());
    for entry in ar.entries().map_err(bad)? {
        let mut e = entry.map_err(bad)?;
        let kind = e.header().entry_type();
        if kind.is_pax_global_extensions() || kind.is_pax_local_extensions() || kind.is_gnu_longname() || kind.is_gnu_longlink() {
            continue;
        }
        let path = e.path().map_err(bad)?.into_owned();
        let rel: PathBuf = path.components().skip(usize::from(strip)).collect();
        if rel.as_os_str().is_empty() {
            continue;
        }
        if !rel.components().all(|c| matches!(c, Component::Normal(_))) {
            return Err(format!("{}: unsafe path {}", archive.display(), path.display()));
        }
        if (kind.is_symlink() || kind.is_hard_link()) && !links {
            continue;
        }
        if kind.is_hard_link() {   // a second name for a file already unpacked: a copy of it
            let target: PathBuf = e.link_name().map_err(bad)?.ok_or("a link without a target")?.components().skip(usize::from(strip)).collect();
            if !target.components().all(|c| matches!(c, Component::Normal(_))) {
                return Err(format!("{}: unsafe link {}", archive.display(), target.display()));
            }
            let dest = into.join(&rel);
            fs::copy(into.join(&target), &dest).map_err(|err| format!("{}: {err}", dest.display()))?;
            continue;
        }
        if !(kind.is_file() || kind.is_dir() || kind.is_symlink() || kind.is_hard_link() || kind == tar::EntryType::Continuous) {
            continue;
        }
        let dest = into.join(&rel);
        if let Some(p) = dest.parent() {
            fs::create_dir_all(p).map_err(|e| format!("{}: {e}", p.display()))?;
        }
        e.unpack(&dest).map_err(|err| format!("{}: {err}", dest.display()))?;
    }
    Ok(())
}

fn remove(p: &Path) {
    if p.is_dir() {
        let _ = fs::remove_dir_all(p);
    } else if p.exists() {
        let _ = fs::remove_file(p);
    }
}

/// The Python for Kurator: the pinned build, downloaded once (true: a new one, so the .venv starts over).
fn ensure_python(app: &AppHandle, say: Say) -> Result<(PathBuf, String, bool), String> {
    let rt: Runtime = serde_json::from_str(RUNTIME).map_err(|e| format!("python-runtime.json: {e}"))?;
    let dir = base(app).join("runtime").join("python");
    let have = installed(app).map(|i| i.python).unwrap_or_default();
    if have == rt.version && python_exe(&dir).is_file() {
        return Ok((python_exe(&dir), rt.version, false));
    }
    let t = target().ok_or("This computer is not one Kurator's Python is made for (64-bit Windows, macOS or Linux)")?;
    let asset = rt.assets.get(&t).ok_or_else(|| format!("No Python for {t} in python-runtime.json"))?;
    let dl = base(app).join("downloads");
    fs::create_dir_all(&dl).map_err(|e| format!("{}: {e}", dl.display()))?;
    let archive = dl.join(format!("python-{}.tar.gz", rt.version));
    download(&asset.url, &archive, &format!("Python {}", rt.version), Some(&asset.sha256), say)?;
    say("Unpacking Python…", "", None);
    let part = base(app).join("runtime").join("python.part");
    remove(&part);
    unpack(&archive, &part, true, true)?;   // the archive's top folder is python/
    remove(&dir);
    fs::rename(&part, &dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    remove(&archive);
    let exe = python_exe(&dir);
    if !exe.is_file() {
        return Err(format!("The Python download has no {}", exe.display()));
    }
    Ok((exe, rt.version, true))
}

/// The Kurator code of `version` (or KURATOR_SOURCE: a .tar.gz path or address, for tests and
/// unreleased builds) in <app data>/kurator, keeping its .venv unless the Python is new.
fn place_code(app: &AppHandle, version: &str, keep_venv: bool, say: Say) -> Result<PathBuf, String> {
    let dl = base(app).join("downloads");
    fs::create_dir_all(&dl).map_err(|e| format!("{}: {e}", dl.display()))?;
    let source = std::env::var("KURATOR_SOURCE").ok().filter(|s| !s.trim().is_empty());
    let archive = match &source {
        Some(s) if Path::new(s).is_file() => PathBuf::from(s),
        Some(url) => {
            let a = dl.join("kurator-source.tar.gz");
            download(url, &a, "Kurator", None, say)?;
            a
        }
        None => {
            let a = dl.join(format!("kurator-{version}.tar.gz"));
            download(&SOURCE.replace("{version}", version), &a, &format!("Kurator {version}"), None, say)?;
            a
        }
    };
    say("Unpacking Kurator…", "", None);
    let home = home(app);
    let new = base(app).join("kurator.new");
    let old = base(app).join("kurator.old");
    remove(&new);
    remove(&old);
    unpack(&archive, &new, true, false)?;
    if !new.join("kurator").join("__main__.py").is_file() {
        return Err("The download has no Kurator in it".into());
    }
    if source.as_deref().is_none_or(|s| !Path::new(s).is_file()) {
        remove(&archive);
    }
    if keep_venv && home.join(".venv").is_dir() {
        fs::rename(home.join(".venv"), new.join(".venv")).map_err(|e| format!("Could not keep Kurator's packages: {e}"))?;
    }
    if home.exists() {
        fs::rename(&home, &old).map_err(|e| format!("{} is in use - close programs using it and try again ({e})", home.display()))?;
    }
    fs::rename(&new, &home).map_err(|e| format!("{}: {e}", home.display()))?;
    remove(&old);
    fs::write(home.join(MARKER), "Installed by the Kurator desktop app; it updates this folder.\n").map_err(|e| e.to_string())?;
    Ok(home)
}

/// Kurator's own installer, with the app's Python; its output goes to install.log.
fn run_installer(home: &Path, python: &Path, log: &Path, say: Say,
                 child_slot: &std::sync::Mutex<Option<std::process::Child>>) -> Result<(), String> {
    let out = File::create(log).map_err(|e| format!("{}: {e}", log.display()))?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("cmd");
        c.args(["/C", "install.bat", "nopause"]);
        c
    } else {
        let mut c = Command::new("/bin/bash");
        c.arg("install.sh");
        c
    };
    cmd.current_dir(home)
        .env("KURATOR_PYTHON", python)
        .env("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let child = cmd.spawn().map_err(|e| format!("Kurator's installer did not start: {e}"))?;
    *child_slot.lock().unwrap() = Some(child);
    let t0 = Instant::now();
    loop {
        let done = child_slot.lock().unwrap().as_mut().map(|c| c.try_wait());
        match done {
            None => return Err("The installation was stopped".into()),
            Some(Ok(Some(st))) => {
                child_slot.lock().unwrap().take();
                return if st.success() { Ok(()) } else { Err(format!("Kurator's installer stopped ({st})")) };
            }
            Some(Err(e)) => return Err(e.to_string()),
            Some(Ok(None)) => {}
        }
        let last = crate::tail(log, 1);
        say("Setting up Kurator…",
            &format!("Installing the packages Kurator needs - a few minutes the first time ({} s).\n{}", t0.elapsed().as_secs(), last.trim()), None);
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// Install (or update to) Kurator `version`; returns its folder.
pub fn install(app: &AppHandle, version: &str, say: Say, child_slot: &std::sync::Mutex<Option<std::process::Child>>) -> Result<PathBuf, String> {
    fs::create_dir_all(base(app)).map_err(|e| format!("{}: {e}", base(app).display()))?;
    let (python, py_version, new_python) = ensure_python(app, say)?;
    let home = place_code(app, version, !new_python, say)?;
    let log = crate::log_dir(app).join("install.log");
    run_installer(&home, &python, &log, say, child_slot)
        .map_err(|e| format!("{e}\n\n{}", crate::tail(&log, 25)))?;
    let done = Installed { kurator: version.to_string(), python: py_version };
    fs::write(base(app).join("install.json"), serde_json::to_string_pretty(&done).unwrap_or_default()).map_err(|e| e.to_string())?;
    remove(&base(app).join("downloads"));
    Ok(home)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert!(version_tuple("0.19.1") < version_tuple("0.20.0"));
        assert!(version_tuple("v0.19.10") > version_tuple("0.19.9"));
        assert_eq!(version_tuple("0.19.1"), version_tuple("0.19.1"));
    }

    #[test]
    fn pinned_python_for_every_build() {
        let rt: Runtime = serde_json::from_str(RUNTIME).unwrap();
        for t in ["x86_64-pc-windows-msvc", "x86_64-apple-darwin", "aarch64-apple-darwin", "x86_64-unknown-linux-gnu"] {
            let a = &rt.assets[t];
            assert!(a.url.starts_with("https://github.com/astral-sh/python-build-standalone/releases/download/"), "{t}");
            assert!(a.url.contains(&rt.version.replace('+', "%2B")) && a.sha256.len() == 64, "{t}");
        }
        assert!(target().is_some());
    }

    #[test]
    fn unpacking_stays_inside() {
        let dir = std::env::temp_dir().join(format!("kurator-unpack-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let tgz = dir.join("a.tar.gz");
        {
            let gz = flate2::write::GzEncoder::new(File::create(&tgz).unwrap(), flate2::Compression::fast());
            let mut b = tar::Builder::new(gz);
            for (name, data) in [("top/kurator/__main__.py", "print(1)"), ("top/install.sh", "echo")] {
                let mut h = tar::Header::new_gnu();
                h.set_size(data.len() as u64);
                h.set_mode(0o644);
                h.set_cksum();
                b.append_data(&mut h, name, data.as_bytes()).unwrap();
            }
            let mut h = tar::Header::new_gnu();
            h.set_entry_type(tar::EntryType::Symlink);
            h.set_size(0);
            h.set_cksum();
            b.append_link(&mut h, "top/escape", "../../outside").unwrap();
            b.into_inner().unwrap().finish().unwrap();
        }
        let out = dir.join("out");
        unpack(&tgz, &out, true, false).unwrap();
        assert!(out.join("kurator").join("__main__.py").is_file() && out.join("install.sh").is_file());
        assert!(!out.join("escape").exists());   // no links from the code download
        let _ = fs::remove_dir_all(&dir);
    }
}
