/// sidecar.rs - Python subprocess manager
///
/// Runs the bundled SignalOS Core sidecar and exposes a restartable runtime for
/// the desktop UI. All /signal-* commands are sent over stdin as JSON messages;
/// responses are emitted back to the frontend as Tauri events.
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter};
use tauri_plugin_shell::ShellExt;

#[derive(Serialize, Debug)]
pub struct SidecarRequest {
    pub id: String,
    pub command: String,
    pub args: Vec<String>,
    pub cwd: Option<String>,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct SidecarResponse {
    pub id: String,
    pub ok: bool,
    pub output: Option<String>,
    pub error: Option<String>,
    pub data: Option<serde_json::Value>,
}

/// Wave 2 / G1-7: progress event from a long-running command.
/// Multiplexed on the same stdout stream as SidecarResponse, distinguished
/// by the presence of the `kind: "progress"` field.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SidecarProgress {
    pub id: String,
    pub kind: String, // always "progress"
    pub phase: String,
    pub substep: String,
    pub state: String, // pending | running | done | error
    pub detail: Option<String>,
    pub ts: u128,
}

#[derive(Serialize, Clone, Debug)]
pub struct SidecarRuntimeStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub generation: u64,
    pub last_event: String,
    pub last_error: Option<String>,
    pub updated_at_ms: u128,
}

impl Default for SidecarRuntimeStatus {
    fn default() -> Self {
        Self {
            running: false,
            pid: None,
            generation: 0,
            last_event: "Not started".into(),
            last_error: None,
            updated_at_ms: now_ms(),
        }
    }
}

#[derive(Debug)]
enum SidecarControl {
    Request(SidecarRequest),
    Stop,
}

struct SidecarManager {
    tx: Mutex<Option<tokio::sync::mpsc::Sender<SidecarControl>>>,
    status: Mutex<SidecarRuntimeStatus>,
    next_generation: AtomicU64,
}

impl Default for SidecarManager {
    fn default() -> Self {
        Self {
            tx: Mutex::new(None),
            status: Mutex::new(SidecarRuntimeStatus::default()),
            next_generation: AtomicU64::new(1),
        }
    }
}

static MANAGER: OnceLock<SidecarManager> = OnceLock::new();

fn manager() -> &'static SidecarManager {
    MANAGER.get_or_init(SidecarManager::default)
}

/// Spawn the Python sidecar and set up stdin/stdout IPC channels.
pub async fn spawn_python_sidecar(app: &AppHandle) -> Result<()> {
    start_python_sidecar(app, false).await
}

#[tauri::command]
pub fn get_sidecar_status() -> SidecarRuntimeStatus {
    manager().status.lock().unwrap().clone()
}

#[tauri::command]
pub async fn restart_python_sidecar(app: AppHandle) -> Result<SidecarRuntimeStatus, String> {
    stop_current_sidecar();
    start_python_sidecar(&app, true)
        .await
        .map_err(|e| e.to_string())?;
    Ok(get_sidecar_status())
}

async fn start_python_sidecar(app: &AppHandle, replace_existing: bool) -> Result<()> {
    if replace_existing {
        stop_current_sidecar();
    } else if manager().tx.lock().unwrap().is_some() {
        return Ok(());
    }

    let shell = app.shell();
    // Snapshot all keychain-stored API keys into env vars at spawn time so
    // the Python harness (orchestrator, signal-harness call) can resolve
    // LLM providers without the user exporting env vars manually. Re-snapshot
    // on every restart so a key updated in Settings takes effect after
    // restart_python_sidecar.
    let env_keys = crate::keychain::snapshot_env_keys();
    let (mut rx, mut child) = shell
        .sidecar("signalos-python")
        .context("Failed to find signalos-python sidecar")?
        .envs(env_keys)
        .spawn()
        .context("Failed to spawn signalos-python")?;

    let pid = child.pid();
    let generation = manager().next_generation.fetch_add(1, Ordering::Relaxed);
    let (tx, mut cmd_rx) = tokio::sync::mpsc::channel::<SidecarControl>(64);
    *manager().tx.lock().unwrap() = Some(tx);
    update_status(generation, |status| {
        status.running = true;
        status.pid = Some(pid);
        status.last_event = "Engine started".into();
        status.last_error = None;
    });

    let _ = app.emit("sidecar:status", get_sidecar_status());
    let app_emit = app.clone();

    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        let mut stdout_buf = String::new();
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(chunk) => {
                    stdout_buf.push_str(&String::from_utf8_lossy(&chunk));
                    while let Some(pos) = stdout_buf.find('\n') {
                        let line = stdout_buf[..pos].trim().to_string();
                        stdout_buf = stdout_buf[pos + 1..].to_string();
                        if line.is_empty() {
                            continue;
                        }

                        update_status(generation, |status| {
                            status.last_event = "Engine response received".into();
                            status.last_error = None;
                        });
                        // Wave 2 / G1-7: distinguish progress events from
                        // final responses. Progress carries kind="progress";
                        // a final response is everything else.
                        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) {
                            if value.get("kind").and_then(|v| v.as_str()) == Some("progress") {
                                if let Ok(prog) =
                                    serde_json::from_value::<SidecarProgress>(value.clone())
                                {
                                    let _ = app_emit.emit("sidecar:progress", &prog);
                                    continue;
                                }
                            }
                            // Phase 3 Stream B: agent events carry
                            // kind="agent-event". Pass the full envelope through
                            // unchanged as the "agent:event" payload for the
                            // frontend (Stream C) to parse.
                            if value.get("kind").and_then(|v| v.as_str()) == Some("agent-event") {
                                let _ = app_emit.emit("agent:event", &value);
                                continue;
                            }
                            if let Ok(resp) = serde_json::from_value::<SidecarResponse>(value) {
                                let _ = app_emit.emit("sidecar:response", &resp);
                                continue;
                            }
                        }
                        let _ = app_emit.emit("sidecar:log", &line);
                    }
                }
                CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    eprintln!("[signalos-py] {}", text);
                    update_status(generation, |status| {
                        status.last_event = "Engine diagnostic output".into();
                    });
                    let _ = app_emit.emit("sidecar:stderr", &text);
                }
                CommandEvent::Error(e) => {
                    eprintln!("[sidecar] error: {}", e);
                    update_status(generation, |status| {
                        status.running = false;
                        status.last_event = "Engine error".into();
                        status.last_error = Some(e.clone());
                    });
                    clear_sender_if_generation(generation);
                    let _ = app_emit.emit("sidecar:error", &e);
                    let _ = app_emit.emit("sidecar:status", get_sidecar_status());
                }
                CommandEvent::Terminated(status) => {
                    eprintln!("[sidecar] terminated: {:?}", status);
                    update_status(generation, |runtime| {
                        runtime.running = false;
                        runtime.pid = None;
                        runtime.last_event = "Engine terminated".into();
                        runtime.last_error =
                            status.code.map(|code| format!("Exited with code {code}"));
                    });
                    clear_sender_if_generation(generation);
                    let _ = app_emit.emit("sidecar:terminated", &status.code);
                    let _ = app_emit.emit("sidecar:status", get_sidecar_status());
                    break;
                }
                _ => {}
            }
        }
    });

    tauri::async_runtime::spawn(async move {
        while let Some(req) = cmd_rx.recv().await {
            match req {
                SidecarControl::Request(req) => {
                    if let Ok(json) = serde_json::to_string(&req) {
                        let line = format!("{}\n", json);
                        if let Err(error) = child.write(line.as_bytes()) {
                            update_status(generation, |status| {
                                status.last_event = "Engine stdin write failed".into();
                                status.last_error = Some(error.to_string());
                            });
                        }
                    }
                }
                SidecarControl::Stop => {
                    // Tree-kill: the PyInstaller launcher exe spawns a real
                    // Python interpreter as a child. `child.kill()` only
                    // hits the launcher; the interpreter survives and
                    // keeps owning stdout. taskkill /T walks the tree.
                    let pid_to_kill = child.pid();
                    let _ = child.kill();
                    tree_kill(pid_to_kill);
                    break;
                }
            }
        }
    });

    Ok(())
}

fn stop_current_sidecar() {
    if let Some(tx) = manager().tx.lock().unwrap().take() {
        let _ = tx.try_send(SidecarControl::Stop);
    }
}

pub fn send_command(req: SidecarRequest) -> Result<()> {
    let tx = manager()
        .tx
        .lock()
        .unwrap()
        .clone()
        .context("Sidecar not started")?;
    tx.try_send(SidecarControl::Request(req))
        .context("Sidecar command queue full")?;
    Ok(())
}

fn update_status(generation: u64, update: impl FnOnce(&mut SidecarRuntimeStatus)) {
    let mut status = manager().status.lock().unwrap();
    if generation < status.generation {
        return;
    }
    status.generation = generation;
    status.updated_at_ms = now_ms();
    update(&mut status);
}

fn clear_sender_if_generation(generation: u64) {
    let current_generation = manager().status.lock().unwrap().generation;
    if generation == current_generation {
        *manager().tx.lock().unwrap() = None;
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

/// Cross-platform tree-kill (sidecar.rs + runtime.rs share this).
///
/// PyInstaller-bundled sidecars and npm dev servers spawn child processes
/// (the launcher exe → real Python; `npm` → `node` → `vite`). Killing only
/// the immediate process leaves the children alive and chewing CPU/ports.
///
/// Windows: `taskkill /T /F /PID <pid>` walks the process tree.
/// Unix: send SIGKILL to the process group (negative pid) via the `kill`
/// command. Falls back to a plain SIGKILL if the group send fails.
pub fn tree_kill(pid: u32) {
    use std::process::Command;
    if cfg!(windows) {
        let _ = Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .output();
    } else {
        // Negative pid means "process group" for /bin/kill.
        let _ = Command::new("kill")
            .args(["-9", &format!("-{pid}")])
            .output();
        // Fallback: kill the leader directly in case the child wasn't in
        // its own process group.
        let _ = Command::new("kill").args(["-9", &pid.to_string()]).output();
    }
}
