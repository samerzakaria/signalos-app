import { describe, it, expect, beforeEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { App } from './app';
import { appVisible, mobileNavOpen, onboardingVisible, sbTab, tab } from './state';

describe('App view shell isolation', () => {
  beforeEach(() => {
    cleanup();
    tab.value = 'build';
    sbTab.value = 'projects';
    mobileNavOpen.value = false;
    onboardingVisible.value = false;
    appVisible.value = true;
    window.switchTab = vi.fn();
    window.switchSbTab = vi.fn();
  });

  it('keeps exactly one product view active from the tab signal', () => {
    const { container } = render(<App />);

    const activeViews = Array.from(container.querySelectorAll('.view.active'))
      .map((el) => el.getAttribute('data-view'));

    expect(activeViews).toEqual(['build']);
    expect(container.querySelector('.view[data-view="dashboard"]')).not.toHaveClass('active');
  });

  it('updates active content without leaving the previous page visible', async () => {
    const { container } = render(<App />);

    // 'preview' is one of the product tabs that remain after the unified
    // Build surface owns delivery and governed commands.
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

  it('keeps the 375px mobile shell usable without stacked pages', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    window.dispatchEvent(new Event('resize'));

    const { container } = render(<App />);

    expect(container.querySelector('.sidebar')).not.toHaveClass('mobile-open');
    expect(container.querySelector('.mobile-nav-backdrop')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /toggle navigation/i }));

    await waitFor(() => {
      expect(container.querySelector('.sidebar')).toHaveClass('mobile-open');
      expect(container.querySelector('.mobile-nav-backdrop')).not.toBeNull();
      const activeViews = Array.from(container.querySelectorAll('.view.active'))
        .map((el) => el.getAttribute('data-view'));
      expect(activeViews).toEqual(['build']);
    });

    fireEvent.click(container.querySelector('.mobile-nav-backdrop') as HTMLElement);

    await waitFor(() => {
      expect(container.querySelector('.sidebar')).not.toHaveClass('mobile-open');
      expect(container.querySelector('.mobile-nav-backdrop')).toBeNull();
    });
  });
});
