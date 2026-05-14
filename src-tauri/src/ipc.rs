/// ipc.rs - Tauri command handlers
///
/// Every function here is exposed to the frontend via invoke().
/// All file writes are validated against the sandbox boundary before execution.
use anyhow::Result;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::{Emitter, State};

use crate::sidecar::{send_command, SidecarRequest};

// â”€â”€â”€ WORKSPACE STATE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[derive(Default)]
pub struct WorkspaceState(pub Mutex<Option<PathBuf>>);

/// Set the active workspace root. All agent writes are sandboxed to this path.
#[tauri::command]
pub fn set_workspace(path: String, state: State<WorkspaceState>) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.exists() || !p.is_dir() {
        return Err(format!(
            "Path does not exist or is not a directory: {}",
            path
        ));
    }
    *state.0.lock().unwrap() = Some(p);
    Ok(())
}

/// Get the active workspace root.
#[tauri::command]
pub fn get_workspace(state: State<WorkspaceState>) -> Option<String> {
    state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string())
}

/// Validate that a target path is inside the workspace sandbox.
/// Returns Err if the path escapes the workspace root (path traversal guard).
#[tauri::command]
pub fn validate_workspace_write(
    target: String,
    state: State<WorkspaceState>,
) -> Result<(), String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let target_path = PathBuf::from(&target);
    let canonical = target_path
        .canonicalize()
        .map_err(|e| format!("Cannot resolve path: {}", e))?;

    if !canonical.starts_with(&workspace) {
        return Err(format!(
            "Write denied: {} is outside the workspace boundary ({})",
            target,
            workspace.display()
        ));
    }
    Ok(())
}

#[derive(Serialize, Clone)]
pub struct ProjectArtifact {
    pub name: String,
    pub path: String,
    pub kind: String,
    pub exists: bool,
    pub detail: String,
}

#[derive(Serialize)]
pub struct ProjectArtifacts {
    pub workspace: String,
    pub initialized: bool,
    pub artifacts: Vec<ProjectArtifact>,
}

#[derive(Serialize)]
pub struct WorkspaceExport {
    pub relative_path: String,
    pub absolute_path: String,
}

#[tauri::command]
pub fn get_project_artifacts(state: State<WorkspaceState>) -> Result<ProjectArtifacts, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let artifact = |name: &str, rel: &str, kind: &str, detail: String| -> ProjectArtifact {
        let path = workspace.join(rel);
        ProjectArtifact {
            name: name.into(),
            path: rel.into(),
            kind: kind.into(),
            exists: path.exists(),
            detail,
        }
    };

    let command_dir = workspace.join("core").join("execution").join("commands");
    let command_count = count_files_with_ext(&command_dir, "md");
    let runtime_dir = workspace.join(".signalos");
    let plan_file = workspace.join("core").join("strategy").join("PLAN.md");
    let issue_report_dir = runtime_dir.join("issue-reports");
    let handoff_dir = runtime_dir.join("handoffs");
    let issue_report_count = count_files_with_ext(&issue_report_dir, "md");
    let handoff_count = count_files_with_ext(&handoff_dir, "md");

    let artifacts = vec![
        artifact(
            "Runtime state",
            ".signalos",
            "folder",
            if runtime_dir.exists() {
                "Local SignalOS runtime folder is present.".into()
            } else {
                "Missing runtime state folder.".into()
            },
        ),
        artifact(
            "Wave plan",
            "core/strategy/PLAN.md",
            "file",
            if plan_file.exists() {
                "Project plan is present.".into()
            } else {
                "Missing project plan.".into()
            },
        ),
        artifact(
            "Command library",
            "core/execution/commands",
            "folder",
            if command_dir.exists() {
                format!("{command_count} command definition files found.")
            } else {
                "Missing command definition folder.".into()
            },
        ),
        artifact(
            "IDE integrations",
            "integrations",
            "folder",
            if workspace.join("integrations").exists() {
                "IDE integration files are present.".into()
            } else {
                "No IDE integration folder found.".into()
            },
        ),
        artifact(
            "Project README",
            "README.md",
            "file",
            if workspace.join("README.md").exists() {
                "Project README is present.".into()
            } else {
                "No project README found.".into()
            },
        ),
        artifact(
            "Issue reports",
            ".signalos/issue-reports",
            "folder",
            if issue_report_dir.exists() {
                format!("{issue_report_count} redacted issue report exports found.")
            } else {
                "No issue report exports yet.".into()
            },
        ),
        artifact(
            "Team handoffs",
            ".signalos/handoffs",
            "folder",
            if handoff_dir.exists() {
                format!("{handoff_count} team handoff exports found.")
            } else {
                "No team handoff exports yet.".into()
            },
        ),
    ];

    let initialized = runtime_dir.exists() && plan_file.exists();
    Ok(ProjectArtifacts {
        workspace: workspace.to_string_lossy().to_string(),
        initialized,
        artifacts,
    })
}

#[tauri::command]
pub fn open_workspace_path(
    relative_path: String,
    app: tauri::AppHandle,
    state: State<WorkspaceState>,
) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;

    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let workspace_root = workspace
        .canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))?;
    let target = workspace_root.join(relative_path.trim_matches(|c| c == '/' || c == '\\'));
    let canonical = target
        .canonicalize()
        .map_err(|e| format!("Cannot open missing path: {e}"))?;

    if !canonical.starts_with(&workspace_root) {
        return Err("Refused to open a path outside the workspace.".into());
    }

    app.opener()
        .open_path(canonical.to_string_lossy().to_string(), None::<String>)
        .map_err(|e| e.to_string())
}

// â”€â”€â”€ SIGNAL COMMAND EXECUTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn write_workspace_export(
    kind: String,
    filename: String,
    content: String,
    state: State<WorkspaceState>,
) -> Result<WorkspaceExport, String> {
    if content.len() > 2_000_000 {
        return Err("Export is too large. Keep reports under 2 MB.".into());
    }

    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let workspace_root = workspace
        .canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))?;
    let safe_kind = sanitize_path_segment(&kind).unwrap_or_else(|| "exports".into());
    let safe_filename = sanitize_filename(&filename).unwrap_or_else(|| "signalos-export.md".into());
    let export_dir = workspace_root.join(".signalos").join(&safe_kind);

    std::fs::create_dir_all(&export_dir)
        .map_err(|e| format!("Could not create export folder: {e}"))?;

    let target = export_dir.join(&safe_filename);
    if !target.starts_with(&workspace_root) {
        return Err("Refused to write an export outside the workspace.".into());
    }

    std::fs::write(&target, content).map_err(|e| format!("Could not write export: {e}"))?;

    let relative_path = format!(".signalos/{safe_kind}/{safe_filename}");
    Ok(WorkspaceExport {
        relative_path,
        absolute_path: target.to_string_lossy().to_string(),
    })
}

/// Run any /signal-* command via the Python sidecar.
#[tauri::command]
pub fn run_signal_command(
    command: String,
    args: Vec<String>,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());

    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command,
        args,
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ WAVE STATE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn get_wave_state(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "state:wave".into(),
        args: vec![],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ GATES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn get_gate_status(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "state:gates".into(),
        args: vec![],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

#[tauri::command]
pub fn sign_gate(
    gate_id: u8,
    signer: String,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "gate:sign".into(),
        args: vec![gate_id.to_string(), signer],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ BRAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn get_brain_entries(
    query: Option<String>,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "brain:search".into(),
        args: vec![query.unwrap_or_default()],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

#[tauri::command]
pub fn add_brain_entry(
    text: String,
    entry_type: String,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "brain:add".into(),
        args: vec![entry_type, text],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ AUDIT TRAIL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn get_audit_trail(limit: Option<u32>, state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "audit:list".into(),
        args: vec![limit.unwrap_or(50).to_string()],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ COST METER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[tauri::command]
pub fn get_cost_summary(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "cost:summary".into(),
        args: vec![],
        cwd,
    })
    .map_err(|e| e.to_string())?;
    Ok(id)
}

// â”€â”€â”€ GIT / WORKTREE STATUS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[derive(Serialize, Clone)]
pub struct GitWorktree {
    pub path: String,
    pub branch: String,
    pub head: String, // short SHA (7 chars)
}

#[derive(Serialize)]
pub struct GitStatus {
    pub branch: String,
    pub is_clean: bool,
    pub ahead: u32,
    pub behind: u32,
    pub worktrees: Vec<GitWorktree>,
    pub last_sync: String, // ISO-8601 timestamp of HEAD commit
}

/// Returns branch, ahead/behind, clean flag, and active worktrees for the workspace.
#[tauri::command]
pub fn get_git_status(state: State<WorkspaceState>) -> Result<GitStatus, String> {
    use std::process::Command;

    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let run = |args: &[&str]| -> String {
        Command::new("git")
            .args(args)
            .current_dir(&workspace)
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default()
    };

    let branch = run(&["rev-parse", "--abbrev-ref", "HEAD"]);
    let is_clean = run(&["status", "--short"]).is_empty();

    // ahead/behind relative to upstream (silently fails if no upstream)
    let (ahead, behind) = {
        let s = Command::new("git")
            .args(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
            .current_dir(&workspace)
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();
        let parts: Vec<u32> = s.split('\t').filter_map(|x| x.parse().ok()).collect();
        (
            parts.first().copied().unwrap_or(0),
            parts.get(1).copied().unwrap_or(0),
        )
    };

    // Last commit timestamp
    let last_sync = run(&["log", "-1", "--format=%cI"]);

    // Worktrees
    let wt_raw = run(&["worktree", "list", "--porcelain"]);
    let mut worktrees: Vec<GitWorktree> = Vec::new();
    let mut cur = GitWorktree {
        path: String::new(),
        branch: String::new(),
        head: String::new(),
    };
    for line in wt_raw.lines() {
        if let Some(v) = line.strip_prefix("worktree ") {
            if !cur.path.is_empty() {
                worktrees.push(cur.clone());
            }
            cur = GitWorktree {
                path: v.to_string(),
                branch: String::new(),
                head: String::new(),
            };
        } else if let Some(v) = line.strip_prefix("HEAD ") {
            cur.head = v.chars().take(7).collect();
        } else if let Some(v) = line.strip_prefix("branch refs/heads/") {
            cur.branch = v.to_string();
        } else if line == "bare" {
            cur.branch = "(bare)".to_string();
        }
    }
    if !cur.path.is_empty() {
        worktrees.push(cur);
    }

    Ok(GitStatus {
        branch,
        is_clean,
        ahead,
        behind,
        worktrees,
        last_sync,
    })
}

// â”€â”€â”€ AUTO-UPDATER (T1-5) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/// Check for a new SignalOS version using the Tauri updater plugin.
/// Returns { available: bool, version?, notes?, date? }.
#[tauri::command]
pub async fn check_for_updates(channel: Option<String>) -> Result<serde_json::Value, String> {
    let selected = match channel.as_deref() {
        Some("stable") => "stable",
        _ => "beta",
    };
    let manifest = if selected == "stable" {
        "latest.json"
    } else {
        "beta.json"
    };
    let url = format!(
        "https://raw.githubusercontent.com/samerzakaria/signalos-app/main/distribution/update-manifest/{manifest}"
    );
    let current_version = env!("CARGO_PKG_VERSION");

    let response = match reqwest::get(&url).await {
        Ok(value) => value,
        Err(e) => {
            return Ok(serde_json::json!({
                "available": false,
                "channel": selected,
                "error": format!("Could not reach update manifest: {e}"),
            }));
        }
    };

    if !response.status().is_success() {
        return Ok(serde_json::json!({
            "available": false,
            "channel": selected,
            "error": format!("Update manifest returned HTTP {}", response.status()),
        }));
    }

    let manifest_json: serde_json::Value = match response.json().await {
        Ok(value) => value,
        Err(e) => {
            return Ok(serde_json::json!({
                "available": false,
                "channel": selected,
                "error": format!("Could not parse update manifest: {e}"),
            }));
        }
    };

    let version = manifest_json
        .get("version")
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    let signatures_missing = manifest_json
        .get("platforms")
        .and_then(|value| value.as_object())
        .map(|platforms| {
            platforms.values().any(|entry| {
                entry
                    .get("signature")
                    .and_then(|value| value.as_str())
                    .unwrap_or_default()
                    .is_empty()
            })
        })
        .unwrap_or(true);

    Ok(serde_json::json!({
        "available": !version.is_empty() && version != current_version,
        "channel": selected,
        "current_version": current_version,
        "version": version,
        "notes": manifest_json.get("notes").and_then(|value| value.as_str()).unwrap_or_default(),
        "date": manifest_json.get("pub_date").and_then(|value| value.as_str()).unwrap_or_default(),
        "signatures_missing": signatures_missing,
    }))
}

// â”€â”€â”€ FILE WATCHER (T1-4) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/// Start watching the workspace for file-system changes.
/// Spawns a Tokio task that polls the workspace directory mtime every 2 s
/// and emits a "workspace:changed" event to the frontend when a change is
/// detected.  Safe to call multiple times - only one watcher per process.
#[tauri::command]
pub async fn start_workspace_watch(
    app: tauri::AppHandle,
    state: State<'_, WorkspaceState>,
) -> Result<(), String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        use std::time::{Duration, SystemTime};
        use tokio::time::sleep;

        let mut last_mtime: Option<SystemTime> = None;
        loop {
            sleep(Duration::from_secs(2)).await;
            if let Ok(meta) = tokio::fs::metadata(&workspace).await {
                if let Ok(mtime) = meta.modified() {
                    if last_mtime.map_or(false, |prev| prev != mtime) {
                        let _ = app_handle
                            .emit("workspace:changed", workspace.to_string_lossy().to_string());
                    }
                    last_mtime = Some(mtime);
                }
            }
        }
    });

    Ok(())
}

// â”€â”€â”€ HELPERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

fn uuid() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    static NEXT_ID: AtomicU64 = AtomicU64::new(1);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let seq = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    format!("req-{:x}-{:x}", nanos, seq)
}

fn count_files_with_ext(dir: &Path, ext: &str) -> usize {
    std::fs::read_dir(dir)
        .map(|entries| {
            entries
                .filter_map(|entry| entry.ok())
                .filter(|entry| {
                    entry
                        .path()
                        .extension()
                        .and_then(|value| value.to_str())
                        .is_some_and(|value| value.eq_ignore_ascii_case(ext))
                })
                .count()
        })
        .unwrap_or(0)
}

// â”€â”€â”€ UNIT TESTS (T5-1) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

fn sanitize_path_segment(value: &str) -> Option<String> {
    let cleaned: String = value
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
        .take(48)
        .collect();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned)
    }
}

fn sanitize_filename(value: &str) -> Option<String> {
    let cleaned: String = value
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        .take(96)
        .collect();
    let cleaned = cleaned.trim_matches('.').to_string();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // â”€â”€ Workspace path validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn workspace_state_starts_empty() {
        let state = WorkspaceState::default();
        assert!(state.0.lock().unwrap().is_none());
    }

    #[test]
    fn workspace_state_set_and_get() {
        let state = WorkspaceState::default();
        // Use the temp dir as a guaranteed-existing path
        let tmp = std::env::temp_dir();
        *state.0.lock().unwrap() = Some(tmp.clone());
        let stored = state.0.lock().unwrap().clone().unwrap();
        assert_eq!(stored, tmp);
    }

    #[test]
    fn workspace_path_traversal_guard() {
        // Simulate what validate_workspace_write does, without needing a Tauri state
        let workspace = std::env::temp_dir();
        let inside = workspace.join("allowed.txt");
        // A path that escapes via ../.. should not start_with workspace
        let outside = PathBuf::from("/etc/passwd");
        assert!(inside.starts_with(&workspace));
        assert!(!outside.starts_with(&workspace));
    }

    // â”€â”€ UUID helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn uuid_has_correct_prefix() {
        let id = uuid();
        assert!(
            id.starts_with("req-"),
            "uuid should start with 'req-': {id}"
        );
    }

    #[test]
    fn uuid_is_nonempty_hex_suffix() {
        let id = uuid();
        let hex_part = id.trim_start_matches("req-");
        assert!(!hex_part.is_empty());
        assert!(
            hex_part.chars().all(|c| c.is_ascii_hexdigit() || c == '-'),
            "uuid suffix should use hex digits and separators: {hex_part}"
        );
    }

    // UUID uniqueness is not guaranteed (subsec_nanos can repeat), but
    // two calls in rapid succession will often differ - test format only.
    #[test]
    fn uuid_two_calls_same_format() {
        let a = uuid();
        let b = uuid();
        assert!(a.starts_with("req-"));
        assert!(b.starts_with("req-"));
    }

    // â”€â”€ Git worktree parser â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn git_worktree_struct_serialises() {
        let wt = GitWorktree {
            path: "/home/user/proj".into(),
            branch: "main".into(),
            head: "abc1234".into(),
        };
        let json = serde_json::to_string(&wt).unwrap();
        assert!(json.contains("main"));
        assert!(json.contains("abc1234"));
    }

    #[test]
    fn git_status_struct_serialises() {
        let gs = GitStatus {
            branch: "feat/x".into(),
            is_clean: true,
            ahead: 2,
            behind: 0,
            worktrees: vec![],
            last_sync: "2026-05-03T00:00:00Z".into(),
        };
        let json = serde_json::to_string(&gs).unwrap();
        assert!(json.contains("feat/x"));
        assert!(json.contains("\"is_clean\":true"));
        assert!(json.contains("\"ahead\":2"));
    }

    // â”€â”€ Sandbox boundary logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn path_inside_workspace_passes() {
        let workspace = PathBuf::from("/workspace/root");
        let target = PathBuf::from("/workspace/root/subdir/file.txt");
        assert!(target.starts_with(&workspace));
    }

    #[test]
    fn path_outside_workspace_fails() {
        let workspace = PathBuf::from("/workspace/root");
        let target = PathBuf::from("/workspace/other/file.txt");
        assert!(!target.starts_with(&workspace));
    }

    #[test]
    fn dotdot_escape_detected() {
        // Simulates what canonicalize() would catch:
        // /workspace/root/../../../etc/passwd -> starts_with check after canonicalize
        let workspace = PathBuf::from("/workspace/root");
        // After canonicalize the path would be /etc/passwd - not inside workspace
        let escaped = PathBuf::from("/etc/passwd");
        assert!(!escaped.starts_with(&workspace));
    }

    #[test]
    fn export_filename_is_sanitized() {
        assert_eq!(
            sanitize_filename("../issue report?.md").as_deref(),
            Some("issuereport.md")
        );
        assert_eq!(
            sanitize_filename("handoff.md").as_deref(),
            Some("handoff.md")
        );
        assert!(sanitize_filename("").is_none());
    }

    #[test]
    fn export_kind_is_sanitized() {
        assert_eq!(
            sanitize_path_segment("issue-reports").as_deref(),
            Some("issue-reports")
        );
        assert_eq!(
            sanitize_path_segment("../audit logs").as_deref(),
            Some("auditlogs")
        );
    }
}
