// ChatPreviewBubble.tsx — inline design preview in the conversation
// (Phase 1.6). Renders the LLM-generated interactive prototype in a sandboxed
// iframe directly in the chat, with a "Pop out to Preview" button that hands
// off to the Preview tab. Drives test T29 ("G3 pause shows design preview").
//
// Two source modes:
//   - `srcDoc`  : self-contained HTML (design_preview.py output) rendered in a
//                 sandboxed iframe (no same-origin, scripts allowed for the
//                 prototype to be interactive).
//   - `url`     : a live dev-server URL (rare in-chat; usually popped out).
//
// Preact only.

import { previewUrl, previewStatus, tab } from '../state';

export interface ChatPreviewBubbleProps {
  /** Inline self-contained HTML prototype. */
  srcDoc?: string;
  /** Or a live URL to embed. */
  url?: string;
  /** Caption under the frame (e.g. "Design direction — Mantine"). */
  caption?: string;
  /** Height of the inline frame in px (default 320). */
  height?: number;
  /**
   * Called when the user pops out. Defaults to switching to the Preview tab
   * (and, when a url is present, seeding previewUrl). Injected for tests.
   */
  onPopOut?: () => void;
}

function defaultPopOut(url?: string) {
  return () => {
    if (url) {
      previewUrl.value = url;
      if (previewStatus.value === 'idle') previewStatus.value = 'running';
    }
    tab.value = 'preview';
    try {
      void window.switchTab?.('preview');
    } catch {
      /* non-fatal */
    }
  };
}

export function ChatPreviewBubble({ srcDoc, url, caption, height, onPopOut }: ChatPreviewBubbleProps) {
  const popOut = onPopOut ?? defaultPopOut(url);
  const frameHeight = height ?? 320;

  return (
    <div className="msg spark" data-testid="chat-preview-bubble">
      <div className="msg-av"><i className="ti ti-layout-2" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="chat-preview">
          <div className="chat-preview-head">
            <span className="chat-preview-title">
              <i className="ti ti-eye"></i> {caption || 'Design preview'}
            </span>
            <button
              type="button"
              className="chat-preview-popout"
              data-testid="chat-preview-popout"
              onClick={popOut}
            >
              <i className="ti ti-external-link"></i> Pop out to Preview
            </button>
          </div>
          <div className="chat-preview-frame" style={{ height: `${frameHeight}px` }}>
            {srcDoc ? (
              <iframe
                title={caption || 'Design preview'}
                srcDoc={srcDoc}
                sandbox="allow-scripts"
                data-testid="chat-preview-iframe"
              />
            ) : url ? (
              <iframe
                title={caption || 'Design preview'}
                src={url}
                sandbox="allow-scripts allow-same-origin"
                data-testid="chat-preview-iframe"
              />
            ) : (
              <div className="chat-preview-empty">No preview available yet.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
