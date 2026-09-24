"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getBillingSummary } from "@/lib/api";
import type { BillingSummary } from "@/types";

export default function BillingPage() {
  const { checking } = useRequireAuth();
  const [billing, setBilling] = useState<BillingSummary | null>(null);

  useEffect(() => {
    if (checking) return;
    getBillingSummary().then(setBilling).catch(() => setBilling(null));
  }, [checking]);

  if (checking) return null;

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Billing" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Billing" subtitle="Current plan and subscription status." />
        <Card padding="lg" style={{ maxWidth: 480 }}>
          <CardHeader><CardTitle>Current Plan</CardTitle></CardHeader>
          {!billing && <Spinner label="Loading…" />}
          {billing && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <span style={{ fontSize: "var(--text-xl)", fontWeight: 600, textTransform: "capitalize" }}>{billing.plan}</span>
                <Badge tone={billing.status === "active" ? "success" : "warning"}>{billing.status}</Badge>
              </div>
              {!billing.stripe_configured && (
                <p style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                  Payment processing isn&apos;t connected yet -- this reflects your organization&apos;s real trial
                  status, but there&apos;s no live Stripe integration behind it. Contact us to set up a paid pilot.
                </p>
              )}
            </>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
