"use client";

import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { StagePanel } from "@/components/change-cases/StagePanel";
import {
  addMilestone, addReviewer, addTask, completeMilestone, completeTask, deleteMilestone, deleteTask, removeReviewer, setOwner,
  updateDetails, type Workspace,
} from "@/lib/api/workspace";
import { formatDate, messageOf } from "./shared";
import "@/styles/components/program-workspace.css";

const FIELDS: { key: "material_replaced" | "reason_for_substitution" | "business_impact" | "original_formulation" | "manufacturing_constraints"; label: string; rows: number }[] = [
  { key: "material_replaced", label: "Material being replaced", rows: 1 },
  { key: "reason_for_substitution", label: "Reason for substitution", rows: 2 },
  { key: "business_impact", label: "Business impact", rows: 2 },
  { key: "original_formulation", label: "Original formulation or material", rows: 3 },
  { key: "manufacturing_constraints", label: "Manufacturing constraints", rows: 2 },
];

interface Props { changeCaseId: number; ws: Workspace; onChange: (ws: Workspace) => void; onStageChanged: () => void }

export function OverviewTab({ changeCaseId, ws, onChange, onStageChanged }: Props) {
  const p = ws.program;
  const [draft, setDraft] = useState<Record<string, string>>(() => ({
    ...Object.fromEntries(FIELDS.map((f) => [f.key, p[f.key] ?? ""])), target_date: p.target_date ?? "",
  }));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [milestoneTitle, setMilestoneTitle] = useState("");
  const [milestoneDate, setMilestoneDate] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskAssignee, setTaskAssignee] = useState("");
  const [taskDate, setTaskDate] = useState("");

  const manage = ws.permissions.can_manage;
  const write = ws.permissions.can_write;
  const dirty = FIELDS.some((f) => draft[f.key] !== (p[f.key] ?? "")) || draft.target_date !== (p.target_date ?? "");

  async function run(action: () => Promise<Workspace>, after?: () => void) {
    setError(null); setSaved(false); setBusy(true);
    try { onChange(await action()); after?.(); } catch (err) { setError(messageOf(err)); } finally { setBusy(false); }
  }

  const reviewerIds = new Set(ws.reviewers.map((r) => r.id));
  const available = ws.people.filter((u) => !reviewerIds.has(u.id));

  return (
    <div className="pw-stack">
      {error && <ErrorCallout message={error} />}
      {ws.completion.message && <p className="pw-notice" role="status">{ws.completion.message}</p>}
      <StagePanel changeCaseId={changeCaseId} onChanged={onStageChanged} />

      <Card padding="lg">
        <CardHeader><CardTitle>Substitution details</CardTitle></CardHeader>
        <form className="pw-form" onSubmit={(e) => {
          e.preventDefault();
          const changes: Record<string, string | null> = {};
          for (const f of FIELDS) if (draft[f.key] !== (p[f.key] ?? "")) changes[f.key] = draft[f.key] || null;
          if (draft.target_date !== (p.target_date ?? "")) changes.target_date = draft.target_date || null;
          run(() => updateDetails(changeCaseId, changes), () => setSaved(true));
        }}>
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label className="pw-label" htmlFor={`pw-${f.key}`}>{f.label}</label>
              <textarea id={`pw-${f.key}`} className="pw-field" rows={f.rows} maxLength={f.key === "material_replaced" ? 300 : 2000}
                value={draft[f.key]} disabled={!manage} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
            </div>
          ))}
          <div className="pw-row">
            <div>
              <label className="pw-label" htmlFor="pw-target-date">Target completion date</label>
              <input id="pw-target-date" type="date" className="pw-field" value={draft.target_date} disabled={!manage}
                onChange={(e) => setDraft({ ...draft, target_date: e.target.value })} />
            </div>
          </div>
          {manage ? (
            <div className="pw-actions"><Button type="submit" variant="primary" size="sm" loading={busy} disabled={!dirty}>Save details</Button></div>
          ) : (
            <p className="pw-muted">Only an owner, an admin or the program owner can change these details.</p>
          )}
          {saved && <p className="pw-ok" role="status">Saved</p>}
        </form>
      </Card>

      <div className="pw-grid">
        <Card padding="lg">
          <CardHeader><CardTitle>Owner and reviewers</CardTitle></CardHeader>
          <div className="pw-form">
            <label className="pw-label" htmlFor="pw-owner">Program owner</label>
            <select id="pw-owner" className="pw-field" disabled={!manage || busy} value={ws.owner?.id ?? ""}
              onChange={(e) => run(() => setOwner(changeCaseId, e.target.value ? Number(e.target.value) : null))}>
              <option value="">No owner</option>
              {ws.people.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <span className="pw-label">Reviewers</span>
            {ws.reviewers.length === 0 ? <p className="pw-muted">No reviewers assigned.</p> : (
              <ul className="pw-list">
                {ws.reviewers.map((r) => (
                  <li key={r.id}>
                    <span>{r.name}</span>
                    {manage && <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => removeReviewer(changeCaseId, r.id as number))}>Remove</Button>}
                  </li>
                ))}
              </ul>
            )}
            {manage && (
              <div className="pw-row">
                <div>
                  <label className="pw-label" htmlFor="pw-reviewer">Add a reviewer</label>
                  <select id="pw-reviewer" className="pw-field" value={reviewer} onChange={(e) => setReviewer(e.target.value)}>
                    <option value="">Choose a person</option>
                    {available.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </div>
                <Button size="sm" variant="secondary" disabled={!reviewer || busy} onClick={() => run(() => addReviewer(changeCaseId, Number(reviewer)), () => setReviewer(""))}>Add</Button>
              </div>
            )}
          </div>
        </Card>

        <Card padding="lg">
          <CardHeader><CardTitle>Milestones</CardTitle></CardHeader>
          {ws.milestones.length === 0 ? <p className="pw-muted">No milestones yet.</p> : (
            <ul className="pw-list">
              {ws.milestones.map((m) => (
                <li key={m.id}>
                  <span>
                    <span className={m.overdue ? "pw-overdue" : ""}>{m.title}</span>
                    <span className="pw-list__meta">
                      {m.completed_at ? `Completed ${formatDate(m.completed_at)}` : `Due ${formatDate(m.due_date)}${m.overdue ? ", overdue" : ""}`}
                    </span>
                  </span>
                  {write && (
                    <span className="pw-actions">
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => completeMilestone(changeCaseId, m.id, !m.completed_at))}>
                        {m.completed_at ? "Reopen" : "Mark complete"}
                      </Button>
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => deleteMilestone(changeCaseId, m.id))}>Remove</Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {write && (
            <form className="pw-row" style={{ marginTop: 12 }} onSubmit={(e) => {
              e.preventDefault();
              run(() => addMilestone(changeCaseId, milestoneTitle, milestoneDate), () => { setMilestoneTitle(""); setMilestoneDate(""); });
            }}>
              <div>
                <label className="pw-label" htmlFor="pw-ms-title">Milestone</label>
                <input id="pw-ms-title" className="pw-field" value={milestoneTitle} maxLength={400} required onChange={(e) => setMilestoneTitle(e.target.value)} />
              </div>
              <div>
                <label className="pw-label" htmlFor="pw-ms-date">Due date</label>
                <input id="pw-ms-date" type="date" className="pw-field" value={milestoneDate} required onChange={(e) => setMilestoneDate(e.target.value)} />
              </div>
              <Button type="submit" size="sm" variant="secondary" disabled={busy}>Add milestone</Button>
            </form>
          )}
        </Card>
      </div>

      <Card padding="lg">
        <CardHeader><CardTitle>Review tasks</CardTitle></CardHeader>
        {ws.tasks.length === 0 ? <p className="pw-muted">No review tasks yet.</p> : (
          <ul className="pw-list">
            {ws.tasks.map((t) => (
              <li key={t.id}>
                <span>
                  <span className={t.overdue ? "pw-overdue" : ""}>{t.title}</span>
                  <span className="pw-list__meta">
                    {t.assignee?.name ?? "Unassigned"}
                    {t.completed_at ? `, completed ${formatDate(t.completed_at)}` : t.due_date ? `, due ${formatDate(t.due_date)}${t.overdue ? ", overdue" : ""}` : ""}
                  </span>
                </span>
                {write && (
                  <span className="pw-actions">
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => completeTask(changeCaseId, t.id, !t.completed_at))}>
                      {t.completed_at ? "Reopen" : "Mark done"}
                    </Button>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => deleteTask(changeCaseId, t.id))}>Remove</Button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {write && (
          <form className="pw-row" style={{ marginTop: 12 }} onSubmit={(e) => {
            e.preventDefault();
            run(() => addTask(changeCaseId, taskTitle, taskAssignee ? Number(taskAssignee) : null, taskDate || null),
              () => { setTaskTitle(""); setTaskAssignee(""); setTaskDate(""); });
          }}>
            <div>
              <label className="pw-label" htmlFor="pw-task-title">Task</label>
              <input id="pw-task-title" className="pw-field" value={taskTitle} maxLength={400} required onChange={(e) => setTaskTitle(e.target.value)} />
            </div>
            <div>
              <label className="pw-label" htmlFor="pw-task-who">Assigned to</label>
              <select id="pw-task-who" className="pw-field" value={taskAssignee} onChange={(e) => setTaskAssignee(e.target.value)}>
                <option value="">Nobody</option>
                {ws.people.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </div>
            <div>
              <label className="pw-label" htmlFor="pw-task-date">Due date (optional)</label>
              <input id="pw-task-date" type="date" className="pw-field" value={taskDate} onChange={(e) => setTaskDate(e.target.value)} />
            </div>
            <Button type="submit" size="sm" variant="secondary" disabled={busy}>Add task</Button>
          </form>
        )}
      </Card>
    </div>
  );
}
