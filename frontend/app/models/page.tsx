"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorCallout } from "@/components/ui/Feedback";
import { StateBadge } from "@/components/models/parts";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { listModels, type ModelLibrary } from "@/lib/api/models";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/program-workspace.css";
import "@/styles/components/models.css";

export default function ModelsPage() {
  const { checking } = useRequireAuth();
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ModelLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((o: number) => {
    listModels(undefined, o).then((p) => { setPage(p); setError(null); }).catch(() => setError("Could not load model versions. Try again."));
  }, []);
  useEffect(() => { if (!checking) load(offset); }, [checking, offset, load]);

  if (checking) return null;
  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Models" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Models" subtitle="Build records of the model across your programs, with how far each one has been validated." />
        {error && <ErrorCallout message={error} />}
        <Card padding="lg">
          {!page && !error && <p className="pw-muted">Loading model versions</p>}
          {page && page.versions.length === 0 && (
            <EmptyState title="No model versions yet" description="A model version is a build record. It is registered when you rank candidates in a program, records what the model was built from and holds its validation results. The fitted model itself is not stored."
              action={<Link href="/programs"><Button variant="primary">Open programs</Button></Link>} />
          )}
          {page && page.versions.length > 0 && (
            <div className="mv-scroll">
              <table className="mv-table">
                <caption className="pw-sr-only">Model versions</caption>
                <thead><tr><th scope="col">Version</th><th scope="col">Program</th><th scope="col">Evidence</th><th scope="col" className="num">Training rows</th><th scope="col">Validation</th><th scope="col">Registered</th></tr></thead>
                <tbody>
                  {page.versions.map((v) => (
                    <tr key={v.id}>
                      <td><Link href={`/models/${v.id}`}>{`Version ${v.version_number}`}</Link> <span className="mv-hash">{v.model_key}</span> {v.is_current && <Badge tone="accent">Current evidence</Badge>}</td>
                      <td><Link href={`/programs/${v.program_id}`}>{v.program_name}</Link></td>
                      <td>{v.dataset_filename ?? "Not stored"}</td>
                      <td className="num">{v.n_training_rows}</td>
                      <td><StateBadge state={v.validation.state} /></td>
                      <td>{formatRelativeTime(v.registered_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page && page.total > page.limit && (
            <div className="pw-row" style={{ marginTop: 12 }}>
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - page.limit))}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>Next</Button>
              <span className="pw-muted">{`${offset + 1} to ${Math.min(offset + page.limit, page.total)} of ${page.total}`}</span>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
