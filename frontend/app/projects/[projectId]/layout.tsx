"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useParams, usePathname } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { StatusBadge } from "@/components/ui/Badge";
import { Tabs } from "@/components/ui/Tabs";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getProject } from "@/lib/api";
import type { Project } from "@/types";

export default function ProjectLayout({ children }: { children: ReactNode }) {
  const { checking } = useRequireAuth();
  const params = useParams();
  const pathname = usePathname();
  const projectId = Number(params.projectId);
  const [project, setProject] = useState<Project | null>(null);

  useEffect(() => {
    if (checking || !projectId) return;
    getProject(projectId).then(setProject).catch(() => setProject(null));
  }, [checking, projectId, pathname]);

  if (checking) return null;

  const base = `/projects/${projectId}`;

  return (
    <AppShell>
      <Topbar>
        <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: project?.name ?? "…" }]} />
      </Topbar>
      <div className="app-shell__content">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
          <h1 style={{ fontSize: "var(--text-2xl)", fontWeight: 600 }}>{project?.name ?? "Loading…"}</h1>
          {project && <StatusBadge status={project.status} />}
        </div>
        {project && (
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", marginBottom: 20 }}>
            Target: {project.target_metric} {project.direction === "maximize" ? "\u2265" : "\u2264"}{" "}
            {project.target_value ?? "not set"} · {project.experiment_count ?? 0} experiments
          </p>
        )}

        <Tabs
          items={[
            { label: "Overview", href: base, exact: true },
            { label: "Candidates", href: `${base}/candidates` },
            { label: "Experiments", href: `${base}/experiments` },
            { label: "Model", href: `${base}/model` },
            { label: "Insights", href: `${base}/insights` },
            { label: "Reports", href: `${base}/reports` },
            { label: "Settings", href: `${base}/settings` },
          ]}
        />

        {children}
      </div>
    </AppShell>
  );
}