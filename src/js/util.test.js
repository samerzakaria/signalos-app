import { describe, expect, it } from 'vitest';
import { errorMessage } from './util.js';

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
  });
});
