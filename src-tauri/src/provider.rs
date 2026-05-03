/// provider.rs — Multi-provider LLM routing
///
/// Model names and pricing are NEVER hardcoded here.
/// They live in ~/.config/signalos/providers.json, which is written on first
/// launch with safe defaults and can be edited freely by the user or updated
/// via the Settings UI without reinstalling the app.
///
/// Adding a new model or a new provider = edit providers.json. No recompile.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;

// ─── PROVIDER ID ─────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Anthropic,
    OpenAI,
    Gemini,
    Ollama,
}

impl Provider {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "anthropic" => Some(Self::Anthropic),
            "openai"    => Some(Self::OpenAI),
            "gemini"    => Some(Self::Gemini),
            "ollama"    => Some(Self::Ollama),
            _           => None,
        }
    }

    pub fn id(&self) -> &str {
        match self {
            Self::Anthropic => "anthropic",
            Self::OpenAI    => "openai",
            Self::Gemini    => "gemini",
            Self::Ollama    => "ollama",
        }
    }

    pub fn display_name(&self) -> &str {
        match self {
            Self::Anthropic => "Anthropic Claude",
            Self::OpenAI    => "OpenAI",
            Self::Gemini    => "Google Gemini",
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

    /// Whether this provider requires an API key (Ollama is local — no key needed)
    pub fn needs_api_key(&self) -> bool {
        !matches!(self, Self::Ollama)
    }

    pub fn all() -> &'static [Provider] {
        &[Self::Anthropic, Self::OpenAI, Self::Gemini, Self::Ollama]
    }
}

// ─── PER-PROVIDER CONFIG (user-editable, persisted to disk) ──────────────────

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ProviderConfig {
    /// The model string sent to the API — user sets this, we never hardcode it.
    /// Ollama: whatever the user has pulled (e.g. "llama3.2", "mistral", "phi4").
    /// Cloud providers: whatever the user wants (e.g. "claude-opus-4-6", "gpt-4o-mini").
    pub model: String,

    /// USD per 1M input tokens. User can update when providers change pricing.
    pub price_in_1m: f64,

    /// USD per 1M output tokens.
    pub price_out_1m: f64,
}

impl ProviderConfig {
    /// Conservative defaults shipped with the app.
    /// These intentionally use stable, currently-available models.
    /// Users are expected to update models to whatever is current for them.
    /// Ollama model is blank — user must fill it in (they know what they've pulled).
    fn defaults() -> HashMap<String, ProviderConfig> {
        [
            ("anthropic", ProviderConfig {
                model:        "claude-sonnet-4-6".into(),
                price_in_1m:  3.00,
                price_out_1m: 15.00,
            }),
            ("openai", ProviderConfig {
                model:        "gpt-4o".into(),
                price_in_1m:  5.00,
                price_out_1m: 15.00,
            }),
            ("gemini", ProviderConfig {
                model:        "gemini-2.0-flash".into(),
                price_in_1m:  0.10,
                price_out_1m: 0.40,
            }),
            ("ollama", ProviderConfig {
                model:        "".into(),   // blank — user fills in their pulled model
                price_in_1m:  0.00,
                price_out_1m: 0.00,
            }),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect()
    }
}

// ─── CONFIG FILE ─────────────────────────────────────────────────────────────

pub fn load_provider_configs(app_config_dir: &PathBuf) -> HashMap<String, ProviderConfig> {
    let path = app_config_dir.join("providers.json");

    if let Ok(data) = std::fs::read_to_string(&path) {
        if let Ok(configs) = serde_json::from_str::<HashMap<String, ProviderConfig>>(&data) {
            // Merge with defaults so new providers added in future versions appear
            let mut merged = ProviderConfig::defaults();
            for (k, v) in configs {
                merged.insert(k, v);
            }
            return merged;
        }
    }

    // File missing or corrupt — write defaults and return them
    let defaults = ProviderConfig::defaults();
    persist_configs(app_config_dir, &defaults);
    defaults
}

fn persist_configs(app_config_dir: &PathBuf, configs: &HashMap<String, ProviderConfig>) {
    let _ = std::fs::create_dir_all(app_config_dir);
    if let Ok(json) = serde_json::to_string_pretty(configs) {
        let _ = std::fs::write(app_config_dir.join("providers.json"), json);
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
    pub model:        String,
}

impl CostAccumulator {
    pub fn record(&mut self, tokens_in: u64, tokens_out: u64, cfg: &ProviderConfig) {
        self.tokens_in  += tokens_in;
        self.tokens_out += tokens_out;
        let cost = (tokens_in  as f64 / 1_000_000.0) * cfg.price_in_1m
                 + (tokens_out as f64 / 1_000_000.0) * cfg.price_out_1m;
        self.session_usd  += cost;
        self.monthly_usd  += cost;
    }

    pub fn over_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd
    }

    pub fn near_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd * 0.8
    }
}

// ─── GLOBAL STATE ────────────────────────────────────────────────────────────

pub struct ProviderState {
    pub active:         Mutex<Provider>,
    pub cost:           Mutex<CostAccumulator>,
    pub configs:        Mutex<HashMap<String, ProviderConfig>>,
    pub app_config_dir: PathBuf,
}

impl ProviderState {
    pub fn new(app_config_dir: PathBuf) -> Self {
        let configs = load_provider_configs(&app_config_dir);
        let default_model = configs
            .get("anthropic")
            .map(|c| c.model.clone())
            .unwrap_or_default();

        Self {
            active:  Mutex::new(Provider::Anthropic),
            cost:    Mutex::new(CostAccumulator {
                budget_usd: 10.0,
                provider:   "Anthropic Claude".into(),
                model:      default_model,
                ..Default::default()
            }),
            configs: Mutex::new(configs),
            app_config_dir,
        }
    }
}

// ─── TAURI COMMANDS ──────────────────────────────────────────────────────────

use tauri::State;

#[derive(Serialize)]
pub struct ProviderInfo {
    pub id:           String,
    pub name:         String,
    pub model:        String,   // user's current configured model
    pub needs_key:    bool,
    pub price_in_1m:  f64,
    pub price_out_1m: f64,
}

/// List all providers with the user's currently configured model and pricing.
/// Model names come from providers.json — not from this source file.
#[tauri::command]
pub fn list_providers(state: State<ProviderState>) -> Vec<ProviderInfo> {
    let configs = state.configs.lock().unwrap();
    Provider::all()
        .iter()
        .map(|p| {
            let cfg = configs.get(p.id()).cloned().unwrap_or(ProviderConfig {
                model: String::new(), price_in_1m: 0.0, price_out_1m: 0.0,
            });
            ProviderInfo {
                id:           p.id().into(),
                name:         p.display_name().into(),
                model:        cfg.model,
                needs_key:    p.needs_api_key(),
                price_in_1m:  cfg.price_in_1m,
                price_out_1m: cfg.price_out_1m,
            }
        })
        .collect()
}

#[tauri::command]
pub fn get_active_provider(state: State<ProviderState>) -> String {
    state.active.lock().unwrap().id().into()
}

#[tauri::command]
pub fn set_active_provider(provider: String, state: State<ProviderState>) -> Result<(), String> {
    let p = Provider::from_str(&provider)
        .ok_or_else(|| format!("Unknown provider: {}", provider))?;
    *state.active.lock().unwrap() = p.clone();
    let configs = state.configs.lock().unwrap();
    let model = configs.get(p.id()).map(|c| c.model.clone()).unwrap_or_default();
    drop(configs);
    let mut cost = state.cost.lock().unwrap();
    cost.provider = p.display_name().into();
    cost.model    = model;
    Ok(())
}

/// Set the model for a provider and persist to providers.json immediately.
/// This is the primary way the user changes models — via Settings UI or onboarding.
#[tauri::command]
pub fn set_provider_model(
    provider: String,
    model:    String,
    state:    State<ProviderState>,
) -> Result<(), String> {
    let mut configs = state.configs.lock().unwrap();
    configs.entry(provider.clone()).or_insert(ProviderConfig {
        model: String::new(), price_in_1m: 0.0, price_out_1m: 0.0,
    }).model = model.clone();
    persist_configs(&state.app_config_dir, &configs);

    // Also update cost tracker if this is the active provider
    drop(configs);
    let active = state.active.lock().unwrap();
    if active.id() == provider {
        state.cost.lock().unwrap().model = model;
    }
    Ok(())
}

/// Update pricing for a provider (user corrects when provider changes rates).
#[tauri::command]
pub fn set_provider_pricing(
    provider:     String,
    price_in_1m:  f64,
    price_out_1m: f64,
    state:        State<ProviderState>,
) -> Result<(), String> {
    let mut configs = state.configs.lock().unwrap();
    let entry = configs.entry(provider).or_insert(ProviderConfig {
        model: String::new(), price_in_1m: 0.0, price_out_1m: 0.0,
    });
    entry.price_in_1m  = price_in_1m;
    entry.price_out_1m = price_out_1m;
    persist_configs(&state.app_config_dir, &configs);
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
    let configs  = state.configs.lock().unwrap();
    let cfg = configs.get(provider.id()).cloned().unwrap_or(ProviderConfig {
        model: String::new(), price_in_1m: 0.0, price_out_1m: 0.0,
    });
    drop(configs);
    let mut cost = state.cost.lock().unwrap();
    cost.record(tokens_in, tokens_out, &cfg);
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

// ─── LIVE MODEL FETCHING ─────────────────────────────────────────────────────
//
// Hits the provider's own /models endpoint so the user always sees the real,
// current list — no hardcoded strings, no stale data.

#[derive(Serialize, Clone)]
pub struct FetchedModel {
    pub id:   String,   // the string sent to the API (e.g. "claude-sonnet-4-6")
    pub name: String,   // human display name (e.g. "Claude Sonnet 4.6")
}

/// Fetch available models from the selected provider's API.
/// For Ollama: hits localhost:11434 — no key needed.
/// For cloud providers: uses the supplied API key.
#[tauri::command]
pub async fn fetch_provider_models(
    provider: String,
    api_key:  Option<String>,
) -> Result<Vec<FetchedModel>, String> {
    match provider.as_str() {
        "anthropic" => fetch_anthropic(&api_key).await,
        "openai"    => fetch_openai(&api_key).await,
        "gemini"    => fetch_gemini(&api_key).await,
        "ollama"    => fetch_ollama().await,
        _           => Err(format!("Unknown provider: {provider}")),
    }
}

async fn fetch_anthropic(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("Anthropic requires an API key")?;
    let resp = reqwest::Client::new()
        .get("https://api.anthropic.com/v1/models")
        .header("x-api-key", key)
        .header("anthropic-version", "2023-06-01")
        .send().await.map_err(|e| e.to_string())?
        .json::<serde_json::Value>().await.map_err(|e| e.to_string())?;

    let models = resp["data"].as_array().ok_or("Unexpected Anthropic response")?
        .iter()
        .filter_map(|m| {
            let id   = m["id"].as_str()?.to_string();
            let name = m["display_name"].as_str().unwrap_or(&id).to_string();
            Some(FetchedModel { id, name })
        })
        .collect();
    Ok(models)
}

async fn fetch_openai(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("OpenAI requires an API key")?;
    let resp = reqwest::Client::new()
        .get("https://api.openai.com/v1/models")
        .bearer_auth(key)
        .send().await.map_err(|e| e.to_string())?
        .json::<serde_json::Value>().await.map_err(|e| e.to_string())?;

    // Filter to chat-capable models only — OpenAI returns dozens of fine-tune / TTS / embedding models
    let useful = ["gpt-4", "gpt-3.5-turbo", "o1", "o3", "o4", "chatgpt"];
    let mut models: Vec<FetchedModel> = resp["data"].as_array()
        .ok_or("Unexpected OpenAI response")?
        .iter()
        .filter_map(|m| {
            let id = m["id"].as_str()?.to_string();
            let keep = useful.iter().any(|prefix| id.starts_with(prefix));
            keep.then(|| FetchedModel { name: id.clone(), id })
        })
        .collect();

    // Newest first
    models.sort_by(|a, b| b.id.cmp(&a.id));
    Ok(models)
}

async fn fetch_gemini(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("Gemini requires an API key")?;
    let url  = format!("https://generativelanguage.googleapis.com/v1beta/models?key={key}");
    let resp = reqwest::Client::new()
        .get(&url)
        .send().await.map_err(|e| e.to_string())?
        .json::<serde_json::Value>().await.map_err(|e| e.to_string())?;

    let models = resp["models"].as_array().ok_or("Unexpected Gemini response")?
        .iter()
        .filter_map(|m| {
            // Only keep models that support text generation
            let methods = m["supportedGenerationMethods"].as_array()?;
            let supports_generate = methods.iter()
                .any(|v| v.as_str() == Some("generateContent"));
            if !supports_generate { return None; }

            // Strip "models/" prefix from the name field → that's the model ID
            let raw  = m["name"].as_str()?;
            let id   = raw.strip_prefix("models/").unwrap_or(raw).to_string();
            let name = m["displayName"].as_str().unwrap_or(&id).to_string();
            Some(FetchedModel { id, name })
        })
        .collect();
    Ok(models)
}

async fn fetch_ollama() -> Result<Vec<FetchedModel>, String> {
    let resp = reqwest::Client::new()
        .get("http://localhost:11434/api/tags")
        .send().await
        .map_err(|_| "Ollama is not running on localhost:11434".to_string())?
        .json::<serde_json::Value>().await.map_err(|e| e.to_string())?;

    let models = resp["models"].as_array()
        .ok_or("No models found — have you pulled any? Run: ollama pull llama3.2")?
        .iter()
        .filter_map(|m| {
            let id = m["name"].as_str()?.to_string();
            Some(FetchedModel { name: id.clone(), id })
        })
        .collect();
    Ok(models)
}
