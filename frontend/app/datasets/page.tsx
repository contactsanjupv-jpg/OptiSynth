"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Database } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Feedback";
import { DataTable, type DataTableColumn } from "@/components/tables/DataTable";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { listProjects } from "@/lib/api";
import type { Project } from "@/types";

/**
 * There is no standalone "datasets" resource on the backend -- a dataset
 * is always scoped to a project (see backend/app/models/schema.sql). This
 * page lists projects with their experiment counts as a datasets overview
 * and links into each project's own dataset upload (Settings tab), rather
 * than inventing a cross-project datasets list the API doesn't support.
 */
export default function DatasetsPage() {
  const { checking } = useRequireAuth();
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    if (checking) return;
    listProjects().then(setProjects).catch(() => setProjects([]));
  }, [checking]);

  if (checking) return null;

  const columns: DataTableColumn<Project>[] = [
    { key: "name", header: "Project", render: (p) => <Link href={`/projects/${p.id}/settings`}>{p.name}</Link> },
    { key: "count", header: "Experiments", align: "right", render: (p) => String(p.experiment_count ?? 0) },
    { key: "metric", header: "Target Metric", render: (p) => p.target_metric },
  ];

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Datasets" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Datasets" subtitle="Historical experiment data uploaded per project." />
        <Card padding="lg">
          {!projects && <Spinner label="Loading…" />}
          {projects && projects.length === 0 && (
            <EmptyState icon={<Database size={28} />} title="No datasets yet"
              description="Create a project and upload a CSV of historical experiments to see it here." />
          )}
          {projects && projects.length > 0 && (
            <DataTable columns={columns} rows={projects} getRowKey={(p) => p.id} />
          )}
        </Card>
      </div>
    </AppShell>
  );
}
