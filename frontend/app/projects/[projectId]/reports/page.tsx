"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { FileText, Download } from "lucide-react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { EmptyState } from "@/components/ui/EmptyState";
import { listProjectReports, generateReport, downloadReport, triggerBrowserDownload } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils/format";
import type { ReportRecord } from "@/types";

export default function ProjectReportsPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [reports, setReports] = useState<ReportRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

    const load = useCallback(() => {
    listProjectReports(projectId).then(setReports).catch(() => setReports([]));
  }, [projectId]);

  useEffect(() => { if (projectId) load(); }, [projectId, load]);

  async function handleGenerate() {
    setError(null);
    setGenerating(true);
    try {
      await generateReport(projectId);
      load();
    } catch {
      setError("Could not generate a report. Make sure you've uploaded historical data first.");
    } finally {
      setGenerating(false);
    }
  }

  async function handleDownload(report: ReportRecord) {
    const blob = await downloadReport(report.id);
    triggerBrowserDownload(blob, `report_project_${report.project_id}_${report.id}.docx`);
  }

  return (
    <Card padding="lg">
      <CardHeader>
        <CardTitle>Reports</CardTitle>
        <Button variant="primary" loading={generating} onClick={handleGenerate}>Generate Report</Button>
      </CardHeader>

      {error && <ErrorCallout message={error} />}
      {!reports && !error && <Spinner label="Loading reports…" />}
      {reports && reports.length === 0 && (
        <EmptyState icon={<FileText size={28} />} title="No reports yet"
          description="Generate a report to get a shareable summary of your optimization results and ROI evidence." />
      )}
      {reports && reports.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {reports.map((r) => (
            <div key={r.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 4px", borderBottom: "1px solid var(--color-border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <FileText size={16} color="var(--color-text-tertiary)" />
                <div>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>Optimization Summary Report</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    Generated {formatRelativeTime(r.created_at)} · {r.n_historical_rows} experiments
                  </div>
                </div>
              </div>
              <Button size="sm" variant="secondary" icon={<Download size={14} />} onClick={() => handleDownload(r)}>
                Download
              </Button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
