# SignalOS Provider Validation Guide

Date: 2026-05-14

This guide defines the live-account checks needed before a public beta. The app already has model fetching, model selection, manual model entry, key replacement/deletion, provider testing, and friendly error copy. These checks prove the live provider behavior.

## Providers To Validate

- Anthropic.
- OpenAI.
- Google Gemini.
- Qwen.
- OpenRouter.
- DeepSeek.
- Mistral.
- Groq.
- Cerebras.
- Together AI.
- xAI.
- Ollama.

## Checks Per Cloud Provider

For each provider:

1. Save a valid key.
2. Fetch models.
3. Select a fetched model.
4. Send a short chat message.
5. Replace the saved key.
6. Delete the saved key.
7. Try an invalid key and confirm the error is understandable.
8. Try an invalid model and confirm the error is understandable.
9. Trigger or simulate quota/rate-limit behavior when possible.
10. Confirm raw keys do not appear in the UI, transcript, diagnostics, issue report, or handoff report.

## Checks For Ollama

1. Start Ollama.
2. Pull a local model.
3. Select Ollama.
4. Fetch local models.
5. Select a fetched model.
6. Send a short chat message.
7. Stop Ollama.
8. Confirm the app shows a local connection recovery message.

## Evidence To Keep

For each provider, keep:

- Provider name.
- Date tested.
- Model used.
- Success/failure result.
- Redacted issue report if a failure occurred.
- Whether raw secret values stayed hidden.
