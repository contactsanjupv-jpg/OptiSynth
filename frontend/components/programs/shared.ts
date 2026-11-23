import { ApiRequestError } from "@/lib/api/http";

export function messageOf(err: unknown): string {
  return err instanceof ApiRequestError ? err.message : "Something went wrong. Try again.";
}

export const CRITICALITY_LABEL: Record<string, string> = { critical: "Critical", important: "Important", informational: "Informational" };
export const SUPPORT_LABEL: Record<string, string> = { met: "Met by a recorded result", not_met: "Not met", no_result: "No result recorded" };
export const DIRECTION_LABEL: Record<string, string> = { maximize: "At least", minimize: "At most" };

export function formatDate(iso: string | null): string {
  if (!iso) return "Not set";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}
