"""Scientific validation tooling for the surrogate model. It never changes how the Gaussian process is built or
how candidates are scored; it records what the model was, and measures how it behaves on data it did not see."""

# Bump when anything in this package, or the surrogate it describes, changes in a way that can change results.
# It is part of every model identity hash, so a change here produces new model versions rather than silently
# re-labelling old ones.
ENGINE_VERSION = "optisynth-engine-5.0"
MODEL_FAMILY = "gp-rank-v1"
