// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod governance;
mod ipc;
mod keychain;
mod provider;
mod sidecar;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // ── Provider config dir (user-editable providers.json lives here) ──
            // e.g. ~/Library/Application Support/io.signalos.app/  (macOS)
            //      %APPDATA%\io.signalos.app\                       (Windows)
            let config_dir = app
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."));

            app.manage(provider::ProviderState::new(config_dir.clone()));
            app.manage(ipc::WorkspaceState::default());
            app.manage(governance::GovernanceState::new());

            // ── Spawn the Python SignalOS Core sidecar ────────────────────────
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = sidecar::spawn_python_sidecar(&app_handle).await {
                    eprintln!("[SignalOS] Failed to start Python sidecar: {e}");
                }
            });

            // ── Open devtools in debug builds ─────────────────────────────────
            #[cfg(debug_assertions)]
            if let Some(win) = app.get_webview_window("main") {
                win.open_devtools();
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // ── Workspace ───────────────────────────────────────────────────
            ipc::set_workspace,
            ipc::get_workspace,
            ipc::validate_workspace_write,
            ipc::get_git_status,               // live branch / worktree info
            ipc::start_workspace_watch,        // file watcher → workspace:changed events (T1-4)

            // ── Auto-updater (T1-5) ─────────────────────────────────────────
            ipc::check_for_updates,

            // ── Signal commands (→ Python sidecar) ──────────────────────────
            ipc::run_signal_command,

            // ── Wave + Gate state (read) ────────────────────────────────────
            ipc::get_wave_state,
            ipc::get_gate_status,
            ipc::sign_gate,

            // ── Brain ────────────────────────────────────────────────────────
            ipc::get_brain_entries,
            ipc::add_brain_entry,

            // ── Audit trail ──────────────────────────────────────────────────
            ipc::get_audit_trail,
            ipc::get_cost_summary,

            // ── Keychain ─────────────────────────────────────────────────────
            keychain::store_api_key,
            keychain::get_api_key,
            keychain::delete_api_key,
            keychain::has_api_key,

            // ── Providers + cost ─────────────────────────────────────────────
            provider::list_providers,
            provider::get_active_provider,
            provider::set_active_provider,
            provider::set_provider_model,       // user updates model in settings
            provider::set_provider_pricing,     // user corrects pricing
            provider::get_cost_state,
            provider::record_token_usage,
            provider::reset_session_cost,
            provider::set_monthly_budget,
            provider::fetch_provider_models,    // live model list from provider API
        ])
        .run(tauri::generate_context!())
        .expect("error while running SignalOS");
}
