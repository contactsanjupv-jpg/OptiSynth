"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { ApiRequestError } from "@/lib/api/http";
import { getOrganization, updateOrganization, type OrganizationProfile } from "@/lib/api/admin";

export function OrganizationTab({ role }: { role: string }) {
  const canEdit = role === "owner" || role === "admin";
  const [org, setOrg] = useState<OrganizationProfile | null>(null);
  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("");
  const [website, setWebsite] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getOrganization()
      .then((o) => { setOrg(o); setName(o.name); setIndustry(o.industry ?? ""); setWebsite(o.website ?? ""); })
      .catch(() => setError("Could not load organization details. Try again."));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setFieldError(null); setSaved(false);
    try {
      const o = await updateOrganization({ name, industry, website });
      setOrg(o); setSaved(true);
    } catch (err) {
      if (err instanceof ApiRequestError && err.body?.field) setFieldError({ field: err.body.field, message: err.message });
      else setError(err instanceof ApiRequestError ? err.message : "Could not save. Try again.");
    } finally { setBusy(false); }
  }

  const dirty = org !== null && (name !== org.name || industry !== (org.industry ?? "") || website !== (org.website ?? ""));
  const fe = (f: string) => (fieldError?.field === f ? <p className="settings-note" role="alert" style={{ color: "var(--color-text-primary)" }}>{fieldError.message}</p> : null);

  return (
    <Card padding="lg" style={{ maxWidth: 560 }}>
      <CardHeader><CardTitle>Organization profile</CardTitle></CardHeader>
      {error && <ErrorCallout message={error} />}
      {org && (
        <form onSubmit={save} className="settings-form">
          <label className="settings-label" htmlFor="org-name">Organization name</label>
          <input id="org-name" className="settings-field" value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} maxLength={120} required />
          {fe("name")}
          <label className="settings-label" htmlFor="org-industry">Industry (optional)</label>
          <select id="org-industry" className="settings-field" value={industry} onChange={(e) => setIndustry(e.target.value)} disabled={!canEdit}>
            <option value="">Not set</option>
            {org.industries.map((i) => <option key={i} value={i}>{i}</option>)}
            {industry && !org.industries.includes(industry) && <option value={industry}>{industry}</option>}
          </select>
          {fe("industry")}
          <label className="settings-label" htmlFor="org-website">Website (optional)</label>
          <input id="org-website" className="settings-field" value={website} onChange={(e) => setWebsite(e.target.value)} disabled={!canEdit} placeholder="https://example.com" inputMode="url" />
          {fe("website")}
          {canEdit ? (
            <div className="settings-actions">
              <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!dirty}>Save changes</Button>
            </div>
          ) : (
            <p className="settings-note">Only an owner or admin can change these details.</p>
          )}
          {saved && <p className="settings-ok" role="status">Saved</p>}
        </form>
      )}
    </Card>
  );
}
