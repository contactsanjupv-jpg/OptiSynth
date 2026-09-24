import type { AuditLogEntry } from "@/types";

/**
 * The backend logs raw "<METHOD> <path>" strings (see app.audit() in
 * app.py) -- it has no notion of a human-readable activity feed. This is
 * the ONLY place that translates those into sentences, so the mapping
 * lives in one spot instead of being duplicated per page.
 */
export function describeActivity(entry: AuditLogEntry, projectName: string | undefined): string {
  const name = projectName ?? "a project";
  const action = entry.action;

  if (action.match(/POST \/projects\/\d+\/dataset$/)) return `Historical data uploaded to ${name}`;
  if (action.match(/POST \/projects\/\d+\/recommend$/)) return `New candidate set generated for ${name}`;
  if (action.match(/GET \/projects\/\d+\/backtest$/)) return `Backtest run for ${name}`;
  if (action.match(/GET \/projects\/\d+\/report$/)) return `Report generated for ${name}`;
  if (action.match(/PATCH \/projects\/\d+\/status$/)) return `Status updated for ${name}`;
  if (action.match(/POST \/projects$/)) return `Project "${name}" created`;
  return action;
}
