/// provider.rs — Multi-provider LLM routing
///
/// Model names and pricing live in ~/.config/signalos/providers.json after
/// first launch. Bootstrap defaults only keep first-run config usable; the UI
/// fetches provider model lists live so retired model IDs do not require a new
/// app release.
///
/// Adding a new model or a new provider = edit providers.json. No recompile.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

const DEFAULT_ANTHROPIC_MODEL: &str = "claude-sonnet-4-20250514";

// Wave 4 / G3-1: every HTTP call goes through this client so it gets a
// real timeout. The previous implementation created `reqwest::Client::new()`
// per call with no timeout — a hung provider could lock up an IPC for
// the OS TCP timeout (minutes), bricking the UI.
fn http() -> reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(60))
                .build()
                .expect("reqwest client build")
        })
        .clone()
}

// Wave 4 / G3-2: per-model max_tokens. Defaults raised from a global 8192.
// Models that support higher caps get them; older 4K-output models stay safe.
#[cfg(test)]
mod max_tokens_tests {
    use super::anthropic_max_tokens_for;

    #[test]
    fn opus_4_7_gets_64k() {
        assert_eq!(anthropic_max_tokens_for("claude-opus-4-7"), 64_000);
    }

    #[test]
    fn sonnet_4_gets_32k() {
        assert_eq!(anthropic_max_tokens_for("claude-sonnet-4-20250514"), 32_000);
    }

    #[test]
    fn haiku_gets_16k() {
        assert_eq!(anthropic_max_tokens_for("claude-haiku-4-5"), 16_000);
    }

    #[test]
    fn unknown_model_gets_8k() {
        assert_eq!(anthropic_max_tokens_for("claude-3-old"), 8_192);
        assert_eq!(anthropic_max_tokens_for("gpt-4"), 8_192);
    }

    #[test]
    fn case_insensitive() {
        assert_eq!(anthropic_max_tokens_for("CLAUDE-OPUS-4-7"), 64_000);
    }
}

#[cfg(test)]
mod provider_tests {
    use super::Provider;

    #[test]
    fn from_str_round_trip() {
        for p in Provider::all() {
            let id = p.id();
            let parsed = Provider::from_str(id).expect("from_str should accept its own id");
            assert_eq!(parsed.id(), id);
        }
    }

    #[test]
    fn from_str_is_case_insensitive() {
        assert!(Provider::from_str("ANTHROPIC").is_some());
        assert!(Provider::from_str("Anthropic").is_some());
        assert!(Provider::from_str("openai").is_some());
    }

    #[test]
    fn ollama_does_not_need_api_key() {
        assert!(!Provider::Ollama.needs_api_key());
    }

    #[test]
    fn all_others_need_api_key() {
        for p in Provider::all() {
            if matches!(p, Provider::Ollama) {
                continue;
            }
            assert!(p.needs_api_key(), "{:?} should need a key", p);
        }
    }
}

fn anthropic_max_tokens_for(model: &str) -> u64 {
    let m = model.to_ascii_lowercase();
    if m.contains("opus-4-7") {
        64_000
    } else if m.contains("opus") || m.contains("sonnet-4") {
        32_000
    } else if m.contains("haiku") {
        16_000
    } else {
        8_192
    }
}

// ─── PROVIDER ID ─────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Anthropic,
    OpenAI,
    Gemini,
    Qwen,
    Ollama,
    OpenRouter,
    DeepSeek,
    Mistral,
    Groq,
    Cerebras,
    Together,
    XAI,
}

impl Provider {
    #[allow(clippy::should_implement_trait)]
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "anthropic" => Some(Self::Anthropic),
            "openai" => Some(Self::OpenAI),
            "gemini" => Some(Self::Gemini),
            "qwen" => Some(Self::Qwen),
            "ollama" => Some(Self::Ollama),
            "openrouter" => Some(Self::OpenRouter),
            "deepseek" => Some(Self::DeepSeek),
            "mistral" => Some(Self::Mistral),
            "groq" => Some(Self::Groq),
            "cerebras" => Some(Self::Cerebras),
            "together" => Some(Self::Together),
            "xai" => Some(Self::XAI),
            _ => None,
        }
    }

    pub fn id(&self) -> &str {
        match self {
            Self::Anthropic => "anthropic",
            Self::OpenAI => "openai",
            Self::Gemini => "gemini",
            Self::Qwen => "qwen",
            Self::Ollama => "ollama",
            Self::OpenRouter => "openrouter",
            Self::DeepSeek => "deepseek",
            Self::Mistral => "mistral",
            Self::Groq => "groq",
            Self::Cerebras => "cerebras",
            Self::Together => "together",
            Self::XAI => "xai",
        }
    }

    pub fn display_name(&self) -> &str {
        match self {
            Self::Anthropic => "Anthropic Claude",
            Self::OpenAI => "OpenAI",
            Self::Gemini => "Google Gemini",
            Self::Qwen => "Qwen",
            Self::Ollama => "Ollama (local)",
            Self::OpenRouter => "OpenRouter",
            Self::DeepSeek => "DeepSeek",
            Self::Mistral => "Mistral",
            Self::Groq => "Groq",
            Self::Cerebras => "Cerebras",
            Self::Together => "Together AI",
            Self::XAI => "xAI",
        }
    }

    pub fn api_base(&self) -> &str {
        match self {
            Self::Anthropic => "https://api.anthropic.com",
            Self::OpenAI => "https://api.openai.com",
            Self::Gemini => "https://generativelanguage.googleapis.com",
            Self::Qwen => "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            Self::Ollama => "http://localhost:11434",
            Self::OpenRouter => "https://openrouter.ai/api/v1",
            Self::DeepSeek => "https://api.deepseek.com",
            Self::Mistral => "https://api.mistral.ai/v1",
            Self::Groq => "https://api.groq.com/openai/v1",
            Self::Cerebras => "https://api.cerebras.ai/v1",
            Self::Together => "https://api.together.xyz/v1",
            Self::XAI => "https://api.x.ai/v1",
        }
    }

    /// Whether this provider requires an API key (Ollama is local — no key needed)
    pub fn needs_api_key(&self) -> bool {
        !matches!(self, Self::Ollama)
    }

    pub fn all() -> &'static [Provider] {
        &[
            Self::Anthropic,
            Self::OpenAI,
            Self::Gemini,
            Self::Qwen,
            Self::Ollama,
            Self::OpenRouter,
            Self::DeepSeek,
            Self::Mistral,
            Self::Groq,
            Self::Cerebras,
            Self::Together,
            Self::XAI,
        ]
    }
}

// ─── PER-PROVIDER CONFIG (user-editable, persisted to disk) ──────────────────

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ProviderConfig {
    /// The model string sent to the API — user sets this, we never hardcode it.
    /// Ollama: whatever the user has pulled (e.g. "llama3.2", "mistral", "phi4").
    /// Cloud providers: whatever the user wants (e.g. "claude-sonnet-4-20250514", "gpt-4o-mini").
    pub model: String,

    /// USD per 1M input tokens. User can update when providers change pricing.
    pub price_in_1m: f64,

    /// USD per 1M output tokens.
    pub price_out_1m: f64,
}

impl ProviderConfig {
    /// Wave 1 / G0-4: refreshed defaults to current-generation, cost-efficient
    /// models per provider. Anthropic uses the stable Sonnet 4 snapshot API ID;
    /// OpenAI defaults to gpt-4o-mini (10x cheaper than gpt-4o for similar
    /// Builder reliability); Gemini defaults to 2.5-flash. Users can upgrade
    /// per provider via the model picker.
    fn defaults() -> HashMap<String, ProviderConfig> {
        [
            (
                "anthropic",
                ProviderConfig {
                    model: DEFAULT_ANTHROPIC_MODEL.into(),
                    price_in_1m: 3.00,
                    price_out_1m: 15.00,
                },
            ),
            (
                "openai",
                ProviderConfig {
                    model: "gpt-4o-mini".into(),
                    price_in_1m: 0.15,
                    price_out_1m: 0.60,
                },
            ),
            (
                "gemini",
                ProviderConfig {
                    model: "gemini-2.5-flash".into(),
                    price_in_1m: 0.075,
                    price_out_1m: 0.30,
                },
            ),
            (
                "ollama",
                ProviderConfig {
                    model: "".into(), // blank — user fills in their pulled model
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "qwen",
                ProviderConfig {
                    model: "qwen-plus".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "openrouter",
                ProviderConfig {
                    model: "qwen/qwen-plus".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "deepseek",
                ProviderConfig {
                    model: "deepseek-chat".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "mistral",
                ProviderConfig {
                    model: "mistral-large-latest".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "groq",
                ProviderConfig {
                    model: "llama-3.3-70b-versatile".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "cerebras",
                ProviderConfig {
                    model: "llama-4-scout-17b-16e-instruct".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "together",
                ProviderConfig {
                    model: "meta-llama/Llama-3.3-70B-Instruct-Turbo".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
            (
                "xai",
                ProviderConfig {
                    model: "grok-4".into(),
                    price_in_1m: 0.00,
                    price_out_1m: 0.00,
                },
            ),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect()
    }
}

// ─── CONFIG FILE ─────────────────────────────────────────────────────────────

pub fn load_provider_configs(app_config_dir: &std::path::Path) -> HashMap<String, ProviderConfig> {
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

fn persist_configs(app_config_dir: &std::path::Path, configs: &HashMap<String, ProviderConfig>) {
    let _ = std::fs::create_dir_all(app_config_dir);
    if let Ok(json) = serde_json::to_string_pretty(configs) {
        let _ = std::fs::write(app_config_dir.join("providers.json"), json);
    }
}

// ─── COST ACCUMULATOR ────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct CostAccumulator {
    pub tokens_in: u64,
    pub tokens_out: u64,
    pub session_usd: f64,
    pub monthly_usd: f64,
    pub budget_usd: f64,
    pub provider: String,
    pub model: String,
}

impl CostAccumulator {
    pub fn record(&mut self, tokens_in: u64, tokens_out: u64, cfg: &ProviderConfig) -> f64 {
        self.tokens_in += tokens_in;
        self.tokens_out += tokens_out;
        let cost = provider_cost_usd(tokens_in, tokens_out, cfg);
        self.session_usd += cost;
        self.monthly_usd += cost;
        cost
    }

    pub fn over_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd
    }

    pub fn near_budget(&self) -> bool {
        self.budget_usd > 0.0 && self.monthly_usd >= self.budget_usd * 0.8
    }
}

// ─── GLOBAL STATE ────────────────────────────────────────────────────────────

const ACTIVE_PROVIDER_FILE: &str = "active-provider.txt";

pub struct ProviderState {
    pub active: Mutex<Provider>,
    pub cost: Mutex<CostAccumulator>,
    pub configs: Mutex<HashMap<String, ProviderConfig>>,
    pub app_config_dir: PathBuf,
}

impl ProviderState {
    pub fn new(app_config_dir: PathBuf) -> Self {
        let configs = load_provider_configs(&app_config_dir);
        let active_provider = load_active_provider(&app_config_dir);
        let active_model = configs
            .get(active_provider.id())
            .map(|c| c.model.clone())
            .unwrap_or_default();

        Self {
            active: Mutex::new(active_provider.clone()),
            cost: Mutex::new(CostAccumulator {
                budget_usd: 10.0,
                provider: active_provider.display_name().into(),
                model: active_model,
                ..Default::default()
            }),
            configs: Mutex::new(configs),
            app_config_dir,
        }
    }
}

fn load_active_provider(app_config_dir: &std::path::Path) -> Provider {
    let path = app_config_dir.join(ACTIVE_PROVIDER_FILE);
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| Provider::from_str(raw.trim()))
        .unwrap_or(Provider::Anthropic)
}

fn persist_active_provider(app_config_dir: &std::path::Path, provider: &Provider) {
    let _ = std::fs::create_dir_all(app_config_dir);
    let _ = std::fs::write(app_config_dir.join(ACTIVE_PROVIDER_FILE), provider.id());
}

// ─── TAURI COMMANDS ──────────────────────────────────────────────────────────

use futures_util::StreamExt;
use tauri::{AppHandle, Emitter, State};
use tokio::io::AsyncBufReadExt;
use tokio_util::io::StreamReader;

#[derive(Serialize)]
pub struct ProviderInfo {
    pub id: String,
    pub name: String,
    pub model: String, // user's current configured model
    pub needs_key: bool,
    pub price_in_1m: f64,
    pub price_out_1m: f64,
}

#[derive(Serialize)]
pub struct ProviderConnectionStatus {
    pub ok: bool,
    pub message: String,
    pub model_count: usize,
}

#[derive(Serialize)]
pub struct ProviderChatResponse {
    pub text: String,
    pub tokens_in: Option<u64>,
    pub tokens_out: Option<u64>,
    pub provider: String,
    pub model: String,
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
                model: String::new(),
                price_in_1m: 0.0,
                price_out_1m: 0.0,
            });
            ProviderInfo {
                id: p.id().into(),
                name: p.display_name().into(),
                model: cfg.model,
                needs_key: p.needs_api_key(),
                price_in_1m: cfg.price_in_1m,
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
    let p =
        Provider::from_str(&provider).ok_or_else(|| format!("Unknown provider: {}", provider))?;
    *state.active.lock().unwrap() = p.clone();
    persist_active_provider(&state.app_config_dir, &p);
    let configs = state.configs.lock().unwrap();
    let model = configs
        .get(p.id())
        .map(|c| c.model.clone())
        .unwrap_or_default();
    drop(configs);
    let mut cost = state.cost.lock().unwrap();
    cost.provider = p.display_name().into();
    cost.model = model;
    Ok(())
}

/// Set the model for a provider and persist to providers.json immediately.
/// This is the primary way the user changes models — via Settings UI or onboarding.
#[tauri::command]
pub fn set_provider_model(
    provider: String,
    model: String,
    state: State<ProviderState>,
) -> Result<(), String> {
    let mut configs = state.configs.lock().unwrap();
    configs
        .entry(provider.clone())
        .or_insert(ProviderConfig {
            model: String::new(),
            price_in_1m: 0.0,
            price_out_1m: 0.0,
        })
        .model = model.clone();
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
    provider: String,
    price_in_1m: f64,
    price_out_1m: f64,
    state: State<ProviderState>,
) -> Result<(), String> {
    let mut configs = state.configs.lock().unwrap();
    let entry = configs.entry(provider).or_insert(ProviderConfig {
        model: String::new(),
        price_in_1m: 0.0,
        price_out_1m: 0.0,
    });
    entry.price_in_1m = price_in_1m;
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
    tokens_in: u64,
    tokens_out: u64,
    state: State<ProviderState>,
    workspace: State<crate::ipc::WorkspaceState>,
) -> CostAccumulator {
    let provider = state.active.lock().unwrap().clone();
    let configs = state.configs.lock().unwrap();
    let cfg = configs
        .get(provider.id())
        .cloned()
        .unwrap_or(ProviderConfig {
            model: String::new(),
            price_in_1m: 0.0,
            price_out_1m: 0.0,
        });
    drop(configs);
    let mut cost = state.cost.lock().unwrap();
    let cost_usd = cost.record(tokens_in, tokens_out, &cfg);
    append_usage_ledger_best_effort(
        active_workspace_path(&workspace),
        provider.id(),
        &cfg.model,
        "manual-record",
        tokens_in,
        tokens_out,
        configured_cost_usd(provider.id(), &cfg, cost_usd),
    );
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
pub async fn test_provider_connection(
    provider: String,
    api_key: Option<String>,
    model: Option<String>,
    state: State<'_, ProviderState>,
) -> Result<ProviderConnectionStatus, String> {
    // Wave 1 / G0-3: real chat ping. The previous implementation called
    // /v1/models which only verified the key could *list* models — not
    // that it could actually chat. A real one-token round-trip is the
    // only thing that proves the connection works for the user's flow.
    let provider_id = provider.trim().to_lowercase();
    let provider_enum =
        Provider::from_str(&provider_id).ok_or_else(|| format!("Unknown provider: {provider}"))?;

    // Resolve the key: explicit arg wins; otherwise pull from keychain.
    let key = match api_key {
        Some(k) if !k.trim().is_empty() => Some(k.trim().to_string()),
        _ if provider_enum.needs_api_key() => {
            crate::keychain::get_api_key(provider_enum.id().to_string())?
        }
        _ => None,
    };

    // Resolve the model: explicit arg wins; otherwise pull from configs.
    let selected_model = match model {
        Some(m) if !m.trim().is_empty() => m.trim().to_string(),
        _ => {
            let configs = state.configs.lock().unwrap();
            configs
                .get(provider_enum.id())
                .map(|c| c.model.trim().to_string())
                .unwrap_or_default()
        }
    };
    if selected_model.is_empty() {
        return Err(format!(
            "No model configured for {}. Fetch models and pick one, then test again.",
            provider_enum.display_name()
        ));
    }

    // Single-token chat ping. Reuses the production chat path so this
    // function genuinely proves the path the user will take next works.
    let message = "ping";
    let response = match provider_enum {
        Provider::Anthropic => chat_anthropic(&key, &selected_model, message).await?,
        Provider::OpenAI => {
            chat_openai(&key, Provider::OpenAI.api_base(), &selected_model, message).await?
        }
        Provider::Gemini => chat_gemini(&key, &selected_model, message).await?,
        Provider::Ollama => chat_ollama(&selected_model, message).await?,
        Provider::Qwen => {
            chat_openai(&key, Provider::Qwen.api_base(), &selected_model, message).await?
        }
        Provider::OpenRouter => {
            chat_openai(
                &key,
                Provider::OpenRouter.api_base(),
                &selected_model,
                message,
            )
            .await?
        }
        Provider::DeepSeek => {
            chat_openai(
                &key,
                Provider::DeepSeek.api_base(),
                &selected_model,
                message,
            )
            .await?
        }
        Provider::Mistral => {
            chat_openai(&key, Provider::Mistral.api_base(), &selected_model, message).await?
        }
        Provider::Groq => {
            chat_openai(&key, Provider::Groq.api_base(), &selected_model, message).await?
        }
        Provider::Cerebras => {
            chat_openai(
                &key,
                Provider::Cerebras.api_base(),
                &selected_model,
                message,
            )
            .await?
        }
        Provider::Together => {
            chat_openai(
                &key,
                Provider::Together.api_base(),
                &selected_model,
                message,
            )
            .await?
        }
        Provider::XAI => {
            chat_openai(&key, Provider::XAI.api_base(), &selected_model, message).await?
        }
    };

    let tokens_out = response.tokens_out.unwrap_or(0);
    let truncated_text: String = response.text.trim().chars().take(80).collect();
    Ok(ProviderConnectionStatus {
        ok: true,
        message: format!(
            "{} replied (model {}, {} output tokens): {}",
            provider_enum.display_name(),
            selected_model,
            tokens_out,
            if truncated_text.is_empty() {
                "(no text)".to_string()
            } else {
                truncated_text
            }
        ),
        model_count: 0,
    })
}

// ─── STREAMING CHAT (§11.5/9) ────────────────────────────────────────────────
//
// Stream provider responses as `chat:token` events. The frontend listens via
// onChatToken(streamId, cb) and renders deltas as they arrive. The function
// returns the full text + token counts so callers don't need to reassemble.

// Send-safe emitter used by the streaming chat helpers. Holds the app
// handle plus the streamId/provider/model so each `delta()` call can emit
// without capturing non-Send state from the parent async fn's environment.
struct StreamEmitter {
    app: AppHandle,
    stream_id: String,
    provider: String,
    model: String,
}

impl StreamEmitter {
    fn delta(&self, text: &str) {
        let _ = self.app.emit(
            "chat:token",
            ChatTokenEvent {
                stream_id: self.stream_id.clone(),
                kind: "delta".into(),
                delta: text.to_string(),
                provider: self.provider.clone(),
                model: self.model.clone(),
            },
        );
    }
}

#[derive(Serialize, Clone)]
pub struct ChatTokenEvent {
    pub stream_id: String,
    pub kind: String, // "delta" | "done" | "error"
    pub delta: String,
    pub provider: String,
    pub model: String,
}

#[tauri::command]
pub async fn send_provider_message_stream(
    stream_id: String,
    provider: String,
    model: Option<String>,
    message: String,
    app: AppHandle,
    state: State<'_, ProviderState>,
    workspace: State<'_, crate::ipc::WorkspaceState>,
) -> Result<ProviderChatResponse, String> {
    let provider_id = provider.trim().to_lowercase();
    let provider_enum =
        Provider::from_str(&provider_id).ok_or_else(|| format!("Unknown provider: {provider}"))?;
    let selected_model = resolve_model(&state, provider_enum.id(), model)?;
    let key = if provider_enum.needs_api_key() {
        crate::keychain::get_api_key(provider_enum.id().to_string())?
    } else {
        None
    };

    let emitter = StreamEmitter {
        app: app.clone(),
        stream_id: stream_id.clone(),
        provider: provider_enum.id().to_string(),
        model: selected_model.clone(),
    };

    let result: Result<(String, Option<u64>, Option<u64>), String> = match provider_enum {
        Provider::Anthropic => stream_anthropic(&key, &selected_model, &message, &emitter).await,
        Provider::OpenAI => {
            stream_openai_compatible(
                &key,
                Provider::OpenAI.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::Gemini => stream_gemini(&key, &selected_model, &message, &emitter).await,
        Provider::Ollama => stream_ollama(&selected_model, &message, &emitter).await,
        Provider::Qwen => {
            stream_openai_compatible(
                &key,
                Provider::Qwen.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::OpenRouter => {
            stream_openai_compatible(
                &key,
                Provider::OpenRouter.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::DeepSeek => {
            stream_openai_compatible(
                &key,
                Provider::DeepSeek.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::Mistral => {
            stream_openai_compatible(
                &key,
                Provider::Mistral.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::Groq => {
            stream_openai_compatible(
                &key,
                Provider::Groq.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::Cerebras => {
            stream_openai_compatible(
                &key,
                Provider::Cerebras.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::Together => {
            stream_openai_compatible(
                &key,
                Provider::Together.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
        Provider::XAI => {
            stream_openai_compatible(
                &key,
                Provider::XAI.api_base(),
                &selected_model,
                &message,
                &emitter,
            )
            .await
        }
    };

    match result {
        Ok((text, tokens_in, tokens_out)) => {
            let _ = app.emit(
                "chat:token",
                ChatTokenEvent {
                    stream_id: stream_id.clone(),
                    kind: "done".into(),
                    delta: String::new(),
                    provider: provider_enum.id().to_string(),
                    model: selected_model.clone(),
                },
            );
            record_chat_cost(
                &state,
                Some(&workspace),
                provider_enum.id(),
                "chat-stream",
                tokens_in,
                tokens_out,
            );
            Ok(ProviderChatResponse {
                text,
                tokens_in,
                tokens_out,
                provider: provider_enum.id().to_string(),
                model: selected_model,
            })
        }
        Err(e) => {
            let _ = app.emit(
                "chat:token",
                ChatTokenEvent {
                    stream_id,
                    kind: "error".into(),
                    delta: e.clone(),
                    provider: provider_enum.id().to_string(),
                    model: selected_model,
                },
            );
            Err(e)
        }
    }
}

async fn stream_anthropic(
    api_key: &Option<String>,
    model: &str,
    message: &str,
    emit: &StreamEmitter,
) -> Result<(String, Option<u64>, Option<u64>), String> {
    let key = api_key.as_deref().ok_or("Anthropic requires an API key")?;
    let body = serde_json::json!({
        "model": model,
        "max_tokens": anthropic_max_tokens_for(model),
        "stream": true,
        "messages": [{ "role": "user", "content": message }],
    });
    let resp = http()
        .post("https://api.anthropic.com/v1/messages")
        .header("x-api-key", key)
        .header("anthropic-version", "2023-06-01")
        .header("accept", "text/event-stream")
        .json(&body)
        .send()
        .await
        .map_err(|e| provider_request_error("Anthropic", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!(
            "Anthropic stream HTTP {}: {}",
            status.as_u16(),
            body
        ));
    }
    let mut text = String::new();
    let mut tokens_in: Option<u64> = None;
    let mut tokens_out: Option<u64> = None;
    let stream = resp.bytes_stream();
    let reader = StreamReader::new(stream.map(|r| r.map_err(std::io::Error::other)));
    let mut lines = reader.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if !line.starts_with("data: ") {
            continue;
        }
        let payload = &line[6..];
        if payload == "[DONE]" {
            break;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(payload) else {
            continue;
        };
        let kind = value.get("type").and_then(|v| v.as_str()).unwrap_or("");
        if kind == "content_block_delta" {
            if let Some(delta) = value
                .get("delta")
                .and_then(|v| v.get("text"))
                .and_then(|v| v.as_str())
            {
                text.push_str(delta);
                emit.delta(delta);
            }
        } else if kind == "message_start" {
            if let Some(input) = value
                .get("message")
                .and_then(|v| v.get("usage"))
                .and_then(|v| v.get("input_tokens"))
                .and_then(|v| v.as_u64())
            {
                tokens_in = Some(input);
            }
        } else if kind == "message_delta" {
            if let Some(out) = value
                .get("usage")
                .and_then(|v| v.get("output_tokens"))
                .and_then(|v| v.as_u64())
            {
                tokens_out = Some(out);
            }
        }
    }
    Ok((text, tokens_in, tokens_out))
}

async fn stream_openai_compatible(
    api_key: &Option<String>,
    base_url: &str,
    model: &str,
    message: &str,
    emit: &StreamEmitter,
) -> Result<(String, Option<u64>, Option<u64>), String> {
    let key = api_key
        .as_deref()
        .ok_or("This provider requires an API key")?;
    let url = format!("{}/chat/completions", base_url.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": model,
        "stream": true,
        "stream_options": { "include_usage": true },
        "messages": [{ "role": "user", "content": message }],
    });
    let resp = http()
        .post(url)
        .bearer_auth(key)
        .header("accept", "text/event-stream")
        .json(&body)
        .send()
        .await
        .map_err(|e| provider_request_error("AI provider", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!(
            "Provider stream HTTP {}: {}",
            status.as_u16(),
            body
        ));
    }
    let mut text = String::new();
    let mut tokens_in: Option<u64> = None;
    let mut tokens_out: Option<u64> = None;
    let stream = resp.bytes_stream();
    let reader = StreamReader::new(stream.map(|r| r.map_err(std::io::Error::other)));
    let mut lines = reader.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if !line.starts_with("data: ") {
            continue;
        }
        let payload = &line[6..];
        if payload == "[DONE]" {
            break;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(payload) else {
            continue;
        };
        if let Some(choices) = value.get("choices").and_then(|v| v.as_array()) {
            if let Some(first) = choices.first() {
                if let Some(delta) = first
                    .get("delta")
                    .and_then(|v| v.get("content"))
                    .and_then(|v| v.as_str())
                {
                    text.push_str(delta);
                    emit.delta(delta);
                }
            }
        }
        if let Some(usage) = value.get("usage") {
            tokens_in = usage
                .get("prompt_tokens")
                .and_then(|v| v.as_u64())
                .or(tokens_in);
            tokens_out = usage
                .get("completion_tokens")
                .and_then(|v| v.as_u64())
                .or(tokens_out);
        }
    }
    Ok((text, tokens_in, tokens_out))
}

async fn stream_gemini(
    api_key: &Option<String>,
    model: &str,
    message: &str,
    emit: &StreamEmitter,
) -> Result<(String, Option<u64>, Option<u64>), String> {
    let key = api_key.as_deref().ok_or("Gemini requires an API key")?;
    let url = format!(
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={key}"
    );
    let body = serde_json::json!({
        "contents": [{ "parts": [{ "text": message }] }],
    });
    let resp = http()
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| provider_request_error("Gemini", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("Gemini stream HTTP {}: {}", status.as_u16(), body));
    }
    let mut text = String::new();
    let mut tokens_in: Option<u64> = None;
    let mut tokens_out: Option<u64> = None;
    let stream = resp.bytes_stream();
    let reader = StreamReader::new(stream.map(|r| r.map_err(std::io::Error::other)));
    let mut lines = reader.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if !line.starts_with("data: ") {
            continue;
        }
        let payload = &line[6..];
        let Ok(value) = serde_json::from_str::<serde_json::Value>(payload) else {
            continue;
        };
        if let Some(parts) = value
            .get("candidates")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first())
            .and_then(|c| c.get("content"))
            .and_then(|c| c.get("parts"))
            .and_then(|p| p.as_array())
        {
            for part in parts {
                if let Some(t) = part.get("text").and_then(|v| v.as_str()) {
                    text.push_str(t);
                    emit.delta(t);
                }
            }
        }
        if let Some(meta) = value.get("usageMetadata") {
            tokens_in = meta
                .get("promptTokenCount")
                .and_then(|v| v.as_u64())
                .or(tokens_in);
            tokens_out = meta
                .get("candidatesTokenCount")
                .and_then(|v| v.as_u64())
                .or(tokens_out);
        }
    }
    Ok((text, tokens_in, tokens_out))
}

async fn stream_ollama(
    model: &str,
    message: &str,
    emit: &StreamEmitter,
) -> Result<(String, Option<u64>, Option<u64>), String> {
    let body = serde_json::json!({
        "model": model,
        "prompt": message,
        "stream": true,
    });
    let resp = http()
        .post("http://localhost:11434/api/generate")
        .json(&body)
        .send()
        .await
        .map_err(|e| provider_request_error("Ollama", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("Ollama stream HTTP {}: {}", status.as_u16(), body));
    }
    let mut text = String::new();
    let mut tokens_in: Option<u64> = None;
    let mut tokens_out: Option<u64> = None;
    let stream = resp.bytes_stream();
    let reader = StreamReader::new(stream.map(|r| r.map_err(std::io::Error::other)));
    let mut lines = reader.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        if let Some(delta) = value.get("response").and_then(|v| v.as_str()) {
            text.push_str(delta);
            emit.delta(delta);
        }
        if value.get("done").and_then(|v| v.as_bool()).unwrap_or(false) {
            tokens_in = value.get("prompt_eval_count").and_then(|v| v.as_u64());
            tokens_out = value.get("eval_count").and_then(|v| v.as_u64());
            break;
        }
    }
    Ok((text, tokens_in, tokens_out))
}

#[tauri::command]
pub async fn send_provider_message(
    provider: String,
    model: Option<String>,
    message: String,
    state: State<'_, ProviderState>,
    workspace: State<'_, crate::ipc::WorkspaceState>,
) -> Result<ProviderChatResponse, String> {
    let provider_id = provider.trim().to_lowercase();
    let provider_enum =
        Provider::from_str(&provider_id).ok_or_else(|| format!("Unknown provider: {provider}"))?;
    let selected_model = resolve_model(&state, provider_enum.id(), model)?;
    let key = if provider_enum.needs_api_key() {
        crate::keychain::get_api_key(provider_enum.id().to_string())?
    } else {
        None
    };

    let response = match provider_enum {
        Provider::Anthropic => chat_anthropic(&key, &selected_model, &message).await?,
        Provider::OpenAI => {
            chat_openai(&key, Provider::OpenAI.api_base(), &selected_model, &message).await?
        }
        Provider::Gemini => chat_gemini(&key, &selected_model, &message).await?,
        Provider::Ollama => chat_ollama(&selected_model, &message).await?,
        Provider::Qwen => {
            chat_openai(&key, Provider::Qwen.api_base(), &selected_model, &message).await?
        }
        Provider::OpenRouter => {
            chat_openai(
                &key,
                Provider::OpenRouter.api_base(),
                &selected_model,
                &message,
            )
            .await?
        }
        Provider::DeepSeek => {
            chat_openai(
                &key,
                Provider::DeepSeek.api_base(),
                &selected_model,
                &message,
            )
            .await?
        }
        Provider::Mistral => {
            chat_openai(
                &key,
                Provider::Mistral.api_base(),
                &selected_model,
                &message,
            )
            .await?
        }
        Provider::Groq => {
            chat_openai(&key, Provider::Groq.api_base(), &selected_model, &message).await?
        }
        Provider::Cerebras => {
            chat_openai(
                &key,
                Provider::Cerebras.api_base(),
                &selected_model,
                &message,
            )
            .await?
        }
        Provider::Together => {
            chat_openai(
                &key,
                Provider::Together.api_base(),
                &selected_model,
                &message,
            )
            .await?
        }
        Provider::XAI => {
            chat_openai(&key, Provider::XAI.api_base(), &selected_model, &message).await?
        }
    };

    record_chat_cost(
        &state,
        Some(&workspace),
        provider_enum.id(),
        "chat",
        response.tokens_in,
        response.tokens_out,
    );
    Ok(response)
}

// ─── LIVE MODEL FETCHING ─────────────────────────────────────────────────────
//
// Hits the provider's own /models endpoint so the user always sees the real,
// current list — no hardcoded strings, no stale data.

#[derive(Serialize, Clone)]
pub struct FetchedModel {
    pub id: String,   // the string sent to the API (e.g. "claude-sonnet-4-20250514")
    pub name: String, // human display name returned by the provider
}

/// Fetch available models from the selected provider's API.
/// For Ollama: hits localhost:11434 — no key needed.
/// For cloud providers: uses the supplied API key.
#[tauri::command]
pub async fn fetch_provider_models(
    provider: String,
    api_key: Option<String>,
) -> Result<Vec<FetchedModel>, String> {
    let provider_id = provider.trim().to_lowercase();
    let resolved_api_key = match api_key {
        Some(key) if !key.trim().is_empty() => Some(key.trim().to_string()),
        _ if provider_id != "ollama" => crate::keychain::get_api_key(provider_id.clone())?,
        _ => None,
    };

    match provider_id.as_str() {
        "anthropic" => fetch_anthropic(&resolved_api_key).await,
        "openai" => fetch_openai(&resolved_api_key).await,
        "gemini" => fetch_gemini(&resolved_api_key).await,
        "qwen" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::Qwen.api_base(),
                "Qwen",
                &["qwen"],
            )
            .await
        }
        "ollama" => fetch_ollama().await,
        "openrouter" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::OpenRouter.api_base(),
                "OpenRouter",
                &[],
            )
            .await
        }
        "deepseek" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::DeepSeek.api_base(),
                "DeepSeek",
                &["deepseek"],
            )
            .await
        }
        "mistral" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::Mistral.api_base(),
                "Mistral",
                &["mistral"],
            )
            .await
        }
        "groq" => {
            fetch_openai_compatible(&resolved_api_key, Provider::Groq.api_base(), "Groq", &[]).await
        }
        "cerebras" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::Cerebras.api_base(),
                "Cerebras",
                &[],
            )
            .await
        }
        "together" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::Together.api_base(),
                "Together AI",
                &[],
            )
            .await
        }
        "xai" => {
            fetch_openai_compatible(
                &resolved_api_key,
                Provider::XAI.api_base(),
                "xAI",
                &["grok"],
            )
            .await
        }
        _ => Err(format!("Unknown provider: {provider}")),
    }
}

async fn fetch_anthropic(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("Anthropic requires an API key")?;
    let resp = fetch_json(
        "Anthropic",
        http()
            .get("https://api.anthropic.com/v1/models")
            .header("x-api-key", key)
            .header("anthropic-version", "2023-06-01"),
    )
    .await?;

    let models = resp["data"]
        .as_array()
        .ok_or("Unexpected Anthropic response")?
        .iter()
        .filter_map(|m| {
            let id = m["id"].as_str()?.to_string();
            let name = m["display_name"].as_str().unwrap_or(&id).to_string();
            Some(FetchedModel { id, name })
        })
        .collect();
    Ok(models)
}

async fn fetch_openai(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("OpenAI requires an API key")?;
    let resp = fetch_json(
        "OpenAI",
        http()
            .get("https://api.openai.com/v1/models")
            .bearer_auth(key),
    )
    .await?;

    // Filter to chat-capable models only — OpenAI returns dozens of fine-tune / TTS / embedding models
    let useful = ["gpt-4", "gpt-3.5-turbo", "o1", "o3", "o4", "chatgpt"];
    let mut models: Vec<FetchedModel> = resp["data"]
        .as_array()
        .ok_or("Unexpected OpenAI response")?
        .iter()
        .filter_map(|m| {
            let id = m["id"].as_str()?.to_string();
            let keep = useful.iter().any(|prefix| id.starts_with(prefix));
            keep.then(|| FetchedModel {
                name: id.clone(),
                id,
            })
        })
        .collect();

    // Newest first
    models.sort_by(|a, b| b.id.cmp(&a.id));
    Ok(models)
}

async fn fetch_gemini(api_key: &Option<String>) -> Result<Vec<FetchedModel>, String> {
    let key = api_key.as_deref().ok_or("Gemini requires an API key")?;
    let url = format!("https://generativelanguage.googleapis.com/v1beta/models?key={key}");
    let resp = fetch_json("Gemini", http().get(&url)).await?;

    let models = resp["models"]
        .as_array()
        .ok_or("Unexpected Gemini response")?
        .iter()
        .filter_map(|m| {
            // Only keep models that support text generation
            let methods = m["supportedGenerationMethods"].as_array()?;
            let supports_generate = methods
                .iter()
                .any(|v| v.as_str() == Some("generateContent"));
            if !supports_generate {
                return None;
            }

            // Strip "models/" prefix from the name field → that's the model ID
            let raw = m["name"].as_str()?;
            let id = raw.strip_prefix("models/").unwrap_or(raw).to_string();
            let name = m["displayName"].as_str().unwrap_or(&id).to_string();
            Some(FetchedModel { id, name })
        })
        .collect();
    Ok(models)
}

async fn fetch_ollama() -> Result<Vec<FetchedModel>, String> {
    let resp = http()
        .get("http://localhost:11434/api/tags")
        .send()
        .await
        .map_err(|_| "Ollama is not running on localhost:11434".to_string())?
        .json::<serde_json::Value>()
        .await
        .map_err(|e| e.to_string())?;

    let models = resp["models"]
        .as_array()
        .ok_or("No models found — have you pulled any? Run: ollama pull llama3.2")?
        .iter()
        .filter_map(|m| {
            let id = m["name"].as_str()?.to_string();
            Some(FetchedModel {
                name: id.clone(),
                id,
            })
        })
        .collect();
    Ok(models)
}

async fn fetch_openai_compatible(
    api_key: &Option<String>,
    base_url: &str,
    display_name: &str,
    useful_prefixes: &[&str],
) -> Result<Vec<FetchedModel>, String> {
    let key = api_key
        .as_deref()
        .ok_or_else(|| format!("{display_name} requires an API key"))?;
    let url = format!("{}/models", base_url.trim_end_matches('/'));
    let resp = fetch_json(display_name, http().get(url).bearer_auth(key)).await?;

    let mut models: Vec<FetchedModel> = resp["data"]
        .as_array()
        .ok_or_else(|| format!("Unexpected {display_name} response"))?
        .iter()
        .filter_map(|m| {
            let id = m["id"].as_str()?.to_string();
            let keep = useful_prefixes.is_empty()
                || useful_prefixes.iter().any(|prefix| id.starts_with(prefix));
            keep.then(|| FetchedModel {
                name: id.clone(),
                id,
            })
        })
        .collect();

    models.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(models)
}

fn resolve_model(
    state: &State<'_, ProviderState>,
    provider_id: &str,
    model: Option<String>,
) -> Result<String, String> {
    if let Some(value) = model
        .map(|m| m.trim().to_string())
        .filter(|m| !m.is_empty())
    {
        return Ok(value);
    }

    let configs = state.configs.lock().unwrap();
    let configured = configs
        .get(provider_id)
        .map(|cfg| cfg.model.trim().to_string())
        .filter(|m| !m.is_empty());
    configured.ok_or_else(|| {
        format!("No model is configured for {provider_id}. Fetch models and select one.")
    })
}

fn record_chat_cost(
    state: &State<'_, ProviderState>,
    workspace: Option<&State<'_, crate::ipc::WorkspaceState>>,
    provider_id: &str,
    stage: &str,
    tokens_in: Option<u64>,
    tokens_out: Option<u64>,
) {
    let (Some(tokens_in), Some(tokens_out)) = (tokens_in, tokens_out) else {
        return;
    };
    let configs = state.configs.lock().unwrap();
    let Some(cfg) = configs.get(provider_id).cloned() else {
        return;
    };
    drop(configs);
    let cost_usd = state
        .cost
        .lock()
        .unwrap()
        .record(tokens_in, tokens_out, &cfg);
    append_usage_ledger_best_effort(
        workspace.and_then(active_workspace_path),
        provider_id,
        &cfg.model,
        stage,
        tokens_in,
        tokens_out,
        configured_cost_usd(provider_id, &cfg, cost_usd),
    );
}

fn active_workspace_path(workspace: &State<'_, crate::ipc::WorkspaceState>) -> Option<PathBuf> {
    workspace.0.lock().unwrap().clone()
}

fn provider_cost_usd(tokens_in: u64, tokens_out: u64, cfg: &ProviderConfig) -> f64 {
    (tokens_in as f64 / 1_000_000.0) * cfg.price_in_1m
        + (tokens_out as f64 / 1_000_000.0) * cfg.price_out_1m
}

fn configured_cost_usd(provider_id: &str, cfg: &ProviderConfig, cost_usd: f64) -> Option<f64> {
    if provider_id == "ollama" || cfg.price_in_1m > 0.0 || cfg.price_out_1m > 0.0 {
        Some(cost_usd)
    } else {
        None
    }
}

fn append_usage_ledger_best_effort(
    workspace: Option<PathBuf>,
    provider_id: &str,
    model: &str,
    stage: &str,
    tokens_in: u64,
    tokens_out: u64,
    cost_usd: Option<f64>,
) {
    let Some(workspace) = workspace else {
        return;
    };
    if let Err(err) = append_usage_ledger(
        &workspace,
        provider_id,
        model,
        stage,
        tokens_in,
        tokens_out,
        cost_usd,
    ) {
        eprintln!("[cost-ledger] failed to append AI usage: {err}");
    }
}

fn append_usage_ledger(
    workspace: &Path,
    provider_id: &str,
    model: &str,
    stage: &str,
    tokens_in: u64,
    tokens_out: u64,
    cost_usd: Option<f64>,
) -> Result<PathBuf, String> {
    let ledger_path = workspace
        .join(".signalos")
        .join("product")
        .join("AI_USAGE.jsonl");
    let parent = ledger_path
        .parent()
        .ok_or_else(|| "AI usage ledger path has no parent".to_string())?;
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("Could not create AI usage ledger directory: {e}"))?;

    let mut row = serde_json::json!({
        "ts": crate::ipc::ipc_chrono_iso8601(),
        "schema_version": 1,
        "source": "foundry-desktop-provider",
        "provider": provider_id,
        "model": model,
        "stage": stage,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "currency": "USD",
        "cost_basis": if cost_usd.is_some() { "configured-provider-pricing" } else { "unpriced-provider-config" },
    });
    if let Some(wave) = read_active_wave_id(workspace) {
        row["wave"] = serde_json::Value::String(wave);
    }
    if let Some(cost) = cost_usd.and_then(format_cost_usd) {
        row["cost_usd"] = serde_json::Value::String(cost);
    }

    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&ledger_path)
        .map_err(|e| format!("Could not open AI usage ledger: {e}"))?;
    writeln!(file, "{row}").map_err(|e| format!("Could not append AI usage ledger: {e}"))?;
    Ok(ledger_path)
}

fn read_active_wave_id(workspace: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(workspace.join(".signalos").join("wave.json")).ok()?;
    let value = serde_json::from_str::<serde_json::Value>(&raw).ok()?;
    let status = value
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if !status.eq_ignore_ascii_case("active") {
        return None;
    }
    value
        .get("wave")
        .or_else(|| value.get("wave_id"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(ToOwned::to_owned)
}

fn format_cost_usd(cost_usd: f64) -> Option<String> {
    if !cost_usd.is_finite() || cost_usd < 0.0 {
        return None;
    }
    let mut out = format!("{cost_usd:.8}");
    while out.contains('.') && out.ends_with('0') {
        out.pop();
    }
    if out.ends_with('.') {
        out.push('0');
    }
    Some(out)
}

#[cfg(test)]
mod cost_ledger_tests {
    use super::{
        append_usage_ledger, configured_cost_usd, format_cost_usd, provider_cost_usd,
        read_active_wave_id, ProviderConfig,
    };
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_workspace(name: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("signalos-provider-{name}-{nonce}"));
        std::fs::create_dir_all(&root).expect("create temp workspace");
        root
    }

    #[test]
    fn cost_formula_uses_configured_provider_pricing() {
        let cfg = ProviderConfig {
            model: "model-test".into(),
            price_in_1m: 1.0,
            price_out_1m: 2.0,
        };

        let cost = provider_cost_usd(1_000, 2_000, &cfg);

        assert_eq!(format_cost_usd(cost).as_deref(), Some("0.005"));
        assert_eq!(
            configured_cost_usd("configured-cloud", &cfg, cost)
                .and_then(format_cost_usd)
                .as_deref(),
            Some("0.005")
        );
    }

    #[test]
    fn unknown_zero_priced_cloud_provider_remains_unpriced() {
        let cfg = ProviderConfig {
            model: "provider-model".into(),
            price_in_1m: 0.0,
            price_out_1m: 0.0,
        };

        assert!(configured_cost_usd("openrouter", &cfg, 0.0).is_none());
        assert_eq!(configured_cost_usd("ollama", &cfg, 0.0), Some(0.0));
    }

    #[test]
    fn appends_usage_ledger_row_with_wave_and_cost() {
        let root = temp_workspace("ledger-cost");
        std::fs::create_dir_all(root.join(".signalos")).expect("create signalos dir");
        std::fs::write(
            root.join(".signalos").join("wave.json"),
            r#"{"wave":"W07","status":"ACTIVE"}"#,
        )
        .expect("write wave");

        let path = append_usage_ledger(
            &root,
            "openai",
            "gpt-test",
            "chat",
            1_000,
            2_000,
            Some(0.005),
        )
        .expect("append ledger");

        let text = std::fs::read_to_string(path).expect("read ledger");
        let row: serde_json::Value = serde_json::from_str(text.trim()).expect("parse ledger row");
        assert_eq!(row["source"], "foundry-desktop-provider");
        assert_eq!(row["provider"], "openai");
        assert_eq!(row["model"], "gpt-test");
        assert_eq!(row["stage"], "chat");
        assert_eq!(row["tokens_in"], 1_000);
        assert_eq!(row["tokens_out"], 2_000);
        assert_eq!(row["total_tokens"], 3_000);
        assert_eq!(row["cost_usd"], "0.005");
        assert_eq!(row["wave"], "W07");
        assert_eq!(read_active_wave_id(&root).as_deref(), Some("W07"));

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn appends_usage_ledger_row_without_cost_when_unpriced() {
        let root = temp_workspace("ledger-unpriced");

        let path = append_usage_ledger(
            &root,
            "openrouter",
            "custom-model",
            "chat-stream",
            10,
            20,
            None,
        )
        .expect("append ledger");

        let text = std::fs::read_to_string(path).expect("read ledger");
        let row: serde_json::Value = serde_json::from_str(text.trim()).expect("parse ledger row");
        assert!(row.get("cost_usd").is_none());
        assert_eq!(row["cost_basis"], "unpriced-provider-config");

        let _ = std::fs::remove_dir_all(root);
    }
}

async fn chat_anthropic(
    api_key: &Option<String>,
    model: &str,
    message: &str,
) -> Result<ProviderChatResponse, String> {
    let key = api_key.as_deref().ok_or("Anthropic requires an API key")?;
    // Wave 4 / G3-2: per-model max_tokens so Builder JSON doesn't truncate.
    let body = serde_json::json!({
        "model": model,
        "max_tokens": anthropic_max_tokens_for(model),
        "messages": [{ "role": "user", "content": message }],
    });
    let resp = chat_json(
        "Anthropic",
        http()
            .post("https://api.anthropic.com/v1/messages")
            .header("x-api-key", key)
            .header("anthropic-version", "2023-06-01")
            .json(&body),
    )
    .await?;

    let text = resp["content"]
        .as_array()
        .map(|parts| {
            parts
                .iter()
                .filter_map(|part| part["text"].as_str())
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();

    Ok(ProviderChatResponse {
        text,
        tokens_in: resp["usage"]["input_tokens"].as_u64(),
        tokens_out: resp["usage"]["output_tokens"].as_u64(),
        provider: "anthropic".into(),
        model: model.into(),
    })
}

async fn chat_openai(
    api_key: &Option<String>,
    base_url: &str,
    model: &str,
    message: &str,
) -> Result<ProviderChatResponse, String> {
    let key = api_key
        .as_deref()
        .ok_or("This provider requires an API key")?;
    let url = format!("{}/chat/completions", base_url.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": model,
        "messages": [{ "role": "user", "content": message }],
    });
    let resp = chat_json("AI provider", http().post(url).bearer_auth(key).json(&body)).await?;
    let text = resp["choices"]
        .as_array()
        .and_then(|choices| choices.first())
        .and_then(|choice| choice["message"]["content"].as_str())
        .unwrap_or("")
        .to_string();

    Ok(ProviderChatResponse {
        text,
        tokens_in: resp["usage"]["prompt_tokens"].as_u64(),
        tokens_out: resp["usage"]["completion_tokens"].as_u64(),
        provider: "openai-compatible".into(),
        model: model.into(),
    })
}

async fn chat_gemini(
    api_key: &Option<String>,
    model: &str,
    message: &str,
) -> Result<ProviderChatResponse, String> {
    let key = api_key.as_deref().ok_or("Gemini requires an API key")?;
    let url = format!(
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    );
    let body = serde_json::json!({
        "contents": [{ "parts": [{ "text": message }] }],
    });
    let resp = chat_json("Gemini", http().post(url).json(&body)).await?;
    let text = resp["candidates"]
        .as_array()
        .and_then(|candidates| candidates.first())
        .and_then(|candidate| candidate["content"]["parts"].as_array())
        .map(|parts| {
            parts
                .iter()
                .filter_map(|part| part["text"].as_str())
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();

    Ok(ProviderChatResponse {
        text,
        tokens_in: resp["usageMetadata"]["promptTokenCount"].as_u64(),
        tokens_out: resp["usageMetadata"]["candidatesTokenCount"].as_u64(),
        provider: "gemini".into(),
        model: model.into(),
    })
}

async fn chat_ollama(model: &str, message: &str) -> Result<ProviderChatResponse, String> {
    let body = serde_json::json!({
        "model": model,
        "prompt": message,
        "stream": false,
    });
    let resp = chat_json(
        "Ollama",
        http()
            .post("http://localhost:11434/api/generate")
            .json(&body),
    )
    .await?;

    Ok(ProviderChatResponse {
        text: resp["response"].as_str().unwrap_or("").to_string(),
        tokens_in: resp["prompt_eval_count"].as_u64(),
        tokens_out: resp["eval_count"].as_u64(),
        provider: "ollama".into(),
        model: model.into(),
    })
}

async fn chat_json(
    display_name: &str,
    request: reqwest::RequestBuilder,
) -> Result<serde_json::Value, String> {
    let response = request
        .send()
        .await
        .map_err(|e| provider_request_error(display_name, e))?;
    let status = response.status();
    if !status.is_success() {
        let detail = response.text().await.unwrap_or_default();
        let suffix = if detail.trim().is_empty() {
            String::new()
        } else {
            format!(": {}", detail.trim().chars().take(240).collect::<String>())
        };
        return Err(format!(
            "{display_name} chat failed: HTTP {}{}",
            status.as_u16(),
            suffix
        ));
    }
    response
        .json::<serde_json::Value>()
        .await
        .map_err(|_| format!("{display_name} chat returned an unreadable response"))
}

async fn fetch_json(
    display_name: &str,
    request: reqwest::RequestBuilder,
) -> Result<serde_json::Value, String> {
    let response = request
        .send()
        .await
        .map_err(|e| provider_request_error(display_name, e))?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!(
            "{display_name} model list failed: HTTP {}",
            status.as_u16()
        ));
    }
    response
        .json::<serde_json::Value>()
        .await
        .map_err(|_| format!("{display_name} model list returned an unreadable response"))
}

fn provider_request_error(display_name: &str, error: reqwest::Error) -> String {
    let reason = if error.is_timeout() {
        "timed out"
    } else if error.is_connect() {
        "could not connect"
    } else {
        "request failed"
    };
    format!("{display_name} model list {reason}")
}
