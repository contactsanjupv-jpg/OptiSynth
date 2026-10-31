"""
How the model-estimated probability is DISPLAYED (the stored/API number is never changed).

The number is P(prediction meets the requirement) under the model's own Gaussian predictive distribution
(engine/optimization/rank_candidates._probability_meets_target). Whole-percent rounding printed any value of
99.5% or more as "100%" -- which read as certainty beside candidates that measured far below the requirement. These
tests pin: no figure is ever shown as 100% or 0%; extremes are shown as a bound; the raw value is untouched; the
report, the recommendation text and the frontend all use the same rule; the explanatory note is present.

Run:  python3 -m unittest backend.tests.test_probability_display -v
"""
import json
import math
import os
import pathlib
import re
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.services import change_case_rules as rules  # noqa: E402
from backend.app.services import report_builder  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import IsolatedApiTestCase  # noqa: E402

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"
GE, LE = "\u226599%", "\u22641%"


class TestFormatter(unittest.TestCase):
    def test_extremes_are_bounds_and_never_100_or_0(self):
        for p in (1.0, 0.9999999, 0.9951, 0.995, 0.99):
            self.assertEqual(rules.format_model_probability(p), GE, p)
        for p in (0.0, 1e-9, 0.004, 0.01):
            self.assertEqual(rules.format_model_probability(p), LE, p)

    def test_no_value_in_the_range_is_ever_displayed_as_100_or_0(self):
        for i in range(0, 100001):
            text = rules.format_model_probability(i / 100000)
            self.assertNotIn(text, ("100%", "0%"), i)

    def test_ordinary_values_are_whole_percent_as_before(self):
        for p, text in ((0.5, "50%"), (0.0123, "1%"), (0.85, "85%"), (0.9899, "99%"), (0.0101, "1%"), (0.3, "30%")):
            self.assertEqual(rules.format_model_probability(p), text, p)

    def test_missing_or_invalid_values_are_not_shown_as_a_percentage(self):
        for bad in (None, float("nan"), float("inf"), float("-inf"), 1.0000001, 1.5, 100, -0.1, -1, "0.9", True, False, [], {}):
            self.assertEqual(rules.format_model_probability(bad), "not available", repr(bad))
        for good in (0, 1, 0.0, 1.0):  # the closed interval [0, 1] is valid and still shown as a bound
            self.assertIn(rules.format_model_probability(good), (GE, LE), good)

    def test_display_never_changes_the_value_it_is_given(self):
        p = 0.9999
        rules.format_model_probability(p)
        self.assertEqual(p, 0.9999)

    def test_note_states_what_the_number_is_and_is_not(self):
        note = rules.PROBABILITY_DISPLAY_NOTE
        for phrase in ("model's own normal-distribution assumption", "not a validated error bound", GE, LE,
                       "never 100% or 0%", "no figure here is a guarantee or a measured result"):
            self.assertIn(phrase, note)
        self.assertIn(note, report_builder.MODEL_ESTIMATE_NOTE)
        self.assertIn("not a validated qualification probability", report_builder.MODEL_ESTIMATE_NOTE)


class TestRecommendationWording(unittest.TestCase):
    """The recommendation text must never tell a customer to skip physical validation because of a model estimate."""

    def _text(self, probability, **kw):
        from backend.app.services import change_case_service
        candidate = {"name": "Cand", "predicted_probability": probability}
        return change_case_service._minimum_experiment_description(candidate, 35.0, "maximize", **kw)

    def test_a_low_estimate_is_described_as_fallible_in_both_directions(self):
        text = self._text(0.12)
        self.assertIn("low model-estimated probability", text)
        self.assertIn("estimates can be wrong in either direction", text)
        self.assertIn("only physical validation can show how it performs", text)
        self.assertNotIn("not worth", text)
        self.assertNotIn("likely not worth physical validation", text)
        self.assertIn("a model output rather than a measured or validated result", text)

    def test_no_estimate_tier_discourages_validation(self):
        for p in (0.0, 0.12, 0.39, 0.4, 0.6, 0.79, 0.8, 0.95, 1.0):
            text = self._text(p)
            self.assertNotIn("not worth", text, p)
            self.assertIn("does not replace running and recording the actual physical result", text, p)
        self.assertIn("high model-estimated probability", self._text(0.95))
        self.assertIn("moderate model-estimated probability", self._text(0.6))


class TestFrontendMatchesBackend(unittest.TestCase):
    def setUp(self):
        if not FRONTEND.is_dir():
            self.skipTest("frontend sources are not part of this checkout")

    def test_thresholds_are_the_same_in_both_languages(self):
        ts = (FRONTEND / "lib/utils/format.ts").read_text(encoding="utf-8")
        self.assertEqual(float(re.search(r"PROBABILITY_DISPLAY_CEILING = ([0-9.]+);", ts).group(1)),
                         rules.PROBABILITY_DISPLAY_CEILING)
        self.assertEqual(float(re.search(r"PROBABILITY_DISPLAY_FLOOR = ([0-9.]+);", ts).group(1)),
                         rules.PROBABILITY_DISPLAY_FLOOR)
        self.assertIn("\\u226599%", ts)
        self.assertIn("\\u22641%", ts)

    def test_the_page_uses_the_formatter_and_carries_the_same_note(self):
        page = (FRONTEND / "app/change-cases/[id]/page.tsx").read_text(encoding="utf-8")
        self.assertIn("formatModelProbability(c.latest_prediction.predicted_probability)", page)
        self.assertNotIn("Math.round(c.latest_prediction.predicted_probability", page)
        self.assertIn(rules.PROBABILITY_DISPLAY_NOTE, page)


class TestThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_probability_display"
    HISTORY = F.base_rows(14)
    IN_RANGE = {"crosslinker_ratio": 0.12, "cure_temp_c": 158}

    def _ranked(self):
        org_id, client, case_id = self.setup_case("Prob")
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, self.HISTORY)).status_code, 201)
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "Sure", "features": self.IN_RANGE})
        self.assertEqual(r.status_code, 201, r.text)
        rk = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(rk.status_code, 200, rk.text)
        return client, case_id, {x["candidate_name"]: x for x in rk.json()}

    def test_raw_value_is_untouched_and_the_report_and_recommendation_never_say_100_percent(self):
        client, case_id, ranked = self._ranked()
        p = ranked["Sure"]["predicted_probability"]
        self.assertGreaterEqual(p, 0.99, "fixture must produce a near-certain model estimate, otherwise this test is empty")
        self.assertIsInstance(p, float)                      # the API still carries the exact model output
        listing = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertEqual(listing[0]["latest_prediction"]["predicted_probability"], p)
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn(GE, text)
        self.assertNotIn("100%", text.replace("never 100% or 0%", ""))
        self.assertNotIn("model estimate 100%", text)
        self.assertIn("model-estimated probability", text)
        self.assertIn("a model output rather than a measured or validated result", text)
        self.assertIn(rules.PROBABILITY_DISPLAY_NOTE, text)
        rec = ranked["Sure"]["recommended_experiment"]
        self.assertNotRegex(rec, r"\b100%")
        self.assertIn(GE, rec)


if __name__ == "__main__":
    unittest.main()
