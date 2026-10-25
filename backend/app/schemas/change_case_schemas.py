"""
Pydantic v2 schemas for the forced-substitution qualification diagnostic
(change_cases domain). Scope is deliberately narrow, per the approved
Phase 2 plan: customer-supplied candidates only (no product-level cap on how
many the customer nominates), no candidate discovery/generation, no open-ended
formulation optimization.
"""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from backend.app.schemas.errors import ValidationError


TriggerType = Literal["regulatory_restriction", "supplier_loss", "component_obsolescence", "other"]
ChangeCaseStatus = Literal["draft", "active", "completed"]


# ---------------------------------------------------------------------------
# Qualification specification (the requirement a diagnostic is defined by)
# ---------------------------------------------------------------------------

MAX_SPEC_FEATURES = 50
MAX_NAME_LENGTH = 100
MAX_UNIT_LENGTH = 40


def _clean_name(value, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    if value != value.strip():
        raise ValueError(f"{what} {value!r} has leading or trailing spaces; remove them so it matches the data headers exactly")
    if not value:
        raise ValueError(f"{what} must not be empty")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{what} {value[:20]!r}... is longer than {MAX_NAME_LENGTH} characters")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{what} {value!r} contains control characters")
    return value


class QualificationSpec(BaseModel):
    """The requirement a diagnostic is built around. EVERY key here is read by the engine or the
    evidence gate; unknown keys are rejected (extra='forbid') because an ignored key would let a
    user believe the model honours a setting it never sees."""
    model_config = ConfigDict(extra="forbid")

    feature_columns: list[str] = Field(description="Modelled input properties, named exactly as in the evidence data.")
    target_metric: str = Field(description="The property the requirement is on.")
    target_value: float = Field(description="Threshold the target property must reach.")
    direction: Literal["maximize", "minimize"] = Field(
        description="'maximize': the property must be at or above target_value; 'minimize': at or below.")
    units: dict[str, str] | None = Field(
        default=None,
        description="Canonical unit for EVERY modelled input feature and for the target (required when a diagnostic "
        "is created; cannot be edited afterwards). Use a real unit (degC, h, mm ...), or explicitly 'dimensionless', "
        "'fraction' (0-1) or '%' (0-100) -- three different units, never converted into each other. Any other "
        "explicit label is a custom unit matched by exact symbol only (letter case matters, spacing is ignored). Older diagnostics may lack units; they "
        "cannot be ranked.")

    @field_validator("feature_columns", mode="after")
    @classmethod
    def _features(cls, value):
        if not value:
            raise ValueError("at least one feature column is required")
        if len(value) > MAX_SPEC_FEATURES:
            raise ValueError(f"at most {MAX_SPEC_FEATURES} feature columns are supported")
        seen = {}
        for name in value:
            _clean_name(name, "feature column")
            key = name.casefold()
            if key in seen:
                raise ValueError(f"feature column {name!r} is listed twice"
                                 + ("" if seen[key] == name else f" (as {seen[key]!r} and {name!r}; names are compared ignoring case)"))
            seen[key] = name
        return value

    @field_validator("target_metric", mode="after")
    @classmethod
    def _target(cls, value):
        return _clean_name(value, "target metric")

    @field_validator("target_value", mode="before")
    @classmethod
    def _target_value(cls, value):
        # bool is an int subclass and strings like "80" would be silently coerced: both are refused.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("target value must be a number")
        if not math.isfinite(value):
            raise ValueError("target value must be a finite number (not NaN or infinity)")
        return float(value)

    @field_validator("units", mode="after")
    @classmethod
    def _units(cls, value):
        if value is None:
            return value
        for column, unit in value.items():
            _clean_name(column, "unit column")
            if not unit.strip():
                raise ValueError(f"the unit for {column!r} must not be empty (leave the column out to leave it unchecked)")
            if len(unit) > MAX_UNIT_LENGTH:
                raise ValueError(f"the unit for {column!r} is longer than {MAX_UNIT_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _cross_checks(self):
        features = {f.casefold() for f in self.feature_columns}
        if self.target_metric.casefold() in features:
            raise ValueError(f"the target metric {self.target_metric!r} is also listed as a feature column; "
                             "a model cannot predict a value it is also given as an input")
        known = set(self.feature_columns) | {self.target_metric}
        if self.units:
            unknown = sorted(set(self.units) - known)
            if unknown:
                raise ValueError("units name columns that are neither features nor the target metric: "
                                 + ", ".join(repr(u) for u in unknown))
        return self


def _describe_spec_errors(err: PydanticValidationError) -> str:
    problems = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"])
        kind = e["type"]
        if kind == "missing":
            problems.append(f"'{loc}' is required.")
        elif kind == "extra_forbidden":
            problems.append(f"Unrecognized field '{loc}'; it would be ignored by the model, so it is not accepted.")
        elif kind == "literal_error" and loc == "direction":
            problems.append("'direction' must be 'maximize' or 'minimize'.")
        else:
            msg = str(e["msg"])
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            problems.append(f"{loc + ': ' if loc else ''}{msg[0].upper() + msg[1:]}" + ("" if msg.endswith(".") else "."))
    return "Invalid qualification_spec. " + " ".join(problems)


def validate_qualification_spec(raw) -> dict:
    """Validates a qualification spec and returns the clean dict that is stored. Raises the
    product's ValidationError (HTTP 400, customer-safe message). Used by every code path that
    creates or changes a spec, so a direct API call gets exactly the checks the form does."""
    if not isinstance(raw, dict):
        raise ValidationError("Invalid qualification_spec. It must be an object.", field="qualification_spec")
    try:
        spec = QualificationSpec.model_validate(raw)
    except PydanticValidationError as err:
        raise ValidationError(_describe_spec_errors(err), field="qualification_spec") from None
    return spec.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class CreateChangeCaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger_type: TriggerType
    restricted_substance: str | None = None
    qualification_spec: dict = Field(default_factory=dict)


class UpdateStatusRequest(BaseModel):
    status: ChangeCaseStatus


class UpdateChangeCaseRequest(BaseModel):
    """Partial edit of a diagnostic: only the fields that are sent are changed (send
    restricted_substance as null or "" to clear it). Fields that define the evidence structure
    (feature columns, target metric, units) are deliberately not editable; unknown fields are
    rejected, not ignored."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    trigger_type: TriggerType | None = None
    restricted_substance: str | None = Field(default=None, max_length=500)
    target_value: float | None = None
    direction: Literal["maximize", "minimize"] | None = None

    @field_validator("target_value", mode="before")
    @classmethod
    def _target_value(cls, value):
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("target value must be a number")
        if not math.isfinite(value):
            raise ValueError("target value must be a finite number (not NaN or infinity)")
        return float(value)

    @field_validator("name", "trigger_type", "direction", "target_value", mode="after")
    @classmethod
    def _not_null_when_sent(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} can't be empty")
        return value


def _strict_feature_values(value):
    """Feature values must be real JSON numbers. Pydantic's default float coercion would silently turn
    True into 1.0 and the text "5" into 5.0 -- a value the user did not type as a number being used as
    model input. NaN / infinity are refused later with the candidate-specific message
    (change_case_rules.check_candidate_features_finite)."""
    if not isinstance(value, dict):
        return value  # the type check below produces the error
    for key, v in value.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"the value for {key!r} must be a number")
    return value


class AddCandidateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    features: dict[str, float] = Field(
        description="Candidate's known property values, keyed by the same "
        "feature column names used in the uploaded qualification dataset."
    )
    units: dict[str, str] | None = Field(
        default=None,
        description="Unit for each feature value, required wherever the change case declares a canonical "
        "unit for that feature. Converted only by an exact factor; never guessed.",
    )

    _check_numbers = field_validator("features", mode="before")(_strict_feature_values)


class UpdateCandidateRequest(BaseModel):
    """Partial edit of a candidate: only the fields that are sent are changed. `features` replaces the
    candidate's WHOLE set of input values (every modelled feature must be present, with its unit
    wherever the diagnostic declares one); `units` is only accepted together with `features`.
    Unknown fields are rejected, not ignored."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    features: dict[str, float] | None = None
    units: dict[str, str] | None = None

    _check_numbers = field_validator("features", mode="before")(_strict_feature_values)

    @field_validator("name", "features", mode="after")
    @classmethod
    def _not_null_when_sent(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} can't be empty")
        return value


class RecordOutcomeRequest(BaseModel):
    recommended_experiment_id: int | None = None
    actual_result: str = Field(min_length=1)
    passed_spec: bool


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class ChangeCaseResponse(BaseModel):
    id: int
    name: str
    trigger_type: TriggerType
    restricted_substance: str | None
    status: ChangeCaseStatus
    created_at: str
    updated_at: str
    # Set when the diagnostic is archived (hidden from the default list and read-only).
    archived_at: str | None = None

    model_config = {"from_attributes": True}


class QualificationDatasetResponse(BaseModel):
    id: int
    original_filename: str
    row_count: int
    created_at: str

    model_config = {"from_attributes": True}


class PredictionResponse(BaseModel):
    # The model's predicted target-property value, in the case's canonical target unit. This is a
    # MODEL ESTIMATE, not a measured result. None means "not recorded": the prediction was stored
    # before predicted values were persisted and is never back-filled.
    predicted_value: float | None = None
    uncertainty_std: float
    predicted_probability: float
    model_version: str
    dataset_version_id: int

    model_config = {"protected_namespaces": ()}


class CandidateResponse(BaseModel):
    id: int
    candidate_name: str
    properties: dict
    input_record: dict | None = None
    # Stale-ranking marker: True when latest_prediction came from an older dataset than the
    # current evidence; None until the candidate has a prediction.
    prediction_stale: bool | None = None
    prediction_dataset: dict | None = None
    current_dataset: dict | None = None
    stale_notice: str | None = None
    # Level 1D. False = the prediction was computed for the candidate's CURRENT input values; True =
    # the values were edited after the ranking ran (the prediction is kept as history, but rank,
    # domain coverage and decision support are withheld); None = no prediction yet.
    prediction_inputs_stale: bool | None = None
    # True when candidates were added / removed / restored / edited after the ranking ran, so the order
    # is out of date (rank is withheld for every candidate until the ranking is re-run).
    ranking_outdated: bool | None = None
    # Ready-to-show sentence when either of the two flags above applies.
    ranking_notice: str | None = None
    # Stored values for fields this diagnostic does not model (legacy rows); never used by the model.
    ignored_properties: dict | None = None
    # How well the historical data supports ranking this candidate (set whenever a prediction for its current
    # input values exists): ranked | ranked_with_caution | not_ranked_insufficient_evidence, with a ready-to-show
    # statement. A not-ranked candidate has rank None whatever else is true; its prediction is an extrapolation.
    ranking_status: str | None = None
    ranking_statement: str | None = None
    # Lifecycle: what depends on the candidate and what may be done with it.
    archived: bool = False
    archived_at: str | None = None
    prediction_count: int = 0
    outcome_count: int = 0
    deletable: bool = True
    delete_blocked_reason: str | None = None
    features_editable: bool = True
    features_locked_reason: str | None = None
    latest_prediction: PredictionResponse | None = None
    # Heuristic historical-range coverage, judged against the dataset the
    # latest prediction was generated from. None if not yet ranked.
    domain_coverage: dict | None = None
    # C1: categorical decision support derived only from domain_coverage
    # (evidence_supported / caution / requires_validation). None until ranked.
    decision_support: dict | None = None
    # 1 = best, by the same deterministic ordering the ranking run uses (probability, then predicted
    # value in the favorable direction, then candidate id). None until the candidate has a prediction.
    rank: int | None = None


class RecommendedExperimentResponse(BaseModel):
    id: int
    description: str
    priority: int


class RankResultResponse(BaseModel):
    """Response for POST /rank -- every candidate's fresh prediction plus
    its recommended validation experiment, best-ranked first."""
    # "model_quality" legitimately starts with Pydantic's reserved "model_"
    # prefix (used for its own methods like model_dump); this field is not
    # one of those methods, so the collision is silenced rather than
    # renaming a name already used consistently across the service/report.
    model_config = {"protected_namespaces": ()}

    # 1 = best. None = NOT RANKED: the historical data does not cover this candidate's inputs, so no position
    # is given (neither first nor last) -- see ranking_status / ranking_statement. The model's own numbers
    # below are still reported unchanged for such a candidate; they are an extrapolation.
    rank: int | None
    ranking_status: str | None = None  # ranked | ranked_with_caution | not_ranked_insufficient_evidence
    ranking_statement: str | None = None
    # Same dict on every result of one run: counts of ranked / provisional / not-ranked candidates and a message.
    ranking_summary: dict | None = None
    candidate_id: int
    candidate_name: str
    # Model-predicted target-property value in the case's canonical target unit (an estimate, not a measurement).
    predicted_value: float | None = None
    predicted_probability: float
    uncertainty_std: float
    recommended_experiment: str
    domain_coverage: dict | None = None
    decision_support: dict | None = None
    model_quality: dict | None = None
    uncertainty_calibration: dict | None = None


class OutcomeResponse(BaseModel):
    id: int
    candidate_id: int
    actual_result: str
    passed_spec: bool
    created_at: str