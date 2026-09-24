"use client";

import { useEffect, useState } from "react";
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
  getChangeCase, uploadQualificationDataset, addCandidate, listCandidates,
  rankChangeCase, recordOutcome, generateQualificationReport,
} from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type { ChangeCase, Candidate } from "@/lib/api/change-cases";

function probabilityTone(p: number): "success" | "warning" | "danger" {
  if (p >= 0.7) return "success";
  if (p >= 0.35) return "warning";
  return "danger";
}

export default function ChangeCaseDetailPage() {
  const params = useParams();
  const changeCaseId = Number(params.id);
  const { checking } = useRequireAuth();

  const [changeCase, setChangeCase] = useState<ChangeCase | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [ranking, setRanking] = useState(false);
  const [generatingReport, setGeneratingReport] = useState(false);

  const [candName, setCandName] = useState("");
  const [candFeatures, setCandFeatures] = useState(""); // "key=value, key=value"
  const [addingCandidate, setAddingCandidate] = useState(false);

  const [outcomeCandidateId, setOutcomeCandidateId] = useState<number | null>(null);
  const [outcomeResult, setOutcomeResult] = useState("");
  const [outcomePassed, setOutcomePassed] = useState(true);

  function load() {
    setError(null);
    getChangeCase(changeCaseId).then(setChangeCase).catch(() => setError("Could not load this change case."));
    listCandidates(changeCaseId).then(setCandidates).catch(() => setError("Could not load candidates."));
  }

  useEffect(() => {
    if (!checking && changeCaseId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, changeCaseId]);

  if (checking) return null;

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null); setInfo(null); setUploading(true);
    try {
      const result = await uploadQualificationDataset(changeCaseId, file);
      setInfo(`Ingested ${result.rows_ingested} rows${result.rows_skipped ? ` (${result.rows_skipped} skipped)` : ""}.`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleAddCandidate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setAddingCandidate(true);
    try {
      const features: Record<string, number> = {};
      for (const pair of candFeatures.split(",")) {
        const [k, v] = pair.split("=").map((s) => s.trim());
        if (k && v !== undefined) features[k] = parseFloat(v);
      }
      await addCandidate(changeCaseId, candName, features);
      setCandName(""); setCandFeatures("");
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
          <CardHeader><CardTitle>1. Historical qualification dataset</CardTitle></CardHeader>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginBottom: 10 }}>
            Upload a CSV with your qualification spec&apos;s feature columns and target metric.
          </p>
          <input type="file" accept=".csv" onChange={handleUpload} disabled={uploading} />
          {uploading && <Spinner label="Uploading…" />}
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
            <div style={{ flex: 1, minWidth: 240 }}>
              <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Features (key=value, comma-separated)</label>
              <input required value={candFeatures} onChange={(e) => setCandFeatures(e.target.value)}
                placeholder="e.g. crosslinker_ratio=0.16, cure_temp_c=178"
                style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
            </div>
            <Button type="submit" variant="secondary" loading={addingCandidate}>Add candidate</Button>
          </form>

          {!candidates && <Spinner label="Loading candidates…" />}
          {candidates && candidates.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>No candidates yet.</p>
          )}
          {candidates && candidates.map((c) => (
            <div key={c.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--color-border)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{c.candidate_name}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {Object.entries(c.properties).map(([k, v]) => `${k}=${v}`).join(", ")}
                  </div>
                </div>
                {c.latest_prediction && (
                  <div style={{ textAlign: "right" }}>
                    <Badge tone={probabilityTone(c.latest_prediction.predicted_probability)}>
                      {Math.round(c.latest_prediction.predicted_probability * 100)}% predicted
                    </Badge>
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 4 }}>
                      uncertainty (std): {c.latest_prediction.uncertainty_std.toFixed(1)}
                    </div>
                  </div>
                )}
              </div>
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

          <div style={{ marginTop: 16 }}>
            <Button variant="primary" onClick={handleRank} loading={ranking} disabled={!candidates || candidates.length === 0}>
              Run ranking
            </Button>
          </div>
        </Card>

        <Card padding="lg">
          <CardHeader><CardTitle>3. Diagnostic report</CardTitle></CardHeader>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginBottom: 10 }}>
            An analysis deliverable -- ranked candidates, predicted probability and uncertainty, and a
            recommended validation plan. This does not represent completed physical qualification.
          </p>
          <Button variant="secondary" onClick={handleDownloadReport} loading={generatingReport}>
            Download diagnostic report (.docx)
          </Button>
        </Card>
      </div>
    </AppShell>
  );
}
