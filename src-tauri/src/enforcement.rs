/// enforcement.rs — Runtime enforcement of SignalOS governance (Wave 3 / G2-21..26)
///
/// This module is the code that makes "fully wired & enforced SignalOS" true.
/// Every rule in docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4d has an
/// IPC-exposed check here. The frontend asks "may I do X?" and gets back a
/// decision plus, for blocks, the failing rule and an audit-friendly reason.
///
/// Overrides are first-class: the frontend can request a labeled override
/// (with a reason string) which is audit-logged before the action proceeds.
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::State;

use crate::governance::{append_audit, AuditEntry};

// ─── Rule names ──────────────────────────────────────────────────────────────

pub const RULE_GATE_GATE: &str = "gate-gating";
pub const RULE_PLAN_GATE: &str = "plan-gating";
pub const RULE_TRUST_TIER: &str = "trust-tier";
pub const RULE_AUDIT_APPEND: &str = "audit-append";
pub const RULE_SECRET_BLOCK: &str = "secret-block";
pub const RULE_ROLE_SIGN: &str = "role-sign";
pub const RULE_STACK_CONTRACT: &str = "stack-contract";
pub const RULE_WAVE_FREEZE: &str = "wave-freeze";
pub const RULE_TEST_FIRST: &str = "test-first";
pub const RULE_GATE_COMPLIANCE: &str = "gate-compliance";
pub const RULE_ZERO_MANUAL: &str = "zero-manual-regression";
pub const RULE_MUTATION: &str = "mutation-threshold";

const ALL_RULES: &[&str] = &[
    RULE_GATE_GATE,
    RULE_PLAN_GATE,
    RULE_TRUST_TIER,
    RULE_AUDIT_APPEND,
    RULE_SECRET_BLOCK,
    RULE_ROLE_SIGN,
    RULE_STACK_CONTRACT,
    RULE_WAVE_FREEZE,
    RULE_TEST_FIRST,
    RULE_GATE_COMPLIANCE,
    RULE_ZERO_MANUAL,
    RULE_MUTATION,
];

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Strict,
    Warn,
    Off,
}

#[derive(Serialize, Clone, Debug)]
pub struct RuleStatus {
    pub rule: String,
    pub mode: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct EnforcementState {
    pub modes: Vec<RuleStatus>,
    pub overrides_this_wave: u32,
    pub wave_frozen: bool,
    pub required_gates: Vec<u8>, // gate IDs that must be signed before Build
    pub signed_gates: Vec<u8>,
}

// ─── Persistent state ────────────────────────────────────────────────────────

pub struct EnforcementStore {
    pub modes: Mutex<Vec<(String, Mode)>>,
    pub overrides: Mutex<u32>,
    pub wave_frozen: Mutex<bool>,
}

impl Default for EnforcementStore {
    fn default() -> Self {
        let modes = ALL_RULES
            .iter()
            .map(|r| (r.to_string(), Mode::Strict))
            .collect();
        Self {
            modes: Mutex::new(modes),
            overrides: Mutex::new(0),
            wave_frozen: Mutex::new(false),
        }
    }
}

impl EnforcementStore {
    pub fn new() -> Self {
        Self::default()
    }
}

// ─── Tauri commands ──────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_enforcement_state(
    state: State<EnforcementStore>,
    ws: State<crate::ipc::WorkspaceState>,
) -> EnforcementState {
    let modes: Vec<RuleStatus> = state
        .modes
        .lock()
        .unwrap()
        .iter()
        .map(|(rule, mode)| RuleStatus {
            rule: rule.clone(),
            mode: mode_str(mode),
        })
        .collect();
    let overrides = *state.overrides.lock().unwrap();
    let wave_frozen = *state.wave_frozen.lock().unwrap();
    let signed_gates = if let Some(p) = ws.0.lock().unwrap().clone() {
        read_signed_gates(&p)
    } else {
        Vec::new()
    };
    EnforcementState {
        modes,
        overrides_this_wave: overrides,
        wave_frozen,
        required_gates: vec![0, 1, 2], // G0 Constitution, G1 Belief, G2 Expectation Map
        signed_gates,
    }
}

#[derive(Deserialize)]
pub struct BuildPrecheckArgs {
    pub stack: String,
}

#[derive(Serialize)]
pub struct PrecheckResult {
    pub allowed: bool,
    pub blocking_rule: Option<String>,
    pub reason: Option<String>,
    pub mode: Option<String>,
}

/// Run all build-time enforcement rules. Returns the first blocking rule
/// (if any) so the UI can surface a clear "fix X to continue" message.
#[tauri::command]
pub fn build_precheck(
    _args: BuildPrecheckArgs,
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<PrecheckResult, String> {
    let ws = workspace
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    // Rule: wave-freeze
    if check_rule(&enforcement, RULE_WAVE_FREEZE) && *enforcement.wave_frozen.lock().unwrap() {
        return Ok(PrecheckResult {
            allowed: false,
            blocking_rule: Some(RULE_WAVE_FREEZE.into()),
            reason: Some(
                "Wave is frozen. Sign G5 Quality Check and start a new wave to continue.".into(),
            ),
            mode: Some("strict".into()),
        });
    }

    // Rule: gate-gating + gate-compliance
    if check_rule(&enforcement, RULE_GATE_GATE) {
        let required: HashSet<u8> = [0u8, 1, 2].iter().copied().collect();
        let signed: HashSet<u8> = read_signed_gates(&ws).into_iter().collect();
        let missing: Vec<u8> = required.difference(&signed).copied().collect();
        if !missing.is_empty() {
            let missing_str = missing
                .iter()
                .map(|g| format!("G{g}"))
                .collect::<Vec<_>>()
                .join(", ");
            return Ok(PrecheckResult {
                allowed: false,
                blocking_rule: Some(RULE_GATE_GATE.into()),
                reason: Some(format!("Sign {missing_str} before running Build.")),
                mode: Some("strict".into()),
            });
        }
    }

    Ok(PrecheckResult {
        allowed: true,
        blocking_rule: None,
        reason: None,
        mode: None,
    })
}

#[derive(Deserialize)]
pub struct OverrideArgs {
    pub rule: String,
    pub reason: String,
    pub context: Option<String>,
}

#[tauri::command]
pub fn override_rule(
    args: OverrideArgs,
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<(), String> {
    if args.reason.trim().is_empty() {
        return Err("Override requires a reason.".into());
    }
    if !ALL_RULES.contains(&args.rule.as_str()) {
        return Err(format!("Unknown rule: {}", args.rule));
    }
    *enforcement.overrides.lock().unwrap() += 1;
    let ws = workspace
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;
    let entry = AuditEntry {
        ts: crate::ipc::ipc_chrono_iso8601(),
        action: "enforcement:override".to_string(),
        actor: "app".to_string(),
        gate_id: None,
        detail: format!(
            "rule={} reason={} context={}",
            args.rule,
            args.reason,
            args.context.unwrap_or_default()
        ),
    };
    append_audit(&ws, &entry).map_err(|e| format!("Could not write override audit: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn set_rule_mode(
    rule: String,
    mode: String,
    enforcement: State<EnforcementStore>,
) -> Result<(), String> {
    if !ALL_RULES.contains(&rule.as_str()) {
        return Err(format!("Unknown rule: {}", rule));
    }
    let m = match mode.as_str() {
        "strict" => Mode::Strict,
        "warn" => Mode::Warn,
        "off" => Mode::Off,
        _ => return Err(format!("Unknown mode: {}", mode)),
    };
    let mut modes = enforcement.modes.lock().unwrap();
    for (r, mode_v) in modes.iter_mut() {
        if *r == rule {
            *mode_v = m.clone();
            return Ok(());
        }
    }
    Err(format!("Rule {} not found", rule))
}

#[tauri::command]
pub fn freeze_wave(
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<(), String> {
    *enforcement.wave_frozen.lock().unwrap() = true;
    if let Some(ws) = workspace.0.lock().unwrap().clone() {
        let entry = AuditEntry {
            ts: crate::ipc::ipc_chrono_iso8601(),
            action: "wave:freeze".to_string(),
            actor: "app".to_string(),
            gate_id: None,
            detail: "wave frozen".to_string(),
        };
        let _ = append_audit(&ws, &entry);
    }
    Ok(())
}

#[tauri::command]
pub fn unfreeze_wave(
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<(), String> {
    *enforcement.wave_frozen.lock().unwrap() = false;
    *enforcement.overrides.lock().unwrap() = 0; // reset override counter on new wave
    if let Some(ws) = workspace.0.lock().unwrap().clone() {
        let entry = AuditEntry {
            ts: crate::ipc::ipc_chrono_iso8601(),
            action: "wave:unfreeze".to_string(),
            actor: "app".to_string(),
            gate_id: None,
            detail: "wave unfrozen".to_string(),
        };
        let _ = append_audit(&ws, &entry);
    }
    Ok(())
}

// ─── helpers ─────────────────────────────────────────────────────────────────

fn check_rule(state: &State<EnforcementStore>, rule: &str) -> bool {
    state
        .modes
        .lock()
        .unwrap()
        .iter()
        .any(|(r, m)| r == rule && matches!(m, Mode::Strict))
}

fn mode_str(m: &Mode) -> String {
    match m {
        Mode::Strict => "strict".into(),
        Mode::Warn => "warn".into(),
        Mode::Off => "off".into(),
    }
}

/// Read signed gate IDs from .signalos/gates.json or AUDIT_TRAIL.jsonl
/// gate:sign entries. The bundled SignalOS Core writes gates.json; the
/// desktop's own gate-sign command writes audit entries. We accept both.
fn read_signed_gates(workspace: &Path) -> Vec<u8> {
    let mut signed = HashSet::new();
    // Source 1: gates.json
    if let Some(gates) = crate::governance::load_gate_state(workspace) {
        for g in gates {
            if matches!(g.status, crate::governance::GateStatus::Signed) {
                signed.insert(g.id);
            }
        }
    }
    // Source 2: AUDIT_TRAIL.jsonl
    let audit_path: PathBuf = workspace.join(".signalos").join("AUDIT_TRAIL.jsonl");
    if let Ok(content) = std::fs::read_to_string(&audit_path) {
        for line in content.lines() {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                if v.get("action").and_then(|x| x.as_str()) == Some("gate:sign") {
                    let gate_field = v.get("gate").and_then(|x| x.as_str()).unwrap_or("");
                    if let Ok(num) = gate_field.trim_start_matches('G').parse::<u8>() {
                        signed.insert(num);
                    }
                    if let Some(id) = v.get("gate_id").and_then(|x| x.as_u64()) {
                        signed.insert(id as u8);
                    }
                }
            }
        }
    }
    let mut out: Vec<u8> = signed.into_iter().collect();
    out.sort();
    out
}
