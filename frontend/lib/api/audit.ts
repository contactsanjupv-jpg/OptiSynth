import { apiRequest } from "@/lib/api/http";
import type { AuditLogEntry } from "@/types";

export function getAuditLog(projectId?: number) {
  const qs = projectId !== undefined ? `?project_id=${projectId}` : "";
  return apiRequest<AuditLogEntry[]>(`/api/audit-log${qs}`);
}
