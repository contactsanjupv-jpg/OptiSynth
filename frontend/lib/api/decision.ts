import { apiRequest } from "@/lib/api/http";

export type ValueType = "number" | "text" | "boolean" | "text_list";
export type ConstraintState = "satisfied" | "violated" | "unknown";
export type Group = "feasible" | "unresolved" | "rejected";

export interface Attribute { id: number; key: string; label: string; value_type: ValueType; unit: string | null; category: string; description: string | null; status: "active" | "archived"; values_recorded: number }
export interface Constraint {
  id: number; name: string; kind: "hard" | "soft"; category: string; ref: string; ref_label: string; ref_available: boolean; operator: string;
  operator_label: string; value: unknown; unit: string | null; rationale: string; status: "active" | "archived";
}
export interface Objective { id: number; name: string; ref: string; ref_label: string; ref_available: boolean; direction: "maximize" | "minimize"; unit: string | null; status: "active" | "archived" }
export interface Reference { ref: string; label: string; value_type: ValueType; unit: string | null; origin: string; group: string }
export interface CandidateValues { candidate_id: number; name: string; values: Record<string, { value: unknown; source_note: string; entered_at: string } | null> }
export interface AnalysisBrief { id: number; input_hash: string; engine_version: string; created_at: string; summary: { counts: Record<Group, number>; candidates: number; front: number[] } }
export interface Workspace {
  attributes: Attribute[]; constraints: Constraint[]; objectives: Objective[]; references: Reference[];
  operators: Record<ValueType, { value: string; label: string }[]>;
  constraint_categories: { value: string; label: string }[]; attribute_categories: { value: string; label: string }[];
  candidate_values: CandidateValues[]; latest_analysis_id: number | null; analysis_outdated: boolean | null; analyses: AnalysisBrief[];
  permissions: { can_manage: boolean; can_run: boolean; can_enter_values: boolean }; no_invented_data: string;
}

export interface HardResult {
  constraint_id: number; name: string; kind: "hard" | "soft"; state: ConstraintState; detail: string; margin: number | null; observed: unknown;
  interval: [number, number] | null; point_estimate_met: boolean | null; unit: string | null; operator: string; limit: unknown;
}
export interface CandidateResult {
  candidate_id: number; name: string; group: Group; hard: HardResult[]; soft: HardResult[]; violated_hard: number[]; unknown_hard: number[];
  soft_summary: { met: number; missed: number; unknown: number; total: number };
  objective_values: Record<string, { value: number; sd: number | null; interval: [number, number] | null } | null>;
  pareto: { status: string; layer: number | null; dominated_by: number[]; confidently_dominated_by: number[]; missing: string[] };
  provisional: { layer: number | null };
  missing_information: { ref: string; needed_for: string; kind: string; reason: string }[];
  predicted: { available: boolean; value?: number; sd?: number | null; interval?: [number, number] | null; unit?: string | null; reason?: string };
  context: { domain_label?: string | null; input_space_label?: string | null; decision_support_label?: string | null; model_version_number?: number | null; validation_state?: string | null };
  physical_validation: { status: string; text: string };
  qualification_status: "not_qualified";
  explanation: { headline: string; promising: string[]; rejected: string[]; dominating_constraints: { constraint_id: number; name: string; effect: string }[]; supported_by: string[]; could_change: string[]; qualification: string };
}
export interface Analysis {
  id: number; created_at: string; engine_version: string; input_hash: string; program: { id: number; name: string }; target: { name: string; unit: string | null; direction: string };
  constraints: { id: number; name: string; kind: string }[]; objectives: { id: number; name: string; ref: string; direction: string }[];
  summary: {
    counts: Record<Group, number>; candidates: number; hard_constraints: number; soft_constraints: number; front: number[]; provisional_front: number[];
    objectives: { ref: string; name: string; direction: string }[]; method: string; interval_note: string; qualification: string;
    constraint_impact: { constraint_id: number; name: string; violated_by: number; unknown_for: number; sole_blocker_for: number[]; would_change_if_removed: Record<string, string> }[];
  };
  order: number[]; candidates: Record<string, CandidateResult>; qualification: string;
}

const base = (programId: number) => `/api/change-cases/${programId}/decision`;

export const getDecisionWorkspace = (programId: number) => apiRequest<Workspace>(base(programId));
export const createAttribute = (programId: number, json: Record<string, unknown>) => apiRequest<Attribute>(`${base(programId)}/attributes`, { method: "POST", json });
export const updateAttribute = (programId: number, id: number, json: Record<string, unknown>) => apiRequest<Attribute>(`${base(programId)}/attributes/${id}`, { method: "PATCH", json });
export const setCandidateValue = (programId: number, candidateId: number, attributeId: number, json: { value: unknown; source_note: string }) =>
  apiRequest<unknown>(`${base(programId)}/candidates/${candidateId}/attributes/${attributeId}`, { method: "PUT", json });
export const clearCandidateValue = (programId: number, candidateId: number, attributeId: number) =>
  apiRequest<unknown>(`${base(programId)}/candidates/${candidateId}/attributes/${attributeId}`, { method: "DELETE" });
export const createConstraint = (programId: number, json: Record<string, unknown>) => apiRequest<Constraint>(`${base(programId)}/constraints`, { method: "POST", json });
export const updateConstraint = (programId: number, id: number, json: Record<string, unknown>) => apiRequest<Constraint>(`${base(programId)}/constraints/${id}`, { method: "PATCH", json });
export const createObjective = (programId: number, json: Record<string, unknown>) => apiRequest<Objective>(`${base(programId)}/objectives`, { method: "POST", json });
export const updateObjective = (programId: number, id: number, json: Record<string, unknown>) => apiRequest<Objective>(`${base(programId)}/objectives/${id}`, { method: "PATCH", json });
export const runDecisionAnalysis = (programId: number) => apiRequest<{ analysis: Analysis }>(`${base(programId)}/analyses`, { method: "POST" });
export const getDecisionAnalysis = (programId: number, id: number) => apiRequest<Analysis>(`${base(programId)}/analyses/${id}`);
