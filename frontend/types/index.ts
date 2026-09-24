/**
 * Types mirror the Flask API's actual JSON responses (see
 * backend/app/api/routes/*.py). Keep these in sync with the backend by
 * hand -- there is no shared schema generator yet.
 */

export type Direction = "maximize" | "minimize";
export type ProjectStatus = "draft" | "running" | "completed";
export type ConstraintOp = "<=" | ">=";
export type MembershipRole = "owner" | "admin" | "member";

export interface Constraint {
  column: string;
  op: ConstraintOp;
  value: number;
}

export interface Project {
  id: number;
  organization_id: number;
  created_by_user_id: number | null;
  name: string;
  objective: string | null;
  target_metric: string;
  direction: Direction;
  feature_columns: string[];
  constraints: Constraint[];
  target_value: number | null;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
  /** Present only on list_projects responses (computed server-side). */
  experiment_count?: number;
}

export interface Experiment {
  id: number;
  organization_id: number;
  project_id: number;
  dataset_id: number | null;
  features: Record<string, number>;
  target_value: number;
  constraint_values: Record<string, number>;
  source: "historical" | "recommended";
  created_at: string;
}

export interface Recommendation {
  id?: number;
  project_id?: number;
  rank?: number;
  features: Record<string, number>;
  predicted_value: number;
  uncertainty_std: number;
  feasibility_probability: number;
  acquisition_score: number;
  created_at?: string;
}

export interface BacktestResult {
  target?: number;
  actual_order_evals: number | null;
  engine_order_evals: number | null;
  n_historical_rows: number;
  reduction_pct: number | null;
  actual_order_curve?: number[];
  engine_order_curve?: number[];
  error?: string;
}

export interface ModelMetrics {
  r2_score?: number;
  mean_absolute_error?: number;
  prediction_accuracy_pct?: number;
  accuracy_tolerance_band?: number;
  n_folds_used?: number;
  n_points_scored?: number;
  error?: string;
}

export interface ReportRecord {
  id: number;
  organization_id: number;
  project_id: number;
  kind: string;
  file_path: string;
  n_historical_rows: number;
  created_at: string;
}

export interface DashboardProjectSummary {
  id: number;
  name: string;
  status: ProjectStatus;
  target_metric: string;
  target_value: number | null;
  direction: Direction;
  experiment_count: number;
  best_value: number | null;
  target_met: boolean;
  updated_at: string;
}

export interface DashboardSummary {
  active_projects: number;
  total_projects: number;
  total_experiments: number;
  targets_achieved: number;
  projects: DashboardProjectSummary[];
}

export interface AuditLogEntry {
  id: number;
  organization_id: number;
  user_id: number | null;
  project_id: number | null;
  action: string;
  detail: string | null;
  created_at: string;
}

export interface UserPublic {
  id: number;
  email: string;
  display_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface BillingSummary {
  plan: string;
  status: string;
  current_period_end?: string | null;
  stripe_configured: boolean;
}

export interface ApiError {
  error: string;
  field?: string;
  [key: string]: unknown;
}
