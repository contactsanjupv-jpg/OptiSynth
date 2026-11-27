"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ErrorCallout } from "@/components/ui/Feedback";
import { StateBadge } from "@/components/models/parts";
import { listModels, registerModel, type ModelSummary } from "@/lib/api/models";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/models.css";

/** Analysis tab of a program: the model versions built from its evidence and how far each has been validated. */
export function ModelAnalysisPanel({ programId, canRun }: { programId: number; canRun: boolean }) {
  const [versions, setVersions] = useState<ModelSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { listModels(programId).then((p) => { setVersions(p.versions); setError(null); }).catch(() => setError("Could not load model versions.")); }, [programId]);
  useEffect(() => { load(); }, [load]);

  async function register() {
    setBusy(true); setMessage(null); setError(null);
    try {
      const r = await registerModel(programId);
      setMessage(r.created ? `Registered version ${r.version.version_number} from the current evidence.` : `The current evidence is already registered as version ${r.version.version_number}.`);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not register a model version."); } finally { setBusy(false); }
  }

  return (
    <Card padding="lg">
      <h3 style={{ margin: "0 0 8px" }}>Model build records</h3>
      <p className="mv-note">A model version is a build record. It records exactly what the model was built from, and the fitted model itself is not stored. Open one to see its validation, applicability and uncertainty. None of this is a physical qualification result.</p>
      {error && <ErrorCallout message={error} />}
      {message && <p className="mv-note" role="status">{message}</p>}
      {versions && versions.length === 0 && <p className="mv-note">No model version yet. Rank candidates on the Candidates tab, or register one from the current evidence.</p>}
      {versions && versions.length > 0 && (
        <div className="mv-scroll"><table className="mv-table">
          <caption className="pw-sr-only">Model versions of this program</caption>
          <thead><tr><th scope="col">Version</th><th scope="col">Evidence</th><th scope="col" className="num">Rows</th><th scope="col">Validation</th><th scope="col">Registered</th></tr></thead>
          <tbody>{versions.map((v) => (
            <tr key={v.id}><td><Link href={`/models/${v.id}`}>{`Version ${v.version_number}`}</Link> {v.is_current && <Badge tone="accent">Current evidence</Badge>}</td>
              <td>{v.dataset_filename ?? "Not stored"}</td><td className="num">{v.n_training_rows}</td><td><StateBadge state={v.validation.state} /></td><td>{formatRelativeTime(v.registered_at)}</td></tr>
          ))}</tbody></table></div>
      )}
      {canRun && <div style={{ marginTop: 12 }}><Button size="sm" variant="secondary" disabled={busy} onClick={register}>{busy ? "Registering" : "Register current evidence"}</Button></div>}
    </Card>
  );
}
