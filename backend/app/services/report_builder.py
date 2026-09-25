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


DOMAIN_COVERAGE_LABELS = {
    "within_historical_domain": "Within historical range",
    "near_edge_of_domain": "Near edge of historical range",
    "outside_historical_domain": "Outside historical range",
}


def build_qualification_report(change_case: dict, ranked_results: list, n_historical_rows: int,
                                organization_name: str, data_quality: dict = None) -> str:
    """The Phase 2 commercial deliverable: the qualification diagnostic
    report. Deliberately explicit that this is an ANALYSIS deliverable
    (a ranked, confidence-scored shortlist and a recommended validation
    plan), not a claim that physical qualification is complete -- per the
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

    doc.add_heading("Ranked Candidates", level=1)
    if not ranked_results:
        doc.add_paragraph("No candidates ranked yet.")
    else:
        show_coverage = any(r.get("domain_coverage") for r in ranked_results)
        table = doc.add_table(rows=1, cols=4 if show_coverage else 3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "Candidate"
        hdr[1].text = "Predicted qualification probability"
        hdr[2].text = "Uncertainty (std)"
        if show_coverage:
            hdr[3].text = "Historical data coverage"
        for r in ranked_results:
            row = table.add_row().cells
            row[0].text = r["candidate_name"]
            row[1].text = f"{r['predicted_probability']:.0%}"
            row[2].text = f"{r['uncertainty_std']:.3f}"
            if show_coverage:
                cov = r.get("domain_coverage")
                row[3].text = DOMAIN_COVERAGE_LABELS[cov["status"]] if cov else "Not assessed"

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

    doc.add_heading("Basis for this analysis", level=1)
    if data_quality and data_quality["status"] == "insufficient":
        # Never claim a model basis the data cannot support.
        doc.add_paragraph(INSUFFICIENT_EVIDENCE_STATEMENT)
    else:
        doc.add_paragraph(
            f"Predictions are based on {n_historical_rows} historical qualification "
            "records supplied by the customer, using a Bayesian surrogate model with "
            "calibrated uncertainty -- not a heuristic score."
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
        "This report is an analysis deliverable: a ranked, confidence-scored "
        "shortlist and a recommended validation plan. It does not represent "
        "completed physical qualification -- recommended experiments must "
        "still be run and their real-world outcomes recorded before any "
        "candidate is considered qualified."
    ).font.size = Pt(9)

    out_filename = f"{secrets.token_hex(12)}.docx"
    out_path = os.path.join(REPORTS_ROOT, out_filename)
    doc.save(out_path)
    return out_path
