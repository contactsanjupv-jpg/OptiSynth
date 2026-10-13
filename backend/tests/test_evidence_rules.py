"""
B2 (column mapping) + B3 (units / test conditions / conflicts) -- pure rule tests.
No database, no files. Run:  python3 -m unittest backend.tests.test_evidence_rules -v

Principle under test: when comparability cannot be established the gate BLOCKS
(a flag with severity "blocking" and NO rows returned) -- it never guesses.
"""
import csv
import io
import unittest

from backend.app.services import evidence_rules as er

SPEC = {"feature_columns": ["crosslinker_ratio", "cure_temp_c"], "target_metric": "salt_spray_hours"}
SPEC_H = {**SPEC, "units": {"salt_spray_hours": "h"}}
CANON = ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"]


def base_rows(n=12):
    return [(round(0.08 + 0.005 * i, 4), 140 + 3 * i, 100 + 40 * i) for i in range(n)]


def make_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def run(spec, text, options=None):
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in reader.fieldnames]
    reader.fieldnames = headers
    rows = list(reader)
    return er.analyze_rows(spec, headers, rows, options)


def codes(review, severity=None):
    return {f["code"] for f in review["flags"] if severity is None or f["severity"] == severity}


class TestParseQuantity(unittest.TestCase):
    def test_plain_and_scientific_numbers(self):
        self.assertEqual(er.parse_quantity("540"), (er.PARSE_OK, 540.0, None))
        self.assertEqual(er.parse_quantity(" 1e3 "), (er.PARSE_OK, 1000.0, None))

    def test_number_with_unit(self):
        self.assertEqual(er.parse_quantity("540 h"), (er.PARSE_OK, 540.0, "h"))
        self.assertEqual(er.parse_quantity("24h"), (er.PARSE_OK, 24.0, "h"))
        self.assertEqual(er.parse_quantity("12 mPa·s")[2], "mPa·s")
        self.assertEqual(er.parse_quantity("25 °C")[2], "°C")
        self.assertEqual(er.parse_quantity("5%")[2], "%")

    def test_non_numbers(self):
        for cell in ("", "   ", "abc", "h 540", None, "--"):
            self.assertEqual(er.parse_quantity(cell)[0], er.PARSE_NONE, repr(cell))

    def test_comma_numbers_are_ambiguous_never_interpreted(self):
        for cell in ("0,085", "1,234", "1,234.5", "12,5"):
            self.assertEqual(er.parse_quantity(cell)[0], er.PARSE_AMBIGUOUS, cell)

    def test_non_finite_words_parse_as_non_finite_floats(self):
        for cell in ("nan", "inf", "-Infinity"):
            status, value, _ = er.parse_quantity(cell)
            self.assertEqual(status, er.PARSE_OK)
            self.assertFalse(value == value and abs(value) != float("inf"))


class TestUnitConversion(unittest.TestCase):
    def test_equivalent_time_units(self):
        for v, u in ((24, "h"), (1440, "min"), (1, "day"), (86400, "s"), (1, "d")):
            ok, out = er.convert_value(v, u, "h")
            self.assertTrue(ok, u)
            self.assertEqual(out, 24 if u != "h" or v == 24 else out)
        self.assertEqual(er.convert_value(1440, "min", "h"), (True, 24.0))
        self.assertEqual(er.convert_value(1, "day", "h"), (True, 24.0))

    def test_viscosity_cp_equals_mpa_s_exactly(self):
        self.assertEqual(er.convert_value(142, "cP", "mPa·s"), (True, 142.0))
        self.assertEqual(er.convert_value(1, "Pa.s", "cP"), (True, 1000.0))

    def test_no_float_noise_for_mil_to_um(self):
        ok, out = er.convert_value(1, "mil", "um")
        self.assertTrue(ok)
        self.assertEqual(out, 25.4)

    def test_different_dimensions_never_convert(self):
        for a, b in (("h", "%"), ("cP", "h"), ("mm", "min"), ("kg", "h")):
            self.assertEqual(er.convert_value(1, a, b), (False, None), (a, b))

    def test_unknown_units_equal_only_when_identical(self):
        # Unit symbols are case-sensitive, so an unrecognised unit equals only the SAME spelling.
        # (This used to assert that 'widgets' == 'Widgets'; letter case can change what a unit
        # means -- MPa vs mPa -- so that equivalence was removed deliberately.)
        self.assertEqual(er.convert_value(3, "widgets", "widgets"), (True, 3))
        self.assertEqual(er.convert_value(3, "widgets", "Widgets"), (False, None))
        self.assertEqual(er.convert_value(3, "widgets", "gadgets"), (False, None))

    def test_temperature_scales_are_not_converted(self):
        self.assertEqual(er.convert_value(25, "°C", "degC"), (True, 25))
        self.assertEqual(er.convert_value(25, "K", "degC"), (False, None))
        self.assertEqual(er.convert_value(77, "°F", "degC"), (False, None))

    def test_missing_unit_never_converts(self):
        self.assertEqual(er.convert_value(1, None, "h"), (False, None))


class TestColumnMapping(unittest.TestCase):
    def test_canonical_header_accepted_unaided(self):
        review, rows, errors = run(SPEC, make_csv(CANON, base_rows()))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(i["method"] == "exact" for i in review["column_mapping"].values()))
        self.assertEqual(set(rows[0]["features"]), {"crosslinker_ratio", "cure_temp_c"})

    def test_known_alias_is_proposed_but_not_used_until_confirmed(self):
        header = ["Crosslinker Ratio", "Cure Temp (C)", "Salt Spray (h)"]
        review, rows, _ = run(SPEC, make_csv(header, base_rows()))
        self.assertEqual(rows, [])  # nothing reaches the model
        self.assertEqual(codes(review, "blocking"), {"MAPPING_NOT_CONFIRMED"})
        self.assertEqual(review["proposed_mapping"], {
            "crosslinker_ratio": "Crosslinker Ratio", "cure_temp_c": "Cure Temp (C)",
            "salt_spray_hours": "Salt Spray (h)"})

    def test_confirmed_mapping_ingests_canonical_names(self):
        header = ["Crosslinker Ratio", "Cure Temp (C)", "Salt Spray (h)"]
        review, rows, _ = run(SPEC, make_csv(header, base_rows()), {"column_mapping": {
            "crosslinker_ratio": "Crosslinker Ratio", "cure_temp_c": "Cure Temp (C)",
            "salt_spray_hours": "Salt Spray (h)"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        self.assertEqual(set(rows[0]["features"]), {"crosslinker_ratio", "cure_temp_c"})
        self.assertEqual(rows[3]["target_value"], 220)
        self.assertTrue(all(i["method"] == "confirmed_mapping" for i in review["column_mapping"].values()))

    def test_ambiguous_header_is_never_guessed(self):
        header = ["Crosslinker Ratio", "crosslinker-ratio", "cure_temp_c", "salt_spray_hours"]
        rows_in = [(a, a, b, c) for a, b, c in base_rows()]
        review, rows, _ = run(SPEC, make_csv(header, rows_in))
        self.assertEqual(rows, [])
        self.assertIn("AMBIGUOUS_COLUMN_MAPPING", codes(review, "blocking"))
        msg = next(f["message"] for f in review["flags"] if f["code"] == "AMBIGUOUS_COLUMN_MAPPING")
        self.assertIn("Crosslinker Ratio", msg)
        self.assertIn("crosslinker-ratio", msg)
        self.assertEqual(review["proposed_mapping"].get("crosslinker_ratio"), None)  # no single proposal

    def test_ambiguity_resolved_only_by_explicit_mapping(self):
        header = ["Crosslinker Ratio", "crosslinker-ratio", "cure_temp_c", "salt_spray_hours"]
        rows_in = [(a, a, b, c) for a, b, c in base_rows()]
        review, rows, _ = run(SPEC, make_csv(header, rows_in),
                              {"column_mapping": {"crosslinker_ratio": "crosslinker-ratio"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)

    def test_duplicate_header_blocks(self):
        text = "crosslinker_ratio,cure_temp_c,salt_spray_hours,salt_spray_hours\n0.1,150,100,100\n"
        review, rows, _ = run(SPEC, text)
        self.assertIn("DUPLICATE_HEADER", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_unknown_headers_block_with_nothing_proposed(self):
        review, rows, _ = run(SPEC, make_csv(["foo", "bar", "baz"], base_rows()))
        self.assertEqual(rows, [])
        self.assertEqual(codes(review, "blocking"), {"MISSING_REQUIRED_COLUMN"})
        self.assertEqual(review["proposed_mapping"], {})

    def test_missing_required_field_blocks(self):
        review, rows, _ = run(SPEC, make_csv(["crosslinker_ratio", "cure_temp_c"], [(r[0], r[1]) for r in base_rows()]))
        self.assertEqual(rows, [])
        self.assertIn("MISSING_REQUIRED_COLUMN", codes(review, "blocking"))
        self.assertTrue(any(f.get("column") == "salt_spray_hours" for f in review["flags"]))

    def test_mapping_to_nonexistent_header_blocks(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"column_mapping": {"cure_temp_c": "Nope"}})
        self.assertIn("INVALID_MAPPING", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_mapping_two_columns_to_one_header_blocks(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"column_mapping": {
            "crosslinker_ratio": "cure_temp_c"}})
        self.assertIn("INVALID_MAPPING", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_mapping_for_non_required_column_blocks(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"column_mapping": {"colour": "cure_temp_c"}})
        self.assertIn("INVALID_MAPPING", codes(review, "blocking"))

    def test_extra_irrelevant_column_is_ignored_safely_and_reported(self):
        header = CANON + ["operator_notes"]
        review, rows, _ = run(SPEC, make_csv(header, [r + ("fine",) for r in base_rows()]))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        self.assertNotIn("operator_notes", rows[0]["features"])
        self.assertIn("operator_notes", review["ignored_columns"])
        self.assertIn("COLUMNS_IGNORED", codes(review, "warning"))
        self.assertNotIn("POSSIBLE_UNIT_OR_CONDITION_COLUMN_IGNORED", codes(review))

    def test_ignored_column_that_looks_like_unit_or_method_is_flagged(self):
        header = CANON + ["Test Method", "unit"]
        review, rows, _ = run(SPEC, make_csv(header, [r + ("B117", "h") for r in base_rows()]))
        flagged = {f["column"] for f in review["flags"] if f["code"] == "POSSIBLE_UNIT_OR_CONDITION_COLUMN_IGNORED"}
        self.assertEqual(flagged, {"Test Method", "unit"})

    def test_provenance_columns_recognised_and_not_modelled(self):
        header = CANON + ["source_document", "source_page"]
        review, rows, _ = run(SPEC, make_csv(header, [r + ("Report 12", 4) for r in base_rows()]))
        self.assertEqual(review["provenance_columns"], ["source_document", "source_page"])
        self.assertNotIn("NO_PROVENANCE_COLUMNS", codes(review))
        self.assertNotIn("source_page", rows[0]["features"])
        self.assertEqual(set(rows[0]), {"features", "target_value"})

    def test_no_provenance_columns_is_a_warning_not_a_block(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()))
        self.assertIn("NO_PROVENANCE_COLUMNS", codes(review, "warning"))
        self.assertEqual(er.blocking_flags(review["flags"]), [])

    def test_unknown_option_key_is_rejected_not_ignored(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"colunm_mapping": {}})
        self.assertIn("INVALID_OPTIONS", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_spec_extension_validation(self):
        self.assertIsNone(er.validate_spec_extensions(SPEC))
        self.assertIsNone(er.validate_spec_extensions(SPEC_H))
        self.assertIsNotNone(er.validate_spec_extensions({**SPEC, "units": ["h"]}))
        self.assertIsNotNone(er.validate_spec_extensions({**SPEC, "units": {"nope": "h"}}))
        self.assertIsNotNone(er.validate_spec_extensions({**SPEC, "units": {"salt_spray_hours": ""}}))


class TestUnits(unittest.TestCase):
    def _target_cells(self, cells):
        return make_csv(CANON, [(a, b, c) for (a, b, _), c in zip(base_rows(len(cells)), cells)])

    def test_declared_unit_matching_canonical_passes_unchanged(self):
        text = self._target_cells([f"{100 + 40 * i} h" for i in range(12)])
        review, rows, _ = run(SPEC_H, text)
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual([r["target_value"] for r in rows], [100 + 40 * i for i in range(12)])
        self.assertEqual(review["units"]["salt_spray_hours"]["canonical"], "h")
        self.assertEqual(review["units"]["salt_spray_hours"]["rows_converted"], 0)

    def test_equivalent_units_are_converted_exactly_and_reported(self):
        cells = ["24 h", "1440 min", "1 day", "86400 s"] + [f"{100 + 40 * i} h" for i in range(4, 12)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual([r["target_value"] for r in rows[:4]], [24.0, 24.0, 24.0, 24.0])
        info = review["units"]["salt_spray_hours"]
        self.assertEqual(info["rows_converted"], 3)
        self.assertEqual(sorted((c["from"], c["rows"]) for c in info["conversions"]),
                         [("day", 1), ("min", 1), ("s", 1)])
        self.assertIn("UNIT_CONVERTED", codes(review, "warning"))  # never silent

    def test_incompatible_unit_blocks(self):
        cells = ["100 %", "140 h"] + [f"{180 + 40 * i} h" for i in range(10)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells))
        self.assertEqual(rows, [])
        self.assertIn("UNIT_INCOMPATIBLE", codes(review, "blocking"))
        f = next(f for f in review["flags"] if f["code"] == "UNIT_INCOMPATIBLE")
        self.assertEqual(f["rows"], [1])

    def test_unit_of_wrong_dimension_blocks_even_if_known(self):
        cells = [f"{100 + 40 * i} mm" for i in range(12)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells))
        self.assertEqual(rows, [])
        self.assertIn("UNIT_INCOMPATIBLE", codes(review, "blocking"))

    def test_missing_unit_where_required_blocks(self):
        cells = [f"{100 + 40 * i}" for i in range(12)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells))
        self.assertEqual(rows, [])
        self.assertIn("UNIT_MISSING", codes(review, "blocking"))

    def test_declared_source_unit_resolves_missing_unit_and_is_recorded(self):
        cells = [f"{100 + 40 * i}" for i in range(12)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells), {"declared_units": {"salt_spray_hours": "h"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        self.assertEqual(review["units"]["salt_spray_hours"]["declared_source_unit"], "h")

    def test_declared_source_unit_converts_when_different_from_canonical(self):
        cells = [f"{(100 + 40 * i) / 60}" for i in range(12)]  # minutes -> hours
        review, rows, _ = run(SPEC_H, self._target_cells(cells), {"declared_units": {"salt_spray_hours": "min"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertAlmostEqual(rows[0]["target_value"], 100 / 3600, places=9)

    def test_mixed_units_without_canonical_block_and_say_how_to_fix(self):
        cells = [f"{100 + 40 * i} h" for i in range(6)] + [f"{2 + i} day" for i in range(6)]
        review, rows, _ = run(SPEC, self._target_cells(cells))
        self.assertEqual(rows, [])
        f = next(f for f in review["flags"] if f["code"] == "UNIT_MIXED")
        self.assertIn("day", f["message"])
        self.assertIn("interconvertible", f["message"])  # hours/days CAN be made explicit via spec units

    def test_mixed_units_that_are_not_convertible_say_so(self):
        cells = [f"{100 + 40 * i} h" for i in range(6)] + [f"{2 + i} %" for i in range(6)]
        review, rows, _ = run(SPEC, self._target_cells(cells))
        f = next(f for f in review["flags"] if f["code"] == "UNIT_MIXED")
        self.assertIn("not interconvertible", f["message"])

    def test_some_rows_with_units_some_without_blocks_when_no_canonical(self):
        cells = [f"{100 + 40 * i} h" for i in range(6)] + [f"{100 + 40 * i}" for i in range(6, 12)]
        review, rows, _ = run(SPEC, self._target_cells(cells))
        self.assertEqual(rows, [])
        self.assertIn("UNIT_MISSING", codes(review, "blocking"))

    def test_entirely_unitless_column_without_canonical_is_allowed_but_flagged(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()))
        self.assertEqual(len(rows), 12)
        flagged = {f["column"] for f in review["flags"] if f["code"] == "UNITS_NOT_DECLARED"}
        self.assertEqual(flagged, set(CANON))

    def test_feature_units_are_enforced_too(self):
        spec = {**SPEC, "units": {"cure_temp_c": "degC"}}
        rows_in = [(a, f"{b} K", c) for a, b, c in base_rows()]
        review, rows, _ = run(spec, make_csv(CANON, rows_in))
        self.assertEqual(rows, [])
        self.assertIn("UNIT_INCOMPATIBLE", codes(review, "blocking"))

    def test_viscosity_cp_and_mpa_s_are_equivalent_with_explicit_canonical(self):
        spec = {"feature_columns": ["visc"], "target_metric": "y", "units": {"visc": "mPa·s"}}
        rows_in = [(f"{100 + i} cP" if i % 2 else f"{100 + i} mPa·s", 10 + i) for i in range(12)]
        review, rows, _ = run(spec, make_csv(["visc", "y"], rows_in))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual([r["features"]["visc"] for r in rows], [100.0 + i for i in range(12)])

    def test_blocked_unit_problem_returns_no_rows_at_all(self):
        cells = ["100 %"] + [f"{100 + 40 * i} h" for i in range(1, 12)]
        review, rows, _ = run(SPEC_H, self._target_cells(cells))
        self.assertEqual(rows, [])  # the 11 good rows are NOT partially admitted


class TestUnitCaseSensitivity(unittest.TestCase):
    """Unit symbols are case-sensitive: letter case changes the physical meaning, so two units that
    differ only by case must never be treated as equal or converted into each other. Supported
    conversions are unchanged; everything unsupported fails safely."""

    # Pairs that the old lower-casing logic treated as IDENTICAL (factor 1), several of them
    # nine orders of magnitude apart.
    CASE_ONLY_PAIRS = [
        ("MPa.s", "mPa.s"), ("MPa\u00b7s", "cP"), ("Mm", "mm"), ("mm", "MM"), ("S", "s"), ("Min", "min"),
        ("H", "h"), ("D", "d"), ("CP", "cP"), ("Cm", "cm"), ("PA.S", "Pa.s"), ("NM", "nm"), ("UM", "um"),
        ("HR", "hr"), ("Sec", "sec"), ("M", "m"),
    ]

    def test_case_only_differences_are_never_equal_or_converted(self):
        for a, b in self.CASE_ONLY_PAIRS:
            self.assertEqual(er.convert_value(1, a, b), (False, None), (a, b))
            self.assertEqual(er.convert_value(1, b, a), (False, None), (b, a))
            self.assertFalse(er.units_convertible(a, b), (a, b))

    def test_mega_and_milli_are_not_the_same_unit(self):
        self.assertEqual(er.convert_value(1, "MPa.s", "mPa.s"), (False, None))
        self.assertEqual(er.convert_value(1, "MPa\u00b7s", "cP"), (False, None))
        self.assertEqual(er.convert_value(1, "Mm", "mm"), (False, None))
        self.assertNotEqual(er.normalize_unit("MPa"), er.normalize_unit("mPa"))

    def test_supported_spellings_still_convert_exactly(self):
        cases = [
            (1, "mPa.s", "cP", 1.0), (1, "mPa\u00b7s", "cP", 1.0), (142, "cP", "mPa\u00b7s", 142.0),
            (1, "Pa.s", "cP", 1000.0), (1, "P", "cP", 100.0), (1, "Pa.s", "mPa.s", 1000.0),
            (1, "mil", "um", 25.4), (1, "cm", "mm", 10.0), (1, "mm", "cm", 0.1), (1, "m", "mm", 1000.0),
            (1, "um", "nm", 1000.0), (1, "day", "h", 24.0), (1, "d", "h", 24.0), (1440, "min", "h", 24.0),
            (3600, "s", "h", 1.0), (60, "sec", "min", 1.0), (2, "hr", "min", 120.0), (25, "\u00b0C", "degC", 25),
            (25, "C", "degC", 25),
        ]
        for v, a, b, expected in cases:
            ok, out = er.convert_value(v, a, b)
            self.assertTrue(ok, (a, b))
            self.assertAlmostEqual(out, expected, places=9, msg=(v, a, b))

    def test_structural_spellings_of_a_supported_symbol_are_accepted(self):
        # degree sign, micro sign, middle dot and spaces are punctuation, not case
        self.assertEqual(er.convert_value(5, "\u00b0C", "degC"), (True, 5))
        self.assertEqual(er.convert_value(1, "\u00b5m", "um"), (True, 1))   # micro sign U+00B5
        self.assertEqual(er.convert_value(1, "\u03bcm", "um"), (True, 1))   # Greek mu U+03BC
        self.assertEqual(er.convert_value(1, " mPa \u00b7 s ", "cP"), (True, 1.0))   # inner spaces are removed
        self.assertEqual(er.convert_value(1, "mPa*s", "cP"), (True, 1.0))

    def test_whole_words_match_ignoring_case(self):
        for word, expected in (("Hours", 1.0), ("HOURS", 1.0), ("Hour", 1.0), ("Days", 24.0), ("DAY", 24.0)):
            self.assertEqual(er.convert_value(1, word, "h"), (True, expected), word)
        self.assertEqual(er.convert_value(1, "Micron", "um"), (True, 1.0))
        self.assertEqual(er.convert_value(1, "MICRONS", "um"), (True, 1.0))
        self.assertEqual(er.convert_value(1, "Centipoise", "cP"), (True, 1.0))
        self.assertEqual(er.convert_value(1, "Poise", "cP"), (True, 100.0))
        self.assertEqual(er.normalize_unit("Hours"), "hours")
        self.assertEqual(er.normalize_unit("HOURS"), er.normalize_unit("hours"))

    def test_lowercase_spellings_that_cannot_mean_anything_else_stay_supported(self):
        self.assertEqual(er.convert_value(1, "cp", "cP"), (True, 1.0))
        self.assertEqual(er.convert_value(1, "pa.s", "cP"), (True, 1000.0))
        self.assertEqual(er.convert_value(1, "degc", "degC"), (True, 1))

    def test_lowercase_spellings_that_could_mean_something_else_are_refused(self):
        # 'mpa.s' could be a lower-cased MPa.s; 'p' is pico, not poise; 'c' is not the Celsius symbol.
        for u, to in (("mpa.s", "cP"), ("mpa", "cP"), ("p", "cP"), ("c", "degC")):
            self.assertEqual(er.convert_value(1, u, to), (False, None), u)

    def test_unsupported_units_and_dimension_mismatches_fail_safely(self):
        for a, b in (("K", "degC"), ("\u00b0F", "degC"), ("kg", "g"), ("mg", "g"), ("wt%", "%"), ("ppm", "%"),
                     ("MPa", "psi"), ("bar", "kPa"), ("in", "mm"), ("ft", "m"), ("mL", "L"), ("g/cm3", "kg/m3"),
                     ("ms", "s"), ("h", "%"), ("cP", "h"), ("mm", "min"), ("cSt", "cP")):
            self.assertEqual(er.convert_value(1, a, b), (False, None), (a, b))

    def test_unrecognised_units_match_only_the_identical_spelling(self):
        self.assertEqual(er.convert_value(7, "MPa", "MPa"), (True, 7))
        self.assertEqual(er.convert_value(7, "psi", "psi"), (True, 7))
        self.assertEqual(er.convert_value(7, "MPa", "mPa"), (False, None))
        self.assertEqual(er.convert_value(7, "MPa", "psi"), (False, None))
        self.assertEqual(er.convert_value(7, "MPa", "kPa"), (False, None))

    def test_missing_or_blank_units_never_convert(self):
        for a, b in ((None, "h"), ("h", None), ("", "h"), ("  ", "h"), (None, None)):
            self.assertEqual(er.convert_value(1, a, b), (False, None), (a, b))

    def test_normalize_unit_preserves_case_and_cleans_structure(self):
        self.assertIsNone(er.normalize_unit(None))
        self.assertIsNone(er.normalize_unit("   "))
        self.assertEqual(er.normalize_unit(" \u00b0C "), "degC")
        self.assertEqual(er.normalize_unit("mPa \u00b7 s"), "mPa.s")
        self.assertEqual(er.normalize_unit("MPa"), "MPa")
        self.assertEqual(er.normalize_unit("mPa"), "mPa")
        self.assertEqual(er.normalize_unit("Mm"), "Mm")

    def test_case_only_alternatives_and_hint_name_the_difference(self):
        self.assertEqual(er.case_only_alternatives("MPa.s"), ["mPa.s"])
        self.assertEqual(er.case_only_alternatives("Mm"), ["mm"])
        self.assertEqual(er.case_only_alternatives("CP"), ["cP", "cp"])
        self.assertEqual(er.case_only_alternatives("mm"), [])        # supported itself
        self.assertEqual(er.case_only_alternatives("widgets"), [])   # no supported neighbour
        hint = er.unit_problem_hint("Mm", "mm")
        self.assertIn("case-sensitive", hint)
        self.assertIn("'Mm'", hint)
        self.assertIn("'mm'", hint)
        self.assertEqual(er.unit_problem_hint("kg", "g"), "")                # unsupported, not a case problem
        self.assertEqual(er.unit_problem_hint("cp", "cP"), "")               # accepted spelling: nothing to explain
        self.assertIn("'MPa'", er.unit_problem_hint("mPa", "MPa"))           # case near-miss of the case's own unit

    # ---- through the evidence gate (historical data) -------------------------------------
    @staticmethod
    def _visc_rows(units, values=None):
        values = values or [100 + i for i in range(len(units))]
        return [(f"{v} {u}", 10 + i) for i, (v, u) in enumerate(zip(values, units))]

    def _run_visc(self, spec_unit, units, values=None):
        spec = {"feature_columns": ["visc"], "target_metric": "y"}
        if spec_unit:
            spec["units"] = {"visc": spec_unit}
        return run(spec, make_csv(["visc", "y"], self._visc_rows(units, values)))

    def test_column_mixing_MPa_and_mPa_is_blocked_not_merged(self):
        review, rows, _ = self._run_visc(None, ["MPa"] * 6 + ["mPa"] * 6)
        self.assertEqual(rows, [])
        f = next(f for f in review["flags"] if f["code"] == "UNIT_MIXED")
        self.assertEqual(f["severity"], "blocking")
        self.assertIn("MPa", f["message"])
        self.assertIn("mPa", f["message"])
        self.assertIn("not interconvertible", f["message"])
        self.assertIn("differ only by letter case", f["message"])

    def test_column_mixing_Mm_and_mm_is_blocked(self):
        review, rows, _ = self._run_visc(None, ["Mm"] * 6 + ["mm"] * 6)
        self.assertEqual(rows, [])
        self.assertIn("UNIT_MIXED", codes(review, "blocking"))

    def test_column_mixing_case_variants_of_a_supported_unit_is_blocked(self):
        review, rows, _ = self._run_visc(None, ["h"] * 6 + ["H"] * 6)
        self.assertEqual(rows, [])
        self.assertIn("UNIT_MIXED", codes(review, "blocking"))

    def test_MPa_s_cell_is_not_silently_accepted_as_centipoise(self):
        review, rows, _ = self._run_visc("cP", ["cP"] * 11 + ["MPa\u00b7s"])
        self.assertEqual(rows, [])  # nothing partially admitted
        f = next(f for f in review["flags"] if f["code"] == "UNIT_INCOMPATIBLE")
        self.assertEqual(f["rows"], [12])
        self.assertIn("cannot be safely converted", f["message"])
        self.assertIn("case-sensitive", f["message"])

    def test_lowercased_mpa_s_cell_is_refused_against_a_viscosity_unit(self):
        review, rows, _ = self._run_visc("mPa\u00b7s", ["mPa\u00b7s"] * 11 + ["mpa.s"])
        self.assertEqual(rows, [])
        self.assertIn("UNIT_INCOMPATIBLE", codes(review, "blocking"))

    def test_canonical_unit_that_is_unrecognised_matches_only_exactly(self):
        review, rows, _ = self._run_visc("MPa", ["MPa"] * 12)
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        review, rows, _ = self._run_visc("MPa", ["MPa"] * 11 + ["mPa"])
        self.assertEqual(rows, [])
        f = next(f for f in review["flags"] if f["code"] == "UNIT_INCOMPATIBLE")
        self.assertIn("differs from 'MPa' only by letter case", f["message"])

    def test_supported_viscosity_conversions_through_the_gate_are_exact(self):
        review, rows, _ = self._run_visc("mPa\u00b7s", ["cP", "mPa\u00b7s", "Pa\u00b7s", "P", "Poise", "centipoise"] * 2,
                                         values=list(range(1, 13)))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual([r["features"]["visc"] for r in rows[:6]], [1.0, 2.0, 3000.0, 400.0, 500.0, 6.0])
        self.assertIn("UNIT_CONVERTED", codes(review, "warning"))

    def test_word_units_in_any_case_convert_through_the_gate(self):
        cells = ["24 Hours", "1 DAY", "1440 min"] + [f"{100 + 40 * i} h" for i in range(3, 12)]
        spec = SPEC_H
        text = make_csv(CANON, [(a, b, c) for (a, b, _), c in zip(base_rows(12), cells)])
        review, rows, _ = run(spec, text)
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual([r["target_value"] for r in rows[:3]], [24.0, 24.0, 24.0])

    def test_unit_case_difference_in_a_declared_source_unit_is_refused(self):
        cells = [f"{100 + 40 * i}" for i in range(12)]
        text = make_csv(CANON, [(a, b, c) for (a, b, _), c in zip(base_rows(12), cells)])
        review, rows, _ = run(SPEC_H, text, {"declared_units": {"salt_spray_hours": "H"}})
        self.assertEqual(rows, [])
        self.assertIn("UNIT_INCOMPATIBLE", codes(review, "blocking"))

    def test_condition_units_are_case_sensitive_too(self):
        header = CANON + ["Test Temp"]
        rows_in = [r + (t,) for r, t in zip(base_rows(12), ["25 \u00b0C", "25 C", "25 degC"] * 4)]
        review, rows, _ = run(SPEC, make_csv(header, rows_in), {"condition_columns": ["Test Temp"]})
        self.assertEqual(er.blocking_flags(review["flags"]), [])  # three spellings of one supported unit
        rows_in = [r + (t,) for r, t in zip(base_rows(12), ["25 C"] * 6 + ["25 c"] * 6)]
        review, rows, _ = run(SPEC, make_csv(header, rows_in), {"condition_columns": ["Test Temp"]})
        self.assertIn("CONDITIONS_DIFFER", codes(review, "blocking"))  # 'c' is not assumed to be 'C'

    # ---- through candidate entry ------------------------------------------------------------
    def test_candidate_entered_in_MPa_s_is_refused_against_cP(self):
        spec = {"feature_columns": ["visc"], "target_metric": "y", "units": {"visc": "cP"}}
        out, record, problems = er.convert_candidate_inputs(spec, {"visc": 5.0}, {"visc": "MPa\u00b7s"})
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot be safely converted", problems[0])
        self.assertIn("case-sensitive", problems[0])
        self.assertNotIn("converted_value", record["visc"])
        self.assertEqual(out["visc"], 5.0)  # untouched, and the caller refuses the candidate

    def test_candidate_entered_in_supported_units_converts_exactly(self):
        spec = {"feature_columns": ["visc", "t"], "target_metric": "y", "units": {"visc": "cP", "t": "h"}}
        out, record, problems = er.convert_candidate_inputs(
            spec, {"visc": 2.0, "t": 2.0}, {"visc": "Pa\u00b7s", "t": "Days"})
        self.assertEqual(problems, [])
        self.assertEqual((out["visc"], out["t"]), (2000.0, 48.0))
        self.assertEqual(record["visc"], {"value": 2.0, "unit": "Pa\u00b7s", "converted_value": 2000.0, "converted_unit": "cP"})

    def test_candidate_unit_differing_only_by_case_from_the_case_unit_is_refused(self):
        spec = {"feature_columns": ["d"], "target_metric": "y", "units": {"d": "mm"}}
        for bad in ("Mm", "MM"):
            _, _, problems = er.convert_candidate_inputs(spec, {"d": 1.0}, {"d": bad})
            self.assertEqual(len(problems), 1, bad)
            self.assertIn("case-sensitive", problems[0])
        _, _, problems = er.convert_candidate_inputs(spec, {"d": 1.0}, {"d": "mm"})
        self.assertEqual(problems, [])


class TestConditions(unittest.TestCase):
    HEADER = CANON + ["Test Temp"]

    def _rows(self, temps):
        return [r + (t,) for r, t in zip(base_rows(len(temps)), temps)]

    def test_single_condition_set_passes_and_is_recorded(self):
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(["25"] * 12)), {"condition_columns": ["Test Temp"]})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(review["conditions"]["reference"], {"Test Temp": "25"})

    def test_different_conditions_are_never_merged(self):
        temps = ["25"] * 6 + ["80"] * 6
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(temps)), {"condition_columns": ["Test Temp"]})
        self.assertEqual(rows, [])
        self.assertIn("CONDITIONS_DIFFER", codes(review, "blocking"))
        self.assertEqual(review["conditions"]["observed"]["Test Temp"], ["25", "80"])

    def test_reference_condition_selects_one_set_and_records_exclusions(self):
        temps = ["25"] * 9 + ["80"] * 3
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(temps)),
                              {"condition_columns": ["Test Temp"], "reference_conditions": {"Test Temp": "25"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 9)
        self.assertEqual(review["conditions"]["rows_excluded_other_conditions"], 3)
        self.assertIn("ROWS_EXCLUDED_OTHER_CONDITIONS", codes(review, "warning"))

    def test_condition_values_with_units_compare_numerically_and_by_unit(self):
        temps = ["25 °C", "25.0 C"] * 6
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(temps)), {"condition_columns": ["Test Temp"]})
        self.assertEqual(er.blocking_flags(review["flags"]), [])  # same condition, different spelling
        temps = ["25 °C"] * 6 + ["25 K"] * 6
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(temps)), {"condition_columns": ["Test Temp"]})
        self.assertIn("CONDITIONS_DIFFER", codes(review, "blocking"))  # 25 C is not 25 K

    def test_blank_condition_blocks(self):
        temps = ["25"] * 11 + [""]
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(temps)), {"condition_columns": ["Test Temp"]})
        self.assertEqual(rows, [])
        f = next(f for f in review["flags"] if f["code"] == "CONDITION_MISSING")
        self.assertEqual(f["rows"], [12])

    def test_reference_matching_nothing_blocks(self):
        review, rows, _ = run(SPEC, make_csv(self.HEADER, self._rows(["25"] * 12)),
                              {"condition_columns": ["Test Temp"], "reference_conditions": {"Test Temp": "99"}})
        self.assertIn("INVALID_OPTIONS", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_condition_column_not_in_file_blocks(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"condition_columns": ["Nope"]})
        self.assertIn("INVALID_OPTIONS", codes(review, "blocking"))

    def test_condition_column_cannot_also_be_modelled(self):
        review, rows, _ = run(SPEC, make_csv(CANON, base_rows()), {"condition_columns": ["cure_temp_c"]})
        self.assertIn("INVALID_OPTIONS", codes(review, "blocking"))


class TestConflicts(unittest.TestCase):
    def _with_repeats(self, targets):
        rows = base_rows(10)
        same = (0.2, 200)
        return rows + [same + (t,) for t in targets]

    def test_conflicting_observations_100_900_5_block(self):
        review, rows, _ = run(SPEC, make_csv(CANON, self._with_repeats([100, 900, 5])))
        self.assertEqual(rows, [])
        self.assertIn("CONFLICTING_OBSERVATIONS", codes(review, "blocking"))
        f = next(f for f in review["flags"] if f["code"] == "CONFLICTING_OBSERVATIONS")
        self.assertEqual(f["rows"], [11, 12, 13])

    def test_conflict_is_resolved_only_by_explicit_reasoned_exclusion(self):
        text = make_csv(CANON, self._with_repeats([100, 900, 5]))
        review, rows, _ = run(SPEC, text, {"exclude_rows": [12, 13], "exclusion_reason": "Rows 12-13 are typos per customer email 2026-09-28"})
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 11)
        self.assertEqual(review["exclusions"]["operator_excluded_rows"], [12, 13])
        self.assertIn("customer email", review["exclusions"]["reason"])
        self.assertIn("ROWS_EXCLUDED_BY_OPERATOR", codes(review, "warning"))

    def test_exclusion_without_reason_is_refused(self):
        review, rows, _ = run(SPEC, make_csv(CANON, self._with_repeats([100, 900, 5])), {"exclude_rows": [12, 13]})
        self.assertIn("INVALID_OPTIONS", codes(review, "blocking"))
        self.assertEqual(rows, [])

    def test_small_replicate_scatter_is_reported_not_blocked(self):
        review, rows, _ = run(SPEC, make_csv(CANON, self._with_repeats([100, 103, 101])))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 13)  # nothing averaged, nothing dropped
        self.assertIn("REPLICATE_VARIATION", codes(review, "warning"))
        self.assertEqual(review["replicates"]["threshold_status"], "HEURISTIC, NOT SCIENTIFICALLY VALIDATED")

    def test_exact_duplicates_are_not_conflicts(self):
        review, rows, _ = run(SPEC, make_csv(CANON, self._with_repeats([100, 100, 100])))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 13)
        self.assertNotIn("REPLICATE_VARIATION", codes(review))

    def test_conflict_detected_after_unit_conversion(self):
        # 24 h and 1 day are the SAME value, 24 h vs 2 day (=48 h) at same inputs is a difference.
        rows_in = base_rows(10) + [(0.2, 200, "24 h"), (0.2, 200, "1 day"), (0.2, 200, "400 h")]
        review, rows, _ = run(SPEC_H, make_csv(CANON, rows_in), {"declared_units": {"salt_spray_hours": "h"}})
        self.assertIn("CONFLICTING_OBSERVATIONS", codes(review, "blocking"))
        f = next(f for f in review["flags"] if f["code"] == "CONFLICTING_OBSERVATIONS")
        self.assertEqual(f["rows"], [11, 12, 13])
        # ...and 24 h vs 1 day alone (same value once converted) is NOT a conflict
        rows_ok = base_rows(10) + [(0.2, 200, "24 h"), (0.2, 200, "1 day")]
        review, rows, _ = run(SPEC_H, make_csv(CANON, rows_ok), {"declared_units": {"salt_spray_hours": "h"}})
        self.assertEqual(er.blocking_flags(review["flags"]), [])


class TestAmbiguousAndMessyValues(unittest.TestCase):
    def test_comma_decimal_values_block_instead_of_being_guessed(self):
        rows_in = [(f"{a}".replace(".", ","), b, c) for a, b, c in base_rows()]
        review, rows, _ = run(SPEC, make_csv(CANON, rows_in))
        self.assertEqual(rows, [])
        f = next(f for f in review["flags"] if f["code"] == "AMBIGUOUS_NUMBER")
        self.assertEqual(len(f["rows"]), 12)

    def test_non_finite_values_are_skipped_and_reported(self):
        rows_in = base_rows(12)
        rows_in[2] = (rows_in[2][0], rows_in[2][1], "nan")
        rows_in[5] = (rows_in[5][0], "inf", rows_in[5][2])
        review, rows, errors = run(SPEC, make_csv(CANON, rows_in))
        self.assertEqual(len(rows), 10)
        self.assertEqual(sorted(e["row"] for e in errors), [3, 6])
        self.assertTrue(all("Non-finite" in e["error"] for e in errors))
        self.assertIn("ROWS_SKIPPED_UNPARSEABLE", codes(review, "warning"))

    def test_missing_values_are_skipped_and_reported(self):
        rows_in = base_rows(12)
        rows_in[1] = (rows_in[1][0], rows_in[1][1], "")
        review, rows, errors = run(SPEC, make_csv(CANON, rows_in))
        self.assertEqual(len(rows), 11)
        self.assertEqual([e["row"] for e in errors], [2])

    def test_blank_spreadsheet_rows_are_ignored_and_counted(self):
        text = make_csv(CANON, base_rows(6)) + ",,\n,,\n" + "\n".join(f"{a},{b},{c}" for a, b, c in base_rows(12)[6:]) + "\n"
        review, rows, errors = run(SPEC, text)
        self.assertEqual(len(rows), 12)
        self.assertEqual(review["counts"]["blank_rows_ignored"], 2)
        self.assertEqual(errors, [])

    def test_whitespace_padded_headers_and_cells(self):
        text = " crosslinker_ratio , cure_temp_c ,salt_spray_hours\n" + "\n".join(f" {a} , {b} , {c} " for a, b, c in base_rows()) + "\n"
        review, rows, _ = run(SPEC, text)
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)

    def test_trailing_blank_header_columns_from_spreadsheet_export(self):
        text = "crosslinker_ratio,cure_temp_c,salt_spray_hours,,\n" + "\n".join(f"{a},{b},{c},," for a, b, c in base_rows()) + "\n"
        review, rows, _ = run(SPEC, text)
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)

    def test_title_row_above_header_is_refused_not_guessed(self):
        text = "Coating salt spray results,,\n" + make_csv(CANON, base_rows())
        review, rows, _ = run(SPEC, text)
        self.assertEqual(rows, [])
        self.assertIn("MISSING_REQUIRED_COLUMN", codes(review, "blocking"))

    def test_valid_clean_data_passes_with_only_informational_warnings(self):
        review, rows, errors = run(SPEC, make_csv(CANON, base_rows()))
        self.assertEqual(er.blocking_flags(review["flags"]), [])
        self.assertEqual(len(rows), 12)
        self.assertEqual(errors, [])
        self.assertEqual(review["counts"]["ingested"], 12)
        self.assertEqual(er.summarize_blocking([]).startswith("Dataset not accepted: 0"), True)


class TestSummaries(unittest.TestCase):
    def test_summary_mentions_codes_and_rows_and_points_to_preview(self):
        review, _, _ = run(SPEC, make_csv(CANON, [(0.1, 150, 100), (0.1, 150, 900), (0.1, 150, 5)] + base_rows(9)))
        msg = er.summarize_blocking(review["flags"])
        self.assertIn("CONFLICTING_OBSERVATIONS", msg)
        self.assertIn("preview", msg)

    def test_sha256_is_stable_and_content_sensitive(self):
        self.assertEqual(er.file_sha256(b"abc"), er.file_sha256(b"abc"))
        self.assertNotEqual(er.file_sha256(b"abc"), er.file_sha256(b"abd"))


if __name__ == "__main__":
    unittest.main()