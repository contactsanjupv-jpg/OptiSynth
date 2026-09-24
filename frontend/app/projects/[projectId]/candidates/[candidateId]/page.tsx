"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatCard } from "@/components/ui/StatCard";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getCandidateDetail, getProject } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/utils/format";
import type { Recommendation, Project } from "@/types";

export default function CandidateDetailPage() {
  const { checking } = useRequireAuth();
  const params = useParams();
  const projectId = Number(params.projectId);
  const candidateId = Number(params.candidateId);
  const [candidate, setCandidate] = useState<Recommendation | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (checking || !candidateId) return;
    Promise.all([getCandidateDetail(candidateId), getProject(projectId)])
      .then(([c, p]) => { setCandidate(c); setProject(p); })
      .catch(() => setError("Could not load this candidate."));
  }, [checking, candidateId, projectId]);

  if (checking) return null;

  return (
    <AppShell>
      <Topbar>
        <Breadcrumbs items={[
          { label: "Projects", href: "/projects" },
          { label: project?.name ?? "…", href: `/projects/${projectId}` },
          { label: "Candidates", href: `/projects/${projectId}/candidates` },
          { label: `#${candidateId}` },
        ]} />
      </Topbar>
      <div className="app-shell__content">
        {error && <ErrorCallout message={error} />}
        {!candidate && !error && <Spinner label="Loading candidate…" />}
        {candidate && (
          <>
            <h1 style={{ fontSize: "var(--text-2xl)", fontWeight: 600, marginBottom: 24 }}>
              Candidate #{candidateId}
            </h1>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
              <StatCard label="Predicted Value" value={formatNumber(candidate.predicted_value)} />
              <StatCard label="Uncertainty (std)" value={formatNumber(candidate.uncertainty_std)} />
              <StatCard label="Feasibility" value={formatPercent(candidate.feasibility_probability * 100)} />
              <StatCard label="Acquisition Score" value={formatNumber(candidate.acquisition_score, 3)} />
            </div>
            <Card padding="lg">
              <CardHeader><CardTitle>Recommended Variable Values</CardTitle></CardHeader>
              <table style={{ width: "100%", fontSize: "var(--text-sm)" }}>
                <tbody>
                  {Object.entries(candidate.features).map(([key, value]) => (
                    <tr key={key} style={{ borderBottom: "1px solid var(--color-border)" }}>
                      <td style={{ padding: "8px 0", color: "var(--color-text-secondary)" }}>{key}</td>
                      <td style={{ padding: "8px 0", textAlign: "right", fontWeight: 500 }}>{formatNumber(value, 3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}
