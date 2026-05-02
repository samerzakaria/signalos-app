/// provider.rs — Multi-provider LLM routing (Option B)
///
/// Routes LLM calls to the correct provider based on user config.
/// API keys are never stored here — always fetched from the keychain at call time.
/// Token counts are tracked per-call and accumulated for the cost meter.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::sync::Mutex;

// ─── PROVIDER CONFIG ─────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Anthropic,
    OpenAI,
    Gemini,
    Ollama,
}

impl Provider {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "anthropic" => Some(Self::Anthropic),
            "openai"    => Some(Self::OpenAI),
            "gemini"    => Some(Self::Gemini),
            "ollama"    => Some(Self::Ollama),
            _           => None,
        }
    }

    pub fn display_name(&self) -> &str {
        match self {
            Self::Anthropic => "Claude",
            Self::OpenAI    => "GPT-4o",
            Self::Gemini    => "Gemini",
            Self::Ollama    => "Ollama (local)",
        }
    }

    pub fn api_base(&self) -> &str {
        match self {
            Self::Anthropic => "https://api.anthropic.com",
            Self::OpenAI    => "https://api.openai.com",
            Self::Gemini    => "https://generativelanguage.googleapis.com",
            Self::Ollama    => "http://localhost:11434",
        }
    }

    pub fn default_model(&self) -> &str {
        match self {
            Self::Anthropic => "claude-sonnet-4-6",
            Self::OpenAI    => "gpt-4o",
            Self::Gemini    => "gemini-1.5-pro",
            Self::Ollama    => "llama3",
        }
    }

    /// USD per 1M input tokens
    pub fn price_per_1m_in(&self) -> f64 {
        match self {
            Self::Anthropic => 3.00,
            Self::OpenAI    => 5.00,
            Self::Gemini    => 1.25,
            Self::Ollama    => 0.00, // local, no cost
        }
    }

    /// USD per 1M output tokens
    pub fn price_per_1m_out(&self) -> f64 {
        match self {
            Self::Anthropic => 15.00,
            Self::OpenAI    => 15.00,
            Self::Gemini    => 5.00,
            Self::Ollama    => 0.00,
        }
    }
}

// ─── COST ACCUMULATOR ────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct CostAccumulator {
    pub tokens_in:    u64,
    pub tokens_out:   u64,
    pub session_usd:  f64,
    pub monthly_usd:  f64,
    pub budget_usd:   f64,
    pub provider:     String,
}

impl CostAccumulator {
    pub fn new(budget_usd: f64, provider: &Provider) -> Self {
        Self {
            budget_usd,
            provider: provider.display_name().into(),
            ..Default::default()
        }
    }

    /// Record token usage from a single LLM call.
    pub fn record(&mut self, tokens_in: u64, tokens_out: u64, provider: &Provider) {
        self.tokens_in  += tokens_in;
        self.tokens_out += tokens_out;
        let call_cost = (tokens_in  as f64 / 1_000_000.0) * provider.price_per_1m_in()
                      + (tokens_out as f64 / 1_000_000.0) * provider.price_per_1m_out();
        self.session_usd  += call_cost;
        self.monthly_usd  += call_cost;
    }

    pub fn over_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd
    }

    pub fn near_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd * 0.8
    }
}

// ─── GLOBAL COST STATE ───────────────────────────────────────────────────────

pub struct ProviderState {
    pub active:      Mutex<Provider>,
    pub cost:        Mutex<CostAccumulator>,
}

impl ProviderState {
    pub fn new() -> Self {
        let provider = Provider::Anthropic;
        let cost = CostAccumulator::new(10.0, &provider);
        Self {
            active: Mutex::new(provider),
            cost:   Mutex::new(cost),
        }
    }
}

// ─── TAURI COMMANDS ──────────────────────────────────────────────────────────

use tauri::State;

#[derive(Serialize)]
pub struct ProviderInfo {
    pub id:            String,
    pub name:          String,
    pub model:         String,
    pub has_key:       bool,
    pub price_in_1m:   f64,
    pub price_out_1m:  f64,
}

#[tauri::command]
pub fn get_active_provider(state: State<ProviderState>) -> String {
    let p = state.active.lock().unwrap();
    match *p {
        Provider::Anthropic => "anthropic",
        Provider::OpenAI    => "openai",
        Provider::Gemini    => "gemini",
        Provider::Ollama    => "ollama",
    }.into()
}

#[tauri::command]
pub fn set_active_provider(provider: String, state: State<ProviderState>) -> Result<(), String> {
    let p = Provider::from_str(&provider)
        .ok_or_else(|| format!("Unknown provider: {}", provider))?;
    *state.active.lock().unwrap() = p.clone();
    let mut cost = state.cost.lock().unwrap();
    cost.provider = p.display_name().into();
    Ok(())
}

#[tauri::command]
pub fn get_cost_state(state: State<ProviderState>) -> CostAccumulator {
    state.cost.lock().unwrap().clone()
}

#[tauri::command]
pub fn record_token_usage(
    tokens_in:  u64,
    tokens_out: u64,
    state:      State<ProviderState>,
) -> CostAccumulator {
    let provider = state.active.lock().unwrap().clone();
    let mut cost = state.cost.lock().unwrap();
    cost.record(tokens_in, tokens_out, &provider);
    cost.clone()
}

#[tauri::command]
pub fn reset_session_cost(state: State<ProviderState>) {
    state.cost.lock().unwrap().session_usd = 0.0;
}

#[tauri::command]
pub fn set_monthly_budget(budget_usd: f64, state: State<ProviderState>) {
    state.cost.lock().unwrap().budget_usd = budget_usd;
}

#[tauri::command]
pub fn list_providers() -> Vec<serde_json::Value> {
    vec![
        serde_json::json!({ "id": "anthropic", "name": "Anthropic Claude", "model": "claude-sonnet-4-6",  "price_in": 3.00,  "price_out": 15.00 }),
        serde_json::json!({ "id": "openai",    "name": "OpenAI",           "model": "gpt-4o",             "price_in": 5.00,  "price_out": 15.00 }),
        serde_json::json!({ "id": "gemini",    "name": "Google Gemini",    "model": "gemini-1.5-pro",     "price_in": 1.25,  "price_out": 5.00  }),
        serde_json::json!({ "id": "ollama",    "name": "Ollama (local)",   "model": "llama3",             "price_in": 0.00,  "price_out": 0.00  }),
    ]
}
