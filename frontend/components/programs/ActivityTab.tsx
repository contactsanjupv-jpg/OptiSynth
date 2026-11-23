"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { addComment, getTimeline, listComments, type Comment, type TimelineEntry } from "@/lib/api/workspace";
import { formatRelativeTime } from "@/lib/utils/format";
import { messageOf } from "./shared";
import "@/styles/components/program-workspace.css";

export function ActivityTab({ changeCaseId, canDecide }: { changeCaseId: number; canDecide: boolean }) {
  const [comments, setComments] = useState<Comment[] | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);
  const [body, setBody] = useState("");
  const [kind, setKind] = useState<"comment" | "decision">("comment");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([listComments(changeCaseId), getTimeline(changeCaseId)])
      .then(([c, t]) => { setComments(c); setTimeline(t); setError(null); })
      .catch(() => setError("Could not load activity. Try again."));
  }, [changeCaseId]);
  useEffect(() => { load(); }, [load]);

  async function post(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { setComments(await addComment(changeCaseId, kind, body)); setBody(""); setKind("comment"); getTimeline(changeCaseId).then(setTimeline).catch(() => undefined); }
    catch (err) { setError(messageOf(err)); } finally { setBusy(false); }
  }

  return (
    <div className="pw-grid">
      <Card padding="lg">
        <CardHeader><CardTitle>Discussion and decisions</CardTitle></CardHeader>
        {error && <ErrorCallout message={error} />}
        <form className="pw-form" onSubmit={post}>
          <label className="pw-label" htmlFor="pw-comment">Add to the discussion</label>
          <textarea id="pw-comment" className="pw-field" rows={3} maxLength={4000} value={body} required onChange={(e) => setBody(e.target.value)} />
          <div className="pw-row">
            {canDecide && (
              <div>
                <label className="pw-label" htmlFor="pw-kind">Type</label>
                <select id="pw-kind" className="pw-field" value={kind} onChange={(e) => setKind(e.target.value as "comment" | "decision")}>
                  <option value="comment">Comment</option>
                  <option value="decision">Decision</option>
                </select>
              </div>
            )}
            <Button type="submit" size="sm" variant="primary" loading={busy} disabled={!body.trim()}>Post</Button>
          </div>
        </form>
        {comments && comments.length === 0 && <p className="pw-muted" style={{ marginTop: 12 }}>No comments yet.</p>}
        {comments?.map((c) => (
          <div key={c.id} className="pw-comment">
            <strong>{c.kind === "decision" ? "Decision" : "Comment"}</strong>
            <span className="pw-list__meta">{c.author ?? "Unknown"}, {formatRelativeTime(c.created_at)}</span>
            {c.body}
          </div>
        ))}
      </Card>
      <Card padding="lg">
        <CardHeader><CardTitle>Activity timeline</CardTitle></CardHeader>
        {timeline && timeline.length === 0 && <p className="pw-muted">No activity yet.</p>}
        <ul className="pw-list">
          {timeline?.map((t) => (
            <li key={t.id}><span>{t.label}<span className="pw-list__meta">{t.actor ?? "System"}</span></span><span className="pw-list__meta">{formatRelativeTime(t.created_at)}</span></li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
