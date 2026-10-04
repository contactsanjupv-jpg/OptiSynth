"""
Pure tests of the extraction layer (no database, no HTTP): what each file type
yields, what is refused, and that selection is never silent.
Run:  python3 -m unittest backend.tests.test_evidence_extraction -v
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.app.services import evidence_extraction as X  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402


def refs(listing):
    return [t["ref"] for t in listing["tables"]]


class TestFileKind(unittest.TestCase):
    def test_supported_kinds_are_case_insensitive(self):
        for name, kind in (("a.csv", "csv"), ("A.XLSX", "xlsx"), ("r.Docx", "docx"), ("c.PDF", "pdf")):
            self.assertEqual(X.file_kind(name), kind)

    def test_legacy_and_unknown_types_are_refused_with_an_instruction(self):
        for name, needle in (("a.xls", ".xlsx"), ("a.xlsm", "Macro"), ("a.doc", ".docx"), ("a.txt", "Accepted file types"),
                             ("noext", "Accepted file types"), ("", "Accepted file types")):
            with self.assertRaises(ValidationError, msg=name) as cm:
                X.file_kind(name)
            self.assertIn(needle, str(cm.exception))


class TestListTables(unittest.TestCase):
    def test_xlsx_sheets_keep_true_excel_row_numbers_and_trim_empty_rows(self):
        t = X.list_tables("a.xlsx", F.xlsx_title_rows_then_table())["tables"][0]
        self.assertEqual([r["src_row"] for r in t["rows"][:3]], [1, 3, 4])  # row 2 was empty and is skipped, numbering is not
        self.assertEqual(X.default_header_row(t), 1)  # never guessed past the title row
        self.assertEqual(t["rows"][1]["cells"][:3], ["Crosslinker Ratio", "Cure Temp (C)", "Salt Spray (h)"])

    def test_xlsx_numbers_are_text_that_the_rules_can_parse(self):
        t = X.list_tables("a.xlsx", F.xlsx_alt_headers())["tables"][0]
        self.assertEqual(t["rows"][1]["cells"][:3], ["0.08", "140", "100"])

    def test_xlsx_number_format_with_quoted_text_is_flagged_per_column(self):
        t = X.list_tables("a.xlsx", F.xlsx_missing_units_with_hidden_number_format())["tables"][0]
        self.assertEqual([(w["col_index"], w["code"]) for w in t["column_warnings"]], [(2, "XLSX_NUMBER_FORMAT_HIDES_TEXT")])

    def test_xlsx_hidden_sheet_is_listed_and_marked(self):
        lst = X.list_tables("a.xlsx", F.xlsx_hidden_sheet_only_data())
        data = next(t for t in lst["tables"] if t["ref"] == "xlsx:Data")
        self.assertIn("hidden sheet", data["location"])
        self.assertTrue(any("hidden" in w for w in data["warnings"]))

    def test_docx_tables_numbered_in_document_order_with_preceding_paragraph(self):
        lst = X.list_tables("r.docx", F.docx_customer_report())
        self.assertEqual(refs(lst), ["docx:t1", "docx:t2"])
        self.assertEqual(lst["tables"][1]["caption"], "Table 2. Salt spray results, ASTM B117, 35 C")
        self.assertEqual(lst["tables"][1]["rows"][1]["cells"], ["0.08", "140", "100"])

    def test_docx_without_tables_yields_none(self):
        self.assertEqual(X.list_tables("n.docx", F.docx_no_tables())["tables"], [])

    def test_pdf_ruled_tables_by_page(self):
        lst = X.list_tables("c.pdf", F.pdf_two_tables())
        self.assertEqual(refs(lst), ["pdf:p1t1", "pdf:p2t1"])
        self.assertTrue(all(t["confidence"] == "ruled" for t in lst["tables"]))
        self.assertEqual(lst["tables"][1]["rows"][1]["cells"], ["0.08", "140", "100"])

    def test_scanned_pdf_has_no_tables_and_a_clear_warning(self):
        lst = X.list_tables("s.pdf", F.pdf_scanned())
        self.assertEqual(lst["tables"], [])
        self.assertTrue(any("OCR is not supported" in w for w in lst["warnings"]))

    def test_csv_is_one_table_with_line_numbers(self):
        lst = X.list_tables("a.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(3)))
        self.assertEqual(refs(lst), ["csv"])
        self.assertEqual([r["src_row"] for r in lst["tables"][0]["rows"]], [1, 2, 3, 4])

    def test_corrupt_files_raise_a_user_safe_error(self):
        for name in ("a.xlsx", "a.docx", "a.pdf"):
            with self.assertRaises(ValidationError, msg=name) as cm:
                X.list_tables(name, b"definitely not a real file")
            self.assertIn("Could not read", str(cm.exception))

    def test_empty_csv_has_no_header(self):
        with self.assertRaises(ValidationError):
            X.list_tables("a.csv", b"\n\n")


class TestLoadSelection(unittest.TestCase):
    def _flags(self, sel):
        return {f["code"]: f for f in sel["flags"]}

    def test_several_tables_and_no_choice_is_a_blocking_review_item_not_a_default(self):
        sel = X.load_selection("two.xlsx", F.xlsx_two_sheets(), {})
        self.assertIsNone(sel["headers"])
        self.assertEqual(sel["flags"][0]["code"], "TABLE_SELECTION_REQUIRED")
        self.assertIn("xlsx:Summary", sel["flags"][0]["message"])

    def test_single_table_is_used_with_a_confirmation_warning(self):
        sel = X.load_selection("one.docx", F.docx_single_table(), {})
        self.assertEqual(sel["headers"], F.CANON_HEADERS)
        self.assertEqual(self._flags(sel)["TABLE_AUTO_SELECTED"]["severity"], "warning")

    def test_explicit_selection_returns_rows_with_source_references(self):
        sel = X.load_selection("two.xlsx", F.xlsx_two_sheets(), {"table_ref": "xlsx:Data"})
        self.assertEqual(len(sel["data_rows"]), 12)
        self.assertEqual(sel["row_refs"][0], {"file": "two.xlsx", "table": "xlsx:Data", "location": "Sheet 'Data'", "row": 2})
        self.assertEqual(sel["source"]["tables"][0]["header_row"], 1)

    def test_unknown_table_and_unknown_header_row_block(self):
        self.assertEqual(X.load_selection("a.xlsx", F.xlsx_two_sheets(), {"table_ref": "nope"})["flags"][0]["code"], "INVALID_OPTIONS")
        sel = X.load_selection("a.xlsx", F.xlsx_title_rows_then_table(), {"header_row": 2})
        self.assertIn("INVALID_OPTIONS", [f["code"] for f in sel["flags"]])  # (after the single-table notice)
        self.assertIsNone(sel["headers"])

    def test_header_row_selects_the_real_header_and_skips_the_title(self):
        sel = X.load_selection("a.xlsx", F.xlsx_title_rows_then_table(), {"header_row": 3})
        self.assertEqual(sel["headers"][:3], ["Crosslinker Ratio", "Cure Temp (C)", "Salt Spray (h)"])
        self.assertEqual(len(sel["data_rows"]), 12)
        self.assertEqual(sel["row_refs"][0]["row"], 4)

    def test_header_row_with_several_tables_is_refused(self):
        sel = X.load_selection("c.pdf", F.pdf_table_continued_over_two_pages(),
                               {"table_ref": ["pdf:p1t1", "pdf:p2t1"], "header_row": 1})
        self.assertEqual(sel["flags"][0]["code"], "INVALID_OPTIONS")

    def test_matching_headers_combine_and_each_row_keeps_its_own_table(self):
        sel = X.load_selection("c.pdf", F.pdf_table_continued_over_two_pages(), {"table_ref": ["pdf:p1t1", "pdf:p2t1"]})
        self.assertEqual(len(sel["data_rows"]), 12)
        self.assertEqual([r["table"] for r in sel["row_refs"]].count("pdf:p2t1"), 6)

    def test_different_headers_never_combine(self):
        sel = X.load_selection("c.pdf", F.pdf_two_tables(), {"table_ref": ["pdf:p1t1", "pdf:p2t1"]})
        self.assertEqual(sel["flags"][0]["code"], "HEADERS_DIFFER_ACROSS_TABLES")

    def test_no_tables_is_blocking_and_carries_the_extraction_note(self):
        sel = X.load_selection("s.pdf", F.pdf_scanned(), {})
        self.assertEqual(sel["flags"][0]["code"], "NO_TABLES_FOUND")
        self.assertIn("OCR is not supported", sel["flags"][0]["message"])

    def test_table_and_column_warnings_become_review_flags(self):
        sel = X.load_selection("a.xlsx", F.xlsx_missing_units_with_hidden_number_format(), {})
        f = next(f for f in sel["flags"] if f["code"] == "XLSX_NUMBER_FORMAT_HIDES_TEXT")
        self.assertEqual(f["column"], "salt_spray_hours")


class TestTableSummary(unittest.TestCase):
    def test_summary_reports_how_well_the_first_row_matches_and_is_display_only(self):
        t = X.list_tables("a.xlsx", F.xlsx_alt_headers())["tables"][0]
        s = X.table_summary(t, F.CANON_HEADERS)
        self.assertEqual((s["matched_required"], s["total_required"]), (3, 3))
        self.assertEqual(s["matches"]["salt_spray_hours"], {"best": "Salt Spray (h)", "confidence": "partial", "n_candidates": 1})
        self.assertEqual(s["matches"]["crosslinker_ratio"]["confidence"], "alias")  # identical words, different case/punctuation
        self.assertEqual(s["default_header_row"], 1)

    def test_ambiguous_headers_do_not_count_as_matched(self):
        grid = F.make_xlsx({"S": [["Crosslinker Ratio", "crosslinker-ratio", "Cure Temp", "Salt Spray"], [1, 1, 1, 1]]})
        s = X.table_summary(X.list_tables("a.xlsx", grid)["tables"][0], F.CANON_HEADERS)
        self.assertEqual(s["matches"]["crosslinker_ratio"]["n_candidates"], 2)
        self.assertEqual(s["matched_required"], 2)  # only the two UNIQUE matches count; the ambiguous column does not

    def test_preview_is_capped(self):
        rows = [(i, i, i) for i in range(1, 120)]
        t = X.list_tables("a.xlsx", F.make_xlsx({"D": F.grid(F.CANON_HEADERS, rows)}))["tables"][0]
        s = X.table_summary(t)
        self.assertEqual((len(s["preview"]), s["preview_truncated"], s["n_rows"]), (X.PREVIEW_ROWS, True, 120))


if __name__ == "__main__":
    unittest.main()
