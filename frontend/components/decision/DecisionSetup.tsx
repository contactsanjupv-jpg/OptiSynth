"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import {
  clearCandidateValue, createAttribute, createConstraint, createObjective, setCandidateValue, updateAttribute, updateConstraint, updateObjective,
  type Workspace,
} from "@/lib/api/decision";
import "@/styles/components/decision.css";

type Props = { programId: number; workspace: Workspace; onChanged: () => void };

function useAction(onChanged: () => void) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true); setError(null);
    try { await fn(); onChanged(); return true; } catch (e) { setError(e instanceof Error ? e.message : "That did not work. Try again."); return false; } finally { setBusy(false); }
  }
  return { error, busy, run };
}

const show = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v));

export function ConstraintsSection({ programId, workspace, onChanged }: Props) {
  const { error, busy, run } = useAction(onChanged);
  const canManage = workspace.permissions.can_manage;
  const [form, setForm] = useState({ name: "", kind: "hard", category: "property_threshold", ref: "", operator: "", value: "", value2: "", rationale: "" });
  const ref = workspace.references.find((r) => r.ref === form.ref);
  const operators = ref ? workspace.operators[ref.value_type].filter((o) => !(ref.origin === "prediction" && o.value === "==")) : [];
  const isList = ref && (ref.value_type === "text_list" || (ref.value_type === "text" && (form.operator === "in" || form.operator === "not_in")));

  function payloadValue(): unknown {
    if (!ref) return "";
    if (ref.value_type === "number") return form.operator === "between" ? [Number(form.value), Number(form.value2)] : Number(form.value);
    if (ref.value_type === "boolean") return form.value === "true";
    if (isList) return form.value.split(",").map((s) => s.trim()).filter(Boolean);
    return form.value;
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await run(() => createConstraint(programId, { name: form.name, kind: form.kind, category: form.category, ref: form.ref, operator: form.operator, value: payloadValue(), rationale: form.rationale }));
    if (ok) setForm({ ...form, name: "", value: "", value2: "", rationale: "" });
  }

  return (
    <div className="dc-section">
      <h3>Constraints</h3>
      <p className="dc-note">A hard constraint must be met. A candidate that misses one is rejected, and one that cannot be checked is not confirmed. A soft constraint is a preference. Missing it is shown as a trade off and never rejects a candidate.</p>
      {error && <ErrorCallout message={error} />}
      {workspace.constraints.length === 0 ? <p className="dc-note">No constraints yet.</p> : (
        <div className="dc-scroll"><table className="dc-table">
          <caption className="pw-sr-only">Constraints</caption>
          <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Rule</th><th scope="col">Why</th><th scope="col"><span className="pw-sr-only">Actions</span></th></tr></thead>
          <tbody>{workspace.constraints.map((c) => (
            <tr key={c.id}>
              <td>{c.name} {c.status === "archived" && <Badge tone="neutral">Archived</Badge>}</td>
              <td>{c.kind === "hard" ? "Hard" : "Soft"}, {workspace.constraint_categories.find((x) => x.value === c.category)?.label ?? c.category}</td>
              <td>{`${c.ref_label} ${c.operator_label} ${show(c.value)}${c.unit ? ` ${c.unit}` : ""}`}{!c.ref_available && <div className="pw-muted">Its quantity is no longer available</div>}</td>
              <td>{c.rationale}</td>
              <td>{canManage && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => updateConstraint(programId, c.id, { status: c.status === "active" ? "archived" : "active" }))}>{c.status === "active" ? "Archive" : "Restore"}</Button>}</td>
            </tr>))}</tbody></table></div>
      )}
      {canManage && (
        <form className="dc-form" onSubmit={submit} aria-label="Add a constraint">
          <label>Name<input className="pw-field" required maxLength={120} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Hard or soft
            <select className="pw-field" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="hard">Hard</option><option value="soft">Soft</option></select></label>
          <label>Category
            <select className="pw-field" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>{workspace.constraint_categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
          <label>Quantity
            <select className="pw-field" required value={form.ref} onChange={(e) => setForm({ ...form, ref: e.target.value, operator: "", value: "", value2: "" })}>
              <option value="">Choose</option>{workspace.references.map((r) => <option key={r.ref} value={r.ref}>{`${r.label}${r.unit ? ` (${r.unit})` : ""}`}</option>)}</select></label>
          <label>Operator
            <select className="pw-field" required value={form.operator} disabled={!ref} onChange={(e) => setForm({ ...form, operator: e.target.value })}>
              <option value="">Choose</option>{operators.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          {ref?.value_type === "boolean" ? (
            <label>Value<select className="pw-field" required value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })}><option value="">Choose</option><option value="true">Yes</option><option value="false">No</option></select></label>
          ) : (
            <label>{isList ? "Values, separated by commas" : form.operator === "between" ? "Lower limit" : "Value"}
              <input className="pw-field" required disabled={!ref} type={ref?.value_type === "number" ? "number" : "text"} step="any" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></label>
          )}
          {form.operator === "between" && <label>Upper limit<input className="pw-field" required type="number" step="any" value={form.value2} onChange={(e) => setForm({ ...form, value2: e.target.value })} /></label>}
          {ref?.unit && <p className="pw-muted">{`Unit: ${ref.unit}. Values are never converted.`}</p>}
          <label className="dc-wide">Why this constraint exists<input className="pw-field" required minLength={3} maxLength={400} value={form.rationale} onChange={(e) => setForm({ ...form, rationale: e.target.value })} /></label>
          <div className="dc-wide"><Button type="submit" variant="primary" disabled={busy}>{busy ? "Saving" : "Add constraint"}</Button></div>
        </form>
      )}
      {!canManage && <p className="pw-muted">Only an owner, an admin or the program owner can change constraints.</p>}
    </div>
  );
}

export function ObjectivesSection({ programId, workspace, onChanged }: Props) {
  const { error, busy, run } = useAction(onChanged);
  const canManage = workspace.permissions.can_manage;
  const [form, setForm] = useState({ name: "", ref: "", direction: "maximize" });
  const numeric = workspace.references.filter((r) => r.value_type === "number");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await run(() => createObjective(programId, form))) setForm({ ...form, name: "", ref: "" });
  }
  return (
    <div className="dc-section">
      <h3>Objectives</h3>
      <p className="dc-note">Objectives are competing goals, such as higher performance and lower cost. Candidates are compared on all of them at once. They are never added together and no weights are applied.</p>
      {error && <ErrorCallout message={error} />}
      {workspace.objectives.length === 0 ? <p className="dc-note">No objectives yet. Without objectives candidates are grouped by constraints only.</p> : (
        <div className="dc-scroll"><table className="dc-table">
          <caption className="pw-sr-only">Objectives</caption>
          <thead><tr><th scope="col">Name</th><th scope="col">Quantity</th><th scope="col">Goal</th><th scope="col"><span className="pw-sr-only">Actions</span></th></tr></thead>
          <tbody>{workspace.objectives.map((o) => (
            <tr key={o.id}><td>{o.name} {o.status === "archived" && <Badge tone="neutral">Archived</Badge>}</td><td>{o.ref_label}{o.unit ? ` (${o.unit})` : ""}</td><td>{o.direction === "maximize" ? "Maximize" : "Minimize"}</td>
              <td>{canManage && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => updateObjective(programId, o.id, { status: o.status === "active" ? "archived" : "active" }))}>{o.status === "active" ? "Archive" : "Restore"}</Button>}</td></tr>))}</tbody></table></div>
      )}
      {canManage && (
        <form className="dc-form" onSubmit={submit} aria-label="Add an objective">
          <label>Name<input className="pw-field" required maxLength={120} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Quantity<select className="pw-field" required value={form.ref} onChange={(e) => setForm({ ...form, ref: e.target.value })}><option value="">Choose</option>{numeric.map((r) => <option key={r.ref} value={r.ref}>{`${r.label}${r.unit ? ` (${r.unit})` : ""}`}</option>)}</select></label>
          <label>Goal<select className="pw-field" value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}><option value="maximize">Maximize</option><option value="minimize">Minimize</option></select></label>
          <div className="dc-wide"><Button type="submit" variant="primary" disabled={busy}>{busy ? "Saving" : "Add objective"}</Button></div>
        </form>
      )}
      {!canManage && <p className="pw-muted">Only an owner, an admin or the program owner can change objectives.</p>}
    </div>
  );
}

export function CandidateDataSection({ programId, workspace, onChanged }: Props) {
  const { error, busy, run } = useAction(onChanged);
  const canManage = workspace.permissions.can_manage;
  const active = workspace.attributes.filter((a) => a.status === "active");
  const [editing, setEditing] = useState<{ candidateId: number; attributeId: number } | null>(null);
  const [draft, setDraft] = useState({ value: "", source: "" });
  const [attr, setAttr] = useState({ key: "", label: "", value_type: "number", unit: "", category: "cost" });

  function startEdit(candidateId: number, attributeId: number, current: unknown) {
    setEditing({ candidateId, attributeId });
    setDraft({ value: current === undefined || current === null ? "" : Array.isArray(current) ? current.join(", ") : String(current), source: "" });
  }
  async function save() {
    if (!editing) return;
    const a = active.find((x) => x.id === editing.attributeId)!;
    const value = a.value_type === "number" ? Number(draft.value) : a.value_type === "boolean" ? draft.value === "true" : a.value_type === "text_list" ? draft.value.split(",").map((s) => s.trim()).filter(Boolean) : draft.value;
    if (await run(() => setCandidateValue(programId, editing.candidateId, editing.attributeId, { value, source_note: draft.source }))) setEditing(null);
  }
  async function addAttribute(e: React.FormEvent) {
    e.preventDefault();
    if (await run(() => createAttribute(programId, { key: attr.key, label: attr.label, value_type: attr.value_type, unit: attr.unit || null, category: attr.category }))) setAttr({ ...attr, key: "", label: "", unit: "" });
  }

  return (
    <div className="dc-section">
      <h3>Candidate data</h3>
      <p className="dc-note">{workspace.no_invented_data} Each value needs the source you got it from.</p>
      {error && <ErrorCallout message={error} />}
      {workspace.attributes.length > 0 && (
        <div className="dc-scroll dc-section"><table className="dc-table">
          <caption className="pw-sr-only">Tracked attributes</caption>
          <thead><tr><th scope="col">Attribute</th><th scope="col">Type</th><th scope="col">Unit</th><th scope="col" className="num">Values entered</th><th scope="col"><span className="pw-sr-only">Actions</span></th></tr></thead>
          <tbody>{workspace.attributes.map((a) => (
            <tr key={a.id}><td>{a.label} {a.status === "archived" && <Badge tone="neutral">Archived</Badge>}<div className="pw-muted">{workspace.attribute_categories.find((c) => c.value === a.category)?.label}</div></td>
              <td>{a.value_type.replace("_", " ")}</td><td>{a.unit ?? "None"}</td><td className="num">{a.values_recorded}</td>
              <td>{canManage && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => updateAttribute(programId, a.id, { status: a.status === "active" ? "archived" : "active" }))}>{a.status === "active" ? "Archive" : "Restore"}</Button>}</td></tr>))}</tbody></table></div>
      )}
      {canManage && (
        <form className="dc-form" onSubmit={addAttribute} aria-label="Track a new attribute">
          <label>Key<input className="pw-field" required pattern="[a-z][a-z0-9_]{1,39}" title="Lower case letters, digits and underscores" value={attr.key} onChange={(e) => setAttr({ ...attr, key: e.target.value })} /></label>
          <label>Label<input className="pw-field" required maxLength={80} value={attr.label} onChange={(e) => setAttr({ ...attr, label: e.target.value })} /></label>
          <label>Type<select className="pw-field" value={attr.value_type} onChange={(e) => setAttr({ ...attr, value_type: e.target.value })}><option value="number">Number</option><option value="text">Text</option><option value="boolean">Yes or no</option><option value="text_list">List</option></select></label>
          <label>Unit<input className="pw-field" disabled={attr.value_type !== "number"} maxLength={20} value={attr.unit} onChange={(e) => setAttr({ ...attr, unit: e.target.value })} /></label>
          <label>Category<select className="pw-field" value={attr.category} onChange={(e) => setAttr({ ...attr, category: e.target.value })}>{workspace.attribute_categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
          <div className="dc-wide"><Button type="submit" variant="primary" disabled={busy}>{busy ? "Saving" : "Track attribute"}</Button></div>
        </form>
      )}
      {active.length > 0 && workspace.candidate_values.length > 0 && (
        <div className="dc-scroll" style={{ marginTop: 16 }}><table className="dc-table">
          <caption className="pw-sr-only">Values entered for each candidate</caption>
          <thead><tr><th scope="col">Candidate</th>{active.map((a) => <th key={a.id} scope="col">{a.label}{a.unit ? ` (${a.unit})` : ""}</th>)}</tr></thead>
          <tbody>{workspace.candidate_values.map((c) => (
            <tr key={c.candidate_id}><th scope="row">{c.name}</th>
              {active.map((a) => {
                const cell = c.values[String(a.id)];
                const open = editing?.candidateId === c.candidate_id && editing.attributeId === a.id;
                return (
                  <td key={a.id}>
                    {open ? (
                      <div className="dc-form" style={{ margin: 0 }}>
                        {a.value_type === "boolean" ? (
                          <select className="pw-field" aria-label={`${a.label} for ${c.name}`} value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })}><option value="">Choose</option><option value="true">Yes</option><option value="false">No</option></select>
                        ) : (
                          <input className="pw-field" aria-label={`${a.label} for ${c.name}`} type={a.value_type === "number" ? "number" : "text"} step="any" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
                        )}
                        <input className="pw-field" aria-label={`Source for ${a.label} of ${c.name}`} placeholder="Source" minLength={3} maxLength={200} value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })} />
                        <div className="pw-actions"><Button size="sm" variant="primary" disabled={busy} onClick={save}>Save</Button><Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button></div>
                      </div>
                    ) : (
                      <>
                        {cell ? <>{show(cell.value)}<div className="pw-muted">{cell.source_note}</div></> : <span className="dc-unknown">Unknown</span>}
                        <div className="pw-actions">
                          <Button size="sm" variant="secondary" onClick={() => startEdit(c.candidate_id, a.id, cell?.value)}>{cell ? "Change" : "Enter"}</Button>
                          {cell && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => clearCandidateValue(programId, c.candidate_id, a.id))}>Clear</Button>}
                        </div>
                      </>
                    )}
                  </td>
                );
              })}</tr>))}</tbody></table></div>
      )}
      {active.length === 0 && <p className="dc-note">No attributes yet. Track what matters for the decision, such as cost, supplier or process facts, then enter the values for each candidate.</p>}
    </div>
  );
}
