"""
Level 1 -- the qualification specification is validated on the SERVER, for direct API calls too.

Run:  python3 -m unittest backend.tests.test_qualification_spec_validation -v
"""
import json
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.schemas.change_case_schemas import QualificationSpec, validate_qualification_spec  # noqa: E402
from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.tests._api_base import IsolatedApiTestCase  # noqa: E402

GOOD = {"feature_columns": ["crosslinker_ratio", "cure_temp_c"], "target_metric": "salt_spray_hours",
        "target_value": 300.0, "direction": "maximize",
        # every modelled field and the target needs an explicit unit when a diagnostic is created
        "units": {"crosslinker_ratio": "fraction", "cure_temp_c": "degC", "salt_spray_hours": "h"}}


def problem(spec):
    with_error = None
    try:
        validate_qualification_spec(spec)
    except ValidationError as e:
        with_error = e
    return with_error


class TestSpecSchema(unittest.TestCase):
    def test_valid_specs_pass_and_are_returned_clean(self):
        self.assertEqual(validate_qualification_spec(GOOD), GOOD)
        with_units = {**GOOD, "units": {"salt_spray_hours": "h", "cure_temp_c": "degC"}}
        self.assertEqual(validate_qualification_spec(with_units), with_units)
        self.assertEqual(validate_qualification_spec({**GOOD, "direction": "minimize", "target_value": 5})["target_value"], 5.0)
        self.assertNotIn("units", validate_qualification_spec({**GOOD, "units": None}))  # absent, as before

    def test_returned_spec_round_trips_through_the_schema(self):
        QualificationSpec.model_validate(validate_qualification_spec({**GOOD, "units": {"salt_spray_hours": "h"}}))

    def test_required_fields(self):
        for key in GOOD:
            if key == "units":
                continue  # optional for the shape check; creation requires them (test_units_at_creation.py)
            spec = {k: v for k, v in GOOD.items() if k != key}
            err = problem(spec)
            self.assertIsNotNone(err, key)
            self.assertIn(f"'{key}' is required", err.message)
            self.assertEqual(err.field, "qualification_spec")
        self.assertIsNotNone(problem({}))
        self.assertIsNotNone(problem({"target_value": 80.0}))  # the old "no spec yet" shape

    def test_not_an_object(self):
        for bad in (None, [], "text", 5):
            self.assertIsNotNone(problem(bad), bad)

    def test_target_value_must_be_a_finite_number(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "300", "abc", None, True, False, [1], {"v": 1}):
            self.assertIsNotNone(problem({**GOOD, "target_value": bad}), repr(bad))
        for ok in (0, -4.5, 1e6, 300):
            self.assertIsNone(problem({**GOOD, "target_value": ok}), ok)

    def test_direction_must_be_maximize_or_minimize(self):
        for bad in ("up", "MAXIMIZE", "", None, 1, "max"):
            self.assertIsNotNone(problem({**GOOD, "direction": bad}), repr(bad))

    def test_feature_columns_rules(self):
        for bad in ([], "a", None, [1], [""], ["  "], ["a", "a"], ["a", "A"], ["a "], [" a"], ["a\nb"], ["x" * 101],
                    [f"f{i}" for i in range(51)]):
            self.assertIsNotNone(problem({**GOOD, "feature_columns": bad}), repr(bad)[:40])
        self.assertIsNone(problem({**GOOD, "feature_columns": ["Viscosity (cP)", "solids %"],
                                   "units": {"salt_spray_hours": "h"}}))

    def test_duplicate_features_are_named_in_the_message(self):
        err = problem({**GOOD, "feature_columns": ["a", "b", "a"]})
        self.assertIn("'a' is listed twice", err.message)

    def test_target_metric_rules(self):
        for bad in ("", " ", None, 5, " y", "y ", "x" * 101):
            self.assertIsNotNone(problem({**GOOD, "target_metric": bad}), repr(bad))

    def test_target_cannot_also_be_a_feature(self):
        err = problem({**GOOD, "target_metric": "cure_temp_c"})
        self.assertIn("also listed as a feature", err.message)
        self.assertIsNotNone(problem({**GOOD, "target_metric": "CURE_TEMP_C"}))

    def test_units_rules(self):
        for bad in (["h"], "h", {"not_a_column": "h"}, {"salt_spray_hours": ""}, {"salt_spray_hours": "  "},
                    {"salt_spray_hours": 5}, {"salt_spray_hours": None}, {"salt_spray_hours": "x" * 41}):
            self.assertIsNotNone(problem({**GOOD, "units": bad}), repr(bad))

    def test_unrecognised_fields_are_rejected_not_ignored(self):
        for extra in ({"threshold": 5}, {"acceptance_criteria": "x"}, {"Direction": "maximize"}, {"target": 5}):
            err = problem({**GOOD, **extra})
            self.assertIsNotNone(err, extra)
            self.assertIn("Unrecognized field", err.message)

    def test_error_messages_never_leak_internal_details(self):
        err = problem({**GOOD, "target_value": "300"})
        self.assertNotIn("pydantic", err.message.lower())
        self.assertNotIn("Traceback", err.message)


class TestSpecThroughTheApi(IsolatedApiTestCase):
    dir_name = "_test_env_spec_validation"

    def _create(self, client, spec, name="case"):
        return client.post("/api/change-cases", json={
            "name": name, "trigger_type": "regulatory_restriction", "restricted_substance": "PFHxA",
            "qualification_spec": spec})

    def _case_count(self, client):
        return len(client.get("/api/change-cases").json())

    def test_valid_spec_is_stored_exactly(self):
        _, client = self.org_and_client("SpecOK")
        r = self._create(client, GOOD)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(json.loads(r.json()["qualification_spec_json"]), GOOD)

    def test_invalid_specs_are_rejected_with_400_and_nothing_is_stored(self):
        _, client = self.org_and_client("SpecBad")
        bad_specs = [
            {}, {"target_value": 80.0}, {**GOOD, "target_value": "300"}, {**GOOD, "direction": "sideways"},
            {**GOOD, "feature_columns": []}, {**GOOD, "feature_columns": ["a", "a"]},
            {**GOOD, "target_metric": "crosslinker_ratio"}, {**GOOD, "unexpected": 1},
            {**GOOD, "units": {"nope": "h"}}, {**GOOD, "units": ["h"]},
        ]
        for spec in bad_specs:
            r = self._create(client, spec)
            self.assertEqual(r.status_code, 400, (spec, r.text))
            self.assertIn("qualification_spec", r.json()["error"])
            self.assertEqual(r.json().get("field"), "qualification_spec")
        self.assertEqual(self._case_count(client), 0)

    def test_non_finite_numbers_sent_as_raw_json_are_rejected(self):
        _, client = self.org_and_client("SpecNaN")
        for token in ("NaN", "Infinity", "-Infinity"):
            body = ('{"name":"c","trigger_type":"regulatory_restriction","restricted_substance":"x",'
                    '"qualification_spec":{"feature_columns":["a"],"target_metric":"y","target_value":'
                    + token + ',"direction":"maximize"}}')
            r = client.post("/api/change-cases", content=body, headers={"Content-Type": "application/json"})
            self.assertIn(r.status_code, (400, 422), (token, r.text))
        self.assertEqual(self._case_count(client), 0)

    def test_the_form_payload_shape_still_works(self):
        # exactly what the Diagnostics create form sends
        _, client = self.org_and_client("SpecForm")
        spec = {"feature_columns": ["viscosity", "solids_pct"], "target_metric": "salt_spray_hours",
                "target_value": 500, "direction": "maximize",
                "units": {"viscosity": "cP", "solids_pct": "%", "salt_spray_hours": "h"}}
        self.assertEqual(self._create(client, spec).status_code, 201)

    def test_unauthenticated_create_is_rejected(self):
        self.assertEqual(self._create(self.client_factory(), GOOD).status_code, 401)


if __name__ == "__main__":
    unittest.main()