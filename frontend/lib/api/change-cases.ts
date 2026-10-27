import { apiRequest } from "@/lib/api/http";

export type TriggerType = "regulatory_restriction" | "supplier_loss" | "component_obsolescence" | "other";
/**
 * What each status means (the only meanings the product gives them -- see
 * backend change_case_lifecycle_rules.py):
 *   draft      setup in progress; nobody has started the diagnostic.
 *   active     in progress; a team member explicitly started it. Workflow progress, NOT a
 *              qualification result.
 *   completed  closed record; cannot be reopened.
 * "Archived" is not a status: it is a separate marker (`archived_at`) -- hidden from the default list
 * and read-only, with everything retained.
 */
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
  /** Returned by the list and by GET /api/change-cases/{id} as stored JSON text. */
  qualification_spec_json?: string;
  /** Only on the response to creating a diagnostic: what each custom (unrecognised but explicit) unit label means --
   *  exact-spelling match only, not dimensionally validated, never converted. */
  unit_notes?: string[];
  /** Set when archived (hidden from the default list, read-only). */
  archived_at?: string | null;
  archived?: boolean;
  /** One-sentence meaning of the current state, from the server. */
  status_meaning?: string;
  /** What depends on the diagnostic. */
  dataset_count?: number;
  candidate_count?: number;
  prediction_count?: number;
  outcome_count?: number;
  /** Permanent deletion is only offered while nothing depends on the diagnostic. */
  deletable?: boolean;
  delete_blocked_reason?: string | null;
  /** The requirement (threshold / direction) is locked once candidates have been ranked. */
  requirement_editable?: boolean;
  requirement_locked_reason?: string | null;
}

/** Fields that can be edited. Send only what changed; restricted_substance null/"" clears it. */
export interface ChangeCaseUpdate {
  name?: string;
  trigger_type?: TriggerType;
  restricted_substance?: string | null;
  target_value?: number;
  direction?: "maximize" | "minimize";
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

export function listChangeCases(includeArchived = false) {
  return apiRequest<ChangeCase[]>(`/api/change-cases${includeArchived ? "?include_archived=true" : ""}`);
}

export function updateChangeCase(changeCaseId: number, changes: ChangeCaseUpdate) {
  return apiRequest<ChangeCase>(`/api/change-cases/${changeCaseId}`, { method: "PATCH", json: changes });
}

export function archiveChangeCase(changeCaseId: number) {
  return apiRequest<ChangeCase>(`/api/change-cases/${changeCaseId}/archive`, { method: "POST" });
}

export function restoreChangeCase(changeCaseId: number) {
  return apiRequest<ChangeCase>(`/api/change-cases/${changeCaseId}/restore`, { method: "POST" });
}

export interface DeleteChangeCaseResult {
  deleted: boolean;
  id: number;
  name: string;
  datasets_removed: number;
  candidates_removed: number;
  files_removed: number;
  files_not_removed: number;
}

/** Permanent. Owners and admins only, and only while nothing depends on the diagnostic. */
export function deleteChangeCase(changeCaseId: number) {
  return apiRequest<DeleteChangeCaseResult>(`/api/change-cases/${changeCaseId}`, { method: "DELETE" });
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
  /**
   * The model's predicted target-property value in the case's canonical target unit. A MODEL
   * ESTIMATE, not a measured result. null = not recorded: the prediction was stored before values
   * were saved and is never back-filled (re-run the ranking to obtain one).
   */
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

export type DecisionSupportStatus = "within_data_range" | "caution" | "requires_validation";

/**
 * Categorical decision support (backend C1), derived ONLY from domain
 * coverage. It is not a score and does not modify predicted_probability.
 * "within_data_range" is a per-feature range check only: it does NOT mean qualified -- physical validation is
 * still required for every candidate.
 */
export interface DecisionSupport {
  status: DecisionSupportStatus;
  basis: string;
  /** What the signal checks: "per_feature_range_check" -- never a joint-coverage, accuracy or physical-evidence check. */
  scope?: string;
  /** What the signal does NOT establish (joint coverage, accuracy for this candidate, performance, compliance, qualification). */
  not_established?: string[];
  domain_status: DomainStatus | null;
  label: string;
  statement: string;
}

export type RankingStatus = "ranked" | "ranked_with_caution" | "not_ranked_insufficient_evidence";

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
  /** 1 = best, by the same deterministic order the ranking run used. null until the candidate has a prediction,
   *  and withheld (null) whenever the ranking no longer covers the candidates as they are now. */
  rank?: number | null;
  /**
   * How well the historical data supports ranking this candidate (set whenever a prediction for its current
   * input values exists). "not_ranked_insufficient_evidence" = an input lies outside the observed range (or its
   * coverage is unknown): the candidate gets NO position -- neither first nor last -- and its model estimate is an
   * extrapolation. That is the absence of evidence, not a verdict on the candidate.
   */
  ranking_status?: RankingStatus | null;
  /** Ready-to-show explanation of ranking_status. */
  ranking_statement?: string | null;
  /** Bumped every time the candidate's input values change. */
  inputs_version?: number;
  /**
   * true = the candidate's input values were edited AFTER latest_prediction was computed: the prediction is
   * kept as history but describes the PREVIOUS values (rank, domain coverage and decision support are
   * withheld). false = computed for the current values. null = no prediction yet.
   */
  prediction_inputs_stale?: boolean | null;
  /** true = candidates were added / removed / restored / edited after the ranking ran, so the order is out of date. */
  ranking_outdated?: boolean | null;
  /** Ready-to-show sentence when prediction_inputs_stale or ranking_outdated applies. */
  ranking_notice?: string | null;
  /** Stored values for fields the diagnostic does not model (older rows). Never used by the model. */
  ignored_properties?: Record<string, number> | null;
  /** Removed from the analysis: hidden by default, excluded from ranking, retained with its history. */
  archived?: boolean;
  archived_at?: string | null;
  prediction_count?: number;
  outcome_count?: number;
  /** Permanent deletion is only offered while nothing depends on the candidate. */
  deletable?: boolean;
  delete_blocked_reason?: string | null;
  /** The input values are locked once a physical outcome has been recorded. */
  features_editable?: boolean;
  features_locked_reason?: string | null;
}

/** Fields that can be edited. Send only what changed. `features` replaces the WHOLE set of input values, and
 *  `units` is only accepted together with `features`. */
export interface CandidateUpdate {
  name?: string;
  features?: Record<string, number>;
  units?: Record<string, string>;
}

export interface DeleteCandidateResult {
  deleted: boolean;
  id: number;
  name: string;
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

// ---- Candidate CSV import (preview writes nothing; import is all-or-nothing and needs confirm: true) ----
export interface CandidateImportOptions {
  /** Column holding the candidate names (default on the server: "candidate_name"). */
  name_column?: string;
  /** {diagnostic field: column in the file}. Fields left out fall back to an exact header match only. */
  column_mapping?: Record<string, string>;
  /** Explicit unit of the file's values per field; converted only by an exact factor, never guessed. */
  units?: Record<string, string>;
  /** The user confirms every value not given an explicit unit is already in the diagnostic's own unit. */
  confirm_canonical_units?: boolean;
  /** Only honoured by the import endpoint. */
  confirm?: boolean;
}

export interface CandidateImportProblem { row: number | null; column: string | null; message: string }

export interface CandidateImportPreview {
  filename: string;
  sha256: string;
  ok: boolean;
  blocked_reason: string | null;
  headers: string[];
  row_count: number;
  blank_rows_skipped: number;
  name_column: string | null;
  mapping: Record<string, string | null>;
  suggested_mapping: Record<string, string | null>;
  ignored_columns: string[];
  expected_units: Record<string, string | null>;
  units: Record<string, string>;
  preview_rows: { row: number; name: string; ok: boolean; values: Record<string, string> }[];
  would_import: number;
  error_count: number;
  errors: CandidateImportProblem[];
}

export interface CandidateImportResult {
  imported_count: number;
  candidates: { id: number; candidate_name: string }[];
  sha256: string;
  ranking_notice: string;
}

function candidateImportForm(file: File, options: CandidateImportOptions) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("options", JSON.stringify(options));
  return formData;
}

export function previewCandidateImport(changeCaseId: number, file: File, options: CandidateImportOptions) {
  return apiRequest<CandidateImportPreview>(`/api/change-cases/${changeCaseId}/candidates/import/preview`, {
    method: "POST",
    formData: candidateImportForm(file, options),
  });
}

export function importCandidates(changeCaseId: number, file: File, options: CandidateImportOptions) {
  return apiRequest<CandidateImportResult>(`/api/change-cases/${changeCaseId}/candidates/import`, {
    method: "POST",
    formData: candidateImportForm(file, { ...options, confirm: true }),
  });
}

export function listCandidates(changeCaseId: number, includeArchived = false) {
  return apiRequest<Candidate[]>(
    `/api/change-cases/${changeCaseId}/candidates${includeArchived ? "?include_archived=true" : ""}`,
  );
}

export function updateCandidate(changeCaseId: number, candidateId: number, changes: CandidateUpdate) {
  return apiRequest<Candidate>(`/api/change-cases/${changeCaseId}/candidates/${candidateId}`, {
    method: "PATCH",
    json: changes,
  });
}

/** Removes a candidate from the analysis. Nothing is deleted; it can be restored. */
export function archiveCandidate(changeCaseId: number, candidateId: number) {
  return apiRequest<Candidate>(`/api/change-cases/${changeCaseId}/candidates/${candidateId}/archive`, { method: "POST" });
}

export function restoreCandidate(changeCaseId: number, candidateId: number) {
  return apiRequest<Candidate>(`/api/change-cases/${changeCaseId}/candidates/${candidateId}/restore`, { method: "POST" });
}

/** Permanent. Only while the candidate has never been ranked and has no recorded outcome. */
export function deleteCandidate(changeCaseId: number, candidateId: number) {
  return apiRequest<DeleteCandidateResult>(`/api/change-cases/${changeCaseId}/candidates/${candidateId}`, {
    method: "DELETE",
  });
}

export interface RankResult {
  /** null = not ranked (insufficient evidence); never first, never last. */
  rank: number | null;
  ranking_status: RankingStatus;
  ranking_statement: string;
  /** Same on every result of one run. */
  ranking_summary: {
    candidates: number;
    ranked: number;
    ranked_with_caution: number;
    not_ranked: number;
    message: string;
    ordering_rule: string;
  };
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