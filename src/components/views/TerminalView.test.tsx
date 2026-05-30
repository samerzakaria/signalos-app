import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/preact';
import { engineRunning, terminalLines, termInputValue, workspacePath } from '../../state';
import { TerminalView } from './TerminalView';

describe('TerminalView', () => {
  beforeEach(() => {
    engineRunning.value = true;
    workspacePath.value = 'C:/Products/todo';
    terminalLines.value = [];
    termInputValue.value = '';
  });

  it('renders readable ASCII status copy and command placeholder', () => {
    render(<TerminalView />);

    expect(screen.getByText('SignalOS Core running - Python sidecar ready')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('type a command...')).toBeInTheDocument();
  });
});
