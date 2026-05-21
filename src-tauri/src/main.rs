// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// All modules live in lib.rs - import them from the library crate
use signalos_desktop_lib::enforcement;
use signalos_desktop_lib::governance;
use signalos_desktop_lib::ipc;
use signalos_desktop_lib::keychain;
use signalos_desktop_lib::provider;
use signalos_desktop_lib::runtime;
use signalos_desktop_lib::sidecar;
use signalos_desktop_lib::test_automation;

use tauri::{Emitter, LogicalSize, Manager};

fn main() {
    // â”€â”€ Startup timer (T5-6) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            // â”€â”€ Provider config dir (user-editable providers.json lives here) â”€â”€
            // e.g. ~/Library/Application Support/io.signalos.app/  (macOS)
            //      %APPDATA%\io.signalos.app\                       (Windows)
            let config_dir = app
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."));

            app.manage(provider::ProviderState::new(config_dir.clone()));
            app.manage(ipc::WorkspaceState::default());
            app.manage(governance::GovernanceState::new());
            // Wave 3 / G2-21: enforcement state for runtime rule checks.
            app.manage(enforcement::EnforcementStore::new());

            // â”€â”€ Spawn the Python SignalOS Core sidecar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = sidecar::spawn_python_sidecar(&app_handle).await {
                    eprintln!("[SignalOS] Failed to start Python sidecar: {e}");
                    let _ = app_handle.emit("sidecar:error", e.to_string());
                }
            });

            // â”€â”€ Native menu (T1-1) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            build_menu(app)?;

            // tauri.conf.json's minWidth/minHeight weren't being honored on
            // macOS in the v2.0.0-internal build (window shattered below
            // 900px wide). Programmatic set_min_size is the backstop.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_min_size(Some(LogicalSize::new(900.0, 600.0)));
            }

            // â”€â”€ Open devtools in debug builds â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            #[cfg(debug_assertions)]
            if let Some(win) = app.get_webview_window("main") {
                win.open_devtools();
            }

            // â”€â”€ Log startup time (T5-6) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            eprintln!("[SignalOS] startup ready in {}ms", t0.elapsed().as_millis());

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // â”€â”€ Workspace â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::set_workspace,
            ipc::get_workspace,
            ipc::validate_workspace_write,
            ipc::get_project_artifacts,
            ipc::open_workspace_path,
            ipc::write_workspace_export,
            ipc::write_workspace_files,
            ipc::preview_workspace_files,
            ipc::read_workspace_file,
            ipc::list_workspace_dir,
            ipc::upsert_workspace_secret,
            // Wave 1 / G0-6 — Replit-style secrets manager
            ipc::list_workspace_secrets,
            ipc::reveal_workspace_secret,
            ipc::delete_workspace_secret,
            ipc::apply_workspace_env_diff,
            // Wave 3 — Identity + role assignment
            ipc::set_identity,
            ipc::get_identity,
            ipc::check_role_for_gate,
            ipc::get_git_status,        // live branch / worktree info
            ipc::start_workspace_watch, // file watcher -> workspace:changed events (T1-4)
            // â”€â”€ Auto-updater (T1-5) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::check_for_updates,
            // â”€â”€ Signal commands (-> Python sidecar) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::run_signal_command,
            sidecar::get_sidecar_status,
            sidecar::restart_python_sidecar,
            // â”€â”€ Wave + Gate state (read) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::get_wave_state,
            ipc::get_gate_status,
            ipc::sign_gate,
            // â”€â”€ Brain â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::get_brain_entries,
            ipc::add_brain_entry,
            // â”€â”€ Audit trail â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ipc::get_audit_trail,
            ipc::get_cost_summary,
            // â”€â”€ Keychain â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            keychain::store_api_key,
            keychain::delete_api_key,
            keychain::has_api_key,
            // â”€â”€ Providers + cost â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            provider::list_providers,
            provider::get_active_provider,
            provider::set_active_provider,
            provider::set_provider_model, // user updates model in settings
            provider::set_provider_pricing, // user corrects pricing
            provider::get_cost_state,
            provider::record_token_usage,
            provider::reset_session_cost,
            provider::set_monthly_budget,
            provider::fetch_provider_models, // live model list from provider API
            provider::test_provider_connection,
            provider::send_provider_message,
            provider::send_provider_message_stream,
            // ── Wave 2 / G1-10+11 — LocalProcessSupervisor (preview pane) ──
            runtime::probe_node,
            runtime::start_preview,
            runtime::stop_preview,
            runtime::list_previews,
            runtime::get_preview,
            // ── Wave 3 / G2-21..26 — Runtime enforcement ──
            enforcement::get_enforcement_state,
            enforcement::build_precheck,
            enforcement::override_rule,
            enforcement::set_rule_mode,
            enforcement::freeze_wave,
            enforcement::unfreeze_wave,
            // ── Wave 5 / G4 — Test Automation enforcement ──
            test_automation::list_test_debt,
            test_automation::add_test_debt,
            test_automation::resolve_test_debt,
            test_automation::check_mutation_threshold,
            test_automation::check_test_first,
            test_automation::read_mutation_score,
        ])
        .run(tauri::generate_context!())
        .expect("error while running SignalOS");
}

// â”€â”€â”€ NATIVE MENU (T1-1) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
//
// Builds a minimal OS-native menu with File / Edit / View / Help.
// Tauri 2 uses the Menu builder API; items emit window events that the
// frontend can handle via window.__TAURI__.menu (or simply as shortcuts).
fn build_menu(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};

    let handle = app.handle();

    // â”€â”€ File â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    let file_menu = Submenu::with_items(
        handle,
        "File",
        true,
        &[
            &MenuItem::with_id(
                handle,
                "open-workspace",
                "Open Workspace...",
                true,
                Some("CmdOrCtrl+O"),
            )?,
            &PredefinedMenuItem::separator(handle)?,
            &MenuItem::with_id(
                handle,
                "export-audit",
                "Export Handoff...",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::quit(handle, None)?,
        ],
    )?;

    // â”€â”€ Edit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    // â”€â”€ View â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    let view_menu = Submenu::with_items(
        handle,
        "View",
        true,
        &[
            &MenuItem::with_id(handle, "nav-chat", "Chat", true, Some("CmdOrCtrl+1"))?,
            &MenuItem::with_id(
                handle,
                "nav-dashboard",
                "Dashboard",
                true,
                Some("CmdOrCtrl+2"),
            )?,
            &MenuItem::with_id(handle, "nav-brain", "Brain", true, Some("CmdOrCtrl+3"))?,
            &MenuItem::with_id(
                handle,
                "nav-audit",
                "Audit Trail",
                true,
                Some("CmdOrCtrl+4"),
            )?,
            &PredefinedMenuItem::separator(handle)?,
            &PredefinedMenuItem::fullscreen(handle, None)?,
        ],
    )?;

    // â”€â”€ Help â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    let help_menu = Submenu::with_items(
        handle,
        "Help",
        true,
        &[
            &MenuItem::with_id(handle, "open-docs", "SignalOS Docs", true, None::<&str>)?,
            &MenuItem::with_id(
                handle,
                "check-update",
                "Check for Updates...",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(handle)?,
            &MenuItem::with_id(handle, "about", "About SignalOS", true, None::<&str>)?,
        ],
    )?;

    let menu = Menu::with_items(handle, &[&file_menu, &edit_menu, &view_menu, &help_menu])?;
    app.set_menu(menu)?;

    // â”€â”€ Handle menu events -> forward to frontend as JS-visible events â”€â”€â”€â”€â”€
    app.on_menu_event(|app_handle, event| {
        let window = app_handle.get_webview_window("main");
        match event.id().as_ref() {
            "open-workspace" => {
                let _ = window.map(|w| w.emit("menu:open-workspace", ()));
            }
            "export-audit" => {
                let _ = window.map(|w| w.emit("menu:export-audit", ()));
            }
            "nav-chat" => {
                let _ = window.map(|w| w.emit("menu:nav", "chat"));
            }
            "nav-dashboard" => {
                let _ = window.map(|w| w.emit("menu:nav", "dashboard"));
            }
            "nav-brain" => {
                let _ = window.map(|w| w.emit("menu:nav", "brain"));
            }
            "nav-audit" => {
                let _ = window.map(|w| w.emit("menu:nav", "audit"));
            }
            "check-update" => {
                let _ = window.map(|w| w.emit("menu:check-update", ()));
            }
            "open-docs" => {
                use tauri_plugin_opener::OpenerExt;
                let _ = app_handle
                    .opener()
                    .open_url("https://docs.signalos.io", None::<&str>);
            }
            _ => {}
        }
    });

    Ok(())
}
