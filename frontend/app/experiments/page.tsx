"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FlaskConical } from "lucide-react";
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

/** Same pattern as /datasets -- experiments are project-scoped on the
 * backend; this is a cross-project index linking into each project's own
 * Experiments tab, not a fabricated flat experiment list. */
export default function GlobalExperimentsPage() {
  const { checking } = useRequireAuth();
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    if (checking) return;
    listProjects().then(setProjects).catch(() => setProjects([]));
  }, [checking]);

  if (checking) return null;

  const columns: DataTableColumn<Project>[] = [
    { key: "name", header: "Project", render: (p) => <Link href={`/projects/${p.id}/experiments`}>{p.name}</Link> },
    { key: "count", header: "Experiments", align: "right", render: (p) => String(p.experiment_count ?? 0) },
    { key: "status", header: "Status", render: (p) => p.status },
  ];

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Experiments" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Experiments" subtitle="Every experiment logged across your projects." />
        <Card padding="lg">
          {!projects && <Spinner label="Loading…" />}
          {projects && projects.length === 0 && (
            <EmptyState icon={<FlaskConical size={28} />} title="No experiments yet" />
          )}
          {projects && projects.length > 0 && <DataTable columns={columns} rows={projects} getRowKey={(p) => p.id} />}
        </Card>
      </div>
    </AppShell>
  );
}
