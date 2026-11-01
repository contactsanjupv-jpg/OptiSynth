"""Level 2: the service groups replicate rows and the report says which validation scheme was used.
Run:  python3 -m unittest backend.tests.test_grouped_validation_wiring -v"""
import json
import os
import unittest
from unittest import mock

from docx import Document

from backend.app.services import change_case_service as svc
from backend.app.services import report_builder as rb

FEATURES = ["a", "b"]


def _experiments(rows):
    return [{"features_json": json.dumps({"a": a, "b": b}), "target_value": t} for a, b, t in rows]


class TestReplicateGroups(unittest.TestCase):
    def test_identical_inputs_share_a_label_regardless_of_target(self):
        rows = [{"features": {"a": 1.0, "b": 2.0}, "target_value": 5},
                {"features": {"a": 3.0, "b": 2.0}, "target_value": 6},
                {"features": {"a": 1.0, "b": 2.0}, "target_value": 7}]
        self.assertEqual(svc._replicate_groups(rows, FEATURES), [0, 1, 0])

    def test_nearly_identical_inputs_are_not_grouped(self):
        rows = [{"features": {"a": 1.0, "b": 2.0}, "target_value": 5},
                {"features": {"a": 1.0000001, "b": 2.0}, "target_value": 5}]
        self.assertEqual(svc._replicate_groups(rows, FEATURES), [0, 1])

    def test_empty(self):
        self.assertEqual(svc._replicate_groups([], FEATURES), [])


class TestServicePassesGroups(unittest.TestCase):
    SPEC = {"feature_columns": FEATURES}

    def test_groups_reach_both_engine_functions(self):
        rows = [(i % 4, 1.0, 10.0 + i) for i in range(8)]  # 4 input sets x 2 replicates
        with mock.patch("engine.facade.compute_model_metrics", return_value={"r2_score": 0}) as m, \
             mock.patch("engine.facade.compute_uncertainty_calibration", return_value={}) as c:
            svc._compute_evidence_metrics(self.SPEC, _experiments(rows))
        self.assertEqual(m.call_args.kwargs["groups"], [0, 1, 2, 3, 0, 1, 2, 3])
        self.assertEqual(c.call_args.kwargs["groups"], [0, 1, 2, 3, 0, 1, 2, 3])

    def test_exact_duplicates_are_removed_before_grouping(self):
        rows = [(1.0, 1.0, 5.0), (1.0, 1.0, 5.0), (2.0, 1.0, 6.0)]
        with mock.patch("engine.facade.compute_model_metrics", return_value={"r2_score": 0}) as m, \
             mock.patch("engine.facade.compute_uncertainty_calibration", return_value={}):
            svc._compute_evidence_metrics(self.SPEC, _experiments(rows))
        self.assertEqual(m.call_args.kwargs["groups"], [0, 1])

    def test_real_engine_grouped_and_too_few_input_sets_is_explained(self):
        enough = [(i % 8 / 8.0, (i % 8) ** 2 / 64.0, 10.0 + (i % 8) + 0.1 * (i // 8)) for i in range(24)]
        mq, cal = svc._compute_evidence_metrics(self.SPEC, _experiments(enough))
        self.assertEqual(mq["cv_scheme"], "grouped")
        self.assertEqual(mq["n_groups"], 8)
        few = [(i % 4 / 4.0, 0.5, 10.0 + (i % 4) + 0.1 * (i // 4)) for i in range(12)]  # 12 rows, 4 input sets
        mq, cal = svc._compute_evidence_metrics(self.SPEC, _experiments(few))
        self.assertIn("distinct input sets", mq["error"])
        self.assertIsNone(cal)


def _report_text(model_quality, calibration):
    ranked = [{"candidate_name": "Resin A", "predicted_probability": 0.8, "uncertainty_std": 0.05,
               "recommended_experiment": "Run a test.", "domain_coverage": None}]
    path = rb.build_qualification_report(
        {"name": "Case", "trigger_type": "regulatory_restriction", "restricted_substance": "X"},
        ranked, 15, "Acme",
        data_quality={"status": "valid", "dataset_id": 1, "row_count": 15, "distinct_rows": 15,
                      "duplicate_rows": 0, "constant_columns": []},
        model_quality=model_quality, uncertainty_calibration=calibration)
    text = "\n".join(p.text for p in Document(path).paragraphs)
    os.remove(path)
    return text


BASE = {"r2_score": 0.5, "mean_absolute_error": 1.0, "prediction_accuracy_pct": 70.0,
        "accuracy_tolerance_band": 2.0, "n_folds_used": 5, "n_points_scored": 15}
CAL = {"sigma_error_correlation": 0.3, "within_1sigma_pct": 60.0, "within_2sigma_pct": 92.0, "n_points_scored": 15}
DIAG = {"n_fits": 5, "n_convergence_warnings": 2, "n_fits_with_hyperparameter_at_bound": 3,
        "n_fits_noise_at_lower_bound": 1}


class TestReportWording(unittest.TestCase):
    def test_grouped_scheme_uses_grouped_caveat_only(self):
        text = _report_text({**BASE, "cv_scheme": "grouped", "n_groups": 8}, CAL)
        self.assertIn(rb.GROUPED_CROSS_VALIDATION_CAVEAT, text)
        self.assertNotIn(rb.CROSS_VALIDATION_CAVEAT, text)

    def test_random_or_missing_scheme_keeps_the_original_caveat(self):
        for q in (BASE, {**BASE, "cv_scheme": "random_rows"}):
            text = _report_text(q, CAL)
            self.assertIn(rb.CROSS_VALIDATION_CAVEAT, text)
            self.assertNotIn(rb.GROUPED_CROSS_VALIDATION_CAVEAT, text)

    def test_calibration_figures_come_with_the_reference_values(self):
        self.assertIn(rb.CALIBRATION_REFERENCE_NOTE, _report_text(BASE, CAL))
        self.assertIn("about 68% and 95%", rb.CALIBRATION_REFERENCE_NOTE)

    def test_fit_note_only_when_the_optimiser_reported_problems(self):
        shown = _report_text({**BASE, "fit_diagnostics": DIAG}, CAL)
        self.assertIn("Model-fitting note: across 5 cross-validation fits", shown)
        self.assertIn("convergence warning in 2", shown)
        for clean in ({"n_fits": 5, "n_convergence_warnings": 0, "n_fits_with_hyperparameter_at_bound": 0,
                       "n_fits_noise_at_lower_bound": 0}, None):
            self.assertNotIn("Model-fitting note", _report_text({**BASE, "fit_diagnostics": clean}, CAL))

    def test_error_branch_unchanged(self):
        text = _report_text({"error": "Need at least 6 distinct input sets to cross-validate (found 4)."}, None)
        self.assertIn("could not be computed", text)


if __name__ == "__main__":
    unittest.main()
