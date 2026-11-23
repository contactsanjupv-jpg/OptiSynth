"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
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
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { listEvidenceLibrary, type LibraryFilter, type LibraryPage } from "@/lib/api/evidence";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/program-workspace.css";
import "@/styles/components/overview.css";

function EvidenceLibraryInner() {
  const { checking } = useRequireAuth();
  const [draft, setDraft] = useState<LibraryFilter>({});
  const [applied, setApplied] = useState<LibraryFilter>({});
  const [page, setPage] = useState<LibraryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((f: LibraryFilter) => {
    listEvidenceLibrary(f).then((p) => { setPage(p); setError(null); }).catch(() => setError("Could not load the evidence library. Try again."));
  }, []);
  useEffect(() => { if (!checking) load(applied); }, [checking, applied, load]);

  if (checking) return null;
  const offset = page?.offset ?? 0;
  const limit = page?.limit ?? 25;
  const filtered = Boolean(applied.q || applied.document_type || applied.status);

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Evidence" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Evidence" subtitle="Every uploaded document across your programs, with its review status." />
        {error && <ErrorCallout message={error} />}
        <Card padding="lg">
          <form className="pw-row" onSubmit={(e) => { e.preventDefault(); setApplied({ ...draft, offset: 0 }); }} style={{ marginBottom: 16 }}>
            <div>
              <label className="pw-label" htmlFor="ev-q">Search</label>
              <input id="ev-q" className="pw-field" value={draft.q ?? ""} placeholder="File, program, source or method" onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
            </div>
            <div>
              <label className="pw-label" htmlFor="ev-type">Document type</label>
              <select id="ev-type" className="pw-field" value={draft.document_type ?? ""} onChange={(e) => setDraft({ ...draft, document_type: e.target.value })}>
                <option value="">All types</option>
                {page?.types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="pw-label" htmlFor="ev-status">Review status</label>
              <select id="ev-status" className="pw-field" value={draft.status ?? ""} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
                <option value="">All statuses</option>
                {page?.statuses.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
            </div>
            <Button type="submit" size="sm" variant="primary">Apply</Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => { setDraft({}); setApplied({}); }}>Clear</Button>
          </form>
          {page && page.items.length === 0 && (
            filtered ? <p className="pw-muted">No documents match these filters.</p>
              : <EmptyState title="No evidence yet" description="Upload evidence from a program. It appears here with its source, version and review status." action={<Link href="/programs"><Button variant="primary">Open programs</Button></Link>} />
          )}
          {page && page.items.length > 0 && (
            <div className="pw-table-wrap">
              <table className="pw-table">
                <thead><tr><th>File</th><th>Program</th><th>Type</th><th>Version</th><th>Status</th><th>Open conflicts</th><th>Uploaded by</th><th>When</th></tr></thead>
                <tbody>
                  {page.items.map((d) => (
                    <tr key={d.id}>
                      <td>{d.filename}</td>
                      <td><Link href={`/programs/${d.program.id}?tab=evidence`} className="ov-link ov-link--strong">{d.program.name}</Link></td>
                      <td>{d.document_type_label ?? "Not set"}</td>
                      <td>{d.document_version ?? `Upload ${d.version_number ?? ""}`}</td>
                      <td><Badge tone={d.status === "accepted" ? "success" : d.status === "rejected" ? "danger" : d.status === "clarification_requested" || d.status === "conflict_flagged" ? "warning" : "neutral"}>{d.status_label}</Badge></td>
                      <td>{d.open_conflicts}</td>
                      <td>{d.uploaded_by ?? "Unknown"}</td>
                      <td>{formatRelativeTime(d.uploaded_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page && page.total > 0 && (
            <div className="pw-actions" style={{ marginTop: 12, alignItems: "center" }}>
              <span className="pw-muted">{offset + 1} to {Math.min(offset + limit, page.total)} of {page.total}</span>
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setApplied({ ...applied, offset: Math.max(0, offset - limit) })}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={offset + limit >= page.total} onClick={() => setApplied({ ...applied, offset: offset + limit })}>Next</Button>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

export default function EvidenceLibraryPage() {
  return (
    <Suspense fallback={null}>
      <EvidenceLibraryInner />
    </Suspense>
  );
}
