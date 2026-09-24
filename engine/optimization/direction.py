import numpy as np


def best_so_far(y: np.ndarray, direction: str) -> float:
    return float(np.min(y)) if direction == "minimize" else float(np.max(y))


def is_better(a: float, b: float, direction: str) -> bool:
    return a < b if direction == "minimize" else a > b


def target_reached(current_best: float, target: float, direction: str) -> bool:
    return current_best >= target if direction == "maximize" else current_best <= target
