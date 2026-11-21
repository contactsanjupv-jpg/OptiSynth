"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { getNotifications, markAllNotificationsRead, markNotificationRead, type NotificationItem } from "@/lib/api/admin";
import { formatRelativeTime } from "@/lib/utils/format";

export function NotificationsTab() {
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getNotifications().then((r) => { setItems(r.items); setUnread(r.unread); setError(null); })
      .catch(() => setError("Could not load notifications. Try again."));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function read(id: number) {
    try { await markNotificationRead(id); load(); window.dispatchEvent(new Event("notifications-changed")); } catch { /* shown on next load */ }
  }
  async function readAll() {
    try { await markAllNotificationsRead(); load(); window.dispatchEvent(new Event("notifications-changed")); } catch { setError("Could not update notifications. Try again."); }
  }

  return (
    <Card padding="lg">
      {error && <ErrorCallout message={error} />}
      {items && (
        <div className="settings-actions" style={{ marginBottom: 12, justifyContent: "space-between", alignItems: "center" }}>
          <span className="settings-lead" style={{ margin: 0 }}>{unread === 0 ? "You are up to date." : `${unread} unread`}</span>
          <Button size="sm" variant="secondary" disabled={unread === 0} onClick={readAll}>Mark all as read</Button>
        </div>
      )}
      {items && items.length === 0 && <p className="settings-note">You have no notifications. You are told when evidence is ready for review, a decision is needed, a program is approved or rejected, or someone joins.</p>}
      {items && items.length > 0 && (
        <ul className="settings-list">
          {items.map((n) => (
            <li key={n.id}>
              <span className={n.read_at ? "" : "settings-unread"}>
                {n.link ? <Link href={n.link} onClick={() => !n.read_at && read(n.id)} className="ov-link ov-link--strong">{n.title}</Link> : n.title}
                <span className="settings-list__meta">{n.body ? `${n.body}, ` : ""}{formatRelativeTime(n.created_at)}</span>
              </span>
              {!n.read_at && <Button size="sm" variant="secondary" onClick={() => read(n.id)}>Mark as read</Button>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
