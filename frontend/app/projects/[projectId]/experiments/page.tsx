"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { DataTable, type DataTableColumn } from "@/components/tables/DataTable";
import { listExperiments } from "@/lib/api";
import { formatNumber, formatDate } from "@/lib/utils/format";
import type { Experiment } from "@/types";

export default function ExperimentsPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [experiments, setExperiments] = useState<Experiment[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    listExperiments(projectId).then(setExperiments).catch(() => setError("Could not load experiments."));
  }, [projectId]);

  if (error) return <ErrorCallout message={error} />;
  if (!experiments) return <Spinner label="Loading experiments…" />;

  const featureKeys = experiments[0] ? Object.keys(experiments[0].features) : [];

  const columns: DataTableColumn<Experiment>[] = [
    { key: "id", header: "ID", render: (e) => `#${e.id}` },
    { key: "source", header: "Source", render: (e) => <Badge tone={e.source === "recommended" ? "accent" : "neutral"}>{e.source}</Badge> },
    ...featureKeys.map((k) => ({
      key: k, header: k, align: "right" as const, render: (e: Experiment) => formatNumber(e.features[k], 2),
    })),
    { key: "target", header: "Target Value", align: "right", render: (e) => formatNumber(e.target_value, 2) },
    { key: "date", header: "Date", render: (e) => formatDate(e.created_at) },
  ];

  return (
    <Card padding="lg">
      <CardHeader><CardTitle>Experiments ({experiments.length})</CardTitle></CardHeader>
      <DataTable columns={columns} rows={experiments} getRowKey={(e) => e.id}
        emptyMessage="No experiments yet. Upload a historical CSV from Project Settings to get started." />
    </Card>
  );
}
