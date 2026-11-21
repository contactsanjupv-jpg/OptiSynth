import { apiRequest } from "@/lib/api/http";

export type Severity = "high" | "medium" | "low";

export type StageKey =
  | "intake" | "requirements" | "evidence_collection" | "evidence_review" | "candidate_assessment"
  | "experiment_planning" | "physical_validation" | "engineering_review" | "approved" | "rejected" | "closed";

export const STAGE_LABELS: Record<StageKey, string> = {
  intake: "Intake",
  requirements: "Requirements",
  evidence_collection: "Evidence collection",
  evidence_review: "Evidence review",
  candidate_assessment: "Candidate assessment",
  experiment_planning: "Experiment planning",
  physical_validation: "Physical validation",
  engineering_review: "Engineering review",
  approved: "Approved",
  rejected: "Rejected",
  closed: "Closed",
};

export interface ProgramIssue { type: string; severity: Severity; label: string; detail: string | null }

export interface ProgramRow {
  id: number;
  name: string;
  status: string;
  stage: StageKey;
  stage_label: string;
  severity: Severity | null;
  issues: ProgramIssue[];
  created_by: string | null;
  updated_at: string;
}

export interface OverviewAction {
  type: string; severity: Severity; label: string; detail: string | null;
  program_id: number; program_name: string; updated_at: string;
}

export interface Overview {
  range_days: number;
  total_programs: number;
  kpis: { active_programs: number; evidence_review: number; tests_pending: number; needs_attention: number };
  pipeline: { stage: StageKey; label: string; count: number }[];
  status_summary: { no_open_issues: number; open_issues: number; high_priority: number };
  attention: ProgramRow[];
  actions: OverviewAction[];
  issue_types: { type: string; label: string; count: number }[];
  decisions: { id: number; program_id: number; program_name: string; outcome: string; reason: string | null; actor: string | null; created_at: string }[];
  activity: { id: number; label: string; program_id: number | null; program_name: string | null; actor: string | null; created_at: string }[];
}

export function getOverview(days: number) {
  return apiRequest<Overview>(`/api/portfolio/overview?days=${days}`);
}

export interface ProgramFilter { filter?: string; stage?: string; issue?: string; q?: string }

export function listPortfolioPrograms(f: ProgramFilter) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) params.set(k, v);
  const qs = params.toString();
  return apiRequest<{ programs: ProgramRow[]; total: number }>(`/api/portfolio/programs${qs ? `?${qs}` : ""}`);
}

export interface StageOption { stage: StageKey; label: string; blocked_reason: string | null; needs_reason: boolean }
export interface StageState { stage: StageKey; label: string; options: StageOption[] }
export interface StageHistoryEntry {
  id: number; from_stage: StageKey | null; to_stage: StageKey; from_label: string | null; to_label: string;
  reason: string | null; created_at: string; actor: string | null;
}

export function getStage(changeCaseId: number) {
  return apiRequest<StageState>(`/api/change-cases/${changeCaseId}/stage`);
}

export function getStageHistory(changeCaseId: number) {
  return apiRequest<StageHistoryEntry[]>(`/api/change-cases/${changeCaseId}/stage-history`);
}

export function changeStage(changeCaseId: number, toStage: StageKey, reason: string) {
  return apiRequest<{ stage: StageKey; label: string }>(`/api/change-cases/${changeCaseId}/stage`, {
    method: "POST",
    json: { to_stage: toStage, reason: reason.trim() || null },
  });
}
