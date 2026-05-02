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

#[tauri::command]
pub fn get_api_key(provider: String) -> Result<Option<String>, String> {
    validate_provider(&provider)?;
    match Entry::new(SERVICE, &provider).map_err(|e| e.to_string())?.get_password() {
        Ok(key)                              => Ok(Some(key)),
        Err(keyring::Error::NoEntry)         => Ok(None),
        Err(e)                               => Err(format!("Keychain read error: {}", e)),
    }
}

#[tauri::command]
pub fn has_api_key(provider: String) -> Result<bool, String> {
    validate_provider(&provider)?;
    match Entry::new(SERVICE, &provider).map_err(|e| e.to_string())?.get_password() {
        Ok(_)                        => Ok(true),
        Err(keyring::Error::NoEntry) => Ok(false),
        Err(e)                       => Err(format!("Keychain error: {}", e)),
    }
}

#[tauri::command]
pub fn delete_api_key(provider: String) -> Result<(), String> {
    validate_provider(&provider)?;
    Entry::new(SERVICE, &provider)
        .map_err(|e| e.to_string())?
        .delete_credential()
        .map_err(|e| format!("Keychain delete failed ({}): {}", provider, e))
}

fn validate_provider(provider: &str) -> Result<(), String> {
    match provider {
        "anthropic" | "openai" | "gemini" | "ollama" => Ok(()),
        other => Err(format!("Unknown provider: {}", other)),
    }
}
