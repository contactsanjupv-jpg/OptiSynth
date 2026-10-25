"""
A diagnostic must declare an explicit canonical unit for every modelled input feature and for the target WHEN IT IS
CREATED, because units cannot be edited afterwards: a diagnostic created without them could never be ranked.

  * Enforced in the service (change_case_service.create_change_case), so the API, scripts and any other caller get the
    same refusal; the creation form checks too, but the backend is authoritative.
  * Dimensionless quantities are explicit and distinct: 'dimensionless', 'fraction' (0-1) and '%' (0-100). A blank is
    never read as dimensionless, and fraction and % are never converted into each other.
  * Supported units keep their exact conversions. Any other explicit label is a CUSTOM unit: exact symbol only (letter case matters, spacing is ignored), not
    dimensionally validated, never converted -- and the creation response says so.
  * Legacy diagnostics (created without units) are left exactly as stored: nothing invented, nothing rewritten; new
    rankings stay refused with a recovery path.

Run:  python3 -m unittest backend.tests.test_units_at_creation -v
"""
import json
import unittest

from backend.tests import evidence_fixtures as F
from backend.tests._api_base import SPEC, SPEC_NO_UNITS, IsolatedApiTestCase

IN_A = {"crosslinker_ratio": 0.12, "cure_temp_c": 158}
IN_B = {"crosslinker_ratio": 0.10, "cure_temp_c": 150}
FAR = {"crosslinker_ratio": 0.40, "cure_temp_c": 300}


class TestCreationRequiresUnits(IsolatedApiTestCase):
    dir_name = "_test_env_units_create"

    def _create(self, client, spec, name="diag"):
        return client.post("/api/change-cases", json={
            "name": name, "trigger_type": "regulatory_restriction", "restricted_substance": "x",
            "qualification_spec": spec})

    def _count(self, client):
        return len(client.get("/api/change-cases").json())

    def _with_units(self, **overrides):
        return {**SPEC, "units": {**SPEC["units"], **overrides}}

    def _refused(self, client, spec):
        before = self._count(client)
        r = self._create(client, spec)
        self.assertIn(r.status_code, (400, 422), r.text)
        self.assertEqual(self._count(client), before, "a refused diagnostic must not be stored")
        return r

    # ---------------------------------------------------------------- missing
    def test_a_diagnostic_without_any_unit_is_refused_naming_every_field(self):
        _, client = self.org_and_client("missing")
        r = self._refused(client, SPEC_NO_UNITS)
        self.assertEqual(r.status_code, 400)
        message = r.json()["error"]
        for field in ("crosslinker_ratio (input feature)", "cure_temp_c (input feature)", "salt_spray_hours (target)"):
            self.assertIn(field + ": no unit declared", message)
        for needle in ("never guessed", "'dimensionless'", "'fraction'", "'%'", "never converted into each other",
                       "custom unit", "exact symbol only (letter case matters, spacing is ignored)", "cannot be edited after the diagnostic is created"):
            self.assertIn(needle, message)

    def test_every_way_of_leaving_units_out_is_refused(self):
        _, client = self.org_and_client("omit")
        base = {k: v for k, v in SPEC.items() if k != "units"}
        for label, spec in {
            "units absent": base,
            "units null": {**base, "units": None},
            "units empty": {**base, "units": {}},
            "target unit only": {**base, "units": {"salt_spray_hours": "h"}},
            "features only": {**base, "units": {"crosslinker_ratio": "fraction", "cure_temp_c": "degC"}},
            "one feature missing": self._with_units(cure_temp_c=None) if False else
                                   {**base, "units": {"crosslinker_ratio": "fraction", "salt_spray_hours": "h"}},
        }.items():
            r = self._refused(client, spec)
            self.assertEqual(r.status_code, 400, label)

    def test_only_the_fields_that_lack_a_unit_are_named(self):
        _, client = self.org_and_client("partial")
        spec = {**SPEC, "units": {"crosslinker_ratio": "fraction", "salt_spray_hours": "h"}}
        message = self._refused(client, spec).json()["error"]
        self.assertIn("cure_temp_c (input feature): no unit declared", message)
        self.assertNotIn("crosslinker_ratio (", message)
        self.assertNotIn("salt_spray_hours (", message)

    # ---------------------------------------------------------------- invalid / ambiguous
    def test_blank_and_whitespace_units_are_refused_never_read_as_dimensionless(self):
        _, client = self.org_and_client("blank")
        for blank in ("", "   ", "\t"):
            r = self._refused(client, self._with_units(crosslinker_ratio=blank))
            self.assertIn("crosslinker_ratio", json.dumps(r.json()))
        self._refused(client, self._with_units(crosslinker_ratio=None))

    def test_ambiguous_spellings_are_refused_with_the_three_explicit_choices(self):
        _, client = self.org_and_client("ambiguous")
        for unit in ("ratio", "Ratio", "percent", "Percent", "percentage", "pct", "unitless", "none", "n/a", "-"):
            message = self._refused(client, self._with_units(crosslinker_ratio=unit)).json()["error"]
            self.assertIn("does not say whether the values are a pure number", message, unit)
            self.assertIn("'dimensionless', 'fraction' or '%'", message, unit)
        for unit in ("Fraction", "DIMENSIONLESS", "Dimensionless"):
            self.assertIn("only by letter case", self._refused(client, self._with_units(crosslinker_ratio=unit)).json()["error"], unit)
        self.assertIn("'H' is ambiguous", self._refused(client, self._with_units(salt_spray_hours="H")).json()["error"])

    # ---------------------------------------------------------------- dimensionless, explicit and distinct
    def test_dimensionless_fraction_and_percent_are_each_accepted_and_stored_exactly_as_declared(self):
        _, client = self.org_and_client("dimless")
        for unit in ("dimensionless", "fraction", "%"):
            spec = self._with_units(crosslinker_ratio=unit)
            r = self._create(client, spec, name=f"d-{unit}")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(json.loads(r.json()["qualification_spec_json"])["units"]["crosslinker_ratio"], unit)
            self.assertEqual(r.json()["unit_notes"], [], "an explicit dimensionless unit needs no custom-label note")

    def test_fraction_and_percent_are_never_converted_into_each_other(self):
        _, client = self.org_and_client("noconvert")
        for declared, stated in (("fraction", "%"), ("%", "fraction"), ("dimensionless", "fraction"), ("fraction", "dimensionless")):
            case_id = self._create(client, self._with_units(crosslinker_ratio=declared), name=f"{declared}-{stated}").json()["id"]
            r = client.post(f"/api/change-cases/{case_id}/candidates", json={
                "name": "X", "features": IN_A, "units": {"crosslinker_ratio": stated, "cure_temp_c": "degC"}})
            self.assertEqual(r.status_code, 400, f"{stated} stated for a {declared} field must be refused: {r.text}")
            ok = client.post(f"/api/change-cases/{case_id}/candidates", json={
                "name": "X", "features": IN_A, "units": {"crosslinker_ratio": declared, "cure_temp_c": "degC"}})
            self.assertEqual(ok.status_code, 201, ok.text)

    # ---------------------------------------------------------------- supported conversions and custom labels
    def test_supported_exact_conversions_still_work(self):
        _, client = self.org_and_client("convert")
        spec = {"feature_columns": ["crosslinker_ratio", "cure_time_h"], "target_metric": "salt_spray_hours",
                "target_value": 300.0, "direction": "maximize",
                "units": {"crosslinker_ratio": "fraction", "cure_time_h": "h", "salt_spray_hours": "h"}}
        r = self._create(client, spec)
        self.assertEqual(r.status_code, 201, r.text)
        case_id = r.json()["id"]
        add = client.post(f"/api/change-cases/{case_id}/candidates", json={
            "name": "C", "features": {"crosslinker_ratio": 0.1, "cure_time_h": 90},
            "units": {"crosslinker_ratio": "fraction", "cure_time_h": "min"}})
        self.assertEqual(add.status_code, 201, add.text)
        cands = {c["candidate_name"]: c for c in client.get(f"/api/change-cases/{case_id}/candidates").json()}
        self.assertAlmostEqual(cands["C"]["properties"]["cure_time_h"], 1.5, places=9)  # 90 min = 1.5 h, exactly
        self.assertEqual(r.json()["unit_notes"], [])

    def test_a_custom_label_is_accepted_and_documented_as_exact_match_only(self):
        _, client = self.org_and_client("custom")
        spec = self._with_units(salt_spray_hours="score", crosslinker_ratio="wt%")
        r = self._create(client, spec)
        self.assertEqual(r.status_code, 201, r.text)
        notes = " ".join(r.json()["unit_notes"])
        self.assertEqual(len(r.json()["unit_notes"]), 2)
        for needle in ("salt_spray_hours: 'score' is a custom unit label", "crosslinker_ratio: 'wt%' is a custom unit label",
                       "matched by exact symbol only (letter case matters, spacing is ignored)", "has not been checked dimensionally", "never converted"):
            self.assertIn(needle, notes)
        case_id = r.json()["id"]
        url = f"/api/change-cases/{case_id}/candidates"
        for different in ("WT%", "wt%%", "wt-%"):   # letter case matters; any other symbol is a different label
            wrong = client.post(url, json={"name": "X", "features": IN_A, "units": {"crosslinker_ratio": different, "cure_temp_c": "degC"}})
            self.assertEqual(wrong.status_code, 400, f"{different!r} is not the declared label: {wrong.text}")
        spaced = client.post(url, json={"name": "S", "features": IN_A, "units": {"crosslinker_ratio": "wt %", "cure_temp_c": "degC"}})
        self.assertEqual(spaced.status_code, 201, "spacing is ignored, exactly as documented")
        right = client.post(url, json={"name": "X", "features": IN_A, "units": {"crosslinker_ratio": "wt%", "cure_temp_c": "degC"}})
        self.assertEqual(right.status_code, 201, right.text)

    # ---------------------------------------------------------------- API bypass attempts
    def test_bypass_attempts_are_refused_and_store_nothing(self):
        _, client = self.org_and_client("bypass")
        before = self._count(client)
        attempts = [
            {**SPEC_NO_UNITS, "units": {"nope": "h"}},                                  # unit for a non-field
            {**SPEC_NO_UNITS, "units": ["h"]},                                           # wrong type
            {**SPEC_NO_UNITS, "units": "h"},
            {**SPEC_NO_UNITS, "units": {"crosslinker_ratio": ["fraction"], "cure_temp_c": "degC", "salt_spray_hours": "h"}},
            {**SPEC_NO_UNITS, "units": {"crosslinker_ratio": 1, "cure_temp_c": "degC", "salt_spray_hours": "h"}},
            {**SPEC_NO_UNITS, "default_units": {"crosslinker_ratio": "fraction"}},        # an invented extra field
            {**SPEC, "unit": "h"},
            {k: v for k, v in SPEC_NO_UNITS.items()},                                    # simply unitless
        ]
        for spec in attempts:
            r = self._create(client, spec)
            self.assertIn(r.status_code, (400, 422), json.dumps(spec)[:80] + " -> " + r.text[:120])
        self.assertEqual(self._count(client), before)

    def test_units_cannot_be_added_to_or_changed_on_an_existing_diagnostic(self):
        org_id, client = self.org_and_client("noedit")
        legacy = self.make_legacy_case(org_id, SPEC_NO_UNITS)
        before = client.get(f"/api/change-cases/{legacy}").json()["qualification_spec_json"]
        for body in ({"units": SPEC["units"]}, {"qualification_spec": SPEC}, {"qualification_spec_json": json.dumps(SPEC)}):
            r = client.patch(f"/api/change-cases/{legacy}", json=body)
            self.assertIn(r.status_code, (400, 422), r.text)
        self.assertEqual(client.get(f"/api/change-cases/{legacy}").json()["qualification_spec_json"], before)

    def test_the_service_itself_refuses_so_scripts_and_other_callers_are_covered(self):
        from backend.app.schemas.errors import ValidationError
        from backend.app.services import change_case_service
        org_id, client = self.org_and_client("service")
        with self.assertRaises(ValidationError) as ctx:
            change_case_service.create_change_case(org_id, 1, "n", "regulatory_restriction", None, dict(SPEC_NO_UNITS))
        self.assertEqual(ctx.exception.field, "units")
        case = change_case_service.create_change_case(org_id, self._user_id(org_id), "n", "regulatory_restriction", None, dict(SPEC))
        self.assertIn("unit_notes", case)

    def _user_id(self, org_id):
        from sqlalchemy import text
        from backend.app.config.database import db_connection
        with db_connection() as conn:
            return conn.execute(text("SELECT user_id FROM memberships WHERE organization_id = :o LIMIT 1"), {"o": org_id}).scalar()

    # ---------------------------------------------------------------- legacy diagnostics
    def test_a_legacy_diagnostic_is_unchanged_stays_visible_and_has_a_recovery_path(self):
        org_id, client = self.org_and_client("legacy")
        self.paid(org_id)
        legacy = self.make_legacy_case(org_id, SPEC_NO_UNITS, name="Legacy")
        before = client.get(f"/api/change-cases/{legacy}").json()["qualification_spec_json"]
        self.assertEqual(json.loads(before), SPEC_NO_UNITS)
        self.assertNotIn("units", json.loads(before), "no unit was invented")
        listed = {c["name"]: c for c in client.get("/api/change-cases").json()}
        self.assertEqual(listed["Legacy"]["qualification_spec_json"], before)
        up = self.upload(client, legacy, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14)))
        self.assertEqual(up.status_code, 201, up.text)  # a legacy diagnostic can still take evidence as before
        client.post(f"/api/change-cases/{legacy}/candidates", json={"name": "A", "features": IN_A})
        r = client.post(f"/api/change-cases/{legacy}/rank")
        self.assertEqual(r.status_code, 400)
        message = r.json()["error"]
        self.assertIn("create a new diagnostic", message)
        self.assertIn("left unchanged", message)
        sufficiency = client.get(f"/api/change-cases/{legacy}/sufficiency").json()
        self.assertEqual(next(c for c in sufficiency["checks"] if c["code"] == "UNITS_DECLARED")["status"], "fail")
        self.assertEqual(client.get(f"/api/change-cases/{legacy}").json()["qualification_spec_json"], before)

    # ---------------------------------------------------------------- earlier safeguards still hold
    def test_a_diagnostic_created_with_units_ranks_and_keeps_the_domain_and_stale_report_safeguards(self):
        org_id, client = self.org_and_client("safeguards")
        self.paid(org_id)
        case_id = self._create(client, SPEC).json()["id"]
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        for name, features in (("InA", IN_A), ("InB", IN_B), ("Far", FAR)):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features}).status_code, 201)
        by = {x["candidate_name"]: x for x in client.post(f"/api/change-cases/{case_id}/rank").json()}
        self.assertIsNone(by["Far"]["rank"])                                    # domain-aware ranking
        self.assertEqual(sorted(by[n]["rank"] for n in ("InA", "InB")), [1, 2])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)
        client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "InC", "features": {"crosslinker_ratio": 0.13, "cure_temp_c": 165}})
        refused = client.post(f"/api/change-cases/{case_id}/report")           # stale-report refusal
        self.assertEqual(refused.status_code, 400)
        self.assertIn("out of date", refused.json()["error"])
        self.assertIn("accuracy_tolerance_note", by["InA"]["model_quality"])   # metric labelling


if __name__ == "__main__":
    unittest.main()
