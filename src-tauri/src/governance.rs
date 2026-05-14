/// governance.rs — Wave state machine + audit trail persistence
///
/// This module owns the authoritative in-memory representation of the
/// current wave. It is kept in sync with the Python sidecar via IPC events
/// and is the single source of truth for the frontend's gate/phase UI.
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::sync::Mutex;

// ─── TYPES ───────────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum GateStatus {
    Signed,
    Current,
    Locked,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Gate {
    pub id: u8,
    pub name: String,
    pub desc: String,
    pub status: GateStatus,
    pub signer: Option<String>,
    pub signed_at: Option<String>,
    pub artifacts: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct WaveSnapshot {
    pub name: String,
    pub phase: u8,
    pub phase_name: String,
    pub progress_pct: u8,
    pub belief_conf: u8,
    pub gates: Vec<Gate>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct AuditEntry {
    pub ts: String,
    pub action: String,
    pub actor: String,
    pub gate_id: Option<u8>,
    pub detail: String,
}

// ─── STATE ───────────────────────────────────────────────────────────────────

pub struct GovernanceState {
    pub wave: Mutex<Option<WaveSnapshot>>,
    pub audit: Mutex<Vec<AuditEntry>>,
}

impl GovernanceState {
    pub fn new() -> Self {
        Self {
            wave: Mutex::new(Some(Self::default_wave())),
            audit: Mutex::new(vec![]),
        }
    }

    fn default_wave() -> WaveSnapshot {
        WaveSnapshot {
            name: "Wave 1".into(),
            phase: 1,
            phase_name: "Discovery".into(),
            progress_pct: 0,
            belief_conf: 0,
            gates: vec![
                Gate {
                    id: 0,
                    name: "Constitution".into(),
                    desc: "Immutable project rules".into(),
                    status: GateStatus::Signed,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["CONSTITUTION.md".into()],
                },
                Gate {
                    id: 1,
                    name: "Belief".into(),
                    desc: "Signed statement of what we believe".into(),
                    status: GateStatus::Signed,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["BELIEF.md".into()],
                },
                Gate {
                    id: 2,
                    name: "Expectation Map".into(),
                    desc: "Measurable success criteria for this wave".into(),
                    status: GateStatus::Current,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["EXPECTATION_MAP.md".into()],
                },
                Gate {
                    id: 3,
                    name: "Plan".into(),
                    desc: "PLAN.md approved by PO".into(),
                    status: GateStatus::Locked,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["PLAN.md".into()],
                },
                Gate {
                    id: 4,
                    name: "Trust Tier".into(),
                    desc: "All tasks declared T1/T2/T3".into(),
                    status: GateStatus::Locked,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["PLAN.md (T-tier column)".into()],
                },
                Gate {
                    id: 5,
                    name: "Quality Check".into(),
                    desc: "Critical findings resolved, QUALITY_CHECK.md signed".into(),
                    status: GateStatus::Locked,
                    signer: None,
                    signed_at: None,
                    artifacts: vec!["QUALITY_CHECK.md".into()],
                },
            ],
        }
    }
}

// ─── AUDIT TRAIL PERSISTENCE ─────────────────────────────────────────────────

/// Append an audit entry to .signalos/audit.jsonl in the workspace.
pub fn append_audit(workspace: &Path, entry: &AuditEntry) -> anyhow::Result<()> {
    let dir = workspace.join(".signalos");
    fs::create_dir_all(&dir)?;
    let path = dir.join("audit.jsonl");
    let line = serde_json::to_string(entry)? + "\n";
    fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    fs::write(&path, {
        let existing = fs::read_to_string(&path).unwrap_or_default();
        existing + &line
    })?;
    Ok(())
}

/// Read audit entries from .signalos/audit.jsonl (most recent first).
pub fn read_audit(workspace: &Path, limit: usize) -> Vec<AuditEntry> {
    let path = workspace.join(".signalos").join("audit.jsonl");
    let Ok(content) = fs::read_to_string(&path) else {
        return vec![];
    };
    let mut entries: Vec<AuditEntry> = content
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    entries.reverse();
    entries.truncate(limit);
    entries
}

/// Write signed gate state to .signalos/gates.json in the workspace.
pub fn persist_gate_state(workspace: &Path, gates: &[Gate]) -> anyhow::Result<()> {
    let dir = workspace.join(".signalos");
    fs::create_dir_all(&dir)?;
    let json = serde_json::to_string_pretty(gates)?;
    fs::write(dir.join("gates.json"), json)?;
    Ok(())
}

/// Load gate state from .signalos/gates.json if it exists.
pub fn load_gate_state(workspace: &Path) -> Option<Vec<Gate>> {
    let path = workspace.join(".signalos").join("gates.json");
    let content = fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}
