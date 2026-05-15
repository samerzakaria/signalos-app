/// test_automation.rs — Test Automation enforcement (Wave 5 / G4)
///
/// Implements four runtime rules from docs/test-automation/:
///   9.  Test-first for beliefs       — every Belief gate (G1) sign must
///                                       link ≥1 test file or test plan entry.
///   10. Gate compliance is binary    — build_precheck (already in enforcement.rs)
///                                       refuses to advance until all layer gates pass.
///   11. Zero manual regression       — every manually-found defect becomes a
///                                       test-debt entry in .signalos/test-debt.jsonl.
///   12. Mutation threshold           — Builder writes are gated on a mutation
///                                       score ≥ 95% for business-logic files.
///
/// The frontend exposes a Test Debt drawer that reads this store; the Build
/// flow consults the mutation threshold before write.
use serde::{Deserialize, Serialize};
use std::path::Path;
use tauri::State;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct TestDebtEntry {
    pub ts: String,
    pub kind: String, // "manual-defect" | "missing-test" | "mutation-low"
    pub area: String, // file glob / module name
    pub title: String,
    pub detail: String,
    pub resolved: bool,
}

#[derive(Serialize)]
pub struct TestDebtSummary {
    pub entries: Vec<TestDebtEntry>,
    pub open_count: u32,
    pub resolved_count: u32,
}

/// Read all test-debt entries from `.signalos/test-debt.jsonl`.
/// Most recent first.
#[tauri::command]
pub fn list_test_debt(state: State<crate::ipc::WorkspaceState>) -> Result<TestDebtSummary, String> {
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let path = ws.join(".signalos").join("test-debt.jsonl");
    let mut entries: Vec<TestDebtEntry> = Vec::new();
    if let Ok(content) = std::fs::read_to_string(&path) {
        for line in content.lines() {
            if let Ok(e) = serde_json::from_str::<TestDebtEntry>(line) {
                entries.push(e);
            }
        }
    }
    let open_count = entries.iter().filter(|e| !e.resolved).count() as u32;
    let resolved_count = entries.iter().filter(|e| e.resolved).count() as u32;
    entries.reverse();
    Ok(TestDebtSummary {
        entries,
        open_count,
        resolved_count,
    })
}

#[derive(Deserialize)]
pub struct TestDebtNew {
    pub kind: String,
    pub area: String,
    pub title: String,
    pub detail: String,
}

/// Append a test-debt entry. Used when a manual defect is logged or when
/// the Builder's mutation score is below threshold.
#[tauri::command]
pub fn add_test_debt(
    entry: TestDebtNew,
    state: State<crate::ipc::WorkspaceState>,
) -> Result<(), String> {
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let dir = ws.join(".signalos");
    std::fs::create_dir_all(&dir).map_err(|e| format!("Could not create .signalos: {e}"))?;
    let path = dir.join("test-debt.jsonl");
    let line = serde_json::to_string(&TestDebtEntry {
        ts: crate::ipc::ipc_chrono_iso8601(),
        kind: entry.kind,
        area: entry.area,
        title: entry.title,
        detail: entry.detail,
        resolved: false,
    })
    .map_err(|e| format!("Could not serialize: {e}"))?;
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("Could not open test-debt.jsonl: {e}"))?;
    writeln!(f, "{line}").map_err(|e| format!("Could not append: {e}"))?;
    f.sync_data().ok();
    Ok(())
}

#[tauri::command]
pub fn resolve_test_debt(
    title: String,
    state: State<crate::ipc::WorkspaceState>,
) -> Result<bool, String> {
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let path = ws.join(".signalos").join("test-debt.jsonl");
    let Ok(content) = std::fs::read_to_string(&path) else {
        return Ok(false);
    };
    let mut updated = String::new();
    let mut hit = false;
    for line in content.lines() {
        if let Ok(mut e) = serde_json::from_str::<TestDebtEntry>(line) {
            if !e.resolved && e.title == title {
                e.resolved = true;
                hit = true;
            }
            if let Ok(s) = serde_json::to_string(&e) {
                updated.push_str(&s);
                updated.push('\n');
            }
        }
    }
    if hit {
        let tmp = path.with_extension("jsonl.tmp");
        std::fs::write(&tmp, updated).map_err(|e| format!("Could not write tmp: {e}"))?;
        std::fs::rename(&tmp, &path).map_err(|e| format!("Could not rename: {e}"))?;
    }
    Ok(hit)
}

#[derive(Deserialize)]
pub struct MutationScoreArgs {
    pub score: f64, // 0.0..1.0
    pub area: String,
}

#[derive(Serialize)]
pub struct MutationGateResult {
    pub allowed: bool,
    pub threshold: f64,
    pub score: f64,
    pub area: String,
    pub reason: Option<String>,
}

/// Test-automation rule 12: mutation threshold. Returns a gate decision so
/// the Builder can refuse to write business-logic files that fail the
/// threshold (or proceed with an audited override).
#[tauri::command]
pub fn check_mutation_threshold(args: MutationScoreArgs) -> MutationGateResult {
    let threshold = 0.95;
    let allowed = args.score >= threshold;
    MutationGateResult {
        allowed,
        threshold,
        score: args.score,
        area: args.area.clone(),
        reason: if allowed {
            None
        } else {
            Some(format!(
                "Mutation score {:.0}% < {:.0}% threshold for {}.",
                args.score * 100.0,
                threshold * 100.0,
                args.area
            ))
        },
    }
}

/// Test-first rule (9): when signing G1 Belief, the caller must supply
/// at least one test reference (file path or plan-entry id). Empty list = refuse.
#[derive(Deserialize)]
pub struct TestFirstArgs {
    pub test_refs: Vec<String>,
}

#[derive(Serialize)]
pub struct TestFirstResult {
    pub allowed: bool,
    pub reason: Option<String>,
}

#[tauri::command]
pub fn check_test_first(args: TestFirstArgs) -> TestFirstResult {
    let refs: Vec<String> = args
        .test_refs
        .into_iter()
        .filter(|s| !s.trim().is_empty())
        .collect();
    if refs.is_empty() {
        TestFirstResult {
            allowed: false,
            reason: Some("Belief gate (G1) sign requires at least one test file or test plan entry. Add a reference and try again.".into()),
        }
    } else {
        TestFirstResult {
            allowed: true,
            reason: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mutation_threshold_passes_at_or_above_95() {
        let result = check_mutation_threshold(MutationScoreArgs {
            score: 0.95,
            area: "src/business".into(),
        });
        assert!(result.allowed);
        assert!(result.reason.is_none());
        assert_eq!(result.threshold, 0.95);
    }

    #[test]
    fn mutation_threshold_refuses_below_95() {
        let result = check_mutation_threshold(MutationScoreArgs {
            score: 0.80,
            area: "src/business".into(),
        });
        assert!(!result.allowed);
        let reason = result.reason.expect("reason should be set when blocked");
        assert!(
            reason.contains("80%") || reason.contains("80"),
            "reason: {reason}"
        );
    }

    #[test]
    fn mutation_threshold_boundary_at_threshold() {
        let result = check_mutation_threshold(MutationScoreArgs {
            score: 0.9499,
            area: "x".into(),
        });
        assert!(!result.allowed);
    }

    #[test]
    fn test_first_refuses_empty_refs() {
        let result = check_test_first(TestFirstArgs { test_refs: vec![] });
        assert!(!result.allowed);
        assert!(result.reason.unwrap().contains("test"));
    }

    #[test]
    fn test_first_refuses_only_whitespace_refs() {
        let result = check_test_first(TestFirstArgs {
            test_refs: vec!["".into(), "   ".into()],
        });
        assert!(!result.allowed);
    }

    #[test]
    fn test_first_accepts_any_nonempty_ref() {
        let result = check_test_first(TestFirstArgs {
            test_refs: vec!["tests/auth.spec.ts".into()],
        });
        assert!(result.allowed);
        assert!(result.reason.is_none());
    }

    #[test]
    fn ensure_test_debt_creates_file() {
        // Windows refuses ':' in path segments, so build a filesystem-safe slug.
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        let tmp = std::env::temp_dir().join(format!("signalos-td-{ts}"));
        let _ = std::fs::create_dir_all(&tmp);
        let path = tmp.join(".signalos").join("test-debt.jsonl");
        let _ = std::fs::remove_file(&path);
        ensure_test_debt(&tmp).expect("should create");
        assert!(path.is_file(), "expected {} to exist", path.display());
        let _ = std::fs::remove_dir_all(&tmp);
    }
}

/// Initialize the .signalos/test-debt.jsonl file (touch only) so the gate
/// runners on disk can append even before the UI has been opened.
pub fn ensure_test_debt(workspace: &Path) -> std::io::Result<()> {
    let dir = workspace.join(".signalos");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("test-debt.jsonl");
    if !path.exists() {
        std::fs::File::create(&path)?;
    }
    Ok(())
}

/// Look up the most recent mutation score on disk. CI writes this file when
/// it runs `cargo mutants` / stryker / mutmut. The Builder reads it before
/// writing files: a stale or missing score means we cannot prove rule 12,
/// which the user must explicitly override to proceed.
#[derive(Serialize)]
pub struct MutationScoreFile {
    pub score: Option<f64>,
    pub area: String,
    pub measured_at: Option<String>,
    pub source: Option<String>,
    pub present: bool,
}

#[tauri::command]
pub fn read_mutation_score(
    state: State<crate::ipc::WorkspaceState>,
) -> Result<MutationScoreFile, String> {
    let ws = state
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let path = ws.join(".signalos").join("mutation-score.json");
    if !path.is_file() {
        return Ok(MutationScoreFile {
            score: None,
            area: "(no score on file)".into(),
            measured_at: None,
            source: None,
            present: false,
        });
    }
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Read mutation-score.json: {e}"))?;
    let json: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| format!("Parse mutation-score.json: {e}"))?;
    Ok(MutationScoreFile {
        score: json.get("score").and_then(|v| v.as_f64()),
        area: json
            .get("area")
            .and_then(|v| v.as_str())
            .unwrap_or("workspace")
            .to_string(),
        measured_at: json
            .get("measured_at")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        source: json
            .get("source")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        present: true,
    })
}
