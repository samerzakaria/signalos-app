import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatBubbles } from '../state';

describe('chat auto-scroll', () => {
  beforeEach(() => {
    vi.resetModules();
    document.body.innerHTML = '';
    chatBubbles.value = [];
  });

  it('scrolls the Build conversation to the bottom when bubbles change', async () => {
    const scroll = document.createElement('div');
    scroll.id = 'chatScroll';
    Object.defineProperty(scroll, 'scrollHeight', {
      configurable: true,
      value: 900,
    });
    document.body.appendChild(scroll);

    await import('./chat');
    chatBubbles.value = [{ id: 'b1', kind: 'ai', text: 'new message' }];
    await new Promise<void>((resolve) => queueMicrotask(() => resolve()));

    expect(scroll.scrollTop).toBe(900);
  });
});
