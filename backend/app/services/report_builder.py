"""
Builds the customer-facing .docx pilot/status report. Deliberately a pure
function of its arguments -- no database or Flask import here -- so it can
be unit-tested by simply calling it with in-memory data (see
backend/tests/test_report_builder.py) without needing a live DB.
"""
import os
import secrets

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

REPORTS_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports", "generated")


def build_project_report(project: dict, backtest: dict, recommendations: list,
                          n_historical_rows: int, organization_name: str) -> str:
    os.makedirs(REPORTS_ROOT, exist_ok=True)

    doc = Document()

    title = doc.add_heading(project["name"], level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    subtitle = doc.add_paragraph()
    run = subtitle.add_run("Optimization Summary Report")
    run.italic = True
    run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    meta = doc.add_paragraph()
    meta.add_run(f"Prepared for: {organization_name}\n").font.size = Pt(10)
    meta.add_run(f"Target: {project['target_metric']} "
                 f"({'>=' if project['direction'] == 'maximize' else '<='} "
                 f"{project.get('target_value', 'not set')})").font.size = Pt(10)

    doc.add_heading("Results", level=1)
    if "error" in backtest:
        doc.add_paragraph(backtest["error"])
    else:
        table = doc.add_table(rows=1, cols=2)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Metric"
        hdr[1].text = "Value"
        rows_data = [
            ("Historical experiments", str(n_historical_rows)),
            ("Experiments needed (as actually run)", str(backtest.get("actual_order_evals", "target not reached"))),
            ("Experiments needed (engine-optimized order)", str(backtest.get("engine_order_evals", "target not reached"))),
            ("Reduction", f"{backtest['reduction_pct']}%" if backtest.get("reduction_pct") is not None else "n/a"),
        ]
        for label, value in rows_data:
            row = table.add_row().cells
            row[0].text = label
            row[1].text = value

    doc.add_heading("Recommended Next Experiments", level=1)
    if not recommendations:
        doc.add_paragraph("No recommendations generated yet.")
    else:
        rec_table = doc.add_table(rows=1, cols=4)
        rec_table.style = "Light Grid Accent 1"
        hdr = rec_table.rows[0].cells
        hdr[0].text = "Rank"
        hdr[1].text = "Predicted Value"
        hdr[2].text = "Confidence"
        hdr[3].text = "Key Variables"
        for i, rec in enumerate(recommendations, start=1):
            row = rec_table.add_row().cells
            row[0].text = str(i)
            row[1].text = f"{rec['predicted_value']:.2f}"
            row[2].text = f"{rec['feasibility_probability'] * 100:.0f}%"
            row[3].text = ", ".join(f"{k}={v:.2f}" for k, v in rec["features"].items())

    disclaimer = doc.add_paragraph()
    disclaimer.add_run(
        "This report reflects a retrospective backtest on the customer's own historical "
        "data, not new physical experiments. See the Model Performance page for "
        "cross-validated prediction-quality metrics."
    ).font.size = Pt(9)

    out_filename = f"{secrets.token_hex(12)}.docx"
    out_path = os.path.join(REPORTS_ROOT, out_filename)
    doc.save(out_path)
    return out_path


INSUFFICIENT_EVIDENCE_STATEMENT = "Insufficient evidence to establish predictive performance."


# C1: wording shown beneath the ranked-candidates table.
MODEL_ESTIMATE_NOTE = (
    "Model-estimated probability is the model's output given the supplied "
    "historical data. It is not a validated qualification probability."
)
DECISION_SUPPORT_EXPLANATION = (
    "Decision support is a separate, categorical signal derived only from whether each "
    "candidate lies within the observed historical range of every feature. It does not "
    "change or adjust the model-estimated probability, and 'Evidence-supported' does not "
    "mean qualified: physical validation is required for every candidate."
)

DOMAIN_COVERAGE_LABELS = {
    "within_historical_domain": "Within historical range",
    "near_edge_of_domain": "Near edge of historical range",
    "outside_historical_domain": "Outside historical range",
}


DIRECTION_PHRASE = {"maximize": "at or above", "minimize": "at or below"}

HUMAN_DECISION_STATEMENT = (
    "This analysis does not qualify any candidate. It estimates, organises the available "
    "evidence and recommends physical validation. The decision to qualify a substitute rests "
    "with the customer's authorised qualification personnel, based on physical validation "
    "results and the customer's own qualification requirements."
)


def _kv_table(doc, rows):
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value
    return table


def _add_requirement_and_record(doc, ctx):
    """Requirement analysed + analysis record: states what the model-estimated
    probability actually refers to, and exactly which case/dataset/model/inputs
    produced this report, so the document is traceable without the software."""
    spec = ctx["spec"]
    doc.add_heading("Requirement and analysis record", level=1)
    phrase = DIRECTION_PHRASE.get(spec.get("direction"), spec.get("direction", ""))
    doc.add_paragraph(
        f"Requirement analysed: {spec['target_metric']} {phrase} {spec['target_value']}. "
        f"'Model-estimated probability' in this report is the model's estimate that a candidate's "
        f"{spec['target_metric']} meets this requirement, given the candidate inputs and the "
        "historical data listed below. It is not a validated qualification probability."
    )
    dataset = ctx.get("dataset")
    rows = [
        ("Change case ID", str(ctx["change_case_id"])),
        ("Report generated (UTC)", ctx["generated_at_utc"]),
        ("Input features modelled", ", ".join(spec["feature_columns"])),
    ]
    if dataset:
        rows += [
            ("Historical dataset", f"Version {dataset['id']}: {dataset['original_filename']}"),
            ("Dataset uploaded (UTC)", str(dataset["created_at"])),
            ("Records ingested from that file", str(dataset["row_count"])),
        ]
    else:
        rows.append(("Historical dataset", "None uploaded"))
    versions = ctx.get("model_versions") or []
    rows.append(("Model", "Gaussian process surrogate; version(s): " + (", ".join(versions) if versions else "no ranking run yet")))
    if ctx.get("ranked_at_utc"):
        rows.append(("Latest ranking run (UTC)", ctx["ranked_at_utc"]))
    _kv_table(doc, rows)

    cands = ctx.get("candidates") or []
    if cands:
        doc.add_paragraph("Candidate inputs used for the estimates (as supplied by the customer):")
        table = doc.add_table(rows=1, cols=1 + len(spec["feature_columns"]))
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Candidate"
        for i, col in enumerate(spec["feature_columns"], start=1):
            hdr[i].text = col
        for c in cands:
            row = table.add_row().cells
            row[0].text = c["candidate_name"]
            for i, col in enumerate(spec["feature_columns"], start=1):
                row[i].text = str(c["properties"].get(col, ""))


def _evidence_gaps(ranked_results, ctx):
    """Evidence gaps stated from facts already in the system -- no new
    heuristics or thresholds. Returns a list of plain-language bullets."""
    spec = ctx["spec"]
    gaps = []
    for r in ranked_results:
        cov = r.get("domain_coverage")
        if not cov:
            continue
        for col, f in cov["features"].items():
            if f["status"] != "within_historical_domain":
                gaps.append(
                    f"{r['candidate_name']}: no historical evidence at {col} = {f['value']} "
                    f"(observed {f['historical_min']} to {f['historical_max']}); the estimate is an extrapolation."
                )
    unranked = [c["candidate_name"] for c in ctx.get("candidates", []) if not c.get("has_prediction")]
    if unranked:
        gaps.append("Not yet ranked: " + ", ".join(unranked) + ".")
    with_outcome = {o["candidate_name"] for o in ctx.get("outcomes", [])}
    no_outcome = [c["candidate_name"] for c in ctx.get("candidates", []) if c["candidate_name"] not in with_outcome]
    if no_outcome:
        gaps.append("No physical validation outcome has been recorded for: " + ", ".join(no_outcome) + ".")
    gaps += [
        "Only these input features are modelled: " + ", ".join(spec["feature_columns"]) +
        ". Any other property relevant to qualification is not evaluated by this analysis.",
        f"Only one requirement is analysed ({spec['target_metric']}). Other qualification requirements are not evaluated.",
        "Units of measure and test conditions are not recorded or verified by this system; the customer's "
        "historical data is used as supplied.",
        "Row-level source provenance (source document, page or table) is not recorded for the historical data.",
        "Range coverage is checked per feature, not jointly: a candidate inside every individual range can "
        "still lie in a region of the combined input space with no historical evidence.",
    ]
    return gaps


def _add_evidence_gaps(doc, ranked_results, ctx):
    doc.add_heading("Evidence gaps", level=1)
    for g in _evidence_gaps(ranked_results, ctx):
        doc.add_paragraph(g, style="List Bullet")


def _add_recorded_outcomes(doc, ctx):
    doc.add_heading("Recorded physical validation outcomes", level=1)
    outcomes = ctx.get("outcomes") or []
    if not outcomes:
        doc.add_paragraph("No physical validation outcomes have been recorded for this change case.")
        return
    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Candidate"
    hdr[1].text = "Model-estimated probability (latest ranking)"
    hdr[2].text = "Recorded result"
    hdr[3].text = "Recorded as meeting requirement"
    hdr[4].text = "Recorded (UTC)"
    for o in outcomes:
        row = table.add_row().cells
        row[0].text = o["candidate_name"]
        row[1].text = f"{o['model_probability']:.0%}" if o.get("model_probability") is not None else "Not ranked"
        row[2].text = o["actual_result"] or ""
        row[3].text = "Yes" if o["passed_spec"] else "No"
        row[4].text = str(o["recorded_at"])
    doc.add_paragraph(
        "Outcomes are entered by the customer's team and are append-only records; they are shown "
        "next to the model estimate for comparison and do not alter it."
    ).runs[0].font.size = Pt(9)


def _add_human_decision_block(doc):
    doc.add_heading("Human qualification decision", level=1)
    doc.add_paragraph(HUMAN_DECISION_STATEMENT)
    _kv_table(doc, [
        ("Decision (qualify / do not qualify / further testing)", ""),
        ("Candidate", ""),
        ("Basis and reference to physical test records", ""),
        ("Decision-maker (name, role)", ""),
        ("Date", ""),
        ("Signature", ""),
    ])


def build_qualification_report(change_case: dict, ranked_results: list, n_historical_rows: int,
                                organization_name: str, data_quality: dict = None,
                                model_quality: dict = None, uncertainty_calibration: dict = None,
                                audit_context: dict = None) -> str:
    """The Phase 2 commercial deliverable: the qualification diagnostic
    report. Deliberately explicit that this is an ANALYSIS deliverable
    (a ranked shortlist ordered by model-estimated probability and a
    recommended validation plan), not a claim that physical qualification is complete -- per the
    approved Phase 2 scope, this must never imply physical lab
    qualification itself was performed or completed within any stated
    timeframe."""
    os.makedirs(REPORTS_ROOT, exist_ok=True)

    doc = Document()

    title = doc.add_heading(change_case["name"], level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    subtitle = doc.add_paragraph()
    run = subtitle.add_run("Substitute Qualification Diagnostic")
    run.italic = True
    run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    meta = doc.add_paragraph()
    meta.add_run(f"Prepared for: {organization_name}\n").font.size = Pt(10)
    meta.add_run(f"Trigger: {change_case['trigger_type']}").font.size = Pt(10)
    if change_case.get("restricted_substance"):
        meta.add_run(f" ({change_case['restricted_substance']})").font.size = Pt(10)

    if audit_context:
        _add_requirement_and_record(doc, audit_context)

    doc.add_heading("Ranked Candidates", level=1)
    if not ranked_results:
        doc.add_paragraph("No candidates ranked yet.")
    else:
        show_coverage = any(r.get("domain_coverage") for r in ranked_results)
        # C1: the categorical decision-support column is shown whenever any
        # result carries it (i.e. always for real ranked predictions).
        show_decision = any(r.get("decision_support") for r in ranked_results)
        table = doc.add_table(rows=1, cols=3 + int(show_coverage) + int(show_decision))
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Candidate"
        hdr[1].text = "Model-estimated probability"
        hdr[2].text = "Uncertainty (std)"
        next_col = 3
        if show_coverage:
            hdr[next_col].text = "Historical data coverage"
            next_col += 1
        if show_decision:
            hdr[next_col].text = "Decision support"
        for r in ranked_results:
            row = table.add_row().cells
            row[0].text = r["candidate_name"]
            row[1].text = f"{r['predicted_probability']:.0%}"
            row[2].text = f"{r['uncertainty_std']:.3f}"
            next_col = 3
            if show_coverage:
                cov = r.get("domain_coverage")
                row[next_col].text = DOMAIN_COVERAGE_LABELS[cov["status"]] if cov else "Not assessed"
                next_col += 1
            if show_decision:
                ds = r.get("decision_support")
                row[next_col].text = ds["label"] if ds else "Not assessed"

        doc.add_paragraph(MODEL_ESTIMATE_NOTE).runs[0].font.size = Pt(9)
        if show_decision:
            doc.add_paragraph(DECISION_SUPPORT_EXPLANATION).runs[0].font.size = Pt(9)

        doc.add_heading("Recommended Validation Experiments", level=1)
        for r in ranked_results:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{r['candidate_name']}: ").bold = True
            p.add_run(r["recommended_experiment"])

    coverage_items = [r for r in ranked_results if r.get("domain_coverage")]
    if coverage_items:
        doc.add_heading("Historical range coverage", level=1)
        for r in coverage_items:
            cov = r["domain_coverage"]
            flagged = [(col, f) for col, f in cov["features"].items()
                       if f["status"] != "within_historical_domain"]
            if not flagged:
                continue
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{r['candidate_name']}: ").bold = True
            p.add_run(
                "prediction is an extrapolation beyond the historical data -- "
                + "; ".join(
                    f"{col} = {f['value']} is {DOMAIN_COVERAGE_LABELS[f['status']].lower()} "
                    f"({f['historical_min']} to {f['historical_max']})"
                    for col, f in flagged
                ) + "."
            )
        doc.add_paragraph(coverage_items[0]["domain_coverage"]["note"]).runs[0].font.size = Pt(9)

    if model_quality:
        doc.add_heading("Model performance evidence", level=1)
        if "error" in model_quality:
            doc.add_paragraph(
                "Cross-validated model-quality metrics could not be computed for this "
                f"dataset: {model_quality['error']}"
            )
        else:
            doc.add_paragraph(
                f"Cross-validated on this dataset's own historical data "
                f"({model_quality['n_folds_used']}-fold, {model_quality['n_points_scored']} held-out "
                f"points): R\u00b2 = {model_quality['r2_score']}, mean absolute error = "
                f"{model_quality['mean_absolute_error']} ({model_quality['prediction_accuracy_pct']}% of "
                f"held-out predictions fell within {model_quality['accuracy_tolerance_band']}, a "
                f"tolerance band of 10% of this dataset's observed target range)."
            )
            if uncertainty_calibration and "error" not in uncertainty_calibration:
                doc.add_paragraph(
                    "Predicted-uncertainty calibration: correlation between predicted uncertainty "
                    f"and actual held-out error = {uncertainty_calibration['sigma_error_correlation']} "
                    "(positive means higher predicted uncertainty is associated with larger actual "
                    f"error); {uncertainty_calibration['within_1sigma_pct']}% of held-out points fell "
                    f"within 1 predicted standard deviation, "
                    f"{uncertainty_calibration['within_2sigma_pct']}% within 2."
                )
            elif uncertainty_calibration and "error" in uncertainty_calibration:
                doc.add_paragraph(
                    "Uncertainty calibration could not be evaluated for this dataset: "
                    f"{uncertainty_calibration['error']}"
                )

    if audit_context:
        _add_evidence_gaps(doc, ranked_results, audit_context)
        _add_recorded_outcomes(doc, audit_context)
        _add_human_decision_block(doc)

    doc.add_heading("Basis for this analysis", level=1)
    if data_quality and data_quality["status"] == "insufficient":
        # Never claim a model basis the data cannot support.
        doc.add_paragraph(INSUFFICIENT_EVIDENCE_STATEMENT)
    else:
        # Priority 7B: this used to assert "with calibrated uncertainty" as
        # an unconditional fact. It no longer does -- whether the model's
        # uncertainty is actually calibrated is a measured, dataset-specific
        # result (see "Model performance evidence" above), not a property
        # of the model class itself, and this sentence must not claim more
        # than the evidence supports.
        doc.add_paragraph(
            f"Predictions are based on {n_historical_rows} historical qualification "
            "records ingested from the customer-supplied file, using a Gaussian process (Bayesian) "
            "surrogate model. See 'Model performance evidence' above for this "
            "dataset's own cross-validated accuracy and uncertainty-calibration results."
        )

    if data_quality:
        doc.add_heading("Data quality and evidence basis", level=1)
        doc.add_paragraph(f"Dataset version: {data_quality['dataset_id']}")
        doc.add_paragraph(f"Data quality status: {data_quality['status']}")
        doc.add_paragraph(
            f"Historical records: {data_quality['row_count']} "
            f"({data_quality['distinct_rows']} distinct after removing "
            f"{data_quality['duplicate_rows']} exact duplicate rows)."
        )
        if data_quality["status"] == "insufficient":
            doc.add_paragraph(
                "Too few distinct historical records were supplied to support a "
                "reliable ranking, so no candidate ranking was produced from this dataset."
            )

    disclaimer = doc.add_paragraph()
    disclaimer.add_run(
        "This report is an analysis deliverable: a ranked shortlist ordered by "
        "model-estimated probability and a recommended validation plan. It does not represent "
        "completed physical qualification -- recommended experiments must "
        "still be run and their real-world outcomes recorded before any "
        "candidate is considered qualified."
    ).font.size = Pt(9)

    out_filename = f"{secrets.token_hex(12)}.docx"
    out_path = os.path.join(REPORTS_ROOT, out_filename)
    doc.save(out_path)
    return out_path
