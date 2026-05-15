/// runtime.rs — LocalProcessSupervisor (Wave 2 / G1-10)
///
/// Spawns and manages local processes (npm install / npm run dev / python app.py)
/// that serve the generated app in the right-pane preview. Each process is keyed
/// by (workspace, stack). Stdout/stderr are captured and emitted to the frontend
/// as `preview:event` events; the supervisor extracts the listening port from
/// stdout so the iframe can point at http://localhost:<port>.
///
/// Also exposes a Node.js runtime detection probe so the wizard / Build flow
/// can surface clear errors when the user picks a JS stack but doesn't have
/// Node 18+ on PATH.
///
/// Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.3 + §11.5/1-2
use serde::Serialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Mutex, OnceLock};
use tauri::{AppHandle, Emitter, State};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::mpsc;

// ─── Types ────────────────────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct PreviewRuntime {
    pub key: String,
    pub stack: String,
    pub workspace: String,
    pub status: String, // installing | starting | running | stopped | error
    pub url: Option<String>,
    pub pid: Option<u32>,
    pub last_event: String,
    pub last_error: Option<String>,
    pub started_at_ms: u128,
}

#[derive(Serialize, Clone)]
pub struct PreviewEvent {
    pub key: String,
    pub kind: String, // status | stdout | stderr | port | error | exit
    pub message: String,
    pub ts_ms: u128,
}

#[derive(Serialize, Clone)]
pub struct NodeProbe {
    pub found: bool,
    pub version: Option<String>,
    pub major: Option<u32>,
    pub path: Option<String>,
    pub message: String,
}

// ─── Supervisor singleton ─────────────────────────────────────────────────────

struct Process {
    runtime: PreviewRuntime,
    stop_tx: Option<mpsc::Sender<()>>,
}

#[derive(Default)]
pub struct SupervisorState {
    inner: Mutex<HashMap<String, Process>>,
}

static SUPERVISOR: OnceLock<SupervisorState> = OnceLock::new();

fn supervisor() -> &'static SupervisorState {
    SUPERVISOR.get_or_init(SupervisorState::default)
}

fn now_ms() -> u128 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

// ─── Stack contracts ──────────────────────────────────────────────────────────

#[derive(Clone)]
struct StackContract {
    install: Option<(&'static str, &'static [&'static str])>,
    run: (&'static str, &'static [&'static str]),
    port_regex: &'static str,
    default_port: u16,
}

fn stack_for(name: &str) -> Option<StackContract> {
    match name {
        "react-vite" | "vite" => Some(StackContract {
            install: Some(("npm", &["install"])),
            run: ("npm", &["run", "dev", "--", "--host", "127.0.0.1"]),
            port_regex: r"(?i)local:\s+https?://[^:]+:(\d+)",
            default_port: 5173,
        }),
        "next" => Some(StackContract {
            install: Some(("npm", &["install"])),
            run: ("npm", &["run", "dev"]),
            port_regex: r"(?i)(?:ready|local).*?http://[^:]+:(\d+)",
            default_port: 3000,
        }),
        "node-express" => Some(StackContract {
            install: Some(("npm", &["install"])),
            run: ("npm", &["start"]),
            port_regex: r"(?i)(?:listening|port)[^\d]*(\d{2,5})",
            default_port: 3000,
        }),
        "python-flask" => Some(StackContract {
            install: None,
            run: ("python", &["app.py"]),
            port_regex: r"(?i)(?:running on|listening).*?:(\d+)",
            default_port: 5000,
        }),
        "static" => Some(StackContract {
            install: None,
            run: ("python", &["-m", "http.server", "0"]),
            port_regex: r"(?i)serving http.*?:(\d+)",
            default_port: 8000,
        }),
        _ => None,
    }
}

// ─── Tauri commands ───────────────────────────────────────────────────────────

/// Probe for Node.js on PATH. Returns the major version (e.g. 18, 20) when
/// available so the UI can warn if it's too old for a chosen stack.
#[tauri::command]
pub async fn probe_node() -> NodeProbe {
    let out = tokio::process::Command::new("node")
        .arg("--version")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .await;
    match out {
        Ok(o) if o.status.success() => {
            let raw = String::from_utf8_lossy(&o.stdout).trim().to_string();
            let version = raw.trim_start_matches('v').to_string();
            let major = version.split('.').next().and_then(|s| s.parse::<u32>().ok());
            let path = which_path("node");
            let too_old = major.map(|m| m < 18).unwrap_or(true);
            NodeProbe {
                found: true,
                major,
                version: Some(version),
                path,
                message: if too_old {
                    "Node found but version < 18. Some stacks need 18+.".into()
                } else {
                    "Node ready.".into()
                },
            }
        }
        _ => NodeProbe {
            found: false,
            version: None,
            major: None,
            path: None,
            message: "Node.js not detected on PATH. Install Node 18+ from https://nodejs.org and reopen SignalOS.".into(),
        },
    }
}

fn which_path(bin: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        for ext in if cfg!(windows) {
            &[".exe", ".cmd", ".bat", ""][..]
        } else {
            &[""][..]
        } {
            let candidate = dir.join(format!("{bin}{ext}"));
            if candidate.is_file() {
                return Some(candidate.to_string_lossy().to_string());
            }
        }
    }
    None
}

/// Start (or restart) the preview process for the current workspace+stack.
/// Side-effects: emits `preview:event` events; updates SupervisorState.
#[tauri::command]
pub async fn start_preview(
    app: AppHandle,
    stack: String,
    workspace: String,
    state: State<'_, crate::ipc::WorkspaceState>,
) -> Result<PreviewRuntime, String> {
    // Workspace must be the active one — a guard to prevent running random paths.
    let active = state.0.lock().unwrap().clone();
    let active_path = active.ok_or("No workspace selected")?;
    let req_path = PathBuf::from(&workspace);
    let req_canon = req_path
        .canonicalize()
        .map_err(|e| format!("Cannot canonicalize workspace: {e}"))?;
    let active_canon = active_path
        .canonicalize()
        .map_err(|e| format!("Cannot canonicalize active workspace: {e}"))?;
    if req_canon != active_canon {
        return Err("Refused to start a preview outside the active workspace.".into());
    }

    let contract = stack_for(&stack).ok_or_else(|| format!("Unknown stack: {stack}"))?;
    let key = format!("{}::{}", active_canon.display(), stack);
    stop_internal(&key, &app).await; // idempotent restart

    let (stop_tx, mut stop_rx) = mpsc::channel::<()>(1);
    {
        let mut map = supervisor().inner.lock().unwrap();
        map.insert(
            key.clone(),
            Process {
                runtime: PreviewRuntime {
                    key: key.clone(),
                    stack: stack.clone(),
                    workspace: active_canon.to_string_lossy().to_string(),
                    status: "installing".into(),
                    url: None,
                    pid: None,
                    last_event: "Preparing".into(),
                    last_error: None,
                    started_at_ms: now_ms(),
                },
                stop_tx: Some(stop_tx),
            },
        );
    }

    let app_clone = app.clone();
    let workspace_clone = active_canon.clone();
    let key_clone = key.clone();
    tauri::async_runtime::spawn(async move {
        // Install phase — interruptible by Stop. run_blocking selects on the
        // shared stop_rx so clicking Stop during `npm install` actually
        // tree-kills the install subprocess instead of waiting it out.
        if let Some((bin, args)) = contract.install {
            update_runtime(&key_clone, |r| r.status = "installing".into());
            emit_event(&app_clone, &key_clone, "status", "Installing dependencies");
            let outcome = run_blocking(
                &app_clone,
                &key_clone,
                bin,
                args,
                &workspace_clone,
                contract.port_regex,
                &mut stop_rx,
            )
            .await;
            match outcome {
                RunOutcome::Success => {}
                RunOutcome::Failed => {
                    update_runtime(&key_clone, |r| {
                        r.status = "error".into();
                        r.last_error = Some("install failed".into());
                    });
                    emit_event(&app_clone, &key_clone, "error", "Install failed.");
                    return;
                }
                RunOutcome::Stopped => {
                    // User clicked Stop mid-install. Mark as stopped (not error)
                    // so the UI shows "Stopped" not "Errored — check log".
                    update_runtime(&key_clone, |r| {
                        r.status = "stopped".into();
                        r.last_error = None;
                    });
                    emit_event(
                        &app_clone,
                        &key_clone,
                        "exit",
                        "stopped by user during install",
                    );
                    return;
                }
            }
        }
        // Run phase — keep child alive, capture port from stdout
        update_runtime(&key_clone, |r| r.status = "starting".into());
        emit_event(&app_clone, &key_clone, "status", "Starting dev server");
        let mut cmd = Command::new(contract.run.0);
        cmd.args(contract.run.1)
            .current_dir(&workspace_clone)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child: Child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                update_runtime(&key_clone, |r| {
                    r.status = "error".into();
                    r.last_error = Some(e.to_string());
                });
                emit_event(
                    &app_clone,
                    &key_clone,
                    "error",
                    &format!("Could not start: {e}"),
                );
                return;
            }
        };
        let pid = child.id();
        update_runtime(&key_clone, |r| r.pid = pid);

        let port_re = regex::Regex::new(contract.port_regex).ok();
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let app_l1 = app_clone.clone();
        let key_l1 = key_clone.clone();
        let port_re_l1 = port_re.clone();
        let port_seen = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let port_seen_l1 = port_seen.clone();
        if let Some(out) = stdout {
            let mut reader = BufReader::new(out).lines();
            tauri::async_runtime::spawn(async move {
                while let Ok(Some(line)) = reader.next_line().await {
                    emit_event(&app_l1, &key_l1, "stdout", &line);
                    if !port_seen_l1.load(std::sync::atomic::Ordering::Relaxed) {
                        if let Some(re) = &port_re_l1 {
                            if let Some(caps) = re.captures(&line) {
                                if let Some(p) =
                                    caps.get(1).and_then(|m| m.as_str().parse::<u16>().ok())
                                {
                                    let url = format!("http://localhost:{p}");
                                    update_runtime(&key_l1, |r| {
                                        r.status = "running".into();
                                        r.url = Some(url.clone());
                                        r.last_event = format!("Listening on {url}");
                                    });
                                    emit_event(&app_l1, &key_l1, "port", &url);
                                    port_seen_l1.store(true, std::sync::atomic::Ordering::Relaxed);
                                }
                            }
                        }
                    }
                }
            });
        }
        let app_l2 = app_clone.clone();
        let key_l2 = key_clone.clone();
        if let Some(err) = stderr {
            let mut reader = BufReader::new(err).lines();
            tauri::async_runtime::spawn(async move {
                while let Ok(Some(line)) = reader.next_line().await {
                    emit_event(&app_l2, &key_l2, "stderr", &line);
                }
            });
        }

        // After ~30s without a port, assume default and let the iframe try.
        {
            let app_fallback = app_clone.clone();
            let key_fallback = key_clone.clone();
            let port_seen_fallback = port_seen.clone();
            let default_port = contract.default_port;
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_secs(30)).await;
                if !port_seen_fallback.load(std::sync::atomic::Ordering::Relaxed) {
                    let url = format!("http://localhost:{default_port}");
                    update_runtime(&key_fallback, |r| {
                        if r.url.is_none() {
                            r.status = "running".into();
                            r.url = Some(url.clone());
                            r.last_event = format!("Default port {default_port}");
                        }
                    });
                    emit_event(&app_fallback, &key_fallback, "port", &url);
                }
            });
        }

        // Wait for either stop signal or child exit.
        tokio::select! {
            _ = stop_rx.recv() => {
                // Tree-kill: `npm` spawns `node`, `node` spawns vite, vite
                // spawns more. child.kill() only hits npm. Walk the tree.
                let pid_to_kill = child.id();
                let _ = child.kill().await;
                if let Some(pid) = pid_to_kill {
                    crate::sidecar::tree_kill(pid);
                }
                emit_event(&app_clone, &key_clone, "exit", "stopped by user");
                update_runtime(&key_clone, |r| r.status = "stopped".into());
            }
            status = child.wait() => {
                let code = status.ok().and_then(|s| s.code()).unwrap_or(-1);
                emit_event(&app_clone, &key_clone, "exit", &format!("exited with code {code}"));
                update_runtime(&key_clone, |r| {
                    r.status = if code == 0 { "stopped".into() } else { "error".into() };
                    r.last_error = if code == 0 { None } else { Some(format!("exit code {code}")) };
                });
            }
        }
    });

    Ok(get_runtime_for(&key).unwrap_or_else(|| PreviewRuntime {
        key,
        stack,
        workspace: active_canon.to_string_lossy().to_string(),
        status: "installing".into(),
        url: None,
        pid: None,
        last_event: "Preparing".into(),
        last_error: None,
        started_at_ms: now_ms(),
    }))
}

/// Three-state outcome for `run_blocking`. The caller distinguishes between
/// "the command failed on its own" (Failed → status=error) and "the user
/// clicked Stop mid-run" (Stopped → status=stopped). Conflating them puts
/// "errored" on the UI for a clean user-initiated cancel, which is wrong.
enum RunOutcome {
    Success,
    Failed,
    Stopped,
}

/// Run an install/build command to completion or until the user hits Stop.
///
/// The install phase is now interruptible: we race `child.wait()` against
/// `stop_rx.recv()` in a `tokio::select!`. If Stop wins, we tree-kill the
/// install subprocess (including grandchildren — npm spawns node spawns…)
/// and return `Stopped`.
async fn run_blocking(
    app: &AppHandle,
    key: &str,
    bin: &str,
    args: &[&str],
    cwd: &PathBuf,
    _port_regex: &str,
    stop_rx: &mut tokio::sync::mpsc::Receiver<()>,
) -> RunOutcome {
    let mut cmd = Command::new(bin);
    cmd.args(args)
        .current_dir(cwd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            emit_event(app, key, "error", &format!("spawn failed: {e}"));
            return RunOutcome::Failed;
        }
    };
    let install_pid = child.id();
    if let Some(out) = child.stdout.take() {
        let app_c = app.clone();
        let key_c = key.to_string();
        tauri::async_runtime::spawn(async move {
            let mut r = BufReader::new(out).lines();
            while let Ok(Some(line)) = r.next_line().await {
                emit_event(&app_c, &key_c, "stdout", &line);
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        let app_c = app.clone();
        let key_c = key.to_string();
        tauri::async_runtime::spawn(async move {
            let mut r = BufReader::new(err).lines();
            while let Ok(Some(line)) = r.next_line().await {
                emit_event(&app_c, &key_c, "stderr", &line);
            }
        });
    }
    tokio::select! {
        // Honor Stop arriving during install.
        _ = stop_rx.recv() => {
            emit_event(app, key, "status", "Stopping install");
            let _ = child.kill().await;
            if let Some(pid) = install_pid {
                crate::sidecar::tree_kill(pid);
            }
            // Drain any final exit so we don't leave a zombie.
            let _ = child.wait().await;
            RunOutcome::Stopped
        }
        status = child.wait() => {
            if matches!(status, Ok(ref s) if s.success()) {
                RunOutcome::Success
            } else {
                RunOutcome::Failed
            }
        }
    }
}

/// Stop the preview process. Idempotent.
#[tauri::command]
pub async fn stop_preview(key: String, app: AppHandle) -> Result<(), String> {
    stop_internal(&key, &app).await;
    Ok(())
}

async fn stop_internal(key: &str, app: &AppHandle) {
    let stop_tx = {
        let mut map = supervisor().inner.lock().unwrap();
        if let Some(p) = map.get_mut(key) {
            p.stop_tx.take()
        } else {
            None
        }
    };
    if let Some(tx) = stop_tx {
        let _ = tx.send(()).await;
    }
    emit_event(app, key, "status", "Stopping");
    update_runtime(key, |r| r.status = "stopped".into());
}

#[tauri::command]
pub fn list_previews() -> Vec<PreviewRuntime> {
    supervisor()
        .inner
        .lock()
        .unwrap()
        .values()
        .map(|p| p.runtime.clone())
        .collect()
}

#[tauri::command]
pub fn get_preview(key: String) -> Option<PreviewRuntime> {
    get_runtime_for(&key)
}

fn get_runtime_for(key: &str) -> Option<PreviewRuntime> {
    supervisor()
        .inner
        .lock()
        .unwrap()
        .get(key)
        .map(|p| p.runtime.clone())
}

fn update_runtime(key: &str, f: impl FnOnce(&mut PreviewRuntime)) {
    if let Some(p) = supervisor().inner.lock().unwrap().get_mut(key) {
        f(&mut p.runtime);
    }
}

fn emit_event(app: &AppHandle, key: &str, kind: &str, message: &str) {
    let _ = app.emit(
        "preview:event",
        PreviewEvent {
            key: key.to_string(),
            kind: kind.to_string(),
            message: message.to_string(),
            ts_ms: now_ms(),
        },
    );
}
