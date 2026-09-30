import { apiRequest } from "@/lib/api/http";

export type TriggerType = "regulatory_restriction" | "supplier_loss" | "component_obsolescence" | "other";
export type ChangeCaseStatus = "draft" | "active" | "completed";

export interface QualificationSpec {
  feature_columns: string[];
  target_metric: string;
  target_value: number;
  direction: "maximize" | "minimize";
}

export interface ChangeCase {
  id: number;
  name: string;
  trigger_type: TriggerType;
  restricted_substance: string | null;
  status: ChangeCaseStatus;
  created_at: string;
  updated_at: string;
}

export function createChangeCase(
  name: string,
  trigger_type: TriggerType,
  restricted_substance: string | null,
  qualification_spec: QualificationSpec,
) {
  return apiRequest<ChangeCase>("/api/change-cases", {
    method: "POST",
    json: { name, trigger_type, restricted_substance, qualification_spec },
  });
}

export function listChangeCases() {
  return apiRequest<ChangeCase[]>("/api/change-cases");
}

export function getChangeCase(changeCaseId: number) {
  return apiRequest<ChangeCase>(`/api/change-cases/${changeCaseId}`);
}

export function updateChangeCaseStatus(changeCaseId: number, status: ChangeCaseStatus) {
  return apiRequest<ChangeCase>(`/api/change-cases/${changeCaseId}/status`, {
    method: "PATCH",
    json: { status },
  });
}

export interface QualificationDatasetUploadResult {
  dataset_id: number;
  rows_ingested: number;
  rows_skipped: number;
  errors: { row: number; error: string }[];
}

export function uploadQualificationDataset(changeCaseId: number, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest<QualificationDatasetUploadResult>(`/api/change-cases/${changeCaseId}/dataset`, {
    method: "POST",
    formData,
  });
}

export interface Prediction {
  predicted_value: number | null;
  uncertainty_std: number;
  predicted_probability: number;
  model_version: string;
  dataset_version_id: number;
}

export type DomainStatus = "within_historical_domain" | "near_edge_of_domain" | "outside_historical_domain";

/** Heuristic per-feature historical-range coverage (backend Priority 5). */
export interface DomainCoverage {
  status: DomainStatus;
  edge_margin_pct: number;
  features: Record<
    string,
    { status: DomainStatus; value: number; historical_min: number; historical_max: number }
  >;
  note: string;
}

export type DecisionSupportStatus = "evidence_supported" | "caution" | "requires_validation";

/**
 * Categorical decision support (backend C1), derived ONLY from domain
 * coverage. It is not a score and does not modify predicted_probability.
 * "evidence_supported" does NOT mean qualified -- physical validation is
 * still required for every candidate.
 */
export interface DecisionSupport {
  status: DecisionSupportStatus;
  basis: string;
  domain_status: DomainStatus | null;
  label: string;
  statement: string;
}

export interface Candidate {
  id: number;
  candidate_name: string;
  properties: Record<string, number>;
  latest_prediction: Prediction | null;
  /** null until the candidate has been ranked. */
  domain_coverage: DomainCoverage | null;
  /** null until the candidate has been ranked. */
  decision_support: DecisionSupport | null;
}

export function addCandidate(changeCaseId: number, name: string, features: Record<string, number>) {
  return apiRequest<{ id: number; candidate_name: string }>(`/api/change-cases/${changeCaseId}/candidates`, {
    method: "POST",
    json: { name, features },
  });
}

export function listCandidates(changeCaseId: number) {
  return apiRequest<Candidate[]>(`/api/change-cases/${changeCaseId}/candidates`);
}

export interface RankResult {
  candidate_id: number;
  candidate_name: string;
  predicted_probability: number;
  uncertainty_std: number;
  recommended_experiment: string;
  domain_coverage: DomainCoverage | null;
  decision_support: DecisionSupport | null;
}

export function rankChangeCase(changeCaseId: number) {
  return apiRequest<RankResult[]>(`/api/change-cases/${changeCaseId}/rank`, { method: "POST" });
}

export function recordOutcome(
  changeCaseId: number,
  candidateId: number,
  actual_result: string,
  passed_spec: boolean,
  recommended_experiment_id: number | null = null,
) {
  return apiRequest<{ id: number }>(`/api/change-cases/${changeCaseId}/candidates/${candidateId}/outcome`, {
    method: "POST",
    json: { actual_result, passed_spec, recommended_experiment_id },
  });
}

export function generateQualificationReport(changeCaseId: number) {
  return apiRequest<Blob>(`/api/change-cases/${changeCaseId}/report`, {
    method: "POST",
    expectBlob: true,
  });
}
