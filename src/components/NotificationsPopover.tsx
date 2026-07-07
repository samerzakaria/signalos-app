// NotificationsPopover.tsx — the bell popover (#22). Reuses the enf-popover
// shell styling; lists recent feed items newest-first with kind icons, an
// unread marker, and a "Mark all read" action.

import {
  notifications,
  notificationsOpen,
  lastSeenTs,
  markAllRead,
  unreadCount,
  type NotificationKind,
} from '../services/notifications';
import { formatTs } from '../js/util.js';

const KIND_ICONS: Record<NotificationKind, string> = {
  gate: 'ti-signature',
  reopen: 'ti-rotate-2',
  override: 'ti-alert-triangle',
  error: 'ti-alert-circle',
  delivery: 'ti-circle-check',
  info: 'ti-info-circle',
};

export function NotificationsPopover() {
  const items = notifications.value;
  const watermark = lastSeenTs.value;
  const cls = notificationsOpen.value ? 'enf-popover notif-popover open' : 'enf-popover notif-popover';

  return (
    <div className={cls} id="notifPopover" data-testid="notif-popover" onClick={(e) => e.stopPropagation()}>
      <div className="enf-pop-head">
        <div className="enf-pop-title">Notifications</div>
        <div className="enf-pop-sub" data-testid="notif-sub">
          {items.length === 0
            ? 'Nothing yet — gate reviews, reopens, and incidents land here.'
            : unreadCount.value > 0
              ? `${unreadCount.value} unread`
              : 'All caught up'}
        </div>
      </div>
      <div className="notif-list" data-testid="notif-list">
        {items.map((n) => (
          <div
            className={'notif-row' + (n.ts > watermark ? ' unread' : '')}
            key={n.id}
            data-testid="notif-item"
          >
            <div className={`notif-ic ${n.kind}`}>
              <i className={`ti ${KIND_ICONS[n.kind] || KIND_ICONS.info}`}></i>
            </div>
            <div className="notif-tx">
              <div className="notif-text">{n.text}</div>
              <div className="notif-meta">{formatTs(n.ts)}</div>
            </div>
            {n.ts > watermark ? <span className="notif-dot" data-testid="notif-unread-dot"></span> : null}
          </div>
        ))}
      </div>
      {items.length > 0 ? (
        <div className="enf-pop-foot">
          <button
            className="btn btn-soft"
            style={{ fontSize: '12px', padding: '7px 12px' }}
            data-testid="notif-mark-read"
            onClick={() => markAllRead()}
          >
            <i className="ti ti-checks"></i> Mark all read
          </button>
        </div>
      ) : null}
    </div>
  );
}
