import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatRelativeTime } from "@/lib/utils/format";
import { describeActivity } from "@/lib/utils/activity";
import type { AuditLogEntry } from "@/types";
import "@/styles/components/activity-feed.css";

interface ActivityFeedProps {
  entries: AuditLogEntry[];
  projectNamesById: Record<number, string>;
}

export function ActivityFeed({ entries, projectNamesById }: ActivityFeedProps) {
  const recent = entries.slice(0, 6);
  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
      </CardHeader>
      {recent.length === 0 ? (
        <p className="activity-feed__empty">No activity yet.</p>
      ) : (
        <ul className="activity-feed__list">
          {recent.map((entry) => (
            <li key={entry.id} className="activity-feed__item">
              <span className="activity-feed__dot" />
              <div>
                <p className="activity-feed__text">
                  {describeActivity(entry, entry.project_id ? projectNamesById[entry.project_id] : undefined)}
                </p>
                <span className="activity-feed__time">{formatRelativeTime(entry.created_at)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
