import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Bell popover (#22): newest-first listing with kind icons, unread markers,
// mark-all-read, and empty state.

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
  audit: { list: vi.fn() },
}));

const {
  notifications,
  notificationsOpen,
  lastSeenTs,
  unreadCount,
  __resetNotificationsForTests,
} = await import('../services/notifications');
const { NotificationsPopover } = await import('./NotificationsPopover');

beforeEach(() => {
  cleanup();
  __resetNotificationsForTests();
  notificationsOpen.value = true;
});

describe('NotificationsPopover', () => {
  it('renders items newest-first with kind icons and unread markers', () => {
    lastSeenTs.value = 1500;
    notifications.value = [
      { id: 'n2', ts: 2000, kind: 'error', text: 'Tool run_tests failed' },
      { id: 'n1', ts: 1000, kind: 'gate', text: 'gate.signed (G1)' },
    ];
    render(<NotificationsPopover />);

    const rows = screen.getAllByTestId('notif-item');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Tool run_tests failed');
    expect(rows[1].textContent).toContain('gate.signed (G1)');
    // Only the newer item is unread.
    expect(rows[0].className).toContain('unread');
    expect(rows[1].className).not.toContain('unread');
    expect(screen.getAllByTestId('notif-unread-dot')).toHaveLength(1);
    expect(rows[0].querySelector('.ti-alert-circle')).toBeTruthy();
    expect(rows[1].querySelector('.ti-signature')).toBeTruthy();
    expect(screen.getByTestId('notif-sub').textContent).toContain('1 unread');
  });

  it('mark all read clears the unread state', () => {
    notifications.value = [
      { id: 'n1', ts: 3000, kind: 'reopen', text: 'G2 reopened by Samer' },
    ];
    render(<NotificationsPopover />);
    expect(unreadCount.value).toBe(1);

    fireEvent.click(screen.getByTestId('notif-mark-read'));

    expect(unreadCount.value).toBe(0);
    expect(lastSeenTs.value).toBe(3000);
    expect(screen.getByTestId('notif-sub').textContent).toContain('All caught up');
  });

  it('shows an honest empty state (and no mark-read button)', () => {
    render(<NotificationsPopover />);
    expect(screen.getByTestId('notif-sub').textContent).toContain('Nothing yet');
    expect(screen.queryByTestId('notif-mark-read')).toBeNull();
  });

  it('is hidden while closed (popover class contract)', () => {
    notificationsOpen.value = false;
    render(<NotificationsPopover />);
    expect(screen.getByTestId('notif-popover').className).not.toContain('open');
    cleanup();
    notificationsOpen.value = true;
    render(<NotificationsPopover />);
    expect(screen.getByTestId('notif-popover').className).toContain('open');
  });
});
