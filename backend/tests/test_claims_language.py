"""Regression guard for unsupported scientific claims (demo script, project report, project UI labels).
Run:  python3 -m unittest backend.tests.test_claims_language -v
Reads tracked files only; changes nothing."""
import os
import re
import unittest

from docx import Document

from backend.app.services.report_builder import build_project_report

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class TestDemoScriptClaims(unittest.TestCase):
    BANNED = [
        r"calibrated model", r"knows what it doesn'?t know", r"not worth committing",
        r"strongest bet", r"defensible to show an auditor", r"guaranteed? (to )?(pass|meet|work)",
    ]

    def test_banned_phrases_absent_from_demo_and_readme(self):
        for name in ("DEMO.md", "README.md"):
            text = _read(name)
            for pattern in self.BANNED:
                self.assertIsNone(re.search(pattern, text, re.I), f"{name}: unsupported claim /{pattern}/")

    def test_demo_states_the_limits(self):
        text = _read("DEMO.md")
        self.assertIn("not a proven error bound", text)
        self.assertIn("does not rule anything out", text)
        self.assertRegex(text, r"Never describe the percentages as validated or calibrated")
        self.assertIn("physical test result is recorded", text)


class TestProjectReportWording(unittest.TestCase):
    def _build(self, recs):
        project = {"name": "P", "target_metric": "y", "direction": "maximize", "target_value": 20.0}
        backtest = {"actual_order_evals": 33, "engine_order_evals": 12, "reduction_pct": 63.6}
        path = build_project_report(project, backtest, recs, 60, "Acme")
        doc = Document(path)
        os.remove(path)
        cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
        return cells, "\n".join(p.text for p in doc.paragraphs)

    def test_probability_column_is_not_called_confidence(self):
        rec = {"predicted_value": 21.4, "feasibility_probability": 0.91, "features": {"a": 1.0}}
        cells, text = self._build([rec])
        self.assertIn("Model-estimated probability", cells)
        self.assertNotIn("Confidence", cells)
        self.assertIn("not a measured or validated success rate", text)

    def test_no_recommendations_and_boundary_probabilities(self):
        cells, text = self._build([])
        self.assertIn("No recommendations generated yet.", text)
        for p in (0.0, 1.0):
            rec = {"predicted_value": 1.0, "feasibility_probability": p, "features": {"a": 1.0}}
            cells, _ = self._build([rec])
            self.assertIn("Model-estimated probability", cells)


class TestProjectUiLabels(unittest.TestCase):
    def test_candidates_page_label(self):
        text = _read("frontend", "app", "projects", "[projectId]", "candidates", "page.tsx")
        self.assertIn('header: "Model-estimated probability"', text)
        self.assertNotIn('header: "Confidence"', text)

    def test_model_page_labels(self):
        text = _read("frontend", "app", "projects", "[projectId]", "model", "page.tsx")
        self.assertNotIn('"Prediction Accuracy"', text)
        self.assertIn("Within tolerance band", text)
        self.assertIn("not an acceptance threshold", text)
        self.assertIn("random-row cross-validation", text)


if __name__ == "__main__":
    unittest.main()
