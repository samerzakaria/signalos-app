// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// All modules live in lib.rs — import them from the library crate
use signalos_desktop_lib::governance;
use signalos_desktop_lib::ipc;
use signalos_desktop_lib::keychain;
use signalos_desktop_lib::provider;
use signalos_desktop_lib::sidecar;

use tauri::{Emitter, Manager};

fn main() {
    // ── Startup timer (T5-6) ─────────────────────────────────────────────────
    let t0 = std::time::Instant::now();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
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
                    let _ = app_handle.emit("sidecar:error", e.to_string());
                }
            });

            // ── Native menu (T1-1) ────────────────────────────────────────────
            build_menu(app)?;

            // ── Open devtools in debug builds ─────────────────────────────────
            #[cfg(debug_assertions)]
            if let Some(win) = app.get_webview_window("main") {
                win.open_devtools();
            }

            // ── Log startup time (T5-6) ───────────────────────────────────────
            eprintln!("[SignalOS] startup ready in {}ms", t0.elapsed().as_millis());

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

// ─── NATIVE MENU (T1-1) ──────────────────────────────────────────────────────
//
// Builds a minimal OS-native menu with File / Edit / View / Help.
// Tauri 2 uses the Menu builder API; items emit window events that the
// frontend can handle via window.__TAURI__.menu (or simply as shortcuts).
fn build_menu(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};

    let handle = app.handle();

    // ── File ──────────────────────────────────────────────────────────────
    let file_menu = Submenu::with_items(
        handle,
        "File",
        true,
        &[
            &MenuItem::with_id(handle, "open-workspace", "Open Workspace…", true, Some("CmdOrCtrl+O"))?,
            &PredefinedMenuItem::separator(handle)?,
            &MenuItem::with_id(handle, "export-audit",   "Export Audit Trail…", true, None::<&str>)?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::quit(handle, None)?,
        ],
    )?;

    // ── Edit ──────────────────────────────────────────────────────────────
    let edit_menu = Submenu::with_items(
        handle,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(handle, None)?,
            &PredefinedMenuItem::redo(handle, None)?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::cut(handle, None)?,
            &PredefinedMenuItem::copy(handle, None)?,
            &PredefinedMenuItem::paste(handle, None)?,
            &PredefinedMenuItem::select_all(handle, None)?,
        ],
    )?;

    // ── View ──────────────────────────────────────────────────────────────
    let view_menu = Submenu::with_items(
        handle,
        "View",
        true,
        &[
            &MenuItem::with_id(handle, "nav-chat",      "Chat",       true, Some("CmdOrCtrl+1"))?,
            &MenuItem::with_id(handle, "nav-dashboard", "Dashboard",  true, Some("CmdOrCtrl+2"))?,
            &MenuItem::with_id(handle, "nav-brain",     "Brain",      true, Some("CmdOrCtrl+3"))?,
            &MenuItem::with_id(handle, "nav-audit",     "Audit Trail",true, Some("CmdOrCtrl+4"))?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::fullscreen(handle, None)?,
        ],
    )?;

    // ── Help ──────────────────────────────────────────────────────────────
    let help_menu = Submenu::with_items(
        handle,
        "Help",
        true,
        &[
            &MenuItem::with_id(handle, "open-docs",    "SignalOS Docs",         true, None::<&str>)?,
            &MenuItem::with_id(handle, "check-update", "Check for Updates…",    true, None::<&str>)?,
            &PredefinedMenuItem::separator(handle)?,
            &MenuItem::with_id(handle, "about",        "About SignalOS",        true, None::<&str>)?,
        ],
    )?;

    let menu = Menu::with_items(handle, &[&file_menu, &edit_menu, &view_menu, &help_menu])?;
    app.set_menu(menu)?;

    // ── Handle menu events → forward to frontend as JS-visible events ─────
    app.on_menu_event(|app_handle, event| {
        let window = app_handle.get_webview_window("main");
        match event.id().as_ref() {
            "open-workspace"  => { let _ = window.map(|w| w.emit("menu:open-workspace", ())); }
            "export-audit"    => { let _ = window.map(|w| w.emit("menu:export-audit", ())); }
            "nav-chat"        => { let _ = window.map(|w| w.emit("menu:nav", "chat")); }
            "nav-dashboard"   => { let _ = window.map(|w| w.emit("menu:nav", "dashboard")); }
            "nav-brain"       => { let _ = window.map(|w| w.emit("menu:nav", "brain")); }
            "nav-audit"       => { let _ = window.map(|w| w.emit("menu:nav", "audit")); }
            "check-update"    => { let _ = window.map(|w| w.emit("menu:check-update", ())); }
            "open-docs"       => {
                use tauri_plugin_opener::OpenerExt;
                let _ = app_handle.opener().open_url("https://docs.signalos.io", None::<&str>);
            }
            _ => {}
        }
    });

    Ok(())
}
