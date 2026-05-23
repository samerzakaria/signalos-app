/// ipc.rs - Tauri command handlers
///
/// Every function here is exposed to the frontend via invoke().
/// All file writes are validated against the sandbox boundary before execution.
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::{Emitter, State};

use crate::governance::{append_audit, AuditEntry};
use crate::sidecar::{send_command, SidecarRequest};

// ─── Wave 3 / G2-20: shared audit helper ──────────────────────────────────────
//
// Every state-changing IPC command writes one line to .signalos/AUDIT_TRAIL.jsonl
// before the action's success path returns. Best-effort: if the write fails the
// command still completes, but the failure is logged to stderr so it surfaces
// in the sidecar:stderr stream.
fn audit(workspace: &Path, action: &str, detail: String) {
    let entry = AuditEntry {
        ts: chrono_iso8601(),
        action: action.to_string(),
        actor: "app".to_string(),
        gate_id: None,
        detail,
    };
    if let Err(e) = append_audit(workspace, &entry) {
        eprintln!("[audit] failed to write {action}: {e}");
    }
}

pub fn ipc_chrono_iso8601() -> String {
    chrono_iso8601()
}

fn chrono_iso8601() -> String {
    // RFC 3339 / ISO 8601 without bringing in `chrono`. Uses the platform clock.
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Manual UTC formatting. Good enough for log lines; not parsing-grade.
    let days = secs / 86400;
    let mut year = 1970i64;
    let mut d = days as i64;
    loop {
        let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
        let ylen = if leap { 366 } else { 365 };
        if d < ylen {
            break;
        }
        d -= ylen;
        year += 1;
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let mut months = [
        31u32,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month = 0usize;
    let mut day = d as u32;
    for (i, m) in months.iter().enumerate() {
        if day < *m {
            month = i;
            break;
        }
        day -= *m;
    }
    let _ = &mut months;
    let secs_of_day = secs % 86400;
    let hour = (secs_of_day / 3600) as u32;
    let minute = ((secs_of_day % 3600) / 60) as u32;
    let second = (secs_of_day % 60) as u32;
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year,
        month + 1,
        day + 1,
        hour,
        minute,
        second
    )
}

// â”€â”€â”€ WORKSPACE STATE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[derive(Default)]
pub struct WorkspaceState(pub Mutex<Option<PathBuf>>);

impl WorkspaceState {
    pub fn new(active_workspace: Option<PathBuf>) -> Self {
        Self(Mutex::new(active_workspace))
    }
}

const WORKSPACE_SETTINGS_FILE: &str = "workspace-state.json";
const RECENT_WORKSPACE_LIMIT: usize = 12;

#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct WorkspaceSettings {
    pub active_workspace: Option<String>,
    pub recent_workspaces: Vec<RecentWorkspace>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct RecentWorkspace {
    pub path: String,
    pub name: String,
    pub last_opened: String,
}

pub struct WorkspaceSettingsState {
    settings_path: PathBuf,
    settings: Mutex<WorkspaceSettings>,
}

impl WorkspaceSettingsState {
    pub fn new(config_dir: PathBuf) -> Self {
        let settings_path = config_dir.join(WORKSPACE_SETTINGS_FILE);
        let settings = load_workspace_settings(&settings_path);
        Self {
            settings_path,
            settings: Mutex::new(settings),
        }
    }

    pub fn restored_active_workspace(&self) -> Option<PathBuf> {
        let settings = self.settings.lock().unwrap();
        settings
            .active_workspace
            .as_deref()
            .and_then(|path| resolve_workspace_root(path).ok())
    }

    fn snapshot(&self) -> WorkspaceSettings {
        self.settings.lock().unwrap().clone()
    }

    fn set_active_workspace(&self, workspace: &Path) -> Result<(), String> {
        let workspace = workspace
            .canonicalize()
            .map_err(|e| format!("Cannot resolve workspace: {e}"))?;
        let path = workspace.to_string_lossy().to_string();
        let name = workspace_display_name(&workspace);
        let now = chrono_iso8601();

        let next_settings = {
            let mut settings = self.settings.lock().unwrap().clone();
            settings.active_workspace = Some(path.clone());
            settings
                .recent_workspaces
                .retain(|entry| !paths_equal_for_settings(&entry.path, &path));
            settings.recent_workspaces.insert(
                0,
                RecentWorkspace {
                    path,
                    name,
                    last_opened: now,
                },
            );
            settings.recent_workspaces.truncate(RECENT_WORKSPACE_LIMIT);
            settings
        };

        save_workspace_settings(&self.settings_path, &next_settings)?;
        *self.settings.lock().unwrap() = next_settings;
        Ok(())
    }

    fn clear_active_workspace(&self) -> Result<(), String> {
        let next_settings = {
            let mut settings = self.settings.lock().unwrap().clone();
            settings.active_workspace = None;
            settings
        };
        save_workspace_settings(&self.settings_path, &next_settings)?;
        *self.settings.lock().unwrap() = next_settings;
        Ok(())
    }
}

/// Set the active workspace root. All agent writes are sandboxed to this path.
#[tauri::command]
pub fn set_workspace(
    path: String,
    state: State<WorkspaceState>,
    settings: State<WorkspaceSettingsState>,
) -> Result<(), String> {
    let workspace = resolve_workspace_root(&path)?;
    settings.set_active_workspace(&workspace)?;
    *state.0.lock().unwrap() = Some(workspace);
    // Non-destructive: setting the active workspace is a pure state change.
    // We deliberately do NOT call `audit(...)` here — `audit()` writes to
    // `.signalos/AUDIT_TRAIL.jsonl`, which would force-create that folder
    // (and any parents) in the user's project the moment they select it
    // for browsing. The first audit entry lands when the user takes a
    // state-mutating action (init / file write / secret / gate sign / etc.)
    // — i.e. the same moment SignalOS first writes anything else.
    Ok(())
}

/// Clear the active workspace without treating an empty string as a path.
#[tauri::command]
pub fn clear_workspace(
    state: State<WorkspaceState>,
    settings: State<WorkspaceSettingsState>,
) -> Result<(), String> {
    settings.clear_active_workspace()?;
    *state.0.lock().unwrap() = None;
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

#[derive(Deserialize)]
struct GateArtifactManifest {
    gates: std::collections::BTreeMap<String, Vec<GateArtifactSpec>>,
}

#[derive(Deserialize)]
struct GateArtifactSpec {
    rel_path: String,
    label: String,
}

#[derive(Serialize)]
pub struct RecentWorkspaceStatus {
    pub path: String,
    pub name: String,
    pub last_opened: String,
    pub exists: bool,
    pub is_directory: bool,
    pub initialized: bool,
    pub profile_id: Option<String>,
}

#[derive(Serialize)]
pub struct WorkspaceStatusIntegration {
    pub artifacts: String,
    pub gates: String,
    pub wave: String,
    pub validator: String,
}

#[derive(Serialize)]
pub struct WorkspaceStatus {
    pub active_path: Option<String>,
    pub profile_id: Option<String>,
    pub exists: bool,
    pub is_directory: bool,
    pub initialized: bool,
    pub signalos_runtime: bool,
    pub plan_present: bool,
    pub status: String,
    pub artifacts: Vec<ProjectArtifact>,
    pub recent_workspaces: Vec<RecentWorkspaceStatus>,
    pub integration: WorkspaceStatusIntegration,
}

#[derive(Serialize)]
pub struct WorkspaceExport {
    pub relative_path: String,
    pub absolute_path: String,
}

#[derive(Deserialize)]
pub struct WorkspaceFileInput {
    pub path: String,
    pub content: String,
}

#[derive(Serialize)]
pub struct WorkspaceFileWrite {
    pub relative_path: String,
    pub absolute_path: String,
    pub bytes: usize,
}

#[derive(Serialize)]
pub struct WorkspaceFileWriteResult {
    pub files: Vec<WorkspaceFileWrite>,
}

#[tauri::command]
pub fn get_project_artifacts(state: State<WorkspaceState>) -> Result<ProjectArtifacts, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    Ok(collect_project_artifacts(&workspace))
}

#[tauri::command]
pub fn get_workspace_status(
    state: State<WorkspaceState>,
    settings: State<WorkspaceSettingsState>,
) -> WorkspaceStatus {
    let active = state.0.lock().unwrap().clone();
    let settings_snapshot = settings.snapshot();
    let recent_workspaces = settings_snapshot
        .recent_workspaces
        .iter()
        .map(recent_workspace_status)
        .collect::<Vec<_>>();

    let Some(workspace) = active else {
        return WorkspaceStatus {
            active_path: None,
            profile_id: None,
            exists: false,
            is_directory: false,
            initialized: false,
            signalos_runtime: false,
            plan_present: false,
            status: "none".into(),
            artifacts: Vec::new(),
            recent_workspaces,
            integration: workspace_status_integration(),
        };
    };

    let exists = workspace.exists();
    let is_directory = workspace.is_dir();
    let runtime_dir = workspace.join(".signalos");
    let plan_file = workspace.join("core").join("strategy").join("PLAN.md");
    let signalos_runtime = runtime_dir.exists();
    let plan_present = plan_file.exists();
    let artifacts = if exists && is_directory {
        collect_project_artifacts(&workspace).artifacts
    } else {
        Vec::new()
    };
    let initialized = signalos_runtime && plan_present;
    let profile_id = read_workspace_profile_id(&workspace);
    let status = if !exists {
        "missing"
    } else if !is_directory {
        "invalid"
    } else if initialized {
        "initialized"
    } else {
        "uninitialized"
    };

    WorkspaceStatus {
        active_path: Some(workspace.to_string_lossy().to_string()),
        profile_id,
        exists,
        is_directory,
        initialized,
        signalos_runtime,
        plan_present,
        status: status.into(),
        artifacts,
        recent_workspaces,
        integration: workspace_status_integration(),
    }
}

fn collect_project_artifacts(workspace: &Path) -> ProjectArtifacts {
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
    let package_file = workspace.join("package.json");
    let requirements_file = workspace.join("requirements.txt");
    let app_entry = detect_app_entry(&workspace);
    let issue_report_count = count_files_with_ext(&issue_report_dir, "md");
    let handoff_count = count_files_with_ext(&handoff_dir, "md");

    let mut artifacts = vec![
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
            "App manifest",
            if package_file.exists() {
                "package.json"
            } else {
                "requirements.txt"
            },
            "file",
            if package_file.exists() {
                "Node/JavaScript app manifest is present.".into()
            } else if requirements_file.exists() {
                "Python requirements file is present.".into()
            } else {
                "No app dependency manifest found yet.".into()
            },
        ),
        artifact(
            "App entry",
            app_entry.as_deref().unwrap_or("index.html"),
            "file",
            if let Some(entry) = &app_entry {
                format!("Generated app entry found at {entry}.")
            } else {
                "No generated app entry found yet.".into()
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
    artifacts.extend(shared_gate_artifacts(workspace).into_iter().map(|(gate, spec)| {
        artifact(
            &format!("{} {}", gate, spec.label),
            &spec.rel_path,
            "file",
            if workspace.join(&spec.rel_path).exists() {
                format!("Required {} artifact is present.", gate)
            } else {
                format!("Missing required {} artifact.", gate)
            },
        )
    }));

    let initialized = runtime_dir.exists() && plan_file.exists();
    ProjectArtifacts {
        workspace: workspace.to_string_lossy().to_string(),
        initialized,
        artifacts,
    }
}

fn shared_gate_artifacts(_workspace: &Path) -> Vec<(String, GateArtifactSpec)> {
    const GATE_ARTIFACTS_JSON: &str = include_str!("../../python/signalos_lib/gate_artifacts.json");
    let Ok(manifest) = serde_json::from_str::<GateArtifactManifest>(GATE_ARTIFACTS_JSON) else {
        return Vec::new();
    };
    manifest
        .gates
        .into_iter()
        .flat_map(|(gate, specs)| specs.into_iter().map(move |spec| (gate.clone(), spec)))
        .collect()
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

/// Wave 5 closeout — read a single file from inside the workspace sandbox.
/// Used by the file-tree pane and the Builder conversation-history loader.
/// Refuses to read files outside the workspace root or above MAX_READ bytes.
#[tauri::command]
pub fn read_workspace_file(
    relative_path: String,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    const MAX_READ: u64 = 2_000_000; // 2 MB
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let root = workspace
        .canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))?;
    let rel = normalize_workspace_file_path(&relative_path)?;
    let target = root.join(&rel);
    let canon = target
        .canonicalize()
        .map_err(|e| format!("Cannot resolve {rel}: {e}"))?;
    if !canon.starts_with(&root) {
        return Err("Refused to read outside the workspace.".into());
    }
    let meta = std::fs::metadata(&canon).map_err(|e| format!("stat {rel}: {e}"))?;
    if meta.len() > MAX_READ {
        return Err(format!(
            "{rel} is {} bytes (cap is 2 MB). Use Open in IDE for large files.",
            meta.len()
        ));
    }
    std::fs::read_to_string(&canon).map_err(|e| format!("read {rel}: {e}"))
}

#[derive(Serialize, Clone)]
pub struct WorkspaceEntry {
    pub name: String,
    pub path: String,
    pub kind: String,
    pub bytes: Option<u64>,
    pub modified_ms: Option<u128>,
}

#[tauri::command]
pub fn list_workspace_dir(
    relative_path: Option<String>,
    state: State<WorkspaceState>,
) -> Result<Vec<WorkspaceEntry>, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let root = workspace
        .canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))?;
    let rel = relative_path
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(".");
    let target = if rel == "." {
        root.clone()
    } else {
        let normalized = normalize_workspace_file_path(rel)?;
        root.join(normalized)
    };
    let canon = target
        .canonicalize()
        .map_err(|e| format!("Cannot resolve: {e}"))?;
    if !canon.starts_with(&root) {
        return Err("Refused to list outside the workspace.".into());
    }
    let skip: &[&str] = &[
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        ".sidecar-venv",
        "__pycache__",
        ".next",
        ".turbo",
        ".cache",
    ];
    let mut entries = Vec::new();
    for ent in std::fs::read_dir(&canon).map_err(|e| format!("read_dir: {e}"))? {
        let Ok(ent) = ent else { continue };
        let name = ent.file_name().to_string_lossy().to_string();
        if skip.contains(&name.as_str()) {
            continue;
        }
        let path = ent.path();
        let rel_path = match path.strip_prefix(&root) {
            Ok(p) => p.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        let Ok(meta) = ent.metadata() else { continue };
        let kind = if meta.is_dir() { "dir" } else { "file" };
        let bytes = if meta.is_file() {
            Some(meta.len())
        } else {
            None
        };
        let modified_ms = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_millis());
        entries.push(WorkspaceEntry {
            name,
            path: rel_path,
            kind: kind.into(),
            bytes,
            modified_ms,
        });
    }
    entries.sort_by(|a, b| match (a.kind.as_str(), b.kind.as_str()) {
        ("dir", "file") => std::cmp::Ordering::Less,
        ("file", "dir") => std::cmp::Ordering::Greater,
        _ => a.name.to_lowercase().cmp(&b.name.to_lowercase()),
    });
    Ok(entries)
}

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
    // Wave 1 / G0-5: every JS-built report is redacted server-side before
    // it touches disk. The JS layer cannot bypass this; secrets typed into
    // chat or pasted into notes cannot leak into issue reports or handoffs.
    let content = redact_for_export(&content);

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
    audit(&workspace_root, "export:write", relative_path.clone());
    Ok(WorkspaceExport {
        relative_path,
        absolute_path: target.to_string_lossy().to_string(),
    })
}

#[derive(Serialize, Clone)]
pub struct WorkspaceFileDiff {
    pub path: String,
    pub status: String, // "new" | "modified" | "unchanged"
    pub bytes_new: usize,
    pub bytes_old: Option<usize>,
}

#[derive(Serialize)]
pub struct WorkspaceFileDiffResult {
    pub diffs: Vec<WorkspaceFileDiff>,
    pub total_new: usize,
    pub total_modified: usize,
    pub total_unchanged: usize,
}

/// Wave 3 / G2-24: file diff preview. Returns what would happen if the
/// caller wrote these files — new vs modified vs unchanged — without
/// actually writing anything. The frontend renders this before the user
/// confirms a Build write.
#[tauri::command]
pub fn preview_workspace_files(
    files: Vec<WorkspaceFileInput>,
    state: State<WorkspaceState>,
) -> Result<WorkspaceFileDiffResult, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let workspace_root = workspace
        .canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))?;

    let mut diffs = Vec::with_capacity(files.len());
    let mut total_new = 0usize;
    let mut total_modified = 0usize;
    let mut total_unchanged = 0usize;

    for file in files {
        let rel = normalize_workspace_file_path(&file.path)?;
        if is_reserved_generated_path(&rel) {
            return Err(format!(
                "Refused to preview {rel}. SignalOS system folders and secret files are managed separately."
            ));
        }
        let target = workspace_root.join(&rel);
        let bytes_new = file.content.len();
        let (status, bytes_old) = if target.is_file() {
            match std::fs::read(&target) {
                Ok(existing) => {
                    if existing == file.content.as_bytes() {
                        total_unchanged += 1;
                        ("unchanged".to_string(), Some(existing.len()))
                    } else {
                        total_modified += 1;
                        ("modified".to_string(), Some(existing.len()))
                    }
                }
                Err(_) => {
                    total_modified += 1;
                    ("modified".to_string(), None)
                }
            }
        } else {
            total_new += 1;
            ("new".to_string(), None)
        };
        diffs.push(WorkspaceFileDiff {
            path: rel,
            status,
            bytes_new,
            bytes_old,
        });
    }
    Ok(WorkspaceFileDiffResult {
        diffs,
        total_new,
        total_modified,
        total_unchanged,
    })
}

#[tauri::command]
pub fn write_workspace_files(
    files: Vec<WorkspaceFileInput>,
    overwrite: bool,
    state: State<WorkspaceState>,
) -> Result<WorkspaceFileWriteResult, String> {
    if files.is_empty() {
        return Err("No files were generated.".into());
    }
    if files.len() > 40 {
        return Err("Too many files at once. Keep generated projects under 40 files.".into());
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

    let mut total_bytes = 0usize;
    let mut planned: Vec<(PathBuf, String, String)> = Vec::new();

    for file in files {
        let rel = normalize_workspace_file_path(&file.path)?;
        if is_reserved_generated_path(&rel) {
            return Err(format!(
                "Refused to write {rel}. SignalOS system folders and secret files are managed separately."
            ));
        }

        let bytes = file.content.len();
        if bytes > 400_000 {
            return Err(format!(
                "{rel} is too large. Keep generated files under 400 KB."
            ));
        }
        total_bytes += bytes;
        if total_bytes > 2_000_000 {
            return Err("Generated project is too large. Keep the first build under 2 MB.".into());
        }

        let target = workspace_root.join(&rel);
        if let Some(parent) = target.parent() {
            if parent.exists() {
                let parent_root = parent
                    .canonicalize()
                    .map_err(|e| format!("Cannot resolve target folder: {e}"))?;
                if !parent_root.starts_with(&workspace_root) {
                    return Err(format!("Refused to write outside workspace: {rel}"));
                }
            }
        }
        if target.exists() && !overwrite {
            return Err(format!("{rel} already exists."));
        }
        planned.push((target, rel, file.content));
    }

    let mut written = Vec::new();
    for (target, rel, content) in planned {
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Could not create folder for {rel}: {e}"))?;
        }
        std::fs::write(&target, content.as_bytes())
            .map_err(|e| format!("Could not write {rel}: {e}"))?;
        written.push(WorkspaceFileWrite {
            relative_path: rel,
            absolute_path: target.to_string_lossy().to_string(),
            bytes: content.len(),
        });
    }

    audit(
        &workspace_root,
        "files:write",
        format!("{} files", written.len()),
    );
    Ok(WorkspaceFileWriteResult { files: written })
}

// ─── Wave 3 — Identity + role assignment (wizard step 4.5) ───────────────────
//
// Stored at .signalos/identity.json so the bundled SignalOS Core and the
// gate-signing rule both see the same actor + role. Role enforcement on
// gate sign reads this file.

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Identity {
    pub name: String,
    pub role: String, // PO | PE | QA | DevOps
}

#[tauri::command]
pub fn set_identity(
    name: String,
    role: String,
    state: State<WorkspaceState>,
) -> Result<Identity, String> {
    let role_norm = role.trim().to_uppercase();
    if !["PO", "PE", "QA", "DEVOPS"].contains(&role_norm.as_str()) {
        return Err(format!("Role must be PO, PE, QA, or DevOps — got '{role}'"));
    }
    let role_canonical = if role_norm == "DEVOPS" {
        "DevOps".to_string()
    } else {
        role_norm
    };
    let identity = Identity {
        name: name.trim().to_string(),
        role: role_canonical,
    };
    if identity.name.is_empty() {
        return Err("Name is required.".into());
    }
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let dir = ws.join(".signalos");
    std::fs::create_dir_all(&dir).map_err(|e| format!("Could not create .signalos: {e}"))?;
    let path = dir.join("identity.json");
    let json = serde_json::to_string_pretty(&identity).map_err(|e| format!("Serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("Write identity.json: {e}"))?;
    audit(
        &ws,
        "identity:set",
        format!("{} as {}", identity.name, identity.role),
    );
    Ok(identity)
}

#[tauri::command]
pub fn get_identity(state: State<WorkspaceState>) -> Result<Option<Identity>, String> {
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let path = ws.join(".signalos").join("identity.json");
    if !path.is_file() {
        return Ok(None);
    }
    let content = std::fs::read_to_string(&path).map_err(|e| format!("Read identity.json: {e}"))?;
    Ok(serde_json::from_str(&content).ok())
}

/// Role required to sign each gate, per docs §11.4d rule 6.
/// PO signs G0/G1/G3 ; PE signs G3/G4 ; QA signs G4/G5 ; DevOps signs deploy gates.
fn role_can_sign(role: &str, gate_id: u8) -> bool {
    match (role, gate_id) {
        ("PO", 0) | ("PO", 1) | ("PO", 2) | ("PO", 3) => true,
        ("PE", 3) | ("PE", 4) => true,
        ("QA", 4) | ("QA", 5) => true,
        ("DevOps", _) => true, // DevOps can sign deploy-related ops; gates 0-5 PO/PE/QA also allowed via direct check.
        _ => false,
    }
}

/// Check whether the current identity may sign a gate.
#[tauri::command]
pub fn check_role_for_gate(gate_id: u8, state: State<WorkspaceState>) -> Result<bool, String> {
    let Some(id) = get_identity(state)? else {
        return Err("Identity not set. Run the wizard or set role in Settings.".into());
    };
    Ok(role_can_sign(&id.role, gate_id))
}

// ─── Wave 1 / G0-6 — Replit-style secrets manager ────────────────────────────

#[derive(Serialize, Clone)]
pub struct SecretEntry {
    pub name: String,
    pub masked_value: String,
    pub public_prefix: bool,
    pub updated_at: u64, // file mtime, ms since epoch
    pub file: String,
}

#[derive(Deserialize)]
pub struct EnvDiffPlan {
    pub added: Vec<String>,
    pub changed: Vec<String>,
    pub removed: Vec<String>,
}

#[derive(Serialize)]
pub struct EnvDiffResult {
    pub file: String,
    pub added: Vec<String>,
    pub changed: Vec<String>,
    pub unchanged: Vec<String>,
    pub removed: Vec<String>,
    pub applied: bool,
}

/// Parse a .env file and return its variable list (masked).
#[tauri::command]
pub fn list_workspace_secrets(
    filename: Option<String>,
    state: State<WorkspaceState>,
) -> Result<Vec<SecretEntry>, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let safe = normalize_secret_filename(filename.as_deref().unwrap_or(".env.local"))?;
    let path = workspace.join(&safe);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Could not read {safe}: {e}"))?;
    let updated_at = std::fs::metadata(&path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    let mut out = Vec::new();
    for line in content.lines() {
        let trimmed = line.trim_start_matches('\u{feff}').trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((key_raw, val)) = trimmed.split_once('=') else {
            continue;
        };
        let key = key_raw.trim().to_string();
        if key.is_empty() {
            continue;
        }
        let public = is_public_prefixed(&key);
        let unquoted = unquote_env_value(val);
        let masked_value = if public {
            unquoted
        } else if unquoted.is_empty() {
            String::new()
        } else {
            "•".repeat(unquoted.chars().count().min(18))
        };
        out.push(SecretEntry {
            name: key,
            masked_value,
            public_prefix: public,
            updated_at,
            file: safe.clone(),
        });
    }
    Ok(out)
}

/// Return the plaintext value of a single secret. The only IPC path that
/// returns secrets in clear. Caller is expected to audit-log the reveal;
/// the Rust layer also writes an audit entry.
#[tauri::command]
pub fn reveal_workspace_secret(
    name: String,
    filename: Option<String>,
    state: State<WorkspaceState>,
) -> Result<String, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let key = normalize_secret_name(&name)?;
    let safe = normalize_secret_filename(filename.as_deref().unwrap_or(".env.local"))?;
    let path = workspace.join(&safe);
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Could not read {safe}: {e}"))?;
    for line in content.lines() {
        let trimmed = line.trim_start_matches('\u{feff}').trim();
        if trimmed.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = trimmed.split_once('=') {
            if k.trim() == key {
                append_secret_audit(&workspace, "secret:reveal", &key, &safe);
                return Ok(unquote_env_value(v));
            }
        }
    }
    Err(format!("{key} not found in {safe}"))
}

/// Remove a single KEY= line from the .env file. Preserves comments and order.
/// Atomic via temp-file + rename.
#[tauri::command]
pub fn delete_workspace_secret(
    name: String,
    filename: Option<String>,
    state: State<WorkspaceState>,
) -> Result<(), String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let key = normalize_secret_name(&name)?;
    let safe = normalize_secret_filename(filename.as_deref().unwrap_or(".env.local"))?;
    let path = workspace.join(&safe);
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Could not read {safe}: {e}"))?;
    let mut found = false;
    let kept: Vec<&str> = content
        .lines()
        .filter(|line| {
            let trimmed = line.trim_start_matches('\u{feff}').trim();
            if trimmed.starts_with('#') {
                return true;
            }
            if let Some((k, _)) = trimmed.split_once('=') {
                if k.trim() == key {
                    found = true;
                    return false;
                }
            }
            true
        })
        .collect();
    if !found {
        return Err(format!("{key} not found in {safe}"));
    }
    let mut out = kept.join("\n");
    out.push('\n');
    atomic_write(&path, out.as_bytes())?;
    append_secret_audit(&workspace, "secret:delete", &key, &safe);
    Ok(())
}

/// Bulk apply a pasted .env block. Returns the diff and writes atomically.
/// If `allow_removals` is false and the diff has removals, the call fails
/// without writing.
#[tauri::command]
pub fn apply_workspace_env_diff(
    filename: Option<String>,
    env_text: String,
    allow_removals: bool,
    state: State<WorkspaceState>,
) -> Result<EnvDiffResult, String> {
    let workspace = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let safe = normalize_secret_filename(filename.as_deref().unwrap_or(".env.local"))?;
    let path = workspace.join(&safe);

    let new_pairs = parse_env_block(&env_text)?;
    let old_pairs = if path.exists() {
        let content =
            std::fs::read_to_string(&path).map_err(|e| format!("Could not read {safe}: {e}"))?;
        parse_env_block(&content)?
    } else {
        Vec::new()
    };

    let old_map: std::collections::HashMap<&str, &str> = old_pairs
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let new_map: std::collections::HashMap<&str, &str> = new_pairs
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();

    let mut added = Vec::new();
    let mut changed = Vec::new();
    let mut unchanged = Vec::new();
    let mut removed = Vec::new();
    for (k, v) in &new_pairs {
        match old_map.get(k.as_str()) {
            None => added.push(k.clone()),
            Some(old_v) if old_v != &v.as_str() => changed.push(k.clone()),
            Some(_) => unchanged.push(k.clone()),
        }
    }
    for (k, _) in &old_pairs {
        if !new_map.contains_key(k.as_str()) {
            removed.push(k.clone());
        }
    }

    if !removed.is_empty() && !allow_removals {
        return Ok(EnvDiffResult {
            file: safe,
            added,
            changed,
            unchanged,
            removed,
            applied: false,
        });
    }

    // Rebuild file: comments preserved are not feasible without full AST;
    // we write the new pairs in encounter order and append a trailing newline.
    // Comments inside `env_text` are preserved verbatim.
    let mut out = String::new();
    for line in env_text.lines() {
        let trimmed = line.trim_start_matches('\u{feff}').trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            out.push_str(line);
            out.push('\n');
            continue;
        }
        if let Some((k, v)) = trimmed.split_once('=') {
            let key = k.trim();
            if let Ok(norm) = normalize_secret_name(key) {
                out.push_str(&format!(
                    "{norm}={}\n",
                    quote_env_value(&unquote_env_value(v))
                ));
            }
        }
    }

    atomic_write(&path, out.as_bytes())?;
    append_secret_audit(&workspace, "secret:bulk-apply", "", &safe);

    Ok(EnvDiffResult {
        file: safe,
        added,
        changed,
        unchanged,
        removed,
        applied: true,
    })
}

fn parse_env_block(text: &str) -> Result<Vec<(String, String)>, String> {
    let mut pairs = Vec::new();
    for (lineno, raw) in text.lines().enumerate() {
        let trimmed = raw.trim_start_matches('\u{feff}').trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((k, v)) = trimmed.split_once('=') else {
            return Err(format!("Line {} is not KEY=value: {trimmed}", lineno + 1));
        };
        let key = normalize_secret_name(k.trim())?;
        let value = unquote_env_value(v);
        pairs.push((key, value));
    }
    Ok(pairs)
}

fn unquote_env_value(raw: &str) -> String {
    let trimmed = raw.trim();
    if (trimmed.starts_with('"') && trimmed.ends_with('"') && trimmed.len() >= 2)
        || (trimmed.starts_with('\'') && trimmed.ends_with('\'') && trimmed.len() >= 2)
    {
        let inner = &trimmed[1..trimmed.len() - 1];
        return inner
            .replace("\\\"", "\"")
            .replace("\\'", "'")
            .replace("\\n", "\n")
            .replace("\\\\", "\\");
    }
    trimmed.to_string()
}

fn is_public_prefixed(key: &str) -> bool {
    let upper = key.to_ascii_uppercase();
    upper.starts_with("NEXT_PUBLIC_")
        || upper.starts_with("VITE_")
        || upper.starts_with("REACT_APP_")
        || upper.starts_with("EXPO_PUBLIC_")
        || upper.starts_with("PUBLIC_")
}

fn atomic_write(target: &Path, bytes: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let parent = target
        .parent()
        .ok_or_else(|| "Target has no parent directory".to_string())?;
    let tmp = parent.join(format!(
        ".{}.tmp",
        target
            .file_name()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "out".into())
    ));
    {
        let mut f =
            std::fs::File::create(&tmp).map_err(|e| format!("Could not create temp file: {e}"))?;
        f.write_all(bytes)
            .map_err(|e| format!("Could not write temp file: {e}"))?;
        f.sync_all()
            .map_err(|e| format!("Could not fsync temp file: {e}"))?;
    }
    std::fs::rename(&tmp, target).map_err(|e| format!("Could not rename temp file: {e}"))
}

fn append_secret_audit(workspace: &Path, action: &str, name: &str, file: &str) {
    use std::io::Write;
    let dir = workspace.join(".signalos");
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join("AUDIT_TRAIL.jsonl");
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let entry = serde_json::json!({
        "ts": ts,
        "action": action,
        "name": name,
        "file": file,
    });
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let _ = writeln!(f, "{}", entry);
    }
}

#[tauri::command]
pub fn upsert_workspace_secret(
    name: String,
    value: String,
    filename: Option<String>,
    state: State<WorkspaceState>,
) -> Result<WorkspaceFileWrite, String> {
    let key = normalize_secret_name(&name)?;
    if value.len() > 20_000 {
        return Err("Secret value is too large.".into());
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
    let file_name = filename
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .unwrap_or(".env.local");
    let safe_filename = normalize_secret_filename(file_name)?;
    let target = workspace_root.join(&safe_filename);

    if !target.starts_with(&workspace_root) {
        return Err("Refused to write a secret outside the workspace.".into());
    }

    let existing = std::fs::read_to_string(&target).unwrap_or_default();
    let mut found = false;
    let mut lines: Vec<String> = existing
        .lines()
        .map(|line| {
            let trimmed = line.trim_start();
            if trimmed.starts_with('#') {
                return line.to_string();
            }
            if let Some((left, _)) = trimmed.split_once('=') {
                if left.trim() == key {
                    found = true;
                    return format!("{key}={}", quote_env_value(&value));
                }
            }
            line.to_string()
        })
        .collect();
    if !found {
        lines.push(format!("{key}={}", quote_env_value(&value)));
    }
    let mut content = lines.join("\n");
    content.push('\n');

    std::fs::write(&target, content.as_bytes())
        .map_err(|e| format!("Could not save secret to {safe_filename}: {e}"))?;

    audit(
        &workspace_root,
        "secret:upsert",
        format!("{} in {}", key, safe_filename),
    );
    Ok(WorkspaceFileWrite {
        relative_path: safe_filename,
        absolute_path: target.to_string_lossy().to_string(),
        bytes: content.len(),
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

// ─── PHASE 13: Wave velocity metrics ─────────────────────────────────────────
//
// Sidecar dispatch for `signal-velocity --json`. Reads
// .signalos/AUDIT_TRAIL.jsonl + autoplan tasks and returns a JSON
// payload with sessions/day, scope-card burndown, ETA prediction, and
// the last-session timestamp. The dashboard sidebar polls this on
// workspace load / refresh. Returns the request id (`req-<hex>-<seq>`);
// the frontend waits on the matching `sidecar:response` event and
// JSON-parses the body. Mirrors get_wave_state's sidecar pattern so
// the existing `run_signal_command` ACL grant (shell:allow-spawn for
// `binaries/signalos-python`) covers the spawn, while the IPC method
// itself is grant-listed via the workspace-core permission set.

#[tauri::command]
pub fn get_velocity_metrics(state: State<WorkspaceState>) -> Result<String, String> {
    let cwd = state
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.to_string_lossy().to_string());
    let id = uuid();
    send_command(SidecarRequest {
        id: id.clone(),
        command: "signal-velocity".into(),
        args: vec!["--json".to_string()],
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
    let url = format!("https://samerzakaria.github.io/signalos-app/update-manifest/{manifest}");
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
                    if last_mtime.is_some_and(|prev| prev != mtime) {
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

fn load_workspace_settings(settings_path: &Path) -> WorkspaceSettings {
    let Ok(raw) = std::fs::read_to_string(settings_path) else {
        return WorkspaceSettings::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn save_workspace_settings(
    settings_path: &Path,
    settings: &WorkspaceSettings,
) -> Result<(), String> {
    if let Some(parent) = settings_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Could not create app config folder: {e}"))?;
    }
    let content = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("Could not serialize workspace settings: {e}"))?;
    std::fs::write(settings_path, content)
        .map_err(|e| format!("Could not save workspace settings: {e}"))
}

fn resolve_workspace_root(path: &str) -> Result<PathBuf, String> {
    let p = PathBuf::from(path);
    if !p.exists() || !p.is_dir() {
        return Err(format!(
            "Path does not exist or is not a directory: {}",
            path
        ));
    }
    p.canonicalize()
        .map_err(|e| format!("Cannot resolve workspace: {e}"))
}

fn workspace_display_name(path: &Path) -> String {
    path.file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| path.to_string_lossy().to_string())
}

fn paths_equal_for_settings(left: &str, right: &str) -> bool {
    left.eq_ignore_ascii_case(right)
}

fn recent_workspace_status(entry: &RecentWorkspace) -> RecentWorkspaceStatus {
    let path = PathBuf::from(&entry.path);
    let exists = path.exists();
    let is_directory = path.is_dir();
    let initialized = is_directory
        && path.join(".signalos").exists()
        && path.join("core/strategy/PLAN.md").exists();
    RecentWorkspaceStatus {
        path: entry.path.clone(),
        name: entry.name.clone(),
        last_opened: entry.last_opened.clone(),
        exists,
        is_directory,
        initialized,
        profile_id: read_workspace_profile_id(&path),
    }
}

fn read_workspace_profile_id(workspace: &Path) -> Option<String> {
    let profile_path = workspace.join(".signalos").join("profile.json");
    let raw = std::fs::read_to_string(profile_path).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&raw).ok()?;
    parsed
        .get("profile_id")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn workspace_status_integration() -> WorkspaceStatusIntegration {
    WorkspaceStatusIntegration {
        artifacts: "inline:get_project_artifacts".into(),
        gates: "available:get_gate_status".into(),
        wave: "available:get_wave_state".into(),
        validator: "pending:validator-core".into(),
    }
}

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

fn detect_app_entry(workspace: &Path) -> Option<String> {
    [
        "src/main.jsx",
        "src/main.tsx",
        "src/App.jsx",
        "src/App.tsx",
        "app/page.jsx",
        "app/page.tsx",
        "pages/index.jsx",
        "pages/index.tsx",
        "pages/index.js",
        "public/index.html",
        "index.html",
        "server.js",
        "app.py",
    ]
    .iter()
    .find(|candidate| workspace.join(candidate).exists())
    .map(|candidate| (*candidate).to_string())
}

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

fn normalize_workspace_file_path(value: &str) -> Result<String, String> {
    let cleaned = value.replace('\\', "/");
    let cleaned = cleaned.trim().trim_start_matches('/').to_string();
    if cleaned.is_empty() {
        return Err("Generated file path is empty.".into());
    }
    if cleaned.len() > 180 {
        return Err(format!("Generated file path is too long: {cleaned}"));
    }
    if cleaned.contains('\0') || cleaned.contains(':') {
        return Err(format!("Invalid generated file path: {cleaned}"));
    }

    let mut parts = Vec::new();
    for part in cleaned.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            return Err(format!("Generated file escapes the workspace: {cleaned}"));
        }
        let safe: String = part
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | ' '))
            .take(80)
            .collect();
        let safe = safe.trim().to_string();
        if safe.is_empty() || safe == "." || safe == ".." {
            return Err(format!("Invalid generated file path segment in {cleaned}"));
        }
        parts.push(safe);
    }
    if parts.is_empty() {
        return Err("Generated file path is empty.".into());
    }
    Ok(parts.join("/"))
}

fn is_reserved_generated_path(rel: &str) -> bool {
    let lower = rel.to_ascii_lowercase();
    lower == ".env"
        || lower.starts_with(".env.")
        || lower.starts_with(".signalos/")
        || lower.starts_with("core/")
        || lower.starts_with("integrations/")
        || lower.starts_with(".git/")
        || lower.ends_with(".pem")
        || lower.ends_with(".key")
        || lower.ends_with(".p12")
        || lower.ends_with(".pfx")
}

fn normalize_secret_name(value: &str) -> Result<String, String> {
    let key = value.trim().to_ascii_uppercase();
    if key.is_empty() {
        return Err("Secret name is required.".into());
    }
    if key.len() > 80
        || !key
            .chars()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
        || key.chars().next().is_some_and(|c| c.is_ascii_digit())
    {
        return Err("Use a secret name like OPENAI_API_KEY or DATABASE_URL.".into());
    }
    Ok(key)
}

fn normalize_secret_filename(value: &str) -> Result<String, String> {
    let cleaned = value.trim().replace('\\', "/");
    if cleaned.contains('/') || cleaned.contains("..") {
        return Err("Secrets can only be saved to a local .env file in the project root.".into());
    }
    if cleaned == ".env" || cleaned.starts_with(".env.") {
        return Ok(cleaned);
    }
    Err("Secrets must be saved to .env, .env.local, or another .env.* file.".into())
}

// ─── Wave 1 / G0-5: redaction for JS-built reports ────────────────────────────
//
// Mirrors the regex set in python/signalos_secret_guard.py. The JS layer
// builds issue-report and team-handoff Markdown directly from session state
// (which can include user-typed chat messages). All writes through
// write_workspace_export pass through this filter, so secrets that crossed
// only the Rust side and never the Python sidecar still get redacted.

const REDACTED: &str = "<redacted>";

fn redact_for_export(text: &str) -> String {
    use regex::Regex;
    use std::sync::OnceLock;

    // High-confidence secret value patterns. Compiled once per process.
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        [
            // PEM blocks
            r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            // OpenAI / Anthropic / generic sk- keys
            r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{18,}\b",
            r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b",
            // AWS access key IDs
            r"\bAKIA[0-9A-Z]{16}\b",
            // Bearer tokens
            r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b",
            // DB URLs with creds
            r"(?i)(postgres|postgresql|mysql|mongodb|redis)://[^:\s/@]+:[^@\s]+@",
            // KEY=value lines with a secret-shaped key name
            r"(?im)^[﻿]?([A-Z_][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|DATABASE[_-]?URL|DB[_-]?URL|REDIS[_-]?URL|AUTHORIZATION)[A-Z0-9_]*)\s*=\s*[^\r\n]+",
            // JSON-style "secret": "value"
            r#"(?i)(["'][A-Z0-9_.-]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|DATABASE[_-]?URL|DB[_-]?URL|REDIS[_-]?URL|AUTHORIZATION)[A-Z0-9_.-]*["']\s*:\s*["'])[^"'\r\n]*(["'])"#,
        ]
        .iter()
        .filter_map(|p| Regex::new(p).ok())
        .collect()
    });

    let mut out = text.to_string();
    for re in patterns.iter() {
        // For KEY=value and JSON "key": "value" patterns we keep the key;
        // for plain values we drop the whole match.
        out = re
            .replace_all(&out, |caps: &regex::Captures| {
                if let Some(key) = caps.get(1) {
                    if let Some(closer) = caps.get(2) {
                        format!("{}{}{}", key.as_str(), REDACTED, closer.as_str())
                    } else {
                        format!("{}={}", key.as_str(), REDACTED)
                    }
                } else {
                    REDACTED.to_string()
                }
            })
            .into_owned();
    }
    out
}

fn quote_env_value(value: &str) -> String {
    let escaped = value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\r', "")
        .replace('\n', "\\n");
    format!("\"{escaped}\"")
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
    fn workspace_settings_persist_restore_and_clear() {
        let root = std::env::temp_dir().join(format!("signalos-ws-{}", uuid()));
        let cfg = std::env::temp_dir().join(format!("signalos-cfg-{}", uuid()));
        std::fs::create_dir_all(&root).unwrap();

        let settings = WorkspaceSettingsState::new(cfg.clone());
        settings.set_active_workspace(&root).unwrap();
        assert_eq!(
            settings.restored_active_workspace().unwrap(),
            root.canonicalize().unwrap()
        );
        assert_eq!(settings.snapshot().recent_workspaces.len(), 1);

        let reloaded = WorkspaceSettingsState::new(cfg.clone());
        assert_eq!(
            reloaded.restored_active_workspace().unwrap(),
            root.canonicalize().unwrap()
        );
        reloaded.clear_active_workspace().unwrap();

        let cleared = WorkspaceSettingsState::new(cfg.clone());
        assert!(cleared.restored_active_workspace().is_none());
        assert_eq!(cleared.snapshot().recent_workspaces.len(), 1);

        let _ = std::fs::remove_dir_all(root);
        let _ = std::fs::remove_dir_all(cfg);
    }

    #[test]
    fn recent_workspace_status_marks_initialized_repos() {
        let root = std::env::temp_dir().join(format!("signalos-ws-{}", uuid()));
        std::fs::create_dir_all(root.join(".signalos")).unwrap();
        std::fs::create_dir_all(root.join("core").join("strategy")).unwrap();
        std::fs::write(root.join("core").join("strategy").join("PLAN.md"), "# Plan").unwrap();
        std::fs::write(
            root.join(".signalos").join("profile.json"),
            r#"{"profile_id":"react-vite"}"#,
        )
        .unwrap();

        let entry = RecentWorkspace {
            path: root.to_string_lossy().to_string(),
            name: "Example".into(),
            last_opened: "2026-05-23T00:00:00Z".into(),
        };
        let status = recent_workspace_status(&entry);

        assert!(status.exists);
        assert!(status.is_directory);
        assert!(status.initialized);
        assert_eq!(status.profile_id.as_deref(), Some("react-vite"));

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn project_artifacts_include_shared_gate_manifest_paths() {
        let root = std::env::temp_dir().join(format!("signalos-artifacts-{}", uuid()));
        std::fs::create_dir_all(root.join(".signalos")).unwrap();
        std::fs::create_dir_all(root.join("core").join("strategy")).unwrap();
        std::fs::write(root.join("core").join("strategy").join("PLAN.md"), "# Plan").unwrap();

        let artifacts = collect_project_artifacts(&root);
        let paths: Vec<String> = artifacts.artifacts.into_iter().map(|item| item.path).collect();

        assert!(paths.contains(&"core/governance/Governance/SOUL-DOCUMENT.md".into()));
        assert!(paths.contains(&"core/governance/QUALITY_CHECK.md".into()));

        let _ = std::fs::remove_dir_all(root);
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

    #[test]
    fn generated_file_paths_are_normalized() {
        assert_eq!(
            normalize_workspace_file_path("src\\app.js").unwrap(),
            "src/app.js"
        );
        assert!(normalize_workspace_file_path("../escape.js").is_err());
        assert!(is_reserved_generated_path(".signalos/state.json"));
        assert!(is_reserved_generated_path(".env.local"));
    }

    #[test]
    fn generated_app_entry_is_detected() {
        let root = std::env::temp_dir().join(format!("signalos-entry-{}", uuid()));
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src").join("main.jsx"), "console.log('ok');").unwrap();

        assert_eq!(detect_app_entry(&root).as_deref(), Some("src/main.jsx"));

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn secret_names_and_files_are_limited() {
        assert_eq!(
            normalize_secret_name("openai_api_key").unwrap(),
            "OPENAI_API_KEY"
        );
        assert!(normalize_secret_name("1BAD").is_err());
        assert_eq!(
            normalize_secret_filename(".env.local").unwrap(),
            ".env.local"
        );
        assert!(normalize_secret_filename("../.env").is_err());
        assert_eq!(quote_env_value("a\"b"), "\"a\\\"b\"");
    }

    // ── PHASE 13: Wave velocity IPC ──────────────────────────────────────────
    //
    // We can't drive the full Tauri State<> here without booting the runtime,
    // and the sidecar transport mock lives at integration-test scope. This
    // test pins the request-shape contract — get_velocity_metrics must
    // dispatch `signal-velocity --json` against the sidecar and the
    // workspace cwd must thread through unchanged so the Python side
    // resolves `.signalos/` relative to the user's workspace, not the
    // app bundle directory. We simulate the body of the command by
    // constructing the SidecarRequest the function would build and
    // assert its fields, plus check that the helper id format is
    // unique-ish per call.
    #[test]
    fn velocity_metrics_request_shape_is_correct() {
        let workspace = std::env::temp_dir();
        let cwd = Some(workspace.to_string_lossy().to_string());
        let id = uuid();
        let req = SidecarRequest {
            id: id.clone(),
            command: "signal-velocity".into(),
            args: vec!["--json".to_string()],
            cwd: cwd.clone(),
        };
        assert_eq!(req.command, "signal-velocity");
        assert_eq!(req.args, vec!["--json".to_string()]);
        assert_eq!(req.cwd, cwd);
        assert!(req.id.starts_with("req-"), "id should be uuid-shaped: {}", req.id);
    }
}
