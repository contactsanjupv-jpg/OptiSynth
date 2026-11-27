"""
Builds the customer-facing .docx pilot/status report. Deliberately a pure
function of its arguments -- no database or Flask import here -- so it can
be unit-tested by simply calling it with in-memory data (see
backend/tests/test_report_builder.py) without needing a live DB.
"""
import os
import secrets
from decimal import Decimal

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from backend.app.services import change_case_rules
from backend.app.services import ranking_evidence_rules as evidence_rules
from backend.app.services import report_filename
from backend.app.services import validation_plan

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
        hdr[2].text = "Model-estimated probability"
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
        "data, not new physical experiments. The probability column is the model's own estimate, "
        "not a measured or validated success rate. See the Model Performance page for "
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
    "historical data. It is not a validated qualification probability. "
    + change_case_rules.PROBABILITY_DISPLAY_NOTE
)
DECISION_SUPPORT_EXPLANATION = (
    "Decision support is a separate, categorical signal. It combines two checks: whether each candidate lies within "
    "the observed historical range of every feature, checked one feature at a time, and how far the combination of "
    "its inputs is from the nearest training input set, measured against the training evidence of the model version "
    "that made the prediction. A candidate inside every range but in a sparse or isolated part of the data is labelled "
    "Caution or Requires validation, and a candidate whose combination could not be assessed is labelled Not assessed. "
    "It does not change or adjust the model-estimated probability. 'Within data range' does not mean qualified, does "
    "not show that the combination of inputs is covered by the historical data (the distance check is a diagnostic, "
    "not a guarantee), does not show that the model is accurate for the candidate, and is not physical evidence: physical validation is "
    "required for every candidate. The distance values and thresholds are shown on the model version's Applicability "
    "tab and are not repeated in this report."
)

CROSS_VALIDATION_CAVEAT = (
    "These cross-validation figures use folds assigned at random by row. Repeated formulations (replicate "
    "rows) can therefore fall on both sides of a split, so they can be more optimistic than performance on "
    "new batches. They are not a test of this model on candidates it has not seen."
)

GROUPED_CROSS_VALIDATION_CAVEAT = (
    "These cross-validation figures keep rows with identical inputs (replicates) together, so each scored point is "
    "an input set the model was not fitted on. Near-identical formulations are not grouped, no batch or lot "
    "identifier is used, and the test uses only this dataset: it is not a test on new batches, other suppliers or "
    "other customers' materials."
)
CALIBRATION_REFERENCE_NOTE = (
    "For a well-calibrated normal predictive distribution these would be about 68% and 95%. Lower figures mean the "
    "stated uncertainty is too small."
)

RANKING_ORDER_NOTE = evidence_rules.ORDERING_RULE
UNRANKED_INTRO = (
    "The historical data does not cover the inputs of the candidates below, so the model's figures for them "
    "are extrapolations. They are shown unchanged and for information only. These candidates are not ranked: "
    "a position, high or low, would claim more than the evidence supports. This analysis does not judge any "
    "of them unsuitable. Each needs physical validation before it can be compared with the ranked candidates."
)
UNRANKED_UNCERTAINTY_NOTE = (
    "The uncertainty shown is the model's own spread. Outside the range the historical data covers it is not "
    "a reliable measure of error, so these candidates may differ from the estimates by much more than it suggests."
)
PREDICTED_VALUE_MISSING_NOTE = (
    "'Not recorded' means the stored ranking for that candidate was produced before predicted "
    "values were saved. No value has been estimated or back-filled; re-run the ranking to obtain one."
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


def format_predicted_value(v: float) -> str:
    """A predicted value to four significant digits, never in exponent notation (so 12345.6 reads
    '12350', not '1.235e+04'). The product screen uses the same rule, so screen and report agree."""
    return format(Decimal(f"{float(v):.4g}"), "f")


def _trigger_label(trigger) -> str:
    """'regulatory_restriction' -> 'Regulatory restriction' (the stored code is not customer-facing text)."""
    text = str(trigger or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _fit_table(table, widths_in, font_pt):
    """Fixed column widths (inches) and a smaller font, so a wide table stays readable on a portrait page instead of
    wrapping every column into a sliver."""
    table.autofit = False
    for col, w in zip(table.columns, widths_in):
        col.width = Inches(w)
    for row in table.rows:
        for cell, w in zip(row.cells, widths_in):
            cell.width = Inches(w)
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(font_pt)


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
    if ctx.get("stale_notice"):
        p = doc.add_paragraph()
        run = p.add_run("NOTICE -- NEWER EVIDENCE EXISTS. ")
        run.bold = True
        p.add_run(ctx["stale_notice"] + " Regenerate this report after re-running the ranking.")
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
            ("Dataset uploaded (UTC)", dataset["created_at"]),
            ("Records ingested from that file", str(dataset["row_count"])),
        ]
    else:
        rows.append(("Historical dataset", "None uploaded"))
    versions = ctx.get("model_versions") or []
    rows.append(("Model", "Gaussian process surrogate; version(s): " + (", ".join(versions) if versions else "no ranking run yet")))
    if ctx.get("ranked_at_utc"):
        rows.append(("Latest ranking run (UTC)", ctx["ranked_at_utc"]))
        run_id = report_filename.run_identifier(ctx["ranked_at_utc"])
        if run_id:
            rows.append(("Ranking run identifier", f"{run_id} (the 'run_' part of this file's name)"))
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
        spec_units = spec.get("units") or {}
        if spec_units or any(c.get("input_record") for c in cands):
            doc.add_paragraph(
                "Values above are in the case's canonical units"
                + (": " + ", ".join(f"{k} in {v}" for k, v in spec_units.items()) if spec_units else "")
                + ". Candidate values are entered with a unit and converted only by an exact factor; the "
                "original entry is kept in the record."
            ).runs[0].font.size = Pt(9)
            for c in cands:
                rec = c.get("input_record") or {}
                conv = [
                    f"{col}: entered {e['value']} {e['unit']} -> {e['converted_value']} {e['converted_unit']}"
                    for col, e in rec.items() if e.get("converted_unit") and e.get("unit") != e.get("converted_unit")
                ]
                if conv:
                    doc.add_paragraph(f"{c['candidate_name']} -- " + "; ".join(conv), style="List Bullet")


def _add_ingestion_review(doc, ctx):
    """Evidence intake review (B2/B3): what was done to the customer's file
    before any value reached the model -- mapping, units, test conditions,
    exclusions, conflicts, integrity hash. Read from the stored review record."""
    doc.add_heading("Evidence intake review", level=1)
    review = ctx.get("ingestion_review")
    if not review:
        doc.add_paragraph(
            "No intake review record exists for this dataset (it was uploaded before review records were kept). "
            "Column mapping, units and test conditions for it were not recorded by the system."
        )
        return
    counts = review.get("counts", {})
    rows = [
        ("Stored file SHA-256", review.get("file_sha256", "not recorded")),
        ("Data rows in file", str(counts.get("data_rows", ""))),
        ("Rows used by the model", str(counts.get("ingested", ""))),
        ("Rows skipped (value not a usable number)", str(counts.get("skipped_unparseable", 0))),
        ("Rows excluded by reviewer", str(counts.get("excluded_by_reviewer", 0))),
        ("Rows excluded (other test conditions)", str(counts.get("excluded_other_conditions", 0))),
    ]
    excl = review.get("exclusions", {})
    if excl.get("reason"):
        rows.append(("Reviewer exclusion reason", excl["reason"]))
    source = review.get("source")
    if source:
        rows.insert(0, ("Source file", f"{source.get('file', '')} ({str(source.get('kind', '')).upper()})"))
        for t in source.get("tables", []):
            label = f"{t['location']} (header at source row {t['header_row']}, {t['data_rows']} data rows)"
            if t.get("confidence") == "text_layout":
                label += " -- table INFERRED from PDF text layout"
            rows.insert(1, ("Table used", label))
            if t.get("caption"):
                rows.insert(2, ("Context shown near the table", t["caption"]))
    _kv_table(doc, rows)
    if source:
        doc.add_paragraph(
            "Every historical record carries a source reference (file, table, source row) in the stored evidence "
            "record, so each value can be traced to a place in the file the customer supplied."
        ).runs[0].font.size = Pt(9)

    mapping = review.get("column_mapping", {})
    if mapping:
        doc.add_paragraph("Column mapping (model column <- source header):")
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "Model column", "Source header", "How matched"
        for canon, info in mapping.items():
            row = table.add_row().cells
            row[0].text = canon
            row[1].text = info["source_header"]
            row[2].text = "Exact header" if info["method"] == "exact" else "Reviewer-confirmed mapping"

    units = review.get("units", {})
    if units:
        doc.add_paragraph("Units (as recorded at intake):")
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Column", "Unit used", "Units seen in file", "Converted"
        for canon, u in units.items():
            row = table.add_row().cells
            row[0].text = canon
            row[1].text = u.get("canonical") or "Not declared"
            row[2].text = ", ".join(u.get("source_units_seen") or []) or "none stated"
            conv = u.get("conversions") or []
            row[3].text = "; ".join(f"{c['rows']} row(s) {c['from']} -> {c['to']}" for c in conv) or "No"
        doc.add_paragraph(
            "Conversions are applied only where the change case declares the unit and an exact conversion exists; "
            "otherwise differing units block the upload. Candidate input values are taken as supplied, in the "
            "units shown above; they are not unit-checked."
        ).runs[0].font.size = Pt(9)

    conds = review.get("conditions", {})
    if review.get("condition_columns"):
        ref = conds.get("reference") or {}
        doc.add_paragraph(
            "Test conditions recorded: " + ", ".join(review["condition_columns"]) + ". Reference condition set used: "
            + (", ".join(f"{k}={v}" for k, v in ref.items()) if ref else "not applicable") + "."
        )
    rep = review.get("replicates") or {}
    if rep:
        doc.add_paragraph(
            f"Replicate rows with differing targets: {rep.get('groups_with_differing_targets', 0)} input set(s); "
            f"largest spread {rep.get('max_spread_fraction_of_range', 0):.0%} of the target range. Sets above "
            f"{rep.get('conflict_threshold_fraction', 0):.0%} are blocked for review (threshold status: "
            f"{rep.get('threshold_status', 'heuristic')})."
        ).runs[0].font.size = Pt(9)
    warns = [f for f in review.get("flags", []) if f.get("severity") == "warning"]
    if warns:
        doc.add_paragraph("Review warnings recorded at intake:")
        for f in warns:
            doc.add_paragraph(f"[{f['code']}] {f['message']}", style="List Bullet")


def _unit_and_provenance_gaps(spec, ctx):
    review = ctx.get("ingestion_review")
    if not review:
        return [
            "Units of measure and test conditions are not recorded or verified by this system; the customer's "
            "historical data is used as supplied.",
            "Row-level source provenance (source document, page or table) is not recorded for the historical data.",
        ]
    cols = list(spec["feature_columns"]) + [spec["target_metric"]]
    undeclared = [c for c in cols if not (review.get("units", {}).get(c) or {}).get("canonical")]
    gaps = []
    if undeclared:
        gaps.append("No unit is recorded for: " + ", ".join(undeclared) + "; those values are used as supplied and "
                    "their comparability is not verified.")
    else:
        gaps.append("Units are recorded for every modelled column (see Evidence intake review). Candidate input "
                    "values are taken as supplied in those units and are not unit-checked.")
    if not review.get("condition_columns"):
        gaps.append("No test-condition columns were declared at intake, so test conditions are not recorded or "
                    "compared by this system.")
    prov = review.get("provenance_columns") or []
    if review.get("source"):
        text = ("Row-level source provenance is recorded for every historical record as file, table and row "
                "reference. The original report, page or test certificate a value was transcribed from is "
                "traceable only as far as the uploaded file")
        if prov:
            text += " and its own source columns (" + ", ".join(prov) + ")"
        gaps.append(text + "; source columns are not used by the model.")
    elif prov:
        gaps.append("Source document/page/table information is held only in the stored file's columns ("
                    + ", ".join(prov) + "); it is not stored as structured per-row records or used by the model.")
    else:
        gaps.append("Row-level source provenance (source document, page or table) is not recorded for the "
                    "historical data.")
    return gaps


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
    spec_for_units = ctx.get("spec") or {}
    undeclared = [
        f for f in list(spec_for_units.get("feature_columns") or []) + ([spec_for_units["target_metric"]] if spec_for_units.get("target_metric") else [])
        if not str((spec_for_units.get("units") or {}).get(f) or "").strip()
    ]
    if undeclared:
        gaps.append(
            "Canonical units were not declared for: " + ", ".join(undeclared) + ". Any ranking shown in this report "
            "was produced without unit checking, so its inputs and evidence cannot be shown to be comparable. "
            "New rankings are refused until units are declared."
        )
    for r in ranked_results:
        if r.get("ranking_status") == evidence_rules.STATUS_NOT_RANKED:
            gaps.append(
                f"{r['candidate_name']}: not ranked -- the historical evidence does not cover its inputs, so no "
                "position is given and its model estimate is an extrapolation. Physical validation is required "
                "before it can be compared with other candidates."
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
        *_unit_and_provenance_gaps(spec, ctx),
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
    hdr[1].text = "Model-estimated probability (latest estimate made before the result was recorded)"
    hdr[2].text = "Recorded result"
    hdr[3].text = "Recorded as meeting requirement"
    hdr[4].text = "Recorded (UTC)"
    for o in outcomes:
        row = table.add_row().cells
        row[0].text = o["candidate_name"]
        if o.get("model_probability") is not None:
            made = f" (estimated {o['estimate_made_at']})" if o.get("estimate_made_at") else ""
            row[1].text = f"{change_case_rules.format_model_probability(o['model_probability'])}{made}"
        else:
            row[1].text = o.get("comparison_note") or "Not ranked"
        row[2].text = o["actual_result"] or ""
        row[3].text = "Yes" if o["passed_spec"] else "No"
        row[4].text = str(o["recorded_at"])
    doc.add_paragraph(
        "Outcomes are entered by the customer's team and are append-only records. The application does not "
        "verify them against a test method, specimen record or the requirement: 'Recorded as meeting "
        "requirement' is the customer's own entry. They are shown next to the model estimate for comparison "
        "and do not alter it. An estimate is shown only if it was "
        "made before the result was recorded; the system does not record which estimate, if any, led to a test."
    ).runs[0].font.size = Pt(9)


def _decision_reference(ctx) -> str:
    """Which analysis a decision refers to, from the report's own record: change case, ranking run, dataset."""
    if not ctx:
        return ""
    run_id = report_filename.run_identifier(ctx.get("ranked_at_utc"))
    dataset = ctx.get("dataset")
    return (f"Change case {ctx.get('change_case_id')}; ranking run {run_id or 'none'}; "
            + (f"historical dataset version {dataset['id']} ({dataset['original_filename']})" if dataset else "no historical dataset"))


def _add_human_decision_block(doc, ctx=None):
    doc.add_heading("Human qualification decision", level=1)
    doc.add_paragraph(HUMAN_DECISION_STATEMENT)
    reference = _decision_reference(ctx)
    _kv_table(doc, ([("Analysis this decision refers to", reference)] if reference else []) + [
        ("Decision (qualify / do not qualify / further testing)", ""),
        ("Candidate", ""),
        ("Basis and reference to physical test records", ""),
        ("Decision-maker (name, role)", ""),
        ("Date", ""),
        ("Signature", ""),
    ])


def _add_unranked_section(doc, unranked_results, spec, target_unit, unit_phrase):
    """Candidates the historical data does not support ranking: the model's own numbers, unchanged, labelled
    as extrapolation, with no position."""
    doc.add_heading("Not ranked: outside the historical evidence", level=2)
    doc.add_paragraph(UNRANKED_INTRO)
    metric = spec["target_metric"] if spec else "value"
    headers = [
        "Candidate",
        f"Predicted {metric}{unit_phrase} (extrapolation, not a measured result)",
        "Model-estimated probability (extrapolation)",
        f"Uncertainty (std{', ' + target_unit if target_unit else ''})",
        "Historical data coverage",
        "Decision support",
    ]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    for r in unranked_results:
        cov = r.get("domain_coverage")
        ds = r.get("decision_support")
        values = [
            r["candidate_name"],
            "Not recorded" if r.get("predicted_value") is None else format_predicted_value(r["predicted_value"]),
            change_case_rules.format_model_probability(r["predicted_probability"]),
            f"{r['uncertainty_std']:.3f}",
            DOMAIN_COVERAGE_LABELS[cov["status"]] if cov else "Not assessed",
            ds["label"] if ds else "Not assessed",
        ]
        for cell, text in zip(table.add_row().cells, values):
            cell.text = text
    doc.add_paragraph(UNRANKED_UNCERTAINTY_NOTE).runs[0].font.size = Pt(9)


def _basis_for_position(r, metric, unit_phrase):
    p = change_case_rules.format_model_probability(r["predicted_probability"])
    value = ("predicted value not recorded" if r.get("predicted_value") is None
             else f"predicted {metric}{unit_phrase} {format_predicted_value(r['predicted_value'])}")
    spread = f"std {r['uncertainty_std']:.3f}"
    if r.get("ranking_status") == evidence_rules.STATUS_NOT_RANKED:
        return (f"No rank: its inputs are outside the historical evidence, so the model's figures "
                f"(probability {p}, {value}, {spread}) are an extrapolation. Physical testing is the only way to "
                f"establish {metric}.")
    if r.get("rank") is None:  # supported, but no rank was recorded for it: say so, never print "Rank None"
        return f"No rank recorded: model-estimated probability {p}, {value}, {spread}."
    text = f"Rank {r['rank']}: model-estimated probability {p}, {value}, {spread}."
    if r.get("ranking_status") == evidence_rules.STATUS_RANKED_WITH_CAUTION:
        text += " Ranked provisionally: an input lies just beyond the observed range."
    return text


PLAN_TABLE_WIDTHS_IN = (0.55, 0.8, 1.45, 1.2, 1.75, 0.75)  # six columns that fit a 6.5-inch text width
PLAN_TABLE_FONT_PT = 8


def _rank_cell(r):
    """The model's own rank, never a row counter: 'No rank' when there is none, '(provisional)' when ranked on
    the caution tier (an input just beyond the observed range)."""
    if r.get("rank") is None:
        return "No rank"
    if r.get("ranking_status") == evidence_rules.STATUS_RANKED_WITH_CAUTION:
        return f"{r['rank']} (provisional)"
    return str(r["rank"])


def _add_validation_plan(doc, supported_results, unranked_results, spec, target_unit, unit_phrase, audit_context):
    """What a physical test must establish, in what order, and on what evidence -- from stored facts only."""
    metric = spec["target_metric"] if spec else "the target property"
    if spec:
        phrase = DIRECTION_PHRASE.get(spec.get("direction"), spec.get("direction", ""))
        doc.add_paragraph(
            f"What a physical test must establish: {metric}{unit_phrase} {phrase} {spec['target_value']}. This is the "
            "only acceptance criterion the system holds. " + validation_plan.NOT_SPECIFIED_BY_ANALYSIS
        )
        doc.add_paragraph(validation_plan.reference_condition_text((audit_context or {}).get("ingestion_review")))
    doc.add_paragraph(
        "Order of testing: candidates the historical data supports come first, in rank order, followed by candidates "
        "outside the evidence, which have no rank. The order follows the model estimate; it is not an "
        "experimental design chosen to reduce uncertainty."
    )
    headers = ["Model rank", "Candidate", "Basis for this position", "Evidence gaps for this candidate",
               f"Closest historical records (measured {metric})", "Outcome recorded (customer-entered)"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    # Supported candidates by their actual rank (a stable sort, so the caller's order never decides it), then any
    # supported candidate with no rank, then the unranked ones in the order given. No row number is shown.
    ordered = sorted(supported_results, key=lambda r: (r.get("rank") is None, r.get("rank") or 0)) + list(unranked_results)
    for r in ordered:
        gaps = validation_plan.candidate_gaps(r.get("domain_coverage"), DOMAIN_COVERAGE_LABELS)
        closest = r.get("closest_records") or []
        values = [
            _rank_cell(r),
            r["candidate_name"],
            _basis_for_position(r, metric, unit_phrase),
            "; ".join(gaps) if gaps else "None found by the per-feature range check (joint coverage is not checked).",
            "\n".join(f"{c['reference']}: {c['target']} (distance {c['distance']})" for c in closest) or "Not available.",
            "Yes (see recorded outcomes)" if r.get("outcome_recorded") else "No",
        ]
        for cell, text in zip(table.add_row().cells, values):
            cell.text = text
    _fit_table(table, PLAN_TABLE_WIDTHS_IN, PLAN_TABLE_FONT_PT)
    doc.add_paragraph(validation_plan.CLOSEST_RECORDS_NOTE).runs[0].font.size = Pt(9)


def _add_result_reading(doc, spec, unit_phrase):
    metric = spec["target_metric"]
    phrase = DIRECTION_PHRASE.get(spec.get("direction"), spec.get("direction", ""))
    doc.add_heading("How to read a physical result", level=2)
    doc.add_paragraph(
        f"A measured {metric}{unit_phrase} {phrase} {spec['target_value']}, obtained by the customer's own test method "
        "under conditions comparable with the historical data, supports taking that candidate further. It does not "
        "qualify it: other requirements are not evaluated here, and the decision rests with the customer's authorised "
        "qualification personnel."
    )
    doc.add_paragraph(
        "A measured value on the other side of the threshold contradicts the model's estimate for this requirement and "
        "is evidence against the candidate for it. A measured value that differs from the predicted value by much more "
        "than the predicted standard deviation shows the model was unreliable for that candidate, so its estimates for "
        "similar candidates deserve less weight."
    )
    doc.add_paragraph(
        "Record each result, with the method and conditions used, under 'Recorded physical validation outcomes'. The "
        "application does not verify recorded results."
    )


def _add_summary(doc, ranked_results, supported_results, unranked_results, ctx, model_quality, data_quality,
                 n_historical_rows):
    """The first page for an engineering decision-maker: what was analysed, what the evidence supports, what is
    not established, and where the detail is. Every statement is taken from the same data as the tables below it,
    so it cannot drift from them. Nothing here adds a claim the detailed sections do not already make."""
    doc.add_heading("Summary for the decision-maker", level=1)
    spec = (ctx or {}).get("spec")
    if ctx and ctx.get("stale_notice"):
        p = doc.add_paragraph()
        p.add_run("NOTICE -- NEWER EVIDENCE EXISTS. ").bold = True
        p.add_run(ctx["stale_notice"] + " Regenerate this report after re-running the ranking.")
    target_unit = ((spec.get("units") or {}).get(spec["target_metric"])) if spec else None
    unit_phrase = f" ({target_unit})" if target_unit else ""
    metric = spec["target_metric"] if spec else "the target property"

    if data_quality and data_quality.get("status") == "insufficient":
        doc.add_paragraph(INSUFFICIENT_EVIDENCE_STATEMENT)
    if not ranked_results:
        doc.add_paragraph("No candidate has been ranked yet, so this report holds no ranking or validation plan.")
    else:
        n = len(ranked_results)
        scope = f"This report compares {n} candidate substitute{'s' if n != 1 else ''} against one requirement"
        if spec:
            phrase = DIRECTION_PHRASE.get(spec.get("direction"), spec.get("direction", ""))
            scope += f" ({metric}{unit_phrase} {phrase} {spec['target_value']})"
        doc.add_paragraph(
            scope + f", using {n_historical_rows} historical record{'s' if n_historical_rows != 1 else ''} supplied by "
            "the customer. It is an analysis, not a qualification: it does not qualify any candidate."
        )
        doc.add_paragraph("What the analysis found", style="Heading 2")
        line = f"{len(supported_results)} of {n} candidates lie inside the range the historical data covers and are ranked"
        if unranked_results:
            line += f"; {len(unranked_results)} lie outside it and have no rank"
        doc.add_paragraph(line + ".", style="List Bullet")
        ordered = sorted((r for r in supported_results if r.get("rank") is not None), key=lambda r: r["rank"])
        if ordered:
            top = []
            for r in ordered[:3]:
                value = "" if r.get("predicted_value") is None else \
                    f", predicted {metric}{unit_phrase} {format_predicted_value(r['predicted_value'])}"
                top.append(f"{r['rank']}. {r['candidate_name']} (model-estimated probability "
                           f"{change_case_rules.format_model_probability(r['predicted_probability'])}{value})")
            doc.add_paragraph(
                "Highest model-estimated probability: " + "; ".join(top) + ". The suggested order of physical testing "
                "follows these ranks (see Recommended Validation Experiments); it is not an experimental design chosen "
                "to reduce uncertainty.", style="List Bullet")
        if unranked_results:
            doc.add_paragraph(
                "Not ranked, so physical testing is the only way to establish "
                f"{metric}: " + ", ".join(r["candidate_name"] for r in unranked_results) + ".", style="List Bullet")
        if ctx:
            waiting = [c["candidate_name"] for c in ctx.get("candidates", []) if not c.get("has_prediction")]
            if waiting:
                doc.add_paragraph("Not yet ranked: " + ", ".join(waiting) + ".", style="List Bullet")
            with_outcome = {o["candidate_name"] for o in ctx.get("outcomes") or []}
            doc.add_paragraph(
                f"Outcomes recorded by the customer: {len(with_outcome)} of {len(ctx.get('candidates') or [])} "
                "candidates (the application does not verify them).", style="List Bullet")

    doc.add_paragraph("What this analysis does not establish", style="Heading 2")
    for text in (
        "The model-estimated probability is the model's own estimate, not a measured or validated success rate.",
        (f"Only {metric} is analysed, and only the modelled inputs; other qualification requirements are not evaluated."
         if spec else "Only one requirement and the modelled inputs are analysed; other requirements are not evaluated."),
        "Range coverage is checked one input at a time, not jointly.",
        "The decision to qualify rests with the customer's authorised qualification personnel, after physical validation.",
    ):
        doc.add_paragraph(text, style="List Bullet")

    cautions = []
    if model_quality and "error" in model_quality:
        cautions.append("Model performance could not be assessed on this dataset: " + str(model_quality["error"]))
    elif model_quality:
        how = ("the validation folds keep repeated input sets together, so this is not a test on new batches, other "
               "suppliers or other materials" if model_quality.get("cv_scheme") == "grouped" else
               "the validation folds are random by row, which can look better than performance on new formulations")
        cautions.append(
            f"On the dataset's own history the cross-validated R-squared is {model_quality['r2_score']} and the mean "
            f"absolute error {model_quality['mean_absolute_error']}{' ' + target_unit if target_unit else ''}; {how}.")
        fit = model_quality.get("fit_diagnostics") or {}
        if any(fit.get(k) for k in ("n_convergence_warnings", "n_fits_with_hyperparameter_at_bound",
                                    "n_fits_noise_at_lower_bound")):
            cautions.append("The model optimiser reported problems in some validation fits; treat the uncertainty "
                            "figures with extra caution (see Model performance evidence).")
    review = (ctx or {}).get("ingestion_review")
    if ctx and spec and not review:
        cautions.append("No intake review record exists for this dataset: units, mapping and test conditions were not recorded.")
    elif review and spec:
        undeclared = [c for c in list(spec["feature_columns"]) + [spec["target_metric"]]
                      if not (review.get("units", {}).get(c) or {}).get("canonical")]
        if undeclared:
            cautions.append("No unit is recorded for: " + ", ".join(undeclared) + ".")
        if not review.get("condition_columns"):
            cautions.append("No test conditions were declared for the historical data, so comparability of a new "
                            "physical test with it is not established.")
        warns = [f for f in review.get("flags", []) if f.get("severity") == "warning"]
        if warns:
            cautions.append(f"{len(warns)} intake review warning{'s' if len(warns) != 1 else ''} recorded (see Evidence intake review).")
    if cautions:
        doc.add_paragraph("Evidence cautions for this dataset", style="Heading 2")
        for text in cautions:
            doc.add_paragraph(text, style="List Bullet")

    doc.add_paragraph(
        "Detail follows in this order: Ranked Candidates, Recommended Validation Experiments, recorded outcomes and the "
        "human qualification decision. The technical evidence behind every figure is in the Appendices."
    ).runs[0].font.size = Pt(9)
    if ctx:
        rows = [("Change case ID", str(ctx["change_case_id"]))]
        run_id = report_filename.run_identifier(ctx.get("ranked_at_utc"))
        if run_id:
            rows.append(("Ranking run identifier", run_id))
        dataset = ctx.get("dataset")
        rows.append(("Historical dataset", f"Version {dataset['id']}: {dataset['original_filename']}" if dataset else "None uploaded"))
        rows.append(("Report generated (UTC)", ctx["generated_at_utc"]))
        _kv_table(doc, rows)


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
    meta.add_run(f"Trigger: {_trigger_label(change_case['trigger_type'])}").font.size = Pt(10)
    if change_case.get("restricted_substance"):
        meta.add_run(f" ({change_case['restricted_substance']})").font.size = Pt(10)

    unranked_results = [r for r in ranked_results if r.get("ranking_status") == evidence_rules.STATUS_NOT_RANKED]
    supported_results = [r for r in ranked_results if r.get("ranking_status") != evidence_rules.STATUS_NOT_RANKED]
    _add_summary(doc, ranked_results, supported_results, unranked_results, audit_context, model_quality, data_quality,
                 n_historical_rows)

    doc.add_heading("Ranked Candidates", level=1)
    # Only candidates the historical data supports are ranked. The rest (inputs outside the observed range)
    # are disclosed in their own section: the model's numbers unchanged, labelled as extrapolation, no
    # position (see ranking_evidence_rules). Results without a ranking_status are treated as ranked.
    if not ranked_results:
        doc.add_paragraph("No candidates ranked yet.")
    else:
        spec = (audit_context or {}).get("spec")
        target_unit = ((spec.get("units") or {}).get(spec["target_metric"])) if spec else None
        unit_phrase = f" ({target_unit})" if target_unit else ""
        summary = evidence_rules.summarize_statuses([r.get("ranking_status") for r in ranked_results])

        if spec:
            phrase = DIRECTION_PHRASE.get(spec.get("direction"), spec.get("direction", ""))
            doc.add_paragraph(
                f"Requirement: {spec['target_metric']}{unit_phrase} {phrase} {spec['target_value']} "
                f"(direction: {spec.get('direction')}). "
                + ("" if target_unit else
                   "No canonical unit is declared for this property, so predicted values carry no unit here. ")
                + "Predicted values and uncertainties below are MODEL ESTIMATES, not measured physical results."
            )

        if not supported_results:
            # Nobody is supported by the evidence: say so plainly instead of presenting an empty or invented order.
            doc.add_paragraph(summary["message"])
            doc.add_paragraph(MODEL_ESTIMATE_NOTE).runs[0].font.size = Pt(9)
        else:
            show_coverage = any(r.get("domain_coverage") for r in supported_results)
            # C1: the categorical decision-support column is shown whenever any
            # result carries it (i.e. always for real ranked predictions).
            show_decision = any(r.get("decision_support") for r in supported_results)
            # Predicted target-property value: shown whenever results carry the field at all. A
            # missing value (prediction stored before values were recorded) is labelled, never blank
            # and never filled in.
            show_value = any("predicted_value" in r for r in supported_results)
            show_rank = any(r.get("rank") for r in supported_results)

            headers = []
            if show_rank:
                headers.append("Rank")
            headers.append("Candidate")
            if show_value:
                headers.append(f"Predicted {spec['target_metric']}{unit_phrase} (model estimate)" if spec
                               else "Predicted value (model estimate)")
            headers.append("Model-estimated probability")
            headers.append(f"Uncertainty (std{', ' + target_unit if target_unit else ''})")
            if show_coverage:
                headers.append("Historical data coverage")
            if show_decision:
                headers.append("Decision support")

            table = doc.add_table(rows=1, cols=len(headers))
            table.style = "Light Grid Accent 1"
            for cell, text in zip(table.rows[0].cells, headers):
                cell.text = text
            any_value_missing = False
            for r in supported_results:
                values = []
                if show_rank:
                    values.append(str(r.get("rank", "")))
                values.append(r["candidate_name"])
                if show_value:
                    if r.get("predicted_value") is None:
                        any_value_missing = True
                        values.append("Not recorded")
                    else:
                        values.append(format_predicted_value(r["predicted_value"]))
                values.append(change_case_rules.format_model_probability(r["predicted_probability"]))
                values.append(f"{r['uncertainty_std']:.3f}")
                if show_coverage:
                    cov = r.get("domain_coverage")
                    values.append(DOMAIN_COVERAGE_LABELS[cov["status"]] if cov else "Not assessed")
                if show_decision:
                    ds = r.get("decision_support")
                    values.append(ds["label"] if ds else "Not assessed")
                row = table.add_row().cells
                for cell, text in zip(row, values):
                    cell.text = text

            doc.add_paragraph(MODEL_ESTIMATE_NOTE).runs[0].font.size = Pt(9)
            if show_rank:
                doc.add_paragraph(RANKING_ORDER_NOTE).runs[0].font.size = Pt(9)
            if any_value_missing:
                doc.add_paragraph(PREDICTED_VALUE_MISSING_NOTE).runs[0].font.size = Pt(9)
            if show_decision:
                doc.add_paragraph(DECISION_SUPPORT_EXPLANATION).runs[0].font.size = Pt(9)
            if unranked_results or any(r.get("ranking_status") == evidence_rules.STATUS_RANKED_WITH_CAUTION
                                       for r in supported_results):
                doc.add_paragraph(summary["message"])

        if unranked_results:
            _add_unranked_section(doc, unranked_results, spec, target_unit, unit_phrase)

        doc.add_heading("Recommended Validation Experiments", level=1)
        _add_validation_plan(doc, supported_results, unranked_results, spec, target_unit, unit_phrase, audit_context)
        for r in ranked_results:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{r['candidate_name']}: ").bold = True
            p.add_run(r["recommended_experiment"])
        if spec:
            _add_result_reading(doc, spec, unit_phrase)

    if audit_context:
        _add_recorded_outcomes(doc, audit_context)
        _add_human_decision_block(doc, audit_context)

    # Everything above is for the decision. Everything below is the technical evidence behind it.
    doc.add_page_break()
    doc.add_heading("Appendices", level=1)
    doc.add_paragraph(
        "Technical evidence behind the sections above: the requirement and analysis record, how the customer's file "
        "was read, range coverage, model performance, evidence gaps and the data basis."
    ).runs[0].font.size = Pt(9)

    if audit_context:
        _add_requirement_and_record(doc, audit_context)
        _add_ingestion_review(doc, audit_context)

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
                f"heuristic tolerance band of 10% of this dataset's observed target range -- not an "
                f"engineering acceptance threshold)."
            )
            caveat = (GROUPED_CROSS_VALIDATION_CAVEAT if model_quality.get("cv_scheme") == "grouped"
                      else CROSS_VALIDATION_CAVEAT)
            doc.add_paragraph(caveat).runs[0].font.size = Pt(9)
            fit = model_quality.get("fit_diagnostics") or {}
            if any(fit.get(k) for k in ("n_convergence_warnings", "n_fits_with_hyperparameter_at_bound",
                                        "n_fits_noise_at_lower_bound")):
                doc.add_paragraph(
                    f"Model-fitting note: across {fit['n_fits']} cross-validation fits, the optimiser reported a "
                    f"convergence warning in {fit['n_convergence_warnings']}, a model setting reached its allowed "
                    f"limit in {fit['n_fits_with_hyperparameter_at_bound']}, and the noise estimate reached its "
                    f"minimum in {fit.get('n_fits_noise_at_lower_bound', 0)} (which treats the data as almost "
                    f"noise-free and tends to make predicted uncertainty too small). The figures above may be "
                    f"less reliable than they look."
                ).runs[0].font.size = Pt(9)
            if uncertainty_calibration and "error" not in uncertainty_calibration:
                if uncertainty_calibration.get("sigma_error_correlation_defined", True):
                    correlation_text = (
                        f"{uncertainty_calibration['sigma_error_correlation']} "
                        "(positive means higher predicted uncertainty is associated with larger actual error)"
                    )
                else:
                    correlation_text = (
                        "not defined (the predicted uncertainty or the held-out error was constant, so no "
                        "rank correlation can be computed)"
                    )
                doc.add_paragraph(
                    "Predicted-uncertainty calibration: correlation between predicted uncertainty "
                    f"and actual held-out error = {correlation_text}; "
                    f"{uncertainty_calibration['within_1sigma_pct']}% of held-out points fell "
                    f"within 1 predicted standard deviation, "
                    f"{uncertainty_calibration['within_2sigma_pct']}% within 2. " + CALIBRATION_REFERENCE_NOTE
                )
            elif uncertainty_calibration and "error" in uncertainty_calibration:
                doc.add_paragraph(
                    "Uncertainty calibration could not be evaluated for this dataset: "
                    f"{uncertainty_calibration['error']}"
                )

    if audit_context:
        _add_evidence_gaps(doc, ranked_results, audit_context)

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
