"""Validation protocol definitions.

Every protocol states what is held out, what the result can and cannot show, and how much data it needs. A protocol
hash covers the definition and its parameters, so two results with the same hash were produced the same way."""
from engine.validation.identity import sha256_json

VERSION = 1

RANDOM_ROWS = "random_rows_kfold"
GROUPED_INPUT_SETS = "grouped_input_sets_kfold"
LEAVE_CLUSTER_OUT = "leave_cluster_out_extrapolation"
RECORDED_UNIT = "grouped_by_recorded_unit"

# The protocols that decide whether a model's validation is complete. Random rows is a reference only.
REQUIRED = (GROUPED_INPUT_SETS, LEAVE_CLUSTER_OUT)

MAX_FOLDS = 5
RECOMMENDED_ROWS = 20
MIN_TRAIN_INPUT_SETS = 4
MAX_CLUSTERS = 5

DEFINITIONS = {
    RANDOM_ROWS: {
        "label": "Random rows, k fold",
        "grouping_unit": "single row",
        "evaluates": "interpolation between rows, optimistic when input sets are repeated",
        "purpose": "A reference only. Rows are split at random, so repeated measurements of one input set can land on "
                   "both sides of a split and flatter the result.",
        "generalisation_claim": "none",
        "limitations": [
            "Repeated measurements of the same input set can appear in both training and held out rows.",
            "Not evidence of how the model behaves on a new batch, supplier or material.",
        ],
        "minimum": {"rows": 6, "input_sets": None},
        "required": False,
    },
    GROUPED_INPUT_SETS: {
        "label": "Grouped by input set, k fold",
        "grouping_unit": "identical input values (one group per distinct set of inputs)",
        "evaluates": "interpolation inside the covered input space, with repeated measurements kept together",
        "purpose": "Holds out whole input sets, so a repeated measurement is never used to predict itself.",
        "generalisation_claim": "interpolation between distinct input sets in the evidence",
        "limitations": [
            "Near identical formulations are different groups, so neighbours of a held out input set stay in training.",
            "Does not test a new batch, lot, supplier or material: no such identifier is used.",
            "Results describe the evidence's own region and say little about points far from it.",
        ],
        "minimum": {"rows": 6, "input_sets": 6},
        "required": True,
    },
    LEAVE_CLUSTER_OUT: {
        "label": "Leave one region out",
        "grouping_unit": "region of the input space (deterministic farthest point clusters of the distinct input sets)",
        "evaluates": "prediction in a region of the input space that has no nearby training rows",
        "purpose": "A stress test for extrapolation in the inputs. Whole regions are held out and predicted from the "
                   "rest.",
        "generalisation_claim": "extrapolation in input space only",
        "limitations": [
            "Regions are defined by input values alone. They are not batches, suppliers, materials or dates.",
            "Held out regions are chosen by geometry, so the difficulty depends on how the evidence is spread.",
            "A good result here does not show the model extrapolates reliably to new materials.",
        ],
        "minimum": {"rows": 10, "input_sets": 10},
        "required": True,
    },
    RECORDED_UNIT: {
        "label": "Grouped by batch, lot, supplier or material",
        "grouping_unit": "a recorded batch, lot, supplier or material identifier",
        "evaluates": "generalisation to a new batch, supplier or material",
        "purpose": "The only protocol that can speak to generalisation across real world units.",
        "generalisation_claim": "generalisation across the recorded unit",
        "limitations": ["Needs an identifier on every evidence row."],
        "minimum": {"rows": None, "input_sets": None},
        "required": False,
        "unavailable_reason": "OptiSynth does not store a batch, lot, supplier or material identifier on evidence rows, "
                              "so this protocol cannot be run. Supplier records arrive in a later level.",
    },
}


def definition(key: str) -> dict:
    return DEFINITIONS[key]


def protocol_hash(key: str) -> str:
    """Covers the definition and every parameter that changes how the protocol runs."""
    return sha256_json({"key": key, "version": VERSION, "max_folds": MAX_FOLDS, "min_train_input_sets": MIN_TRAIN_INPUT_SETS,
                        "max_clusters": MAX_CLUSTERS, "definition": DEFINITIONS[key]})


def listing() -> list:
    return [{"key": k, "version": VERSION, "hash": protocol_hash(k), **v} for k, v in DEFINITIONS.items()]
