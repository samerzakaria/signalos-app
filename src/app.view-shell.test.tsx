import { describe, it, expect, beforeEach, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/preact';
import { App } from './app';
import { appVisible, onboardingVisible, sbTab, tab } from './state';

describe('App view shell isolation', () => {
  beforeEach(() => {
    cleanup();
    tab.value = 'deliver';
    sbTab.value = 'projects';
    onboardingVisible.value = false;
    appVisible.value = true;
    window.switchTab = vi.fn();
    window.switchSbTab = vi.fn();
  });

  it('keeps exactly one product view active from the tab signal', () => {
    const { container } = render(<App />);

    const activeViews = Array.from(container.querySelectorAll('.view.active'))
      .map((el) => el.getAttribute('data-view'));

    expect(activeViews).toEqual(['deliver']);
    expect(container.querySelector('.view[data-view="dashboard"]')).not.toHaveClass('active');
  });

  it('updates active content without leaving the previous page visible', async () => {
    const { container } = render(<App />);

    // 'preview' is one of the three project tabs that remain in nav after
    // Phase 1.1 (Build / Preview / Evidence). Terminal was removed from nav.
    tab.value = 'preview';

    await waitFor(() => {
      const activeViews = Array.from(container.querySelectorAll('.view.active'))
        .map((el) => el.getAttribute('data-view'));

      expect(activeViews).toEqual(['preview']);
      expect(container.querySelector('.seg-i.active')?.getAttribute('data-tab')).toBe('preview');
    });
  });

  it('does not keep onboarding active after the app stage opens', async () => {
    const { container } = render(<App />);

    expect(container.querySelector('#app')).toHaveClass('active');
    expect(container.querySelector('#onboarding')).not.toHaveClass('active');

    onboardingVisible.value = true;
    appVisible.value = false;

    await waitFor(() => {
      expect(container.querySelector('#onboarding')).toHaveClass('active');
      expect(container.querySelector('#app')).not.toHaveClass('active');
    });
  });
});
