import { render, screen, fireEvent } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';
import { Markdown, CodeBlock } from './markdown';

describe('Markdown', () => {
  it('renders headings, bold, inline code and lists', () => {
    const { container } = render(
      <Markdown text={'# Title\n\nSome **bold** and `code`.\n\n- one\n- two'} />,
    );
    expect(container.querySelector('h1.md-h1')).toHaveTextContent('Title');
    expect(container.querySelector('strong')).toHaveTextContent('bold');
    expect(container.querySelector('.md-inline-code')).toHaveTextContent('code');
    expect(container.querySelectorAll('.md-ul li')).toHaveLength(2);
  });

  it('renders fenced code blocks with a language label', () => {
    const { container } = render(
      <Markdown text={'```ts\nconst x = 1;\n```'} />,
    );
    expect(container.querySelector('.md-code')).toBeTruthy();
    expect(container.querySelector('.md-code-lang')).toHaveTextContent('ts');
    expect(container.querySelector('.md-tok-kw')).toBeTruthy();
  });
});

describe('CodeBlock copy', () => {
  it('copies code to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<CodeBlock code="hello()" lang="js" />);
    fireEvent.click(screen.getByRole('button', { name: /copy code/i }));
    expect(writeText).toHaveBeenCalledWith('hello()');
  });
});
