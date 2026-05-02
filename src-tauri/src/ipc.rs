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

// ─── HELPERS ─────────────────────────────────────────────────────────────────

fn uuid() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    format!("req-{:x}", t)
}
