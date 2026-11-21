"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { listAuditEvents, type AuditFilter, type AuditPage } from "@/lib/api/admin";

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function AuditTab({ role }: { role: string }) {
  const allowed = role === "owner" || role === "admin";
  const [draft, setDraft] = useState<AuditFilter>({});
  const [applied, setApplied] = useState<AuditFilter>({});
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((f: AuditFilter) => {
    listAuditEvents(f).then((p) => { setPage(p); setError(null); }).catch(() => setError("Could not load the audit log. Try again."));
  }, []);
  useEffect(() => { if (allowed) load(applied); }, [allowed, applied, load]);

  if (!allowed) return <p className="settings-note">Only an owner or admin can view the audit log.</p>;

  const set = (k: keyof AuditFilter) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setDraft({ ...draft, [k]: e.target.value });
  const offset = page?.offset ?? 0;
  const limit = page?.limit ?? 25;

  return (
    <Card padding="lg">
      <form className="settings-filters" onSubmit={(e) => { e.preventDefault(); setApplied({ ...draft, offset: 0 }); }}>
        <div>
          <label className="settings-label" htmlFor="au-actor">Person</label>
          <select id="au-actor" className="settings-field" value={draft.user_id ?? ""} onChange={set("user_id")}>
            <option value="">Everyone</option>
            {page?.actors.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div>
          <label className="settings-label" htmlFor="au-action">Action</label>
          <select id="au-action" className="settings-field" value={draft.action ?? ""} onChange={set("action")}>
            <option value="">All actions</option>
            {page?.actions.map((a) => <option key={a.action} value={a.action}>{a.label}</option>)}
          </select>
        </div>
        <div>
          <label className="settings-label" htmlFor="au-from">From</label>
          <input id="au-from" type="date" className="settings-field" value={draft.date_from ?? ""} onChange={set("date_from")} />
        </div>
        <div>
          <label className="settings-label" htmlFor="au-to">To</label>
          <input id="au-to" type="date" className="settings-field" value={draft.date_to ?? ""} onChange={set("date_to")} />
        </div>
        <div>
          <label className="settings-label" htmlFor="au-q">Search</label>
          <input id="au-q" className="settings-field" value={draft.q ?? ""} onChange={set("q")} />
        </div>
        <Button type="submit" size="sm" variant="primary">Apply</Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => { setDraft({}); setApplied({}); }}>Clear</Button>
      </form>
      {error && <ErrorCallout message={error} />}
      {page && page.events.length === 0 && <p className="settings-note">No events match these filters.</p>}
      {page && page.events.length > 0 && (
        <div className="settings-table-wrap">
          <table className="settings-table">
            <thead><tr><th>When</th><th>Person</th><th>Action</th><th>Program</th><th>Detail</th></tr></thead>
            <tbody>
              {page.events.map((e) => (
                <tr key={e.id}>
                  <td>{when(e.created_at)}</td>
                  <td>{e.actor ?? "System"}</td>
                  <td>{e.label}</td>
                  <td>{e.program_id ? <Link href={`/programs/${e.program_id}`} className="ov-link">{e.program_name}</Link> : ""}</td>
                  <td className="settings-note">{e.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {page && page.total > 0 && (
        <div className="settings-pager">
          <span>{offset + 1} to {Math.min(offset + limit, page.total)} of {page.total}</span>
          <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setApplied({ ...applied, offset: Math.max(0, offset - limit) })}>Previous</Button>
          <Button size="sm" variant="secondary" disabled={offset + limit >= page.total} onClick={() => setApplied({ ...applied, offset: offset + limit })}>Next</Button>
        </div>
      )}
    </Card>
  );
}
