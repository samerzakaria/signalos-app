import { render, screen, fireEvent } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';
import { ChatPreviewBubble } from './ChatPreviewBubble';

describe('ChatPreviewBubble', () => {
  it('embeds a sandboxed iframe for srcDoc and shows a caption', () => {
    render(<ChatPreviewBubble srcDoc="<h1>Hi</h1>" caption="Mantine design" />);
    expect(screen.getByTestId('chat-preview-bubble')).toHaveTextContent('Mantine design');
    const frame = screen.getByTestId('chat-preview-iframe');
    expect(frame.getAttribute('sandbox')).toContain('allow-scripts');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
  });

  it('fires onPopOut when the pop-out button is clicked', () => {
    const onPopOut = vi.fn();
    render(<ChatPreviewBubble url="http://localhost:5173" onPopOut={onPopOut} />);
    fireEvent.click(screen.getByTestId('chat-preview-popout'));
    expect(onPopOut).toHaveBeenCalled();
  });
});
