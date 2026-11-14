"""Level 2 review: the report's model-fitting note must appear whenever ANY fit counter is non-zero,
including noise-at-its-floor on its own. Run:  python3 -m unittest backend.tests.test_fit_note_reporting -v"""
import unittest

from backend.tests.test_grouped_validation_wiring import BASE, CAL, _report_text

ZERO = {"n_fits": 5, "n_convergence_warnings": 0, "n_fits_with_hyperparameter_at_bound": 0, "n_fits_noise_at_lower_bound": 0}


class TestFitNote(unittest.TestCase):
    def _text(self, **counters):
        return _report_text({**BASE, "fit_diagnostics": {**ZERO, **counters}}, CAL)

    def test_noise_at_its_minimum_alone_is_reported(self):
        text = self._text(n_fits_noise_at_lower_bound=2)
        self.assertIn("Model-fitting note: across 5 cross-validation fits", text)
        self.assertIn("the noise estimate reached its minimum in 2", text)
        self.assertIn("convergence warning in 0", text)

    def test_each_other_counter_alone_is_reported(self):
        self.assertIn("convergence warning in 3", self._text(n_convergence_warnings=3))
        self.assertIn("allowed limit in 1", self._text(n_fits_with_hyperparameter_at_bound=1))

    def test_all_zero_missing_and_empty_diagnostics_stay_silent(self):
        for diag in (ZERO, {}, None):
            self.assertNotIn("Model-fitting note", _report_text({**BASE, "fit_diagnostics": diag}, CAL))
        self.assertNotIn("Model-fitting note", _report_text(BASE, CAL))

    def test_note_without_the_optional_noise_key_still_works(self):
        diag = {"n_fits": 4, "n_convergence_warnings": 1, "n_fits_with_hyperparameter_at_bound": 1}
        self.assertIn("minimum in 0", _report_text({**BASE, "fit_diagnostics": diag}, CAL))


if __name__ == "__main__":
    unittest.main()
