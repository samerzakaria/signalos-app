// visualEdit.ts — point-and-click "canvas" editing for the live preview.
//
// The preview runs in a sandboxed iframe on the dev server's own origin, so the
// host cannot reach into it across origins. Instead the preview posts a
// `foundry:select` message describing the clicked element; the host turns that
// (plus a short phrase the user types) into a scoped, plain-language edit
// instruction for the agent loop — no DOM-hierarchy description required.
//
// PICKER_SNIPPET is the small script that makes a preview cooperate: injected
// where same-origin, or includable by Foundry's dev-server template otherwise.
// This module's logic is pure and unit-tested; the host wiring lives in
// PreviewView.

export const VISUAL_EDIT_MESSAGE = 'foundry:select';

export interface VisualEditTarget {
  selector: string;
  tag: string;
  text?: string;
  classes?: string[];
}

/** Validate + normalise an incoming postMessage payload. Returns null if it is
 *  not a well-formed foundry:select message. */
export function parseSelectMessage(data: unknown): VisualEditTarget | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== VISUAL_EDIT_MESSAGE) return null;
  const t = d.target as Record<string, unknown> | undefined;
  if (!t || typeof t.selector !== 'string' || !t.selector.trim()) return null;
  const target: VisualEditTarget = {
    selector: String(t.selector).slice(0, 400),
    tag: typeof t.tag === 'string' ? t.tag.toLowerCase() : 'element',
  };
  if (typeof t.text === 'string' && t.text.trim()) {
    target.text = t.text.trim().slice(0, 120);
  }
  if (Array.isArray(t.classes)) {
    target.classes = t.classes.filter((c) => typeof c === 'string').slice(0, 10) as string[];
  }
  return target;
}

/** A human-legible description of a clicked element. */
export function describeTarget(t: VisualEditTarget): string {
  const tag = `<${t.tag || 'element'}>`;
  const text = t.text ? ` "${t.text}"` : '';
  return `the ${tag}${text} (selector \`${t.selector}\`)`;
}

/** Build a scoped, plain-language edit instruction for the agent loop. */
export function buildEditInstruction(t: VisualEditTarget, phrase: string): string {
  const ask = (phrase || '').trim();
  if (!ask) return '';
  return (
    `In the live preview, update ${describeTarget(t)}: ${ask}. ` +
    `Change only this element; keep everything else exactly as it is.`
  );
}

// Self-contained picker. Highlights elements on hover; on click it computes a
// stable-ish CSS selector and posts a foundry:select message to the parent.
// Stringified so it can be injected or served verbatim.
export const PICKER_SNIPPET = `(() => {
  if (window.__foundryPicker) return;
  window.__foundryPicker = true;
  var last;
  function sel(el) {
    if (el.id) return '#' + el.id;
    var parts = [];
    while (el && el.nodeType === 1 && parts.length < 4) {
      var part = el.tagName.toLowerCase();
      if (el.classList && el.classList.length) {
        part += '.' + Array.prototype.slice.call(el.classList).slice(0, 2).join('.');
      }
      var p = el.parentNode;
      if (p) {
        var sibs = Array.prototype.filter.call(p.children, function (c) { return c.tagName === el.tagName; });
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(el) + 1) + ')';
      }
      parts.unshift(part);
      if (el.id) break;
      el = el.parentNode;
    }
    return parts.join(' > ');
  }
  document.addEventListener('mouseover', function (e) {
    if (last) last.style.outline = '';
    last = e.target;
    if (last && last.style) last.style.outline = '2px solid #6c5ce7';
  }, true);
  document.addEventListener('click', function (e) {
    e.preventDefault(); e.stopPropagation();
    var el = e.target;
    parent.postMessage({
      type: 'foundry:select',
      target: {
        selector: sel(el),
        tag: el.tagName ? el.tagName.toLowerCase() : 'element',
        text: (el.textContent || '').trim().slice(0, 120),
        classes: el.classList ? Array.prototype.slice.call(el.classList) : []
      }
    }, '*');
  }, true);
})();`;
