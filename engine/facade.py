"""
Public interface to the optimization engine. `backend/app/services/optimization_service.py`
imports ONLY from this module -- never reaching into engine/optimization,
engine/models, etc. directly. This keeps the engine free to be refactored
internally without touching the web layer, and keeps the web layer from
accidentally depending on engine internals.
"""
from threadpoolctl import threadpool_limits

from engine.optimization.recommend import recommend_next
from engine.optimization.rank_candidates import rank_candidates, ranking_sort_key
from engine.evaluation.backtest import run_backtest
from engine.evaluation.model_metrics import compute_model_metrics

threadpool_limits(limits=1)

from engine.evaluation.model_metrics import compute_model_metrics, compute_uncertainty_calibration
...
from engine.validation import ENGINE_VERSION, MODEL_FAMILY
from engine.validation import applicability, identity, protocols as validation_protocols, runner as validation_runner, scoring, uncertainty  # noqa: E402,F401
from engine.validation.runner import run_protocol as run_validation_protocol  # noqa: E402

__all__ = ["ENGINE_VERSION", "MODEL_FAMILY", "run_validation_protocol", "validation_protocols", "validation_runner", "identity", "applicability", "scoring", "uncertainty", "recommend_next", "rank_candidates", "ranking_sort_key", "run_backtest", "compute_model_metrics", "compute_uncertainty_calibration"]