fn main() {
    // The app's own commands get permissions (allow-get-status, ...): the start page may use the first
    // seven (capabilities/splash.json), Kurator's page the last two (src/main.rs).
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "get_status", "show_kurator", "retry", "pick_home", "open_logs", "open_help", "install_kurator", "update_kurator", "selftest_report",
        ]),
    ))
    .expect("failed to run the Tauri build script");
}
