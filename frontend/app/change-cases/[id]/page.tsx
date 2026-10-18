"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import {
  getChangeCase, addCandidate, listCandidates, listEvidence, getSufficiency, parseSpec,
  rankChangeCase, recordOutcome, generateQualificationReport,
  updateCandidate, archiveCandidate, restoreCandidate, deleteCandidate,
} from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type {
  ChangeCase, Candidate, CandidateUpdate, DecisionSupport, DomainStatus, EvidenceInventoryItem,
  QualificationSpec, Sufficiency,
} from "@/lib/api/change-cases";

// The model-estimated probability alone must never produce an approval-style
// (green) treatment: a candidate far outside the historical range can still
// receive ~100%. Tone is driven by decision support, and none of the tones
// used here is the "success" (green) tone.
function decisionTone(ds: DecisionSupport | null): "neutral" | "accent" | "warning" | "danger" {
  if (!ds) return "warning"; // prediction exists but no decision-support signal: treat as unverified
  if (ds.status === "requires_validation") return "danger";
  if (ds.status === "caution") return "warning";
  return "accent"; // evidence_supported: informational only, still requires physical validation
}

const DOMAIN_STATUS_LABEL: Record<DomainStatus, string> = {
  within_historical_domain: "Within observed range",
  near_edge_of_domain: "Near edge of observed range",
  outside_historical_domain: "Outside observed range",
};

// Four significant digits, same as the report, so the screen and the DOCX show the same number.
function formatPredictedValue(v: number): string {
  return String(Number(v.toPrecision(4)));
}

const MODEL_ESTIMATE_NOTE =
  "Model-estimated probability is the model's output given the supplied historical data. " +
  "It is not a validated qualification probability.";

type CandidateDialog = { kind: "edit" | "delete" | "remove"; c: Candidate } | null;

const FIELD_STYLE: React.CSSProperties = {
  display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4,
};
const LABEL_STYLE: React.CSSProperties = { fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" };
const NOTICE_STYLE: React.CSSProperties = {
  margin: "8px 0", padding: "10px 12px", border: "1px solid #f59e0b", background: "#fffbeb",
  borderRadius: "var(--radius-sm)", fontSize: "var(--text-xs)", color: "#78350f",
};

export default function ChangeCaseDetailPage() {
  const params = useParams();
  const changeCaseId = Number(params.id);
  const { checking } = useRequireAuth();

  const [changeCase, setChangeCase] = useState<ChangeCase | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [spec, setSpec] = useState<QualificationSpec | null>(null);
  const [evidence, setEvidence] = useState<EvidenceInventoryItem[] | null>(null);
  const [sufficiency, setSufficiency] = useState<Sufficiency | null>(null);

  const [ranking, setRanking] = useState(false);
  const [generatingReport, setGeneratingReport] = useState(false);

  const [candName, setCandName] = useState("");
  const [candValues, setCandValues] = useState<Record<string, string>>({});
  const [candUnits, setCandUnits] = useState<Record<string, string>>({});
  const [addingCandidate, setAddingCandidate] = useState(false);

  const [showRemoved, setShowRemoved] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialog, setDialog] = useState<CandidateDialog>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busyCandidateId, setBusyCandidateId] = useState<number | null>(null);
  // Edit form (filled from the saved candidate each time the edit dialog opens)
  const [editName, setEditName] = useState("");
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [editUnits, setEditUnits] = useState<Record<string, string>>({});
  const [editInitial, setEditInitial] = useState<{ values: Record<string, string>; units: Record<string, string> } | null>(null);

  const [outcomeCandidateId, setOutcomeCandidateId] = useState<number | null>(null);
  const [outcomeResult, setOutcomeResult] = useState("");
  const [outcomePassed, setOutcomePassed] = useState(true);

  function load() {
    setError(null);
    getChangeCase(changeCaseId)
      .then((c) => { setChangeCase(c); setSpec(parseSpec(c)); })
      .catch(() => setError("Could not load this change case."));
    listCandidates(changeCaseId, showRemoved).then(setCandidates).catch(() => setError("Could not load candidates."));
    listEvidence(changeCaseId).then(setEvidence).catch(() => setError("Could not load the evidence inventory."));
    getSufficiency(changeCaseId).then(setSufficiency).catch(() => setSufficiency(null));
  }

  useEffect(() => {
    if (!checking && changeCaseId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, changeCaseId, showRemoved]);

  if (checking) return null;

  // One banner per distinct stale notice (normally exactly one), shown above the candidate list.
  // Ranked candidates first, in the server's deterministic rank order; candidates without a prediction
  // follow in the order they were added. (The server list is in candidate-id order.)
  const orderedCandidates = [...(candidates ?? [])].sort((a, b) => {
    if (Boolean(a.archived) !== Boolean(b.archived)) return a.archived ? 1 : -1; // removed ones last
    const ra = a.rank ?? Number.POSITIVE_INFINITY;
    const rb = b.rank ?? Number.POSITIVE_INFINITY;
    return ra !== rb ? ra - rb : a.id - b.id;
  });
  const activeCandidates = (candidates ?? []).filter((c) => !c.archived);
  // An archived diagnostic is read-only and a completed one is a closed record: neither takes candidate changes.
  const archivedCase = Boolean(changeCase?.archived_at);
  const completedCase = changeCase?.status === "completed";
  const canEditCandidates = changeCase !== null && !archivedCase && !completedCase;
  // The order is out of date (candidates were added/removed/restored/edited since the ranking ran): one banner.
  const orderNotice = activeCandidates.find((c) => c.ranking_outdated)?.ranking_notice ?? null;
  const needsRerank = activeCandidates.some((c) => c.prediction_stale || c.prediction_inputs_stale || c.ranking_outdated);
  const targetUnit = spec?.units?.[spec.target_metric];
  const staleNotices = Array.from(
    new Set((candidates ?? []).map((c) => c.stale_notice).filter((n): n is string => !!n)),
  );

  function openDialog(kind: "edit" | "delete" | "remove", c: Candidate) {
    setDialogError(null);
    if (kind === "edit") {
      // Fill the form with what was ENTERED (value + unit) when there is a record of it; otherwise with the
      // stored value, which is already in the diagnostic's own unit.
      const values: Record<string, string> = {};
      const units: Record<string, string> = {};
      for (const col of spec?.feature_columns ?? []) {
        const entered = c.input_record?.[col];
        if (entered) {
          values[col] = String(entered.value);
          units[col] = entered.unit ?? spec?.units?.[col] ?? "";
        } else {
          const stored = c.properties[col];
          values[col] = stored === undefined ? "" : String(stored);
          units[col] = spec?.units?.[col] ?? "";
        }
      }
      setEditName(c.candidate_name);
      setEditValues(values);
      setEditUnits(units);
      setEditInitial({ values, units });
    }
    setDialog({ kind, c });
  }

  function closeDialog() {
    if (busyCandidateId !== null) return;
    setDialog(null);
    setDialogError(null);
  }

  /** Runs one action on one candidate. The candidate's buttons and the dialog stay disabled while it runs, so a
   *  double click can't submit twice; a failure is shown in the dialog with the user's input kept. */
  async function run(c: Candidate, action: () => Promise<unknown>, success: string) {
    if (busyCandidateId !== null) return;
    setBusyCandidateId(c.id);
    setDialogError(null);
    try {
      await action();
      setDialog(null);
      setNotice(success);
      setError(null);
      load();
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "That didn't work. Please try again.";
      if (dialog) setDialogError(message); else setError(message);
    } finally {
      setBusyCandidateId(null);
    }
  }

  function saveEdit(c: Candidate) {
    const name = editName.trim();
    if (!name) { setDialogError("A candidate needs a name."); return; }
    const changes: CandidateUpdate = {};
    if (name !== c.candidate_name) changes.name = name;
    if (c.features_editable !== false && spec && editInitial) {
      const features: Record<string, number> = {};
      const units: Record<string, string> = {};
      let valuesChanged = false;
      for (const col of spec.feature_columns) {
        const raw = (editValues[col] ?? "").trim();
        const n = Number(raw);
        if (raw === "" || !Number.isFinite(n)) { setDialogError(`Enter a number for ${col}.`); return; }
        features[col] = n;
        const unit = (editUnits[col] ?? "").trim();
        if (unit) units[col] = unit;
        if (raw !== (editInitial.values[col] ?? "").trim() || unit !== (editInitial.units[col] ?? "").trim()) valuesChanged = true;
      }
      // The whole set of values goes together with its units, or nothing about the values is sent.
      if (valuesChanged) {
        changes.features = features;
        if (Object.keys(units).length > 0) changes.units = units;
      }
    }
    if (Object.keys(changes).length === 0) { setDialogError("Nothing has changed."); return; }
    run(c, () => updateCandidate(changeCaseId, c.id, changes),
      changes.features ? `Saved changes to “${name}”. Re-run ranking to get a prediction for the new values.` : `Saved changes to “${name}”.`);
  }

  async function handleAddCandidate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setAddingCandidate(true);
    try {
      if (!spec) {
        setError("The diagnostic definition has not loaded yet. Reload the page and try again.");
        return;
      }
      const features: Record<string, number> = {};
      const units: Record<string, string> = {};
      for (const col of spec.feature_columns) {
        const raw = (candValues[col] ?? "").trim();
        const n = Number(raw);
        if (raw === "" || !Number.isFinite(n)) {
          setError(`Enter a number for ${col}.`);
          return;
        }
        features[col] = n;
        const u = (candUnits[col] ?? "").trim();
        if (u) units[col] = u;
      }
      await addCandidate(changeCaseId, candName, features, units);
      setCandName(""); setCandValues({}); setCandUnits({});
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not add candidate.");
    } finally {
      setAddingCandidate(false);
    }
  }

  async function handleRank() {
    setError(null); setInfo(null); setRanking(true);
    try {
      await rankChangeCase(changeCaseId);
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not run ranking.");
    } finally {
      setRanking(false);
    }
  }

  async function handleRecordOutcome(e: React.FormEvent) {
    e.preventDefault();
    if (outcomeCandidateId === null) return;
    setError(null);
    try {
      await recordOutcome(changeCaseId, outcomeCandidateId, outcomeResult, outcomePassed);
      setOutcomeCandidateId(null); setOutcomeResult("");
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not record outcome.");
    }
  }

  async function handleDownloadReport() {
    setError(null); setGeneratingReport(true);
    try {
      const blob = await generateQualificationReport(changeCaseId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `qualification_diagnostic_${changeCaseId}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not generate report.");
    } finally {
      setGeneratingReport(false);
    }
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Change Cases", href: "/change-cases" }, { label: changeCase?.name ?? "…" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title={changeCase?.name ?? "Loading…"}
          subtitle={changeCase ? `${changeCase.trigger_type}${changeCase.restricted_substance ? ` · ${changeCase.restricted_substance}` : ""}` : ""}
        />

        {error && <ErrorCallout message={error} />}
        {info && (
          <div style={{ padding: 12, background: "var(--color-accent-subtle)", borderRadius: "var(--radius-sm)", fontSize: "var(--text-sm)", marginBottom: 16 }}>
            {info}
          </div>
        )}

        <Card padding="lg" style={{ marginBottom: 20 }}>
          <CardHeader><CardTitle>1. Customer evidence</CardTitle></CardHeader>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginBottom: 10 }}>
            Import the customer&apos;s historical qualification evidence (CSV, XLSX, Word or PDF tables). It is reviewed — table,
            columns, units, test conditions, conflicts — and only evidence you accept reaches the model.
          </p>
          {spec && (
            <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)", marginBottom: 10 }}>
              Requirement analysed: <strong>{spec.target_metric}{spec.units?.[spec.target_metric] ? ` (${spec.units[spec.target_metric]})` : ""}</strong>{" "}
              {spec.direction === "maximize" ? "at or above" : "at or below"} <strong>{spec.target_value}</strong>. Input features:{" "}
              {spec.feature_columns.map((f) => `${f}${spec.units?.[f] ? ` (${spec.units[f]})` : ""}`).join(", ")}.
            </p>
          )}
          {!evidence && <Spinner label="Loading evidence…" />}
          {evidence && evidence.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>No evidence has been accepted yet.</p>
          )}
          {evidence && evidence.map((ev) => (
            <div key={ev.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--color-border)", fontSize: "var(--text-xs)" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <strong style={{ fontSize: "var(--text-sm)" }}>{ev.original_filename}</strong>
                {ev.status === "current"
                  ? <Badge tone="accent">Current evidence</Badge>
                  : <Badge tone="neutral">Previous / superseded evidence</Badge>}
                {ev.status !== "current" && ev.used_by_displayed_rankings && (
                  <Badge tone="warning">rankings below still use this</Badge>
                )}
                {!ev.has_review_record && <Badge tone="warning">no review record</Badge>}
              </div>
              <div style={{ color: "var(--color-text-tertiary)" }}>
                {ev.row_count} records
                {ev.source?.tables.map((t) => ` · ${t.location} (header row ${t.header_row})`).join("")}
                {ev.warning_count > 0 && ` · ${ev.warning_count} note(s) recorded`}
                {ev.file_sha256 && ` · SHA-256 ${ev.file_sha256.slice(0, 12)}…`}
              </div>
            </div>
          ))}
          <div style={{ marginTop: 12 }}>
            <Link href={`/change-cases/${changeCaseId}/intake`}>
              <Button variant="primary">Import and review evidence</Button>
            </Link>
          </div>
        </Card>

        <Card padding="lg" style={{ marginBottom: 20 }}>
          <CardHeader><CardTitle>2. Candidate substitutes ({activeCandidates.length})</CardTitle></CardHeader>
          {archivedCase && (
            <div role="status" style={NOTICE_STYLE}>
              <strong>This diagnostic is archived and read-only.</strong> Restore it from the{" "}
              <Link href="/change-cases">Diagnostics list</Link> to add, edit or remove candidates or to run a ranking.
              The report can still be downloaded.
            </div>
          )}
          {completedCase && !archivedCase && (
            <div role="status" style={NOTICE_STYLE}>
              <strong>This diagnostic is completed (a closed record).</strong> Candidates can no longer be added, edited,
              removed or restored.
            </div>
          )}
          {notice && (
            <div role="status" style={{ marginBottom: 12, padding: "8px 12px", borderRadius: "var(--radius-sm)", fontSize: "var(--text-sm)",
              background: "var(--color-success-subtle)", border: "1px solid var(--color-success-border)", color: "var(--color-success)",
              display: "flex", justifyContent: "space-between", gap: 12 }}>
              <span>{notice}</span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss"
                style={{ background: "none", border: "none", cursor: "pointer", color: "inherit" }}>×</button>
            </div>
          )}
          {canEditCandidates && (
          <form onSubmit={handleAddCandidate} style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Candidate name</label>
              <input required value={candName} onChange={(e) => setCandName(e.target.value)}
                placeholder="e.g. EcoShield SF-100"
                style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
            </div>
            {(spec?.feature_columns ?? []).map((col) => (
              <div key={col} style={{ minWidth: 150 }}>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>{col}</label>
                <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                  <input required type="number" step="any" value={candValues[col] ?? ""}
                    onChange={(e) => setCandValues((v) => ({ ...v, [col]: e.target.value }))}
                    style={{ width: 90, padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)" }} />
                  {spec?.units?.[col] && (
                    <input required value={candUnits[col] ?? ""} aria-label={`${col} unit`}
                      onChange={(e) => setCandUnits((u) => ({ ...u, [col]: e.target.value }))}
                      placeholder={spec.units[col]}
                      style={{ width: 70, padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)" }} />
                  )}
                </div>
              </div>
            ))}
            <Button type="submit" variant="secondary" loading={addingCandidate} disabled={!spec}>
              Add candidate
            </Button>
          </form>
          )}
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)", marginBottom: 8 }}>
            <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
            Show candidates removed from the analysis
          </label>

          {!candidates && <Spinner label="Loading candidates…" />}
          {candidates && candidates.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>No candidates yet.</p>
          )}
          {staleNotices.map((n) => (
            <div key={n} role="alert" style={NOTICE_STYLE}>
              <strong>Stale ranking.</strong> {n}
            </div>
          ))}
          {orderNotice && (
            <div role="alert" style={NOTICE_STYLE}>
              <strong>Ranking out of date.</strong> {orderNotice}
            </div>
          )}
          {candidates && orderedCandidates.map((c) => {
            const removed = Boolean(c.archived);
            const staleInputs = c.prediction_inputs_stale === true;
            const busy = busyCandidateId === c.id;
            return (
            <div key={c.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--color-border)", opacity: removed ? 0.75 : 1 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
                <div style={{ minWidth: 220, flex: "1 1 260px" }}>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    {c.rank != null && <span style={{ color: "var(--color-text-tertiary)" }}>#{c.rank}</span>}
                    <span>{c.candidate_name}</span>
                    {removed && <Badge tone="neutral">Removed from analysis</Badge>}
                  </div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {Object.entries(c.properties).map(([k, v]) => `${k}=${v}${spec?.units?.[k] ? ` ${spec.units[k]}` : ""}`).join(", ")}
                  </div>
                  {c.ignored_properties && Object.keys(c.ignored_properties).length > 0 && (
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                      Stored but not used by the model: {Object.entries(c.ignored_properties).map(([k, v]) => `${k}=${v}`).join(", ")}
                    </div>
                  )}
                  {c.input_record && Object.entries(c.input_record).some(([, e]) => e.converted_unit && e.unit !== e.converted_unit) && (
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                      Entered as: {Object.entries(c.input_record)
                        .filter(([, e]) => e.converted_unit && e.unit !== e.converted_unit)
                        .map(([k, e]) => `${k} ${e.value} ${e.unit} → ${e.converted_value} ${e.converted_unit}`).join("; ")}
                    </div>
                  )}
                  {(c.outcome_count ?? 0) > 0 && (
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                      {c.outcome_count} physical outcome{c.outcome_count === 1 ? "" : "s"} recorded
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
                  {c.latest_prediction && (
                    <div style={{ textAlign: "right", opacity: c.prediction_stale || staleInputs ? 0.7 : 1 }}>
                      {staleInputs && <div style={{ marginBottom: 4 }}><Badge tone="danger">PREVIOUS INPUTS</Badge></div>}
                      {c.prediction_stale && <div style={{ marginBottom: 4 }}><Badge tone="danger">STALE</Badge></div>}
                      <Badge tone={staleInputs ? "neutral" : decisionTone(c.decision_support)}>
                        Model estimate {Math.round(c.latest_prediction.predicted_probability * 100)}%
                      </Badge>
                      <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 4 }}>
                        uncertainty (std): {c.latest_prediction.uncertainty_std.toFixed(1)}{targetUnit ? ` ${targetUnit}` : ""}
                      </div>
                    </div>
                  )}
                  {canEditCandidates && (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }} role="group" aria-label={`Actions for ${c.candidate_name}`}>
                      {removed ? (
                        <Button size="sm" variant="secondary" disabled={busyCandidateId !== null}
                          onClick={() => run(c, () => restoreCandidate(changeCaseId, c.id), `Restored “${c.candidate_name}”. Re-run ranking to include it in the order.`)}>
                          Restore
                        </Button>
                      ) : (
                        <>
                          <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("edit", c)}>Edit</Button>
                          <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("remove", c)}>Remove from analysis</Button>
                          <Button size="sm" variant="danger" disabled={busy} onClick={() => openDialog("delete", c)}>Delete</Button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
              {staleInputs && c.ranking_notice && (
                <div role="alert" style={NOTICE_STYLE}>
                  <strong>Prediction is for previous input values.</strong> {c.ranking_notice}
                </div>
              )}
              {c.latest_prediction && (
                <div style={{ marginTop: 8, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
                  <div>
                    <strong>
                      {staleInputs ? "Previous prediction (before the input values were edited) — " : ""}
                      Predicted {spec?.target_metric ?? "value"}{targetUnit ? ` (${targetUnit})` : ""}:{" "}
                    </strong>
                    {c.latest_prediction.predicted_value != null
                      ? <>{formatPredictedValue(c.latest_prediction.predicted_value)}{targetUnit ? ` ${targetUnit}` : ""}{" "}
                          <span style={{ color: "var(--color-text-tertiary)" }}>(model estimate, not a measured result)</span></>
                      : <span style={{ color: "var(--color-text-tertiary)" }}>
                          Not recorded — this ranking was saved before predicted values were stored. Re-run the ranking to get one.
                        </span>}
                    {spec && (
                      <div style={{ color: "var(--color-text-tertiary)" }}>
                        Requirement: {spec.target_metric}{targetUnit ? ` (${targetUnit})` : ""}{" "}
                        {spec.direction === "maximize" ? "at or above" : "at or below"} {spec.target_value}
                        {" "}· direction: {spec.direction}
                      </div>
                    )}
                  </div>
                  {!staleInputs && (
                    <>
                      <div style={{ marginTop: 4 }}>
                        Historical domain{c.prediction_stale ? " (previous evidence)" : ""}:{" "}
                        {c.domain_coverage ? DOMAIN_STATUS_LABEL[c.domain_coverage.status] : "Not determined"}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
                        <span>Decision support{c.prediction_stale ? " (previous evidence)" : ""}:</span>
                        <Badge tone={decisionTone(c.decision_support)}>
                          {c.decision_support?.label ?? "Requires validation"}
                        </Badge>
                      </div>
                      {c.decision_support && (
                        <div style={{ marginTop: 4, color: "var(--color-text-tertiary)" }}>{c.decision_support.statement}</div>
                      )}
                    </>
                  )}
                </div>
              )}
              {c.latest_prediction && !removed && !archivedCase && (
                <div style={{ marginTop: 8 }}>
                  {outcomeCandidateId === c.id ? (
                    <form onSubmit={handleRecordOutcome} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <input required value={outcomeResult} onChange={(e) => setOutcomeResult(e.target.value)}
                        placeholder="e.g. Passed salt-spray at 540 hours"
                        style={{ flex: 1, minWidth: 220, padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-xs)" }} />
                      <select value={outcomePassed ? "pass" : "fail"} onChange={(e) => setOutcomePassed(e.target.value === "pass")}
                        style={{ padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-xs)" }}>
                        <option value="pass">Passed spec</option>
                        <option value="fail">Failed spec</option>
                      </select>
                      <Button type="submit" size="sm" variant="primary">Save outcome</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setOutcomeCandidateId(null)}>Cancel</Button>
                    </form>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => setOutcomeCandidateId(c.id)}>
                      Record physical validation outcome
                    </Button>
                  )}
                </div>
              )}
            </div>
            );
          })}

          {candidates && candidates.some((c) => c.latest_prediction) && (
            <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 12 }}>
              {MODEL_ESTIMATE_NOTE}
            </p>
          )}

          {sufficiency && (
            <div style={{ marginTop: 16, padding: 12, border: "1px solid var(--color-border)", borderRadius: "var(--radius-sm)" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                <strong style={{ fontSize: "var(--text-sm)" }}>Evidence sufficiency</strong>
                <Badge tone={sufficiency.can_rank ? "neutral" : "danger"}>
                  {sufficiency.can_rank ? "Evidence can support an analysis" : "Analysis stopped — evidence insufficient"}
                </Badge>
              </div>
              {sufficiency.checks.filter((c) => c.status !== "pass").map((c) => (
                <div key={c.code} style={{ fontSize: "var(--text-xs)", padding: "3px 0" }}>
                  <Badge tone={c.status === "fail" ? "danger" : "warning"}>{c.status === "fail" ? "Missing" : "Note"}</Badge>{" "}
                  <strong>{c.title}.</strong> {c.detail}
                </div>
              ))}
              {sufficiency.checks.every((c) => c.status === "pass") && (
                <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                  All {sufficiency.checks.length} checks passed ({sufficiency.summary.distinct_rows} distinct records, {sufficiency.summary.features} input features).
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: 16 }}>
            <Button variant="primary" onClick={handleRank} loading={ranking}
              disabled={archivedCase || activeCandidates.length === 0 || (sufficiency !== null && !sufficiency.can_rank)}>
              {needsRerank ? "Re-run ranking" : "Run ranking"}
            </Button>
          </div>
        </Card>

        <Card padding="lg">
          <CardHeader><CardTitle>3. Diagnostic report</CardTitle></CardHeader>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginBottom: 10 }}>
            An analysis deliverable -- ranked candidates, model-estimated probability and uncertainty,
            decision support, and a recommended validation plan. This does not represent completed physical qualification.
          </p>
          <Button variant="secondary" onClick={handleDownloadReport} loading={generatingReport}>
            Download diagnostic report (.docx)
          </Button>
        </Card>

        {dialog?.kind === "edit" && (() => {
          const c = dialog.c;
          const locked = c.features_editable === false;
          return (
            <Dialog title="Edit candidate" width={560} onClose={closeDialog} busy={busyCandidateId !== null}
              actions={<>
                <Button variant="secondary" onClick={closeDialog} disabled={busyCandidateId !== null}>Cancel</Button>
                <Button variant="primary" loading={busyCandidateId === c.id} onClick={() => saveEdit(c)}>Save changes</Button>
              </>}>
              {dialogError && <ErrorCallout message={dialogError} />}
              <div>
                <label style={LABEL_STYLE} htmlFor="cand-edit-name">Candidate name</label>
                <input id="cand-edit-name" value={editName} onChange={(e) => setEditName(e.target.value)} style={FIELD_STYLE} />
              </div>
              {(spec?.feature_columns ?? []).map((col) => (
                <div key={col}>
                  <label style={LABEL_STYLE} htmlFor={`cand-edit-${col}`}>{col}</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input id={`cand-edit-${col}`} type="number" step="any" disabled={locked} value={editValues[col] ?? ""}
                      onChange={(e) => setEditValues((v) => ({ ...v, [col]: e.target.value }))} style={FIELD_STYLE} />
                    {spec?.units?.[col] && (
                      <input aria-label={`${col} unit`} disabled={locked} value={editUnits[col] ?? ""} placeholder={spec.units[col]}
                        onChange={(e) => setEditUnits((u) => ({ ...u, [col]: e.target.value }))}
                        style={{ ...FIELD_STYLE, width: 110, flex: "none" }} />
                    )}
                  </div>
                </div>
              ))}
              {locked && (
                <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: 0 }}>
                  The input values are locked. {c.features_locked_reason} The name can still be changed.
                </p>
              )}
              {!locked && (c.prediction_count ?? 0) > 0 && (
                <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: 0 }}>
                  This candidate has already been ranked. If you change its input values, the earlier prediction is kept as
                  history but is marked as computed for the previous values, and the order is withheld until you re-run the ranking.
                </p>
              )}
            </Dialog>
          );
        })()}

        {dialog?.kind === "remove" && (
          <Dialog title="Remove this candidate from the analysis?" onClose={closeDialog} busy={busyCandidateId !== null}
            actions={<>
              <Button variant="secondary" onClick={closeDialog} disabled={busyCandidateId !== null}>Cancel</Button>
              <Button variant="primary" loading={busyCandidateId === dialog.c.id}
                onClick={() => run(dialog.c, () => archiveCandidate(changeCaseId, dialog.c.id), `Removed “${dialog.c.candidate_name}” from the analysis. Re-run ranking to update the order.`)}>
                Remove from analysis
              </Button>
            </>}>
            {dialogError && <ErrorCallout message={dialogError} />}
            <p style={{ margin: 0 }}>
              <strong>{dialog.c.candidate_name}</strong> will no longer be ranked or shown in the list. Nothing is deleted: its
              predictions, recommended experiments and any recorded physical outcomes are kept, and you can restore it with
              &ldquo;Show candidates removed from the analysis&rdquo;. The order of the other candidates is withheld until you re-run the ranking.
            </p>
          </Dialog>
        )}

        {dialog?.kind === "delete" && (
          <Dialog title="Delete this candidate?" onClose={closeDialog} busy={busyCandidateId !== null}
            actions={dialog.c.deletable !== false ? (
              <>
                <Button variant="secondary" onClick={closeDialog} disabled={busyCandidateId !== null}>Cancel</Button>
                <Button variant="danger" loading={busyCandidateId === dialog.c.id}
                  onClick={() => run(dialog.c, () => deleteCandidate(changeCaseId, dialog.c.id), `Deleted “${dialog.c.candidate_name}”.`)}>
                  Delete permanently
                </Button>
              </>
            ) : (
              <>
                <Button variant="secondary" onClick={closeDialog} disabled={busyCandidateId !== null}>Close</Button>
                <Button variant="primary" onClick={() => openDialog("remove", dialog.c)}>Remove from analysis instead</Button>
              </>
            )}>
            {dialogError && <ErrorCallout message={dialogError} />}
            {dialog.c.deletable !== false ? (
              <p style={{ margin: 0 }}>
                This permanently deletes <strong>{dialog.c.candidate_name}</strong>. It has not been ranked and has no recorded
                physical outcome. <strong>This cannot be undone.</strong>
              </p>
            ) : (
              <>
                <p style={{ margin: 0 }}><strong>{dialog.c.candidate_name}</strong> can&apos;t be deleted.</p>
                <p style={{ margin: 0 }}>{dialog.c.delete_blocked_reason}</p>
              </>
            )}
          </Dialog>
        )}
      </div>
    </AppShell>
  );
}