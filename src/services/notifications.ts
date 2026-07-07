// notifications.ts — the real notification feed (#22). No new backend: the
// feed is derived from surfaces the frontend already receives or can fetch:
//
//   - get_audit_trail (ipc.audit.list): recent gate signs, reopens, overrides
//     — fetched on popover open to reconcile the feed.
//   - agent events (agentEvents.ts calls notifyFromAgentEvent): incidents
//     (errors, denied/failed tools), gate checkpoints, gate reopens, delivery
//     completions — pushed live as they happen.
//   - local app incidents (app-v2.js calls notifyLocal): sidecar errors.
//
// Unread watermark: the newest item ts the user has acknowledged, persisted
// in localStorage. unreadCount = items newer than the watermark.

import { computed, signal } from '@preact/signals';
import * as ipc from '../js/ipc.js';

export type NotificationKind =
  | 'gate'      // gate signed / gate checkpoint reached
  | 'reopen'    // signed gate reopened (cascade)
  | 'override'  // governed override logged
  | 'error'     // incident: agent/tool/sidecar error
  | 'delivery'  // delivery/run completion
  | 'info';

export interface NotificationItem {
  id: string;
  /** Epoch millis (best effort — unparseable audit ts falls back to now). */
  ts: number;
  kind: NotificationKind;
  text: string;
}

const LAST_SEEN_KEY = 'signalos.notifications.lastSeen.v1';
const FEED_CAP = 50;
const AUDIT_FETCH_LIMIT = 30;

function readLastSeen(): number {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(LAST_SEEN_KEY) : null;
    const parsed = raw === null ? NaN : Number(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  } catch {
    return 0;
  }
}

export const notifications = signal<NotificationItem[]>([]);
export const notificationsOpen = signal<boolean>(false);
/** Watermark: newest acknowledged item ts (epoch ms). */
export const lastSeenTs = signal<number>(readLastSeen());

export const unreadCount = computed(
  () => notifications.value.filter((n) => n.ts > lastSeenTs.value).length,
);

function persistLastSeen(value: number): void {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(LAST_SEEN_KEY, String(value));
  } catch { /* unwritable storage — watermark stays session-local */ }
}

export function parseTs(raw: unknown): number {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    // Heuristic: values that look like epoch seconds get promoted to ms.
    return raw < 1e12 ? raw * 1000 : raw;
  }
  if (typeof raw === 'string' && raw.trim()) {
    const ms = Date.parse(raw);
    if (Number.isFinite(ms)) return ms;
  }
  return Date.now();
}

// ── Normalization ──────────────────────────────────────────────────────────

export interface AuditEntryLike {
  ts?: string | number;
  timestamp?: string | number;
  action?: string;
  gate?: string | number;
  [key: string]: unknown;
}

function kindForAuditAction(action: string): NotificationKind {
  const a = action.toLowerCase();
  if (a.includes('reopen') || a.includes('unwaive') || a.includes('invalidat')) return 'reopen';
  if (a.includes('override')) return 'override';
  if (a.includes('error') || a.includes('fail') || a.includes('incident')) return 'error';
  if (a.includes('closeout') || a.includes('handoff') || a.includes('deliver')) return 'delivery';
  if (a.includes('sign') || a.includes('gate')) return 'gate';
  return 'info';
}

/** Normalize raw audit-trail entries into feed items (newest-first). */
export function collect(entries: unknown): NotificationItem[] {
  if (!Array.isArray(entries)) return [];
  const items: NotificationItem[] = [];
  for (const raw of entries) {
    if (!raw || typeof raw !== 'object') continue;
    const entry = raw as AuditEntryLike;
    const action = typeof entry.action === 'string' ? entry.action.trim() : '';
    if (!action) continue;
    const tsRaw = entry.ts ?? entry.timestamp;
    const ts = parseTs(tsRaw);
    const gate = entry.gate !== undefined && entry.gate !== null && entry.gate !== ''
      ? ` (${String(entry.gate)})`
      : '';
    items.push({
      // Audit entries have no id; ts+action is stable across refetches so
      // reconciliation dedupes instead of duplicating.
      id: `audit-${String(tsRaw ?? ts)}-${action}`,
      ts,
      kind: kindForAuditAction(action),
      text: action + gate,
    });
  }
  return items.sort((a, b) => b.ts - a.ts);
}

function mergeIntoFeed(incoming: NotificationItem[]): void {
  if (incoming.length === 0) return;
  const seen = new Set(notifications.value.map((n) => n.id));
  const fresh = incoming.filter((n) => !seen.has(n.id));
  if (fresh.length === 0) return;
  notifications.value = [...fresh, ...notifications.value]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, FEED_CAP);
}

// ── Live pushes ────────────────────────────────────────────────────────────

let liveSeq = 0;

/** Push a locally-generated event (e.g. sidecar error) into the feed. */
export function notifyLocal(kind: NotificationKind, text: string): void {
  const message = (text || '').trim();
  if (!message) return;
  liveSeq += 1;
  mergeIntoFeed([{ id: `live-${Date.now()}-${liveSeq}`, ts: Date.now(), kind, text: message }]);
}

/**
 * Hook for agentEvents.handle(): translate incident/gate/reopen/completion
 * agent events into feed items as they stream in. Unknown/chatty types
 * (text, tool_done, …) are ignored.
 */
export function notifyFromAgentEvent(evt: {
  type?: string;
  gate?: string;
  title?: string;
  tool?: string;
  error?: string;
  reason?: string;
  by?: string;
  [key: string]: unknown;
}): void {
  if (!evt || typeof evt !== 'object') return;
  switch (evt.type) {
    case 'error':
      notifyLocal('error', 'Agent run failed' + (typeof evt.error === 'string' && evt.error ? `: ${evt.error}` : '.'));
      break;
    case 'tool_error': {
      const tool = typeof evt.tool === 'string' && evt.tool ? evt.tool : 'tool';
      notifyLocal('error', `Tool ${tool} failed` + (typeof evt.error === 'string' && evt.error ? `: ${evt.error}` : '.'));
      break;
    }
    case 'tool_denied': {
      const tool = typeof evt.tool === 'string' && evt.tool ? evt.tool : 'tool';
      notifyLocal('error', `Tool ${tool} denied by enforcement` + (typeof evt.reason === 'string' && evt.reason ? `: ${evt.reason}` : '.'));
      break;
    }
    case 'gate': {
      const gate = typeof evt.gate === 'string' && evt.gate ? evt.gate : 'Gate';
      const title = typeof evt.title === 'string' && evt.title ? ` — ${evt.title}` : '';
      notifyLocal('gate', `${gate} review is waiting for your verdict${title}`);
      break;
    }
    case 'gate_reopened': {
      const gate = typeof evt.gate === 'string' && evt.gate ? evt.gate : 'Gate';
      const by = typeof evt.by === 'string' && evt.by ? evt.by : 'user';
      notifyLocal('reopen', `${gate} reopened by ${by}`);
      break;
    }
    case 'delivery_complete':
    case 'closeout':
      notifyLocal('delivery', 'Delivery completed.');
      break;
    default:
      break; // streaming text / tool_done etc. are not notifications
  }
}

// ── Popover actions ────────────────────────────────────────────────────────

/** Fetch the recent audit trail and reconcile it into the feed. */
export async function reconcileFromAudit(): Promise<void> {
  try {
    const entries = await ipc.audit.list(AUDIT_FETCH_LIMIT);
    mergeIntoFeed(collect(entries));
  } catch {
    // No workspace / engine down — the live feed still works.
  }
}

/** Toggle the bell popover; opening reconciles against the audit trail. */
export async function toggleNotifications(): Promise<void> {
  const next = !notificationsOpen.value;
  notificationsOpen.value = next;
  if (next) await reconcileFromAudit();
}

export function closeNotifications(): void {
  notificationsOpen.value = false;
}

/** Move the unread watermark past everything currently in the feed. */
export function markAllRead(): void {
  const newest = notifications.value.reduce((max, n) => Math.max(max, n.ts), lastSeenTs.value);
  lastSeenTs.value = newest;
  persistLastSeen(newest);
}

/** Test seam: reset module state between tests. */
export function __resetNotificationsForTests(): void {
  notifications.value = [];
  notificationsOpen.value = false;
  lastSeenTs.value = 0;
  liveSeq = 0;
  try {
    if (typeof localStorage !== 'undefined') localStorage.removeItem(LAST_SEEN_KEY);
  } catch { /* ignore */ }
}
