"""Level 5 scientific safeguards: model identity, protocol definitions, leakage, calibration and sharpness, and
applicability diagnostics. Run:  python3 -m unittest engine.tests.test_validation_center -v"""
import unittest
from unittest import mock

import numpy as np

from engine.validation import ENGINE_VERSION, applicability as app, identity, protocols as P, runner, scoring


def dataset(n=30, seed=3, replicates=False):
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, (n, 2))
    y = 3 * X[:, 0] + np.sin(4 * X[:, 1]) + rng.normal(0, 0.05, n)
    if replicates:
        X = np.vstack([X, X[:6]])
        y = np.concatenate([y, y[:6] + rng.normal(0, 0.05, 6)])
    return X, y


def rows_of(X, y):
    return [{"features": {"a": float(r[0]), "b": float(r[1])}, "target_value": float(t)} for r, t in zip(X, y)]


def ident(rows, **kw):
    args = dict(feature_columns=["a", "b"], units={"a": "fraction", "b": "degC"},
                target={"name": "t", "unit": "h", "direction": "maximize"},
                preprocessing=identity.describe_preprocessing(), configuration=identity.describe_surrogate(2), seed=0)
    args.update(kw)
    return identity.model_identity(rows, **args)


class TestIdentity(unittest.TestCase):
    def setUp(self):
        self.X, self.y = dataset(12)
        self.rows = rows_of(self.X, self.y)

    def test_identical_inputs_give_identical_identity(self):
        self.assertEqual(ident(self.rows)["identity_hash"], ident([dict(r) for r in self.rows])["identity_hash"])

    def test_row_order_feature_order_and_key_order_do_not_matter(self):
        base = ident(self.rows)["identity_hash"]
        shuffled = list(reversed(self.rows))
        self.assertEqual(ident(shuffled)["identity_hash"], base)
        reordered = [{"target_value": r["target_value"], "features": {"b": r["features"]["b"], "a": r["features"]["a"]}} for r in self.rows]
        self.assertEqual(ident(reordered)["identity_hash"], base)
        self.assertEqual(ident(self.rows, feature_columns=["b", "a"])["identity_hash"], base)

    def test_negative_zero_and_float_noise_are_normalised(self):
        a = [{"features": {"a": 0.0, "b": 1.0}, "target_value": 5.0}]
        b = [{"features": {"a": -0.0, "b": 1.0 + 1e-15}, "target_value": 5.0}]
        self.assertEqual(identity.training_data_hash(a, ["a", "b"]), identity.training_data_hash(b, ["a", "b"]))

    def test_every_component_changes_the_identity(self):
        base = ident(self.rows)["identity_hash"]
        changed_row = [dict(r) for r in self.rows]
        changed_row[0] = {"features": dict(changed_row[0]["features"]), "target_value": changed_row[0]["target_value"] + 0.01}
        variants = {
            "a training value": ident(changed_row),
            "a removed row": ident(self.rows[1:]),
            "seed": ident(self.rows, seed=1),
            "a unit": ident(self.rows, units={"a": "percent", "b": "degC"}),
            "the target": ident(self.rows, target={"name": "t", "unit": "h", "direction": "minimize"}),
            "preprocessing": ident(self.rows, preprocessing={"scaling": "none"}),
            "configuration": ident(self.rows, configuration={"model": "other"}),
            "engine version": identity.model_identity(self.rows, ["a", "b"], {"a": "fraction", "b": "degC"},
                                                      {"name": "t", "unit": "h", "direction": "maximize"},
                                                      identity.describe_preprocessing(), identity.describe_surrogate(2), 0,
                                                      engine_version=ENGINE_VERSION + "x"),
        }
        for label, v in variants.items():
            self.assertNotEqual(v["identity_hash"], base, label)

    def test_non_finite_values_are_refused(self):
        with self.assertRaises(ValueError):
            identity.canonical_number(float("nan"))

    def test_model_key_comes_from_the_hash(self):
        m = ident(self.rows)
        self.assertEqual(m["model_key"], "gp-" + m["identity_hash"][:12])

    def test_configuration_is_read_from_the_real_surrogate(self):
        cfg = identity.describe_surrogate(2)
        self.assertIn("Matern", cfg["kernel"])
        self.assertTrue(cfg["normalize_y"])


class TestRuntimeIsPartOfIdentity(unittest.TestCase):
    """Other scipy versions were measured to move validation results in the sixth or seventh significant digit
    (more where the likelihood is flat), so the runtime is part of what a model version is."""

    def setUp(self):
        self.rows = rows_of(*dataset(12))

    def test_each_library_version_changes_the_identity(self):
        base_runtime = identity.environment_record()
        base = ident(self.rows, runtime=base_runtime)["identity_hash"]
        self.assertEqual(ident(self.rows, runtime=dict(base_runtime))["identity_hash"], base)
        for lib in ("python", "numpy", "scipy", "scikit-learn"):
            changed = dict(base_runtime, **{lib: base_runtime[lib] + ".post1"})
            self.assertNotEqual(ident(self.rows, runtime=changed)["identity_hash"], base, lib)

    def test_training_data_identity_does_not_depend_on_the_runtime(self):
        a = ident(self.rows, runtime={"scipy": "1.0"})
        b = ident(self.rows, runtime={"scipy": "2.0"})
        self.assertEqual(a["training_data_hash"], b["training_data_hash"])
        self.assertNotEqual(a["identity_hash"], b["identity_hash"])

    def test_the_default_is_the_live_runtime(self):
        self.assertEqual(ident(self.rows)["payload"]["runtime"], identity.environment_record())
        self.assertEqual(set(identity.environment_record()), {"python", "numpy", "scipy", "scikit-learn"})

    def test_the_environment_is_pinned_and_matches_what_is_installed(self):
        import os
        import numpy, scipy, sklearn  # noqa: E401
        path = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "constraints.txt")
        with open(path, encoding="utf-8") as f:
            pins = dict(line.strip().split("==") for line in f if "==" in line)
        installed = {"numpy": numpy.__version__, "scipy": scipy.__version__, "scikit-learn": sklearn.__version__}
        for lib, version in installed.items():
            self.assertIn(lib, pins, f"{lib} is not pinned in backend/constraints.txt")
            self.assertEqual(pins[lib], version, f"{lib} {version} is installed but backend/constraints.txt pins {pins[lib]}. "
                                                 "Install from the constraints file, or expect new model versions and slightly different results.")


class TestProtocolDefinitions(unittest.TestCase):
    def test_every_protocol_states_its_unit_purpose_limits_and_minimums(self):
        for item in P.listing():
            for field in ("grouping_unit", "evaluates", "purpose", "limitations", "minimum", "generalisation_claim", "hash"):
                self.assertTrue(item[field] is not None, (item["key"], field))
            self.assertTrue(item["limitations"])

    def test_interpolation_is_not_described_as_batch_or_supplier_generalisation(self):
        grouped = P.definition(P.GROUPED_INPUT_SETS)
        self.assertIn("interpolation", grouped["generalisation_claim"])
        self.assertTrue(any("batch" in text and "supplier" in text for text in grouped["limitations"]))
        self.assertEqual(P.definition(P.LEAVE_CLUSTER_OUT)["generalisation_claim"], "extrapolation in input space only")

    def test_hashes_are_stable_and_distinct(self):
        hashes = {k: P.protocol_hash(k) for k in P.DEFINITIONS}
        self.assertEqual(len(set(hashes.values())), len(hashes))
        self.assertEqual(hashes, {k: P.protocol_hash(k) for k in P.DEFINITIONS})

    def test_recorded_unit_protocol_cannot_run_without_identifiers(self):
        X, y = dataset(30)
        result = runner.run_protocol(P.RECORDED_UNIT, X, y)
        self.assertEqual(result["status"], runner.NOT_AVAILABLE)
        self.assertIsNone(result["metrics"])
        self.assertIn("batch", result["message"])


class TestLeakage(unittest.TestCase):
    """Each of these fails if a held out row or its target reaches the fit, the scaling, a threshold or a baseline."""

    @staticmethod
    def assert_no_leak(train_idx, test_idx, X, groups):
        if set(train_idx) & set(test_idx):
            raise AssertionError("a row is in both training and validation")
        if {groups[i] for i in train_idx} & {groups[i] for i in test_idx}:
            raise AssertionError("an input set is in both training and validation")

    def capture(self, key, X, y, groups=None):
        calls = []
        real = runner._fit_recorded

        def spy(dim, Xn, yy, diag, random_state=0):
            calls.append((np.array(Xn), np.array(yy)))
            return real(dim, Xn, yy, diag, random_state)
        with mock.patch.object(runner, "_fit_recorded", spy):
            result = runner.run_protocol(key, X, y, groups=groups, seed=0)
        return result, calls

    def test_leak_check_catches_a_leak(self):
        X, y = dataset(12)
        groups = list(range(12))
        with self.assertRaises(AssertionError):
            self.assert_no_leak([0, 1, 2, 3], [3, 4], X, groups)
        with self.assertRaises(AssertionError):
            self.assert_no_leak([0, 1], [2, 5], X, [0, 0, 0, 1, 2, 3])  # same input set on both sides

    def test_grouped_protocol_never_shares_an_input_set(self):
        X, y = dataset(24, replicates=True)
        groups = []
        ids = {}
        for r in X.tolist():
            groups.append(ids.setdefault(tuple(r), len(ids)))
        result, calls = self.capture(P.GROUPED_INPUT_SETS, X, y, groups)
        self.assertEqual(result["status"].startswith("completed"), True)
        seen_test_rows = {p["row"] for p in result["points"]}
        by_fold = {}
        for p in result["points"]:
            by_fold.setdefault(p["fold"], set()).add(p["row"])
        for fold, test_rows in by_fold.items():
            train_rows = set(range(len(X))) - test_rows
            self.assert_no_leak(train_rows, test_rows, X, groups)
        self.assertEqual(len(seen_test_rows), len(X))
        # replicate rows share a fold with their original
        folds_of = {tuple(X[p["row"]]): set() for p in result["points"]}
        for p in result["points"]:
            folds_of[tuple(X[p["row"]])].add(p["fold"])
        self.assertTrue(all(len(v) == 1 for v in folds_of.values()))

    def test_region_protocol_holds_out_whole_regions(self):
        X, y = dataset(30)
        result, calls = self.capture(P.LEAVE_CLUSTER_OUT, X, y)
        self.assertTrue(result["status"].startswith("completed"))
        labels, c = runner.region_clusters(X)
        for p in result["points"]:
            same_region_in_training = [r for r in range(len(X)) if labels[r] == labels[p["row"]] and r not in
                                       {q["row"] for q in result["points"] if q["fold"] == p["fold"]}]
            self.assertEqual(same_region_in_training, [])

    def test_a_validation_row_is_not_inside_the_training_arrays(self):
        X, y = dataset(24)
        result, calls = self.capture(P.RANDOM_ROWS, X, y)
        by_fold = {}
        for p in result["points"]:
            by_fold.setdefault(p["fold"], []).append(p["row"])
        for (Xn_train, y_train), (fold, rows) in zip(calls, sorted(by_fold.items())):
            self.assertEqual(len(y_train), len(X) - len(rows))
            held_out_targets = set(np.round(y[rows], 12))
            self.assertFalse(held_out_targets & set(np.round(y_train, 12)))

    def test_a_held_out_prediction_does_not_depend_on_that_rows_own_target(self):
        X, y = dataset(24)
        before = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y, seed=0)
        y2 = y.copy()
        y2[5] += 100.0
        after = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y2, seed=0)
        a = {p["row"]: p for p in before["points"]}[5]
        b = {p["row"]: p for p in after["points"]}[5]
        self.assertAlmostEqual(a["predicted"], b["predicted"], places=9)
        self.assertAlmostEqual(a["sigma"], b["sigma"], places=9)
        self.assertNotAlmostEqual(a["measured"], b["measured"])

    def test_scaling_bounds_and_baseline_come_from_training_rows_only(self):
        X, y = dataset(24)
        bounds = []
        real = runner.feature_bounds

        def spy(M):
            bounds.append(np.array(M))
            return real(M)
        with mock.patch.object(runner, "feature_bounds", spy):
            result = runner.run_protocol(P.RANDOM_ROWS, X, y, seed=0)
        by_fold = {}
        for p in result["points"]:
            by_fold.setdefault(p["fold"], []).append(p["row"])
        fold_bounds = bounds[-len(by_fold):]
        for M, (fold, rows) in zip(fold_bounds, sorted(by_fold.items())):
            self.assertEqual(len(M), len(X) - len(rows))
            for r in rows:  # a held out row is not one of the rows the bounds came from
                self.assertFalse(any(np.array_equal(X[r], m) for m in M))

    def test_applicability_thresholds_ignore_the_points_being_assessed(self):
        rng = np.random.default_rng(0)
        train = rng.uniform(0, 1, (20, 2))
        near = np.array([[0.5, 0.5]])
        far = np.array([[5.0, 5.0]])
        _, limits_near = app.assess_points(near, train)
        _, limits_far = app.assess_points(far, train)
        self.assertEqual(limits_near, limits_far)
        self.assertEqual(app.assess_points(far, train)[0][0]["class"], app.ISOLATED)

    def test_no_recalibration_is_fitted(self):
        X, y = dataset(30)
        result = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y, seed=0)
        self.assertIn("none applied", result["calibration"]["recalibration"])


class TestStatuses(unittest.TestCase):
    def test_too_little_data_is_insufficient_and_has_no_metrics(self):
        for key, n in ((P.GROUPED_INPUT_SETS, 5), (P.LEAVE_CLUSTER_OUT, 8)):  # one row below each protocol's minimum or more
            X, y = dataset(n)
            r = runner.run_protocol(key, X, y)
            self.assertEqual(r["status"], runner.INSUFFICIENT, key)
            self.assertIsNone(r["metrics"])
            self.assertTrue(r["message"])

    def test_few_distinct_input_sets_with_many_rows_is_insufficient(self):
        X, y = dataset(5)
        X, y = np.vstack([X] * 4), np.concatenate([y] * 4)
        self.assertEqual(runner.run_protocol(P.GROUPED_INPUT_SETS, X, y)["status"], runner.INSUFFICIENT)

    def test_a_fit_failure_is_failed_not_success(self):
        X, y = dataset(24)
        with mock.patch.object(runner, "_fit_recorded", side_effect=RuntimeError("boom")):
            r = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y)
        self.assertEqual(r["status"], runner.FAILED)
        self.assertIsNone(r["metrics"])
        self.assertIsNone(r["calibration"])
        self.assertNotIn("boom", r["message"])  # the internal text is not shown
        summary = runner.summarise({P.GROUPED_INPUT_SETS: r, P.LEAVE_CLUSTER_OUT: runner.run_protocol(P.LEAVE_CLUSTER_OUT, X, y)})
        self.assertEqual(summary["state"], "failed")

    def test_non_finite_input_is_failed(self):
        X, y = dataset(24)
        y[3] = np.nan
        self.assertEqual(runner.run_protocol(P.GROUPED_INPUT_SETS, X, y)["status"], runner.FAILED)

    def test_summary_is_the_worst_required_state(self):
        ok = {"status": runner.COMPLETED}
        warn = {"status": runner.WARNINGS}
        insufficient = {"status": runner.INSUFFICIENT}
        failed = {"status": runner.FAILED}
        g, c = P.GROUPED_INPUT_SETS, P.LEAVE_CLUSTER_OUT
        self.assertEqual(runner.summarise({})["state"], "incomplete")
        self.assertEqual(runner.summarise({g: ok})["state"], "incomplete")
        self.assertEqual(runner.summarise({g: ok, c: insufficient})["state"], "incomplete")
        self.assertEqual(runner.summarise({g: ok, c: warn})["state"], "completed_with_warnings")
        self.assertEqual(runner.summarise({g: ok, c: ok})["state"], "completed")
        self.assertEqual(runner.summarise({g: warn, c: failed})["state"], "failed")
        self.assertEqual(runner.summarise({g: ok, c: ok, P.RANDOM_ROWS: failed})["state"], "completed")  # reference only

    def test_small_samples_and_random_split_replicates_carry_warnings(self):
        X, y = dataset(10)
        r = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y)
        self.assertEqual(r["status"], runner.WARNINGS)
        self.assertIn("small_sample", [w["code"] for w in r["warnings"]])
        X, y = dataset(24, replicates=True)
        r = runner.run_protocol(P.RANDOM_ROWS, X, y)
        self.assertIn("replicates_in_random_split", [w["code"] for w in r["warnings"]])


class TestReproducibility(unittest.TestCase):
    def test_same_inputs_same_seed_same_result(self):
        X, y = dataset(24)
        a = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y, seed=7)
        b = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y, seed=7)
        for key in ("metrics", "calibration", "points", "evaluation_rows_hash", "fold_assignment_hash", "counts"):
            self.assertEqual(a[key], b[key], key)

    def test_evaluation_rows_hash_ignores_row_order(self):
        X, y = dataset(24)
        a = runner.run_protocol(P.GROUPED_INPUT_SETS, X, y, seed=0)["evaluation_rows_hash"]
        order = np.random.default_rng(0).permutation(len(X))
        b = runner.run_protocol(P.GROUPED_INPUT_SETS, X[order], y[order], seed=0)["evaluation_rows_hash"]
        self.assertEqual(a, b)

    def test_clusters_are_deterministic_and_use_only_inputs(self):
        X, y = dataset(30)
        l1, c1 = runner.region_clusters(X)
        l2, c2 = runner.region_clusters(X)
        self.assertTrue(np.array_equal(l1, l2))
        self.assertGreaterEqual(c1, 3)


class TestCalibrationAndSharpness(unittest.TestCase):
    def sample(self, n=400, scale=1.0, seed=0):
        rng = np.random.default_rng(seed)
        mu = rng.normal(0, 3, n)
        y = mu + rng.normal(0, 1.0, n)
        sigma = np.full(n, 1.0 * scale)
        return y, mu, sigma

    def test_matching_intervals_show_no_clear_departure_never_calibrated(self):
        y, mu, sigma = self.sample()
        s = scoring.calibration_summary(y, mu, sigma)
        self.assertEqual(s["assessment"], "no_clear_departure")
        self.assertNotIn("calibrated", s["assessment"].replace("no_clear_departure", ""))
        self.assertIn("does not establish calibration", s["caveat"])

    def test_narrow_intervals_under_cover(self):
        y, mu, sigma = self.sample(scale=0.5)
        self.assertEqual(scoring.calibration_summary(y, mu, sigma)["assessment"], "under_covering")

    def test_wide_intervals_over_cover_and_are_not_sharp(self):
        y, mu, right = self.sample()
        s = scoring.calibration_summary(y, mu, right * 4)
        self.assertEqual(s["assessment"], "over_covering")
        right_s = scoring.calibration_summary(y, mu, right)
        self.assertGreater(s["levels"][-1]["mean_width"], 3.5 * right_s["levels"][-1]["mean_width"])
        self.assertGreater(s["mean_crps"], right_s["mean_crps"])  # coverage alone would have rewarded the wide ones

    def test_small_samples_are_not_read(self):
        y, mu, sigma = self.sample(n=12)
        s = scoring.calibration_summary(y, mu, sigma)
        self.assertEqual(s["assessment"], "insufficient_samples")
        self.assertTrue(all(r["reading"] == "insufficient_samples" for r in s["levels"]))
        self.assertEqual(s["n"], 12)

    def test_every_level_reports_count_width_and_an_uncertainty_interval_on_coverage(self):
        y, mu, sigma = self.sample()
        for row in scoring.calibration_summary(y, mu, sigma)["levels"]:
            for field in ("nominal", "n", "inside", "observed", "observed_interval_95", "mean_width"):
                self.assertIsNotNone(row[field])
            lo, hi = row["observed_interval_95"]
            self.assertLessEqual(lo, row["observed"])
            self.assertGreaterEqual(hi, row["observed"])

    def test_metric_values_match_hand_calculation(self):
        y = np.array([1.0, 2.0, 3.0, 4.0])
        mu = np.array([1.5, 2.0, 2.0, 5.0])
        m = scoring.error_metrics(y, mu, np.full(4, y.mean()))
        self.assertAlmostEqual(m["mae"], 0.625)
        self.assertAlmostEqual(m["rmse"], np.sqrt((0.25 + 0 + 1 + 1) / 4))
        self.assertAlmostEqual(m["r2"], 1 - 2.25 / 5.0)
        self.assertAlmostEqual(m["bias"], (0.5 + 0 - 1 + 1) / 4)

    def test_constant_targets_make_r2_undefined_not_zero(self):
        y = np.full(5, 2.0)
        self.assertIsNone(scoring.error_metrics(y, y + 0.1, y)["r2"])

    def test_crps_matches_the_closed_form_at_the_mean(self):
        # for a standard normal prediction at its mean, CRPS = (sqrt(2) - 1) / sqrt(pi)
        self.assertAlmostEqual(float(scoring.gaussian_crps([0.0], [0.0], [1.0])[0]), (np.sqrt(2) - 1) / np.sqrt(np.pi), places=6)


class TestApplicability(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(2)
        self.train = rng.uniform(0, 1, (30, 2))

    def test_too_few_distinct_inputs_is_not_assessed(self):
        out, limits = app.assess_points(np.array([[0.5, 0.5]]), self.train[:5])
        self.assertIsNone(limits)
        self.assertEqual(out[0]["class"], app.NOT_ASSESSED)

    def test_replicates_do_not_collapse_the_reference(self):
        doubled = np.vstack([self.train, self.train])
        self.assertTrue(np.allclose(app.reference_distances(doubled), app.reference_distances(self.train)))

    def test_classes_follow_distance(self):
        tr = self.train
        out, limits = app.assess_points(np.array([tr[0], [1.15, 1.15], [3.0, 3.0]]), tr)
        self.assertEqual(out[0]["class"], app.WITHIN)
        self.assertIn(out[1]["class"], (app.SPARSE, app.ISOLATED))
        self.assertEqual(out[2]["class"], app.ISOLATED)

    def test_the_convention_is_stated_as_not_a_guarantee(self):
        self.assertIn("not probabilities or guarantees", app.CONVENTION_NOTE)

    def test_validation_results_report_error_by_applicability_class(self):
        X, y = dataset(30)
        r = runner.run_protocol(P.LEAVE_CLUSTER_OUT, X, y)
        check = r["applicability_check"]
        self.assertIn("training rows of that fold only", check["note"])
        self.assertEqual(sum(c["n"] for c in check["by_class"]), r["counts"]["n_scored"])


class TestNearestInputSet(unittest.TestCase):
    """The nearest training input shown to a user must be a real, distinct training input set, however many times it was
    measured and in whatever order the rows arrive."""

    def brute(self, x, X, lo, hi):
        span = np.where(hi > lo, hi - lo, 1.0)
        best = None
        for r in map(tuple, X.tolist()):
            d = float(np.sqrt((((np.array(r) - x) / span) ** 2).sum()))
            if best is None or d < best[0] - 1e-15 or (abs(d - best[0]) <= 1e-15 and r < best[1]):
                best = (d, r)
        return best

    def data(self):
        base = np.array([[0.1, 100.0], [0.2, 120.0], [0.3, 160.0], [0.5, 200.0], [0.9, 150.0]])
        return np.vstack([base, base[1:3], base[1:2], base[1:2]])  # input sets 1 and 2 repeated, set 1 four times

    def test_repeats_are_one_location_and_the_count_is_reported(self):
        X = self.data()
        lo, hi = X.min(axis=0), X.max(axis=0)
        d, vec, n = app.nearest_input_set(np.array([0.2, 121.0]), X, lo, hi)
        self.assertEqual(tuple(vec), (0.2, 120.0))
        self.assertEqual(n, 4)

    def test_a_candidate_equal_to_a_training_input_has_distance_zero_and_returns_that_input(self):
        X = self.data()
        lo, hi = X.min(axis=0), X.max(axis=0)
        for r in np.unique(X, axis=0):
            d, vec, n = app.nearest_input_set(r, X, lo, hi)
            self.assertEqual(d, 0.0)
            self.assertTrue(np.array_equal(vec, r))

    def test_matches_a_brute_force_search_and_ignores_row_order(self):
        rng = np.random.default_rng(5)
        X = np.round(rng.uniform(0, 1, (12, 3)), 2)
        X = np.vstack([X, X[:5], X[:2]])
        lo, hi = X.min(axis=0), X.max(axis=0)
        for trial in range(25):
            x = rng.uniform(-0.2, 1.2, 3)
            expected = self.brute(x, X, lo, hi)
            for order in (np.arange(len(X)), rng.permutation(len(X))):
                d, vec, n = app.nearest_input_set(x, X[order], lo, hi)
                self.assertAlmostEqual(d, expected[0], places=12)
                self.assertEqual(tuple(vec), expected[1])
                self.assertTrue(any(np.array_equal(vec, r) for r in X))  # a real training input, not a rounded or rescaled copy

    def test_a_constant_feature_does_not_break_the_distance(self):
        X = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0], [3.0, 5.0]])
        lo, hi = X.min(axis=0), X.max(axis=0)
        d, vec, n = app.nearest_input_set(np.array([2.1, 5.0]), X, lo, hi)
        self.assertEqual((tuple(vec), n), ((2.0, 5.0), 1))

    def test_ties_resolve_the_same_way_whatever_the_order(self):
        X = np.array([[0.0, 0.0], [2.0, 0.0], [1.0, 5.0]])
        lo, hi = X.min(axis=0), X.max(axis=0)
        a = app.nearest_input_set(np.array([1.0, 0.0]), X, lo, hi)[1]
        b = app.nearest_input_set(np.array([1.0, 0.0]), X[::-1], lo, hi)[1]
        self.assertTrue(np.array_equal(a, b))

    def test_reference_spacing_does_not_change_with_the_number_of_repeats(self):
        X = self.data()
        once = np.unique(X, axis=0)
        lo, hi = X.min(axis=0), X.max(axis=0)
        from engine.preprocessing.normalization import normalize
        a = app.reference_distances(normalize(X, lo, hi))
        b = app.reference_distances(normalize(once, lo, hi))
        self.assertTrue(a is None and b is None or np.allclose(a, b))


class TestSharedCandidateAssessment(unittest.TestCase):
    """Candidate decision support and the validation runner use one distance, one scaling and one set of thresholds."""

    def test_assess_candidate_agrees_with_assess_points_and_nearest_input_set(self):
        from engine.preprocessing.normalization import normalize
        rng = np.random.default_rng(4)
        X = np.round(rng.uniform(0, 1, (20, 3)), 3)
        X = np.vstack([X, X[:4]])
        ref = app.build_reference(X)
        self.assertEqual(ref["n_input_sets"], 20)
        for x in rng.uniform(-0.3, 1.3, (15, 3)):
            found = app.assess_candidate(x, X, ref)
            pts, limits = app.assess_points(normalize(x.reshape(1, -1), ref["lo"], ref["hi"]), normalize(X, ref["lo"], ref["hi"]))
            self.assertEqual(found["class"], pts[0]["class"])
            self.assertAlmostEqual(found["nearest_distance"], pts[0]["nearest_distance"], places=12)
            self.assertEqual(ref["limits"], limits)

    def test_too_few_distinct_input_sets_gives_no_thresholds_and_not_assessed(self):
        X = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 0.5]] * 3)
        ref = app.build_reference(X)
        self.assertIsNone(ref["limits"])
        self.assertEqual(app.assess_candidate(np.array([0.5, 0.5]), X, ref)["class"], app.NOT_ASSESSED)


class TestUncertaintyTypes(unittest.TestCase):
    def test_fitted_noise_is_in_target_units_and_not_called_measurement_uncertainty(self):
        from engine.validation import uncertainty as u
        rng = np.random.default_rng(0)
        X = rng.uniform(0, 1, (40, 2))
        y = 3 * X[:, 0] + rng.normal(0, 0.2, 40)
        fit = u.fitted_noise_summary(X, y)
        self.assertEqual(fit["status"], "ok")
        self.assertGreater(fit["fitted_noise_sd"], 0.05)
        self.assertLess(fit["fitted_noise_sd"], 0.5)  # near the 0.2 that was put in, far below the spread of y
        self.assertIn("not a measurement uncertainty", fit["note"])

    def test_four_types_are_defined_separately(self):
        from engine.validation import uncertainty as u
        self.assertEqual(set(u.TYPES), {"model", "measurement", "data", "applicability"})

    def test_replicate_scatter_is_never_labelled_measurement_uncertainty(self):
        from engine.validation import uncertainty as u
        out = u.replicate_scatter([1, 1.2, 3, 3.1], [0, 0, 1, 1])
        self.assertEqual(out["status"], "observed")
        self.assertIn("not a measurement uncertainty", out["note"])
        self.assertEqual(u.replicate_scatter([1, 2], [0, 1])["status"], "no_repeated_input_sets")


if __name__ == "__main__":
    unittest.main()
