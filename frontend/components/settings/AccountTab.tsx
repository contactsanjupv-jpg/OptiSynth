"use client";

import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { ApiRequestError } from "@/lib/api/http";
import { changePassword, updateProfile } from "@/lib/api/admin";
import type { MeResponse } from "@/lib/api/auth";

const MIN = 10;

export function AccountTab({ me, onChanged }: { me: MeResponse; onChanged: () => void }) {
  const [name, setName] = useState(me.user.display_name ?? "");
  const [nameMsg, setNameMsg] = useState<string | null>(null);
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function saveName(e: React.FormEvent) {
    e.preventDefault();
    setNameErr(null); setNameMsg(null);
    try { await updateProfile(name); setNameMsg("Saved"); onChanged(); }
    catch (err) { setNameErr(err instanceof ApiRequestError ? err.message : "Could not save. Try again."); }
  }

  async function savePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr(null); setPwMsg(null);
    if (next.length < MIN) return setPwErr(`Use at least ${MIN} characters.`);
    if (next !== again) return setPwErr("The new passwords do not match.");
    setBusy(true);
    try {
      const r = await changePassword(current, next);
      setCurrent(""); setNext(""); setAgain("");
      setPwMsg(r.other_sessions_ended > 0 ? `Password changed. ${r.other_sessions_ended} other ${r.other_sessions_ended === 1 ? "session was" : "sessions were"} signed out.` : "Password changed");
    } catch (err) { setPwErr(err instanceof ApiRequestError ? err.message : "Could not change the password. Try again."); }
    finally { setBusy(false); }
  }

  return (
    <div className="settings-grid">
      <Card padding="lg">
        <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
        <form onSubmit={saveName} className="settings-form">
          <span className="settings-label">Email</span>
          <span style={{ fontSize: "var(--text-sm)" }}>{me.user.email}</span>
          <label className="settings-label" htmlFor="acct-name">Name</label>
          <input id="acct-name" className="settings-field" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <span className="settings-label">Role</span>
          <span style={{ fontSize: "var(--text-sm)", textTransform: "capitalize" }}>{me.role}</span>
          {nameErr && <ErrorCallout message={nameErr} />}
          <div className="settings-actions"><Button type="submit" variant="primary" size="sm" disabled={name === (me.user.display_name ?? "")}>Save changes</Button></div>
          {nameMsg && <p className="settings-ok" role="status">{nameMsg}</p>}
        </form>
      </Card>
      <Card padding="lg">
        <CardHeader><CardTitle>Password</CardTitle></CardHeader>
        <form onSubmit={savePassword} className="settings-form">
          <label className="settings-label" htmlFor="pw-current">Current password</label>
          <input id="pw-current" type="password" autoComplete="current-password" className="settings-field" value={current} onChange={(e) => setCurrent(e.target.value)} required />
          <label className="settings-label" htmlFor="pw-new">New password</label>
          <input id="pw-new" type="password" autoComplete="new-password" className="settings-field" value={next} onChange={(e) => setNext(e.target.value)} required minLength={MIN} />
          <label className="settings-label" htmlFor="pw-again">Repeat new password</label>
          <input id="pw-again" type="password" autoComplete="new-password" className="settings-field" value={again} onChange={(e) => setAgain(e.target.value)} required />
          <p className="settings-note">Changing your password signs you out of your other sessions.</p>
          {pwErr && <ErrorCallout message={pwErr} />}
          <div className="settings-actions"><Button type="submit" variant="primary" size="sm" loading={busy}>Change password</Button></div>
          {pwMsg && <p className="settings-ok" role="status">{pwMsg}</p>}
        </form>
      </Card>
    </div>
  );
}
