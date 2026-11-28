"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { formatRelativeTime } from "@/lib/utils/format";
import { getDecisionAnalysis, runDecisionAnalysis, type Analysis, type CandidateResult, type Group, type Workspace } from "@/lib/api/decision";
import "@/styles/components/decision.css";

const GROUP_LABEL: Record<Group, string> = { feasible: "Feasible", unresolved: "Not confirmed", rejected: "Rejected" };
const GROUP_TONE: Record<Group, "success" | "warning" | "danger"> = { feasible: "success", unresolved: "warning", rejected: "danger" };
const PARETO_LABEL: Record<string, string> = {
  front: "On the Pareto front", dominated: "Dominated", not_comparable: "Not compared", not_eligible: "Not eligible", no_objectives: "No objectives defined",
};

const n = (v: number | null | undefined) => (v === null || v === undefined ? "Unknown" : Number(v.toPrecision(5)).toString());

function predictedText(r: CandidateResult, unit: string) {
  if (!r.predicted.available) return <span className="dc-unknown">Unknown. {r.predicted.reason}</span>;
  const p = r.predicted;
  return <>{`${n(p.value)} ${unit}`}{p.interval ? <div className="pw-muted">{`Model interval ${n(p.interval[0])} to ${n(p.interval[1])}`}</div> : null}</>;
}

function Explanation({ r }: { r: CandidateResult }) {
  const e = r.explanation;
  return (
    <details className="dc-details">
      <summary>{e.headline}</summary>
      {e.promising.length > 0 && (<><h4>Why it is promising</h4><ul>{e.promising.map((t) => <li key={t}>{t}</li>)}</ul></>)}
      {e.rejected.length > 0 && (<><h4>Why it is rejected</h4><ul>{e.rejected.map((t) => <li key={t}>{t}</li>)}</ul></>)}
      {e.dominating_constraints.length > 0 && (<><h4>Constraints that decide it</h4><ul>{e.dominating_constraints.map((c) => <li key={c.constraint_id}>{`${c.name}. ${c.effect}`}</li>)}</ul></>)}
      {e.supported_by.length > 0 && (<><h4>Data behind this assessment</h4><ul>{e.supported_by.map((t) => <li key={t}>{t}</li>)}</ul></>)}
      {e.could_change.length > 0 && (<><h4>What could change the result</h4><ul>{e.could_change.map((t) => <li key={t}>{t}</li>)}</ul></>)}
      <h4>Qualification</h4>
      <ul><li>{e.qualification}</li></ul>
    </details>
  );
}

export function DecisionResults({ programId, workspace, onChanged }: { programId: number; workspace: Workspace; onChanged: () => void }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);

  const latestId = workspace.latest_analysis_id;
  if (latestId !== null && loadedFor !== latestId && !busy) {
    setLoadedFor(latestId);
    getDecisionAnalysis(programId, latestId).then(setAnalysis).catch(() => setError("Could not load the latest analysis."));
  }

  async function run() {
    setBusy(true); setError(null);
    try {
      const r = await runDecisionAnalysis(programId);
      setAnalysis(r.analysis); setLoadedFor(r.analysis.id); onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : "The analysis could not be run."); } finally { setBusy(false); }
  }
  async function open(id: number) {
    setError(null);
    try { setAnalysis(await getDecisionAnalysis(programId, id)); } catch { setError("Could not load that analysis."); }
  }

  const unit = analysis?.target.unit ?? "";
  const rows: CandidateResult[] = analysis ? analysis.order.map((id) => analysis.candidates[String(id)]).filter((r): r is CandidateResult => r !== undefined) : [];
  return (
    <div>
      <p className="dc-banner" role="note">Ranking or a place on the Pareto front is not qualification. Every candidate stays Not qualified until physical validation is recorded and an approval is given.</p>
      {error && <ErrorCallout message={error} />}
      <div className="pw-row" style={{ marginBottom: 12 }}>
        {workspace.permissions.can_run && <Button variant="primary" disabled={busy} onClick={run}>{busy ? "Running" : analysis ? "Run a new analysis" : "Run analysis"}</Button>}
        {!workspace.permissions.can_run && <p className="pw-muted">Only an owner, an admin, the program owner or a reviewer can run an analysis.</p>}
        {workspace.analyses.length > 0 && (
          <label className="pw-label">Earlier analyses
            <select className="pw-field" value={analysis?.id ?? ""} onChange={(e) => open(Number(e.target.value))}>
              {workspace.analyses.map((a) => <option key={a.id} value={a.id}>{`Analysis ${a.id}, ${formatRelativeTime(a.created_at)}`}</option>)}
            </select>
          </label>
        )}
      </div>
      {workspace.analysis_outdated && <p className="dc-note" role="status">The setup, candidate values or predictions changed after the latest analysis. Run a new analysis to see the current picture. The earlier analysis is kept as it was.</p>}
      {!analysis && <p className="dc-note">No analysis yet. Add constraints and objectives, enter candidate values, then run an analysis. Anything you do not enter stays unknown.</p>}
      {analysis && (
        <>
          <dl className="dc-stats">
            <div className="dc-stat"><dt>Candidates</dt><dd>{analysis.summary.candidates}</dd></div>
            <div className="dc-stat"><dt>Feasible</dt><dd>{analysis.summary.counts.feasible}</dd></div>
            <div className="dc-stat"><dt>Not confirmed</dt><dd>{analysis.summary.counts.unresolved}</dd></div>
            <div className="dc-stat"><dt>Rejected</dt><dd>{analysis.summary.counts.rejected}</dd></div>
            <div className="dc-stat"><dt>On the Pareto front</dt><dd>{analysis.summary.front.length}</dd></div>
          </dl>
          <p className="dc-note">{analysis.summary.method}</p>
          <p className="dc-note">{analysis.summary.objectives.length ? `Objectives: ${analysis.summary.objectives.map((o) => `${o.direction} ${o.name}`).join(", ")}.` : "No objectives are defined, so candidates are grouped by constraints only."}</p>
          <div className="dc-scroll dc-section">
            <table className="dc-table">
              <caption className="pw-sr-only">Candidate comparison</caption>
              <thead><tr>
                <th scope="col">Candidate</th><th scope="col">Group</th><th scope="col">Trade off position</th><th scope="col">{`Predicted ${analysis.target.name}`}</th>
                <th scope="col">Evidence and applicability</th><th scope="col">Hard constraints</th><th scope="col">Soft trade offs</th><th scope="col">Missing information</th><th scope="col">Physical validation</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => {
                  const id = r.candidate_id;
                  const violated = r.hard.filter((h) => h.state === "violated");
                  const unknown = r.hard.filter((h) => h.state === "unknown");
                  return (
                    <tr key={id}>
                      <td><strong>{r.name}</strong><div><Badge tone="neutral">Not qualified</Badge></div></td>
                      <td><Badge tone={GROUP_TONE[r.group]}>{GROUP_LABEL[r.group]}</Badge></td>
                      <td>{PARETO_LABEL[r.pareto.status] ?? r.pareto.status}{r.group === "unresolved" && r.provisional.layer === 1 ? <div className="pw-muted">Would be on the front if its unknown values pass</div> : null}</td>
                      <td>{predictedText(r, unit)}</td>
                      <td>
                        <div>{r.context.domain_label ?? "Range check not available"}</div>
                        <div>{`Combination of inputs: ${r.context.input_space_label ?? "Not assessed"}`}</div>
                        <div className="pw-muted">{r.context.model_version_number ? `Model version ${r.context.model_version_number}. ${r.context.validation_state ?? ""}` : "No model version"}</div>
                      </td>
                      <td>
                        {r.hard.length === 0 ? "None defined" : (
                          <>
                            <div>{`${r.hard.length - violated.length - unknown.length} met, ${violated.length} violated, ${unknown.length} unknown`}</div>
                            {violated.map((h) => <div key={h.constraint_id} className="pw-muted">{`Violated: ${h.name}`}</div>)}
                            {unknown.map((h) => <div key={h.constraint_id} className="pw-muted">{`Unknown: ${h.name}`}</div>)}
                          </>
                        )}
                      </td>
                      <td>{r.soft_summary.total === 0 ? "None defined" : `${r.soft_summary.met} met, ${r.soft_summary.missed} missed, ${r.soft_summary.unknown} unknown`}</td>
                      <td>{r.missing_information.length === 0 ? "None" : <ul className="pw-list">{r.missing_information.slice(0, 4).map((m, i) => <li key={`${m.ref}-${i}`}><span>{m.needed_for}</span></li>)}{r.missing_information.length > 4 ? <li><span className="pw-muted">{`and ${r.missing_information.length - 4} more`}</span></li> : null}</ul>}</td>
                      <td>{r.physical_validation.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="dc-section">
            <h3>Why each candidate landed where it did</h3>
            {rows.map((r) => {
              const id = r.candidate_id;
              return <div key={id} style={{ marginBottom: 8 }}><strong>{r.name}</strong><Explanation r={r} /></div>;
            })}
          </div>
          {analysis.summary.constraint_impact.length > 0 && (
            <div className="dc-section">
              <h3>Which hard constraints decide the outcome</h3>
              <p className="dc-note">Each row shows what changes if that one constraint were removed.</p>
              <div className="dc-scroll"><table className="dc-table">
                <caption className="pw-sr-only">Constraint impact</caption>
                <thead><tr><th scope="col">Constraint</th><th scope="col" className="num">Candidates violating it</th><th scope="col" className="num">Candidates it cannot check</th><th scope="col">Would change if removed</th></tr></thead>
                <tbody>{analysis.summary.constraint_impact.map((c) => (
                  <tr key={c.constraint_id}><td>{c.name}</td><td className="num">{c.violated_by}</td><td className="num">{c.unknown_for}</td>
                    <td>{Object.keys(c.would_change_if_removed).length === 0 ? "Nothing" : Object.entries(c.would_change_if_removed).map(([id, g]) => `${analysis.candidates[id]?.name ?? id} would be ${g}`).join(", ")}</td></tr>
                ))}</tbody></table></div>
            </div>
          )}
          <p className="dc-note">{analysis.summary.interval_note}</p>
          <p className="dc-note">{`Analysis ${analysis.id}, ${formatRelativeTime(analysis.created_at)}. Engine ${analysis.engine_version}. Input identity ${analysis.input_hash.slice(0, 12)}.`}</p>
        </>
      )}
    </div>
  );
}
