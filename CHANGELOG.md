# Changelog

## [1.0.0-beta5] - 2026-05-13

### Chat attachments and release fix

- Added chat file selection and drag/drop attachment intake.
- Added support for images, PDFs, Word, PowerPoint, Excel, text, Markdown, CSV, JSON, logs, code files, and zip references.
- Blocked .env, key/certificate files, SQL/database dumps, and likely secret attachments.
- Redacted likely API keys and secrets from accepted text and document summaries.
- Added Office/PDF text extraction for safe summaries without returning raw file bytes.
- Fixed CI release builds by bundling the platform-specific Python sidecar before Tauri packaging.

## [1.0.0-beta4] - 2026-05-13

### Provider and secrets release

- Added Qwen as a first-level AI provider.
- Moved lower-frequency AI integrations under More providers.
- Added OpenRouter, DeepSeek, Mistral, Groq, Cerebras, Together AI, and xAI provider entries.
- Removed frontend access to raw saved AI keys.
- Added secret redaction for .env files, likely secret values, command arguments, sidecar output, errors, notes, and nested response data.
- Added a settings secrets summary that shows secret file names and variable names only.
- Sanitized provider model-list errors so API keys are not echoed back to the UI.

## [1.0.0-beta1] — 2026-05-03

### First public beta

- Native desktop app for macOS, Windows, and Linux
- Multi-provider LLM chat (Anthropic Claude, OpenAI, Google Gemini, Ollama)
- API keys stored in OS keychain — never written to disk
- SignalOS governance UI: wave state, gate signing, audit trail
- Brain knowledge base with BM25 search
- Live phase debt and belief confidence dashboard
- Command palette with all /signal-* commands
- Python SignalOS Core sidecar integration
- Auto-updater with signed update manifests
- File watcher — workspace change events refresh the UI
