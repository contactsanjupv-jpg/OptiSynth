import { apiRequest } from "@/lib/api/http";

export type ValidationStatus = "completed" | "completed_with_warnings" | "insufficient_samples" | "failed" | "not_available";
export type ValidationState = "completed" | "completed_with_warnings" | "incomplete" | "failed";

export interface ValidationSummary { state: ValidationState; required: Record<string, string>; note: string }
export interface ModelSummary {
  id: number; program_id: number; program_name?: string; version_number: number; model_key: string; family: string;
  registered_at: string; source: string; kind: "build_record"; fitted_model_stored: false; n_training_rows: number; n_input_sets: number; dataset_id: number | null;
  dataset_filename: string | null; is_current: boolean; validation: ValidationSummary;
}
export interface ModelLibrary { total: number; limit: number; offset: number; versions: ModelSummary[] }

export interface ProtocolDefinition {
  key: string; version: number; hash: string; label: string; grouping_unit: string; evaluates: string; purpose: string;
  generalisation_claim: string; limitations: string[]; minimum: { rows: number | null; input_sets: number | null }; required: boolean;
  unavailable_reason?: string;
}
export interface ValidationBrief {
  id: number; protocol_key: string; protocol_hash: string; status: ValidationStatus; message: string | null; n_rows: number | null;
  n_scored: number | null; created_at: string; seed: number; engine_version: string;
}
export interface ModelDetail extends ModelSummary {
  identity_hash: string; training_data_hash: string; engine_version: string; seed: number;
  feature_definitions: { name: string; unit: string | null }[]; target: { name: string; unit: string | null; direction: string };
  preprocessing: Record<string, string>; configuration: Record<string, string | number | boolean>; environment: Record<string, string>;
  training_summary: { data: DataSummary; replicate_scatter: Scatter; fit: { status: string; fitted_noise_sd: number | null; fitted_kernel?: string } };
  integrity: { reproduces_identity: boolean; problem: string | null }; prediction_count: number;
  protocols: (ProtocolDefinition & { latest: ValidationBrief | null })[]; history: ValidationBrief[];
  permissions: { can_run: boolean }; limitations: string[];
  reproducibility: { statement: string; runtime_recorded: Record<string, string>; runtime_now: Record<string, string>; runtime_matches: boolean };
}
export interface DataSummary {
  n_rows: number; n_input_sets: number; n_repeated_input_sets: number; rows_in_repeated_input_sets: number;
  feature_ranges: Record<string, [number, number]>; target_range: [number, number]; target_sd: number | null;
}
export interface Scatter { status: string; n_sets: number; pooled_sd: number | null; note: string; degrees_of_freedom?: number }

export interface IntervalLevel {
  nominal: number; n: number; inside: number; observed: number; observed_interval_95: [number, number] | null;
  mean_width: number; mean_width_relative_to_range: number | null; reading: string;
}
export interface Calibration {
  n: number; levels: IntervalLevel[]; assessment: string; max_gap: number; mean_crps: number; caveat: string; recalibration: string;
  sigma_error_rank_correlation: number | null;
  standardised_residuals: { n: number; mean: number | null; sd: number | null; note: string };
  sharpness: { mean_sigma: number; median_sigma: number; mean_sigma_relative_to_range: number | null };
}
export interface ValidationPoint { row: number; fold: number; measured: number; predicted: number; sigma: number; nearest_distance: number; applicability: string }
export interface ValidationResult {
  protocol: { key: string; version: number; hash: string }; status: ValidationStatus; message: string | null;
  warnings: { code: string; message: string }[];
  counts: { n_rows: number; n_input_sets: number; n_groups: number | null; n_folds: number; n_folds_used: number; n_folds_skipped: number; n_scored: number } | null;
  metrics: {
    n: number; mae: number; rmse: number; r2: number | null; bias: number; median_absolute_error: number; max_absolute_error: number;
    target_range_evaluated: number; baseline_rmse: number; skill_vs_mean_baseline: number | null;
    residuals: { mean: number; sd: number | null; quantiles: Record<string, number>; by_predicted_level: { band: string; n: number; mean_residual: number; mean_absolute_residual: number }[] };
  } | null;
  calibration: Calibration | null;
  applicability_check: { note: string; convention: string; by_class: { class: string; n: number; median_absolute_error: number; mean_absolute_error: number; coverage_95?: number }[] } | null;
  points: ValidationPoint[] | null;
}
export interface ValidationRecord extends ValidationBrief { result: ValidationResult; evaluation_rows_hash: string | null; fold_assignment_hash: string | null }

export interface Uncertainty {
  model: { description: string; status: string; predictive_sd_of_current_predictions: { min: number; max: number; n: number } | null; fitted_noise_sd: number | null;
           fit_note?: string; held_out: { protocol: string; n_scored: number; calibration: Calibration } | null };
  measurement: { description: string; status: string; reason: string };
  data: { description: string; summary: DataSummary; replicate_scatter: Scatter };
  applicability: { description: string; convention: string };
  note: string;
}
export interface ApplicabilityCandidate {
  candidate_id: number; name: string; archived: boolean; predicted_value: number | null; uncertainty_std: number | null;
  features: Record<string, number>; support: string; class: string; reason?: string; range_status?: string;
  nearest_distance?: number; nearest_training_input_set?: Record<string, number>; nearest_training_rows?: number; unsupported_combination?: boolean;
  range_features?: Record<string, { status: string; value: number; historical_min: number; historical_max: number }>;
}
export interface Applicability {
  convention: string; problem: string | null; support_note?: string; candidates: ApplicabilityCandidate[];
  reference: { sparse_above: number; isolated_above: number; reference_points: number } | { status: string; reason: string } | null;
}
export interface ComparisonRow {
  protocol_key: string; comparable: boolean; reason?: string; reading?: string; same_evaluation_rows?: boolean;
  metrics?: Record<string, { a: number | null; b: number | null; difference: number | null }>;
  sharpness?: { a: number; b: number }; mean_crps?: { a: number; b: number }; calibration_reading?: { a: string; b: string };
  n_scored?: { a: number; b: number };
}
export interface Comparison { a: ModelSummary; b: ModelSummary; protocols: ComparisonRow[]; note: string }

export const listModels = (programId?: number, offset = 0) =>
  apiRequest<ModelLibrary>(`/api/models?limit=25&offset=${offset}${programId ? `&program_id=${programId}` : ""}`);
export const getModel = (id: number) => apiRequest<ModelDetail>(`/api/models/${id}`);
export const getValidation = (id: number, validationId: number) => apiRequest<ValidationRecord>(`/api/models/${id}/validations/${validationId}`);
export const runValidation = (id: number, protocol: string) =>
  apiRequest<{ created: boolean; validation: ValidationBrief }>(`/api/models/${id}/validations`, { method: "POST", json: { protocol } });
export const getUncertainty = (id: number) => apiRequest<Uncertainty>(`/api/models/${id}/uncertainty`);
export const getApplicability = (id: number) => apiRequest<Applicability>(`/api/models/${id}/applicability`);
export const compareModels = (a: number, b: number) => apiRequest<Comparison>(`/api/models/compare?a=${a}&b=${b}`);
export const registerModel = (programId: number) =>
  apiRequest<{ created: boolean; version: ModelSummary }>(`/api/change-cases/${programId}/models/register`, { method: "POST" });
