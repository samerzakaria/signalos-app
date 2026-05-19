import { effect } from '@preact/signals';
import { terminalLines } from '../state';

// Auto-scroll #termBody to the bottom whenever terminalLines changes.
// Side-effect-only module; imported by main.tsx for its registration.
effect(() => {
  terminalLines.value; // subscribe
  queueMicrotask(() => {
    const body = document.getElementById('termBody');
    if (body) body.scrollTop = body.scrollHeight;
  });
});
