"""
R&D optimization engine.

This package is intentionally standalone: it has no knowledge of Flask,
HTTP, sessions, tenants, or the database. It takes numpy arrays in and
returns numbers/arrays out. The `backend/app/services/optimization_service.py`
module is the ONLY place that is allowed to import from here and hand
results to the web layer -- routes, repositories, and auth code must never
import from `engine` directly.

Submodules:
  models/         Surrogate model construction (Gaussian Process wrapper).
  preprocessing/  Feature normalization shared by every optimization step.
  acquisition/    Acquisition functions (Expected Improvement).
  constraints/    Feasibility-probability scoring for constrained candidates.
  optimization/   The sequential candidate-selection loop itself.
  evaluation/      Backtesting and cross-validated model-quality metrics.
  benchmarks/      Synthetic, reproducible benchmark used when no customer
                    dataset exists yet (demos / sales pilots).
"""
