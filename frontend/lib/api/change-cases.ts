import { apiRequest } from "@/lib/api/http";

export type TriggerType = "regulatory_restriction" | "supplier_loss" | "component_obsolescence" | "other";
export type ChangeCaseStatus = "draft" | "active" | "completed";

export interface QualificationSpec {
  feature_columns: string[];
  target_metric: string;
  target_value: number;
  direction: "maximize" | "minimize";
  /** Canonical unit per modelled field. Fields left out are dimensionless / not tracked. */
  units?: Record<string, string>;
}

export interface ChangeCase {
  id: number;
  name: string;
  trigger_type: TriggerType;
  restricted_substance: string | null;
  status: ChangeCaseStatus;
  created_at: string;
  updated_at: string;
  /** Returned by GET /api/change-cases/{id} as stored JSON text. */
  qualification_spec_json?: string;
}

export function parseSpec(changeCase: ChangeCase | null): QualificationSpec | null {
  if (!changeCase?.qualification_spec_json) return null;
  try {
    return JSON.parse(changeCase.qualification_spec_json) as QualificationSpec;
  } catch {
    return null;
  }
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
  errors: { row: number; error: string; source?: string }[];
  data_quality_status?: string;
  duplicate_rows_found?: number;
  review?: IntakeReview;
}

// ---------------------------------------------------------------------------
// Evidence intake (extract -> review -> accept). Every call below is display-
// only until uploadQualificationDataset is called with a review that has no
// blocking finding.
// ---------------------------------------------------------------------------
export interface IntakeOptions {
  /** One table ref (e.g. "xlsx:Data"), or several refs from the SAME file whose header rows are identical. */
  table_ref?: string | string[];
  /** Source row number (Excel row / table row / CSV line) holding the column headers. */
  header_row?: number;
  /** canonical field -> header text in the file */
  column_mapping?: Record<string, string>;
  /** canonical field -> unit of the values in the file (for cells that carry no unit) */
  declared_units?: Record<string, string>;
  condition_columns?: string[];
  reference_conditions?: Record<string, string>;
  exclude_rows?: number[];
  exclusion_reason?: string;
}

export interface TablePreviewRow { src_row: number; cells: string[] }

export interface ExtractedTable {
  ref: string;
  kind: "csv" | "xlsx" | "docx" | "pdf";
  location: string;
  caption: string;
  confidence: "native" | "ruled" | "text_layout";
  n_rows: number;
  n_cols: number;
  default_header_row: number | null;
  preview: TablePreviewRow[];
  preview_truncated: boolean;
  warnings: string[];
  column_warnings: { col_index: number; code: string; message: string }[];
  matches?: Record<string, { best: string | null; confidence: string | null; n_candidates: number }>;
  matched_required?: number;
  total_required?: number;
}

export interface ExtractResult {
  file_name: string;
  file_kind: "csv" | "xlsx" | "docx" | "pdf";
  file_sha256: string;
  warnings: string[];
  required_columns: { name: string; role: "feature" | "target"; unit: string | null | undefined }[];
  tables: ExtractedTable[];
}

export interface ReviewFlag {
  severity: "blocking" | "warning";
  code: string;
  message: string;
  column?: string;
  rows?: number[];
  row_sources?: string[];
}

export interface IntakeReview {
  column_mapping: Record<string, { source_header: string; method: string }>;
  proposed_mapping: Record<string, string>;
  proposed_units: Record<string, string>;
  proposed_condition_columns: string[];
  ignored_columns: string[];
  provenance_columns: string[];
  condition_columns: string[];
  units: Record<string, { canonical: string | null; declared_source_unit: string | null; source_units_seen: string[]; rows_converted: number }>;
  conditions: { observed?: Record<string, string[]>; reference?: Record<string, string>; rows_excluded_other_conditions?: number };
  counts: Record<string, number>;
  flags: ReviewFlag[];
  source?: { file: string; kind: string; tables: { ref: string; location: string; header_row: number; data_rows: number }[] };
}

export interface PreviewResult {
  would_be_accepted: boolean;
  file_sha256: string;
  review: IntakeReview;
  errors: { row: number; error: string; source?: string }[];
  data_quality_status?: string;
}

function intakeForm(file: File, options?: IntakeOptions) {
  const formData = new FormData();
  formData.append("file", file);
  if (options) formData.append("options", JSON.stringify(options));
  return formData;
}

export function extractEvidence(changeCaseId: number, file: File) {
  return apiRequest<ExtractResult>(`/api/change-cases/${changeCaseId}/evidence/extract`, {
    method: "POST",
    formData: intakeForm(file),
  });
}

export function previewEvidence(changeCaseId: number, file: File, options: IntakeOptions) {
  return apiRequest<PreviewResult>(`/api/change-cases/${changeCaseId}/dataset/preview`, {
    method: "POST",
    formData: intakeForm(file, options),
  });
}

export function uploadQualificationDataset(changeCaseId: number, file: File, options?: IntakeOptions) {
  return apiRequest<QualificationDatasetUploadResult>(`/api/change-cases/${changeCaseId}/dataset`, {
    method: "POST",
    formData: intakeForm(file, options),
  });
}

export interface EvidenceInventoryItem {
  id: number;
  original_filename: string;
  uploaded_at: string;
  row_count: number;
  is_current: boolean;
  /** "current" = the newest upload (used by the next ranking); "superseded" = earlier evidence kept as history. */
  status: "current" | "superseded";
  /** true when the rankings currently shown were generated from this dataset. */
  used_by_displayed_rankings: boolean;
  has_review_record: boolean;
  file_sha256: string | null;
  source: { file: string; kind: string; tables: { ref: string; location: string; header_row: number; data_rows: number }[] } | null;
  counts: Record<string, number> | null;
  warning_count: number;
}

export function listEvidence(changeCaseId: number) {
  return apiRequest<EvidenceInventoryItem[]>(`/api/change-cases/${changeCaseId}/evidence`);
}

export interface SufficiencyCheck {
  code: string;
  status: "pass" | "fail" | "warn";
  title: string;
  detail: string;
}

export interface Sufficiency {
  can_rank: boolean;
  checks: SufficiencyCheck[];
  blocking: SufficiencyCheck[];
  warnings: SufficiencyCheck[];
  summary: { distinct_rows: number; features: number; candidates: number };
}

export function getSufficiency(changeCaseId: number) {
  return apiRequest<Sufficiency>(`/api/change-cases/${changeCaseId}/sufficiency`);
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
  /** What was entered (value + unit) and what it became in the case's canonical units. */
  input_record?: Record<string, { value: number; unit: string | null; converted_value?: number; converted_unit?: string }> | null;
  /**
   * Stale-ranking marker. true = this prediction was generated from an OLDER dataset than the
   * current evidence; false = generated from the current evidence; null = no prediction yet.
   */
  prediction_stale?: boolean | null;
  /** The dataset this candidate's prediction was generated from. */
  prediction_dataset?: { id: number; original_filename: string } | null;
  /** The current (newest) evidence. */
  current_dataset?: { id: number; original_filename: string } | null;
  /** Ready-to-show sentence when prediction_stale is true. */
  stale_notice?: string | null;
  latest_prediction: Prediction | null;
  /** null until the candidate has been ranked. */
  domain_coverage: DomainCoverage | null;
  /** null until the candidate has been ranked. */
  decision_support: DecisionSupport | null;
}

export function addCandidate(
  changeCaseId: number,
  name: string,
  features: Record<string, number>,
  units?: Record<string, string>,
) {
  return apiRequest<{ id: number; candidate_name: string }>(`/api/change-cases/${changeCaseId}/candidates`, {
    method: "POST",
    json: { name, features, ...(units && Object.keys(units).length ? { units } : {}) },
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
