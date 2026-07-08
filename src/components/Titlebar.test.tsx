import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/preact';
import { Titlebar } from './Titlebar';
import { ai, aiModel, currentCost } from '../state';

// The live badge must show the ACTUAL selected model, never a hardcoded
// per-provider label. The old map rendered "GPT-4o" for every OpenAI model,
// so a user who picked a different model saw the wrong one.
describe('Titlebar live badge', () => {
  beforeEach(() => {
    currentCost.value = 0;
    ai.value = 'openai';
    aiModel.value = '';
  });

  it('shows the real selected model, not a hardcoded provider label', () => {
    ai.value = 'openai';
    aiModel.value = 'gpt-5.5';
    const { getByText } = render(<Titlebar />);
    expect(getByText(/gpt-5\.5 · live/)).toBeTruthy();
  });

  it('strips a trailing -YYYYMMDD snapshot suffix', () => {
    ai.value = 'anthropic';
    aiModel.value = 'claude-sonnet-4-5-20250929';
    const { getByText } = render(<Titlebar />);
    expect(getByText(/claude-sonnet-4-5 · live/)).toBeTruthy();
  });

  it('falls back to the provider brand only when no model is selected', () => {
    ai.value = 'anthropic';
    aiModel.value = '';
    const { getByText } = render(<Titlebar />);
    expect(getByText(/Claude · live/)).toBeTruthy();
  });

  it('never shows the old hardcoded "GPT-4o" for a different OpenAI model', () => {
    ai.value = 'openai';
    aiModel.value = 'gpt-5.5';
    const { queryByText } = render(<Titlebar />);
    expect(queryByText(/GPT-4o/)).toBeNull();
  });
});
