"use client";

import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import {
  addRequirement, archiveRequirement, recordRequirementResult, restoreRequirement, updateRequirement, type Requirement, type Workspace,
} from "@/lib/api/workspace";
import { CRITICALITY_LABEL, DIRECTION_LABEL, SUPPORT_LABEL, formatDate, messageOf } from "./shared";
import "@/styles/components/program-workspace.css";

interface Props { changeCaseId: number; ws: Workspace; onChange: (ws: Workspace) => void }

const EMPTY = { property: "", threshold: "", direction: "maximize", unit: "", test_method: "", criticality: "important", source: "" };

export function RequirementsTab({ changeCaseId, ws, onChange }: Props) {
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState<number | null>(null);
  const [resultText, setResultText] = useState("");
  const [resultMet, setResultMet] = useState("yes");
  const [resultRef, setResultRef] = useState("");

  const manage = ws.permissions.can_manage;
  const write = ws.permissions.can_write;

  async function run(action: () => Promise<Workspace>, after?: () => void) {
    setError(null); setBusy(true);
    try { onChange(await action()); after?.(); } catch (err) { setError(messageOf(err)); } finally { setBusy(false); }
  }

  function change(req: Requirement, value: string) {
    run(() => updateRequirement(changeCaseId, req.id, { criticality: value }));
  }

  return (
    <div className="pw-stack">
      {error && <ErrorCallout message={error} />}
      <Card padding="lg">
        <CardHeader><CardTitle>Requirements register</CardTitle></CardHeader>
        <p className="pw-lead" style={{ marginBottom: 12 }}>
          What a replacement must achieve. The ranking models the first requirement only. The others are tracked here and are met only by a recorded result, never by a model estimate.
        </p>
        {ws.completion.message && <p className="pw-notice" role="status">{ws.completion.message}</p>}
        <div className="pw-table-wrap">
          <table className="pw-table">
            <thead>
              <tr><th>Property</th><th>Requirement</th><th>Test method</th><th>Criticality</th><th>Source</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {ws.requirements.map((r) => (
                <tr key={r.id}>
                  <td>{r.property}{r.is_ranking_target && <span className="pw-list__meta">Modelled by the ranking</span>}</td>
                  <td>{DIRECTION_LABEL[r.direction]} {r.threshold}{r.unit ? ` ${r.unit}` : ""}</td>
                  <td>{r.test_method ?? "Not set"}</td>
                  <td>
                    {manage ? (
                      <select aria-label={`Criticality of ${r.property}`} className="pw-field" value={r.criticality} disabled={busy} onChange={(e) => change(r, e.target.value)}>
                        {Object.entries(CRITICALITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    ) : CRITICALITY_LABEL[r.criticality]}
                  </td>
                  <td>{r.source ?? "Not set"}</td>
                  <td>
                    {SUPPORT_LABEL[r.support]}
                    {r.latest_result && <span className="pw-list__meta">{r.latest_result.text}{r.latest_result.reference ? `, ${r.latest_result.reference}` : ""}</span>}
                  </td>
                  <td>
                    <span className="pw-actions">
                      {write && !r.is_ranking_target && <Button size="sm" variant="secondary" onClick={() => { setRecording(recording === r.id ? null : r.id); setResultText(""); setResultRef(""); setResultMet("yes"); }}>Record result</Button>}
                      {manage && !r.is_ranking_target && r.criticality !== "critical" && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => archiveRequirement(changeCaseId, r.id))}>Archive</Button>}
                    </span>
                  </td>
                </tr>
              ))}
              {ws.requirements.length === 0 && <tr><td colSpan={7} className="pw-muted">No requirements yet.</td></tr>}
            </tbody>
          </table>
        </div>
        {recording !== null && (
          <form className="pw-row" style={{ marginTop: 16 }} onSubmit={(e) => {
            e.preventDefault();
            run(() => recordRequirementResult(changeCaseId, recording, { result_text: resultText, met: resultMet === "yes", test_reference: resultRef || null }),
              () => setRecording(null));
          }}>
            <div>
              <label className="pw-label" htmlFor="pw-result-text">Measured result</label>
              <input id="pw-result-text" className="pw-field" value={resultText} maxLength={1000} required onChange={(e) => setResultText(e.target.value)} />
            </div>
            <div>
              <label className="pw-label" htmlFor="pw-result-met">Met the requirement</label>
              <select id="pw-result-met" className="pw-field" value={resultMet} onChange={(e) => setResultMet(e.target.value)}>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </div>
            <div>
              <label className="pw-label" htmlFor="pw-result-ref">Test report (optional)</label>
              <input id="pw-result-ref" className="pw-field" value={resultRef} maxLength={300} onChange={(e) => setResultRef(e.target.value)} />
            </div>
            <Button type="submit" size="sm" variant="primary" loading={busy}>Save result</Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => setRecording(null)}>Cancel</Button>
          </form>
        )}
      </Card>

      {ws.archived_requirements.length > 0 && (
        <Card padding="lg">
          <CardHeader><CardTitle>Archived requirements</CardTitle></CardHeader>
          <p className="pw-lead" style={{ marginBottom: 12 }}>
            Archived requirements are not counted when a program is approved or closed. A restored requirement counts again with the criticality shown here.
          </p>
          <div className="pw-table-wrap">
            <table className="pw-table">
              <thead><tr><th>Property</th><th>Requirement</th><th>Criticality</th><th>Archived</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {ws.archived_requirements.map((r) => (
                  <tr key={r.id}>
                    <td>{r.property}</td>
                    <td>{DIRECTION_LABEL[r.direction]} {r.threshold}{r.unit ? ` ${r.unit}` : ""}</td>
                    <td>{CRITICALITY_LABEL[r.criticality]}</td>
                    <td>{formatDate(r.archived_at)}</td>
                    <td>{manage && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => restoreRequirement(changeCaseId, r.id))}>Restore</Button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {manage && (
        <Card padding="lg">
          <CardHeader><CardTitle>Add a requirement</CardTitle></CardHeader>
          <form className="pw-form" onSubmit={(e) => {
            e.preventDefault();
            run(() => addRequirement(changeCaseId, {
              property: form.property, threshold: Number(form.threshold), direction: form.direction, unit: form.unit || null,
              test_method: form.test_method || null, criticality: form.criticality, source: form.source || null,
            }), () => setForm(EMPTY));
          }}>
            <div className="pw-row">
              <div>
                <label className="pw-label" htmlFor="rq-property">Property</label>
                <input id="rq-property" className="pw-field" value={form.property} maxLength={120} required onChange={(e) => setForm({ ...form, property: e.target.value })} />
              </div>
              <div>
                <label className="pw-label" htmlFor="rq-direction">Value must be</label>
                <select id="rq-direction" className="pw-field" value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
                  <option value="maximize">At least</option>
                  <option value="minimize">At most</option>
                </select>
              </div>
              <div>
                <label className="pw-label" htmlFor="rq-threshold">Threshold</label>
                <input id="rq-threshold" type="number" step="any" className="pw-field" value={form.threshold} required onChange={(e) => setForm({ ...form, threshold: e.target.value })} />
              </div>
              <div>
                <label className="pw-label" htmlFor="rq-unit">Unit</label>
                <input id="rq-unit" className="pw-field" value={form.unit} maxLength={40} onChange={(e) => setForm({ ...form, unit: e.target.value })} />
              </div>
            </div>
            <div className="pw-row">
              <div>
                <label className="pw-label" htmlFor="rq-method">Test method</label>
                <input id="rq-method" className="pw-field" value={form.test_method} maxLength={200} onChange={(e) => setForm({ ...form, test_method: e.target.value })} />
              </div>
              <div>
                <label className="pw-label" htmlFor="rq-criticality">Criticality</label>
                <select id="rq-criticality" className="pw-field" value={form.criticality} onChange={(e) => setForm({ ...form, criticality: e.target.value })}>
                  {Object.entries(CRITICALITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div>
                <label className="pw-label" htmlFor="rq-source">Source</label>
                <input id="rq-source" className="pw-field" value={form.source} maxLength={200} onChange={(e) => setForm({ ...form, source: e.target.value })} />
              </div>
            </div>
            <div className="pw-actions"><Button type="submit" variant="primary" size="sm" loading={busy}>Add requirement</Button></div>
          </form>
        </Card>
      )}
    </div>
  );
}
