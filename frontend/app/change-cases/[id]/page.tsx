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
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import {
  getChangeCase, addCandidate, listCandidates, listEvidence, getSufficiency, parseSpec,
  rankChangeCase, recordOutcome, generateQualificationReport,
} from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type {
  ChangeCase, Candidate, DecisionSupport, DomainStatus, EvidenceInventoryItem, QualificationSpec, Sufficiency,
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

const MODEL_ESTIMATE_NOTE =
  "Model-estimated probability is the model's output given the supplied historical data. " +
  "It is not a validated qualification probability.";

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

  const [outcomeCandidateId, setOutcomeCandidateId] = useState<number | null>(null);
  const [outcomeResult, setOutcomeResult] = useState("");
  const [outcomePassed, setOutcomePassed] = useState(true);

  function load() {
    setError(null);
    getChangeCase(changeCaseId)
      .then((c) => { setChangeCase(c); setSpec(parseSpec(c)); })
      .catch(() => setError("Could not load this change case."));
    listCandidates(changeCaseId).then(setCandidates).catch(() => setError("Could not load candidates."));
    listEvidence(changeCaseId).then(setEvidence).catch(() => setError("Could not load the evidence inventory."));
    getSufficiency(changeCaseId).then(setSufficiency).catch(() => setSufficiency(null));
  }

  useEffect(() => {
    if (!checking && changeCaseId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, changeCaseId]);

  if (checking) return null;

  // One banner per distinct stale notice (normally exactly one), shown above the candidate list.
  const staleNotices = Array.from(
    new Set((candidates ?? []).map((c) => c.stale_notice).filter((n): n is string => !!n)),
  );

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
          <CardHeader><CardTitle>2. Candidate substitutes ({candidates?.length ?? 0}/5)</CardTitle></CardHeader>
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
            <Button type="submit" variant="secondary" loading={addingCandidate} disabled={!spec || (candidates?.length ?? 0) >= 5}>
              Add candidate
            </Button>
          </form>

          {!candidates && <Spinner label="Loading candidates…" />}
          {candidates && candidates.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>No candidates yet.</p>
          )}
          {staleNotices.map((n) => (
            <div key={n} role="alert" style={{
              margin: "8px 0", padding: "10px 12px", border: "1px solid #f59e0b", background: "#fffbeb",
              borderRadius: "var(--radius-sm)", fontSize: "var(--text-xs)", color: "#78350f",
            }}>
              <strong>Stale ranking.</strong> {n}
            </div>
          ))}
          {candidates && candidates.map((c) => (
            <div key={c.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--color-border)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{c.candidate_name}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {Object.entries(c.properties).map(([k, v]) => `${k}=${v}${spec?.units?.[k] ? ` ${spec.units[k]}` : ""}`).join(", ")}
                  </div>
                  {c.input_record && Object.entries(c.input_record).some(([, e]) => e.converted_unit && e.unit !== e.converted_unit) && (
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                      Entered as: {Object.entries(c.input_record)
                        .filter(([, e]) => e.converted_unit && e.unit !== e.converted_unit)
                        .map(([k, e]) => `${k} ${e.value} ${e.unit} → ${e.converted_value} ${e.converted_unit}`).join("; ")}
                    </div>
                  )}
                </div>
                {c.latest_prediction && (
                  <div style={{ textAlign: "right", opacity: c.prediction_stale ? 0.7 : 1 }}>
                    {c.prediction_stale && <div style={{ marginBottom: 4 }}><Badge tone="danger">STALE</Badge></div>}
                    <Badge tone={decisionTone(c.decision_support)}>
                      Model estimate {Math.round(c.latest_prediction.predicted_probability * 100)}%
                    </Badge>
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 4 }}>
                      uncertainty (std): {c.latest_prediction.uncertainty_std.toFixed(1)}
                    </div>
                  </div>
                )}
              </div>
              {c.latest_prediction && (
                <div style={{ marginTop: 8, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
                  <div>
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
                </div>
              )}
              {c.latest_prediction && (
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
          ))}

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
              disabled={!candidates || candidates.length === 0 || (sufficiency !== null && !sufficiency.can_rank)}>
              {(candidates ?? []).some((c) => c.prediction_stale) ? "Re-run ranking" : "Run ranking"}
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
      </div>
    </AppShell>
  );
}
