import { effect } from '@preact/signals';
import { chatBubbles } from '../state';

// Auto-scroll #chatScroll to the bottom whenever chatBubbles changes
// (new user/AI bubble pushed, or a streaming bubble's text updates).
effect(() => {
  chatBubbles.value; // subscribe
  queueMicrotask(() => {
    const s = document.getElementById('chatScroll');
    if (s) s.scrollTop = s.scrollHeight;
  });
});
