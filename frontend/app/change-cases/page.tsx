"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import {
  listChangeCases, createChangeCase, updateChangeCase, updateChangeCaseStatus,
  archiveChangeCase, restoreChangeCase, deleteChangeCase, parseSpec,
} from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import { formatRelativeTime } from "@/lib/utils/format";
import type { ChangeCase, ChangeCaseUpdate, TriggerType } from "@/lib/api/change-cases";

const TRIGGER_LABELS: Record<TriggerType, string> = {
  regulatory_restriction: "Regulatory restriction",
  supplier_loss: "Supplier loss",
  component_obsolescence: "Component obsolescence",
  other: "Other",
};

// What each badge means. These are the only meanings the product gives the statuses, and they match
// the server (backend change_case_lifecycle_rules.py). "Active" is never set automatically: a team
// member starts the diagnostic with the "Start diagnostic" action below.
const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  active: "Active",
  completed: "Completed",
};
const STATUS_TONE: Record<string, "neutral" | "success" | "warning"> = {
  draft: "neutral",
  active: "warning",
  completed: "success",
};
const STATUS_MEANING: Record<string, string> = {
  draft: "Setup in progress. The diagnostic has not been started.",
  active: "In progress. A team member started the diagnostic. This is workflow progress, not a qualification result.",
  completed: "Closed record. It cannot be reopened. Recorded physical outcomes are retained.",
};

type DialogState =
  | { kind: "edit" | "delete" | "archive" | "start" | "complete"; c: ChangeCase }
  | null;

const FIELD_STYLE: React.CSSProperties = {
  display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4,
};
const LABEL_STYLE: React.CSSProperties = { fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" };

export default function ChangeCasesPage() {
  const { checking } = useRequireAuth();
  const [cases, setCases] = useState<ChangeCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [name, setName] = useState("");
  const [triggerType, setTriggerType] = useState<TriggerType>("regulatory_restriction");
  const [restrictedSubstance, setRestrictedSubstance] = useState("");
  // Canonical fields: the names/units the analysis is defined in. The customer's own
  // file headers are mapped onto these in the evidence review -- they do NOT need to match.
  const [features, setFeatures] = useState<{ name: string; unit: string }[]>([{ name: "", unit: "" }]);
  const [targetMetric, setTargetMetric] = useState("");
  const [targetUnit, setTargetUnit] = useState("");
  const [targetValue, setTargetValue] = useState("");
  const [direction, setDirection] = useState<"maximize" | "minimize">("maximize");
  const [creating, setCreating] = useState(false);

  const router = useRouter();
  const [showArchived, setShowArchived] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // Edit form (filled from the saved record each time the edit dialog opens)
  const [editName, setEditName] = useState("");
  const [editTrigger, setEditTrigger] = useState<TriggerType>("regulatory_restriction");
  const [editSubstance, setEditSubstance] = useState("");
  const [editTarget, setEditTarget] = useState("");
  const [editDirection, setEditDirection] = useState<"maximize" | "minimize">("maximize");

  function load(includeArchived = showArchived) {
    listChangeCases(includeArchived)
      .then((rows) => { setCases(rows); setError(null); })
      .catch(() => setError("Could not load change cases."));
  }

  useEffect(() => {
    if (!checking) load(showArchived);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, showArchived]);

  if (checking) return null;

  function openDialog(kind: NonNullable<DialogState>["kind"], c: ChangeCase) {
    setDialogError(null);
    if (kind === "edit") {
      const spec = parseSpec(c);
      setEditName(c.name);
      setEditTrigger(c.trigger_type);
      setEditSubstance(c.restricted_substance ?? "");
      setEditTarget(spec ? String(spec.target_value) : "");
      setEditDirection(spec?.direction ?? "maximize");
    }
    setDialog({ kind, c });
  }

  function closeDialog() {
    if (busyId !== null) return;
    setDialog(null);
    setDialogError(null);
  }

  /** Runs one action on one diagnostic. The row's buttons and the dialog stay disabled while it runs,
   *  so a double click can't submit twice; a failure is shown in the dialog with the user's input kept. */
  async function run(c: ChangeCase, action: () => Promise<unknown>, success: string) {
    if (busyId !== null) return;
    setBusyId(c.id);
    setDialogError(null);
    try {
      await action();
      setDialog(null);
      setNotice(success);
      setError(null);
      load();
    } catch (err) {
      const message = err instanceof ApiRequestError ? err.message : "That didn't work. Please try again.";
      if (dialog) setDialogError(message); else setError(message);
    } finally {
      setBusyId(null);
    }
  }

  function saveEdit(c: ChangeCase) {
    const spec = parseSpec(c);
    const name = editName.trim();
    if (!name) { setDialogError("A diagnostic needs a name."); return; }
    const changes: ChangeCaseUpdate = {};
    if (name !== c.name) changes.name = name;
    if (editTrigger !== c.trigger_type) changes.trigger_type = editTrigger;
    if (editSubstance.trim() !== (c.restricted_substance ?? "")) changes.restricted_substance = editSubstance.trim() || null;
    if (c.requirement_editable && spec) {
      const target = Number(editTarget);
      if (editTarget.trim() === "" || !Number.isFinite(target)) { setDialogError("The required value must be a number."); return; }
      if (target !== spec.target_value) changes.target_value = target;
      if (editDirection !== spec.direction) changes.direction = editDirection;
    }
    if (Object.keys(changes).length === 0) { setDialogError("Nothing has changed."); return; }
    run(c, () => updateChangeCase(c.id, changes), `Saved changes to “${name}”.`);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const NAME_RE = /^[a-z][a-z0-9_]*$/;
      const rows = features.map((f) => ({ name: f.name.trim(), unit: f.unit.trim() }));
      const bad = [...rows.map((r) => r.name), targetMetric.trim()].find((n) => !NAME_RE.test(n));
      if (bad !== undefined) {
        setError(`Field name "${bad}" is not valid. Use lower-case letters, digits and underscores, starting with a letter (e.g. cure_temp_c).`);
        return;
      }
      const names = [...rows.map((r) => r.name), targetMetric.trim()];
      if (new Set(names).size !== names.length) {
        setError("Each input feature and the target need a different name.");
        return;
      }
      const units: Record<string, string> = {};
      rows.forEach((r) => { if (r.unit) units[r.name] = r.unit; });
      if (targetUnit.trim()) units[targetMetric.trim()] = targetUnit.trim();
      await createChangeCase(name, triggerType, restrictedSubstance || null, {
        feature_columns: rows.map((r) => r.name),
        target_metric: targetMetric.trim(),
        target_value: parseFloat(targetValue),
        direction,
        ...(Object.keys(units).length ? { units } : {}),
      });
      setName(""); setRestrictedSubstance(""); setFeatures([{ name: "", unit: "" }]);
      setTargetMetric(""); setTargetUnit(""); setTargetValue("");
      setShowForm(false);
      load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not create change case.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Diagnostics" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title="Qualification diagnostics"
          subtitle="Forced-substitution qualification diagnostics -- when a material or supplier change forces a requalification decision."
        />

        {error && <ErrorCallout message={error} />}

        <div style={{ marginBottom: 16 }}>
          <Button variant="primary" onClick={() => setShowForm((s) => !s)}>
            {showForm ? "Cancel" : "New diagnostic"}
          </Button>
        </div>

        {showForm && (
          <Card padding="lg" style={{ marginBottom: 20 }}>
            <CardHeader><CardTitle>New diagnostic</CardTitle></CardHeader>
            <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 480 }}>
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Name</label>
                <input required value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. AquaShield 400 -- PFAS Fluorosurfactant Replacement"
                  style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
              </div>
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Trigger type</label>
                <select value={triggerType} onChange={(e) => setTriggerType(e.target.value as TriggerType)}
                  style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }}>
                  {Object.entries(TRIGGER_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Restricted substance (optional)</label>
                <input value={restrictedSubstance} onChange={(e) => setRestrictedSubstance(e.target.value)}
                  placeholder="e.g. PFAS-based fluorosurfactant leveling agent"
                  style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
              </div>
              <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: 0 }}>
                One diagnostic covers ONE qualification requirement. Define the fields the analysis uses below;
                your own spreadsheet or report headers are matched to these in the evidence review, so they do
                not have to be identical. Give every field a unit (leave it blank only for dimensionless values) --
                values are converted only where an exact conversion exists, and are never guessed.
              </p>
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
                  Input features (what you control or specify for a candidate)
                </label>
                {features.map((f, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, marginTop: 6 }}>
                    <input required value={f.name} aria-label={`Feature ${i + 1} name`}
                      onChange={(e) => setFeatures((fs) => fs.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))}
                      placeholder="e.g. cure_temp_c"
                      style={{ flex: 2, padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)" }} />
                    <input value={f.unit} aria-label={`Feature ${i + 1} unit`}
                      onChange={(e) => setFeatures((fs) => fs.map((x, k) => (k === i ? { ...x, unit: e.target.value } : x)))}
                      placeholder="unit, e.g. degC"
                      style={{ flex: 1, padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)" }} />
                    {features.length > 1 && (
                      <Button type="button" size="sm" variant="ghost" onClick={() => setFeatures((fs) => fs.filter((_, k) => k !== i))}>
                        Remove
                      </Button>
                    )}
                  </div>
                ))}
                {features.length < 10 && (
                  <div style={{ marginTop: 6 }}>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setFeatures((fs) => [...fs, { name: "", unit: "" }])}>
                      Add another input feature
                    </Button>
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <div style={{ flex: 2 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Requirement being tested (the target)</label>
                  <input required value={targetMetric} onChange={(e) => setTargetMetric(e.target.value)}
                    placeholder="e.g. salt_spray_hours"
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Target unit</label>
                  <input value={targetUnit} onChange={(e) => setTargetUnit(e.target.value)}
                    placeholder="e.g. h"
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <div style={{ width: 160 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Required value</label>
                  <input required type="number" step="any" value={targetValue} onChange={(e) => setTargetValue(e.target.value)}
                    placeholder="500"
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
                </div>
                <div style={{ width: 160 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Direction</label>
                  <select value={direction} onChange={(e) => setDirection(e.target.value as "maximize" | "minimize")}
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }}>
                    <option value="maximize">At or above</option>
                    <option value="minimize">At or below</option>
                  </select>
                </div>
              </div>
              <Button type="submit" variant="primary" loading={creating}>Create diagnostic</Button>
            </form>
          </Card>
        )}

        <Card padding="lg">
          <CardHeader><CardTitle>All diagnostics</CardTitle></CardHeader>
          {notice && (
            <div role="status" style={{ marginBottom: 12, padding: "8px 12px", borderRadius: "var(--radius-sm)", fontSize: "var(--text-sm)",
              background: "var(--color-success-subtle)", border: "1px solid var(--color-success-border)", color: "var(--color-success)",
              display: "flex", justifyContent: "space-between", gap: 12 }}>
              <span>{notice}</span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss"
                style={{ background: "none", border: "none", cursor: "pointer", color: "inherit" }}>×</button>
            </div>
          )}
          <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: "0 0 12px" }}>
            <strong>Draft</strong> = setup in progress. <strong>Active</strong> = a team member started the diagnostic
            (workflow progress, not a qualification result). <strong>Completed</strong> = closed record.
            <strong> Archived</strong> = hidden from this list and read-only; everything is kept.
          </p>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)", marginBottom: 8 }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
          {!cases && <Spinner label="Loading change cases…" />}
          {cases && cases.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
              {showArchived
                ? "No diagnostics yet."
                : "No diagnostics yet. Create one when a material or supplier change forces a requalification decision."}
            </p>
          )}
          {cases && cases.map((c) => {
            const archived = Boolean(c.archived_at);
            const busy = busyId === c.id;
            return (
              <div key={c.id} style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap",
                padding: "12px 0", borderBottom: "1px solid var(--color-border)", opacity: archived ? 0.8 : 1 }}>
                <div style={{ minWidth: 220, flex: "1 1 260px" }}>
                  <Link href={`/change-cases/${c.id}`} style={{ fontSize: "var(--text-sm)", fontWeight: 500, color: "inherit" }}>{c.name}</Link>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {TRIGGER_LABELS[c.trigger_type]}
                    {c.restricted_substance && ` · ${c.restricted_substance}`}
                    {" · created "}{formatRelativeTime(c.created_at)}
                  </div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {c.dataset_count ?? 0} evidence upload{c.dataset_count === 1 ? "" : "s"} · {c.candidate_count ?? 0} candidate{c.candidate_count === 1 ? "" : "s"}
                    {(c.prediction_count ?? 0) > 0 && " · ranked"}
                    {(c.outcome_count ?? 0) > 0 && ` · ${c.outcome_count} physical outcome${c.outcome_count === 1 ? "" : "s"} recorded`}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
                  <div style={{ display: "flex", gap: 6 }} title={archived ? "Archived: hidden from the default list and read-only." : STATUS_MEANING[c.status]}>
                    {archived && <Badge tone="neutral">Archived</Badge>}
                    <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{STATUS_LABEL[c.status] ?? c.status}</Badge>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }} role="group" aria-label={`Actions for ${c.name}`}>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => router.push(`/change-cases/${c.id}`)}>Open</Button>
                    {!archived && c.status !== "completed" && (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("edit", c)}>Edit</Button>
                    )}
                    {!archived && c.status === "draft" && (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("start", c)}>Start diagnostic</Button>
                    )}
                    {!archived && c.status === "active" && (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("complete", c)}>Mark completed</Button>
                    )}
                    {archived ? (
                      <Button size="sm" variant="secondary" disabled={busy}
                        onClick={() => run(c, () => restoreChangeCase(c.id), `Restored “${c.name}”.`)}>Restore</Button>
                    ) : (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => openDialog("archive", c)}>Archive</Button>
                    )}
                    <Button size="sm" variant="danger" disabled={busy} onClick={() => openDialog("delete", c)}>Delete</Button>
                  </div>
                </div>
              </div>
            );
          })}
        </Card>

        {dialog?.kind === "edit" && (() => {
          const c = dialog.c;
          const spec = parseSpec(c);
          const unit = spec?.units?.[spec.target_metric];
          return (
            <Dialog title="Edit diagnostic" onClose={closeDialog} busy={busyId !== null}
              actions={<>
                <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Cancel</Button>
                <Button variant="primary" loading={busyId === c.id} onClick={() => saveEdit(c)}>Save changes</Button>
              </>}>
              {dialogError && <ErrorCallout message={dialogError} />}
              <div>
                <label style={LABEL_STYLE} htmlFor="edit-name">Name</label>
                <input id="edit-name" value={editName} onChange={(e) => setEditName(e.target.value)} style={FIELD_STYLE} />
              </div>
              <div>
                <label style={LABEL_STYLE} htmlFor="edit-trigger">Change reason</label>
                <select id="edit-trigger" value={editTrigger} onChange={(e) => setEditTrigger(e.target.value as TriggerType)} style={FIELD_STYLE}>
                  {Object.entries(TRIGGER_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              <div>
                <label style={LABEL_STYLE} htmlFor="edit-substance">Restricted substance (optional)</label>
                <input id="edit-substance" value={editSubstance} onChange={(e) => setEditSubstance(e.target.value)} style={FIELD_STYLE} />
              </div>
              {spec && (
                <div>
                  <div style={LABEL_STYLE}>
                    Requirement: {spec.target_metric}{unit ? ` (${unit})` : ""}
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <div style={{ flex: 1 }}>
                      <label style={LABEL_STYLE} htmlFor="edit-target">Required value</label>
                      <input id="edit-target" type="number" step="any" value={editTarget} disabled={!c.requirement_editable}
                        onChange={(e) => setEditTarget(e.target.value)} style={FIELD_STYLE} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={LABEL_STYLE} htmlFor="edit-direction">Direction</label>
                      <select id="edit-direction" value={editDirection} disabled={!c.requirement_editable}
                        onChange={(e) => setEditDirection(e.target.value as "maximize" | "minimize")} style={FIELD_STYLE}>
                        <option value="maximize">At or above</option>
                        <option value="minimize">At or below</option>
                      </select>
                    </div>
                  </div>
                  {!c.requirement_editable && (
                    <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: "6px 0 0" }}>
                      The requirement is locked. {c.requirement_locked_reason}
                    </p>
                  )}
                  <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", margin: "6px 0 0" }}>
                    The input fields, target field and units define how evidence is read and can&apos;t be edited; create a new diagnostic to change them.
                  </p>
                </div>
              )}
            </Dialog>
          );
        })()}

        {dialog?.kind === "start" && (
          <Dialog title="Start this diagnostic?" onClose={closeDialog} busy={busyId !== null}
            actions={<>
              <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Cancel</Button>
              <Button variant="primary" loading={busyId === dialog.c.id}
                onClick={() => run(dialog.c, () => updateChangeCaseStatus(dialog.c.id, "active"), `“${dialog.c.name}” is now Active.`)}>
                Start diagnostic
              </Button>
            </>}>
            {dialogError && <ErrorCallout message={dialogError} />}
            <p style={{ margin: 0 }}>
              This marks <strong>{dialog.c.name}</strong> as Active: in progress. It is workflow progress only. It does not mean any
              candidate has passed physical testing, and nothing is ranked or changed by starting it.
            </p>
          </Dialog>
        )}

        {dialog?.kind === "complete" && (
          <Dialog title="Mark this diagnostic completed?" onClose={closeDialog} busy={busyId !== null}
            actions={<>
              <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Cancel</Button>
              <Button variant="primary" loading={busyId === dialog.c.id}
                onClick={() => run(dialog.c, () => updateChangeCaseStatus(dialog.c.id, "completed"), `“${dialog.c.name}” is now Completed.`)}>
                Mark completed
              </Button>
            </>}>
            {dialogError && <ErrorCallout message={dialogError} />}
            <p style={{ margin: 0 }}>
              <strong>{dialog.c.name}</strong> becomes a closed record. <strong>This cannot be undone:</strong> a completed diagnostic
              can&apos;t be reopened, edited, or given new candidates. Recorded physical outcomes are kept. Completing it is a record
              of your team&apos;s decision, not a qualification result.
            </p>
          </Dialog>
        )}

        {dialog?.kind === "archive" && (
          <Dialog title="Archive this diagnostic?" onClose={closeDialog} busy={busyId !== null}
            actions={<>
              <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Cancel</Button>
              <Button variant="primary" loading={busyId === dialog.c.id}
                onClick={() => run(dialog.c, () => archiveChangeCase(dialog.c.id), `Archived “${dialog.c.name}”. Use “Show archived” to find it.`)}>
                Archive
              </Button>
            </>}>
            {dialogError && <ErrorCallout message={dialogError} />}
            <p style={{ margin: 0 }}>
              <strong>{dialog.c.name}</strong> will be hidden from this list and become read-only. Nothing is deleted: its evidence,
              candidates, rankings, outcomes and reports are all kept, and you can restore it at any time.
            </p>
          </Dialog>
        )}

        {dialog?.kind === "delete" && (
          <Dialog title="Delete this diagnostic?" onClose={closeDialog} busy={busyId !== null}
            actions={dialog.c.deletable ? (
              <>
                <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Cancel</Button>
                <Button variant="danger" loading={busyId === dialog.c.id}
                  onClick={() => run(dialog.c, () => deleteChangeCase(dialog.c.id), `Deleted “${dialog.c.name}”.`)}>
                  Delete permanently
                </Button>
              </>
            ) : (
              <>
                <Button variant="secondary" onClick={closeDialog} disabled={busyId !== null}>Close</Button>
                {!dialog.c.archived_at && (
                  <Button variant="primary" onClick={() => openDialog("archive", dialog.c)}>Archive instead</Button>
                )}
              </>
            )}>
            {dialogError && <ErrorCallout message={dialogError} />}
            {dialog.c.deletable ? (
              <>
                <p style={{ margin: 0 }}>
                  This permanently deletes <strong>{dialog.c.name}</strong>, its {dialog.c.dataset_count ?? 0} uploaded evidence file
                  {dialog.c.dataset_count === 1 ? "" : "s"} and its {dialog.c.candidate_count ?? 0} candidate
                  {dialog.c.candidate_count === 1 ? "" : "s"}. <strong>This cannot be undone.</strong>
                </p>
                <p style={{ margin: 0 }}>Nothing has been ranked and no physical outcome has been recorded for it. Only owners and admins can delete.</p>
              </>
            ) : (
              <>
                <p style={{ margin: 0 }}><strong>{dialog.c.name}</strong> can&apos;t be deleted.</p>
                <p style={{ margin: 0 }}>{dialog.c.delete_blocked_reason}</p>
              </>
            )}
          </Dialog>
        )}
      </div>
    </AppShell>
  );
}