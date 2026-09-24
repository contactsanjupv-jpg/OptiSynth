"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { StatsGrid } from "@/components/dashboard/StatsGrid";
import { RecentProjectsList } from "@/components/dashboard/RecentProjectsList";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getDashboardSummary, getAuditLog } from "@/lib/api";
import type { DashboardSummary, AuditLogEntry } from "@/types";
import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";

export default function DashboardPage() {
  const { checking, cached } = useRequireAuth();
  const router = useRouter();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (checking) return;
    Promise.all([getDashboardSummary(), getAuditLog()])
      .then(([s, a]) => {
        setSummary(s);
        setAuditLog(a);
      })
      .catch(() => setError("Could not load your dashboard right now."));
  }, [checking]);

  if (checking) return null;

  const projectNamesById = Object.fromEntries((summary?.projects ?? []).map((p) => [p.id, p.name]));
  const greetingName = cached?.displayName || cached?.userEmail?.split("@")[0] || "there";

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Dashboard" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title={`Good morning, ${greetingName}`}
          subtitle="Here's what's happening with your projects today."
          actions={
            <Button variant="primary" icon={<Plus size={16} />} onClick={() => router.push("/projects/new")}>
              New Project
            </Button>
          }
        />

        {error && <ErrorCallout message={error} />}
        {!summary && !error && <Spinner label="Loading dashboard…" />}

        {summary && (
          <>
            <StatsGrid summary={summary} />
            <RecentProjectsList projects={summary.projects} />
            <div style={{ marginTop: 24 }}>
              <ActivityFeed entries={auditLog} projectNamesById={projectNamesById} />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
