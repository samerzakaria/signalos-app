import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/preact';
import { PreviewView } from './PreviewView';
import {
  previewDevice,
  previewUrl,
  previewStatus,
  workspacePath,
} from '../../state';

// PreviewView is a state-machine view: idle / starting / installing /
// running / error. The UX depends on the status -> label mapping being
// right, because users use this to decide whether to wait, retry, or
// open externally. Locking the mapping under a test prevents UX drift.

describe('PreviewView status -> label mapping', () => {
  beforeEach(() => {
    // Reset signals between cases so each test starts from a known state.
    previewDevice.value = 'desktop';
    previewUrl.value = '';
    previewStatus.value = 'idle';
    workspacePath.value = '';
  });

  it('shows "No workspace" when no workspace is selected', () => {
    render(<PreviewView />);
    expect(screen.getByText('No workspace')).toBeInTheDocument();
  });

  it('shows "No preview running" once a workspace is set but preview is idle', () => {
    workspacePath.value = 'C:/fake/ws';
    render(<PreviewView />);
    expect(screen.getByText('No preview running')).toBeInTheDocument();
  });

  it('shows "Starting…" when preview is launching', () => {
    workspacePath.value = 'C:/fake/ws';
    previewStatus.value = 'starting';
    render(<PreviewView />);
    // Rendered both in the status bar and the empty stage; either is fine.
    expect(screen.getAllByText(/Starting/).length).toBeGreaterThan(0);
  });

  it('shows "Installing…" when preview is installing deps', () => {
    workspacePath.value = 'C:/fake/ws';
    previewStatus.value = 'installing';
    render(<PreviewView />);
    expect(screen.getAllByText(/Installing/).length).toBeGreaterThan(0);
  });

  it('shows "Crashed" when preview hit an error', () => {
    workspacePath.value = 'C:/fake/ws';
    previewStatus.value = 'error';
    render(<PreviewView />);
    expect(screen.getByText('Crashed')).toBeInTheDocument();
  });

  it('shows the live URL when running, and exposes Reload/Stop controls', () => {
    workspacePath.value = 'C:/fake/ws';
    previewStatus.value = 'running';
    previewUrl.value = 'http://localhost:5173';
    render(<PreviewView />);
    expect(screen.getByText('http://localhost:5173')).toBeInTheDocument();
    expect(screen.getByLabelText('Reload')).toBeInTheDocument();
    expect(screen.getByLabelText('Stop')).toBeInTheDocument();
    expect(screen.getByLabelText('Open externally')).toBeInTheDocument();
  });

  it('marks the active device segment', () => {
    workspacePath.value = 'C:/fake/ws';
    previewDevice.value = 'mobile';
    const { container } = render(<PreviewView />);
    const segments = container.querySelectorAll('.dev-b');
    const active = Array.from(segments).filter((el) => el.classList.contains('active'));
    expect(active).toHaveLength(1);
    expect(active[0].getAttribute('data-device')).toBe('mobile');
  });
});
