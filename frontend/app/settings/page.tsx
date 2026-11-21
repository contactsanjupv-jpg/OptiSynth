"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { OrganizationTab } from "@/components/settings/OrganizationTab";
import { AccountTab } from "@/components/settings/AccountTab";
import { TeamPanel } from "@/components/settings/TeamPanel";
import { SecurityTab } from "@/components/settings/SecurityTab";
import { AuditTab } from "@/components/settings/AuditTab";
import { DataTab } from "@/components/settings/DataTab";
import { NotificationsTab } from "@/components/settings/NotificationsTab";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getMe } from "@/lib/api";
import type { MeResponse } from "@/lib/api/auth";
import "@/styles/components/settings.css";
import "@/styles/components/overview.css";

const TABS = [
  { key: "organization", label: "Organization" },
  { key: "account", label: "Account" },
  { key: "team", label: "Team" },
  { key: "security", label: "Security" },
  { key: "audit", label: "Audit log" },
  { key: "data", label: "Data" },
  { key: "notifications", label: "Notifications" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

function SettingsInner() {
  const { checking } = useRequireAuth();
  const router = useRouter();
  const params = useSearchParams();
  const requested = params.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === requested) ? (requested as TabKey) : "organization";
  const [me, setMe] = useState<MeResponse | null>(null);

  const loadMe = useCallback(() => { getMe().then(setMe).catch(() => setMe(null)); }, []);
  useEffect(() => { if (!checking) loadMe(); }, [checking, loadMe]);

  if (checking) return null;

  function onKey(e: React.KeyboardEvent, index: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const target = TABS[(index + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    if (!target) return;
    router.replace(`/settings?tab=${target.key}`);
    document.getElementById(`tab-${target.key}`)?.focus();
  }

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Settings" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader title="Settings" subtitle="Your organization, account and workspace." />
        <div role="tablist" aria-label="Settings sections" className="settings-tabs">
          {TABS.map((t, i) => (
            <button key={t.key} id={`tab-${t.key}`} role="tab" type="button" className="settings-tab" aria-selected={tab === t.key}
              aria-controls="settings-panel" tabIndex={tab === t.key ? 0 : -1}
              onClick={() => router.replace(`/settings?tab=${t.key}`)} onKeyDown={(e) => onKey(e, i)}>
              {t.label}
            </button>
          ))}
        </div>
        <div id="settings-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {me && tab === "organization" && <OrganizationTab role={me.role} />}
          {me && tab === "account" && <AccountTab me={me} onChanged={loadMe} />}
          {me && tab === "team" && <TeamPanel />}
          {me && tab === "security" && <SecurityTab />}
          {me && tab === "audit" && <AuditTab role={me.role} />}
          {me && tab === "data" && <DataTab role={me.role} />}
          {me && tab === "notifications" && <NotificationsTab />}
        </div>
      </div>
    </AppShell>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={null}>
      <SettingsInner />
    </Suspense>
  );
}
