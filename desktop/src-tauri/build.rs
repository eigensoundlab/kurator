fn main() {
    // The app's own commands get permissions (allow-get-status, ...): the start page may use the
    // first five (capabilities/splash.json), Kurator's page only allow-selftest-report (src/main.rs).
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["get_status", "retry", "pick_home", "open_logs", "open_help", "selftest_report"]),
    ))
    .expect("failed to run the Tauri build script");
}
