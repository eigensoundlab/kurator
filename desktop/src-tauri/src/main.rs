// Kurator desktop: a window around the local Kurator server.
//
// Kurator itself is the Python program in your Kurator folder (set up once with install.bat or
// install.sh). This app finds that folder, starts Kurator in the background - or uses one that is
// already running - shows it in its own window and stops it again when the window closes.
// In the window, dropped files come with their paths, files can be dragged out to other programs,
// links open in the default browser and right-click shows the system's menu.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::ipc::CapabilityBuilder;
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

/// Kurator's usual port: a Kurator already running there (started with kurator.bat) is used as it is.
const PORT: u16 = 8765;
/// The first start after an update can take a while (the catalog is brought up to date).
const START_TIMEOUT: Duration = Duration::from_secs(300);
const HELP: &str = "https://github.com/eigensoundlab/kurator#install-and-run";
const START_FLAG: &str = "--quit-on-stdin-close";

#[derive(Clone, Serialize, Default)]
struct Status {
    /// starting | need-home | error | ready
    phase: String,
    message: String,
    detail: String,
    /// Kurator's page once it runs (the start page goes there too: after Back, say)
    url: String,
}

#[derive(Default)]
struct Shell {
    status: Mutex<Status>,
    /// the Kurator this app started (None: none, or one that was already running)
    child: Mutex<Option<Child>>,
    /// that Kurator quits by itself when its input closes (older versions are stopped instead)
    graceful: AtomicBool,
    starting: AtomicBool,
    granted: Mutex<Vec<u16>>,
    selftest: bool,
}

// ------------------------------------------------------------------ logs

fn log_dir(app: &AppHandle) -> PathBuf {
    let d = app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("kurator-desktop"));
    let _ = fs::create_dir_all(&d);
    d
}

fn log(app: &AppHandle, line: &str) {
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(log_dir(app).join("desktop.log")) {
        let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let _ = writeln!(f, "[{t}] {line}");
    }
}

fn set_status(app: &AppHandle, phase: &str, message: impl Into<String>, detail: impl Into<String>) {
    let shell = app.state::<Shell>();
    let url = shell.status.lock().unwrap().url.clone();
    let s = Status { phase: phase.into(), message: message.into(), detail: detail.into(), url: if phase == "ready" { url } else { String::new() } };
    log(app, &format!("{}: {} {}", s.phase, s.message, s.detail.lines().next().unwrap_or("")));
    if shell.selftest && (phase == "error" || phase == "need-home") {
        selftest_done(app, false, &format!("{}\n{}", s.message, s.detail));
    }
    *shell.status.lock().unwrap() = s;
}

fn tail(path: &Path, lines: usize) -> String {
    let text = fs::read(path).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
    let all: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

// ----------------------------------------------------- the Kurator folder

fn config_file(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("kurator-folder.txt"))
}

fn is_kurator(dir: &Path) -> bool {
    dir.join("kurator").join("__main__.py").is_file()
}

fn venv_python(dir: &Path) -> Option<PathBuf> {
    let p = if cfg!(windows) {
        dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        dir.join(".venv").join("bin").join("python")
    };
    p.is_file().then_some(p)
}

/// Where to look: KURATOR_HOME, the folder chosen before, around this app (a build inside the
/// Kurator folder), then where people usually clone it.
fn candidates(app: &AppHandle) -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(h) = std::env::var("KURATOR_HOME") {
        v.push(PathBuf::from(h));
    }
    if let Some(saved) = config_file(app).and_then(|f| fs::read_to_string(f).ok()) {
        if !saved.trim().is_empty() {
            v.push(PathBuf::from(saved.trim()));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        v.extend(exe.ancestors().skip(1).take(8).map(Path::to_path_buf));
    }
    if let Ok(docs) = app.path().document_dir() {   // the real one: localized, or on OneDrive
        v.push(docs.join("GitHub").join("kurator"));
        v.push(docs.join("kurator"));
    }
    if let Ok(home) = app.path().home_dir() {
        for rel in [
            "Documents/GitHub/kurator", "OneDrive/Documents/GitHub/kurator", "GitHub/kurator", "Documents/kurator",
            "kurator", "Kurator", "src/kurator", "Projects/kurator", "code/kurator", "dev/kurator", "Desktop/kurator",
        ] {
            v.push(rel.split('/').fold(home.clone(), |p, part| p.join(part)));
        }
    }
    v
}

/// The Kurator folder: one that is installed (has its .venv) first, else one that still needs install.bat.
fn find_home(app: &AppHandle) -> Option<PathBuf> {
    let all: Vec<PathBuf> = candidates(app).into_iter().filter(|d| is_kurator(d)).collect();
    all.iter().find(|d| venv_python(d).is_some()).or(all.first()).cloned()
}

// ------------------------------------------------------------------ http

fn http_get(port: u16, path: &str) -> Option<(u16, String)> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(400)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    s.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    write!(s, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n").ok()?;
    let mut buf = Vec::new();
    s.take(4 << 20).read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    let code = text.split_whitespace().nth(1)?.parse().ok()?;
    let body = text.split_once("\r\n\r\n").map(|x| x.1.to_string()).unwrap_or_default();
    Some((code, body))
}

/// The version of the Kurator answering on this port (None: nothing there, or something else).
fn kurator_at(port: u16) -> Option<String> {
    let (code, body) = http_get(port, "/api/state")?;
    if code != 200 {
        return None;
    }
    let v: serde_json::Value = serde_json::from_str(&body).ok()?;
    v.get("extensions")?;
    v.get("version")?.as_str().map(String::from)
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

// --------------------------------------------------------------- starting

fn spawn(app: &AppHandle, home: &Path, python: &Path, port: u16, graceful: bool) -> std::io::Result<(Child, PathBuf)> {
    let dir = log_dir(app);
    let log_path = dir.join("kurator-server.log");
    if log_path.exists() {
        let _ = fs::rename(&log_path, dir.join("kurator-server.previous.log"));
    }
    let out = fs::File::create(&log_path)?;
    let err = out.try_clone()?;
    let mut cmd = Command::new(python);
    cmd.args(["-m", "kurator", "--no-browser", "--port", &port.to_string()]);
    if graceful {
        cmd.arg(START_FLAG);
    }
    if let Ok(extra) = std::env::var("KURATOR_ARGS") {
        cmd.args(extra.split_whitespace());
    }
    cmd.current_dir(home)
        .stdin(Stdio::piped())
        .stdout(out)
        .stderr(err)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(target_os = "macos")]
    {
        // started from the Dock an app gets a short PATH: add Homebrew's, where ffmpeg and rclone live
        let path = std::env::var("PATH").unwrap_or_default();
        cmd.env("PATH", format!("/opt/homebrew/bin:/usr/local/bin:{path}"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window
    }
    log(app, &format!("starting {} -m kurator --port {port} in {}", python.display(), home.display()));
    Ok((cmd.spawn()?, log_path))
}

fn start(app: AppHandle) {
    let shell = app.state::<Shell>();
    if shell.starting.swap(true, Ordering::SeqCst) {
        return;
    }
    run_start(&app);
    shell.starting.store(false, Ordering::SeqCst);
}

fn run_start(app: &AppHandle) {
    set_status(app, "starting", "Looking for Kurator…", "");
    if let Some(v) = kurator_at(PORT) {
        return show(app, PORT, format!("Kurator {v} is already running"));
    }
    let Some(home) = find_home(app) else {
        return set_status(app, "need-home", "Where is Kurator?",
            "Choose the folder Kurator is in: the one with install.bat, install.sh and kurator.bat.\n\
             No Kurator yet? Download it, run install.bat (Windows) or install.sh (macOS, Linux) once, then come back.");
    };
    let Some(python) = venv_python(&home) else {
        return set_status(app, "need-home", "Kurator is not set up in this folder yet",
            format!("{}\n\nRun install.bat (Windows) or install.sh (macOS, Linux) there once, then Try again.", home.display()));
    };
    let port = if port_free(PORT) { Some(PORT) } else { (PORT + 1..PORT + 50).find(|p| port_free(*p)) };
    let Some(port) = port else {
        return set_status(app, "error", "No free port for Kurator", format!("Ports {PORT}-{} are all in use.", PORT + 49));
    };
    let shell = app.state::<Shell>();
    let mut graceful = true;
    loop {
        set_status(app, "starting", "Starting Kurator…", home.display().to_string());
        let (child, log_path) = match spawn(app, &home, &python, port, graceful) {
            Ok(x) => x,
            Err(e) => return set_status(app, "error", "Kurator did not start", format!("{}: {e}", python.display())),
        };
        *shell.child.lock().unwrap() = Some(child);
        shell.graceful.store(graceful, Ordering::SeqCst);
        let t0 = Instant::now();
        let mut shown = 0;
        loop {
            let exited = shell.child.lock().unwrap().as_mut().map(|c| c.try_wait().ok().flatten().is_some()).unwrap_or(true);
            if exited {
                shell.child.lock().unwrap().take();
                let out = tail(&log_path, 30);
                if graceful && out.contains(START_FLAG) {
                    // a Kurator older than this app: start it the way it knows (it is stopped, not asked, later)
                    graceful = false;
                    break;
                }
                return set_status(app, "error", "Kurator stopped while starting", if out.is_empty() { "(nothing in the log)".into() } else { out });
            }
            if kurator_at(port).is_some() {
                return show(app, port, "Kurator is running".to_string());
            }
            if t0.elapsed() > START_TIMEOUT {
                stop_server(app);
                return set_status(app, "error", "Kurator took too long to start", tail(&log_path, 30));
            }
            let secs = t0.elapsed().as_secs();
            if secs >= 3 && secs != shown {
                shown = secs;
                set_status(app, "starting", format!("Starting Kurator… {secs} s"), home.display().to_string());
            }
            thread::sleep(Duration::from_millis(250));
        }
    }
}

/// Lets Kurator's page (and only it) use what the app adds: native drops, dragging files out, the
/// system's menus, the clipboard and the Open dialog.
fn grant(app: &AppHandle, port: u16) {
    let shell = app.state::<Shell>();
    let mut granted = shell.granted.lock().unwrap();
    if granted.contains(&port) {
        return;
    }
    let cap = CapabilityBuilder::new(format!("kurator-page-{port}"))
        .remote(format!("http://127.0.0.1:{port}/*"))
        .window("main")
        .permission("core:event:allow-listen")
        .permission("core:event:allow-unlisten")
        .permission("core:menu:default")
        .permission("core:resources:default")
        .permission("drag:allow-start-drag")
        .permission("clipboard-manager:allow-write-text")
        .permission("dialog:allow-open")
        .permission("allow-selftest-report");
    if let Err(e) = app.add_capability(cap) {
        log(app, &format!("capability for the page: {e}"));   // the page still shows, as in a browser
    }
    granted.push(port);
}

fn show(app: &AppHandle, port: u16, note: String) {
    grant(app, port);
    let url = Url::parse(&format!("http://127.0.0.1:{port}/")).expect("a valid url");
    app.state::<Shell>().status.lock().unwrap().url = url.to_string();
    set_status(app, "ready", note, "");
    if let Some(w) = app.get_webview_window("main") {
        if let Err(e) = w.navigate(url) {
            set_status(app, "error", "Could not show Kurator", e.to_string());
        }
    }
}

fn stop_server(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let Some(mut child) = shell.child.lock().unwrap().take() else { return };
    if shell.graceful.load(Ordering::SeqCst) {
        drop(child.stdin.take()); // Kurator quits when its input closes
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(8) {
            if let Ok(Some(_)) = child.try_wait() {
                log(app, "Kurator stopped");
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    log(app, "Kurator stopped (ended)");
}

// ------------------------------------------------------------------ links

/// Pages the window may show: its own start page and Kurator. Anything else opens in the browser.
fn allowed(app: &AppHandle, url: &Url) -> bool {
    match (url.scheme(), url.host_str()) {
        ("tauri" | "about" | "data" | "blob", _) => true,
        ("http" | "https", Some("tauri.localhost")) => true,
        ("http", Some("127.0.0.1")) => url.port().is_some_and(|p| app.state::<Shell>().granted.lock().unwrap().contains(&p)),
        _ => false,
    }
}

fn open_outside(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        if let Err(e) = app.opener().open_url(url.as_str(), None::<&str>) {
            log(app, &format!("open {url}: {e}"));
        }
    }
}

// --------------------------------------------------------------- commands

#[tauri::command]
fn get_status(shell: tauri::State<'_, Shell>) -> Status {
    shell.status.lock().unwrap().clone()
}

#[tauri::command]
fn retry(app: AppHandle) {
    thread::spawn(move || start(app));
}

#[tauri::command]
async fn pick_home(app: AppHandle) -> Result<(), String> {
    let Some(picked) = app.dialog().file().set_title("Choose the Kurator folder").blocking_pick_folder() else {
        return Ok(());
    };
    let dir = picked.into_path().map_err(|e| e.to_string())?;
    if !is_kurator(&dir) {
        return Err(format!("{} has no Kurator in it: choose the folder with install.bat, install.sh and kurator.bat", dir.display()));
    }
    if let Some(f) = config_file(&app) {
        if let Some(parent) = f.parent() {
            let _ = fs::create_dir_all(parent);
        }
        fs::write(&f, dir.display().to_string()).map_err(|e| e.to_string())?;
    }
    std::env::set_var("KURATOR_HOME", &dir);
    thread::spawn(move || start(app));
    Ok(())
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    app.opener().open_path(log_dir(&app).display().to_string(), None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_help(app: AppHandle) -> Result<(), String> {
    app.opener().open_url(HELP, None::<&str>).map_err(|e| e.to_string())
}

/// --selftest (for CI): the Kurator page reports what it can use, and the app exits 0 or 1.
#[tauri::command]
fn selftest_report(app: AppHandle, report: serde_json::Value) {
    let ok = ["tauri", "listen", "menu"].iter().all(|k| report.get(*k) == Some(&serde_json::Value::Bool(true)));
    selftest_done(&app, ok, &report.to_string());
}

fn selftest_done(app: &AppHandle, ok: bool, text: &str) {
    if !app.state::<Shell>().selftest {
        return;
    }
    let out = std::env::var("KURATOR_SELFTEST_OUT").map(PathBuf::from).unwrap_or_else(|_| log_dir(app).join("selftest.txt"));
    let line = format!("{}\n{text}\n", if ok { "SELFTEST OK" } else { "SELFTEST FAILED" });
    let _ = fs::write(&out, &line);
    println!("{line}");
    log(app, &line);
    app.exit(if ok { 0 } else { 1 });
}

const SELFTEST_JS: &str = r#"
(async () => {
  const r = { title: document.title, tauri: !!(window.__TAURI__ && window.__TAURI_INTERNALS__) };
  try { const un = await window.__TAURI__.event.listen('kurator://selftest', () => {}); un(); r.listen = true; } catch (e) { r.listen = String(e); }
  try { const m = await window.__TAURI__.menu.Menu.new({ items: [{ text: 'Test', action: () => {} }] }); await m.close(); r.menu = true; } catch (e) { r.menu = String(e); }
  r.grid = !!document.querySelector('.grid, #grid, .tree');
  await window.__TAURI__.core.invoke('selftest_report', { report: r });
})().catch((e) => console.error('selftest', e));
"#;

// ------------------------------------------------------------------- main

fn main() {
    let selftest = std::env::args().any(|a| a == "--selftest");
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_drag::init())
        .manage(Shell { selftest, ..Default::default() })
        .invoke_handler(tauri::generate_handler![get_status, retry, pick_home, open_logs, open_help, selftest_report])
        .setup(move |app| {
            let h = app.handle().clone();
            let nav = h.clone();
            let popup = h.clone();
            let loaded = h.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Kurator")
                .inner_size(1440.0, 900.0)
                .min_inner_size(720.0, 480.0)
                .on_navigation(move |url| {
                    let ok = allowed(&nav, url);
                    if !ok {
                        open_outside(&nav, url);
                    }
                    ok
                })
                .on_new_window(move |url, _features| {
                    open_outside(&popup, &url); // links with target=_blank: the default browser
                    NewWindowResponse::Deny
                })
                .on_download(|_webview, _event| true) // saved into Downloads (as a browser would)
                .on_page_load(move |w, p| {
                    if p.event() == PageLoadEvent::Finished && p.url().host_str() == Some("127.0.0.1") && loaded.state::<Shell>().selftest {
                        let _ = w.eval(SELFTEST_JS);
                    }
                })
                .build()?;
            if selftest {
                let h2 = h.clone();
                thread::spawn(move || {
                    thread::sleep(START_TIMEOUT + Duration::from_secs(60));
                    selftest_done(&h2, false, "no report from the Kurator page (did it load? is the page allowed to call the app?)");
                });
            }
            thread::spawn(move || start(h));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Kurator app");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            stop_server(app);
        }
    });
}
