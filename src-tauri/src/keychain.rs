/// keychain.rs — OS credential storage
///
/// macOS  → Keychain Services (Security framework)
/// Windows → Windows Credential Manager (wincred)
/// Linux  → libsecret / GNOME Keyring
///
/// Keys are NEVER written to disk or included in log output.
use keyring::Entry;

const SERVICE: &str = "com.signalos.desktop";

#[tauri::command]
pub fn store_api_key(provider: String, key: String) -> Result<(), String> {
    validate_provider(&provider)?;
    Entry::new(SERVICE, &provider)
        .map_err(|e| e.to_string())?
        .set_password(&key)
        .map_err(|e| format!("Keychain write failed ({}): {}", provider, e))
}

pub fn get_api_key(provider: String) -> Result<Option<String>, String> {
    validate_provider(&provider)?;
    match Entry::new(SERVICE, &provider)
        .map_err(|e| e.to_string())?
        .get_password()
    {
        Ok(key) => Ok(Some(key)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("Keychain read error: {}", e)),
    }
}

#[tauri::command]
pub fn has_api_key(provider: String) -> Result<bool, String> {
    validate_provider(&provider)?;
    match Entry::new(SERVICE, &provider)
        .map_err(|e| e.to_string())?
        .get_password()
    {
        Ok(_) => Ok(true),
        Err(keyring::Error::NoEntry) => Ok(false),
        Err(e) => Err(format!("Keychain error: {}", e)),
    }
}

#[tauri::command]
pub fn delete_api_key(provider: String) -> Result<(), String> {
    validate_provider(&provider)?;
    Entry::new(SERVICE, &provider)
        .map_err(|e| e.to_string())?
        .delete_password()
        .map_err(|e| format!("Keychain delete failed ({}): {}", provider, e))
}

fn validate_provider(provider: &str) -> Result<(), String> {
    match provider {
        "anthropic" | "openai" | "gemini" | "qwen" | "ollama" | "openrouter" | "deepseek"
        | "mistral" | "groq" | "cerebras" | "together" | "xai" => Ok(()),
        other => Err(format!("Unknown provider: {}", other)),
    }
}

/// Provider id -> env-var name expected by the harness / vendor SDKs.
///
/// This is the bridge that makes `signalos orchestrate` and `signalos harness`
/// actually able to call LLMs. The harness reads from env vars (so the
/// Python subprocess can stay stdlib-only with no Rust callbacks); the chat
/// path reads from the keychain directly. Same key, two access paths.
pub fn env_var_for_provider(provider: &str) -> Option<&'static str> {
    match provider {
        "anthropic" => Some("ANTHROPIC_API_KEY"),
        "openai" => Some("OPENAI_API_KEY"),
        "gemini" => Some("GEMINI_API_KEY"),
        "qwen" => Some("DASHSCOPE_API_KEY"),
        "openrouter" => Some("OPENROUTER_API_KEY"),
        "deepseek" => Some("DEEPSEEK_API_KEY"),
        "mistral" => Some("MISTRAL_API_KEY"),
        "groq" => Some("GROQ_API_KEY"),
        "cerebras" => Some("CEREBRAS_API_KEY"),
        "together" => Some("TOGETHER_API_KEY"),
        "xai" => Some("XAI_API_KEY"),
        "ollama" => None, // local, no key needed
        _ => None,
    }
}

/// Snapshot every keychain-stored API key as { env_var: key } pairs.
///
/// Returns the env-var bag the Python sidecar should be spawned with so
/// the harness can resolve providers without the user manually exporting
/// env vars before launching the app.
///
/// Errors fetching individual keys (e.g. NoEntry) are silently skipped --
/// the harness will report a clearer error for the specific provider it
/// tries to use later.
pub fn snapshot_env_keys() -> std::collections::HashMap<String, String> {
    let providers = [
        "anthropic",
        "openai",
        "gemini",
        "qwen",
        "openrouter",
        "deepseek",
        "mistral",
        "groq",
        "cerebras",
        "together",
        "xai",
    ];
    let mut env = std::collections::HashMap::new();
    for p in providers {
        let Some(var) = env_var_for_provider(p) else {
            continue;
        };
        if let Ok(Some(key)) = get_api_key(p.to_string()) {
            if p == "together" {
                env.insert("TOGETHERAI_API_KEY".to_string(), key.clone());
            }
            env.insert(var.to_string(), key);
        }
    }
    env
}
