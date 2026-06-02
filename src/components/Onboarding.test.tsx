import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/preact';
import { ai, aiModel, apiKeyInput, obStep, providerModels, providerModelsError, providerModelsLoading } from '../state';
import { Onboarding } from './Onboarding';
import { loadProviderModels } from '../services/providerModels';

vi.mock('../services/providerModels', () => ({
  loadProviderModels: vi.fn(async () => []),
}));

describe('Onboarding provider model selection', () => {
  beforeEach(() => {
    obStep.value = 2;
    ai.value = 'anthropic';
    aiModel.value = 'claude-sonnet-4-5-20250929';
    providerModels.value = [
      { id: 'claude-sonnet-4-5-20250929', name: 'Claude Sonnet 4.5' },
      { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' },
    ];
    apiKeyInput.value = '';
    providerModelsError.value = null;
    providerModelsLoading.value = false;
    window.selectProv = vi.fn();
    window.toggleMoreProvs = vi.fn();
    window.toggleKey = vi.fn();
    window.nextStep = vi.fn();
    window.prevStep = vi.fn();
  });

  it('uses a provider-fetched model selector and marks the active provider circle', () => {
    const { container } = render(<Onboarding />);

    const modelSelect = screen.getByLabelText('Model') as HTMLSelectElement;
    expect(modelSelect.tagName).toBe('SELECT');
    expect(modelSelect.value).toBe('claude-sonnet-4-5-20250929');
    expect(screen.getByText('Claude Sonnet 4.5')).toBeInTheDocument();

    const selectedProvider = container.querySelector('[data-ai="anthropic"]');
    expect(selectedProvider).toHaveClass('sel');
    expect(selectedProvider?.querySelector('.ai-rd i')).toHaveClass('ti-check');
  });

  it('tries saved provider keys quietly when changing provider during onboarding', () => {
    const { container } = render(<Onboarding />);

    const openAiCard = container.querySelector('[data-ai="openai"]') as HTMLElement;
    fireEvent.click(openAiCard);

    expect(loadProviderModels).toHaveBeenCalledWith('openai', null, { quietMissingKey: true });
  });
});
