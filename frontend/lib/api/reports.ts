import { apiRequest } from "@/lib/api/http";
import type { DashboardSummary, ReportRecord } from "@/types";

export function listProjectReports(projectId: number) {
  return apiRequest<ReportRecord[]>(`/api/projects/${projectId}/reports`);
}

/** Triggers report generation server-side (recomputes backtest +
 * recommendations from current data) and persists a new report record. */
export function generateReport(projectId: number) {
  return apiRequest<{ report_id: number }>(`/api/projects/${projectId}/report`, { method: "POST" });
}

export function downloadReport(reportId: number) {
  return apiRequest<Blob>(`/api/reports/${reportId}/download`, { expectBlob: true });
}

export function getDashboardSummary() {
  return apiRequest<DashboardSummary>("/api/dashboard/summary");
}

export function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
