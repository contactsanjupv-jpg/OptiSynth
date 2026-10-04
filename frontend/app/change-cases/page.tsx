"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ErrorCallout, Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { listChangeCases, createChangeCase } from "@/lib/api";
import { ApiRequestError } from "@/lib/api/http";
import { formatRelativeTime } from "@/lib/utils/format";
import type { ChangeCase, TriggerType } from "@/lib/api/change-cases";

const TRIGGER_LABELS: Record<TriggerType, string> = {
  regulatory_restriction: "Regulatory restriction",
  supplier_loss: "Supplier loss",
  component_obsolescence: "Component obsolescence",
  other: "Other",
};

const STATUS_TONE: Record<string, "neutral" | "success" | "warning"> = {
  draft: "neutral",
  active: "warning",
  completed: "success",
};

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

  function load() {
    listChangeCases()
      .then(setCases)
      .catch(() => setError("Could not load change cases."));
  }

  useEffect(() => {
    if (!checking) load();
  }, [checking]);

  if (checking) return null;

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
          {!cases && <Spinner label="Loading change cases…" />}
          {cases && cases.length === 0 && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
              No change cases yet. Create one when a material or supplier change forces a requalification decision.
            </p>
          )}
          {cases && cases.map((c) => (
            <Link key={c.id} href={`/change-cases/${c.id}`} style={{ textDecoration: "none", color: "inherit" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 0", borderBottom: "1px solid var(--color-border)" }}>
                <div>
                  <div style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>{c.name}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                    {TRIGGER_LABELS[c.trigger_type]}
                    {c.restricted_substance && ` · ${c.restricted_substance}`}
                    {" · created "}{formatRelativeTime(c.created_at)}
                  </div>
                </div>
                <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</Badge>
              </div>
            </Link>
          ))}
        </Card>
      </div>
    </AppShell>
  );
}
