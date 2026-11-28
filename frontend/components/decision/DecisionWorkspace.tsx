"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorCallout } from "@/components/ui/Feedback";
import { CandidateDataSection, ConstraintsSection, ObjectivesSection } from "@/components/decision/DecisionSetup";
import { DecisionResults } from "@/components/decision/DecisionResults";
import { getDecisionWorkspace, type Workspace } from "@/lib/api/decision";
import "@/styles/components/decision.css";

const SECTIONS = [
  { key: "results", label: "Results" }, { key: "constraints", label: "Constraints" }, { key: "objectives", label: "Objectives" }, { key: "data", label: "Candidate data" },
] as const;
type Section = (typeof SECTIONS)[number]["key"];

/** Analysis tab of a program: constraints, objectives, customer entered values and the decision analysis. */
export function DecisionWorkspace({ programId }: { programId: number }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [section, setSection] = useState<Section>("results");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => { getDecisionWorkspace(programId).then((w) => { setWorkspace(w); setError(null); }).catch(() => setError("Could not load the decision setup.")); }, [programId]);
  useEffect(() => { load(); }, [load]);

  if (error) return <ErrorCallout message={error} />;
  if (!workspace) return <p className="pw-muted">Loading the decision setup</p>;
  return (
    <div className="dc-section">
      <h2 style={{ margin: "0 0 4px", fontSize: "var(--text-lg)" }}>Substitution decision</h2>
      <p className="dc-note">Compare candidates against your constraints and competing goals. Predictions are model estimates. Nothing here is a physical qualification result.</p>
      <div role="tablist" aria-label="Decision sections" className="pw-tabs">
        {SECTIONS.map((s) => <button key={s.key} id={`dc-tab-${s.key}`} role="tab" type="button" className="pw-tab" aria-selected={section === s.key} onClick={() => setSection(s.key)}>{s.label}</button>)}
      </div>
      <div role="tabpanel" aria-labelledby={`dc-tab-${section}`} style={{ marginTop: 16 }}>
        {section === "results" && <DecisionResults programId={programId} workspace={workspace} onChanged={load} />}
        {section === "constraints" && <ConstraintsSection programId={programId} workspace={workspace} onChanged={load} />}
        {section === "objectives" && <ObjectivesSection programId={programId} workspace={workspace} onChanged={load} />}
        {section === "data" && <CandidateDataSection programId={programId} workspace={workspace} onChanged={load} />}
      </div>
    </div>
  );
}
