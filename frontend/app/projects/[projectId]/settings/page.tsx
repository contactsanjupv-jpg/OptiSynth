"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { useParams } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { getProject, setProjectStatus, uploadDataset, updateProject } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import type { Project, ProjectStatus, Direction, Constraint } from "@/types";

export default function ProjectSettingsPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [targetMetric, setTargetMetric] = useState("");
  const [direction, setDirection] = useState<Direction>("maximize");
  const [targetValue, setTargetValue] = useState("");
  const [featureColumnsText, setFeatureColumnsText] = useState("");
  const [constraints, setConstraints] = useState<Constraint[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    if (projectId) getProject(projectId).then(setProject);
  }, [projectId]);

  function startEditing() {
    if (!project) return;
    setName(project.name);
    setObjective(project.objective ?? "");
    setTargetMetric(project.target_metric);
    setDirection(project.direction);
    setTargetValue(project.target_value !== null ? String(project.target_value) : "");
    setFeatureColumnsText(project.feature_columns.join(", "));
    setConstraints(project.constraints);
    setSaveMsg(null);
    setEditing(true);
  }

  function addConstraint() {
    setConstraints([...constraints, { column: "", op: "<=", value: 0 }]);
  }
  function updateConstraintRow(i: number, patch: Partial<Constraint>) {
    setConstraints(constraints.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function removeConstraint(i: number) {
    setConstraints(constraints.filter((_, idx) => idx !== i));
  }

  async function handleSave() {
    if (!project) return;
    setError(null);
    setSaving(true);
    try {
      const hasExperiments = (project.experiment_count ?? 0) > 0;
      const featureColumns = featureColumnsText.split(",").map((s) => s.trim()).filter(Boolean);

      const updated = await updateProject(projectId, {
        name,
        objective,
        target_metric: targetMetric,
        direction,
        target_value: targetValue ? Number(targetValue) : undefined,
        ...(hasExperiments ? {} : { feature_columns: featureColumns, constraints }),
      });
      setProject(updated);
      setEditing(false);
      setSaveMsg("Saved.");
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  async function handleStatusChange(status: ProjectStatus) {
    setError(null);
    try {
      const updated = await setProjectStatus(projectId, status);
      setProject(updated);
    } catch {
      setError("Could not update status.");
    }
  }

  async function handleUpload(file: File) {
    setError(null);
    try {
      const result = await uploadDataset(projectId, file);
      setUploadMsg(`Imported ${result.rows_ingested} rows (${result.rows_skipped} skipped).`);
      getProject(projectId).then(setProject);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not upload dataset.");
    }
  }

  if (!project) return null;

  const hasExperiments = (project.experiment_count ?? 0) > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {error && <ErrorCallout message={error} />}

      <Card padding="lg">
        <CardHeader><CardTitle>Project Status</CardTitle></CardHeader>
        <div style={{ display: "flex", gap: 8 }}>
          {(["draft", "running", "completed"] as ProjectStatus[]).map((s) => (
            <Button key={s} variant={project.status === s ? "primary" : "secondary"} size="sm"
              onClick={() => handleStatusChange(s)}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </Button>
          ))}
        </div>
      </Card>

      <Card padding="lg">
        <CardHeader><CardTitle>Upload Additional Historical Data</CardTitle></CardHeader>
        <input type="file" accept=".csv" onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])} />
        {uploadMsg && <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 8 }}>{uploadMsg}</p>}
      </Card>

      <Card padding="lg">
        <CardHeader>
          <CardTitle>Project Details</CardTitle>
          {!editing && (
            <Button size="sm" variant="secondary" onClick={startEditing}>Edit</Button>
          )}
        </CardHeader>

        {!editing ? (
          <>
            <dl style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: "8px 16px", fontSize: "var(--text-sm)" }}>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Name</dt><dd>{project.name}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Objective</dt><dd>{project.objective || "—"}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Target metric</dt><dd>{project.target_metric} ({project.direction})</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Target value</dt><dd>{project.target_value ?? "not set"}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Feature columns</dt><dd>{project.feature_columns.join(", ")}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Constraints</dt>
              <dd>{project.constraints.length ? project.constraints.map((c) => `${c.column} ${c.op} ${c.value}`).join("; ") : "none"}</dd>
            </dl>
            {saveMsg && <p style={{ fontSize: "var(--text-xs)", color: "var(--color-success)", marginTop: 12 }}>{saveMsg}</p>}
          </>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <label style={editLabelStyle}>Name</label>
            <input style={editInputStyle} value={name} onChange={(e) => setName(e.target.value)} />

            <label style={editLabelStyle}>Objective</label>
            <input style={editInputStyle} value={objective} onChange={(e) => setObjective(e.target.value)} />

            <label style={editLabelStyle}>Target metric column name</label>
            <input style={editInputStyle} value={targetMetric} onChange={(e) => setTargetMetric(e.target.value)} />

            <label style={editLabelStyle}>Direction</label>
            <select style={editInputStyle} value={direction} onChange={(e) => setDirection(e.target.value as Direction)}>
              <option value="maximize">Maximize</option>
              <option value="minimize">Minimize</option>
            </select>

            <label style={editLabelStyle}>Target value</label>
            <input style={editInputStyle} type="number" value={targetValue} onChange={(e) => setTargetValue(e.target.value)} />

            <label style={editLabelStyle}>Feature columns (comma-separated)</label>
            {hasExperiments ? (
              <>
                <input style={{ ...editInputStyle, opacity: 0.5 }} value={featureColumnsText} disabled />
                <p style={lockedNoteStyle}>
                  Locked -- this project already has {project.experiment_count} experiment(s). Changing feature
                  columns now would desync existing data. Create a new project to use different variables.
                </p>
              </>
            ) : (
              <input style={editInputStyle} value={featureColumnsText} onChange={(e) => setFeatureColumnsText(e.target.value)} />
            )}

            <label style={editLabelStyle}>Constraints</label>
            {hasExperiments ? (
              <p style={lockedNoteStyle}>
                Locked for the same reason as feature columns above -- existing experiment data would desync.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {constraints.map((c, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 0.6fr 1fr auto", gap: 8 }}>
                    <input style={editInputStyle} placeholder="column name" value={c.column}
                      onChange={(e) => updateConstraintRow(i, { column: e.target.value })} />
                    <select style={editInputStyle} value={c.op}
                      onChange={(e) => updateConstraintRow(i, { op: e.target.value as "<=" | ">=" })}>
                      <option value="<=">{"\u2264"}</option>
                      <option value=">=">{"\u2265"}</option>
                    </select>
                    <input style={editInputStyle} type="number" placeholder="value" value={c.value}
                      onChange={(e) => updateConstraintRow(i, { value: Number(e.target.value) })} />
                    <Button variant="ghost" size="sm" onClick={() => removeConstraint(i)}>Remove</Button>
                  </div>
                ))}
                <Button variant="secondary" size="sm" onClick={addConstraint}>+ Add constraint</Button>
              </div>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <Button variant="primary" loading={saving} onClick={handleSave}>Save Changes</Button>
              <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

const editLabelStyle: CSSProperties = {
  fontSize: "var(--text-xs)", fontWeight: 500, color: "var(--color-text-secondary)", marginTop: 4,
};
const editInputStyle: CSSProperties = {
  padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)",
  fontSize: "var(--text-sm)",
};
const lockedNoteStyle: CSSProperties = {
  fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", marginTop: 2,
};