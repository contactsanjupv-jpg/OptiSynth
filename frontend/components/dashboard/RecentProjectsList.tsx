import Link from "next/link";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/Badge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { formatNumber, formatRelativeTime } from "@/lib/utils/format";
import type { DashboardProjectSummary } from "@/types";
import "@/styles/components/recent-projects.css";

export function RecentProjectsList({ projects }: { projects: DashboardProjectSummary[] }) {
  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Recent Projects</CardTitle>
      </CardHeader>

      {projects.length === 0 ? (
        <p className="recent-projects__empty">No projects yet — create one to get started.</p>
      ) : (
        <div className="recent-projects__list">
          {projects.slice(0, 6).map((p) => {
            const progressPct =
              p.target_value && p.best_value !== null
                ? Math.min(100, Math.round((p.best_value / p.target_value) * 100))
                : 0;
            return (
              <Link key={p.id} href={`/projects/${p.id}`} className="recent-projects__row">
                <div className="recent-projects__main">
                  <span className="recent-projects__name">{p.name}</span>
                  <span className="recent-projects__meta">
                    {p.target_value !== null
                      ? `Target: ${p.target_metric} ${p.direction === "maximize" ? "\u2265" : "\u2264"} ${formatNumber(p.target_value)}`
                      : "No target set"}
                  </span>
                </div>
                <StatusBadge status={p.status} />
                <div className="recent-projects__progress">
                  <ProgressBar value={progressPct} />
                  <span className="recent-projects__progress-label">{progressPct}%</span>
                </div>
                <span className="recent-projects__best">
                  {p.best_value !== null ? formatNumber(p.best_value) : "—"}
                </span>
                <span className="recent-projects__count">{p.experiment_count} exp.</span>
                <span className="recent-projects__time">{formatRelativeTime(p.updated_at)}</span>
              </Link>
            );
          })}
        </div>
      )}
    </Card>
  );
}
