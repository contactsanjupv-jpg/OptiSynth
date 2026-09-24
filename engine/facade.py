"""
Public interface to the optimization engine. `backend/app/services/optimization_service.py`
imports ONLY from this module -- never reaching into engine/optimization,
engine/models, etc. directly. This keeps the engine free to be refactored
internally without touching the web layer, and keeps the web layer from
accidentally depending on engine internals.
"""
from threadpoolctl import threadpool_limits

from engine.optimization.recommend import recommend_next
from engine.optimization.rank_candidates import rank_candidates
from engine.evaluation.backtest import run_backtest
from engine.evaluation.model_metrics import compute_model_metrics

threadpool_limits(limits=1)

from engine.evaluation.model_metrics import compute_model_metrics, compute_uncertainty_calibration
...
__all__ = ["recommend_next", "rank_candidates", "run_backtest", "compute_model_metrics", "compute_uncertainty_calibration"]