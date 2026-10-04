"""
Customer-intake adversarial tests across file formats, through the REAL HTTP
path: extract -> table/header selection -> B2/B3 evidence gate -> provenance ->
sufficiency gate -> candidates (canonical units) -> ranking -> report.

Each scenario is a DIFFERENT imaginary customer's file. For every case the
system must either handle it correctly, or flag/reject it explicitly -- and a
rejected upload must leave nothing behind. No test claims the system
"understands arbitrary documents".

Run:  python3 -m unittest backend.tests.test_evidence_formats_api -v
"""
import json
import os
import unittest

from backend.tests import evidence_fixtures as F
from backend.tests._api_base import (CONFIRM_ALIASES, SPEC, SPEC_H, SPEC_U, IsolatedApiTestCase)


def codes(review, severity=None):
    return {f["code"] for f in review["flags"] if severity is None or f["severity"] == severity}


class TestExtractAndSelection(IsolatedApiTestCase):
    dir_name = "_test_env_fmt_a"

    def test_extract_lists_tables_with_location_preview_and_match_quality_and_stores_nothing(self):
        org_id, client, case_id = self.setup_case("Extract")
        r = self.extract(client, case_id, "two.xlsx", F.xlsx_two_sheets())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["file_kind"], "xlsx")
        self.assertEqual(len(body["file_sha256"]), 64)
        refs = {t["ref"]: t for t in body["tables"]}
        self.assertEqual(set(refs), {"xlsx:Summary", "xlsx:Data"})
        data = refs["xlsx:Data"]
        self.assertEqual(data["location"], "Sheet 'Data'")
        self.assertEqual((data["n_rows"], data["n_cols"]), (13, 3))
        self.assertEqual(data["matched_required"], 3)
        self.assertEqual(refs["xlsx:Summary"]["matched_required"], 0)
        self.assertEqual(data["preview"][0], {"src_row": 1, "cells": F.CANON_HEADERS})
        self.assertEqual([c["name"] for c in body["required_columns"]],
                         ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"])
        self.assertEqual(self.datasets(org_id, case_id), [])

    def test_extract_docx_shows_the_nearest_preceding_paragraph_as_context(self):
        _, client, case_id = self.setup_case("ExtractDocx")
        body = self.extract(client, case_id, "r.docx", F.docx_customer_report()).json()
        by = {t["ref"]: t for t in body["tables"]}
        self.assertEqual(by["docx:t2"]["caption"], "Table 2. Salt spray results, ASTM B117, 35 C")
        self.assertEqual(by["docx:t2"]["matched_required"], 3)

    def test_extract_flags_scanned_pdf_and_hidden_number_format_and_hidden_sheet(self):
        _, client, case_id = self.setup_case("ExtractWarn")
        pdf = self.extract(client, case_id, "scan.pdf", F.pdf_scanned()).json()
        self.assertEqual(pdf["tables"], [])
        self.assertTrue(any("no extractable text layer" in w for w in pdf["warnings"]))
        x = self.extract(client, case_id, "fmt.xlsx", F.xlsx_missing_units_with_hidden_number_format()).json()
        self.assertEqual(x["tables"][0]["column_warnings"][0]["code"], "XLSX_NUMBER_FORMAT_HIDES_TEXT")
        h = self.extract(client, case_id, "hid.xlsx", F.xlsx_hidden_sheet_only_data()).json()
        data = next(t for t in h["tables"] if t["ref"] == "xlsx:Data")
        self.assertTrue(any("hidden" in w for w in data["warnings"]))

    def test_extract_is_tenant_scoped(self):
        _, client, case_id = self.setup_case("TenantA")
        _, other = self.org_and_client("TenantB")
        self.assertEqual(self.extract(other, case_id, "a.xlsx", F.xlsx_alt_headers()).status_code, 404)
        self.assertEqual(other.get(f"/api/change-cases/{case_id}/sufficiency").status_code, 404)
        self.assertEqual(other.get(f"/api/change-cases/{case_id}/evidence").status_code, 404)

    def test_unsupported_unreadable_and_corrupt_files_are_refused_with_a_reason(self):
        org_id, client, case_id = self.setup_case("Refuse")
        for name, content, needle in (
            ("old.xls", b"x", "re-save the workbook as .xlsx"),
            ("old.doc", b"x", "re-save the document as .docx"),
            ("macro.xlsm", b"x", "Macro-enabled"),
            ("notes.txt", b"x", "Unsupported file type"),
            ("bad.xlsx", b"this is not a zip file at all", "Could not read this .xlsx"),
            ("bad.docx", b"this is not a zip file at all", "Could not read this .docx"),
            ("bad.pdf", b"%PDF-1.4 not really a pdf", "Could not read this PDF"),
        ):
            r = self.upload(client, case_id, name, content)
            self.assertEqual(r.status_code, 400, name)
            self.assertIn(needle, r.json()["error"], name)
        self.assertEqual(self.datasets(org_id, case_id), [])

    def test_too_many_pdf_pages_is_refused_not_truncated(self):
        from backend.app.services import evidence_extraction as X
        old = X.MAX_PDF_PAGES
        X.MAX_PDF_PAGES = 1
        try:
            _, client, case_id = self.setup_case("Pages")
            r = self.upload(client, case_id, "two.pdf", F.pdf_two_tables())
            self.assertEqual(r.status_code, 400)
            self.assertIn("pages (limit 1)", r.json()["error"])
        finally:
            X.MAX_PDF_PAGES = old


class TestXlsxCustomers(IsolatedApiTestCase):
    dir_name = "_test_env_fmt_b"

    def test_alt_headers_blocked_until_mapping_confirmed_then_provenance_is_stored(self):
        org_id, client, case_id = self.setup_case("XlsxAlias")
        content = F.xlsx_alt_headers()
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "cust.xlsx", content),
                                            "MAPPING_NOT_CONFIRMED")
        pv = self.preview(client, case_id, "cust.xlsx", content).json()
        self.assertFalse(pv["would_be_accepted"])
        self.assertEqual(pv["review"]["proposed_mapping"]["salt_spray_hours"], "Salt Spray (h)")
        self.assertEqual(pv["review"]["proposed_units"], {"cure_temp_c": "C", "salt_spray_hours": "h"})
        self.assertIn("Test Standard", pv["review"]["proposed_condition_columns"])
        ok = self.upload(client, case_id, "cust.xlsx", content, CONFIRM_ALIASES)
        self.assertEqual(ok.status_code, 201, ok.text)
        body = ok.json()
        self.assertEqual(body["rows_ingested"], 12)
        self.assertEqual(body["review"]["source"]["kind"], "xlsx")
        self.assertEqual(body["review"]["source"]["tables"][0]["ref"], "xlsx:Data")
        self.assertIn("COLUMNS_IGNORED", codes(body["review"], "warning"))
        self.assertIn("POSSIBLE_UNIT_OR_CONDITION_COLUMN_IGNORED", codes(body["review"], "warning"))
        exps = self.experiments(org_id, body["dataset_id"])
        prov = json.loads(exps[0]["provenance_json"])
        self.assertEqual(prov, {"file": "cust.xlsx", "table": "xlsx:Data", "location": "Sheet 'Data'", "row": 2})
        self.assertEqual(json.loads(exps[11]["provenance_json"])["row"], 13)
        ds = self.datasets(org_id, case_id)[0]
        self.assertTrue(ds["stored_filename"].endswith(".xlsx"))  # the original file is kept, with its real type

    def test_title_rows_above_the_header_need_an_explicit_header_row(self):
        org_id, client, case_id = self.setup_case("XlsxTitle")
        content = F.xlsx_title_rows_then_table()
        # row 1 is the title: the software never guesses past it, with or without a mapping
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "t.xlsx", content),
                                            "MISSING_REQUIRED_COLUMN")
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "t.xlsx", content, CONFIRM_ALIASES),
                                            "INVALID_MAPPING")
        bad = self.upload(client, case_id, "t.xlsx", content, {**CONFIRM_ALIASES, "header_row": 2})
        self.assert_rejected_nothing_stored(org_id, case_id, bad, "INVALID_OPTIONS")  # row 2 is empty
        ok = self.upload(client, case_id, "t.xlsx", content, {**CONFIRM_ALIASES, "header_row": 3})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["review"]["source"]["tables"][0]["header_row"], 3)
        exps = self.experiments(org_id, ok.json()["dataset_id"])
        self.assertEqual(json.loads(exps[0]["provenance_json"])["row"], 4)  # true Excel row of the first data row

    def test_two_sheets_are_never_chosen_silently(self):
        org_id, client, case_id = self.setup_case("XlsxSheets")
        content = F.xlsx_two_sheets()
        r = self.upload(client, case_id, "s.xlsx", content)
        self.assert_rejected_nothing_stored(org_id, case_id, r, "TABLE_SELECTION_REQUIRED")
        self.assertIn("xlsx:Summary", r.json()["error"])
        self.assert_rejected_nothing_stored(org_id, case_id,
                                            self.upload(client, case_id, "s.xlsx", content, {"table_ref": "xlsx:Nope"}),
                                            "INVALID_OPTIONS")
        ok = self.upload(client, case_id, "s.xlsx", content, {"table_ref": "xlsx:Data"})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 12)

    def test_hidden_sheet_can_be_selected_but_carries_a_warning(self):
        org_id, client, case_id = self.setup_case("XlsxHidden")
        ok = self.upload(client, case_id, "h.xlsx", F.xlsx_hidden_sheet_only_data(), {"table_ref": "xlsx:Data"})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertTrue(any("hidden" in f["message"] for f in ok.json()["review"]["flags"] if f["code"] == "TABLE_WARNING"))

    def test_mixed_and_incompatible_units_are_blocked(self):
        org_id, client, case_id = self.setup_case("XlsxUnits", SPEC_H)
        r = self.upload(client, case_id, "m.xlsx", F.xlsx_mixed_units())
        self.assert_rejected_nothing_stored(org_id, case_id, r, "UNIT_INCOMPATIBLE")
        self.assertIn("(data rows 4)", r.json()["error"])

    def test_equivalent_units_convert_exactly_and_are_disclosed(self):
        org_id, client, case_id = self.setup_case("XlsxEquiv", SPEC_H)
        ok = self.upload(client, case_id, "e.xlsx", F.xlsx_equivalent_units())
        self.assertEqual(ok.status_code, 201, ok.text)
        exps = self.experiments(org_id, ok.json()["dataset_id"])
        self.assertEqual([e["target_value"] for e in exps[:3]], [24.0, 24.0, 24.0])
        self.assertIn("UNIT_CONVERTED", codes(ok.json()["review"], "warning"))

    def test_unit_hidden_in_excel_number_format_is_warned_and_unit_must_be_declared(self):
        org_id, client, case_id = self.setup_case("XlsxFmt", SPEC_H)
        content = F.xlsx_missing_units_with_hidden_number_format()
        pv = self.preview(client, case_id, "f.xlsx", content).json()
        self.assertIn("XLSX_NUMBER_FORMAT_HIDES_TEXT", codes(pv["review"], "warning"))
        self.assertIn("UNIT_MISSING", codes(pv["review"], "blocking"))  # a bare number is not assumed to be hours
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "f.xlsx", content), "UNIT_MISSING")
        ok = self.upload(client, case_id, "f.xlsx", content, {"declared_units": {"salt_spray_hours": "h"}})
        self.assertEqual(ok.status_code, 201, ok.text)

    def test_multiple_test_conditions_blocked_until_one_reference_is_named(self):
        org_id, client, case_id = self.setup_case("XlsxCond")
        content = F.xlsx_multiple_conditions()
        opts = {"condition_columns": ["Test Temp (C)"]}
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "c.xlsx", content, opts), "CONDITIONS_DIFFER")
        ok = self.upload(client, case_id, "c.xlsx", content, {**opts, "reference_conditions": {"Test Temp (C)": "25"}})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 8)
        self.assertEqual(ok.json()["review"]["conditions"]["rows_excluded_other_conditions"], 6)

    def test_duplicates_are_accepted_counted_and_not_double_counted_by_the_gate(self):
        org_id, client, case_id = self.setup_case("XlsxDup")
        ok = self.upload(client, case_id, "d.xlsx", F.xlsx_duplicates())
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 16)
        self.assertEqual(ok.json()["duplicate_rows_found"], 4)
        suff = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertEqual(suff["summary"]["distinct_rows"], 12)  # not 16

    def test_conflicting_observations_are_blocked_with_source_rows_named(self):
        org_id, client, case_id = self.setup_case("XlsxConflict")
        r = self.upload(client, case_id, "x.xlsx", F.xlsx_conflicting())
        self.assert_rejected_nothing_stored(org_id, case_id, r, "CONFLICTING_OBSERVATIONS")
        pv = self.preview(client, case_id, "x.xlsx", F.xlsx_conflicting()).json()
        f = next(f for f in pv["review"]["flags"] if f["code"] == "CONFLICTING_OBSERVATIONS")
        self.assertEqual(f["row_sources"], ["Sheet 'Data' row 12", "Sheet 'Data' row 13", "Sheet 'Data' row 14"])

    def test_missing_target_column_is_blocked_and_nothing_is_stored(self):
        org_id, client, case_id = self.setup_case("XlsxNoTarget")
        r = self.upload(client, case_id, "n.xlsx", F.xlsx_missing_target_column())
        self.assert_rejected_nothing_stored(org_id, case_id, r, "MISSING_REQUIRED_COLUMN")
        self.assertIn("salt_spray_hours", r.json()["error"])

    def test_non_numeric_and_missing_cells_are_skipped_with_their_source_row(self):
        org_id, client, case_id = self.setup_case("XlsxCells")
        g = F.grid(F.CANON_HEADERS, F.base_rows(14))
        g[3][2] = "n/a"
        g[6][1] = None
        ok = self.upload(client, case_id, "c.xlsx", F.make_xlsx({"Data": g}))
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 12)
        self.assertEqual(sorted(e["source"] for e in ok.json()["errors"]), ["Sheet 'Data' row 4", "Sheet 'Data' row 7"])

    def test_evidence_inventory_lists_source_hash_and_marks_the_current_dataset(self):
        org_id, client, case_id = self.setup_case("Inventory")
        content = F.xlsx_alt_headers()
        self.assertEqual(self.upload(client, case_id, "first.xlsx", content, CONFIRM_ALIASES).status_code, 201)
        self.assertEqual(self.upload(client, case_id, "second.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows())).status_code, 201)
        inv = client.get(f"/api/change-cases/{case_id}/evidence").json()
        self.assertEqual([i["original_filename"] for i in inv], ["second.csv", "first.xlsx"])
        self.assertEqual([i["is_current"] for i in inv], [True, False])
        self.assertEqual(inv[1]["source"]["kind"], "xlsx")
        self.assertEqual(len(inv[1]["file_sha256"]), 64)


class TestDocxAndPdfCustomers(IsolatedApiTestCase):
    dir_name = "_test_env_fmt_c"

    def test_docx_two_tables_require_selection_then_ingest_with_table_provenance(self):
        org_id, client, case_id = self.setup_case("Docx")
        content = F.docx_customer_report()
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "r.docx", content), "TABLE_SELECTION_REQUIRED")
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "r.docx", content, {"table_ref": "docx:t2"}),
                                            "MAPPING_NOT_CONFIRMED")
        ok = self.upload(client, case_id, "r.docx", content, {**CONFIRM_ALIASES, "table_ref": "docx:t2"})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["review"]["source"]["tables"][0]["caption"], "Table 2. Salt spray results, ASTM B117, 35 C")
        exps = self.experiments(org_id, ok.json()["dataset_id"])
        self.assertEqual(json.loads(exps[0]["provenance_json"])["table"], "docx:t2")
        self.assertEqual(json.loads(exps[0]["provenance_json"])["row"], 2)

    def test_docx_single_table_is_used_but_flagged_for_confirmation(self):
        org_id, client, case_id = self.setup_case("DocxOne")
        ok = self.upload(client, case_id, "one.docx", F.docx_single_table())
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertIn("TABLE_AUTO_SELECTED", codes(ok.json()["review"], "warning"))

    def test_docx_conditions_in_table_must_be_resolved_and_a_small_reference_set_stays_insufficient(self):
        org_id, client, case_id = self.setup_case("DocxCond")
        content = F.docx_conditions_in_table()
        opts = {"condition_columns": ["Chamber Temp (C)"]}
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "c.docx", content, opts), "CONDITIONS_DIFFER")
        ok = self.upload(client, case_id, "c.docx", content, {**opts, "reference_conditions": {"Chamber Temp (C)": "35"}})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 6)
        self.assertEqual(ok.json()["data_quality_status"], "insufficient")  # accepted as evidence, but cannot support ranking

    def test_docx_without_tables_is_refused_with_the_manual_instruction(self):
        org_id, client, case_id = self.setup_case("DocxNone")
        r = self.upload(client, case_id, "n.docx", F.docx_no_tables())
        self.assert_rejected_nothing_stored(org_id, case_id, r, "NO_TABLES_FOUND")

    def test_pdf_ruled_table_ingests_after_mapping_confirmation(self):
        org_id, client, case_id = self.setup_case("Pdf")
        content = F.pdf_ruled_table()
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "c.pdf", content), "MAPPING_NOT_CONFIRMED")
        ok = self.upload(client, case_id, "c.pdf", content, CONFIRM_ALIASES)
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["review"]["source"]["tables"][0]["location"], "Page 1, table 1")
        prov = json.loads(self.experiments(org_id, ok.json()["dataset_id"])[0]["provenance_json"])
        self.assertEqual((prov["table"], prov["row"]), ("pdf:p1t1", 2))

    def test_pdf_two_tables_require_selection(self):
        org_id, client, case_id = self.setup_case("PdfTwo")
        content = F.pdf_two_tables()
        self.assert_rejected_nothing_stored(org_id, case_id, self.upload(client, case_id, "t.pdf", content), "TABLE_SELECTION_REQUIRED")
        ok = self.upload(client, case_id, "t.pdf", content, {"table_ref": "pdf:p2t1"})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["rows_ingested"], 12)

    def test_pdf_table_continued_over_pages_combines_only_when_headers_match_and_each_row_keeps_its_page(self):
        org_id, client, case_id = self.setup_case("PdfCont")
        content = F.pdf_table_continued_over_two_pages()
        one = self.upload(client, case_id, "c.pdf", content, {"table_ref": "pdf:p1t1"})
        self.assertEqual(one.json()["rows_ingested"], 6)  # one page alone is only half the evidence
        both = self.upload(client, case_id, "c.pdf", content, {"table_ref": ["pdf:p1t1", "pdf:p2t1"]})
        self.assertEqual(both.status_code, 201, both.text)
        self.assertEqual(both.json()["rows_ingested"], 12)
        exps = self.experiments(org_id, both.json()["dataset_id"])
        self.assertEqual({json.loads(e["provenance_json"])["table"] for e in exps}, {"pdf:p1t1", "pdf:p2t1"})

    def test_headers_that_differ_across_selected_tables_are_never_combined(self):
        org_id, client, case_id = self.setup_case("PdfDiff")
        r = self.upload(client, case_id, "t.pdf", F.pdf_two_tables(), {"table_ref": ["pdf:p1t1", "pdf:p2t1"]})
        self.assert_rejected_nothing_stored(org_id, case_id, r, "HEADERS_DIFFER_ACROSS_TABLES")

    def test_scanned_pdf_is_refused_not_guessed(self):
        org_id, client, case_id = self.setup_case("PdfScan")
        r = self.upload(client, case_id, "scan.pdf", F.pdf_scanned())
        self.assert_rejected_nothing_stored(org_id, case_id, r, "NO_TABLES_FOUND")
        self.assertIn("OCR is not supported", r.json()["error"])


class TestFeatureCountsAndSufficiencyGate(IsolatedApiTestCase):
    dir_name = "_test_env_fmt_d"

    def _spec(self, n_features, target_value=300.0):
        return {"feature_columns": [f"f{i}" for i in range(1, n_features + 1)], "target_metric": "y",
                "target_value": target_value, "direction": "maximize"}

    def _rows(self, n_features, n_rows):
        return [[(i + 1) * (j + 1) + j for j in range(n_features)] + [100 + 10 * i] for i in range(n_rows)]

    def _csv(self, n_features, n_rows):
        return F.csv_bytes([f"f{i}" for i in range(1, n_features + 1)] + ["y"], self._rows(n_features, n_rows))

    def _add(self, client, case_id, name, features, units=None):
        body = {"name": name, "features": features}
        if units:
            body["units"] = units
        return client.post(f"/api/change-cases/{case_id}/candidates", json=body)

    def test_one_three_and_five_features_all_work_end_to_end(self):
        for n_feat in (1, 3, 5):
            org_id, client, case_id = self.setup_case(f"Feat{n_feat}", self._spec(n_feat))
            self.paid(org_id)
            self.assertEqual(self.upload(client, case_id, "h.csv", self._csv(n_feat, 14)).status_code, 201)
            inside = {f"f{j + 1}": (7 * (j + 1) + j) for j in range(n_feat)}
            self.assertEqual(self._add(client, case_id, "A", inside).status_code, 201)
            suff = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
            self.assertTrue(suff["can_rank"], (n_feat, suff["blocking"]))
            r = client.post(f"/api/change-cases/{case_id}/rank")
            self.assertEqual(r.status_code, 200, (n_feat, r.text))

    def test_more_features_than_the_records_can_determine_stops_the_analysis_with_an_explanation(self):
        org_id, client, case_id = self.setup_case("Feat9", self._spec(9))
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", self._csv(9, 9)).status_code, 201)
        self.assertEqual(self._add(client, case_id, "A", {f"f{j + 1}": (5 * (j + 1) + j) for j in range(9)}).status_code, 201)
        suff = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertFalse(suff["can_rank"])
        self.assertEqual([b["code"] for b in suff["blocking"]], ["ROWS_VS_FEATURES"])
        r = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Evidence-sufficiency gate", r.json()["error"])
        self.assertIn("ROWS_VS_FEATURES", r.json()["error"])

    def test_sufficiency_before_any_evidence_says_what_is_missing(self):
        _, client, case_id = self.setup_case("SuffEmpty")
        s = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertFalse(s["can_rank"])
        self.assertEqual({b["code"] for b in s["blocking"]}, {"DATASET_PRESENT", "CANDIDATES_PRESENT"})

    def test_too_few_distinct_rows_is_blocked_by_the_gate_and_explained(self):
        org_id, client, case_id = self.setup_case("SuffFew")
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(6))).status_code, 201)
        self._add(client, case_id, "A", {"crosslinker_ratio": 0.1, "cure_temp_c": 150})
        s = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertFalse(s["can_rank"])
        self.assertIn("MIN_DISTINCT_ROWS", {b["code"] for b in s["blocking"]})

    def test_extrapolated_candidate_is_a_visible_warning_not_a_hard_stop(self):
        org_id, client, case_id = self.setup_case("SuffOut")
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        self._add(client, case_id, "Far", {"crosslinker_ratio": 0.9, "cure_temp_c": 400})
        s = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertTrue(s["can_rank"])
        warn = next(w for w in s["warnings"] if w["code"] == "CANDIDATES_OUTSIDE_RANGE")
        self.assertIn("Far", warn["detail"])
        r = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()[0]["decision_support"]["status"], "requires_validation")


class TestEndToEndXlsxToReport(IsolatedApiTestCase):
    dir_name = "_test_env_fmt_e"

    def test_xlsx_customer_to_report_with_canonical_units_provenance_and_gates(self):
        org_id, client, case_id = self.setup_case("E2E", SPEC_U)
        self.paid(org_id)
        content = F.xlsx_alt_headers()
        opts = {**CONFIRM_ALIASES, "declared_units": {"cure_temp_c": "degC", "salt_spray_hours": "h"}}
        ok = self.upload(client, case_id, "Customer Salt Spray.xlsx", content, opts)
        self.assertEqual(ok.status_code, 201, ok.text)

        def add(name, features, units):
            return client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features, "units": units})

        # canonical-unit discipline for candidates
        self.assertEqual(add("NoUnit", {"crosslinker_ratio": 0.12, "cure_temp_c": 160}, None).status_code, 400)
        kelvin = add("Kelvin", {"crosslinker_ratio": 0.12, "cure_temp_c": 433}, {"cure_temp_c": "K"})
        self.assertEqual(kelvin.status_code, 400)
        self.assertIn("cannot be safely converted", kelvin.json()["error"])
        stray = add("Stray", {"crosslinker_ratio": 0.12, "cure_temp_c": 160}, {"cure_temp_c": "°C", "crosslinker_ratio": "wt%"})
        self.assertEqual(stray.status_code, 400)  # the case declares no unit for crosslinker_ratio: cannot be checked
        good = add("Good", {"crosslinker_ratio": 0.12, "cure_temp_c": 160}, {"cure_temp_c": "°C"})
        self.assertEqual(good.status_code, 201, good.text)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/candidates").json()[0]["input_record"]["cure_temp_c"]["unit"], "°C")

        suff = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        self.assertTrue(suff["can_rank"], suff["blocking"])
        rk = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(rk.status_code, 200, rk.text)
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        for needle in ("Source file\nCustomer Salt Spray.xlsx (XLSX)", "Sheet 'Data'", "header at source row 1",
                       "Reviewer-confirmed mapping", "Row-level source provenance is recorded for every historical record",
                       "Evidence intake review", "Human qualification decision"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("is not recorded for the historical data", text)

    def test_report_for_older_dataset_without_source_still_says_provenance_is_not_recorded(self):
        # a dataset whose review record predates `source` (simulated) keeps the honest legacy wording
        org_id, client, case_id = self.setup_case("Legacy")
        self.paid(org_id)
        up = self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14)))
        from backend.app.config.database import db_transaction
        from sqlalchemy import text
        ds = self.datasets(org_id, case_id)[0]
        review = json.loads(ds["review_json"])
        review.pop("source", None)
        with db_transaction() as conn:
            conn.execute(text("UPDATE qualification_datasets SET review_json = :r WHERE id = :i"),
                         {"r": json.dumps(review), "i": ds["id"]})
        client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "A", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        t = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn("Row-level source provenance (source document, page or table) is not recorded", t)


if __name__ == "__main__":
    unittest.main()
