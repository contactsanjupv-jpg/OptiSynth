import { apiRequest } from "@/lib/api/http";
import type { BacktestResult, Experiment, ModelMetrics, Recommendation } from "@/types";

export function listExperiments(projectId: number) {
  return apiRequest<Experiment[]>(`/api/projects/${projectId}/experiments`);
}

/**
 * Calls the live Bayesian-optimization recommendation endpoint
 * (POST /api/projects/:id/recommend -> engine/optimization/recommend.py).
 * All prediction/uncertainty/scoring math happens server-side; this
 * function only shapes the HTTP call. The backend also persists each
 * recommendation (see recommendations table) so getCandidateDetail can
 * fetch one by id afterwards.
 */
export function getRecommendations(projectId: number, nRecommendations = 5) {
  return apiRequest<{ recommendations: Recommendation[] }>(
    `/api/projects/${projectId}/recommend`,
    { method: "POST", json: { n_recommendations: nRecommendations } }
  );
}

/** The most recently generated recommendation batch for this project,
 * persisted server-side -- lets the Candidates page reload without
 * re-running optimization. */
export function getLatestCandidates(projectId: number) {
  return apiRequest<Recommendation[]>(`/api/projects/${projectId}/candidates`);
}

export function getCandidateDetail(recommendationId: number) {
  return apiRequest<Recommendation>(`/api/candidates/${recommendationId}`);
}

/** Retrospective backtest on the customer's own uploaded data -- this is the
 * ROI evidence source, not a synthetic benchmark. See engine/evaluation/backtest.py. */
export function getBacktest(projectId: number, target?: number) {
  const qs = target !== undefined ? `?target=${target}` : "";
  return apiRequest<BacktestResult>(`/api/projects/${projectId}/backtest${qs}`);
}

/** Cross-validated surrogate-model quality (R^2 / MAE / accuracy-within-tolerance),
 * NOT computed on training data. See engine/evaluation/model_metrics.py. */
export function getModelMetrics(projectId: number) {
  return apiRequest<ModelMetrics>(`/api/projects/${projectId}/model-metrics`);
}
