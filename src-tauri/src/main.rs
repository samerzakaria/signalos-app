// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ipc;
mod keychain;
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
            // Spawn the Python signalos sidecar on startup
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = sidecar::spawn_python_sidecar(&app_handle).await {
                    eprintln!("[SignalOS] Failed to start Python sidecar: {}", e);
                }
            });

            // Open devtools in debug builds
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").unwrap();
                window.open_devtools();
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // IPC — signal command execution
            ipc::run_signal_command,
            ipc::get_wave_state,
            ipc::get_gate_status,
            ipc::sign_gate,
            ipc::get_brain_entries,
            ipc::add_brain_entry,
            ipc::get_audit_trail,
            ipc::get_cost_summary,
            // Keychain
            keychain::store_api_key,
            keychain::get_api_key,
            keychain::delete_api_key,
            keychain::has_api_key,
            // Workspace
            ipc::set_workspace,
            ipc::get_workspace,
            ipc::validate_workspace_write,
        ])
        .run(tauri::generate_context!())
        .expect("error while running SignalOS");
}
