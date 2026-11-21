"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { ApiRequestError } from "@/lib/api/http";
import { changeStage, getStage, getStageHistory, type StageHistoryEntry, type StageOption, type StageState } from "@/lib/api/portfolio";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/stage-panel.css";

/** Where the program stands in the workflow, the moves available now, and every earlier change. */
export function StagePanel({ changeCaseId, onChanged }: { changeCaseId: number; onChanged?: () => void }) {
  const [state, setState] = useState<StageState | null>(null);
  const [history, setHistory] = useState<StageHistoryEntry[]>([]);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(() => {
    Promise.all([getStage(changeCaseId), getStageHistory(changeCaseId)])
      .then(([s, h]) => { setState(s); setHistory(h); setError(null); })
      .catch(() => setError("Could not load the stage. Try again."));
  }, [changeCaseId]);

  useEffect(() => { load(); }, [load]);

  const option: StageOption | undefined = state?.options.find((o) => o.stage === target);
  const blocked = option?.blocked_reason ?? null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!option || blocked) return;
    setBusy(true);
    try {
      await changeStage(changeCaseId, option.stage, reason);
      setTarget("");
      setReason("");
      load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not change the stage. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card padding="lg" style={{ marginBottom: 20 }}>
      <CardHeader><CardTitle>Stage</CardTitle></CardHeader>
      {error && <ErrorCallout message={error} />}
      {!state ? (
        !error && <p className="stage-muted">Loading stage</p>
      ) : (
        <>
          <p className="stage-current">{state.label}</p>
          {state.options.length === 0 ? (
            <p className="stage-muted">This stage cannot be changed.</p>
          ) : (
            <form onSubmit={submit} className="stage-form">
              <label htmlFor="stage-target" className="stage-label">Move to</label>
              <select id="stage-target" value={target} onChange={(e) => setTarget(e.target.value)} className="stage-field">
                <option value="">Choose a stage</option>
                {state.options.map((o) => (
                  <option key={o.stage} value={o.stage}>{o.label}</option>
                ))}
              </select>
              {blocked && <p className="stage-blocked" role="alert">{blocked}</p>}
              {option && !blocked && (
                <>
                  <label htmlFor="stage-reason" className="stage-label">
                    Reason{option.needs_reason ? "" : " (optional)"}
                  </label>
                  <textarea id="stage-reason" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)}
                    className="stage-field" required={option.needs_reason} />
                </>
              )}
              <div>
                <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!option || Boolean(blocked)}>
                  Change stage
                </Button>
              </div>
            </form>
          )}
          <p className="stage-note">Stage records where the work stands. It is not a qualification result.</p>
          <button type="button" className="stage-toggle" aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? "Hide history" : `Show history (${history.length})`}
          </button>
          {showHistory && (
            history.length === 0 ? <p className="stage-muted">No stage changes yet.</p> : (
              <ul className="stage-history">
                {history.map((h) => (
                  <li key={h.id}>
                    <span>{h.from_label ? `${h.from_label} to ${h.to_label}` : h.to_label}</span>
                    <span className="stage-muted">
                      {h.reason ? `${h.reason}. ` : ""}{h.actor ?? "System"}, {formatRelativeTime(h.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )
          )}
        </>
      )}
    </Card>
  );
}
