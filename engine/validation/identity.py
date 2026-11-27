"""Deterministic model identity. It covers the numerical runtime as well as the data and settings.

A model version is identified by what it was built from, never by when it was built or by a counter. Two builds
from the same canonical training rows, feature definitions, target, preprocessing, configuration, seed and engine
version have the same identity; any difference gives a different one. Row order, key order and float formatting
cannot change it."""
import hashlib
import json
import math

from engine.validation import ENGINE_VERSION, MODEL_FAMILY

FLOAT_FORMAT = ".12g"  # 12 significant digits: finer than any laboratory measurement, coarser than float noise


def canonical_number(value) -> str:
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("Model identity cannot include a non finite value.")
    if x == 0.0:
        x = 0.0  # -0.0 and 0.0 are the same measurement
    return format(x, FLOAT_FORMAT)


def canonical_feature_definitions(feature_columns, units: dict) -> list:
    """Feature definitions sorted by name, so column order in the spec cannot change identity."""
    units = units or {}
    return [{"name": name, "unit": units.get(name)} for name in sorted(feature_columns)]


def canonical_rows(rows, feature_columns) -> list:
    """rows: [{"features": {col: value}, "target_value": v}]. Returns [[f1, f2, ..., target], ...] as canonical
    strings, features in sorted name order, rows sorted. Exact duplicates are NOT removed here: the caller decides
    what the training set is, and that decision is part of what is being identified."""
    names = sorted(feature_columns)
    out = [[canonical_number(r["features"][n]) for n in names] + [canonical_number(r["target_value"])] for r in rows]
    out.sort()
    return out


def sha256_json(payload) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def training_data_hash(rows, feature_columns) -> str:
    return sha256_json({"feature_order": sorted(feature_columns), "rows": canonical_rows(rows, feature_columns)})


def model_identity(rows, feature_columns, units: dict, target: dict, preprocessing: dict, configuration: dict,
                   seed: int, engine_version: str = ENGINE_VERSION, family: str = MODEL_FAMILY, runtime: dict = None) -> dict:
    """Returns {"identity_hash", "training_data_hash", "model_key", "payload"}. `target` is
    {"name", "unit", "direction"}. `preprocessing` and `configuration` are plain JSON describing exactly what the
    engine does (see describe_surrogate / describe_preprocessing)."""
    data_hash = training_data_hash(rows, feature_columns)
    runtime = environment_record() if runtime is None else runtime
    payload = {
        "runtime": runtime,
        "family": family,
        "engine_version": engine_version,
        "training_data_hash": data_hash,
        "features": canonical_feature_definitions(feature_columns, units),
        "target": {"name": target.get("name"), "unit": target.get("unit"), "direction": target.get("direction")},
        "preprocessing": preprocessing,
        "configuration": configuration,
        "seed": int(seed),
    }
    digest = sha256_json(payload)
    return {"identity_hash": digest, "training_data_hash": data_hash, "model_key": f"gp-{digest[:12]}", "payload": payload}


def describe_preprocessing() -> dict:
    return {
        "scaling": "min-max per feature, bounds taken from the training rows only",
        "target_scaling": "standardised inside the Gaussian process (normalize_y)",
        "duplicates": "exact duplicate rows (same inputs and same target) are removed before fitting",
        "missing_values": "not allowed; rows with a missing or non finite value are refused before fitting",
    }


def describe_surrogate(dim: int, random_state: int = 0) -> dict:
    """What build_surrogate constructs, read from the object itself so the record cannot drift from the code."""
    from engine.models.surrogate import build_surrogate
    gp = build_surrogate(dim, random_state=random_state)
    return {
        "model": "scikit-learn GaussianProcessRegressor",
        "kernel": str(gp.kernel),
        "normalize_y": bool(gp.normalize_y),
        "n_restarts_optimizer": int(gp.n_restarts_optimizer),
        "alpha": float(gp.alpha),
    }


def environment_record() -> dict:
    """The numerical runtime a fit was made under. It is PART of the model identity: the optimiser that fits the
    kernel hyperparameters lives in scipy, and other scipy versions were measured to move results in the sixth or
    seventh significant digit on a well conditioned dataset and more where the likelihood is flat. Same training
    rows under another runtime is therefore a different model version, never a silent relabel."""
    import platform
    import numpy, scipy, sklearn  # noqa: E401
    return {"python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__,
            "scikit-learn": sklearn.__version__}
