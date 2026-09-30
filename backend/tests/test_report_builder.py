"""
Real, executable test for the .docx report builder -- python-docx is
available in the sandbox and report_builder.py has no DB/web-framework
dependency (see its module docstring).
Run with: python3 -m unittest backend.tests.test_report_builder -v
"""
import os
import unittest

from docx import Document

from backend.app.services.report_builder import build_project_report, build_qualification_report


class TestReportBuilder(unittest.TestCase):
    def test_builds_a_real_readable_docx(self):
        project = {
            "name": "Adhesive Optimization", "target_metric": "bond_strength_MPa",
            "direction": "maximize", "target_value": 20.0,
        }
        backtest = {
            "actual_order_evals": 33, "engine_order_evals": 12,
            "reduction_pct": 63.6, "n_historical_rows": 60,
        }
        recommendations = [{
            "predicted_value": 21.4, "feasibility_probability": 0.91,
            "features": {"cure_temp_C": 137.0, "cure_time_min": 38.0},
        }]

        path = build_project_report(project, backtest, recommendations, 60, "Acme Coatings")
        self.assertTrue(os.path.exists(path))

        doc = Document(path)
        full_text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("Adhesive Optimization", full_text)
        self.assertIn("Acme Coatings", full_text)
        os.remove(path)

    def test_handles_backtest_error_gracefully(self):
        project = {"name": "P", "target_metric": "y", "direction": "maximize", "target_value": None}
        backtest = {"error": "Not enough data."}
        path = build_project_report(project, backtest, [], 2, "Acme")
        self.assertTrue(os.path.exists(path))
        doc = Document(path)
        full_text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("Not enough data.", full_text)
        os.remove(path)


class TestQualificationReportModelEvidence(unittest.TestCase):
    """Priority 7B: model_quality/uncertainty_calibration must be surfaced
    honestly, never claimed unconditionally. The 'metrics unavailable'
    branch (n < 6 historical rows) can't be reached through the live API
    -- ranking itself requires >= 8 distinct rows (MIN_HISTORICAL_ROWS_
    FOR_PREDICTION), above compute_model_metrics'/compute_uncertainty_
    calibration's own n < 6 threshold -- so it's tested directly here,
    the same way test_handles_backtest_error_gracefully above already
    tests build_project_report's own error branch directly."""

    def _case(self):
        return {"name": "PFHxA replacement", "trigger_type": "regulatory_restriction",
                "restricted_substance": "PFHxA"}

    def _docx_text(self, path):
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs)
        os.remove(path)
        return text

    def test_states_actual_metrics_when_available(self):
        ranked_results = [{
            "candidate_name": "Resin A", "predicted_probability": 0.8, "uncertainty_std": 0.05,
            "recommended_experiment": "Run a test.", "domain_coverage": None,
        }]
        model_quality = {
            "r2_score": 0.91, "mean_absolute_error": 1.2, "prediction_accuracy_pct": 86.7,
            "accuracy_tolerance_band": 2.5, "n_folds_used": 5, "n_points_scored": 15,
        }
        uncertainty_calibration = {
            "sigma_error_correlation": 0.42, "within_1sigma_pct": 71.4,
            "within_2sigma_pct": 92.9, "n_points_scored": 14,
        }
        path = build_qualification_report(
            self._case(), ranked_results, 15, "Acme Coatings",
            data_quality={"status": "valid", "dataset_id": 1, "row_count": 15,
                          "distinct_rows": 15, "duplicate_rows": 0, "constant_columns": []},
            model_quality=model_quality, uncertainty_calibration=uncertainty_calibration,
        )
        text = self._docx_text(path)
        self.assertIn("Model performance evidence", text)
        self.assertIn("R\u00b2 = 0.91", text)
        self.assertIn("mean absolute error = 1.2", text)
        self.assertIn("= 0.42", text)
        self.assertIn("71.4% of held-out points fell within 1", text)
        self.assertNotIn("with calibrated uncertainty -- not a heuristic score", text)

    def test_states_unavailable_when_metrics_have_errors(self):
        ranked_results = [{
            "candidate_name": "Resin A", "predicted_probability": 0.8, "uncertainty_std": 0.05,
            "recommended_experiment": "Run a test.", "domain_coverage": None,
        }]
        model_quality = {"error": "Need at least 6 historical rows to cross-validate model quality."}
        path = build_qualification_report(
            self._case(), ranked_results, 5, "Acme Coatings",
            data_quality={"status": "valid", "dataset_id": 1, "row_count": 5,
                          "distinct_rows": 5, "duplicate_rows": 0, "constant_columns": []},
            model_quality=model_quality, uncertainty_calibration=None,
        )
        text = self._docx_text(path)
        self.assertIn("Model performance evidence", text)
        self.assertIn("could not be computed", text)
        self.assertIn(model_quality["error"], text)
        # must never assert a calibration claim it has no evidence for
        self.assertNotIn("with calibrated uncertainty -- not a heuristic score", text)
        self.assertNotIn("R\u00b2 =", text)

    def test_no_model_evidence_section_when_metrics_not_provided(self):
        # Backward-compatible default: omitting model_quality entirely
        # (e.g. the insufficient-evidence report path) must not render an
        # empty or misleading evidence section.
        ranked_results = []
        path = build_qualification_report(self._case(), ranked_results, 6, "Acme Coatings",
                                           data_quality={"status": "insufficient", "dataset_id": 1,
                                                          "row_count": 6, "distinct_rows": 6,
                                                          "duplicate_rows": 0, "constant_columns": []})
        text = self._docx_text(path)
        self.assertNotIn("Model performance evidence", text)
        self.assertIn("Insufficient evidence to establish predictive performance.", text)


if __name__ == "__main__":
    unittest.main()

class TestQualificationReportDecisionSupport(unittest.TestCase):
    """C1: 'Model-estimated probability' wording and the 'Decision support'
    column/explanation. Table cells are read too (the 7B helper above only
    reads paragraphs)."""

    def _case(self):
        return {"name": "PFHxA replacement", "trigger_type": "regulatory_restriction",
                "restricted_substance": "PFHxA"}

    def _text(self, path):
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        os.remove(path)
        return "\n".join(parts)

    def _dq(self):
        return {"status": "valid", "dataset_id": 1, "row_count": 15,
                "distinct_rows": 15, "duplicate_rows": 0, "constant_columns": []}

    def _outside_result(self):
        from backend.app.services import change_case_rules as rules
        cov = rules.classify_candidate_domain_coverage({"viscosity": 500}, {"viscosity": (450, 464)})
        return {
            "candidate_name": "Far", "predicted_probability": 1.0, "uncertainty_std": 2.261,
            "recommended_experiment": "Run a test.", "domain_coverage": cov,
            "decision_support": rules.derive_decision_support(cov),
        }

    def test_outside_candidate_shows_model_estimate_and_requires_validation(self):
        path = build_qualification_report(self._case(), [self._outside_result()], 15, "Acme",
                                          data_quality=self._dq())
        text = self._text(path)
        self.assertIn("Model-estimated probability", text)
        self.assertIn("Decision support", text)
        self.assertIn("Requires validation", text)
        self.assertIn("Outside historical range", text)
        self.assertIn("100%", text)  # raw probability shown unchanged
        self.assertIn("2.261", text)
        self.assertIn("not a validated qualification probability", text)
        self.assertIn("'Evidence-supported' does not mean qualified", text)

    def test_old_wording_removed(self):
        path = build_qualification_report(self._case(), [self._outside_result()], 15, "Acme",
                                          data_quality=self._dq())
        text = self._text(path)
        self.assertNotIn("Predicted qualification probability", text)
        self.assertNotIn("confidence-scored", text)
        self.assertIn("ranked shortlist ordered by model-estimated probability", text)

    def test_results_without_decision_support_omit_the_column(self):
        r = self._outside_result()
        r["decision_support"] = None
        path = build_qualification_report(self._case(), [r], 15, "Acme", data_quality=self._dq())
        text = self._text(path)
        self.assertNotIn("Decision support", text)
        self.assertIn("Model-estimated probability", text)