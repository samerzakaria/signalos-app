import { render, screen, fireEvent } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { FileDiffBubble, computeDiff } from './FileDiffBubble';

describe('computeDiff', () => {
  it('marks added and removed lines', () => {
    const d = computeDiff('a\nb\nc', 'a\nB\nc');
    const adds = d.filter((l) => l.kind === 'add').map((l) => l.text);
    const dels = d.filter((l) => l.kind === 'del').map((l) => l.text);
    expect(dels).toContain('b');
    expect(adds).toContain('B');
    expect(d.filter((l) => l.kind === 'ctx').map((l) => l.text)).toEqual(['a', 'c']);
  });

  it('treats empty before as all additions', () => {
    const d = computeDiff('', 'x\ny');
    expect(d.every((l) => l.kind === 'add')).toBe(true);
    expect(d).toHaveLength(2);
  });
});

describe('FileDiffBubble', () => {
  it('renders the path, +/- counts and green/red lines', () => {
    render(<FileDiffBubble path="src/App.tsx" before={'a\nb'} after={'a\nc'} />);
    const bubble = screen.getByTestId('file-diff-bubble');
    expect(bubble).toHaveTextContent('src/App.tsx');
    expect(bubble.querySelector('.file-diff-add')).toHaveTextContent('+1');
    expect(bubble.querySelector('.file-diff-del')).toHaveTextContent('-1');
    expect(bubble.querySelector('.file-diff-line.add')).toBeTruthy();
    expect(bubble.querySelector('.file-diff-line.del')).toBeTruthy();
  });

  it('toggles the full-file view', () => {
    render(<FileDiffBubble path="f.ts" before={'a\nb\nc'} after={'a\nb\nX'} />);
    const toggle = screen.getByTestId('file-diff-toggle');
    expect(toggle).toHaveTextContent('Full file');
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent('Collapse');
  });
});
