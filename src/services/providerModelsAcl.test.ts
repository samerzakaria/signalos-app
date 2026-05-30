import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('provider model ACL', () => {
  it('allows the desktop UI to fetch provider models', () => {
    const toml = readFileSync(
      resolve(process.cwd(), 'src-tauri/permissions/workspace-core.toml'),
      'utf8',
    );

    expect(toml).toContain('"fetch_provider_models"');
    expect(toml).toContain('"set_provider_model"');
    expect(toml).toContain('"test_provider_connection"');
  });
});
