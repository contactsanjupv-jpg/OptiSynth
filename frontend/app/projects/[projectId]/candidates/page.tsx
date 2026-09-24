"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorCallout } from "@/components/ui/Feedback";
import { EmptyState } from "@/components/ui/EmptyState";
import { DataTable, type DataTableColumn } from "@/components/tables/DataTable";
import { getLatestCandidates, getRecommendations } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/utils/format";
import type { Recommendation } from "@/types";
import { Sparkles } from "lucide-react";

export default function CandidatesPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = Number(params.projectId);
  const [candidates, setCandidates] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

    const load = useCallback(() => {
    getLatestCandidates(projectId).then(setCandidates).catch(() => setCandidates([]));
  }, [projectId]);

  useEffect(() => { if (projectId) load(); }, [projectId, load]);

  async function handleGenerate() {
    setError(null);
    setGenerating(true);
    try {
      await getRecommendations(projectId, 5);
      load();
    } catch {
      setError("Could not generate recommendations. Make sure you've uploaded historical data first.");
    } finally {
      setGenerating(false);
    }
  }

  const columns: DataTableColumn<Recommendation>[] = [
    { key: "rank", header: "Rank", render: (r) => r.rank ?? "—" },
    { key: "predicted", header: "Predicted Value", render: (r) => formatNumber(r.predicted_value) },
    { key: "confidence", header: "Confidence", render: (r) => formatPercent(r.feasibility_probability * 100) },
    {
      key: "vars", header: "Key Variables",
      render: (r) => Object.entries(r.features).map(([k, v]) => `${k}=${formatNumber(v, 2)}`).join(", "),
    },
    {
      key: "actions", header: "", align: "right",
      render: (r) => r.id ? (
        <Button size="sm" variant="secondary" onClick={() => router.push(`/projects/${projectId}/candidates/${r.id}`)}>
          View Details
        </Button>
      ) : null,
    },
  ];

  return (
    <Card padding="lg">
      <CardHeader>
        <CardTitle>Recommended Next Experiments</CardTitle>
        <Button variant="primary" icon={<Sparkles size={15} />} loading={generating} onClick={handleGenerate}>
          Generate Candidates
        </Button>
      </CardHeader>
      {error && <ErrorCallout message={error} />}
      {!candidates && !error && <Spinner label="Loading candidates…" />}
      {candidates && candidates.length === 0 && (
        <EmptyState
          icon={<Sparkles size={28} />}
          title="No candidates yet"
          description="Generate candidates to get ranked recommendations for your next experiments, predicted from your historical data."
        />
      )}
      {candidates && candidates.length > 0 && (
        <DataTable columns={columns} rows={candidates} getRowKey={(r) => r.id ?? r.rank ?? Math.random()} />
      )}
    </Card>
  );
}
