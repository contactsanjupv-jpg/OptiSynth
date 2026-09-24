"""
Real, executable test for the .docx report builder -- python-docx is
available in the sandbox and report_builder.py has no DB/web-framework
dependency (see its module docstring).
Run with: python3 -m unittest backend.tests.test_report_builder -v
"""
import os
import unittest

from docx import Document

from backend.app.services.report_builder import build_project_report


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


if __name__ == "__main__":
    unittest.main()
