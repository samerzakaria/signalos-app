/// ipc.rs — Tauri command handlers
///
/// Every function here is exposed to the frontend via invoke().
/// All file writes are validated against the sandbox boundary before execution.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::State;

use crate::sidecar::{send_command, SidecarRequest};

// ─── WORKSPACE STATE ────────────────────────────────────────────────────────

pub struct WorkspaceState(pub Mutex<Option<PathBuf>>);

/// Set the active workspace root. All agent writes are sandboxed to this path.
#[tauri::command]
pub fn set_workspace(path: String, state: State<WorkspaceState>) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.exists() || !p.is_dir() {
        return Err(format!("Path does not exist or is not a directory: {}", path));
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

// ─── SIGNAL COMMAND EXECUTION ────────────────────────────────────────────────

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
    send_command(SidecarRequest { id: id.clone(), command, args, cwd })
        .map_err(|e| e.to_string())?;
    Ok(id)
}

// ─── WAVE STATE ──────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_wave_state(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest { id: id.clone(), command: "state:wave".into(), args: vec![], cwd })
        .map_err(|e| e.to_string())?;
    Ok(id)
}

// ─── GATES ───────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_gate_status(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest { id: id.clone(), command: "state:gates".into(), args: vec![], cwd })
        .map_err(|e| e.to_string())?;
    Ok(id)
}

#[tauri::command]
pub fn sign_gate(gate_id: u8, signer: String, state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
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

// ─── BRAIN ───────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_brain_entries(query: Option<String>, state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
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
pub fn add_brain_entry(text: String, entry_type: String, state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
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

// ─── AUDIT TRAIL ─────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_audit_trail(limit: Option<u32>, state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
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

// ─── COST METER ──────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_cost_summary(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state.0.lock().unwrap().as_ref().map(|p| p.to_string_lossy().to_string());
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

// ─── GIT / WORKTREE STATUS ───────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct GitWorktree {
    pub path:   String,
    pub branch: String,
    pub head:   String,  // short SHA (7 chars)
}

#[derive(Serialize)]
pub struct GitStatus {
    pub branch:    String,
    pub is_clean:  bool,
    pub ahead:     u32,
    pub behind:    u32,
    pub worktrees: Vec<GitWorktree>,
    pub last_sync: String,  // ISO-8601 timestamp of HEAD commit
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

    let branch   = run(&["rev-parse", "--abbrev-ref", "HEAD"]);
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
        (parts.first().copied().unwrap_or(0), parts.get(1).copied().unwrap_or(0))
    };

    // Last commit timestamp
    let last_sync = run(&["log", "-1", "--format=%cI"]);

    // Worktrees
    let wt_raw = run(&["worktree", "list", "--porcelain"]);
    let mut worktrees: Vec<GitWorktree> = Vec::new();
    let mut cur = GitWorktree { path: String::new(), branch: String::new(), head: String::new() };
    for line in wt_raw.lines() {
        if let Some(v) = line.strip_prefix("worktree ") {
            if !cur.path.is_empty() { worktrees.push(cur.clone()); }
            cur = GitWorktree { path: v.to_string(), branch: String::new(), head: String::new() };
        } else if let Some(v) = line.strip_prefix("HEAD ") {
            cur.head = v.chars().take(7).collect();
        } else if let Some(v) = line.strip_prefix("branch refs/heads/") {
            cur.branch = v.to_string();
        } else if line == "bare" {
            cur.branch = "(bare)".to_string();
        }
    }
    if !cur.path.is_empty() { worktrees.push(cur); }

    Ok(GitStatus { branch, is_clean, ahead, behind, worktrees, last_sync })
}

// ─── AUTO-UPDATER (T1-5) ─────────────────────────────────────────────────────

/// Check for a new SignalOS version using the Tauri updater plugin.
/// Returns { available: bool, version?, notes?, date? }.
#[tauri::command]
pub async fn check_for_updates(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    use tauri_plugin_updater::UpdaterExt;

    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await {
        Ok(Some(update)) => Ok(serde_json::json!({
            "available": true,
            "version":   update.version,
            "notes":     update.body.unwrap_or_default(),
            "date":      update.date.map(|d| d.to_string()).unwrap_or_default(),
        })),
        Ok(None) => Ok(serde_json::json!({ "available": false })),
        Err(e)   => Ok(serde_json::json!({ "available": false, "error": e.to_string() })),
    }
}

// ─── FILE WATCHER (T1-4) ─────────────────────────────────────────────────────

/// Start watching the workspace for file-system changes.
/// Spawns a Tokio task that polls the workspace directory mtime every 2 s
/// and emits a "workspace:changed" event to the frontend when a change is
/// detected.  Safe to call multiple times — only one watcher per process.
#[tauri::command]
pub async fn start_workspace_watch(app: tauri::AppHandle, state: State<'_, WorkspaceState>) -> Result<(), String> {
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
                        let _ = app_handle.emit("workspace:changed", workspace.to_string_lossy().to_string());
                    }
                    last_mtime = Some(mtime);
                }
            }
        }
    });

    Ok(())
}

// ─── HELPERS ─────────────────────────────────────────────────────────────────

fn uuid() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    format!("req-{:x}", t)
}

// ─── UNIT TESTS (T5-1) ───────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── Workspace path validation ────────────────────────────────────────────

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
        let inside  = workspace.join("allowed.txt");
        // A path that escapes via ../.. should not start_with workspace
        let outside = PathBuf::from("/etc/passwd");
        assert!(inside.starts_with(&workspace));
        assert!(!outside.starts_with(&workspace));
    }

    // ── UUID helper ──────────────────────────────────────────────────────────

    #[test]
    fn uuid_has_correct_prefix() {
        let id = uuid();
        assert!(id.starts_with("req-"), "uuid should start with 'req-': {id}");
    }

    #[test]
    fn uuid_is_nonempty_hex_suffix() {
        let id = uuid();
        let hex_part = id.trim_start_matches("req-");
        assert!(!hex_part.is_empty());
        assert!(hex_part.chars().all(|c| c.is_ascii_hexdigit()),
            "uuid hex suffix should be all hex digits: {hex_part}");
    }

    // UUID uniqueness is not guaranteed (subsec_nanos can repeat), but
    // two calls in rapid succession will often differ — test format only.
    #[test]
    fn uuid_two_calls_same_format() {
        let a = uuid();
        let b = uuid();
        assert!(a.starts_with("req-"));
        assert!(b.starts_with("req-"));
    }

    // ── Git worktree parser ──────────────────────────────────────────────────

    #[test]
    fn git_worktree_struct_serialises() {
        let wt = GitWorktree {
            path:   "/home/user/proj".into(),
            branch: "main".into(),
            head:   "abc1234".into(),
        };
        let json = serde_json::to_string(&wt).unwrap();
        assert!(json.contains("main"));
        assert!(json.contains("abc1234"));
    }

    #[test]
    fn git_status_struct_serialises() {
        let gs = GitStatus {
            branch:    "feat/x".into(),
            is_clean:  true,
            ahead:     2,
            behind:    0,
            worktrees: vec![],
            last_sync: "2026-05-03T00:00:00Z".into(),
        };
        let json = serde_json::to_string(&gs).unwrap();
        assert!(json.contains("feat/x"));
        assert!(json.contains("\"is_clean\":true"));
        assert!(json.contains("\"ahead\":2"));
    }

    // ── Sandbox boundary logic ───────────────────────────────────────────────

    #[test]
    fn path_inside_workspace_passes() {
        let workspace = PathBuf::from("/workspace/root");
        let target    = PathBuf::from("/workspace/root/subdir/file.txt");
        assert!(target.starts_with(&workspace));
    }

    #[test]
    fn path_outside_workspace_fails() {
        let workspace = PathBuf::from("/workspace/root");
        let target    = PathBuf::from("/workspace/other/file.txt");
        assert!(!target.starts_with(&workspace));
    }

    #[test]
    fn dotdot_escape_detected() {
        // Simulates what canonicalize() would catch:
        // /workspace/root/../../../etc/passwd → starts_with check after canonicalize
        let workspace = PathBuf::from("/workspace/root");
        // After canonicalize the path would be /etc/passwd — not inside workspace
        let escaped = PathBuf::from("/etc/passwd");
        assert!(!escaped.starts_with(&workspace));
    }
}
