import { StatCard } from "@/components/ui/StatCard";
import type { DashboardSummary } from "@/types";
import "@/styles/components/stats-grid.css";

export function StatsGrid({ summary }: { summary: DashboardSummary }) {
  const runningLabel =
    summary.active_projects === 1 ? "1 running" : `${summary.active_projects} running`;

  return (
    <div className="stats-grid">
      <StatCard label="Active Projects" value={String(summary.total_projects)} caption={runningLabel} />
      <StatCard label="Experiments" value={summary.total_experiments.toLocaleString()} />
      <StatCard
        label="Targets Achieved"
        value={String(summary.targets_achieved)}
        caption={`of ${summary.total_projects} projects`}
      />
    </div>
  );
}
