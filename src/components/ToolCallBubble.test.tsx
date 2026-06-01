import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { ToolCallBubble } from './ToolCallBubble';

describe('ToolCallBubble', () => {
  it('renders a running read with a spinner', () => {
    render(<ToolCallBubble tool="read_file" target="package.json" status="running" />);
    const bubble = screen.getByTestId('tool-call-bubble');
    expect(bubble).toHaveTextContent('Reading');
    expect(bubble).toHaveTextContent('package.json');
    expect(bubble.querySelector('.tcb-spin')).toBeTruthy();
  });

  it('shows a checkmark and summary when done', () => {
    render(<ToolCallBubble tool="write_file" target="src/App.tsx" status="done" summary="42 lines" />);
    const card = screen.getByTestId('tool-call-bubble').querySelector('.tool-call');
    expect(card).toHaveAttribute('data-status', 'done');
    expect(card?.querySelector('.ti-check')).toBeTruthy();
    expect(screen.getByTestId('tool-call-bubble')).toHaveTextContent('42 lines');
  });

  it('renders denial state with a shield-off icon and detail', () => {
    render(<ToolCallBubble tool="write_file" target=".env" status="denied" detail="Permission denied" />);
    expect(screen.getByTestId('tool-call-bubble').querySelector('.ti-shield-off')).toBeTruthy();
    expect(screen.getByTestId('tool-call-detail')).toHaveTextContent('Permission denied');
  });
});
