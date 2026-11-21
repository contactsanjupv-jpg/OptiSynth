"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { ApiRequestError } from "@/lib/api/http";
import { clearSessionCache } from "@/lib/auth/session";
import {
  listSessions, listSecurityEvents, revokeSession, revokeOtherSessions, signOutEverywhere,
  type SecurityEvent, type SessionRow,
} from "@/lib/api/admin";
import { formatRelativeTime } from "@/lib/utils/format";

export function SecurityTab() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null); // "session:ID", "others" or "all"
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listSessions(), listSecurityEvents()])
      .then(([s, e]) => { setSessions(s); setEvents(e); setError(null); })
      .catch(() => setError("Could not load security details. Try again."));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>, done: string, leave = false) {
    setError(null); setMessage(null);
    try {
      await action();
      setConfirm(null);
      if (leave) { clearSessionCache(); router.replace("/login"); return; }
      setMessage(done); load();
    } catch (err) { setError(err instanceof ApiRequestError ? err.message : "Could not complete that. Try again."); }
  }

  const others = (sessions ?? []).filter((s) => !s.current).length;

  return (
    <div className="settings-grid">
      <Card padding="lg">
        <CardHeader><CardTitle>Active sessions</CardTitle></CardHeader>
        {error && <ErrorCallout message={error} />}
        {message && <p className="settings-ok" role="status">{message}</p>}
        {!sessions ? <p className="settings-note">Loading sessions</p> : (
          <ul className="settings-list">
            {sessions.map((s) => (
              <li key={s.id}>
                <span>
                  {s.device}{s.current ? " (this session)" : ""}
                  <span className="settings-list__meta">{s.address ?? "Unknown address"}, started {formatRelativeTime(s.created_at)}</span>
                </span>
                {confirm === `session:${s.id}` ? (
                  <span className="settings-actions">
                    <Button size="sm" variant="primary" onClick={() => run(() => revokeSession(s.id), "Session ended", s.current)}>
                      {s.current ? "Sign out" : "End session"}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
                  </span>
                ) : (
                  <Button size="sm" variant="secondary" onClick={() => setConfirm(`session:${s.id}`)}>{s.current ? "Sign out" : "End"}</Button>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="settings-actions" style={{ marginTop: 16 }}>
          {confirm === "others" ? (
            <>
              <Button size="sm" variant="primary" onClick={() => run(revokeOtherSessions, "Other sessions ended")}>End {others} other {others === 1 ? "session" : "sessions"}</Button>
              <Button size="sm" variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
            </>
          ) : confirm === "all" ? (
            <>
              <Button size="sm" variant="primary" onClick={() => run(signOutEverywhere, "", true)}>Sign out everywhere</Button>
              <Button size="sm" variant="secondary" onClick={() => setConfirm(null)}>Cancel</Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="secondary" disabled={others === 0} onClick={() => setConfirm("others")}>End other sessions</Button>
              <Button size="sm" variant="secondary" onClick={() => setConfirm("all")}>Sign out everywhere</Button>
            </>
          )}
        </div>
      </Card>
      <Card padding="lg">
        <CardHeader><CardTitle>Your recent security events</CardTitle></CardHeader>
        {events.length === 0 ? <p className="settings-note">No events yet.</p> : (
          <ul className="settings-list">
            {events.map((e) => (
              <li key={e.id}><span>{e.label}</span><span className="settings-list__meta">{formatRelativeTime(e.created_at)}</span></li>
            ))}
          </ul>
        )}
        <p className="settings-note" style={{ marginTop: 12 }}>Failed sign-in attempts are limited but not listed here.</p>
      </Card>
    </div>
  );
}
