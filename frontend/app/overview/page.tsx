"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorCallout } from "@/components/ui/Feedback";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import { getOverview, STAGE_LABELS, type Overview, type StageKey, type Severity } from "@/lib/api/portfolio";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/overview.css";

const RANGES = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
  { days: 365, label: "Last 12 months" },
  { days: 0, label: "All time" },
];

// The pipeline groups the stored stages into six steps. Each group opens the program list for exactly its stages.
const GROUPS: { label: string; stages: StageKey[] }[] = [
  { label: "Intake", stages: ["intake"] },
  { label: "Requirements", stages: ["requirements"] },
  { label: "Evidence", stages: ["evidence_collection", "evidence_review"] },
  { label: "Analysis", stages: ["candidate_assessment", "experiment_planning"] },
  { label: "Validation", stages: ["physical_validation", "engineering_review"] },
  { label: "Decision", stages: ["approved", "rejected"] },
];

const SEVERITY_LABEL: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low" };

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function Skeleton() {
  return (
    <div className="ov" aria-busy="true" aria-label="Loading overview">
      <div className="ov-kpis">{[0, 1, 2, 3].map((i) => <div key={i} className="ov-skel ov-skel--kpi" />)}</div>
      <div className="ov-skel ov-skel--block" />
      <div className="ov-skel ov-skel--block" />
    </div>
  );
}

export default function OverviewPage() {
  const { checking } = useRequireAuth();
  const [days, setDays] = useState(90);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback((range: number) => {
    setLoading(true);
    getOverview(range)
      .then((d) => { setData(d); setError(null); })
      .catch(() => setError("Could not load the overview. Try again."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!checking) load(days);
  }, [checking, days, load]);

  if (checking) return null;

  const empty = data !== null && data.total_programs === 0;

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Overview" }]} /></Topbar>
      <div className="app-shell__content">
        <PageHeader
          title="Overview"
          subtitle="Programs, evidence and decisions across your organization."
          actions={
            <div className="ov-range">
              <label htmlFor="ov-range" className="ov-range__label">Period</label>
              <select id="ov-range" value={days} onChange={(e) => setDays(Number(e.target.value))} className="ov-select">
                {RANGES.map((r) => <option key={r.days} value={r.days}>{r.label}</option>)}
              </select>
            </div>
          }
        />

        {error && (
          <div className="ov-error">
            <ErrorCallout message={error} />
            <Button variant="secondary" size="sm" onClick={() => load(days)}>Try again</Button>
          </div>
        )}

        {loading && !data && !error && <Skeleton />}

        {empty && (
          <EmptyState
            title="No programs yet"
            description="Create a program when a material or supplier change forces a requalification decision."
            action={<Link href="/programs"><Button variant="primary">Create a program</Button></Link>}
          />
        )}

        {data && !empty && (
          <div className="ov" aria-busy={loading}>
            <section className="ov-kpis" aria-label="Key figures">
              <Link href="/programs?filter=active" className="ov-kpi">
                <span className="ov-kpi__label">Active programs</span>
                <span className="ov-kpi__value">{pad(data.kpis.active_programs)}</span>
              </Link>
              <Link href="/programs?filter=evidence_review" className="ov-kpi">
                <span className="ov-kpi__label">In evidence review</span>
                <span className="ov-kpi__value">{pad(data.kpis.evidence_review)}</span>
              </Link>
              <Link href="/programs?filter=tests_pending" className="ov-kpi">
                <span className="ov-kpi__label">Tests pending</span>
                <span className="ov-kpi__value">{pad(data.kpis.tests_pending)}</span>
              </Link>
              <Link href="/programs?filter=attention" className="ov-kpi">
                <span className="ov-kpi__label">Needs attention</span>
                <span className="ov-kpi__value">{pad(data.kpis.needs_attention)}</span>
              </Link>
            </section>

            <section className="ov-section" aria-labelledby="ov-attention">
              <div className="ov-section__head">
                <h2 id="ov-attention" className="ov-h2">Programs needing attention</h2>
                <Link href="/programs?filter=attention" className="ov-link">View all</Link>
              </div>
              {data.attention.length === 0 ? (
                <p className="ov-muted">No program needs attention right now.</p>
              ) : (
                <div className="ov-table-wrap">
                  <table className="ov-table">
                    <thead>
                      <tr><th>Program</th><th>Stage</th><th>Issue</th><th>Created by</th><th>Updated</th></tr>
                    </thead>
                    <tbody>
                      {data.attention.map((p) => (
                        <tr key={p.id}>
                          <td><Link href={`/programs/${p.id}`} className="ov-link ov-link--strong">{p.name}</Link></td>
                          <td>{p.stage_label}</td>
                          <td>{p.issues[0]?.label}{p.issues.length > 1 ? `, and ${p.issues.length - 1} more` : ""}</td>
                          <td>{p.created_by ?? "Unknown"}</td>
                          <td>{formatRelativeTime(p.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <div className="ov-grid">
              <section className="ov-section" aria-labelledby="ov-actions">
                <h2 id="ov-actions" className="ov-h2">Next actions</h2>
                {data.actions.length === 0 ? (
                  <p className="ov-muted">Nothing to do right now.</p>
                ) : (
                  <ul className="ov-list">
                    {data.actions.map((a, i) => (
                      <li key={`${a.program_id}-${a.type}-${i}`}>
                        <Link href={`/programs/${a.program_id}`} className="ov-row">
                          <span className="ov-row__main">
                            <span className="ov-row__title">{a.label}</span>
                            <span className="ov-row__meta">{a.program_name}{a.detail ? `, ${a.detail}` : ""}</span>
                          </span>
                          <span className={`ov-tag ov-tag--${a.severity}`}>{SEVERITY_LABEL[a.severity]}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="ov-section" aria-labelledby="ov-status">
                <h2 id="ov-status" className="ov-h2">Portfolio status</h2>
                <dl className="ov-status">
                  <div><dt>No open issues</dt><dd>{data.status_summary.no_open_issues}</dd></div>
                  <div><dt>Open issues</dt><dd>{data.status_summary.open_issues}</dd></div>
                  <div><dt>High priority</dt><dd>{data.status_summary.high_priority}</dd></div>
                </dl>
                <h3 className="ov-h3">Open issues by type</h3>
                {data.issue_types.length === 0 ? (
                  <p className="ov-muted">No open issues.</p>
                ) : (
                  <ul className="ov-bars">
                    {data.issue_types.map((t) => {
                      const max = Math.max(...data.issue_types.map((x) => x.count));
                      return (
                        <li key={t.type}>
                          <Link href={`/programs?issue=${t.type}`} className="ov-bar">
                            <span className="ov-bar__label">{t.label}</span>
                            <span className="ov-bar__track"><span className="ov-bar__fill" style={{ width: `${(t.count / max) * 100}%` }} /></span>
                            <span className="ov-bar__count">{t.count}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </div>

            <section className="ov-section" aria-labelledby="ov-pipeline">
              <h2 id="ov-pipeline" className="ov-h2">Program lifecycle</h2>
              <ol className="ov-pipeline">
                {GROUPS.map((g) => {
                  const count = g.stages.reduce((n, s) => n + (data.pipeline.find((p) => p.stage === s)?.count ?? 0), 0);
                  const title = g.stages.map((s) => STAGE_LABELS[s]).join(", ");
                  return (
                    <li key={g.label}>
                      <Link href={`/programs?stage=${g.stages.join(",")}`} className="ov-step" title={title}>
                        <span className={`ov-step__dot${count > 0 ? " ov-step__dot--on" : ""}`} aria-hidden="true" />
                        <span className="ov-step__label">{g.label}</span>
                        <span className="ov-step__count">{count}</span>
                      </Link>
                    </li>
                  );
                })}
              </ol>
              {(() => {
                const closed = data.pipeline.find((p) => p.stage === "closed")?.count ?? 0;
                return closed > 0 ? (
                  <p className="ov-muted"><Link href="/programs?stage=closed" className="ov-link">{closed} closed {closed === 1 ? "program" : "programs"}</Link></p>
                ) : null;
              })()}
            </section>

            <div className="ov-grid">
              <section className="ov-section" aria-labelledby="ov-decisions">
                <h2 id="ov-decisions" className="ov-h2">Recent decisions</h2>
                {data.decisions.length === 0 ? (
                  <p className="ov-muted">No decisions in this period.</p>
                ) : (
                  <ul className="ov-list">
                    {data.decisions.map((d) => (
                      <li key={d.id}>
                        <Link href={`/programs/${d.program_id}`} className="ov-row">
                          <span className="ov-row__main">
                            <span className="ov-row__title">{d.outcome}: {d.program_name}</span>
                            <span className="ov-row__meta">{d.reason ?? "No reason recorded"}{d.actor ? `, ${d.actor}` : ""}</span>
                          </span>
                          <span className="ov-time">{formatRelativeTime(d.created_at)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="ov-section" aria-labelledby="ov-activity">
                <h2 id="ov-activity" className="ov-h2">Recent activity</h2>
                {data.activity.length === 0 ? (
                  <p className="ov-muted">No activity in this period.</p>
                ) : (
                  <ul className="ov-list">
                    {data.activity.map((a) => {
                      const body = (
                        <>
                          <span className="ov-row__main">
                            <span className="ov-row__title">{a.label}</span>
                            <span className="ov-row__meta">{a.program_name ?? "Program no longer available"}{a.actor ? `, ${a.actor}` : ""}</span>
                          </span>
                          <span className="ov-time">{formatRelativeTime(a.created_at)}</span>
                        </>
                      );
                      return (
                        <li key={a.id}>
                          {a.program_id ? <Link href={`/programs/${a.program_id}`} className="ov-row">{body}</Link> : <div className="ov-row ov-row--static">{body}</div>}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
