"""
How the report and the API present the cross-validated model metrics (Fix 2B).

  * The 10%-of-target-range accuracy band is described as a HEURISTIC, never as an engineering acceptance
    threshold.
  * A rank correlation that is undefined (constant predicted uncertainty or error) is reported as "not
    defined", not as a correlation of 0.0.
  * Existing result keys are unchanged, and older metric dicts (without the new keys) still render.

Run:  python3 -m unittest backend.tests.test_report_model_metrics_wording -v
"""
import io
import os
import unittest

from docx import Document

from backend.app.services.report_builder import build_qualification_report
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

QUALITY = {"r2_score": 0.91, "mean_absolute_error": 1.2, "prediction_accuracy_pct": 86.7,
           "accuracy_tolerance_band": 2.5, "n_folds_used": 5, "n_points_scored": 15}


def _report_text(model_quality, uncertainty_calibration):
    ranked = [{"candidate_name": "Resin A", "predicted_probability": 0.8, "uncertainty_std": 0.05,
               "recommended_experiment": "Run a test.", "domain_coverage": None}]
    path = build_qualification_report(
        {"name": "PFHxA replacement", "trigger_type": "regulatory_restriction", "restricted_substance": "PFHxA"},
        ranked, 15, "Acme Coatings",
        data_quality={"status": "valid", "dataset_id": 1, "row_count": 15, "distinct_rows": 15,
                      "duplicate_rows": 0, "constant_columns": []},
        model_quality=model_quality, uncertainty_calibration=uncertainty_calibration,
    )
    text = "\n".join(p.text for p in Document(path).paragraphs)
    os.remove(path)
    return text


class TestReportWording(unittest.TestCase):
    def test_tolerance_band_is_labelled_a_heuristic_not_an_acceptance_threshold(self):
        text = _report_text(QUALITY, None)
        self.assertIn("heuristic tolerance band of 10% of this dataset's observed target range", text)
        self.assertIn("not an engineering acceptance threshold", text)

    def test_undefined_correlation_is_not_presented_as_zero(self):
        calibration = {"sigma_error_correlation": 0.0, "sigma_error_correlation_defined": False,
                       "within_1sigma_pct": 70.0, "within_2sigma_pct": 95.0, "n_points_scored": 14}
        text = _report_text(QUALITY, calibration)
        self.assertIn("not defined", text)
        self.assertNotIn("held-out error = 0.0", text)
        self.assertIn("70.0% of held-out points fell within 1", text)

    def test_a_defined_correlation_and_older_dicts_without_the_flag_still_print_the_number(self):
        older = {"sigma_error_correlation": 0.42, "within_1sigma_pct": 71.4, "within_2sigma_pct": 92.9,
                 "n_points_scored": 14}
        self.assertIn("held-out error = 0.42 (positive means", _report_text(QUALITY, older))
        flagged = dict(older, sigma_error_correlation_defined=True)
        self.assertIn("held-out error = 0.42 (positive means", _report_text(QUALITY, flagged))


class TestApiAndReportCarryTheMetrics(IsolatedApiTestCase):
    dir_name = "_test_env_metric_wording"

    def test_rank_response_keeps_existing_keys_adds_the_new_ones_and_the_report_says_heuristic(self):
        org_id, client, case_id = self.setup_case("metrics")
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        r = client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "InA", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 158}})
        self.assertEqual(r.status_code, 201, r.text)
        rank = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(rank.status_code, 200, rank.text)
        result = rank.json()[0]
        quality, calibration = result["model_quality"], result["uncertainty_calibration"]
        for key in ("r2_score", "mean_absolute_error", "prediction_accuracy_pct", "accuracy_tolerance_band",
                    "n_folds_used", "n_points_scored"):
            self.assertIn(key, quality)
        self.assertIn("Heuristic", quality["accuracy_tolerance_note"])
        self.assertIn("not an engineering acceptance threshold", quality["accuracy_tolerance_note"])
        for key in ("sigma_error_correlation", "within_1sigma_pct", "within_2sigma_pct", "n_points_scored"):
            self.assertIn(key, calibration)
        self.assertIsInstance(calibration["sigma_error_correlation_defined"], bool)
        report = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(report.status_code, 200, report.text[:200])
        text = "\n".join(p.text for p in Document(io.BytesIO(report.content)).paragraphs)
        self.assertIn("heuristic tolerance band", text)
        self.assertIn("not an engineering acceptance threshold", text)


if __name__ == "__main__":
    unittest.main()
