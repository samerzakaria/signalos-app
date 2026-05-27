import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/preact';
import { bulkImportAllowRemovals, bulkImportDiff, bulkImportError, bulkImportOpen, bulkImportText, copiedSecret, revealedSecrets, secretsList, tab } from '../../state';

vi.mock('../../js/ipc.js', () => ({
  secrets: {
    applyDiff: vi.fn(),
  },
}));

const ipc = await import('../../js/ipc.js');
const { VaultView } = await import('./VaultView');

describe('VaultView .env import', () => {
  beforeEach(() => {
    cleanup();
    tab.value = 'vault';
    secretsList.value = [];
    revealedSecrets.value = {};
    copiedSecret.value = null;
    bulkImportOpen.value = false;
    bulkImportText.value = '';
    bulkImportDiff.value = null;
    bulkImportError.value = null;
    bulkImportAllowRemovals.value = false;
    vi.clearAllMocks();
    window.openAddSecret = vi.fn();
  });

  it('opens as a real modal overlay instead of normal page content', () => {
    const { container } = render(<VaultView />);

    fireEvent.click(screen.getByRole('button', { name: /Import \.env/i }));

    const modal = screen.getByTestId('bulk-import-modal');
    expect(modal).toHaveClass('modal-overlay', 'open', 'bulk-import-overlay');
    expect(screen.getByRole('dialog', { name: /Import \.env/i })).toHaveClass('modal', 'bulk-import-modal');
    expect(container.querySelector('.modal-backdrop')).toBeNull();
    expect(container.querySelector('.modal-box')).toBeNull();
  });

  it('renders computed diff rows inside the modal without applying changes', async () => {
    (ipc.secrets.applyDiff as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      applied: false,
      added: ['DATABASE_URL_WITH_A_VERY_LONG_NAME_THAT_MUST_WRAP_INSIDE_THE_DIALOG'],
      changed: ['API_KEY'],
      unchanged: ['PUBLIC_URL'],
      removed: ['OLD_SECRET'],
    });

    render(<VaultView />);
    fireEvent.click(screen.getByRole('button', { name: /Import \.env/i }));
    fireEvent.input(screen.getByPlaceholderText(/DATABASE_URL/), {
      target: { value: 'DATABASE_URL=postgres://example' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Compute diff/i }));

    await waitFor(() => {
      expect(screen.getByText(/DATABASE_URL_WITH_A_VERY_LONG_NAME/)).toBeInTheDocument();
    });
    expect(screen.getByText(/OLD_SECRET/)).toHaveClass('removed');
    expect(ipc.secrets.applyDiff).toHaveBeenCalledWith(
      '.env.local',
      'DATABASE_URL=postgres://example',
      false,
    );
  });
});
