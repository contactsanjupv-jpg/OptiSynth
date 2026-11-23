"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ErrorCallout } from "@/components/ui/Feedback";
import { messageOf } from "@/components/programs/shared";
import {
  getDocument, getDocumentRows, getProgramDocuments, resolveConflict, reviewDocument, scanConflicts, updateDocumentMetadata,
  type Conflict, type DocumentDetail, type EvidenceDocument, type ProgramDocuments, type TracePage,
} from "@/lib/api/evidence";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/program-workspace.css";

const TONE: Record<string, "neutral" | "success" | "warning" | "danger"> = {
  accepted: "success", rejected: "danger", excluded: "neutral", superseded: "neutral", clarification_requested: "warning", conflict_flagged: "warning",
};
const ACTIONS = [
  { key: "accept", label: "Accept" }, { key: "request_clarification", label: "Request clarification" }, { key: "flag_conflict", label: "Flag a conflict" },
  { key: "reject", label: "Reject" }, { key: "exclude", label: "Exclude" }, { key: "supersede", label: "Mark as superseded" },
];
const LEVEL: Record<string, string> = { high: "High", medium: "Medium", low: "Low", none: "None", partial: "Partial", substantial: "Substantial", not_assessed: "Not assessed" };
const RESOLUTIONS = [
  { key: "accepted_as_is", label: "Both are valid, keep both" }, { key: "prefer_dataset", label: "Prefer one document" },
  { key: "not_a_conflict", label: "This is not a conflict" },
];

export function EvidencePanel({ changeCaseId, onChanged }: { changeCaseId: number; onChanged?: () => void }) {
  const [data, setData] = useState<ProgramDocuments | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [trace, setTrace] = useState<TracePage | null>(null);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState("accept");
  const [reason, setReason] = useState("");
  const [replacement, setReplacement] = useState("");
  const [meta, setMeta] = useState<Record<string, string>>({});
  const [resolving, setResolving] = useState<number | null>(null);
  const [resolution, setResolution] = useState("accepted_as_is");
  const [note, setNote] = useState("");
  const [preferred, setPreferred] = useState("");
  const [showResolved, setShowResolved] = useState(false);

  const load = useCallback(() => {
    getProgramDocuments(changeCaseId).then((d) => { setData(d); setError(null); }).catch(() => setError("Could not load the evidence library. Try again."));
  }, [changeCaseId]);
  useEffect(() => { load(); }, [load]);

  function fill(d: DocumentDetail) {
    setDetail(d);
    setMeta({ document_type: d.document_type ?? "", source_note: d.source_note ?? "", document_version: d.document_version ?? "", test_method: d.test_method ?? "",
              document_date: d.document_date ?? "", source_reliability: d.source_reliability ?? "" });
  }

  async function run<T>(fn: () => Promise<T>, then: (r: T) => void) {
    setBusy(true); setError(null);
    try { then(await fn()); } catch (err) { setError(messageOf(err)); } finally { setBusy(false); }
  }

  function toggle(doc: EvidenceDocument) {
    if (open === doc.id) { setOpen(null); setDetail(null); setTrace(null); return; }
    setOpen(doc.id); setTrace(null); setReason(""); setAction("accept"); setReplacement("");
    run(() => getDocument(changeCaseId, doc.id), fill);
  }

  const refresh = () => { load(); onChanged?.(); };
  const unresolved = (data?.conflicts ?? []).filter((c) => c.status === "open");
  const resolved = (data?.conflicts ?? []).filter((c) => c.status !== "open");
  const canReview = Boolean(data?.permissions.can_review);

  function conflictCard(c: Conflict) {
    return (
      <li key={c.id} style={{ display: "block" }}>
        <div><Badge tone={c.severity === "high" ? "danger" : c.severity === "medium" ? "warning" : "neutral"}>{c.severity === "high" ? "High" : c.severity === "medium" ? "Medium" : "Low"}</Badge>{" "}<strong>{c.title}</strong></div>
        <p className="pw-lead" style={{ margin: "6px 0" }}>{c.description}</p>
        {c.status !== "open" && <p className="pw-muted">{c.resolution === "no_longer_detected" ? "Closed because it is no longer detected." : `Resolved by ${c.resolved_by ?? "a reviewer"}: ${c.resolution_note ?? ""}`}</p>}
        {c.status === "open" && canReview && (resolving === c.id ? (
          <form className="pw-form" onSubmit={(e) => {
            e.preventDefault();
            run(() => resolveConflict(changeCaseId, c.id, resolution, note, resolution === "prefer_dataset" ? Number(preferred) : null),
              (d) => { setData(d); setResolving(null); setNote(""); setPreferred(""); onChanged?.(); });
          }}>
            <label className="pw-label" htmlFor={`rs-${c.id}`}>Decision</label>
            <select id={`rs-${c.id}`} className="pw-field" value={resolution} onChange={(e) => setResolution(e.target.value)}>
              {RESOLUTIONS.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
            {resolution === "prefer_dataset" && (
              <select aria-label="Preferred document" className="pw-field" value={preferred} required onChange={(e) => setPreferred(e.target.value)}>
                <option value="">Choose a document</option>
                {c.dataset_ids.map((id, i) => <option key={id} value={id}>{c.documents[i] ?? `Document ${id}`}</option>)}
              </select>
            )}
            <label className="pw-label" htmlFor={`rn-${c.id}`}>Why (required, kept in the record)</label>
            <textarea id={`rn-${c.id}`} className="pw-field" rows={2} value={note} required maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            <div className="pw-actions">
              <Button type="submit" size="sm" variant="primary" loading={busy}>Record decision</Button>
              <Button type="button" size="sm" variant="secondary" onClick={() => setResolving(null)}>Cancel</Button>
            </div>
          </form>
        ) : <Button size="sm" variant="secondary" onClick={() => { setResolving(c.id); setResolution("accepted_as_is"); setNote(""); }}>Resolve</Button>)}
      </li>
    );
  }

  return (
    <div className="pw-stack" style={{ marginTop: 20 }}>
      {error && <ErrorCallout message={error} />}
      {data?.held_notice && <p className="pw-notice" role="status">{data.held_notice}</p>}

      <Card padding="lg">
        <CardHeader><CardTitle>Evidence library</CardTitle></CardHeader>
        <p className="pw-lead" style={{ marginBottom: 12 }}>
          Every uploaded document with its review status. Only accepted evidence reaches the model, and the newest accepted upload is the current evidence.
        </p>
        {data && data.documents.length === 0 && <p className="pw-muted">No documents yet. Import evidence above.</p>}
        <ul className="pw-list">
          {data?.documents.map((d) => (
            <li key={d.id} style={{ display: "block" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <span>
                  <strong>{d.filename}</strong>, version {d.version_number}{d.is_current ? ", current evidence" : ""}
                  <span className="pw-list__meta">
                    {d.document_type_label ?? "Type not set"}, {d.row_count} rows, uploaded {formatRelativeTime(d.uploaded_at)}{d.uploaded_by ? ` by ${d.uploaded_by}` : ""}
                    {d.open_conflicts > 0 ? `, ${d.open_conflicts} open ${d.open_conflicts === 1 ? "conflict" : "conflicts"}` : ""}
                  </span>
                </span>
                <span className="pw-actions">
                  <Badge tone={TONE[d.status] ?? "neutral"}>{d.status_label}</Badge>
                  <Button size="sm" variant="secondary" onClick={() => toggle(d)}>{open === d.id ? "Close" : "Details"}</Button>
                </span>
              </div>
              {open === d.id && detail && detail.id === d.id && (
                <div className="pw-stack" style={{ marginTop: 12 }}>
                  <div className="pw-grid">
                    <form className="pw-form" onSubmit={(e) => {
                      e.preventDefault();
                      const changes: Record<string, string | null> = {};
                      for (const [k, v] of Object.entries(meta)) changes[k] = v || null;
                      run(() => updateDocumentMetadata(changeCaseId, d.id, changes), (r) => { fill(r); refresh(); });
                    }}>
                      <strong>Document details</strong>
                      <label className="pw-label" htmlFor="md-type">Document type</label>
                      <select id="md-type" className="pw-field" value={meta.document_type} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, document_type: e.target.value })}>
                        <option value="">Not set</option>
                        {data.types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                      </select>
                      <label className="pw-label" htmlFor="md-note">Source note</label>
                      <textarea id="md-note" className="pw-field" rows={2} maxLength={1000} value={meta.source_note} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, source_note: e.target.value })} />
                      <div className="pw-row">
                        <div><label className="pw-label" htmlFor="md-ver">Version</label>
                          <input id="md-ver" className="pw-field" maxLength={60} value={meta.document_version} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, document_version: e.target.value })} /></div>
                        <div><label className="pw-label" htmlFor="md-date">Document date</label>
                          <input id="md-date" type="date" className="pw-field" value={meta.document_date} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, document_date: e.target.value })} /></div>
                      </div>
                      <label className="pw-label" htmlFor="md-method">Test method</label>
                      <input id="md-method" className="pw-field" maxLength={200} value={meta.test_method} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, test_method: e.target.value })} />
                      <label className="pw-label" htmlFor="md-rel">Source reliability, assessed by you</label>
                      <select id="md-rel" className="pw-field" value={meta.source_reliability} disabled={!detail.writable} onChange={(e) => setMeta({ ...meta, source_reliability: e.target.value })}>
                        <option value="">Not assessed</option>
                        {data.reliability.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                      </select>
                      {detail.writable && <div className="pw-actions"><Button type="submit" size="sm" variant="primary" loading={busy}>Save details</Button></div>}
                    </form>

                    <div className="pw-form">
                      <strong>Evidence quality</strong>
                      <p className="pw-muted">Six separate dimensions. They are not combined into one score.</p>
                      <ul className="pw-list">
                        {detail.quality.map((q) => (
                          <li key={q.key} style={{ display: "block" }}>
                            <strong>{q.label}</strong>: {LEVEL[q.level] ?? q.level}
                            <span className="pw-list__meta">{q.basis}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>

                  {detail.can_review && detail.writable && detail.status !== "superseded" && (
                    <form className="pw-form" style={{ maxWidth: 520 }} onSubmit={(e) => {
                      e.preventDefault();
                      run(() => reviewDocument(changeCaseId, d.id, action, reason, action === "supersede" ? Number(replacement) : null),
                        (r) => { fill(r); setReason(""); refresh(); });
                    }}>
                      <strong>Review decision</strong>
                      <label className="pw-label" htmlFor="rv-action">Decision</label>
                      <select id="rv-action" className="pw-field" value={action} onChange={(e) => setAction(e.target.value)}>
                        {ACTIONS.filter((a) => a.key !== "accept" || detail.status !== "accepted").map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
                      </select>
                      {action === "supersede" && (
                        <select aria-label="Replacing document" className="pw-field" value={replacement} required onChange={(e) => setReplacement(e.target.value)}>
                          <option value="">Choose the newer document</option>
                          {detail.others.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                        </select>
                      )}
                      <label className="pw-label" htmlFor="rv-reason">Reason{action === "accept" ? " (optional)" : ""}</label>
                      <textarea id="rv-reason" className="pw-field" rows={2} maxLength={1000} value={reason} required={action !== "accept"} onChange={(e) => setReason(e.target.value)} />
                      <div className="pw-actions"><Button type="submit" size="sm" variant="primary" loading={busy}>Record decision</Button></div>
                    </form>
                  )}

                  <div>
                    <strong>Review history</strong>
                    {detail.history.length === 0 ? <p className="pw-muted">No review decisions yet. The document was accepted when it was uploaded.</p> : (
                      <ul className="pw-list">
                        {detail.history.map((h) => (
                          <li key={h.id}><span>{h.label}{h.reason ? `: ${h.reason}` : ""}<span className="pw-list__meta">{h.actor ?? "System"}, {formatRelativeTime(h.created_at)}</span></span></li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => getDocumentRows(changeCaseId, d.id, 0), setTrace)}>View source rows</Button>
                    {trace && trace.dataset.id === d.id && (
                      <div style={{ marginTop: 12 }}>
                        <p className="pw-muted">
                          Version {trace.dataset.version_number}, {trace.dataset.status_label.toLowerCase()}, reviewed by {trace.dataset.reviewed_by ?? "no one yet"}. File hash {trace.dataset.file_sha256?.slice(0, 16) ?? "not recorded"}.
                        </p>
                        <div className="pw-table-wrap">
                          <table className="pw-table">
                            <thead><tr><th>Row</th><th>Source</th><th>Values, original to stored</th><th>Notes</th></tr></thead>
                            <tbody>
                              {trace.rows.map((r) => (
                                <tr key={r.id}>
                                  <td>{r.number}</td>
                                  <td>{r.source ? `${r.source.location ?? r.source.table ?? "Table"}, row ${r.source.row ?? "unknown"}` : "Not recorded"}</td>
                                  <td>
                                    {r.values ? Object.entries(r.values).map(([col, v]) => (
                                      <div key={col}>{col}: {v.original_value}{v.original_unit ? ` ${v.original_unit}` : ""} to {v.normalized_value}{v.normalized_unit ? ` ${v.normalized_unit}` : ""}
                                        <span className="pw-list__meta">{v.transformation}</span></div>
                                    )) : <span className="pw-muted">{r.trace_note}</span>}
                                  </td>
                                  <td>{r.source_flags.map((f) => <div key={f.code + f.message}>{f.message}</div>)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="pw-actions" style={{ marginTop: 8 }}>
                          <span className="pw-muted">{trace.offset + 1} to {Math.min(trace.offset + trace.limit, trace.total)} of {trace.total}</span>
                          <Button size="sm" variant="secondary" disabled={trace.offset === 0 || busy} onClick={() => run(() => getDocumentRows(changeCaseId, d.id, Math.max(0, trace.offset - trace.limit)), setTrace)}>Previous</Button>
                          <Button size="sm" variant="secondary" disabled={trace.offset + trace.limit >= trace.total || busy} onClick={() => run(() => getDocumentRows(changeCaseId, d.id, trace.offset + trace.limit), setTrace)}>Next</Button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <Card padding="lg">
        <CardHeader><CardTitle>Evidence conflicts</CardTitle></CardHeader>
        <p className="pw-lead" style={{ marginBottom: 12 }}>
          Conflicts are reported with their sources. The software never chooses a value. A person records the decision and the reason.
        </p>
        {data?.permissions.writable && (
          <div className="pw-actions" style={{ marginBottom: 12 }}>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => scanConflicts(changeCaseId), (d) => { setData(d); onChanged?.(); })}>Check for conflicts</Button>
          </div>
        )}
        {data && unresolved.length === 0 && <p className="pw-muted">No open conflicts.</p>}
        <ul className="pw-list">{unresolved.map(conflictCard)}</ul>
        {resolved.length > 0 && (
          <>
            <button type="button" className="stage-toggle" aria-expanded={showResolved} onClick={() => setShowResolved((v) => !v)}>
              {showResolved ? "Hide closed conflicts" : `Show closed conflicts (${resolved.length})`}
            </button>
            {showResolved && <ul className="pw-list">{resolved.map(conflictCard)}</ul>}
          </>
        )}
      </Card>
    </div>
  );
}
