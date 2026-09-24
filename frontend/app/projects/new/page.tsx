"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { StepIndicator } from "@/components/projects/StepIndicator";
import { createProject, uploadDataset } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type { Constraint, Direction } from "@/types";
import "@/styles/components/wizard.css";

const STEPS = ["Objective", "Targets", "Constraints", "Review", "Upload Data"];

export default function NewProjectPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createdProjectId, setCreatedProjectId] = useState<number | null>(null);
  const [uploadResult, setUploadResult] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [targetMetric, setTargetMetric] = useState("");
  const [direction, setDirection] = useState<Direction>("maximize");
  const [featureColumnsText, setFeatureColumnsText] = useState("");
  const [targetValue, setTargetValue] = useState("");
  const [constraints, setConstraints] = useState<Constraint[]>([]);

  const featureColumns = featureColumnsText.split(",").map((s) => s.trim()).filter(Boolean);

  function addConstraint() {
    setConstraints([...constraints, { column: "", op: "<=", value: 0 }]);
  }
  function updateConstraint(i: number, patch: Partial<Constraint>) {
    setConstraints(constraints.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function removeConstraint(i: number) {
    setConstraints(constraints.filter((_, idx) => idx !== i));
  }

  async function handleCreate() {
    setError(null);
    setCreating(true);
    try {
      const project = await createProject({
        name, objective, target_metric: targetMetric, direction,
        feature_columns: featureColumns,
        constraints: constraints.filter((c) => c.column),
        target_value: targetValue ? Number(targetValue) : undefined,
      });
      setCreatedProjectId(project.id);
      setStep(4);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not create project.");
    } finally {
      setCreating(false);
    }
  }

  async function handleUpload(file: File) {
    if (!createdProjectId) return;
    setError(null);
    try {
      const result = await uploadDataset(createdProjectId, file);
      setUploadResult(`Imported ${result.rows_ingested} rows (${result.rows_skipped} skipped).`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not upload dataset.");
    }
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: "New Project" }]} /></Topbar>
      <div className="app-shell__content wizard">
        <h1 className="wizard__title">Create New Project</h1>
        <StepIndicator steps={STEPS} currentIndex={step} />

        <Card padding="lg">
          {error && <ErrorCallout message={error} />}

          {step === 0 && (
            <div className="wizard__form">
              <label>Project name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Adhesive Optimization" />
              <label>Objective (optional)</label>
              <input value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Maximize bond strength while staying processable" />
              <label>Target metric column name</label>
              <input value={targetMetric} onChange={(e) => setTargetMetric(e.target.value)} placeholder="bond_strength_MPa" />
              <label>Direction</label>
              <select value={direction} onChange={(e) => setDirection(e.target.value as Direction)}>
                <option value="maximize">Maximize</option>
                <option value="minimize">Minimize</option>
              </select>
              <label>Feature (variable) column names, comma-separated</label>
              <input
                value={featureColumnsText}
                onChange={(e) => setFeatureColumnsText(e.target.value)}
                placeholder="resin_frac, hardener_frac, filler_frac, solvent_frac, cure_temp_C, cure_time_min"
              />
            </div>
          )}

          {step === 1 && (
            <div className="wizard__form">
              <label>Target value (optional)</label>
              <input
                type="number" value={targetValue} onChange={(e) => setTargetValue(e.target.value)}
                placeholder="20.0"
              />
              <p className="wizard__hint">
                {direction === "maximize" ? "Reach at least this value." : "Stay at or below this value."} You can
                leave this unset and add it later from Project Settings.
              </p>
            </div>
          )}

          {step === 2 && (
            <div className="wizard__form">
              {constraints.map((c, i) => (
                <div key={i} className="wizard__constraint-row">
                  <input
                    placeholder="column name" value={c.column}
                    onChange={(e) => updateConstraint(i, { column: e.target.value })}
                  />
                  <select value={c.op} onChange={(e) => updateConstraint(i, { op: e.target.value as "<=" | ">=" })}>
                    <option value="<=">{"\u2264"}</option>
                    <option value=">=">{"\u2265"}</option>
                  </select>
                  <input
                    type="number" placeholder="value" value={c.value}
                    onChange={(e) => updateConstraint(i, { value: Number(e.target.value) })}
                  />
                  <Button variant="ghost" size="sm" onClick={() => removeConstraint(i)}>Remove</Button>
                </div>
              ))}
              <Button variant="secondary" size="sm" onClick={addConstraint}>+ Add constraint</Button>
            </div>
          )}

          {step === 3 && (
            <div className="wizard__review">
              <h3>Review</h3>
              <dl>
                <dt>Name</dt><dd>{name || "—"}</dd>
                <dt>Objective</dt><dd>{objective || "—"}</dd>
                <dt>Target metric</dt><dd>{targetMetric || "—"} ({direction})</dd>
                <dt>Target value</dt><dd>{targetValue || "not set"}</dd>
                <dt>Feature columns</dt><dd>{featureColumns.join(", ") || "—"}</dd>
                <dt>Constraints</dt>
                <dd>{constraints.length ? constraints.map((c) => `${c.column} ${c.op} ${c.value}`).join("; ") : "none"}</dd>
              </dl>
            </div>
          )}

          {step === 4 && createdProjectId && (
            <div className="wizard__form">
              <p>Project created. Upload your historical experiment CSV now, or skip and add it later from the project page.</p>
              <input type="file" accept=".csv" onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])} />
              {uploadResult && <p className="wizard__hint">{uploadResult}</p>}
              <Button variant="primary" onClick={() => router.push(`/projects/${createdProjectId}`)}>
                Go to project
              </Button>
            </div>
          )}
        </Card>

        {step < 4 && (
          <div className="wizard__nav">
            <Button variant="secondary" disabled={step === 0} onClick={() => setStep(step - 1)}>Previous</Button>
            {step < 3 ? (
              <Button variant="primary" onClick={() => setStep(step + 1)}>Next</Button>
            ) : (
              <Button variant="primary" loading={creating} onClick={handleCreate}>Create Project</Button>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
