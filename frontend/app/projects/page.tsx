"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProjectCard } from "@/components/projects/ProjectCard";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { listProjects } from "@/lib/api";
import type { Project } from "@/types";
import { FolderKanban } from "lucide-react";
import "@/styles/components/project-grid.css";

export default function ProjectsPage() {
  const { checking } = useRequireAuth();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (checking) return;
    listProjects().then(setProjects).catch(() => setError("Could not load projects."));
  }, [checking]);

  if (checking) return null;

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Projects" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title="Projects"
          subtitle="Every formulation and process-optimization project in your organization."
          actions={
            <Button variant="primary" icon={<Plus size={16} />} onClick={() => router.push("/projects/new")}>
              New Project
            </Button>
          }
        />

        {error && <ErrorCallout message={error} />}
        {!projects && !error && <Spinner label="Loading projects…" />}

        {projects && projects.length === 0 && (
          <EmptyState
            icon={<FolderKanban size={32} />}
            title="No projects yet"
            description="Create your first optimization project to start uploading experiment data and getting recommendations."
            action={<Button variant="primary" onClick={() => router.push("/projects/new")}>Create Project</Button>}
          />
        )}

        {projects && projects.length > 0 && (
          <div className="project-grid">
            {projects.map((p) => <ProjectCard key={p.id} project={p} />)}
          </div>
        )}
      </div>
    </AppShell>
  );
}
