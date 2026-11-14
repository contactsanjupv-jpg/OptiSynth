"""Level 4: the customer report leads with a summary and the plan, keeps the technical evidence in appendices,
states one run identity that matches the file name, and shows timestamps consistently.
Run:  python3 -m unittest backend.tests.test_report_structure -v"""
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from docx import Document
from docx.shared import Inches

from backend.app.services import report_builder as rb
from backend.app.services import report_filename
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

RANKED, CAUTION, NOT_RANKED = "ranked", "ranked_with_caution", "not_ranked_insufficient_evidence"
SPEC = {"feature_columns": ["a", "b"], "target_metric": "strength", "target_value": 35.0, "direction": "maximize",
        "units": {"a": "kg", "b": "kg", "strength": "MPa"}}
COV_OUT = {"status": "outside_historical_domain", "note": "Outside the observed range.", "features": {
    "a": {"status": "outside_historical_domain", "value": 9.0, "historical_min": 1.0, "historical_max": 4.0},
    "b": {"status": "within_historical_domain", "value": 2.0, "historical_min": 1.0, "historical_max": 4.0}}}
MQ = {"r2_score": 0.5, "mean_absolute_error": 1.0, "prediction_accuracy_pct": 70.0, "accuracy_tolerance_band": 2.0,
      "n_folds_used": 5, "n_points_scored": 15}
RANKED_AT = "2026-10-29T20:42:17.123456+00:00"


def _result(name, rank, status, p=0.8, value=40.0, sd=1.5, cov=None):
    return {"rank": rank, "ranking_status": status, "candidate_name": name, "predicted_value": value,
            "predicted_probability": p, "uncertainty_std": sd, "recommended_experiment": f"Run {name}.",
            "domain_coverage": cov, "decision_support": None, "closest_records": [], "outcome_recorded": False}


RESULTS = [_result("A", 1, RANKED, 0.92), _result("B", 2, RANKED, 0.55, 36.0), _result("C", 3, CAUTION, 0.31, 34.0),
           _result("U", None, NOT_RANKED, 0.99, 120.0, cov=COV_OUT)]
REVIEW = {"file_sha256": "abc", "counts": {"data_rows": 14, "ingested": 14},
          "units": {"a": {"canonical": "kg"}, "b": {"canonical": "kg"}, "strength": {"canonical": "MPa"}},
          "condition_columns": [], "flags": [{"severity": "warning", "code": "W1", "message": "m1"}]}


def _ctx(**over):
    base = {"change_case_id": 3, "spec": SPEC, "generated_at_utc": "2026-11-01 00:00:00",
            "dataset": {"id": 2, "original_filename": "h.csv", "created_at": "2026-10-20T10:20:30.123456+00:00",
                        "row_count": 14},
            "ingestion_review": REVIEW, "model_versions": ["gp-rank-v1"], "ranked_at_utc": RANKED_AT,
            "stale_notice": None, "outcomes": [],
            "candidates": [{"candidate_name": r["candidate_name"], "properties": {"a": 1, "b": 2}, "has_prediction": True}
                           for r in RESULTS]}
    base.update(over)
    return base


CASE = {"name": "Case", "trigger_type": "regulatory_restriction", "restricted_substance": "PFHxA"}


def _build(results=RESULTS, ctx="default", model_quality=MQ, data_quality=None, n_rows=14):
    ctx = _ctx() if ctx == "default" else ctx
    path = rb.build_qualification_report(CASE, results, n_rows, "Acme", data_quality=data_quality,
                                         model_quality=model_quality, audit_context=ctx)
    doc = Document(path)
    os.remove(path)
    return doc


def _h1(doc):
    return [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]


def _text(doc):
    return "\n".join(p.text for p in doc.paragraphs)


def _summary(doc):
    """The paragraphs between the summary heading and the next level-1 heading."""
    out, on = [], False
    for p in doc.paragraphs:
        if p.style.name == "Heading 1":
            on = p.text == "Summary for the decision-maker"
            continue
        if on:
            out.append(p.text)
    return out


def _table(doc, first_header):
    return next(([[c.text for c in row.cells] for row in t.rows] for t in doc.tables if t.rows[0].cells[0].text == first_header))


MAIN = ["Summary for the decision-maker", "Ranked Candidates", "Recommended Validation Experiments",
        "Recorded physical validation outcomes", "Human qualification decision"]
APPENDICES = ["Requirement and analysis record", "Evidence intake review", "Historical range coverage",
              "Model performance evidence", "Evidence gaps", "Basis for this analysis"]


class TestSectionOrder(unittest.TestCase):
    def test_decision_sections_first_then_appendices_in_a_fixed_order(self):
        doc = _build(data_quality={"status": "valid", "dataset_id": 2, "row_count": 14, "distinct_rows": 14,
                                   "duplicate_rows": 0})
        h = _h1(doc)
        expected = MAIN + ["Appendices"] + APPENDICES + ["Data quality and evidence basis"]
        self.assertEqual(h, expected)

    def test_the_decision_block_comes_before_the_appendices_not_between_them(self):
        h = _h1(_build())
        self.assertLess(h.index("Human qualification decision"), h.index("Appendices"))
        self.assertLess(h.index("Appendices"), h.index("Requirement and analysis record"))
        self.assertLess(h.index("Appendices"), h.index("Basis for this analysis"))

    def test_a_page_break_separates_the_appendices(self):
        doc = _build()
        paras = doc.paragraphs
        i = next(i for i, p in enumerate(paras) if p.text == "Appendices" and p.style.name == "Heading 1")
        before = paras[i - 1]._p.xml + paras[i]._p.xml
        self.assertIn('w:type="page"', before)

    def test_no_section_was_dropped(self):
        h = set(_h1(_build()))
        for needed in MAIN + APPENDICES:
            self.assertIn(needed, h)

    def test_without_an_audit_context_the_summary_and_appendices_still_work(self):
        doc = _build(ctx=None)
        h = _h1(doc)
        self.assertEqual(h[0], "Summary for the decision-maker")
        self.assertIn("Appendices", h)
        self.assertNotIn("Human qualification decision", h)  # as before: it needs the audit context
        self.assertIn("the target property", _text(doc))

    def test_summary_heading_is_unique_and_first(self):
        h = _h1(_build())
        self.assertEqual(h.count("Summary for the decision-maker"), 1)
        self.assertEqual(h[0], "Summary for the decision-maker")


class TestSummaryContent(unittest.TestCase):
    def test_counts_names_and_top_ranks_match_the_ranked_table(self):
        doc = _build()
        s = "\n".join(_summary(doc))
        self.assertIn("This report compares 4 candidate substitutes against one requirement (strength (MPa) at or "
                      "above 35.0), using 14 historical records supplied by the customer.", s)
        self.assertIn("3 of 4 candidates lie inside the range the historical data covers and are ranked; 1 lie outside "
                      "it and have no rank.", s)
        table = _table(doc, "Rank")
        prob_col = next(i for i, c in enumerate(table[0]) if c.startswith("Model-estimated probability"))
        by_name = {row[1]: row[prob_col] for row in table[1:]}
        for name, rank in (("A", 1), ("B", 2), ("C", 3)):
            self.assertIn(f"{rank}. {name} (model-estimated probability {by_name[name]}", s)
        self.assertIn("1. A (model-estimated probability 92%, predicted strength (MPa) 40)", s)
        self.assertIn("Not ranked, so physical testing is the only way to establish strength: U.", s)
        self.assertIn("not an experimental design chosen to reduce uncertainty", s)

    def test_only_the_top_three_are_listed_and_in_rank_order(self):
        many = [_result(f"N{i}", i, RANKED, 0.9 - i / 20) for i in (5, 1, 4, 2, 3)]
        s = "\n".join(_summary(_build(many, ctx=_ctx(candidates=[]))))
        line = next(l for l in s.split("\n") if l.startswith("Highest model-estimated probability"))
        self.assertLess(line.index("1. N1"), line.index("2. N2"))
        self.assertLess(line.index("2. N2"), line.index("3. N3"))
        self.assertNotIn("4. N4", line)

    def test_singular_forms_and_the_no_candidate_case(self):
        one = _summary(_build([_result("Only", 1, RANKED)], ctx=_ctx(candidates=[]), n_rows=1))
        self.assertIn("compares 1 candidate substitute against one requirement", "\n".join(one))
        self.assertIn("using 1 historical record supplied", "\n".join(one))
        none = "\n".join(_summary(_build([], ctx=_ctx(candidates=[]))))
        self.assertIn("No candidate has been ranked yet", none)
        self.assertNotIn("Highest model-estimated probability", none)

    def test_candidates_not_yet_ranked_and_outcome_counts_come_from_the_record(self):
        ctx = _ctx(candidates=[{"candidate_name": n, "properties": {}, "has_prediction": hp}
                               for n, hp in (("A", True), ("B", True), ("New", False))],
                   outcomes=[{"candidate_name": "A", "model_probability": 0.9, "actual_result": "ok",
                              "passed_spec": True, "recorded_at": "2026-10-30T01:02:03+00:00"}])
        s = "\n".join(_summary(_build(RESULTS[:2], ctx=ctx)))
        self.assertIn("Not yet ranked: New.", s)
        self.assertIn("Outcomes recorded by the customer: 1 of 3 candidates (the application does not verify them).", s)

    def test_the_limits_are_stated_once_each(self):
        s = "\n".join(_summary(_build()))
        for sentence in ("The model-estimated probability is the model's own estimate, not a measured or validated success rate.",
                         "Only strength is analysed, and only the modelled inputs; other qualification requirements are not evaluated.",
                         "Range coverage is checked one input at a time, not jointly.",
                         "The decision to qualify rests with the customer's authorised qualification personnel, after physical validation."):
            self.assertEqual(s.count(sentence), 1, sentence)

    def test_the_stale_notice_leads_the_summary_and_stays_in_the_record(self):
        doc = _build(ctx=_ctx(stale_notice="This ranking was generated from a.csv. Current evidence is b.csv."))
        self.assertTrue(_summary(doc)[0].startswith("NOTICE -- NEWER EVIDENCE EXISTS."))
        self.assertEqual(_text(doc).count("NOTICE -- NEWER EVIDENCE EXISTS."), 2)
        self.assertNotIn("NEWER EVIDENCE", "\n".join(_summary(_build())))

    def test_insufficient_evidence_is_stated_in_the_summary(self):
        dq = {"status": "insufficient", "dataset_id": 2, "row_count": 3, "distinct_rows": 3, "duplicate_rows": 0}
        s = "\n".join(_summary(_build([], data_quality=dq, model_quality=None)))
        self.assertIn(rb.INSUFFICIENT_EVIDENCE_STATEMENT, s)

    def test_model_cautions_depend_on_the_scheme_and_the_fit_counters(self):
        grouped = "\n".join(_summary(_build(model_quality={**MQ, "cv_scheme": "grouped"})))
        self.assertIn("cross-validated R-squared is 0.5 and the mean absolute error 1.0 MPa; the validation folds keep "
                      "repeated input sets together, so this is not a test on new batches", grouped)
        random_rows = "\n".join(_summary(_build(model_quality={**MQ, "cv_scheme": "random_rows"})))
        self.assertIn("the validation folds are random by row, which can look better", random_rows)
        self.assertNotIn("optimiser reported problems", grouped)
        diag = {"n_fits": 5, "n_convergence_warnings": 0, "n_fits_with_hyperparameter_at_bound": 0,
                "n_fits_noise_at_lower_bound": 2}
        self.assertIn("optimiser reported problems", "\n".join(_summary(_build(model_quality={**MQ, "fit_diagnostics": diag}))))
        err = "\n".join(_summary(_build(model_quality={"error": "Need at least 6 distinct input sets (found 4)."})))
        self.assertIn("Model performance could not be assessed on this dataset: Need at least 6", err)
        self.assertNotIn("R-squared", err)

    def test_data_cautions_come_from_the_intake_review(self):
        s = "\n".join(_summary(_build()))
        self.assertIn("No test conditions were declared", s)
        self.assertIn("1 intake review warning recorded", s)
        two = {**REVIEW, "flags": [{"severity": "warning", "code": "W", "message": "m"}] * 2 + [{"severity": "info", "code": "I", "message": "i"}]}
        self.assertIn("2 intake review warnings recorded", "\n".join(_summary(_build(ctx=_ctx(ingestion_review=two)))))
        undeclared = {**REVIEW, "units": {"a": {"canonical": "kg"}}}
        self.assertIn("No unit is recorded for: b, strength.", "\n".join(_summary(_build(ctx=_ctx(ingestion_review=undeclared)))))
        none = "\n".join(_summary(_build(ctx=_ctx(ingestion_review=None))))
        self.assertIn("No intake review record exists for this dataset", none)

    def test_identity_table_matches_the_record(self):
        doc = _build()
        rows = dict(tuple(r) for r in _table(doc, "Change case ID"))
        self.assertEqual(rows["Change case ID"], "3")
        self.assertEqual(rows["Ranking run identifier"], "20261029T204217Z")
        self.assertEqual(rows["Historical dataset"], "Version 2: h.csv")
        self.assertEqual(rows["Report generated (UTC)"], "2026-11-01 00:00:00")

    def test_the_summary_makes_no_claim_the_detail_does_not(self):
        s = "\n".join(_summary(_build())).lower()
        for banned in ("likely to meet", "safe to", "proven", "guarantee", "automatically", "reject", "worth", "recommend ",
                       "qualified by", "will meet", "confidence"):
            self.assertNotIn(banned, s, banned)


class TestRunIdentityAndTimestamps(unittest.TestCase):
    def test_record_table_preserves_stored_timestamps_and_run_identifier(self):
        doc = _build()
        record = next(
            [[c.text for c in row.cells] for row in t.rows]
            for t in doc.tables
            if t.rows[0].cells[0].text == "Change case ID" and len(t.rows) > 5
        )
        rows = dict(tuple(r) for r in record)

        self.assertEqual(rows["Report generated (UTC)"], "2026-11-01 00:00:00")
        self.assertEqual(rows["Dataset uploaded (UTC)"], "2026-10-20T10:20:30.123456+00:00")
        self.assertEqual(rows["Latest ranking run (UTC)"], "2026-10-29T20:42:17.123456+00:00")
        self.assertEqual(
            rows["Ranking run identifier"],
            "20261029T204217Z (the 'run_' part of this file's name)",
        )

    def test_no_run_identifier_when_nothing_was_ranked(self):
        doc = _build([], ctx=_ctx(ranked_at_utc=None, candidates=[]))
        self.assertNotIn(
            "Ranking run identifier",
            _text(doc) + "\n".join(
                c.text for t in doc.tables for r in t.rows for c in r.cells
            ),
        )

    def test_outcome_timestamps_stay_exactly_as_stored(self):
        o = {"candidate_name": "A", "model_probability": 0.9, "estimate_made_at": "2026-10-29T20:42:17.123456+00:00",
             "actual_result": "310 h", "passed_spec": True, "recorded_at": "2026-10-30T01:02:03.999+00:00"}
        doc = _build(ctx=_ctx(outcomes=[o]))
        outcomes = next([[c.text for c in row.cells] for row in t.rows] for t in doc.tables if t.rows[0].cells[-1].text == "Recorded (UTC)")
        self.assertEqual(outcomes[1][4], "2026-10-30T01:02:03.999+00:00")  # comparable with the estimate stamp beside it
        self.assertEqual(outcomes[1][1], "90% (estimated 2026-10-29T20:42:17.123456+00:00)")  # exact stamp, kept for audit

    def test_decision_block_names_the_analysis_it_refers_to(self):
        doc = _build()
        rows = _table(doc, "Analysis this decision refers to")
        self.assertEqual(rows[0][1], "Change case 3; ranking run 20261029T204217Z; historical dataset version 2 (h.csv)")
        self.assertEqual([r[0] for r in rows[1:]], ["Decision (qualify / do not qualify / further testing)", "Candidate",
                         "Basis and reference to physical test records", "Decision-maker (name, role)", "Date", "Signature"])
        self.assertTrue(all(r[1] == "" for r in rows[1:]))
        self.assertEqual(rb._decision_reference(None), "")
        self.assertIn("ranking run none", rb._decision_reference(_ctx(ranked_at_utc=None)))
        self.assertIn("no historical dataset", rb._decision_reference(_ctx(dataset=None)))

    def test_trigger_is_shown_as_words(self):
        self.assertEqual(rb._trigger_label("regulatory_restriction"), "Regulatory restriction")
        self.assertEqual(rb._trigger_label(None), "")
        self.assertIn("Trigger: Regulatory restriction (PFHxA)", "\n".join(p.text for p in _build().paragraphs))


class TestPlanTableLayout(unittest.TestCase):
    def test_columns_have_fixed_widths_that_fit_the_page_and_a_smaller_font(self):
        doc = _build()
        table = next(t for t in doc.tables if t.rows[0].cells[0].text == "Model rank")
        self.assertEqual(sum(rb.PLAN_TABLE_WIDTHS_IN), 6.5)
        self.assertEqual(len(rb.PLAN_TABLE_WIDTHS_IN), len(table.columns))
        self.assertFalse(table.autofit)
        for row in table.rows:
            self.assertEqual([c.width for c in row.cells], [Inches(w) for w in rb.PLAN_TABLE_WIDTHS_IN])
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        self.assertEqual(run.font.size.pt, rb.PLAN_TABLE_FONT_PT)

    def test_other_tables_are_untouched(self):
        doc = _build()
        ranked = next(t for t in doc.tables if t.rows[0].cells[0].text == "Rank")
        self.assertTrue(ranked.autofit)


class TestThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_report_structure"

    def _setup(self, label="Structure"):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        for name, feats in (("InA", {"crosslinker_ratio": 0.12, "cure_temp_c": 158}),
                            ("InB", {"crosslinker_ratio": 0.10, "cure_temp_c": 150}),
                            ("Far", {"crosslinker_ratio": 0.40, "cure_temp_c": 300})):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": feats}).status_code, 201)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        return org_id, client, case_id

    def _report(self, client, case_id):
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        return r, Document(io.BytesIO(r.content))

    def test_report_order_and_run_identity_match_the_file_name(self):
        org_id, client, case_id = self._setup()
        r, doc = self._report(client, case_id)
        h = _h1(doc)
        self.assertEqual(h[:5], MAIN)
        self.assertEqual(h[5], "Appendices")
        file_run = re.search(r"_run_(\w+)\.docx", r.headers["content-disposition"]).group(1)
        record = next(rows for rows in ([[c.text for c in row.cells] for row in t.rows] for t in doc.tables)
                      if rows[0][0] == "Change case ID" and len(rows) > 5)
        self.assertEqual(dict(tuple(x) for x in record)["Ranking run identifier"].split()[0], file_run)
        self.assertEqual(dict(tuple(x) for x in _table(doc, "Change case ID"))["Ranking run identifier"], file_run)
        s = "\n".join(_summary(doc))
        ranked = _table(doc, "Rank")
        first = next(row for row in ranked[1:] if row[0] == "1")
        self.assertIn(f"1. {first[1]} (model-estimated probability", s)
        self.assertIn("Not ranked, so physical testing is the only way to establish", s)

    def test_a_new_ranking_run_changes_the_identifier_everywhere(self):
        from unittest import mock
        org_id, client, case_id = self._setup("Rerun")
        _, first = self._report(client, case_id)
        with mock.patch("backend.app.repositories.candidate_repo.now_iso", return_value="2030-01-02T03:04:05+00:00"):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        r, second = self._report(client, case_id)
        self.assertIn("run_20300102T030405Z", r.headers["content-disposition"])
        self.assertEqual(dict(tuple(x) for x in _table(second, "Change case ID"))["Ranking run identifier"], "20300102T030405Z")
        self.assertNotEqual(_table(first, "Change case ID"), _table(second, "Change case ID"))

    def test_newer_evidence_is_called_out_in_the_summary(self):
        org_id, client, case_id = self._setup("Stale4")
        rows = F.base_rows(14)
        self.assertEqual(self.upload(client, case_id, "newer.csv", F.csv_bytes(F.CANON_HEADERS, [(a, b, c * 0.5) for a, b, c in rows])).status_code, 201)
        _, doc = self._report(client, case_id)
        self.assertTrue(_summary(doc)[0].startswith("NOTICE -- NEWER EVIDENCE EXISTS."))
        self.assertIn("newer.csv", _summary(doc)[0])

    def test_outcome_counts_in_the_summary_belong_to_their_own_change_case(self):
        from backend.tests._api_base import SPEC as API_SPEC
        org_id, client, case_1 = self._setup("Own1")
        case_2 = self.make_case(client, API_SPEC)
        self.assertEqual(self.upload(client, case_2, "h2.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        c2 = client.post(f"/api/change-cases/{case_2}/candidates", json={"name": "InA", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 158}}).json()["id"]
        self.assertEqual(client.post(f"/api/change-cases/{case_2}/rank").status_code, 200)
        cands = client.get(f"/api/change-cases/{case_1}/candidates").json()
        cid = next(c["id"] for c in cands if c["candidate_name"] == "InA")
        self.assertEqual(client.post(f"/api/change-cases/{case_1}/candidates/{cid}/outcome",
                                     json={"actual_result": "310 h", "passed_spec": True}).status_code, 201)
        self.assertIn("Outcomes recorded by the customer: 1 of 3 candidates", "\n".join(_summary(self._report(client, case_1)[1])))
        self.assertIn("Outcomes recorded by the customer: 0 of 1 candidates", "\n".join(_summary(self._report(client, case_2)[1])))

    @unittest.skipUnless(shutil.which("soffice") and shutil.which("pdftotext"), "LibreOffice / pdftotext not installed")
    def test_rendered_summary_is_on_page_one_and_the_answer_by_page_two(self):
        org_id, client, case_id = self._setup("Render")
        r, _ = self._report(client, case_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.docx")
            with open(path, "wb") as f:
                f.write(r.content)
            subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                           check=True, capture_output=True, timeout=180)
            page = lambda n: subprocess.run(["pdftotext", "-f", str(n), "-l", str(n), "-layout", os.path.join(tmp, "r.pdf"), "-"],
                                            capture_output=True, text=True).stdout
            self.assertIn("Summary for the decision-maker", page(1))
            self.assertIn("What this analysis does not establish", page(1))
            first_two = page(1) + page(2)
            self.assertIsNone(re.search(r"^\s*Evidence intake review\s*$", first_two, re.M), "the intake review is an appendix")
            self.assertIsNone(re.search(r"^\s*Requirement and analysis record\s*$", first_two, re.M))
            self.assertIn("Ranked Candidates", page(1) + page(2))


if __name__ == "__main__":
    unittest.main()
