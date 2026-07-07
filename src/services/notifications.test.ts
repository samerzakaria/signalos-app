import { describe, it, expect, vi, beforeEach } from 'vitest';

// Notifications (#22): audit normalization, unread watermark math,
// mark-all-read persistence, live agent-event pushes, popover reconcile.

vi.mock('../js/ipc.js', () => ({
  signal: { runAndWait: vi.fn(), run: vi.fn() },
  audit: { list: vi.fn() },
}));

const ipc = await import('../js/ipc.js');
const {
  notifications,
  notificationsOpen,
  lastSeenTs,
  unreadCount,
  collect,
  parseTs,
  notifyLocal,
  notifyFromAgentEvent,
  toggleNotifications,
  closeNotifications,
  reconcileFromAudit,
  markAllRead,
  __resetNotificationsForTests,
} = await import('./notifications');

const auditList = ipc.audit.list as ReturnType<typeof vi.fn>;

beforeEach(() => {
  auditList.mockReset();
  __resetNotificationsForTests();
});

describe('collect (audit normalization)', () => {
  it('normalizes entries into {id, ts, kind, text}, newest-first', () => {
    const items = collect([
      { ts: '2026-07-01T10:00:00Z', action: 'gate.signed', gate: 'G2' },
      { ts: '2026-07-03T10:00:00Z', action: 'gate.reopened', gate: 'G2' },
      { ts: '2026-07-02T10:00:00Z', action: 'enforcement.override' },
    ]);
    expect(items.map((i) => i.kind)).toEqual(['reopen', 'override', 'gate']);
    expect(items[0].ts).toBeGreaterThan(items[1].ts);
    expect(items[2].text).toBe('gate.signed (G2)');
    expect(items[0].id).toContain('gate.reopened');
  });

  it('classifies error/delivery/info actions', () => {
    const kinds = collect([
      { ts: '2026-07-01T10:00:03Z', action: 'brownfield.audit-error' },
      { ts: '2026-07-01T10:00:02Z', action: 'delivery.closeout' },
      { ts: '2026-07-01T10:00:01Z', action: 'brain.note-added' },
    ]).map((i) => i.kind);
    expect(kinds).toEqual(['error', 'delivery', 'info']);
  });

  it('tolerates garbage input', () => {
    expect(collect(null)).toEqual([]);
    expect(collect('nope')).toEqual([]);
    expect(collect([null, 42, {}, { ts: 'x' }])).toEqual([]);
  });

  it('parseTs handles ISO strings, epoch seconds, and epoch millis', () => {
    expect(parseTs('2026-07-01T10:00:00Z')).toBe(Date.parse('2026-07-01T10:00:00Z'));
    expect(parseTs(1750000000)).toBe(1750000000 * 1000); // seconds promoted
    expect(parseTs(1750000000000)).toBe(1750000000000);  // already millis
    const now = Date.now();
    expect(parseTs('not-a-date')).toBeGreaterThanOrEqual(now); // fallback
  });
});

describe('unread watermark math', () => {
  it('counts only items newer than the watermark', () => {
    notifications.value = [
      { id: 'a', ts: 3000, kind: 'gate', text: 'newest' },
      { id: 'b', ts: 2000, kind: 'error', text: 'middle' },
      { id: 'c', ts: 1000, kind: 'info', text: 'oldest' },
    ];
    lastSeenTs.value = 0;
    expect(unreadCount.value).toBe(3);
    lastSeenTs.value = 2000; // watermark AT an item ts — that item is read
    expect(unreadCount.value).toBe(1);
    lastSeenTs.value = 3000;
    expect(unreadCount.value).toBe(0);
  });

  it('markAllRead moves the watermark to the newest item and persists it', () => {
    notifications.value = [
      { id: 'a', ts: 5555, kind: 'gate', text: 'x' },
      { id: 'b', ts: 4444, kind: 'info', text: 'y' },
    ];
    markAllRead();
    expect(lastSeenTs.value).toBe(5555);
    expect(unreadCount.value).toBe(0);
    expect(localStorage.getItem('signalos.notifications.lastSeen.v1')).toBe('5555');
  });

  it('markAllRead never moves the watermark backwards', () => {
    lastSeenTs.value = 9000;
    notifications.value = [{ id: 'a', ts: 100, kind: 'info', text: 'old' }];
    markAllRead();
    expect(lastSeenTs.value).toBe(9000);
  });
});

describe('live pushes', () => {
  it('notifyLocal prepends items and skips blanks', () => {
    notifyLocal('error', 'Engine error: boom');
    notifyLocal('error', '   ');
    expect(notifications.value).toHaveLength(1);
    expect(notifications.value[0]).toMatchObject({ kind: 'error', text: 'Engine error: boom' });
    expect(unreadCount.value).toBe(1);
  });

  it('caps the feed at 50 items', () => {
    for (let i = 0; i < 60; i++) notifyLocal('info', `event ${i}`);
    expect(notifications.value).toHaveLength(50);
    // Newest survive the cap.
    expect(notifications.value[0].text).toBe('event 59');
  });

  it('notifyFromAgentEvent maps incident/gate/reopen events and ignores chatty ones', () => {
    notifyFromAgentEvent({ type: 'text', text: 'streaming…' });
    notifyFromAgentEvent({ type: 'tool_done', tool: 'write_file' });
    expect(notifications.value).toHaveLength(0);

    notifyFromAgentEvent({ type: 'error', error: 'provider exploded' });
    notifyFromAgentEvent({ type: 'tool_error', tool: 'run_tests', error: '3 failed' });
    notifyFromAgentEvent({ type: 'tool_denied', tool: 'bash', reason: 'wave frozen' });
    notifyFromAgentEvent({ type: 'gate', gate: 'G3', title: 'Design review' });
    notifyFromAgentEvent({ type: 'gate_reopened', gate: 'G2', by: 'Samer' });

    const texts = notifications.value.map((n) => `${n.kind}:${n.text}`);
    expect(texts).toEqual([
      'reopen:G2 reopened by Samer',
      'gate:G3 review is waiting for your verdict — Design review',
      'error:Tool bash denied by enforcement: wave frozen',
      'error:Tool run_tests failed: 3 failed',
      'error:Agent run failed: provider exploded',
    ]);
  });
});

describe('popover open / reconcile', () => {
  it('opening fetches the audit trail and merges it (dedup by id)', async () => {
    auditList.mockResolvedValue([
      { ts: '2026-07-05T10:00:00Z', action: 'gate.signed', gate: 'G1' },
      { ts: '2026-07-05T11:00:00Z', action: 'gate.signed', gate: 'G2' },
    ]);
    await toggleNotifications();
    expect(notificationsOpen.value).toBe(true);
    expect(auditList).toHaveBeenCalledWith(expect.any(Number));
    expect(notifications.value).toHaveLength(2);

    // Re-reconciling the same entries does not duplicate.
    await reconcileFromAudit();
    expect(notifications.value).toHaveLength(2);
  });

  it('closing does not refetch', async () => {
    auditList.mockResolvedValue([]);
    await toggleNotifications(); // open (1 fetch)
    await toggleNotifications(); // close
    expect(notificationsOpen.value).toBe(false);
    expect(auditList).toHaveBeenCalledTimes(1);
    closeNotifications();
    expect(notificationsOpen.value).toBe(false);
  });

  it('audit fetch failure leaves the live feed intact', async () => {
    notifyLocal('error', 'live incident');
    auditList.mockRejectedValue(new Error('no workspace'));
    await toggleNotifications();
    expect(notificationsOpen.value).toBe(true);
    expect(notifications.value).toHaveLength(1);
  });

  it('live events merge with reconciled audit items in ts order', async () => {
    auditList.mockResolvedValue([
      { ts: '2020-01-01T00:00:00Z', action: 'gate.signed', gate: 'G0' },
    ]);
    notifyLocal('error', 'fresh incident'); // ts = now >> 2020
    await reconcileFromAudit();
    expect(notifications.value.map((n) => n.text)).toEqual([
      'fresh incident',
      'gate.signed (G0)',
    ]);
  });
});
