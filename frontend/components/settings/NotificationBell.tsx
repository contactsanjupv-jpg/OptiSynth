
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, X, CheckCheck, Check, ArrowUpRight } from "lucide-react";
import {
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "@/lib/api/admin";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/notification-drawer.css";

export function NotificationBell() {
  const dialogRef = useRef<HTMLDialogElement>(null);

  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState<number[]>([]);
  const [markingAll, setMarkingAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await getNotifications();
      setItems(result.items);
      setUnread(result.unread);
      setError(null);
    } catch {
      setError("Could not load notifications. Please try again.");
    }
  }, []);

  useEffect(() => {
    const refresh = () => {
      getNotifications(true)
        .then((result) => setUnread(result.unread))
        .catch(() => undefined);
    };

    refresh();

    const timer = window.setInterval(refresh, 60000);
    window.addEventListener("notifications-changed", refresh);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener("notifications-changed", refresh);
    };
  }, []);

  const openDrawer = () => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;

    dialog.showModal();
    setLoading(true);
    void load().finally(() => setLoading(false));
  };

  const closeDrawer = () => {
    dialogRef.current?.close();
  };

  async function markRead(id: number) {
    if (updating.includes(id)) return;

    setUpdating((current) => [...current, id]);

    try {
      await markNotificationRead(id);

      setItems((current) =>
        current.map((item) =>
          item.id === id
            ? { ...item, read_at: new Date().toISOString() }
            : item
        )
      );

      setUnread((current) => Math.max(0, current - 1));
      window.dispatchEvent(new Event("notifications-changed"));
    } catch {
      setError("Could not update this notification. Please try again.");
    } finally {
      setUpdating((current) => current.filter((value) => value !== id));
    }
  }

  async function markAllRead() {
    if (unread === 0 || markingAll) return;

    setMarkingAll(true);

    try {
      await markAllNotificationsRead();

      const now = new Date().toISOString();

      setItems((current) =>
        current.map((item) => ({
          ...item,
          read_at: item.read_at ?? now,
        }))
      );

      setUnread(0);
      window.dispatchEvent(new Event("notifications-changed"));
    } catch {
      setError("Could not update notifications. Please try again.");
    } finally {
      setMarkingAll(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="bell"
        onClick={openDrawer}
        aria-label={
          unread > 0 ? `Notifications, ${unread} unread` : "Notifications"
        }
        aria-haspopup="dialog"
        aria-expanded={dialogRef.current?.open ?? false}
      >
        <Bell size={18} aria-hidden="true" />
        {unread > 0 && (
          <span className="bell__count" aria-hidden="true">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      <dialog
        ref={dialogRef}
        className="notification-drawer"
        aria-labelledby="notification-drawer-title"
        onClick={(event) => {
          if (event.target === event.currentTarget) closeDrawer();
        }}
      >
        <section className="notification-drawer__panel">
          <header className="notification-drawer__header">
            <div>
              <h2 id="notification-drawer-title">Notifications</h2>
            </div>

            <button
              type="button"
              className="notification-drawer__icon-btn"
              onClick={closeDrawer}
              aria-label="Close notifications"
              autoFocus
            >
              <X size={19} />
            </button>
          </header>

          <div className="notification-drawer__toolbar">
            <span>Recent activity</span>

            <button
              type="button"
              className="notification-drawer__mark-all"
              onClick={markAllRead}
              disabled={unread === 0 || markingAll}
            >
              <CheckCheck size={15} />
              {markingAll ? "Updating..." : "Mark all as read"}
            </button>
          </div>

          <div className="notification-drawer__body" aria-live="polite">
            {error && (
              <div className="notification-drawer__error" role="alert">
                {error}
                <button type="button" onClick={() => void load()}>
                  Retry
                </button>
              </div>
            )}

            {loading && items.length === 0 && (
              <p className="notification-drawer__empty">
                Loading notifications...
              </p>
            )}

            {!loading && items.length === 0 && !error && (
              <div className="notification-drawer__empty">
                <Bell size={25} strokeWidth={1.5} />
                <strong>No notifications yet</strong>
                <p>
                  We&apos;ll let you know when evidence needs review, a decision
                  is ready, or someone joins your organization.
                </p>
              </div>
            )}

            {items.map((item) => (
              <article
                key={item.id}
                className={`notification-item ${
                  item.read_at
                    ? "notification-item--read"
                    : "notification-item--unread"
                }`}
              >
                <span
                  className="notification-item__indicator"
                  aria-label={item.read_at ? "Read" : "Unread"}
                />

                <div className="notification-item__content">
                  {item.link ? (
                    <Link
                      href={item.link}
                      className="notification-item__title"
                      onClick={() => {
                        if (!item.read_at) void markRead(item.id);
                        closeDrawer();
                      }}
                    >
                      {item.title}
                      <ArrowUpRight size={14} />
                    </Link>
                  ) : (
                    <strong className="notification-item__title">
                      {item.title}
                    </strong>
                  )}

                  {item.body && (
                    <p className="notification-item__description">
                      {item.body}
                    </p>
                  )}

                  <time
                    className="notification-item__time"
                    dateTime={item.created_at}
                  >
                    {formatRelativeTime(item.created_at)}
                  </time>
                </div>

                {!item.read_at && (
                  <button
                    type="button"
                    className="notification-item__read-btn"
                    onClick={() => void markRead(item.id)}
                    disabled={updating.includes(item.id)}
                    aria-label={`Mark ${item.title} as read`}
                    title="Mark as read"
                  >
                    <Check size={16} />
                  </button>
                )}
              </article>
            ))}
          </div>

          <footer className="notification-drawer__footer">
            <Link
              href="/settings?tab=notifications"
              onClick={closeDrawer}
            >
              View all notifications
              <ArrowUpRight size={15} />
            </Link>
          </footer>
        </section>
      </dialog>
    </>
  );
}
