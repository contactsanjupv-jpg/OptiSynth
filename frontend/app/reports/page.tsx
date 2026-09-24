"use client";

import { useEffect, useState } from "react";
import { FileBarChart, Download } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getDashboardSummary, listProjectReports, downloadReport, triggerBrowserDownload } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils/format";
import type { ReportRecord } from "@/types";

export default function GlobalReportsPage() {
  const { checking } = useRequireAuth();
  const [reports, setReports] = useState<(ReportRecord & { projectName: string })[] | null>(null);

  useEffect(() => {
    if (checking) return;
    getDashboardSummary().then(async (summary) => {
      const perProject = await Promise.all(
        summary.projects.map(async (p) => {
          const rs = await listProjectReports(p.id).catch(() => []);
          return rs.map((r) => ({ ...r, projectName: p.name }));
        })
      );
      setReports(perProject.flat().sort((a, b) => b.id - a.id));
    }).catch(() => setReports([]));
  }, [checking]);

  if (checking) return null;

  async function handleDownload(report: ReportRecord) {
    const blob = await downloadReport(report.id);
    triggerBrowserDownload(blob, `report_project_${report.project_id}_${report.id}.docx`);
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Reports" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Reports" subtitle="Every generated report across your projects." />
        <Card padding="lg">
          {!reports && <Spinner label="Loading…" />}
          {reports && reports.length === 0 && (
            <EmptyState icon={<FileBarChart size={28} />} title="No reports yet"
              description="Generate a report from any project's Reports tab." />
          )}
          {reports && reports.map((r) => (
            <div key={r.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 4px", borderBottom: "1px solid var(--color-border)" }}>
              <div>
                <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{r.projectName}</div>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                  Generated {formatRelativeTime(r.created_at)}
                </div>
              </div>
              <Button size="sm" variant="secondary" icon={<Download size={14} />} onClick={() => handleDownload(r)}>
                Download
              </Button>
            </div>
          ))}
        </Card>
      </div>
    </AppShell>
  );
}
