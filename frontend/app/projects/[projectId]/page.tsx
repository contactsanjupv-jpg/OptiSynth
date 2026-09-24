"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatCard } from "@/components/ui/StatCard";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { LineChart } from "@/components/charts/LineChart";
import { getBacktest, getProject } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/utils/format";
import type { BacktestResult, Project } from "@/types";

export default function ProjectOverviewPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([getProject(projectId), getBacktest(projectId)])
      .then(([p, bt]) => {
        setProject(p);
        setBacktest(bt);
      })
      .catch(() => setError("Not enough data yet to compute optimization progress. Upload experiments to get started."));
  }, [projectId]);

  if (error) return <ErrorCallout message={error} />;
  if (!project || !backtest) return <Spinner label="Loading overview…" />;

  if (backtest.error) {
    return <Card padding="lg"><p>{backtest.error}</p></Card>;
  }

  const engineCurve = (backtest.engine_order_curve ?? []).map((y, i) => ({ x: i + 1, y }));
  const actualCurve = (backtest.actual_order_curve ?? []).map((y, i) => ({ x: i + 1, y }));

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
        <StatCard label="Target" value={project.target_value !== null ? formatNumber(project.target_value) : "—"} />
        <StatCard label="Historical Experiments" value={String(backtest.n_historical_rows)} />
        <StatCard
          label="Experiments to Target (engine)"
          value={backtest.engine_order_evals !== null ? String(backtest.engine_order_evals) : "not reached"}
        />
        <StatCard label="Reduction vs. actual order" value={formatPercent(backtest.reduction_pct ?? undefined)} />
      </div>

      <Card padding="lg">
        <CardHeader><CardTitle>Optimization Progress</CardTitle></CardHeader>
        <LineChart
          series={[
            { name: "Best Found (engine order)", color: "var(--color-chart-primary)", points: engineCurve },
            { name: "Baseline (actual order)", color: "var(--color-chart-baseline)", points: actualCurve, dashed: true },
          ]}
          targetLine={project.target_value !== null ? { value: project.target_value, label: "Target" } : undefined}
          xLabel="Number of Experiments"
          yLabel={project.target_metric}
        />
      </Card>
    </div>
  );
}
