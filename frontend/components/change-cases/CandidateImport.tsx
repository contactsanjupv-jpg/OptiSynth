"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { previewCandidateImport, importCandidates } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type { CandidateImportOptions, CandidateImportPreview, QualificationSpec } from "@/lib/api/change-cases";

const FIELD: React.CSSProperties = {
  padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)",
  fontSize: "var(--text-xs)", width: "100%",
};
const SMALL: React.CSSProperties = { fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" };

interface Props {
  changeCaseId: number;
  spec: QualificationSpec;
  /** Called after a successful import: the page refreshes its candidate list and shows the message. */
  onImported: (message: string) => void;
}

/**
 * Import candidates from a CSV: choose file -> map columns -> state/confirm units -> check (nothing is stored)
 * -> confirm -> import. The server reads and validates the CSV (the browser never parses it), and an import is
 * all-or-nothing: any problem means nothing is stored, and the user fixes the source CSV and checks again.
 *
 * A check result and the final confirmation are valid ONLY for the exact settings they were made with (file, name
 * column, column mapping, units, unit confirmation). Both are therefore tied to a settings key: change anything and
 * the old result and confirmation stop applying until the user checks again. Answers from older checks are ignored.
 * The first look at a file only PRE-FILLS the selectors from the server's suggestion; it is never treated as a check
 * of those settings, so import stays disabled until the user has run "Check file" on the settings actually in effect.
 */
export function CandidateImport({ changeCaseId, spec, onImported }: Props) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [nameColumn, setNameColumn] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [units, setUnits] = useState<Record<string, string>>({});
  const [confirmUnits, setConfirmUnits] = useState(false);
  const [preview, setPreview] = useState<CandidateImportPreview | null>(null);
  const [checkedKey, setCheckedKey] = useState<string | null>(null); // the settings the shown preview was computed for
  const [confirmedKey, setConfirmedKey] = useState<string | null>(null); // the settings the user confirmed
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestCheck = useRef(0); // only the newest check or file selection may update the panel
  const [fileVersion, setFileVersion] = useState(0); // changes with every file selection, so a key is tied to one file

  const features = spec.feature_columns;

  // Everything that changes what an import would do, in a stable form.
  function settingsKey(nc: string, m: Record<string, string>, u: Record<string, string>, cu: boolean) {
    return JSON.stringify([fileVersion, nc, features.map((f) => [m[f] ?? "", (u[f] ?? "").trim()]), cu]);
  }
  const currentKey = settingsKey(nameColumn, mapping, units, confirmUnits);
  const previewCurrent = preview !== null && checkedKey === currentKey;
  const confirmed = previewCurrent && confirmedKey === currentKey;

  function options(over: Partial<CandidateImportOptions> = {}): CandidateImportOptions {
    const explicitUnits = Object.fromEntries(Object.entries(units).filter(([, v]) => v.trim() !== ""));
    return {
      // Always explicit. Left out, "— choose —" would not mean unmapped: the server falls back to an exact header
      // match for an omitted feature (and to "candidate_name" for an omitted name column). Only the first look at a
      // file leaves them out, on purpose, to ask the server for its suggestion.
      name_column: nameColumn,
      column_mapping: Object.fromEntries(features.map((f) => [f, mapping[f] ?? ""])),
      units: explicitUnits,
      confirm_canonical_units: confirmUnits,
      ...over,
    };
  }

  function reset() {
    latestCheck.current += 1; // drop any answer still in flight
    setFile(null); setNameColumn(""); setMapping({}); setUnits({}); setConfirmUnits(false);
    setPreview(null); setCheckedKey(null); setConfirmedKey(null); setError(null);
  }

  // `keyAtRequest` is the settings key the request was made with. null means the first look at a new file: its answer
  // only pre-fills the selectors and is NOT recorded as a check of any settings (checkedKey stays null).
  async function check(f: File, opts: CandidateImportOptions, keyAtRequest: string | null) {
    const mine = ++latestCheck.current;
    setBusy(true); setError(null); setConfirmedKey(null);
    try {
      const p = await previewCandidateImport(changeCaseId, f, opts);
      if (mine !== latestCheck.current) return; // a newer check or file replaced this one: ignore its answer
      setPreview(p);
      if (keyAtRequest === null) {
        // Pre-fill the selectors with what the server found (exact header matches only); the user can change them.
        setNameColumn(p.name_column ?? "");
        setMapping(Object.fromEntries(features.map((c) => [c, p.mapping[c] ?? ""])));
        setCheckedKey(null);
      } else {
        setCheckedKey(keyAtRequest);
      }
    } catch (err) {
      if (mine !== latestCheck.current) return;
      setPreview(null); setCheckedKey(null);
      setError(err instanceof ApiRequestError ? err.message : "Could not read the file.");
    } finally {
      if (mine === latestCheck.current) setBusy(false);
    }
  }

  function onFile(f: File | null) {
    latestCheck.current += 1; // a different file: nothing from the previous file or its settings carries over
    setFileVersion((v) => v + 1);
    setFile(f); setPreview(null); setCheckedKey(null); setConfirmedKey(null); setError(null);
    setNameColumn(""); setMapping({}); setUnits({}); setConfirmUnits(false); setBusy(false);
    if (f) check(f, { confirm_canonical_units: false }, null);
  }

  async function doImport() {
    if (!file || !preview?.ok || !confirmed || busy) return;
    setBusy(true); setError(null);
    try {
      const r = await importCandidates(changeCaseId, file, options());
      reset(); setOpen(false);
      onImported(`Imported ${r.imported_count} candidate${r.imported_count === 1 ? "" : "s"}. ${r.ranking_notice}`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not import the candidates. Nothing was imported.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div style={{ marginBottom: 16 }}>
        <Button type="button" variant="secondary" onClick={() => setOpen(true)} disabled={features.length === 0}>
          Import candidates from CSV
        </Button>
      </div>
    );
  }

  const headers = preview?.headers ?? [];
  const canImport = !!preview?.ok && confirmed && !busy;

  return (
    <div style={{ border: "1px solid var(--color-border-strong)", borderRadius: "var(--radius-sm)", padding: 14, marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <strong style={{ fontSize: "var(--text-sm)" }}>Import candidates from CSV</strong>
        <Button type="button" variant="ghost" size="sm" onClick={() => { reset(); setOpen(false); }}>Close</Button>
      </div>
      <p style={{ ...SMALL, margin: "0 0 10px" }}>
        One row per candidate: a name column plus one column per input field ({features.join(", ")}). Other columns are
        ignored. Nothing is stored until you confirm, and if any row has a problem nothing is stored at all.
      </p>

      <input type="file" accept=".csv,text/csv" aria-label="Candidates CSV file"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)} />

      {error && <div role="alert" style={{ ...SMALL, color: "var(--color-danger, #b91c1c)", marginTop: 10 }}>{error}</div>}

      {preview && (
        <>
          {preview.blocked_reason && (
            <div role="alert" style={{ ...SMALL, color: "#78350f", marginTop: 10 }}>
              <strong>Import is not possible right now:</strong> {preview.blocked_reason}
            </div>
          )}

          <div style={{ marginTop: 14 }}>
            <strong style={{ fontSize: "var(--text-xs)" }}>1. Map the columns</strong>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 1fr) minmax(160px, 1.4fr) minmax(90px, 0.8fr) minmax(120px, 1fr)", gap: 6, marginTop: 6, alignItems: "center" }}>
              <span style={SMALL}>Field</span><span style={SMALL}>Column in your file</span>
              <span style={SMALL}>Diagnostic unit</span><span style={SMALL}>Unit of your values (optional)</span>

              <span style={SMALL}>candidate name</span>
              <select aria-label="Candidate name column" value={nameColumn} style={FIELD} onChange={(e) => { setNameColumn(e.target.value); setConfirmUnits(false); }}>
                <option value="">— choose —</option>
                {headers.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}
              </select>
              <span /><span />

              {features.map((f) => (
                <FeatureRow key={f} field={f} headers={headers} column={mapping[f] ?? ""}
                  canonical={preview.expected_units[f] ?? null} unit={units[f] ?? ""}
                  onColumn={(v) => { setMapping((m) => ({ ...m, [f]: v })); setConfirmUnits(false); }}
                  onUnit={(v) => { setUnits((u) => ({ ...u, [f]: v })); setConfirmUnits(false); }} />
              ))}
            </div>
            {preview.ignored_columns.length > 0 && (
              <p style={{ ...SMALL, margin: "8px 0 0" }}>
                Ignored (not used as inputs): {preview.ignored_columns.join(", ")}.
              </p>
            )}
          </div>

          <div style={{ marginTop: 14 }}>
            <strong style={{ fontSize: "var(--text-xs)" }}>2. Units</strong>
            <label style={{ ...SMALL, display: "flex", gap: 6, alignItems: "flex-start", marginTop: 6 }}>
              <input type="checkbox" checked={confirmUnits} onChange={(e) => setConfirmUnits(e.target.checked)} />
              <span>
                I confirm that every value without a unit entered above is already in the diagnostic&apos;s unit
                ({features.map((f) => `${f}: ${preview.expected_units[f] ?? "no unit"}`).join("; ")}). Units are never guessed.
              </span>
            </label>
          </div>

          <div style={{ marginTop: 12 }}>
            <Button type="button" variant="secondary" size="sm" loading={busy} onClick={() => file && check(file, options(), currentKey)}>
              Check file
            </Button>
          </div>

          {!previewCurrent && !busy && (
            <p role="alert" style={{ ...SMALL, color: "#78350f", marginTop: 10 }}>
              The result below was not checked against the settings now in effect. Click Check file before importing.
            </p>
          )}

          <div style={{ marginTop: 14 }} aria-live="polite">
            <strong style={{ fontSize: "var(--text-xs)" }}>3. Result of the check</strong>
            {preview.error_count > 0 ? (
              <div role="alert" style={{ marginTop: 6 }}>
                <p style={{ ...SMALL, color: "var(--color-danger, #b91c1c)", margin: "0 0 6px" }}>
                  {preview.error_count} problem{preview.error_count === 1 ? "" : "s"} found. Nothing can be imported until the
                  file is fixed. Row numbers are the CSV file&apos;s own (the header is row 1). Correct the CSV, choose it again, and re-check.
                </p>
                <ul style={{ ...SMALL, margin: 0, paddingLeft: 18, maxHeight: 180, overflow: "auto" }}>
                  {preview.errors.map((e, i) => (
                    <li key={i}>{e.row ? `Row ${e.row}: ` : ""}{e.message}</li>
                  ))}
                </ul>
                {preview.error_count > preview.errors.length && (
                  <p style={{ ...SMALL, margin: "4px 0 0" }}>…and {preview.error_count - preview.errors.length} more.</p>
                )}
              </div>
            ) : (
              <p style={{ ...SMALL, margin: "6px 0 0" }}>
                No problems found. {preview.would_import} candidate{preview.would_import === 1 ? "" : "s"} would be imported.
              </p>
            )}
            {preview.preview_rows.length > 0 && (
              <div style={{ overflowX: "auto", marginTop: 8 }}>
                <table style={{ ...SMALL, borderCollapse: "collapse" }}>
                  <thead><tr>
                    <th style={{ textAlign: "left", padding: "2px 8px" }}>Row</th>
                    <th style={{ textAlign: "left", padding: "2px 8px" }}>Name</th>
                    {features.map((f) => <th key={f} style={{ textAlign: "left", padding: "2px 8px" }}>{f}</th>)}
                    <th />
                  </tr></thead>
                  <tbody>
                    {preview.preview_rows.map((r) => (
                      <tr key={r.row}>
                        <td style={{ padding: "2px 8px" }}>{r.row}</td>
                        <td style={{ padding: "2px 8px" }}>{r.name}</td>
                        {features.map((f) => <td key={f} style={{ padding: "2px 8px" }}>{r.values[f]}</td>)}
                        <td style={{ padding: "2px 8px" }}>{r.ok ? "" : "⚠ problem"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p style={{ ...SMALL, margin: "4px 0 0" }}>
                  Showing the first {preview.preview_rows.length} of {preview.row_count} rows.
                </p>
              </div>
            )}
          </div>

          <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--color-border)" }}>
            <label style={{ ...SMALL, display: "flex", gap: 6, alignItems: "flex-start" }}>
              <input type="checkbox" checked={confirmed} disabled={!preview.ok || !previewCurrent}
                onChange={(e) => setConfirmedKey(e.target.checked ? currentKey : null)} />
              <span>
                I have reviewed the mapping, the units and the rows above and want to import {preview.would_import} candidate
                {preview.would_import === 1 ? "" : "s"} into this diagnostic. They will not be ranked automatically.
              </span>
            </label>
            <div style={{ marginTop: 8 }}>
              <Button type="button" variant="primary" loading={busy} disabled={!canImport} onClick={doImport}>
                Import {preview.would_import || ""} candidates
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function FeatureRow(props: {
  field: string; headers: string[]; column: string; canonical: string | null; unit: string;
  onColumn: (v: string) => void; onUnit: (v: string) => void;
}) {
  return (
    <>
      <span style={SMALL}>{props.field}</span>
      <select aria-label={`${props.field} column`} value={props.column} style={FIELD} onChange={(e) => props.onColumn(e.target.value)}>
        <option value="">— choose —</option>
        {props.headers.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}
      </select>
      <span style={SMALL}>{props.canonical ?? "—"}</span>
      <input aria-label={`${props.field} unit of your values`} value={props.unit} placeholder={props.canonical ?? ""}
        style={FIELD} onChange={(e) => props.onUnit(e.target.value)} />
    </>
  );
}
