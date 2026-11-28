"""Decision engine for substitution candidates: constraints, Pareto analysis and explanations.

Pure functions over plain dicts. Nothing here reads a database, fits a model or invents a value: a value that was not
recorded is unknown, and unknown is never treated as passing. No score is computed and no weights exist."""

DECISION_ENGINE_VERSION = "optisynth-decision-6.0"
Z95 = 1.959963984540054  # half width of the model interval, in predictive standard deviations
INTERVAL_NOTE = ("The interval is the model's own 95 percent interval from its normal prediction. It is not a calibrated "
                 "guarantee. See the model version's validation for how such intervals behaved on held out rows.")
QUALIFICATION_STATEMENT = ("Not qualified. A position on the Pareto front, a rank or a prediction is not qualification. "
                           "Physical validation and an approval are required.")
