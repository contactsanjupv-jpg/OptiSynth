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
  const [featureColumns, setFeatureColumns] = useState("");
  const [targetMetric, setTargetMetric] = useState("");
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
      const cols = featureColumns.split(",").map((c) => c.trim()).filter(Boolean);
      await createChangeCase(name, triggerType, restrictedSubstance || null, {
        feature_columns: cols,
        target_metric: targetMetric.trim(),
        target_value: parseFloat(targetValue),
        direction,
      });
      setName(""); setRestrictedSubstance(""); setFeatureColumns(""); setTargetMetric(""); setTargetValue("");
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
      <Topbar><Breadcrumbs items={[{ label: "Change Cases" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title="Change Cases"
          subtitle="Forced-substitution qualification diagnostics -- when a material or supplier change forces a requalification decision."
        />

        {error && <ErrorCallout message={error} />}

        <div style={{ marginBottom: 16 }}>
          <Button variant="primary" onClick={() => setShowForm((s) => !s)}>
            {showForm ? "Cancel" : "New change case"}
          </Button>
        </div>

        {showForm && (
          <Card padding="lg" style={{ marginBottom: 20 }}>
            <CardHeader><CardTitle>New change case</CardTitle></CardHeader>
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
              <div>
                <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
                  Feature columns (comma-separated -- must match your CSV headers)
                </label>
                <input required value={featureColumns} onChange={(e) => setFeatureColumns(e.target.value)}
                  placeholder="e.g. crosslinker_ratio, cure_temp_c"
                  style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Target metric column</label>
                  <input required value={targetMetric} onChange={(e) => setTargetMetric(e.target.value)}
                    placeholder="e.g. salt_spray_hours"
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
                </div>
                <div style={{ width: 120 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Target value</label>
                  <input required type="number" step="any" value={targetValue} onChange={(e) => setTargetValue(e.target.value)}
                    placeholder="500"
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }} />
                </div>
                <div style={{ width: 130 }}>
                  <label style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>Direction</label>
                  <select value={direction} onChange={(e) => setDirection(e.target.value as "maximize" | "minimize")}
                    style={{ display: "block", width: "100%", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", fontSize: "var(--text-sm)", marginTop: 4 }}>
                    <option value="maximize">Maximize</option>
                    <option value="minimize">Minimize</option>
                  </select>
                </div>
              </div>
              <Button type="submit" variant="primary" loading={creating}>Create change case</Button>
            </form>
          </Card>
        )}

        <Card padding="lg">
          <CardHeader><CardTitle>All change cases</CardTitle></CardHeader>
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
