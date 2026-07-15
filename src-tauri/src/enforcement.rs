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
use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::SystemTime;
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

/// Core invariants that may NEVER be disabled via `set_rule_mode` (Wave 0.3).
/// These are the mechanically-enforced governance rules; relaxing one requires
/// the governed override path (`override_rule`, which demands a reason + audit),
/// never a silent toggle to "off". Tunable policy/threshold rules (wave-freeze,
/// stack-contract, zero-manual-regression, mutation-threshold) are not listed.
const CORE_INVARIANTS: &[&str] = &[
    RULE_GATE_GATE,
    RULE_PLAN_GATE,
    RULE_TRUST_TIER,
    RULE_AUDIT_APPEND,
    RULE_SECRET_BLOCK,
    RULE_ROLE_SIGN,
    RULE_TEST_FIRST,
    RULE_GATE_COMPLIANCE,
];

/// Validate a requested (rule, mode) change. Pure and testable: rejects unknown
/// rules/modes and refuses to disable a core invariant.
fn validate_rule_mode(rule: &str, mode: &str) -> Result<Mode, String> {
    if !ALL_RULES.contains(&rule) {
        return Err(format!("Unknown rule: {rule}"));
    }
    let m = match mode {
        "strict" => Mode::Strict,
        "warn" => Mode::Warn,
        "off" => Mode::Off,
        _ => return Err(format!("Unknown mode: {mode}")),
    };
    if mode == "off" && CORE_INVARIANTS.contains(&rule) {
        return Err(format!(
            "'{rule}' is a core invariant and cannot be disabled; use a governed override (with a reason) instead."
        ));
    }
    Ok(m)
}

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

// ─── Persistence (Phase 1 / #15) ─────────────────────────────────────────────
//
// Toggles must survive restart, so the store snapshots to
// `ws/.signalos/enforcement.json` on every mutation. The Python
// FileEnforcementProvider reads the same file. INV: the core-invariant floor is
// re-applied on load so a corrupt/hand-edited file can never seed a core rule
// to "off" — mirrors `validate_rule_mode` (the single write-side authority).

/// Serializable snapshot of the enforcement store. Only the fields that must
/// survive a restart are persisted; `overrides` is per-wave and intentionally
/// not carried over.
#[derive(Serialize, Deserialize, Default)]
pub struct PersistedEnforcement {
    #[serde(default)]
    pub rule_modes: BTreeMap<String, String>,
    #[serde(default)]
    pub wave_frozen: bool,
}

/// Path of the per-workspace enforcement snapshot. Mirrors the `.signalos/`
/// convention used by `read_signed_gates` / gates.json / AUDIT_TRAIL.jsonl.
fn enforcement_path(ws: &Path) -> PathBuf {
    ws.join(".signalos").join("enforcement.json")
}

impl EnforcementStore {
    /// Snapshot `modes` + `wave_frozen` and write atomically (temp + rename)
    /// to `ws/.signalos/enforcement.json`.
    pub fn persist(&self, ws: &Path) -> Result<(), String> {
        let rule_modes: BTreeMap<String, String> = self
            .modes
            .lock()
            .unwrap()
            .iter()
            .map(|(r, m)| (r.clone(), mode_str(m)))
            .collect();
        let snapshot = PersistedEnforcement {
            rule_modes,
            wave_frozen: *self.wave_frozen.lock().unwrap(),
        };
        let dir = ws.join(".signalos");
        std::fs::create_dir_all(&dir).map_err(|e| format!("Could not create .signalos: {e}"))?;
        let path = enforcement_path(ws);
        let tmp = path.with_extension("json.tmp");
        let body = serde_json::to_string_pretty(&snapshot)
            .map_err(|e| format!("Could not serialize enforcement: {e}"))?;
        std::fs::write(&tmp, body)
            .map_err(|e| format!("Could not write enforcement.json.tmp: {e}"))?;
        std::fs::rename(&tmp, &path)
            .map_err(|e| format!("Could not rename enforcement.json: {e}"))?;
        Ok(())
    }

    /// Load a store from `ws/.signalos/enforcement.json` if present. Each
    /// `(rule, mode)` is run through `validate_rule_mode`; any that fails (unknown
    /// rule/mode, or a core invariant set to "off") is dropped and left at its
    /// `Default` (strict). Missing rules and an absent file fall back to Default.
    pub fn load_from(ws: &Path) -> EnforcementStore {
        let store = EnforcementStore::default();
        let path = enforcement_path(ws);
        let Ok(content) = std::fs::read_to_string(&path) else {
            return store;
        };
        let Ok(snapshot) = serde_json::from_str::<PersistedEnforcement>(&content) else {
            // A corrupt file must never weaken the floor: fall back to all-strict.
            return store;
        };
        {
            let mut modes = store.modes.lock().unwrap();
            for (rule, mode) in &snapshot.rule_modes {
                // Re-floor on read: validate_rule_mode rejects off-on-core-invariant,
                // so a hand-edited `gate-gating:off` is dropped and stays strict.
                if let Ok(valid) = validate_rule_mode(rule, mode) {
                    for (r, m) in modes.iter_mut() {
                        if r == rule {
                            *m = valid.clone();
                        }
                    }
                }
            }
        }
        *store.wave_frozen.lock().unwrap() = snapshot.wave_frozen;
        store
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
    #[serde(default)]
    pub rules: Option<Vec<String>>,
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
/// Minimal, well-defined stack contract (#16 Edit 2.2): each declared stack has
/// a set of top-level marker files that belong to a *foreign* stack. Their
/// presence at the workspace root is a contract violation.
fn stack_forbidden_markers(stack: &str) -> &'static [&'static str] {
    match stack {
        "python" => &["Cargo.toml", "package.json"],
        "node" | "typescript" | "javascript" => &["Cargo.toml"],
        "rust" => &["package.json"],
        _ => &[],
    }
}

#[tauri::command]
pub fn build_precheck(
    args: BuildPrecheckArgs,
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<PrecheckResult, String> {
    if let Some(rules) = args.rules.as_ref() {
        for rule in rules {
            if !ALL_RULES.contains(&rule.as_str()) {
                return Err(format!("Unknown rule: {rule}"));
            }
        }
    }
    let ws = workspace
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("No workspace selected")?;

    if let Some(result) = precheck_rules(&ws, &enforcement, &args) {
        return Ok(result);
    }

    Ok(PrecheckResult {
        allowed: true,
        blocking_rule: None,
        reason: None,
        mode: None,
    })
}

/// Pure-ish core of build_precheck, split out so unit tests can exercise every
/// rule against a temp workspace + a plain `EnforcementStore` (no Tauri State).
/// Returns `Some(blocking result)` on the first blocking rule, else `None`.
///
/// INV (anti-weakening): each new rule is guarded by `store_check_rule`, which
/// returns true only for Mode::Strict — so a rule that is warn/off never fires
/// and NEVER changes another rule's decision. The 4 already-enforcing rules
/// (gate-gating here) keep their exact checks.
fn precheck_rules(
    ws: &Path,
    enforcement: &EnforcementStore,
    args: &BuildPrecheckArgs,
) -> Option<PrecheckResult> {
    // Rule: wave-freeze
    if precheck_rule_requested(args, RULE_WAVE_FREEZE)
        && store_check_rule(enforcement, RULE_WAVE_FREEZE)
        && *enforcement.wave_frozen.lock().unwrap()
    {
        return Some(PrecheckResult {
            allowed: false,
            blocking_rule: Some(RULE_WAVE_FREEZE.into()),
            reason: Some(
                "Wave is frozen. Sign G5 Quality Check and start a new wave to continue.".into(),
            ),
            mode: Some("strict".into()),
        });
    }

    // Rule: gate-gating — the original G0-2 delivery-gate check (unchanged).
    if precheck_rule_requested(args, RULE_GATE_GATE)
        && store_check_rule(enforcement, RULE_GATE_GATE)
    {
        let required: HashSet<u8> = [0u8, 1, 2].iter().copied().collect();
        let signed: HashSet<u8> = read_signed_gates(ws).into_iter().collect();
        let missing: Vec<u8> = required.difference(&signed).copied().collect();
        if !missing.is_empty() {
            let mut missing_sorted = missing.clone();
            missing_sorted.sort();
            let missing_str = missing_sorted
                .iter()
                .map(|g| format!("G{g}"))
                .collect::<Vec<_>>()
                .join(", ");
            return Some(PrecheckResult {
                allowed: false,
                blocking_rule: Some(RULE_GATE_GATE.into()),
                reason: Some(format!("Sign {missing_str} before running Build.")),
                mode: Some("strict".into()),
            });
        }
    }

    // Rule: gate-compliance (#16 Edit 2.3) — a SEPARATE, stricter check that the
    // full advance-to-build gate set [0,1,2,3,4] is signed. gate-gating above is
    // untouched; this adds the remaining gates as their own rule.
    if precheck_rule_requested(args, RULE_GATE_COMPLIANCE)
        && store_check_rule(enforcement, RULE_GATE_COMPLIANCE)
    {
        let required: HashSet<u8> = [0u8, 1, 2, 3, 4].iter().copied().collect();
        let signed: HashSet<u8> = read_signed_gates(ws).into_iter().collect();
        let mut missing: Vec<u8> = required.difference(&signed).copied().collect();
        if !missing.is_empty() {
            missing.sort();
            let missing_str = missing
                .iter()
                .map(|g| format!("G{g}"))
                .collect::<Vec<_>>()
                .join(", ");
            return Some(PrecheckResult {
                allowed: false,
                blocking_rule: Some(RULE_GATE_COMPLIANCE.into()),
                reason: Some(format!(
                    "Gate compliance is binary: sign {missing_str} before advancing to Build."
                )),
                mode: Some("strict".into()),
            });
        }
    }

    // Rule: stack-contract (#16 Edit 2.2) — foreign-stack marker files at the
    // workspace root violate the declared stack contract.
    if precheck_rule_requested(args, RULE_STACK_CONTRACT)
        && store_check_rule(enforcement, RULE_STACK_CONTRACT)
    {
        for marker in stack_forbidden_markers(&args.stack) {
            if ws.join(marker).exists() {
                return Some(PrecheckResult {
                    allowed: false,
                    blocking_rule: Some(RULE_STACK_CONTRACT.into()),
                    reason: Some(format!(
                        "Stack contract violation: '{marker}' is not part of the '{}' stack.",
                        args.stack
                    )),
                    mode: Some("strict".into()),
                });
            }
        }
    }

    // Rule: zero-manual-regression (#16 Edit 2.4) — any open manual-defect
    // test-debt entry blocks the build until it becomes a regression test.
    if precheck_rule_requested(args, RULE_ZERO_MANUAL)
        && store_check_rule(enforcement, RULE_ZERO_MANUAL)
    {
        let open = crate::test_automation::open_manual_defect_count(ws);
        if open > 0 {
            return Some(PrecheckResult {
                allowed: false,
                blocking_rule: Some(RULE_ZERO_MANUAL.into()),
                reason: Some(format!(
                    "{open} open manual-defect entr{} must become a regression test before Build.",
                    if open == 1 { "y" } else { "ies" }
                )),
                mode: Some("strict".into()),
            });
        }
    }

    // Rule: mutation-threshold (#16 Edit 2.5) — reuse the existing pure fn.
    // A missing score = cannot prove rule 12 = block.
    if precheck_rule_requested(args, RULE_MUTATION) && store_check_rule(enforcement, RULE_MUTATION)
    {
        let score = crate::test_automation::read_mutation_score_value(ws);
        let (allowed, reason) = match score {
            Some(s) => {
                let result = crate::test_automation::check_mutation_threshold(
                    crate::test_automation::MutationScoreArgs {
                        score: s,
                        area: "workspace".into(),
                    },
                );
                (result.allowed, result.reason)
            }
            None => (
                false,
                Some(
                    "No mutation score on file: run mutation testing before Build (cannot prove the ≥95% threshold)."
                        .to_string(),
                ),
            ),
        };
        if !allowed {
            return Some(PrecheckResult {
                allowed: false,
                blocking_rule: Some(RULE_MUTATION.into()),
                reason,
                mode: Some("strict".into()),
            });
        }
    }

    None
}

fn precheck_rule_requested(args: &BuildPrecheckArgs, rule: &str) -> bool {
    match args.rules.as_ref() {
        None => true,
        Some(rules) => rules.iter().any(|candidate| candidate == rule),
    }
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
    // Persist the modes/frozen snapshot so the override context survives restart.
    enforcement.persist(&ws)?;
    Ok(())
}

#[tauri::command]
pub fn set_rule_mode(
    rule: String,
    mode: String,
    enforcement: State<EnforcementStore>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> Result<(), String> {
    // validate_rule_mode is the single write-side authority: it rejects a core
    // invariant set to "off" BEFORE we touch memory or disk, so no file is ever
    // written for a rejected change (see set_rule_mode_core_invariant_off_still_errors).
    let m = validate_rule_mode(&rule, &mode)?;
    {
        let mut modes = enforcement.modes.lock().unwrap();
        let mut found = false;
        for (r, mode_v) in modes.iter_mut() {
            if *r == rule {
                *mode_v = m.clone();
                found = true;
                break;
            }
        }
        if !found {
            return Err(format!("Rule {} not found", rule));
        }
    }
    // Persist so the toggle survives restart (read back by load_from + the
    // Python FileEnforcementProvider). Persist is best-effort on missing ws.
    if let Some(ws) = workspace.0.lock().unwrap().clone() {
        enforcement.persist(&ws)?;
    }
    Ok(())
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
        // Persist wave_frozen so a freeze survives restart.
        enforcement.persist(&ws)?;
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
        // Persist the cleared freeze so it survives restart.
        enforcement.persist(&ws)?;
    }
    Ok(())
}

// ─── helpers ─────────────────────────────────────────────────────────────────

/// True only when `rule` is Mode::Strict. This is the "is this rule enforced
/// right now" gate used by every build_precheck rule. Kept strict-only this pass
/// (warn == not enforced in Rust) so enabling one rule never alters another's
/// behavior — see the *_ok_when_disabled precheck tests.
fn store_check_rule(store: &EnforcementStore, rule: &str) -> bool {
    store
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

/// Cache key: workspace path → (gates.json mtime, audit.jsonl mtime, audit byte count, parsed signed gates).
///
/// `read_signed_gates` was called once per build_precheck AND once per
/// enforcement_state poll (which the topbar pill refreshes on a timer).
/// Re-parsing the entire AUDIT_TRAIL.jsonl every time was O(n) wasted work
/// where n is the audit length — and the audit grows monotonically.
///
/// New strategy:
///   1. Read mtime + size of gates.json and AUDIT_TRAIL.jsonl
///   2. If both match the cached snapshot, return the cached Vec<u8>
///   3. Otherwise re-parse and update the cache
///
/// gates.json doesn't grow but it can change atomically (rename swap), so
/// we key on mtime. AUDIT_TRAIL.jsonl only appends, so size is a stable
/// fingerprint — change detected = re-parse.
#[derive(Default, Clone)]
struct GatesCache {
    gates_json_mtime: Option<SystemTime>,
    audit_mtime: Option<SystemTime>,
    audit_len: u64,
    workspace: PathBuf,
    signed: Vec<u8>,
}

fn cache() -> &'static Mutex<GatesCache> {
    static CACHE: OnceLock<Mutex<GatesCache>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(GatesCache::default()))
}

/// Read signed gate IDs from .signalos/gates.json or AUDIT_TRAIL.jsonl
/// gate:sign entries. The bundled SignalOS Core writes gates.json; the
/// desktop's own gate-sign command writes audit entries. We accept both.
///
/// Cached by mtime + length — returns instantly when nothing changed.
fn read_signed_gates(workspace: &Path) -> Vec<u8> {
    let gates_path = workspace.join(".signalos").join("gates.json");
    let audit_path = workspace.join(".signalos").join("AUDIT_TRAIL.jsonl");

    let gates_mtime = std::fs::metadata(&gates_path)
        .and_then(|m| m.modified())
        .ok();
    let (audit_mtime, audit_len) = std::fs::metadata(&audit_path)
        .map(|m| (m.modified().ok(), m.len()))
        .unwrap_or((None, 0));

    // Cache lookup.
    {
        let c = cache().lock().unwrap();
        if c.workspace == workspace
            && c.gates_json_mtime == gates_mtime
            && c.audit_mtime == audit_mtime
            && c.audit_len == audit_len
        {
            return c.signed.clone();
        }
    }

    // Cache miss / stale: re-parse from both sources.
    let mut signed = HashSet::new();
    if let Some(gates) = crate::governance::load_gate_state(workspace) {
        for g in gates {
            if matches!(g.status, crate::governance::GateStatus::Signed) {
                signed.insert(g.id);
            }
        }
    }
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

    // Update cache.
    {
        let mut c = cache().lock().unwrap();
        c.workspace = workspace.to_path_buf();
        c.gates_json_mtime = gates_mtime;
        c.audit_mtime = audit_mtime;
        c.audit_len = audit_len;
        c.signed = out.clone();
    }
    out
}

#[cfg(test)]
mod rule_mode_tests {
    use super::{validate_rule_mode, Mode, RULE_TEST_FIRST, RULE_WAVE_FREEZE};

    #[test]
    fn strict_on_core_invariant_is_ok() {
        assert!(matches!(
            validate_rule_mode(RULE_TEST_FIRST, "strict"),
            Ok(Mode::Strict)
        ));
    }

    #[test]
    fn warn_on_core_invariant_is_ok() {
        assert!(matches!(
            validate_rule_mode(RULE_TEST_FIRST, "warn"),
            Ok(Mode::Warn)
        ));
    }

    #[test]
    fn off_on_core_invariant_is_rejected() {
        let result = validate_rule_mode(RULE_TEST_FIRST, "off");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("core invariant"));
    }

    #[test]
    fn off_on_tunable_rule_is_ok() {
        assert!(matches!(
            validate_rule_mode(RULE_WAVE_FREEZE, "off"),
            Ok(Mode::Off)
        ));
    }

    #[test]
    fn unknown_rule_is_rejected() {
        assert!(validate_rule_mode("no-such-rule", "strict").is_err());
    }

    #[test]
    fn unknown_mode_is_rejected() {
        assert!(validate_rule_mode(RULE_TEST_FIRST, "loose").is_err());
    }
}

#[cfg(test)]
mod persist_tests {
    use super::{
        enforcement_path, mode_str, EnforcementStore, Mode, RULE_GATE_GATE, RULE_STACK_CONTRACT,
        RULE_WAVE_FREEZE,
    };
    use std::path::Path;

    fn mode_of(store: &EnforcementStore, rule: &str) -> String {
        store
            .modes
            .lock()
            .unwrap()
            .iter()
            .find(|(r, _)| r == rule)
            .map(|(_, m)| mode_str(m))
            .unwrap()
    }

    fn tmp_ws() -> std::path::PathBuf {
        static NEXT_ID: std::sync::atomic::AtomicUsize =
            std::sync::atomic::AtomicUsize::new(0);
        let mut p = std::env::temp_dir();
        p.push(format!(
            "signalos-enf-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT_ID.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    #[test]
    fn persist_then_load_roundtrips_modes() {
        let ws = tmp_ws();
        let store = EnforcementStore::default();
        {
            let mut modes = store.modes.lock().unwrap();
            for (r, m) in modes.iter_mut() {
                if r == RULE_STACK_CONTRACT {
                    *m = Mode::Warn;
                }
            }
        }
        store.persist(&ws).unwrap();
        assert!(enforcement_path(&ws).is_file());

        let loaded = EnforcementStore::load_from(&ws);
        assert_eq!(mode_of(&loaded, RULE_STACK_CONTRACT), "warn");
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn load_rejects_core_invariant_off() {
        // Hand-write an enforcement.json with a core invariant forced to "off"
        // plus a legitimate warn on a tunable rule.
        let ws = tmp_ws();
        std::fs::create_dir_all(ws.join(".signalos")).unwrap();
        let body = r#"{
            "rule_modes": { "gate-gating": "off", "stack-contract": "warn" },
            "wave_frozen": false
        }"#;
        std::fs::write(enforcement_path(&ws), body).unwrap();

        let loaded = EnforcementStore::load_from(&ws);
        // Floor re-applied: the core invariant loads as strict, never off.
        assert_eq!(mode_of(&loaded, RULE_GATE_GATE), "strict");
        // The other valid mode is preserved.
        assert_eq!(mode_of(&loaded, RULE_STACK_CONTRACT), "warn");
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn set_rule_mode_writes_file() {
        // Mirror set_rule_mode's persistence path without a live Tauri State:
        // mutate + persist, then confirm the file contains the change.
        let ws = tmp_ws();
        let store = EnforcementStore::default();
        {
            let mut modes = store.modes.lock().unwrap();
            for (r, m) in modes.iter_mut() {
                if r == RULE_WAVE_FREEZE {
                    *m = Mode::Warn;
                }
            }
        }
        store.persist(&ws).unwrap();
        let path = enforcement_path(&ws);
        assert!(path.is_file());
        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("wave-freeze"));
        assert!(content.contains("warn"));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn set_rule_mode_core_invariant_off_still_errors_and_writes_nothing() {
        // validate_rule_mode gates the mutation; a rejected off-on-core-invariant
        // returns Err BEFORE any persist, so no file is written.
        use super::validate_rule_mode;
        let ws = tmp_ws();
        let result = validate_rule_mode(RULE_GATE_GATE, "off");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("core invariant"));
        // No persist happened for the rejected change.
        assert!(!enforcement_path(&ws).exists());
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn absent_file_loads_all_strict() {
        let ws = tmp_ws();
        let loaded = EnforcementStore::load_from(&ws);
        assert_eq!(mode_of(&loaded, RULE_STACK_CONTRACT), "strict");
        assert_eq!(mode_of(&loaded, RULE_GATE_GATE), "strict");
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn enforcement_path_is_under_signalos() {
        let p = enforcement_path(Path::new("/ws"));
        assert!(
            p.ends_with(".signalos/enforcement.json") || p.ends_with(".signalos\\enforcement.json")
        );
    }
}

#[cfg(test)]
mod precheck_rule_tests {
    use super::{
        precheck_rules, BuildPrecheckArgs, EnforcementStore, Mode, RULE_GATE_COMPLIANCE,
        RULE_GATE_GATE, RULE_MUTATION, RULE_STACK_CONTRACT, RULE_WAVE_FREEZE, RULE_ZERO_MANUAL,
    };
    use std::io::Write;
    use std::path::PathBuf;

    fn tmp_ws() -> PathBuf {
        static NEXT_ID: std::sync::atomic::AtomicUsize =
            std::sync::atomic::AtomicUsize::new(0);
        let mut p = std::env::temp_dir();
        p.push(format!(
            "signalos-precheck-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT_ID.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir_all(p.join(".signalos")).unwrap();
        p
    }

    /// Set every rule to Off EXCEPT the one under test, so a single rule's
    /// behavior is observed in isolation. (Core-invariant floor is not relevant
    /// here — the store is built directly, not via set_rule_mode.)
    fn only(rule: &str) -> EnforcementStore {
        let store = EnforcementStore::default();
        {
            let mut modes = store.modes.lock().unwrap();
            for (r, m) in modes.iter_mut() {
                *m = if r == rule { Mode::Strict } else { Mode::Off };
            }
        }
        store
    }

    fn set_mode(store: &EnforcementStore, rule: &str, mode: Mode) {
        let mut modes = store.modes.lock().unwrap();
        for (r, m) in modes.iter_mut() {
            if r == rule {
                *m = mode.clone();
            }
        }
    }

    fn sign_gates(ws: &std::path::Path, gates: &[u8]) {
        let audit = ws.join(".signalos").join("AUDIT_TRAIL.jsonl");
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&audit)
            .unwrap();
        for g in gates {
            writeln!(
                f,
                "{{\"action\":\"gate:sign\",\"gate_id\":{g},\"actor\":\"t\"}}"
            )
            .unwrap();
        }
    }

    fn args(stack: &str) -> BuildPrecheckArgs {
        BuildPrecheckArgs {
            stack: stack.into(),
            rules: None,
        }
    }

    fn args_only(stack: &str, rules: &[&str]) -> BuildPrecheckArgs {
        BuildPrecheckArgs {
            stack: stack.into(),
            rules: Some(rules.iter().map(|rule| (*rule).to_string()).collect()),
        }
    }

    // ── stack-contract (2.2) ────────────────────────────────────────────────

    #[test]
    fn stack_contract_blocks_foreign_stack_marker() {
        let ws = tmp_ws();
        // python stack with a rust marker present = violation.
        std::fs::write(ws.join("Cargo.toml"), "[package]\n").unwrap();
        let store = only(RULE_STACK_CONTRACT);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_STACK_CONTRACT));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn stack_contract_ok_when_disabled() {
        let ws = tmp_ws();
        std::fs::write(ws.join("Cargo.toml"), "[package]\n").unwrap();
        // Rule off (warn also non-strict) → same ws is allowed; proves it only
        // fires when strict and never weakens the others.
        let store = only(RULE_STACK_CONTRACT);
        set_mode(&store, RULE_STACK_CONTRACT, Mode::Warn);
        let r = precheck_rules(&ws, &store, &args("python"));
        assert!(r.is_none(), "warn/off stack-contract must not block");
        std::fs::remove_dir_all(&ws).ok();
    }

    // ── gate-compliance (2.3) + gate-gating regression ──────────────────────

    #[test]
    fn gate_compliance_blocks_when_full_set_incomplete() {
        let ws = tmp_ws();
        sign_gates(&ws, &[0, 1, 2]); // G3/G4 missing
        let store = only(RULE_GATE_COMPLIANCE);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_GATE_COMPLIANCE));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn gate_compliance_allows_when_full_set_signed() {
        let ws = tmp_ws();
        sign_gates(&ws, &[0, 1, 2, 3, 4]);
        let store = only(RULE_GATE_COMPLIANCE);
        let r = precheck_rules(&ws, &store, &args("python"));
        assert!(r.is_none());
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn gate_gating_still_blocks_g0_2() {
        // Regression: the original gate-gating behavior is intact.
        let ws = tmp_ws();
        sign_gates(&ws, &[0]); // G1/G2 missing
        let store = only(RULE_GATE_GATE);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_GATE_GATE));
        std::fs::remove_dir_all(&ws).ok();
    }

    // ── zero-manual-regression (2.4) ────────────────────────────────────────

    #[test]
    fn zero_manual_blocks_with_open_manual_defect() {
        let ws = tmp_ws();
        let debt = ws.join(".signalos").join("test-debt.jsonl");
        std::fs::write(
            &debt,
            "{\"ts\":\"t\",\"kind\":\"manual-defect\",\"area\":\"a\",\"title\":\"t\",\"detail\":\"d\",\"resolved\":false}\n",
        )
        .unwrap();
        let store = only(RULE_ZERO_MANUAL);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_ZERO_MANUAL));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn zero_manual_allows_when_none_open() {
        let ws = tmp_ws();
        let debt = ws.join(".signalos").join("test-debt.jsonl");
        // A resolved manual-defect does not block.
        std::fs::write(
            &debt,
            "{\"ts\":\"t\",\"kind\":\"manual-defect\",\"area\":\"a\",\"title\":\"t\",\"detail\":\"d\",\"resolved\":true}\n",
        )
        .unwrap();
        let store = only(RULE_ZERO_MANUAL);
        let r = precheck_rules(&ws, &store, &args("python"));
        assert!(r.is_none());
        std::fs::remove_dir_all(&ws).ok();
    }

    // ── mutation-threshold (2.5) ────────────────────────────────────────────

    #[test]
    fn mutation_threshold_blocks_below_95() {
        let ws = tmp_ws();
        std::fs::write(
            ws.join(".signalos").join("mutation-score.json"),
            "{\"score\":0.80,\"area\":\"workspace\"}",
        )
        .unwrap();
        let store = only(RULE_MUTATION);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_MUTATION));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn mutation_threshold_allows_at_or_above() {
        let ws = tmp_ws();
        std::fs::write(
            ws.join(".signalos").join("mutation-score.json"),
            "{\"score\":0.96,\"area\":\"workspace\"}",
        )
        .unwrap();
        let store = only(RULE_MUTATION);
        let r = precheck_rules(&ws, &store, &args("python"));
        assert!(r.is_none());
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn mutation_threshold_missing_file_blocks() {
        let ws = tmp_ws();
        let store = only(RULE_MUTATION);
        let r = precheck_rules(&ws, &store, &args("python")).expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_MUTATION));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn mutation_threshold_ok_when_disabled() {
        // Missing file would block if strict; with the rule off it must not.
        let ws = tmp_ws();
        let store = only(RULE_MUTATION);
        set_mode(&store, RULE_MUTATION, Mode::Off);
        let r = precheck_rules(&ws, &store, &args("python"));
        assert!(r.is_none());
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn wave_freeze_filter_blocks_frozen_wave_without_other_rules() {
        let ws = tmp_ws();
        let store = EnforcementStore::default();
        *store.wave_frozen.lock().unwrap() = true;
        let r = precheck_rules(&ws, &store, &args_only("auto", &[RULE_WAVE_FREEZE]))
            .expect("should block");
        assert!(!r.allowed);
        assert_eq!(r.blocking_rule.as_deref(), Some(RULE_WAVE_FREEZE));
        std::fs::remove_dir_all(&ws).ok();
    }

    #[test]
    fn wave_freeze_filter_does_not_run_gate_or_mutation_rules() {
        let ws = tmp_ws();
        let store = EnforcementStore::default();
        let r = precheck_rules(&ws, &store, &args_only("auto", &[RULE_WAVE_FREEZE]));
        assert!(
            r.is_none(),
            "wave-freeze-only precheck must not require gates or mutation evidence"
        );
        std::fs::remove_dir_all(&ws).ok();
    }
}
