"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatCard } from "@/components/ui/StatCard";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { LineChart } from "@/components/charts/LineChart";
import { getModelMetrics, getBacktest, getProject } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/utils/format";
import type { ModelMetrics, BacktestResult, Project } from "@/types";

export default function ModelPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [metrics, setMetrics] = useState<ModelMetrics | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([getModelMetrics(projectId), getBacktest(projectId), getProject(projectId)])
      .then(([m, b, p]) => { setMetrics(m); setBacktest(b); setProject(p); })
      .catch(() => setError("Not enough data yet to evaluate model performance."));
  }, [projectId]);

  if (error) return <ErrorCallout message={error} />;
  if (!metrics || !backtest || !project) return <Spinner label="Loading model performance…" />;

  if (metrics.error) {
    return <Card padding="lg"><p>{metrics.error}</p></Card>;
  }

  const engineCurve = (backtest.engine_order_curve ?? []).map((y, i) => ({ x: i + 1, y }));
  const actualCurve = (backtest.actual_order_curve ?? []).map((y, i) => ({ x: i + 1, y }));

  return (
    <div>
      <Card padding="lg" style={{ marginBottom: 24 }}>
        <CardHeader><CardTitle>Optimization Progress</CardTitle></CardHeader>
        <LineChart
          series={[
            { name: "Best Found", color: "var(--color-chart-primary)", points: engineCurve },
            { name: "Baseline", color: "var(--color-chart-baseline)", points: actualCurve, dashed: true },
          ]}
          targetLine={project.target_value !== null ? { value: project.target_value, label: "Target" } : undefined}
          xLabel="Number of Experiments"
          yLabel={project.target_metric}
        />
      </Card>

      <Card padding="lg">
        <CardHeader><CardTitle>Model Metrics (cross-validated, held-out data)</CardTitle></CardHeader>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
          <StatCard label="R\u00b2 Score" value={formatNumber(metrics.r2_score, 2)} />
          <StatCard label="Mean Absolute Error" value={formatNumber(metrics.mean_absolute_error, 2)} />
          <StatCard label="Prediction Accuracy" value={formatPercent(metrics.prediction_accuracy_pct)} caption={`within \u00b1${formatNumber(metrics.accuracy_tolerance_band, 2)}`} />
          <StatCard label="Folds Used" value={String(metrics.n_folds_used ?? "—")} />
        </div>
      </Card>
    </div>
  );
}
