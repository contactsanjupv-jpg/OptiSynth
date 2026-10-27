"""
Candidate CSV import (preview + all-or-nothing import).

What is proven here, through the real HTTP path unless a test says otherwise:
  * a valid CSV of 10 candidates previews without writing anything, and imports exactly once, with every
    value stored in the diagnostic's canonical unit and what was entered kept beside it;
  * the CSV is read with a real CSV parser: commas, quotes, a UTF-8 byte-order mark and CRLF line endings work;
  * missing / duplicated / unknown / wrongly mapped columns, missing values, non-numbers, NaN / infinity and
    incompatible units each produce a row-numbered error, and ANY error means NOTHING is stored;
  * duplicate names (inside the file, against existing candidates, removed ones included) are refused;
  * archived and completed diagnostics refuse the import; other organizations get 404;
  * the batch is atomic: a database failure part-way through leaves zero rows, and the checks are repeated
    inside the write transaction (an interloper's duplicate name, or an archive, between validation and
    insert still stops the whole batch);
  * importing never ranks and never produces a report: an earlier ranking is reported out of date and the
    report is refused until ranking is run again;
  * the file-size limit applies to both new endpoints, and the manual "Add candidate" still works.

Run:  python3 -m unittest backend.tests.test_candidate_csv_import -v
"""
import csv
import io
import json
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.middleware import upload_limits  # noqa: E402
from backend.app.services import candidate_import_rules as rules  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, IsolatedApiTestCase  # noqa: E402

FEATURES = SPEC["feature_columns"]  # crosslinker_ratio (fraction), cure_temp_c (degC)
CONFIRM = {"confirm": True, "confirm_canonical_units": True}
PREVIEW_OK = {"confirm_canonical_units": True}


def make_csv(rows, header=("candidate_name", "crosslinker_ratio", "cure_temp_c"), bom=False, newline="\n"):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator=newline)
    w.writerow(header)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def ten(prefix="Cand"):
    """10 distinct candidates inside the synthetic history's observed range (0.080-0.145, 140-179 C)."""
    return [(f"{prefix} {i:02d}", round(0.09 + 0.005 * i, 4), 141 + 3 * i) for i in range(1, 11)]


# ---------------------------------------------------------------------------
# Pure rules (no database, no HTTP)
# ---------------------------------------------------------------------------
class TestImportRulesPure(unittest.TestCase):
    def test_parse_csv_bom_quotes_commas_and_crlf(self):
        data = make_csv([('Acme "Supreme", v2', "0.1", "150"), ("Müller–Ø, Inc.", "0.12", "160")], bom=True, newline="\r\n")
        parsed = rules.parse_csv(data)
        self.assertEqual(parsed["headers"][0], "candidate_name", "the byte-order mark must not stay in the header")
        self.assertEqual([r["cells"]["candidate_name"] for r in parsed["rows"]], ['Acme "Supreme", v2', "Müller–Ø, Inc."])
        self.assertEqual([r["row"] for r in parsed["rows"]], [2, 3])

    def test_parse_csv_reports_file_row_numbers_after_blank_lines(self):
        parsed = rules.parse_csv(b"a,b\n1,2\n\n3,4\n,\n5,6\n")
        self.assertEqual([r["row"] for r in parsed["rows"]], [2, 4, 6])
        self.assertEqual(parsed["blank_rows_skipped"], 1)

    def test_parse_csv_refuses_unreadable_files(self):
        for bad, text in ((b"", "empty"), (b"   \n", "empty"), (b"a,b\n", "no candidates"),
                          (b"a,b\n1\x00,2\n", "binary"), ("a,b\n1,é\n".encode("latin-1"), "UTF-8")):
            with self.assertRaises(Exception) as ctx:
                rules.parse_csv(bad)
            self.assertIn(text, ctx.exception.message, bad)

    def test_parse_csv_flags_ragged_rows_and_duplicate_headers(self):
        parsed = rules.parse_csv(b"a,b,a\n1,2,3\n4,5\n6,7,8,9\n")
        messages = [(e["row"], e["message"]) for e in parsed["problems"]]
        self.assertTrue(any(r == 3 and "2 values" in m for r, m in messages), messages)
        self.assertTrue(any(r == 4 and "4 values" in m for r, m in messages), messages)
        self.assertTrue(any("'a' more than once" in m for _, m in messages), messages)
        # trailing empty cells beyond the header are tolerated
        self.assertEqual(rules.parse_csv(b"a,b\n1,2,,\n")["problems"], [])

    def test_parse_csv_row_cap(self):
        rows = b"".join(f"n{i},1\n".encode() for i in range(rules.MAX_IMPORT_ROWS + 1))
        with self.assertRaises(Exception) as ctx:
            rules.parse_csv(b"candidate_name,x\n" + rows)
        self.assertIn("Too many candidates", ctx.exception.message)
        self.assertEqual(len(rules.parse_csv(b"candidate_name,x\n" + rows[: len(rows) - len(f"n{rules.MAX_IMPORT_ROWS},1\n")]) ["rows"]),
                         rules.MAX_IMPORT_ROWS)

    def test_parse_number_is_strict(self):
        for good, expected in (("0.12", 0.12), (" 150 ", 150.0), ("-3", -3.0), (".5", 0.5), ("1e3", 1000.0), ("+2.5E-1", 0.25)):
            self.assertEqual(rules.parse_number(good, "f")[0], expected)
        for bad in ("", "  ", "abc", "NaN", "nan", "inf", "-Infinity", "1e999", "1,5", "1,000", "0,085", "1_0",
                    "12 kg", "٣", "1..2", "--1", "0x10", "true", "1/2"):
            value, message = rules.parse_number(bad, "f")
            self.assertIsNone(value, bad)
            self.assertTrue(message, bad)
        self.assertIn("NaN or infinity", rules.parse_number("NaN", "f")[1])

    def test_resolve_mapping_defaults_and_problems(self):
        headers = ["candidate_name", "crosslinker_ratio", "cure_temp_c", "note"]
        name, mapping, problems = rules.resolve_mapping(headers, FEATURES, {})
        self.assertEqual((name, mapping, problems), ("candidate_name", {f: f for f in FEATURES}, []))
        # a header that differs only by case is NOT silently matched
        _, mapping, problems = rules.resolve_mapping(["candidate_name", "Crosslinker_Ratio", "cure_temp_c"], FEATURES, {})
        self.assertIsNone(mapping["crosslinker_ratio"])
        self.assertTrue(any("crosslinker_ratio" in p["message"] for p in problems))
        # explicit mapping wins; a column cannot feed two fields; the name column cannot be a feature
        _, _, problems = rules.resolve_mapping(headers, FEATURES, {"column_mapping": {"cure_temp_c": "crosslinker_ratio"}})
        self.assertTrue(any("mapped to both" in p["message"] for p in problems))
        _, _, problems = rules.resolve_mapping(headers, FEATURES, {"name_column": "cure_temp_c"})
        self.assertTrue(any("both as the candidate name" in p["message"] for p in problems))
        _, _, problems = rules.resolve_mapping(headers, FEATURES, {"column_mapping": {"colour": "note"}})
        self.assertTrue(any("not an input field" in p["message"] for p in problems))
        _, _, problems = rules.resolve_mapping(headers, FEATURES, {"column_mapping": {"cure_temp_c": "nope"}})
        self.assertTrue(any("not in the file" in p["message"] for p in problems))
        _, _, problems = rules.resolve_mapping(headers, FEATURES, {"name_column": "who"})
        self.assertTrue(any("'who' is not in the file" in p["message"] for p in problems))

    def test_resolve_units_never_guesses(self):
        units, problems = rules.resolve_units(SPEC, {})
        self.assertEqual(units, {})
        self.assertEqual(len(problems), 2, "both fields have a declared unit, so both need a statement")
        units, problems = rules.resolve_units(SPEC, {"confirm_canonical_units": True})
        self.assertEqual((units, problems), ({"crosslinker_ratio": "fraction", "cure_temp_c": "degC"}, []))
        units, problems = rules.resolve_units(SPEC, {"confirm_canonical_units": True, "units": {"cure_temp_c": "degF"}})
        self.assertEqual(units["cure_temp_c"], "degF", "an explicit unit beats the blanket confirmation")
        _, problems = rules.resolve_units(SPEC, {"units": {"colour": "x"}, "confirm_canonical_units": True})
        self.assertTrue(any("not an input field" in p["message"] for p in problems))

    def test_parse_options_refuses_unknown_and_malformed(self):
        self.assertEqual(rules.parse_options(None), {})
        self.assertEqual(rules.parse_options('{"confirm": true}'), {"confirm": True})
        for bad in ("{", "[1]", '{"confirmed": true}'):
            with self.assertRaises(Exception):
                rules.parse_options(bad)

    def test_upload_guard_covers_the_new_endpoints(self):
        for path in ("/api/change-cases/7/candidates/import", "/api/change-cases/7/candidates/import/preview"):
            self.assertTrue(any(p.match(path) for p in upload_limits.UPLOAD_PATHS), path)
        for path in ("/api/change-cases/7/candidates", "/api/change-cases/7/candidates/import/other",
                     "/api/change-cases/7/candidates/5"):
            self.assertFalse(any(p.match(path) for p in upload_limits.UPLOAD_PATHS), path)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class ImportApiBase(IsolatedApiTestCase):
    dir_name = "_test_env_cand_import"

    def imp(self, client, case_id, content, options=None, preview=False, filename="candidates.csv"):
        route = "candidates/import/preview" if preview else "candidates/import"
        return self.post_file(client, case_id, filename, content, options, route)

    def stored(self, org_id, case_id):
        from backend.app.repositories import candidate_repo
        return candidate_repo.list_candidates_for_change_case(org_id, case_id, include_archived=True)

    def predictions(self, org_id, case_id):
        from backend.app.repositories import candidate_repo
        return sum(1 for c in self.stored(org_id, case_id)
                   if candidate_repo.get_latest_prediction_for_candidate(org_id, c["id"]))

    def add(self, client, case_id, name, features):
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def assert_refused_nothing_stored(self, org_id, case_id, response, *fragments, before=0):
        self.assertEqual(response.status_code, 400, response.text[:300])
        for f in fragments:
            self.assertIn(f, response.json()["error"])
        self.assertEqual(len(self.stored(org_id, case_id)), before, "a refused import must store nothing")

    def preview_errors(self, client, case_id, content, options=PREVIEW_OK):
        r = self.imp(client, case_id, content, options, preview=True)
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.json()


class TestValidImport(ImportApiBase):
    def test_preview_writes_nothing_and_describes_the_import(self):
        org_id, client, case_id = self.setup_case("Prev")
        p = self.preview_errors(client, case_id, make_csv(ten()))
        self.assertTrue(p["ok"], p)
        self.assertEqual((p["would_import"], p["row_count"], p["error_count"]), (10, 10, 0))
        self.assertEqual(p["name_column"], "candidate_name")
        self.assertEqual(p["mapping"], {f: f for f in FEATURES})
        self.assertEqual(p["expected_units"], {"crosslinker_ratio": "fraction", "cure_temp_c": "degC"})
        self.assertEqual(len(p["preview_rows"]), 10)
        self.assertEqual(p["preview_rows"][0]["row"], 2)
        self.assertEqual(len(p["sha256"]), 64)
        self.assertEqual(self.stored(org_id, case_id), [], "a preview must store nothing")

    def test_valid_import_of_ten_candidates(self):
        org_id, client, case_id = self.setup_case("Ten")
        r = self.imp(client, case_id, make_csv(ten()), CONFIRM)
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["imported_count"], 10)
        self.assertIn("not ranked yet", body["ranking_notice"])
        rows = self.stored(org_id, case_id)
        self.assertEqual([c["candidate_name"] for c in rows], [n for n, _, _ in ten()])
        first = json.loads(rows[0]["properties_json"])
        self.assertEqual(first, {"crosslinker_ratio": 0.095, "cure_temp_c": 144.0})
        record = json.loads(rows[0]["input_json"])
        self.assertEqual(record["cure_temp_c"]["unit"], "degC")
        self.assertEqual(self.predictions(org_id, case_id), 0, "importing must not rank anything")
        # they show up in the normal listing, unranked, like manually added candidates
        listing = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertEqual(len(listing), 10)
        self.assertTrue(all(c["latest_prediction"] is None for c in listing))

    def test_audit_log_records_counts_and_a_fingerprint_but_no_candidate_data(self):
        from backend.app.repositories import audit_repo
        org_id, client, case_id = self.setup_case("Audit")
        r = self.imp(client, case_id, make_csv(ten("SecretName")), CONFIRM)
        self.assertEqual(r.status_code, 201, r.text)
        entries = [e for e in audit_repo.list_audit_logs(org_id) if e["action"] == "candidate.import"]
        self.assertEqual(len(entries), 1)
        detail = entries[0]["detail"]
        self.assertIn("imported=10", detail)
        self.assertIn(f"change_case_id={case_id}", detail)
        self.assertNotIn("SecretName", detail)
        self.assertNotIn("0.095", detail)

    def test_commas_quotes_bom_crlf_and_unicode_in_the_file(self):
        org_id, client, case_id = self.setup_case("Csvq")
        names = ['Acme "Supreme", v2', "Müller–Ø, Inc.", "Plain"]
        rows = [(names[0], "0.10", "150"), (names[1], "0.12", "160"), (names[2], "0.13", "170")]
        r = self.imp(client, case_id, make_csv(rows, bom=True, newline="\r\n"), CONFIRM)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([c["candidate_name"] for c in self.stored(org_id, case_id)], names)

    def test_unrelated_columns_are_ignored_and_never_become_inputs(self):
        org_id, client, case_id = self.setup_case("Extra")
        header = ("candidate_name", "supplier", "crosslinker_ratio", "cure_temp_c", "cost_per_kg", "notes")
        rows = [("A", "Acme", 0.1, 150, 9.5, "x"), ("B", "Beta", 0.12, 160, 8.0, "y")]
        p = self.preview_errors(client, case_id, make_csv(rows, header=header))
        self.assertEqual(p["ignored_columns"], ["supplier", "cost_per_kg", "notes"])
        r = self.imp(client, case_id, make_csv(rows, header=header), CONFIRM)
        self.assertEqual(r.status_code, 201, r.text)
        for c in self.stored(org_id, case_id):
            self.assertEqual(sorted(json.loads(c["properties_json"])), sorted(FEATURES))

    def test_user_mapped_column_names(self):
        org_id, client, case_id = self.setup_case("Mapped")
        header = ("Material", "XL ratio", "Cure C")
        opts = {**CONFIRM, "name_column": "Material",
                "column_mapping": {"crosslinker_ratio": "XL ratio", "cure_temp_c": "Cure C"}}
        r = self.imp(client, case_id, make_csv([("A", 0.1, 150), ("B", 0.12, 160)], header=header), opts)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(json.loads(self.stored(org_id, case_id)[1]["properties_json"]),
                         {"crosslinker_ratio": 0.12, "cure_temp_c": 160.0})
        # without the mapping the same file is refused: nothing is inferred from "similar" names
        org2, client2, case2 = self.setup_case("Mapped2")
        r = self.imp(client2, case2, make_csv([("A", 0.1, 150)], header=header), CONFIRM)
        self.assert_refused_nothing_stored(org2, case2, r, "Nothing was imported", "not in the file")

    def test_manual_add_still_works_and_still_requires_units(self):
        org_id, client, case_id = self.setup_case("Manual")
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={
            "name": "Typed", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150},
            "units": {"crosslinker_ratio": "fraction", "cure_temp_c": "degC"}})
        self.assertEqual(r.status_code, 201, r.text)
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={
            "name": "NoUnits", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, "units": {}})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("a unit is required", r.json()["error"])
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={
            "name": "typed", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150},
            "units": {"crosslinker_ratio": "fraction", "cure_temp_c": "degC"}})
        self.assertEqual(r.status_code, 400, "duplicate names are still refused (ignoring case)")
        self.assertEqual(len(self.stored(org_id, case_id)), 1)


class TestColumnProblems(ImportApiBase):
    def _refused(self, label, content, options=CONFIRM, *fragments):
        org_id, client, case_id = self.setup_case(label)
        r = self.imp(client, case_id, content, options)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Nothing was imported", *fragments)
        return client, case_id

    def test_missing_name_column(self):
        content = make_csv([(0.1, 150)], header=("crosslinker_ratio", "cure_temp_c"))
        client, case_id = self._refused("NoName", content, CONFIRM, "'candidate_name' is not in the file")

    def test_missing_feature_column(self):
        content = make_csv([("A", 0.1)], header=("candidate_name", "crosslinker_ratio"))
        self._refused("NoFeat", content, CONFIRM, "'cure_temp_c'")

    def test_duplicated_column_in_the_file(self):
        content = make_csv([("A", 0.1, 150, 151)], header=("candidate_name", "crosslinker_ratio", "cure_temp_c", "cure_temp_c"))
        self._refused("DupCol", content, CONFIRM, "more than once")

    def test_unknown_field_and_unknown_column_in_the_mapping(self):
        content = make_csv([("A", 0.1, 150)])
        self._refused("UnkField", content, {**CONFIRM, "column_mapping": {"colour_index": "cure_temp_c"}}, "not an input field")
        self._refused("UnkCol", content, {**CONFIRM, "column_mapping": {"cure_temp_c": "nope"}}, "'nope', which is not in the file")

    def test_one_column_mapped_to_two_fields_and_name_reused_as_feature(self):
        content = make_csv([("A", 0.1, 150)])
        self._refused("Twice", content, {**CONFIRM, "column_mapping": {"cure_temp_c": "crosslinker_ratio"}}, "mapped to both")
        self._refused("NameFeat", content, {**CONFIRM, "name_column": "cure_temp_c"}, "both as the candidate name")

    def test_wrongly_mapped_column_is_caught_by_the_value_checks(self):
        # the name column mapped as a feature: its text is not a number, so every row is refused
        content = make_csv([("A", 0.1, 150), ("B", 0.12, 160)])
        org_id, client, case_id = self.setup_case("WrongMap")
        opts = {**CONFIRM, "name_column": "crosslinker_ratio", "column_mapping": {"crosslinker_ratio": "candidate_name"}}
        r = self.imp(client, case_id, content, opts)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.stored(org_id, case_id)), 0)

    def test_ragged_row_is_reported_with_its_row_number(self):
        org_id, client, case_id = self.setup_case("Ragged")
        content = b"candidate_name,crosslinker_ratio,cure_temp_c\nA,0.1,150\nB,0.12\n"
        p = self.preview_errors(client, case_id, content)
        self.assertFalse(p["ok"])
        self.assertTrue(any(e["row"] == 3 and "2 values" in e["message"] for e in p["errors"]), p["errors"])
        r = self.imp(client, case_id, content, CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Row 3")

    def test_unreadable_files(self):
        org_id, client, case_id = self.setup_case("Unread")
        for content, text in ((b"", "empty"), (b"candidate_name,crosslinker_ratio,cure_temp_c\n", "no candidates"),
                              (b"\x00\x01\x02", "binary"), ("a,b\nx,\xe9\n".encode("latin-1"), "UTF-8")):
            r = self.imp(client, case_id, content, CONFIRM)
            self.assert_refused_nothing_stored(org_id, case_id, r, text)
            self.assertEqual(self.imp(client, case_id, content, PREVIEW_OK, preview=True).status_code, 400)

    def test_unknown_or_malformed_options(self):
        org_id, client, case_id = self.setup_case("Opts")
        content = make_csv(ten())
        r = self.imp(client, case_id, content, {**CONFIRM, "confirmed": True})
        self.assert_refused_nothing_stored(org_id, case_id, r, "Unknown import option")
        r = client.post(f"/api/change-cases/{case_id}/candidates/import",
                        files={"file": ("c.csv", content, "text/csv")}, data={"options": "{not json"})
        self.assert_refused_nothing_stored(org_id, case_id, r, "not valid JSON")

    def test_row_cap(self):
        org_id, client, case_id = self.setup_case("Cap")
        rows = [(f"c{i}", 0.1, 150) for i in range(rules.MAX_IMPORT_ROWS + 1)]
        r = self.imp(client, case_id, make_csv(rows), CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Too many candidates")


class TestValueProblems(ImportApiBase):
    def test_each_bad_value_is_a_row_numbered_error_and_blocks_the_whole_import(self):
        org_id, client, case_id = self.setup_case("Vals")
        bad = [("MissingVal", "", 150), ("Letters", "abc", 150), ("NotANumber", "NaN", 150), ("Infinite", "inf", 150),
               ("NegInf", "-Infinity", 150), ("Huge", "1e999", 150), ("CommaDecimal", "0,085", 150),
               ("Underscore", "1_0", 150), ("WithUnit", "0.1 fraction", 150), ("MissingName", "0.1", 150)]
        rows = [("Good1", 0.1, 150)] + bad[:-1] + [("", "0.1", 150)] + [("Good2", 0.12, 160)]
        content = make_csv(rows)
        p = self.preview_errors(client, case_id, content)
        self.assertFalse(p["ok"])
        by_row = {}
        for e in p["errors"]:
            by_row.setdefault(e["row"], []).append(e["message"])
        # rows 2 and the last are good; every bad row (3..11) and the nameless row 12 has an error
        self.assertNotIn(2, by_row)
        self.assertNotIn(len(rows) + 1, by_row)
        for row in range(3, 13):
            self.assertIn(row, by_row, f"row {row} should have an error: {by_row}")
        self.assertIn("missing", by_row[3][0])
        self.assertIn("not a plain number", by_row[4][0])
        self.assertIn("NaN or infinity", by_row[5][0])
        self.assertIn("NaN or infinity", by_row[6][0])
        self.assertIn("too large", by_row[8][0])
        self.assertIn("name is missing", by_row[12][0])
        self.assertEqual(p["would_import"], 0)
        r = self.imp(client, case_id, content, CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Nothing was imported", "Row 3")
        self.assertIn("more", r.json()["error"])  # long lists are summarised, with the count

    def test_one_bad_row_among_nine_good_stores_nothing(self):
        org_id, client, case_id = self.setup_case("NineOne")
        rows = ten()
        rows[6] = (rows[6][0], "oops", rows[6][2])
        r = self.imp(client, case_id, make_csv(rows), CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Row 8")
        # fixing the file and retrying works, and imports all ten
        self.assertEqual(self.imp(client, case_id, make_csv(ten()), CONFIRM).status_code, 201)
        self.assertEqual(len(self.stored(org_id, case_id)), 10)

    def test_feature_values_that_the_existing_candidate_rules_refuse(self):
        # mapped values that parse but break a candidate rule are refused with that rule's own message
        org_id, client, case_id = self.setup_case("Rules")
        p = self.preview_errors(client, case_id, make_csv([("A", 0.1, 150)]), {"confirm_canonical_units": True,
                                "units": {"cure_temp_c": "mm"}})
        self.assertFalse(p["ok"])
        self.assertTrue(any("cannot be safely converted" in e["message"] for e in p["errors"]), p["errors"])


class TestUnits(ImportApiBase):
    SPEC_MM = {"feature_columns": ["thickness", "cure_temp_c"], "target_metric": "salt_spray_hours", "target_value": 300.0,
               "direction": "maximize", "units": {"thickness": "mm", "cure_temp_c": "degC", "salt_spray_hours": "h"}}

    def test_units_must_be_confirmed_or_stated(self):
        org_id, client, case_id = self.setup_case("UnitReq")
        content = make_csv(ten())
        p = self.preview_errors(client, case_id, content, {})
        self.assertFalse(p["ok"])
        self.assertEqual(sorted(e["column"] for e in p["errors"]), sorted(FEATURES))
        self.assertTrue(all("not stated" in e["message"] for e in p["errors"]))
        r = self.imp(client, case_id, content, {"confirm": True})
        self.assert_refused_nothing_stored(org_id, case_id, r, "Nothing was imported", "'fraction'")
        r = self.imp(client, case_id, content, {"confirm": True, "confirm_canonical_units": False})
        self.assert_refused_nothing_stored(org_id, case_id, r, "not stated")

    def test_incompatible_units_are_refused_never_guessed(self):
        org_id, client, case_id = self.setup_case("UnitBad")
        content = make_csv([("A", 0.1, 150), ("B", 0.12, 160)])
        # '%' and 'fraction' are different units and are never converted into each other
        r = self.imp(client, case_id, content, {**CONFIRM, "units": {"crosslinker_ratio": "%"}})
        self.assert_refused_nothing_stored(org_id, case_id, r, "cannot be safely converted")
        r = self.imp(client, case_id, content, {**CONFIRM, "units": {"cure_temp_c": "mm"}})
        self.assert_refused_nothing_stored(org_id, case_id, r, "cannot be safely converted")
        r = self.imp(client, case_id, content, {**CONFIRM, "units": {"colour": "x"}})
        self.assert_refused_nothing_stored(org_id, case_id, r, "not an input field")

    def test_explicit_units_are_converted_by_an_exact_factor_and_the_original_is_kept(self):
        org_id, client = self.org_and_client("UnitConv")
        case_id = self.make_case(client, self.SPEC_MM)
        content = make_csv([("A", 2.5, 150), ("B", 0.8, 160)], header=("candidate_name", "thickness", "cure_temp_c"))
        r = self.imp(client, case_id, content, {"confirm": True, "confirm_canonical_units": True,
                                               "units": {"thickness": "cm"}})
        self.assertEqual(r.status_code, 201, r.text)
        a = self.stored(org_id, case_id)[0]
        self.assertEqual(json.loads(a["properties_json"])["thickness"], 25.0)  # 2.5 cm -> 25 mm
        record = json.loads(a["input_json"])["thickness"]
        self.assertEqual((record["value"], record["unit"], record["converted_value"], record["converted_unit"]),
                         (2.5, "cm", 25.0, "mm"))


class TestNames(ImportApiBase):
    def test_duplicate_names_inside_the_file(self):
        org_id, client, case_id = self.setup_case("DupIn")
        rows = [("Alpha", 0.1, 150), ("beta", 0.11, 151), ("  ALPHA ", 0.12, 152), ("Beta", 0.13, 153)]
        p = self.preview_errors(client, case_id, make_csv(rows))
        msgs = {e["row"]: e["message"] for e in p["errors"]}
        self.assertEqual(sorted(msgs), [4, 5])
        self.assertIn("already on row 2", msgs[4])
        self.assertIn("already on row 3", msgs[5])
        r = self.imp(client, case_id, make_csv(rows), CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "Duplicate name")

    def test_duplicate_names_against_existing_candidates_removed_ones_included(self):
        org_id, client, case_id = self.setup_case("DupEx")
        self.add(client, case_id, "Cand 03", {"crosslinker_ratio": 0.1, "cure_temp_c": 150})
        gone = self.add(client, case_id, "Cand 04", {"crosslinker_ratio": 0.11, "cure_temp_c": 151})
        # a candidate nothing depends on is deleted outright; an archived one still keeps its name
        from backend.app.repositories import candidate_repo
        self.assertTrue(candidate_repo.set_candidate_archived(org_id, gone, True))
        r = self.imp(client, case_id, make_csv(ten()), CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "already exists", before=2)

    def test_repeated_submission_is_safe(self):
        org_id, client, case_id = self.setup_case("Twice")
        content = make_csv(ten())
        self.assertEqual(self.imp(client, case_id, content, CONFIRM).status_code, 201)
        again = self.imp(client, case_id, content, CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, again, "already exists", before=10)
        self.assertEqual(len(self.stored(org_id, case_id)), 10, "no duplicates from a double submit")

    def test_import_requires_explicit_confirmation(self):
        org_id, client, case_id = self.setup_case("NoConf")
        content = make_csv(ten())
        for opts in ({"confirm_canonical_units": True}, {**PREVIEW_OK, "confirm": "true"}, {**PREVIEW_OK, "confirm": 1},
                     {**PREVIEW_OK, "confirm": False}):
            r = self.imp(client, case_id, content, opts)
            self.assert_refused_nothing_stored(org_id, case_id, r, "not confirmed")
        r = self.imp(client, case_id, content, None)
        self.assert_refused_nothing_stored(org_id, case_id, r, "not confirmed")


class TestDiagnosticState(ImportApiBase):
    def test_archived_diagnostic_refuses_import_and_preview_says_why(self):
        org_id, client, case_id = self.setup_case("Arch")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 200)
        content = make_csv(ten())
        p = self.preview_errors(client, case_id, content)
        self.assertFalse(p["ok"])
        self.assertIn("archived", p["blocked_reason"])
        r = self.imp(client, case_id, content, CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "archived")

    def test_completed_diagnostic_refuses_import_and_preview_says_why(self):
        from backend.app.repositories import change_case_repo
        org_id, client, case_id = self.setup_case("Done")
        self.assertTrue(change_case_repo.update_status(org_id, case_id, "completed"))
        content = make_csv(ten())
        p = self.preview_errors(client, case_id, content)
        self.assertFalse(p["ok"])
        self.assertIn("completed", p["blocked_reason"])
        r = self.imp(client, case_id, content, CONFIRM)
        self.assert_refused_nothing_stored(org_id, case_id, r, "completed")

    def test_active_diagnostic_accepts_import(self):
        org_id, client, case_id = self.setup_case("Act")
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"}).status_code, 200)
        self.assertEqual(self.imp(client, case_id, make_csv(ten()), CONFIRM).status_code, 201)

    def test_organization_isolation_and_authentication(self):
        org_a, client_a, case_a = self.setup_case("OrgA")
        org_b, client_b = self.org_and_client("OrgB")
        content = make_csv(ten())
        for preview in (True, False):
            r = self.imp(client_b, case_a, content, CONFIRM, preview=preview)
            self.assertEqual(r.status_code, 404, (preview, r.text[:200]))
        self.assertEqual(self.stored(org_a, case_a), [], "another organization must not write into this diagnostic")
        anonymous = self.client_factory()
        for preview in (True, False):
            r = self.imp(anonymous, case_a, content, CONFIRM, preview=preview)
            self.assertIn(r.status_code, (401, 403), (preview, r.status_code))
        self.assertEqual(self.stored(org_a, case_a), [])
        # the owner is unaffected, and a name used in org A does not collide in org B's own diagnostic
        self.assertEqual(self.imp(client_a, case_a, content, CONFIRM).status_code, 201)
        case_b = self.make_case(client_b, SPEC)
        self.assertEqual(self.imp(client_b, case_b, content, CONFIRM).status_code, 201)
        self.assertEqual(len(self.stored(org_a, case_a)), 10)
        self.assertEqual(len(self.stored(org_b, case_b)), 10)


class TestAtomicity(ImportApiBase):
    def test_database_failure_part_way_leaves_nothing(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id = self.setup_case("DbFail")
        real, calls = candidate_repo.now_iso, {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("simulated database failure on the 4th row")
            return real()

        candidate_repo.now_iso = flaky
        try:
            try:
                r = self.imp(client, case_id, make_csv(ten()), CONFIRM)
                self.assertGreaterEqual(r.status_code, 500, r.text[:200])
            except RuntimeError as exc:  # the test client re-raises server errors
                self.assertIn("simulated", str(exc))
        finally:
            candidate_repo.now_iso = real
        self.assertGreaterEqual(calls["n"], 4, "the failure must have happened part-way through the batch")
        self.assertEqual(self.stored(org_id, case_id), [], "a failed batch must leave no partial rows")
        # and the diagnostic is still fully usable afterwards
        self.assertEqual(self.imp(client, case_id, make_csv(ten()), CONFIRM).status_code, 201)
        self.assertEqual(len(self.stored(org_id, case_id)), 10)

    def test_name_taken_between_validation_and_insert_still_stops_the_whole_batch(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id = self.setup_case("Race")
        real = candidate_repo.create_candidates_batch

        def interloper_first(organization_id, change_case_id, entries, guard):
            candidate_repo.create_candidate(organization_id, change_case_id, "cand 05",
                                            {"crosslinker_ratio": 0.1, "cure_temp_c": 150.0})
            return real(organization_id, change_case_id, entries, guard)

        candidate_repo.create_candidates_batch = interloper_first
        try:
            r = self.imp(client, case_id, make_csv(ten()), CONFIRM)
        finally:
            candidate_repo.create_candidates_batch = real
        self.assert_refused_nothing_stored(org_id, case_id, r, "already exists", before=1)
        self.assertEqual([c["candidate_name"] for c in self.stored(org_id, case_id)], ["cand 05"],
                         "only the interloper exists; none of the ten were stored")

    def test_diagnostic_archived_between_validation_and_insert_stops_the_batch(self):
        from backend.app.repositories import candidate_repo, change_case_repo
        org_id, client, case_id = self.setup_case("Race2")
        real = candidate_repo.create_candidates_batch

        def archive_first(organization_id, change_case_id, entries, guard):
            change_case_repo.set_archived(organization_id, change_case_id, True)
            return real(organization_id, change_case_id, entries, guard)

        candidate_repo.create_candidates_batch = archive_first
        try:
            r = self.imp(client, case_id, make_csv(ten()), CONFIRM)
        finally:
            candidate_repo.create_candidates_batch = real
        self.assert_refused_nothing_stored(org_id, case_id, r, "archived")


class TestStaleRankingAfterImport(ImportApiBase):
    HISTORY = F.base_rows(14)

    def _ranked_case(self, label):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        up = self.upload(client, case_id, "history.csv", F.csv_bytes(F.CANON_HEADERS, self.HISTORY))
        self.assertEqual(up.status_code, 201, up.text)
        self.add(client, case_id, "InA", {"crosslinker_ratio": 0.12, "cure_temp_c": 158})
        self.add(client, case_id, "InB", {"crosslinker_ratio": 0.10, "cure_temp_c": 150})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)
        return org_id, client, case_id

    def _listing(self, client, case_id):
        return {c["candidate_name"]: c for c in client.get(f"/api/change-cases/{case_id}/candidates").json()}

    def test_import_after_ranking_marks_the_ranking_out_of_date_and_the_report_is_refused(self):
        org_id, client, case_id = self._ranked_case("Stale")
        before = self._listing(client, case_id)
        self.assertTrue(all(c["rank"] is not None and not c["ranking_outdated"] for c in before.values()))
        predictions_before = self.predictions(org_id, case_id)

        rows = [("New 1", 0.13, 165), ("New 2", 0.11, 155), ("New 3", 0.09, 146)]
        r = self.imp(client, case_id, make_csv(rows), CONFIRM)
        self.assertEqual(r.status_code, 201, r.text)

        after = self._listing(client, case_id)
        self.assertEqual(len(after), 5)
        for name in ("InA", "InB"):
            self.assertTrue(after[name]["ranking_outdated"], name)
            self.assertIsNone(after[name]["rank"], "the old position must be withheld, not shown as current")
        for name in ("New 1", "New 2", "New 3"):
            self.assertIsNone(after[name]["latest_prediction"])
            self.assertIsNone(after[name]["rank"])
        self.assertEqual(self.predictions(org_id, case_id), predictions_before, "import must not rank anything")
        report = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(report.status_code, 400, "no report may present the old ranking as current")
        self.assertIn("out of date", report.json()["error"])
        self.assertIn("Re-run ranking", report.json()["error"])

        # running ranking again covers everyone and a current report is possible again
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        now = self._listing(client, case_id)
        self.assertFalse(any(c["ranking_outdated"] for c in now.values()))
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)


class TestSizeLimits(ImportApiBase):
    LIMIT = 5000

    def setUp(self):
        from backend.app.config.settings import settings
        self._old = settings.MAX_UPLOAD_BYTES
        settings.MAX_UPLOAD_BYTES = self.LIMIT

    def tearDown(self):
        from backend.app.config.settings import settings
        settings.MAX_UPLOAD_BYTES = self._old

    def _big(self):
        return b"candidate_name,crosslinker_ratio,cure_temp_c\n" + \
            b"x,0.1,150\n" * ((self.LIMIT + upload_limits.MULTIPART_OVERHEAD_BYTES) // 10 + 100)

    def test_oversized_file_is_refused_with_413_on_both_endpoints_and_nothing_is_stored(self):
        org_id, client, case_id = self.setup_case("Big")
        for preview in (True, False):
            r = self.imp(client, case_id, self._big(), CONFIRM, preview=preview)
            self.assertEqual(r.status_code, 413, (preview, r.text[:200]))
            self.assertIn("too large", r.json()["error"])
        self.assertEqual(self.stored(org_id, case_id), [])

    def test_file_just_under_the_limit_still_imports(self):
        org_id, client, case_id = self.setup_case("Small")
        self.assertEqual(self.imp(client, case_id, make_csv(ten()), CONFIRM).status_code, 201)

    def test_route_level_guard_alone_also_refuses_oversize(self):
        # a file inside the middleware's framing allowance but over the limit is stopped by read_upload_limited
        org_id, client, case_id = self.setup_case("Mid")
        content = b"candidate_name,crosslinker_ratio,cure_temp_c\n" + b"x,0.1,150\n" * ((self.LIMIT // 10) + 50)
        r = self.imp(client, case_id, content, CONFIRM)
        self.assertIn(r.status_code, (400, 413), r.text[:200])
        self.assertIn("too large", r.json()["error"])
        self.assertEqual(self.stored(org_id, case_id), [])


if __name__ == "__main__":
    unittest.main()
