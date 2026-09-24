"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getMe } from "@/lib/api";
import type { MeResponse } from "@/lib/api/auth";

export default function SettingsPage() {
  const { checking } = useRequireAuth();
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    if (checking) return;
    getMe().then(setMe).catch(() => setMe(null));
  }, [checking]);

  if (checking) return null;

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Settings" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Settings" subtitle="Your account details." />
        <Card padding="lg" style={{ maxWidth: 480 }}>
          <CardHeader><CardTitle>Account</CardTitle></CardHeader>
          {me && (
            <dl style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: "8px 16px", fontSize: "var(--text-sm)" }}>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Email</dt><dd>{me.user.email}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Name</dt><dd>{me.user.display_name || "—"}</dd>
              <dt style={{ color: "var(--color-text-tertiary)" }}>Role</dt><dd style={{ textTransform: "capitalize" }}>{me.role}</dd>
            </dl>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
