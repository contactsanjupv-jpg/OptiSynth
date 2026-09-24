"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { getBacktest } from "@/lib/api";
import { formatPercent } from "@/lib/utils/format";
import type { BacktestResult } from "@/types";

/**
 * "Insights" surfaces the same backtest evidence as Overview/Model, framed
 * as plain-language takeaways rather than charts/tables -- there is no
 * separate insights-generation endpoint on the backend; this page derives
 * its text directly and honestly from the real backtest numbers, it does
 * not fabricate additional analysis the backend hasn't computed.
 */
export default function InsightsPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    getBacktest(projectId).then(setBacktest).catch(() => setError("Not enough data yet for insights."));
  }, [projectId]);

  if (error) return <ErrorCallout message={error} />;
  if (!backtest) return <Spinner label="Loading insights…" />;
  if (backtest.error) return <Card padding="lg"><p>{backtest.error}</p></Card>;

  const points: string[] = [];
  if (backtest.reduction_pct !== null) {
    points.push(
      `Based on a retrospective replay of your ${backtest.n_historical_rows} historical experiments, ` +
      `an optimization-guided order would have needed ${backtest.engine_order_evals} experiments to reach ` +
      `your target, versus ${backtest.actual_order_evals} in the order they were actually run \u2014 a ` +
      `${formatPercent(backtest.reduction_pct)} reduction.`
    );
  } else {
    points.push("The target hasn't been reached yet in either ordering with the data uploaded so far.");
  }
  points.push(
    `This is a backtest on your own data, not a forward-looking guarantee \u2014 use the Candidates tab ` +
    `to get live recommendations for what to run next.`
  );

  return (
    <Card padding="lg">
      <CardHeader><CardTitle>Insights</CardTitle></CardHeader>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {points.map((p, i) => (
          <p key={i} style={{ fontSize: "var(--text-sm)", color: "var(--color-text-primary)", lineHeight: 1.6 }}>{p}</p>
        ))}
      </div>
    </Card>
  );
}
