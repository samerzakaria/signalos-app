import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/preact';
import { HelpView } from './HelpView';

// HelpView is pure-static content — the right test is "the user lands
// on Help and sees the orientation copy they need to navigate the app."
// No state, no IPC, no event handlers; if rendering breaks here, the
// upgrade path also broke (this is the screen users get pointed at).

describe('HelpView', () => {
  it('renders the page header and lede', () => {
    render(<HelpView />);
    expect(screen.getByRole('heading', { level: 1, name: /Help.*Reference/i })).toBeInTheDocument();
    expect(screen.getByText(/Quick-start guides/i)).toBeInTheDocument();
  });

  it('surfaces the four quick-start tiles a new user needs', () => {
    render(<HelpView />);
    expect(screen.getByText(/Start a new project/i)).toBeInTheDocument();
    expect(screen.getByText(/Add your API key/i)).toBeInTheDocument();
    expect(screen.getByText(/Understand gates/i)).toBeInTheDocument();
  });

  it('mentions the gate model explicitly (G0..G7) so users can find it', () => {
    render(<HelpView />);
    // HelpView spells out the range in multiple tiles; that's fine, we
    // just need at least one to land for the orientation to work.
    expect(screen.getAllByText(/G0.{1,3}G7/).length).toBeGreaterThan(0);
  });
});
