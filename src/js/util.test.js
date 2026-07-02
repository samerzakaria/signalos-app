import { describe, expect, it } from 'vitest';
import { errorMessage, isProviderAuthFailure, providerConnectionMessage } from './util.js';

describe('errorMessage', () => {
  it('keeps plain Tauri rejection strings visible', () => {
    expect(errorMessage('provider rejected key')).toBe('provider rejected key');
  });

  it('keeps structured Tauri errors visible', () => {
    expect(errorMessage({ error: 'sidecar unavailable' })).toBe('sidecar unavailable');
  });

  it('does not surface undefined for empty errors', () => {
    expect(errorMessage(undefined)).toBe('Unknown error');
    expect(errorMessage({})).toBe('Unknown error');
    expect(errorMessage('undefined')).toBe('Unknown error');
  });

  it('turns missing workspace backend errors into product guidance', () => {
    expect(errorMessage('No workspace selected'))
      .toBe('Open or create a product first. The projects root is only a container; Vault actions need an active product folder.');
  });

  it('turns JSON parser failures into structured-data guidance', () => {
    expect(errorMessage('Unexpected token s, "sessions/d"... is not valid JSON'))
      .toBe('Foundry received a text response where structured data was expected. Refresh this panel or run the command again.');
  });
});

describe('providerConnectionMessage', () => {
  it('turns provider 401 model-list failures into user-safe auth guidance', () => {
    expect(providerConnectionMessage('Anthropic model list failed: HTTP 401', 'Anthropic'))
      .toBe('Anthropic rejected the API key. Setup can continue; replace the key in Settings when ready.');
    expect(isProviderAuthFailure('Anthropic model list failed: HTTP 401')).toBe(true);
  });

  it('turns model-list failures into retry guidance without raw internals', () => {
    expect(providerConnectionMessage('Anthropic model list returned an unreadable response', 'Anthropic'))
      .toBe('Anthropic models could not be loaded right now. You can continue setup and refresh models later in Settings.');
  });

  it('turns provider edge blocks into retry guidance without treating the key as rejected', () => {
    expect(providerConnectionMessage('This content is blocked. Contact the site owner to fix the issue.', 'Anthropic'))
      .toBe('Anthropic model fetching is blocked by the provider or network. Your key is saved; refresh models again or switch provider.');
    expect(isProviderAuthFailure('This content is blocked. Contact the site owner to fix the issue.')).toBe(false);
  });

  it('turns chat 404 failures into selected-model guidance', () => {
    expect(providerConnectionMessage('AI provider chat failed: HTTP 404', 'OpenAI'))
      .toBe('OpenAI rejected the selected model for chat. Pick a text/chat model in Settings, test it, then retry.');
  });

  it('turns provider credit JSON into billing guidance without raw JSON', () => {
    const raw = 'Provider call failed: BadRequestError: litellm.BadRequestError: AnthropicException - {"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}';
    expect(providerConnectionMessage(raw, 'Anthropic'))
      .toBe('Anthropic account credit is too low. Add credits with that provider or choose another provider/model in Settings.');
  });
});
