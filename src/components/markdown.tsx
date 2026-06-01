// markdown.tsx — lightweight, dependency-free markdown renderer for the Build
// conversation (Phase 1.2). Renders headings, bold/italic/inline-code, lists,
// links, and fenced code blocks with a copy button + minimal token-class
// syntax highlighting.
//
// Why hand-rolled instead of `marked` + `highlight.js`: adding runtime deps is
// owned by the bundling agent (python/ + src-tauri/ + bundle scripts). This
// keeps Phase 1 self-contained and CSP-safe (no eval, no innerHTML of
// untrusted code — code text is rendered as escaped text nodes).
//
// Preact only — no React.

import { useSignal } from '@preact/signals';
import type { ComponentChildren, VNode } from 'preact';

// ── Code block with copy button + lightweight highlighting ──────────────────

interface CodeBlockProps {
  code: string;
  lang?: string;
}

/** Tokens we highlight regardless of language — keywords, strings, comments,
 *  numbers. Deliberately conservative so we never mangle code. */
const KEYWORDS =
  /\b(const|let|var|function|return|if|else|for|while|class|extends|import|export|from|default|async|await|new|try|catch|finally|throw|typeof|interface|type|enum|public|private|protected|static|void|null|undefined|true|false|def|elif|lambda|None|True|False|self|fn|pub|use|impl|struct|match)\b/g;

function highlight(line: string): VNode[] {
  // Escape happens implicitly because we build text nodes. We split on string
  // literals and comments first, then keyword/number-highlight the rest.
  const out: VNode[] = [];
  let rest = line;
  let key = 0;
  const push = (cls: string | null, text: string) => {
    if (!text) return;
    out.push(cls ? <span className={cls} key={key++}>{text}</span> : <span key={key++}>{text}</span>);
  };

  // line comment (// or #) — only when it starts a comment region
  const commentMatch = rest.match(/(\/\/|#).*$/);
  let comment = '';
  if (commentMatch && commentMatch.index !== undefined) {
    comment = rest.slice(commentMatch.index);
    rest = rest.slice(0, commentMatch.index);
  }

  // split out string literals
  const stringRe = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = stringRe.exec(rest)) !== null) {
    const before = rest.slice(last, m.index);
    pushTokens(before, push);
    push('md-tok-str', m[0]);
    last = m.index + m[0].length;
  }
  pushTokens(rest.slice(last), push);
  if (comment) push('md-tok-com', comment);
  return out;
}

function pushTokens(text: string, push: (cls: string | null, t: string) => void) {
  if (!text) return;
  // numbers + keywords; everything else plain.
  let last = 0;
  const re = new RegExp(`${KEYWORDS.source}|\\b\\d+(?:\\.\\d+)?\\b`, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    push(null, text.slice(last, m.index));
    const tok = m[0];
    const isNum = /^\d/.test(tok);
    push(isNum ? 'md-tok-num' : 'md-tok-kw', tok);
    last = m.index + tok.length;
  }
  push(null, text.slice(last));
}

export function CodeBlock({ code, lang }: CodeBlockProps) {
  const copied = useSignal(false);
  const onCopy = () => {
    try {
      void navigator.clipboard?.writeText(code);
      copied.value = true;
      setTimeout(() => { copied.value = false; }, 1400);
    } catch {
      /* clipboard unavailable — non-fatal */
    }
  };
  const lines = code.replace(/\n$/, '').split('\n');
  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang || 'text'}</span>
        <button type="button" className="md-code-copy" onClick={onCopy} aria-label="Copy code">
          <i className={`ti ${copied.value ? 'ti-check' : 'ti-copy'}`}></i>
          {copied.value ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="md-code-body"><code>
        {lines.map((ln, i) => (
          <div className="md-code-line" key={i}>{highlight(ln)}</div>
        ))}
      </code></pre>
    </div>
  );
}

// ── Inline markdown (bold / italic / code / links) ──────────────────────────

function renderInline(text: string, keyBase: string): ComponentChildren {
  const nodes: ComponentChildren[] = [];
  // order matters: code first (so ** inside code isn't bolded)
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*|_[^_]+_)|(\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyBase}-${i++}`;
    if (tok.startsWith('`')) {
      nodes.push(<code className="md-inline-code" key={k}>{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith('[')) {
      const lm = tok.match(/\[([^\]]+)\]\(([^)]+)\)/);
      if (lm) nodes.push(<a href={lm[2]} target="_blank" rel="noreferrer" key={k}>{lm[1]}</a>);
      else nodes.push(tok);
    } else {
      nodes.push(<em key={k}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// ── Block-level markdown ────────────────────────────────────────────────────

export function Markdown({ text }: { text: string }) {
  const blocks: VNode[] = [];
  const src = String(text ?? '');
  const lines = src.split('\n');
  let i = 0;
  let key = 0;
  let listBuf: string[] = [];

  const flushList = () => {
    if (listBuf.length === 0) return;
    const items = listBuf;
    listBuf = [];
    blocks.push(
      <ul className="md-ul" key={`ul-${key++}`}>
        {items.map((it, idx) => <li key={idx}>{renderInline(it, `li-${key}-${idx}`)}</li>)}
      </ul>,
    );
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    const fence = line.match(/^```(\w+)?\s*$/);
    if (fence) {
      flushList();
      const lang = fence[1];
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      blocks.push(<CodeBlock code={buf.join('\n')} lang={lang} key={`code-${key++}`} />);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      const content = renderInline(heading[2], `h-${key}`);
      const cls = `md-h md-h${level}`;
      blocks.push(
        level === 1 ? <h1 className={cls} key={`h-${key++}`}>{content}</h1>
        : level === 2 ? <h2 className={cls} key={`h-${key++}`}>{content}</h2>
        : level === 3 ? <h3 className={cls} key={`h-${key++}`}>{content}</h3>
        : <h4 className={cls} key={`h-${key++}`}>{content}</h4>,
      );
      i++;
      continue;
    }

    const listItem = line.match(/^\s*[-*+]\s+(.*)$/);
    if (listItem) {
      listBuf.push(listItem[1]);
      i++;
      continue;
    }

    if (line.trim() === '') {
      flushList();
      i++;
      continue;
    }

    // paragraph (accumulate consecutive non-blank lines)
    flushList();
    const para: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,4})\s/.test(lines[i]) &&
      !/^\s*[-*+]\s/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(<p className="md-p" key={`p-${key++}`}>{renderInline(para.join(' '), `p-${key}`)}</p>);
  }
  flushList();

  return <div className="md">{blocks}</div>;
}
