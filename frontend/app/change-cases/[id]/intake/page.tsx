"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import {
  getChangeCase, parseSpec, extractEvidence, previewEvidence, uploadQualificationDataset,
} from "@/lib/api/change-cases";
import { ApiRequestError } from "@/lib/api/http";
import type {
  ChangeCase, QualificationSpec, ExtractResult, ExtractedTable, PreviewResult, IntakeOptions,
  QualificationDatasetUploadResult, ReviewFlag,
} from "@/lib/api/change-cases";

const inputStyle: React.CSSProperties = {
  padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)",
  fontSize: "var(--text-xs)", width: "100%",
};
const smallText: React.CSSProperties = { fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" };
const NOT_MAPPED = "";

function FindingList({ flags }: { flags: ReviewFlag[] }) {
  const ordered = [...flags].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "blocking" ? -1 : 1));
  if (ordered.length === 0) return <p style={smallText}>No findings.</p>;
  return (
    <div>
      {ordered.map((f, i) => (
        <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid var(--color-border)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Badge tone={f.severity === "blocking" ? "danger" : "warning"}>
              {f.severity === "blocking" ? "Needs review" : "Note"}
            </Badge>
            <span style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>{f.code}</span>
            {f.column && <span style={smallText}>column: {f.column}</span>}
          </div>
          <div style={{ fontSize: "var(--text-xs)", marginTop: 4 }}>{f.message}</div>
          {f.row_sources && f.row_sources.length > 0 && (
            <div style={smallText}>
              Where: {f.row_sources.slice(0, 8).join("; ")}{f.row_sources.length > 8 ? "; …" : ""}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function EvidenceIntakePage() {
  const params = useParams();
  const changeCaseId = Number(params.id);
  const { checking } = useRequireAuth();

  const [changeCase, setChangeCase] = useState<ChangeCase | null>(null);
  const [spec, setSpec] = useState<QualificationSpec | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [extraction, setExtraction] = useState<ExtractResult | null>(null);
  const [extracting, setExtracting] = useState(false);

  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [headerRow, setHeaderRow] = useState<number | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [declaredUnits, setDeclaredUnits] = useState<Record<string, string>>({});
  const [conditionCols, setConditionCols] = useState<string[]>([]);
  const [referenceConditions, setReferenceConditions] = useState<Record<string, string>>({});
  const [excludeText, setExcludeText] = useState("");
  const [exclusionReason, setExclusionReason] = useState("");

  const [review, setReview] = useState<PreviewResult | null>(null);
  const [reviewKey, setReviewKey] = useState<string>("");
  const [reviewing, setReviewing] = useState(false);

  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState<QualificationDatasetUploadResult | null>(null);

  useEffect(() => {
    if (checking || !changeCaseId) return;
    getChangeCase(changeCaseId)
      .then((c) => { setChangeCase(c); setSpec(parseSpec(c)); })
      .catch(() => setError("Could not load this diagnostic."));
  }, [checking, changeCaseId]);

  const selectedTables: ExtractedTable[] = useMemo(
    () => (extraction ? extraction.tables.filter((t) => selectedRefs.includes(t.ref)) : []),
    [extraction, selectedRefs],
  );
  const firstTable = selectedTables[0] ?? null;
  const effectiveHeaderRow = headerRow ?? firstTable?.default_header_row ?? null;
  const headerCells: string[] = useMemo(() => {
    if (!firstTable || effectiveHeaderRow === null) return [];
    const row = firstTable.preview.find((r) => r.src_row === effectiveHeaderRow);
    return row ? row.cells.map((c) => c.trim()) : [];
  }, [firstTable, effectiveHeaderRow]);

  function buildOptions(): { options: IntakeOptions; problem: string | null } {
    const o: IntakeOptions = {};
    if (selectedRefs.length === 1) o.table_ref = selectedRefs[0];
    else if (selectedRefs.length > 1) o.table_ref = selectedRefs;
    if (selectedRefs.length === 1 && headerRow !== null) o.header_row = headerRow;
    const cm = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v !== NOT_MAPPED));
    if (Object.keys(cm).length) o.column_mapping = cm;
    const du = Object.fromEntries(Object.entries(declaredUnits).filter(([, v]) => v.trim() !== ""));
    if (Object.keys(du).length) o.declared_units = du;
    if (conditionCols.length) {
      o.condition_columns = conditionCols;
      const ref = Object.fromEntries(Object.entries(referenceConditions).filter(([k, v]) => conditionCols.includes(k) && v !== ""));
      if (Object.keys(ref).length) o.reference_conditions = ref;
    }
    let problem: string | null = null;
    const tokens = excludeText.split(/[\s,]+/).filter(Boolean);
    if (tokens.length) {
      const nums = tokens.map((t) => Number(t));
      if (nums.some((n) => !Number.isInteger(n) || n < 1)) problem = "Excluded rows must be whole row numbers (1 or more), e.g. 12, 13.";
      else o.exclude_rows = nums;
      if (exclusionReason.trim()) o.exclusion_reason = exclusionReason.trim();
    }
    return { options: o, problem };
  }

  const currentKey = JSON.stringify(buildOptions().options) + "|" + (file ? `${file.name}:${file.size}` : "");
  const reviewIsStale = review !== null && reviewKey !== currentKey;

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    resetAll();
    setFile(f);
    setExtracting(true);
    try {
      const result = await extractEvidence(changeCaseId, f);
      setExtraction(result);
      const only = result.tables[0];
      if (result.tables.length === 1 && only) setSelectedRefs([only.ref]);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not read this file.");
      setFile(null);
    } finally {
      setExtracting(false);
    }
  }

  function resetAll() {
    setError(null); setFile(null); setExtraction(null); setSelectedRefs([]); setHeaderRow(null);
    setMapping({}); setDeclaredUnits({}); setConditionCols([]); setReferenceConditions({});
    setExcludeText(""); setExclusionReason(""); setReview(null); setAccepted(null);
  }

  async function runReview() {
    if (!file) return;
    const { options, problem } = buildOptions();
    if (problem) { setError(problem); return; }
    setError(null); setReviewing(true);
    try {
      const result = await previewEvidence(changeCaseId, file, options);
      setReview(result);
      setReviewKey(JSON.stringify(options) + "|" + `${file.name}:${file.size}`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not review this file.");
    } finally {
      setReviewing(false);
    }
  }

  async function accept() {
    if (!file || !review?.would_be_accepted || reviewIsStale) return;
    const { options, problem } = buildOptions();
    if (problem) { setError(problem); return; }
    setError(null); setAccepting(true);
    try {
      setAccepted(await uploadQualificationDataset(changeCaseId, file, options));
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Import failed.");
    } finally {
      setAccepting(false);
    }
  }

  function toggleRef(ref: string) {
    setSelectedRefs((cur) => (cur.includes(ref) ? cur.filter((r) => r !== ref) : [...cur, ref]));
    setHeaderRow(null); setMapping({}); setReview(null);
  }

  function toggleCondition(col: string) {
    setConditionCols((cur) => (cur.includes(col) ? cur.filter((c) => c !== col) : [...cur, col]));
  }

  if (checking) return null;

  const required = extraction?.required_columns ?? (spec
    ? [...spec.feature_columns, spec.target_metric].map((n) => ({
        name: n, role: (n === spec.target_metric ? "target" : "feature") as "target" | "feature", unit: spec.units?.[n],
      }))
    : []);
  const mappedHeaders = new Set(Object.values(mapping).filter(Boolean));
  const conditionCandidates = headerCells.filter((h) => h && !mappedHeaders.has(h) && !required.some((r) => r.name === h));
  const observed = review?.review.conditions.observed;
  const differing = review?.review.flags.some((f) => f.code === "CONDITIONS_DIFFER");
  const hasRowFindings = review?.review.flags.some((f) => (f.rows?.length ?? 0) > 0) ?? false;

  return (
    <AppShell>
      <Topbar>
        <Breadcrumbs items={[
          { label: "Diagnostics", href: "/change-cases" },
          { label: changeCase?.name ?? "…", href: `/change-cases/${changeCaseId}` },
          { label: "Evidence review" },
        ]} />
      </Topbar>
      <div className="app-shell__content">
        <PageHeader
          title="Evidence review"
          subtitle="Import the customer's historical qualification evidence. Nothing reaches the model until every item below is resolved and you accept it."
        />

        <Card padding="md" style={{ marginBottom: 16 }}>
          <p style={{ fontSize: "var(--text-sm)", margin: 0 }}>
            Supported files: <strong>CSV, XLSX, DOCX (Word tables), PDF (text-based tables)</strong>. The software extracts
            tables and shows them to you; it does <strong>not</strong> interpret narrative text, charts, images or scanned pages,
            and it never chooses a table, header row, mapping, unit or test condition for you.
          </p>
        </Card>

        {error && <ErrorCallout message={error} />}

        {accepted ? (
          <Card padding="lg">
            <CardHeader><CardTitle>Evidence accepted</CardTitle></CardHeader>
            <p style={{ fontSize: "var(--text-sm)" }}>
              {accepted.rows_ingested} records imported{accepted.rows_skipped ? `, ${accepted.rows_skipped} skipped` : ""}.
              Each record keeps a reference to its place in the source file, and the review below is stored with the dataset.
            </p>
            {accepted.review && <FindingList flags={accepted.review.flags.filter((f) => f.severity === "warning")} />}
            <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
              <Link href={`/change-cases/${changeCaseId}`}><Button variant="primary">Back to the diagnostic</Button></Link>
              <Button variant="ghost" onClick={resetAll}>Import another file</Button>
            </div>
          </Card>
        ) : (
          <>
            <Card padding="lg" style={{ marginBottom: 16 }}>
              <CardHeader><CardTitle>1. Choose the customer&apos;s file</CardTitle></CardHeader>
              <input type="file" accept=".csv,.xlsx,.docx,.pdf" onChange={handleFile} disabled={extracting} />
              {extracting && <Spinner label="Reading file…" />}
              {extraction && (
                <div style={{ ...smallText, marginTop: 8 }}>
                  {extraction.file_name} · {extraction.file_kind.toUpperCase()} · SHA-256 {extraction.file_sha256.slice(0, 16)}…
                  {extraction.warnings.map((w, i) => <div key={i}>• {w}</div>)}
                </div>
              )}
            </Card>

            {extraction && extraction.tables.length === 0 && (
              <Card padding="lg" style={{ marginBottom: 16 }}>
                <ErrorCallout message="No tables could be extracted from this file. Transcribe the relevant values into a spreadsheet (include units and test conditions) and import that instead." />
              </Card>
            )}

            {extraction && extraction.tables.length > 0 && (
              <Card padding="lg" style={{ marginBottom: 16 }}>
                <CardHeader><CardTitle>2. Which table holds the historical data?</CardTitle></CardHeader>
                <p style={smallText}>
                  Select the table(s) to use. Several tables can be combined only if they come from this file and have identical header rows.
                </p>
                {extraction.tables.map((t) => (
                  <div key={t.ref} style={{ padding: "10px 0", borderBottom: "1px solid var(--color-border)" }}>
                    <label style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
                      <input type="checkbox" checked={selectedRefs.includes(t.ref)} onChange={() => toggleRef(t.ref)} />
                      <span>
                        <span style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{t.location}</span>{" "}
                        <span style={smallText}>
                          {t.n_rows} rows × {t.n_cols} columns
                          {t.total_required !== undefined && ` · ${t.matched_required}/${t.total_required} required fields look matched`}
                        </span>
                        {t.confidence === "text_layout" && <> <Badge tone="warning">inferred from text layout</Badge></>}
                        {t.caption && <div style={smallText}>Nearby text: {t.caption}</div>}
                        {t.warnings.map((w, i) => <div key={i} style={{ ...smallText, color: "var(--color-warning, #b45309)" }}>⚠ {w}</div>)}
                        {t.column_warnings.map((w, i) => (
                          <div key={i} style={{ ...smallText, color: "var(--color-warning, #b45309)" }}>⚠ column {w.col_index + 1}: {w.message}</div>
                        ))}
                      </span>
                    </label>
                  </div>
                ))}

                {firstTable && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
                      <span style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>Preview of {firstTable.location}</span>
                      {selectedRefs.length === 1 && (
                        <label style={smallText}>
                          Header is on source row{" "}
                          <input type="number" min={1} value={effectiveHeaderRow ?? ""} style={{ ...inputStyle, width: 70, display: "inline-block" }}
                            onChange={(e) => { setHeaderRow(e.target.value ? Number(e.target.value) : null); setMapping({}); }} />
                        </label>
                      )}
                    </div>
                    <div style={{ overflowX: "auto", maxHeight: 280, border: "1px solid var(--color-border)", borderRadius: "var(--radius-sm)" }}>
                      <table style={{ borderCollapse: "collapse", fontSize: "var(--text-xs)", width: "100%" }}>
                        <tbody>
                          {firstTable.preview.map((r) => (
                            <tr key={r.src_row} style={{ background: r.src_row === effectiveHeaderRow ? "var(--color-accent-subtle)" : undefined }}>
                              <td style={{ padding: "3px 6px", color: "var(--color-text-tertiary)", borderRight: "1px solid var(--color-border)" }}>{r.src_row}</td>
                              {r.cells.map((c, i) => <td key={i} style={{ padding: "3px 6px", whiteSpace: "nowrap" }}>{c}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {firstTable.preview_truncated && <div style={smallText}>Showing the first {firstTable.preview.length} rows.</div>}
                  </div>
                )}
              </Card>
            )}

            {extraction && selectedRefs.length > 0 && (
              <Card padding="lg" style={{ marginBottom: 16 }}>
                <CardHeader><CardTitle>3. Map the customer&apos;s columns, units and conditions</CardTitle></CardHeader>
                <p style={smallText}>
                  For each field this diagnostic needs, choose the column in the customer&apos;s file that holds it (or leave it
                  unmapped — the review will stop you). Suggestions are shown but are <strong>never applied until you pick them</strong>.
                </p>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-xs)", marginTop: 8 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--color-text-secondary)" }}>
                      <th style={{ padding: 4 }}>Diagnostic field</th><th style={{ padding: 4 }}>Column in the file</th>
                      <th style={{ padding: 4 }}>Unit of the values in the file</th>
                    </tr>
                  </thead>
                  <tbody>
                    {required.map((r) => {
                      const proposed = review?.review.proposed_mapping[r.name];
                      const exact = headerCells.includes(r.name);
                      const unitHint = review?.review.proposed_units[r.name];
                      const unitsSeen = review?.review.units[r.name]?.source_units_seen ?? [];
                      return (
                        <tr key={r.name} style={{ borderTop: "1px solid var(--color-border)", verticalAlign: "top" }}>
                          <td style={{ padding: 6 }}>
                            <div style={{ fontWeight: 600 }}>{r.name}</div>
                            <div style={smallText}>{r.role === "target" ? "requirement" : "input feature"} · case unit: {r.unit ?? "not tracked"}</div>
                          </td>
                          <td style={{ padding: 6 }}>
                            {headerCells.length > 0 ? (
                              <select style={inputStyle} value={mapping[r.name] ?? NOT_MAPPED}
                                onChange={(e) => setMapping((m) => ({ ...m, [r.name]: e.target.value }))}>
                                <option value={NOT_MAPPED}>{exact ? `${r.name} (exact match)` : "— not mapped —"}</option>
                                {headerCells.filter(Boolean).map((h, i) => <option key={i} value={h}>{h}</option>)}
                              </select>
                            ) : (
                              <input style={inputStyle} value={mapping[r.name] ?? ""} placeholder="header text"
                                onChange={(e) => setMapping((m) => ({ ...m, [r.name]: e.target.value }))} />
                            )}
                            {proposed && (mapping[r.name] ?? NOT_MAPPED) !== proposed && (
                              <div style={smallText}>
                                Suggested: “{proposed}”{" "}
                                <button type="button" onClick={() => setMapping((m) => ({ ...m, [r.name]: proposed }))}
                                  style={{ border: 0, background: "none", color: "var(--color-accent)", cursor: "pointer", fontSize: "var(--text-xs)" }}>
                                  Accept suggestion
                                </button>
                              </div>
                            )}
                            {unitsSeen.length > 0 && (
                              <div style={smallText}>Units written in the cells: {unitsSeen.join(", ")}</div>
                            )}
                          </td>
                          <td style={{ padding: 6 }}>
                            <input style={inputStyle} value={declaredUnits[r.name] ?? ""}
                              placeholder={r.unit ? `only if cells carry no unit (case unit: ${r.unit})` : "usually blank"}
                              onChange={(e) => setDeclaredUnits((u) => ({ ...u, [r.name]: e.target.value }))} />
                            {unitHint && (declaredUnits[r.name] ?? "") !== unitHint && (
                              <div style={smallText}>
                                Header suggests “{unitHint}”{" "}
                                <button type="button" onClick={() => setDeclaredUnits((u) => ({ ...u, [r.name]: unitHint }))}
                                  style={{ border: 0, background: "none", color: "var(--color-accent)", cursor: "pointer", fontSize: "var(--text-xs)" }}>
                                  Use it
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>

                {conditionCandidates.length > 0 && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>Other columns in the file — are any of them test conditions?</div>
                    <p style={smallText}>
                      Tick a column if its value changes how results compare (test temperature, method, humidity…). Rows measured
                      under different conditions are never merged.
                    </p>
                    {conditionCandidates.map((h) => (
                      <label key={h} style={{ display: "block", fontSize: "var(--text-xs)", padding: "2px 0" }}>
                        <input type="checkbox" checked={conditionCols.includes(h)} onChange={() => toggleCondition(h)} /> {h}
                        {review?.review.proposed_condition_columns.includes(h) && <> <Badge tone="warning">name looks like a condition</Badge></>}
                      </label>
                    ))}
                  </div>
                )}

                {differing && observed && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>The file mixes test conditions — choose the ONE reference set to analyse</div>
                    {Object.entries(observed).map(([col, values]) => (
                      <div key={col} style={{ marginTop: 6 }}>
                        <span style={{ fontSize: "var(--text-xs)" }}>{col}: </span>
                        {values.map((v) => (
                          <label key={v} style={{ fontSize: "var(--text-xs)", marginRight: 10 }}>
                            <input type="radio" name={`ref-${col}`} checked={referenceConditions[col] === v}
                              onChange={() => setReferenceConditions((r) => ({ ...r, [col]: v }))} /> {v}
                          </label>
                        ))}
                      </div>
                    ))}
                    <p style={smallText}>Rows measured under any other condition will be excluded and the exclusion recorded.</p>
                  </div>
                )}

                {(hasRowFindings || excludeText) && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>Exclude rows (only with a reason, which is stored)</div>
                    <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                      <input style={{ ...inputStyle, flex: 1 }} value={excludeText} placeholder="data row numbers, e.g. 12, 13"
                        onChange={(e) => setExcludeText(e.target.value)} />
                      <input style={{ ...inputStyle, flex: 2 }} value={exclusionReason} placeholder="why (e.g. confirmed typo by customer, 2026-10-01)"
                        onChange={(e) => setExclusionReason(e.target.value)} />
                    </div>
                  </div>
                )}

                <div style={{ marginTop: 16 }}>
                  <Button variant="primary" onClick={runReview} loading={reviewing}>
                    {review ? "Re-run review" : "Run review"}
                  </Button>
                </div>
              </Card>
            )}

            {review && (
              <Card padding="lg" style={{ marginBottom: 16 }}>
                <CardHeader><CardTitle>4. Review result</CardTitle></CardHeader>
                {reviewIsStale && (
                  <p style={{ fontSize: "var(--text-xs)", color: "var(--color-warning, #b45309)" }}>
                    You changed something after this review ran. Re-run the review before accepting.
                  </p>
                )}
                <div style={{ marginBottom: 8, display: "flex", gap: 8, alignItems: "center" }}>
                  <Badge tone={review.would_be_accepted && !reviewIsStale ? "neutral" : "danger"}>
                    {review.would_be_accepted ? "No blocking items" : "Cannot be accepted yet"}
                  </Badge>
                  {review.data_quality_status && <span style={smallText}>data quality: {review.data_quality_status}</span>}
                  {review.review.counts.ingested !== undefined && (
                    <span style={smallText}>{review.review.counts.ingested} records would be used</span>
                  )}
                </div>
                <FindingList flags={review.review.flags} />
                {review.errors.length > 0 && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: "var(--text-xs)", fontWeight: 600 }}>Rows skipped (value missing or not a usable number)</div>
                    {review.errors.slice(0, 10).map((e, i) => (
                      <div key={i} style={smallText}>{e.source ?? `row ${e.row}`}: {e.error}</div>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
                  <Button variant="primary" onClick={accept} loading={accepting}
                    disabled={!review.would_be_accepted || reviewIsStale}>
                    Accept evidence and import
                  </Button>
                  <Button variant="ghost" onClick={resetAll}>Reject and start over</Button>
                </div>
                <p style={{ ...smallText, marginTop: 8 }}>
                  Accepting stores the validated records (each with a reference to its source row), the original file, and this
                  review. The analysis uses only these records.
                </p>
              </Card>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
