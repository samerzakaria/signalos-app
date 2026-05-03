/// sidecar.rs — Python subprocess manager
///
/// Spawns the signalos Python CLI as a long-running sidecar process.
/// All /signal-* commands are sent over stdin as JSON messages and
/// responses are read from stdout. stderr is forwarded to the Tauri
/// webview as log events.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;
use tauri::{AppHandle, Emitter};
use tauri_plugin_shell::ShellExt;

/// A message sent to the Python sidecar over stdin
#[derive(Serialize, Debug)]
pub struct SidecarRequest {
    pub id:      String,
    pub command: String,
    pub args:    Vec<String>,
    pub cwd:     Option<String>,
}

/// A response received from the Python sidecar over stdout
#[derive(Serialize, Deserialize, Debug)]
pub struct SidecarResponse {
    pub id:      String,
    pub ok:      bool,
    pub output:  Option<String>,
    pub error:   Option<String>,
    pub data:    Option<serde_json::Value>,
}

/// Global sidecar child handle (write side only; reads handled in spawn loop)
static SIDECAR_TX: OnceLock<tokio::sync::mpsc::Sender<SidecarRequest>> = OnceLock::new();

/// Spawn the Python sidecar and set up stdin/stdout IPC channels.
pub async fn spawn_python_sidecar(app: &AppHandle) -> Result<()> {
    let shell = app.shell();

    // The sidecar binary name must match tauri.conf.json → bundle.externalBin
    // In dev, this resolves to `python3 -m signalos.ipc_server`
    // In release, it resolves to the bundled `signalos-python` binary
    let (mut rx, mut child) = shell
        .sidecar("signalos-python")
        .context("Failed to find signalos-python sidecar")?
        .spawn()
        .context("Failed to spawn signalos-python")?;

    let (tx, mut cmd_rx) = tokio::sync::mpsc::channel::<SidecarRequest>(64);
    SIDECAR_TX.set(tx).ok();

    let app_emit = app.clone();

    // Stdout reader — emit events back to frontend
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    // Try to parse as SidecarResponse and emit to frontend
                    if let Ok(resp) = serde_json::from_str::<SidecarResponse>(&text) {
                        let _ = app_emit.emit("sidecar:response", &resp);
                    } else {
                        let _ = app_emit.emit("sidecar:log", &text);
                    }
                }
                CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    eprintln!("[signalos-py] {}", text);
                    let _ = app_emit.emit("sidecar:stderr", &text);
                }
                CommandEvent::Error(e) => {
                    eprintln!("[sidecar] error: {}", e);
                    let _ = app_emit.emit("sidecar:error", &e);
                }
                CommandEvent::Terminated(status) => {
                    eprintln!("[sidecar] terminated: {:?}", status);
                    let _ = app_emit.emit("sidecar:terminated", &status.code);
                    break;
                }
                _ => {}
            }
        }
    });

    // Stdin writer — forward queued commands to Python
    tauri::async_runtime::spawn(async move {
        while let Some(req) = cmd_rx.recv().await {
            if let Ok(json) = serde_json::to_string(&req) {
                let line = format!("{}\n", json);
                let _ = child.write(line.as_bytes());
            }
        }
    });

    Ok(())
}

/// Send a command to the Python sidecar. Returns immediately — response
/// arrives as a `sidecar:response` event on the frontend.
pub fn send_command(req: SidecarRequest) -> Result<()> {
    let tx = SIDECAR_TX.get().context("Sidecar not started")?;
    tx.try_send(req).context("Sidecar command queue full")?;
    Ok(())
}
