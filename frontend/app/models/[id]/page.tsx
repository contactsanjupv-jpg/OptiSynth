"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { Topbar } from "@/components/layout/Topbar";
import { Breadcrumbs } from "@/components/layout/Breadcrumbs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ErrorCallout } from "@/components/ui/Feedback";
import { CLASS_LABEL, HeldOutPlot, READING_LABEL, Stat, StateBadge, StatusBadge, SUPPORT_LABEL, num, pct } from "@/components/models/parts";
import { useRequireAuth } from "@/lib/auth/useRequireAuth";
import {
  compareModels, getApplicability, getModel, getUncertainty, getValidation, listModels, runValidation,
  type Applicability, type Comparison, type ModelDetail, type ModelSummary, type Uncertainty, type ValidationRecord,
} from "@/lib/api/models";
import { formatRelativeTime } from "@/lib/utils/format";
import "@/styles/components/program-workspace.css";
import "@/styles/components/models.css";

const TABS = [
  { key: "validation", label: "Validation" }, { key: "applicability", label: "Applicability" }, { key: "uncertainty", label: "Uncertainty" },
  { key: "training", label: "Training data" }, { key: "versions", label: "Versions" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

function ValidationTab({ model, reload }: { model: ModelDetail; reload: () => void }) {
  const [selected, setSelected] = useState<ValidationRecord | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unit = model.target.unit ?? "";

  async function run(key: string) {
    setBusy(key); setError(null);
    try {
      const r = await runValidation(model.id, key);
      setSelected(await getValidation(model.id, r.validation.id));
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Validation could not be run."); } finally { setBusy(null); }
  }
  const open = async (id: number) => { try { setSelected(await getValidation(model.id, id)); } catch { setError("Could not load this validation record."); } };
  const r = selected?.result;

  return (
    <div>
      {error && <ErrorCallout message={error} />}
      <div className="mv-block">
        <h3>Validation protocols</h3>
        <p className="mv-note">Each protocol holds out different rows and answers a different question. Read the limits of each result before using it.</p>
        <div className="mv-scroll">
          <table className="mv-table">
            <caption className="pw-sr-only">Validation protocols</caption>
            <thead><tr><th scope="col">Protocol</th><th scope="col">What it can show</th><th scope="col">Held out unit</th><th scope="col">Minimum</th><th scope="col">Latest result</th><th scope="col"><span className="pw-sr-only">Actions</span></th></tr></thead>
            <tbody>
              {model.protocols.map((p) => (
                <tr key={p.key}>
                  <td>{p.label}{p.required ? "" : <span className="pw-muted">{p.key === "random_rows_kfold" ? " Reference only" : " Optional"}</span>}</td>
                  <td>{p.evaluates}</td>
                  <td>{p.grouping_unit}</td>
                  <td>{p.minimum.rows ? `${p.minimum.rows} rows${p.minimum.input_sets ? `, ${p.minimum.input_sets} input sets` : ""}` : "Needs identifiers"}</td>
                  <td>{p.latest ? <><StatusBadge status={p.latest.status} /><div className="pw-muted">{p.latest.n_scored ? `${p.latest.n_scored} rows scored` : p.latest.message}</div></> : <span className="pw-muted">Not run</span>}</td>
                  <td>
                    {p.latest && p.latest.status !== "failed" && <Button size="sm" variant="secondary" onClick={() => open(p.latest!.id)}>View results</Button>}
                    {model.permissions.can_run && (!p.latest || p.latest.status === "failed") && (
                      <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => run(p.key)}>{busy === p.key ? "Running" : p.latest ? "Run again" : "Run"}</Button>
                    )}
                    {p.latest?.status === "failed" && <Button size="sm" variant="secondary" onClick={() => open(p.latest!.id)}>Details</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!model.permissions.can_run && <p className="mv-note">Only an owner, an admin, the program owner or a reviewer can run validation.</p>}
      </div>

      {selected && r && (
        <div className="mv-block" aria-live="polite">
          <h3>{model.protocols.find((p) => p.key === selected.protocol_key)?.label}</h3>
          <p className="mv-note"><StatusBadge status={selected.status} />{" "}{r.message ?? ""}</p>
          {r.warnings.length > 0 && <ul className="mv-list">{r.warnings.map((w) => <li key={w.code}>{w.message}</li>)}</ul>}
          {r.metrics && r.counts && (
            <>
              <dl className="mv-grid">
                <Stat label="MAE" value={`${num(r.metrics.mae)} ${unit}`} note={`${r.counts.n_scored} held out rows`} />
                <Stat label="RMSE" value={`${num(r.metrics.rmse)} ${unit}`} note={`${r.counts.n_scored} held out rows`} />
                <Stat label="R squared" value={num(r.metrics.r2)} note="On held out rows only" />
                <Stat label="Against a mean baseline" value={r.metrics.skill_vs_mean_baseline === null ? "Not defined" : pct(r.metrics.skill_vs_mean_baseline)} note={`Baseline RMSE ${num(r.metrics.baseline_rmse)} ${unit}`} />
                <Stat label="Bias" value={`${num(r.metrics.bias)} ${unit}`} note="Predicted minus measured" />
              </dl>
              <p className="mv-note">{`Method: ${model.protocols.find((p) => p.key === selected.protocol_key)?.label}, ${r.counts.n_folds_used} of ${r.counts.n_folds} folds used, ${r.counts.n_scored} of ${r.counts.n_rows} rows scored from ${r.counts.n_input_sets} distinct input sets. Protocol hash ${selected.protocol_hash.slice(0, 12)}.`}</p>
              <div className="mv-split">
                <div>{r.points && <HeldOutPlot points={r.points} unit={unit} />}</div>
                <div>
                  <h3>Residuals</h3>
                  <p className="mv-note">{`Mean ${num(r.metrics.residuals.mean)}, spread ${num(r.metrics.residuals.sd)}. Middle half of errors from ${num(r.metrics.residuals.quantiles.p25)} to ${num(r.metrics.residuals.quantiles.p75)} ${unit}.`}</p>
                  {r.metrics.residuals.by_predicted_level.length > 0 && (
                    <table className="mv-table"><thead><tr><th scope="col">Predicted level</th><th scope="col" className="num">Rows</th><th scope="col" className="num">Mean error</th></tr></thead>
                      <tbody>{r.metrics.residuals.by_predicted_level.map((b) => <tr key={b.band}><td>{b.band}</td><td className="num">{b.n}</td><td className="num">{num(b.mean_residual)}</td></tr>)}</tbody></table>
                  )}
                </div>
              </div>
            </>
          )}
          {r.calibration && (
            <div className="mv-block" style={{ marginTop: 16 }}>
              <h3>Interval coverage and sharpness</h3>
              <p className="mv-note">{`Reading across levels: ${READING_LABEL[r.calibration.assessment] ?? r.calibration.assessment}. Based on ${r.calibration.n} held out rows. No recalibration was applied.`}</p>
              <div className="mv-scroll">
                <table className="mv-table">
                  <caption className="pw-sr-only">Observed coverage against nominal level</caption>
                  <thead><tr><th scope="col">Nominal</th><th scope="col" className="num">Observed</th><th scope="col" className="num">Uncertainty of observed</th><th scope="col" className="num">Mean width</th><th scope="col">Reading</th></tr></thead>
                  <tbody>{r.calibration.levels.map((l) => (
                    <tr key={l.nominal}><td>{pct(l.nominal)}</td><td className="num">{`${pct(l.observed)} (${l.inside} of ${l.n})`}</td>
                      <td className="num">{l.observed_interval_95 ? `${pct(l.observed_interval_95[0])} to ${pct(l.observed_interval_95[1])}` : "Not defined"}</td>
                      <td className="num">{`${num(l.mean_width)} ${unit}`}</td><td>{READING_LABEL[l.reading] ?? l.reading}</td></tr>
                  ))}</tbody>
                </table>
              </div>
              <dl className="mv-grid" style={{ marginTop: 12 }}>
                <Stat label="Mean interval score (CRPS)" value={`${num(r.calibration.mean_crps)} ${unit}`} note="Lower is better. Rewards intervals that are right and narrow" />
                <Stat label="Mean predictive spread" value={`${num(r.calibration.sharpness.mean_sigma)} ${unit}`} note="Sharpness. Smaller is sharper" />
                <Stat label="Spread against error" value={num(r.calibration.sigma_error_rank_correlation)} note="Rank correlation. Higher means spread tracks error" />
                <Stat label="Standardised errors" value={`${num(r.calibration.standardised_residuals.mean)} and ${num(r.calibration.standardised_residuals.sd)}`} note="Mean and spread. About 0 and 1 expected" />
              </dl>
              <p className="mv-note">{r.calibration.caveat}</p>
            </div>
          )}
          {r.applicability_check && r.applicability_check.by_class.length > 0 && (
            <div className="mv-block">
              <h3>Held out error by distance from training rows</h3>
              <p className="mv-note">{r.applicability_check.note}</p>
              <table className="mv-table"><thead><tr><th scope="col">Class</th><th scope="col" className="num">Rows</th><th scope="col" className="num">Median error</th><th scope="col" className="num">95% coverage</th></tr></thead>
                <tbody>{r.applicability_check.by_class.map((c) => <tr key={c.class}><td>{CLASS_LABEL[c.class] ?? c.class}</td><td className="num">{c.n}</td><td className="num">{`${num(c.median_absolute_error)} ${unit}`}</td><td className="num">{c.coverage_95 === undefined ? "Too few rows" : pct(c.coverage_95)}</td></tr>)}</tbody></table>
            </div>
          )}
          <div className="mv-block">
            <h3>Limits of this protocol</h3>
            <ul className="mv-list">{model.protocols.find((p) => p.key === selected.protocol_key)?.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </div>
        </div>
      )}
    </div>
  );
}

function ApplicabilityTab({ model }: { model: ModelDetail }) {
  const [data, setData] = useState<Applicability | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { getApplicability(model.id).then(setData).catch(() => setError("Could not load applicability.")); }, [model.id]);
  if (error) return <ErrorCallout message={error} />;
  if (!data) return <p className="pw-muted">Loading applicability</p>;
  const ref = data.reference && "sparse_above" in data.reference ? data.reference : null;
  return (
    <div>
      <p className="mv-note">{data.convention}</p>
      {data.problem && <ErrorCallout message={data.problem} />}
      {ref ? (
        <dl className="mv-grid">
          <Stat label="Sparse above" value={num(ref.sparse_above)} note="Scaled distance to the nearest training input set" />
          <Stat label="Isolated above" value={num(ref.isolated_above)} note="Larger than any spacing within the training data" />
          <Stat label="Training input sets" value={ref.reference_points} note="Used to set both thresholds" />
        </dl>
      ) : <p className="mv-note">{data.reference && "reason" in data.reference ? data.reference.reason : "Not assessed."}</p>}
      {data.candidates.length === 0 ? <p className="mv-note">No candidate was ranked with this model version.</p> : (
        <div className="mv-scroll">
          <table className="mv-table">
            <caption className="pw-sr-only">Candidates ranked with this model version</caption>
            <thead><tr><th scope="col">Candidate</th><th scope="col">Support</th><th scope="col">Range check</th><th scope="col">Spacing</th><th scope="col" className="num">Distance</th><th scope="col">Nearest training input set</th></tr></thead>
            <tbody>
              {data.candidates.map((c) => (
                <tr key={c.candidate_id}>
                  <td>{c.name}{c.archived ? " (archived)" : ""}</td>
                  <td>{SUPPORT_LABEL[c.support] ?? c.support}{c.reason ? <div className="pw-muted">{c.reason}</div> : null}{c.unsupported_combination ? <div className="pw-muted">Every input is inside its range, but this combination has no nearby evidence.</div> : null}</td>
                  <td>{c.range_status ? c.range_status.replace(/_/g, " ") : "Not assessed"}</td>
                  <td>{CLASS_LABEL[c.class] ?? c.class}</td>
                  <td className="num">{c.nearest_distance === undefined ? "Not assessed" : num(c.nearest_distance)}</td>
                  <td>{c.nearest_training_input_set ? `${Object.entries(c.nearest_training_input_set).map(([k, v]) => `${k} ${num(v)}`).join(", ")} (${c.nearest_training_rows} ${c.nearest_training_rows === 1 ? "row" : "rows"})` : "Not assessed"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.support_note && <p className="mv-note">{data.support_note}</p>}
    </div>
  );
}

function UncertaintyTab({ model }: { model: ModelDetail }) {
  const [u, setU] = useState<Uncertainty | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { getUncertainty(model.id).then(setU).catch(() => setError("Could not load uncertainty.")); }, [model.id]);
  if (error) return <ErrorCallout message={error} />;
  if (!u) return <p className="pw-muted">Loading uncertainty</p>;
  const unit = model.target.unit ?? "";
  return (
    <div>
      <p className="mv-note">{u.note}</p>
      <div className="mv-block"><h3>Model uncertainty</h3><p className="mv-note">{u.model.description}</p>
        <dl className="mv-grid">
          <Stat label="Predictive spread now" value={u.model.predictive_sd_of_current_predictions ? `${num(u.model.predictive_sd_of_current_predictions.min)} to ${num(u.model.predictive_sd_of_current_predictions.max)} ${unit}` : "No predictions"} note={u.model.predictive_sd_of_current_predictions ? `${u.model.predictive_sd_of_current_predictions.n} candidates` : undefined} />
          <Stat label="Fitted noise" value={u.model.fitted_noise_sd === null ? "Not available" : `${num(u.model.fitted_noise_sd)} ${unit}`} note="Scatter the model left unexplained" />
        </dl>
        <p className="mv-note">{u.model.fit_note}</p>
        {u.model.held_out ? <p className="mv-note">{`Held out behaviour of these intervals is on the Validation tab (${u.model.held_out.n_scored} rows scored).`}</p> : <p className="mv-note">Run the grouped validation to see how the intervals behaved on held out rows.</p>}
      </div>
      <div className="mv-block"><h3>Measurement uncertainty</h3><p className="mv-note">{u.measurement.description}</p><p className="mv-note"><Badge tone="neutral">Not assessed</Badge>{" "}{u.measurement.reason}</p></div>
      <div className="mv-block"><h3>Data uncertainty</h3><p className="mv-note">{u.data.description}</p>
        <dl className="mv-grid">
          <Stat label="Rows" value={u.data.summary.n_rows} /><Stat label="Distinct input sets" value={u.data.summary.n_input_sets} />
          <Stat label="Repeated input sets" value={u.data.summary.n_repeated_input_sets} />
          <Stat label="Repeated scatter" value={u.data.replicate_scatter.pooled_sd === null ? "None observed" : `${num(u.data.replicate_scatter.pooled_sd)} ${unit}`} />
        </dl>
        <p className="mv-note">{u.data.replicate_scatter.note}</p>
      </div>
      <div className="mv-block"><h3>Applicability uncertainty</h3><p className="mv-note">{u.applicability.description}</p><p className="mv-note">{u.applicability.convention}</p></div>
    </div>
  );
}

function TrainingTab({ model }: { model: ModelDetail }) {
  const s = model.training_summary.data;
  return (
    <div>
      {!model.integrity.reproduces_identity && <ErrorCallout message={model.integrity.problem ?? "The stored evidence does not reproduce this version."} />}
      <dl className="mv-grid">
        <Stat label="Training rows" value={model.n_training_rows} note={`${model.n_input_sets} distinct input sets`} />
        <Stat label="Repeated input sets" value={s.n_repeated_input_sets} note={`${s.rows_in_repeated_input_sets} rows`} />
        <Stat label="Registered" value={formatRelativeTime(model.registered_at)} note={model.source === "ranking" ? "By a ranking" : "From the evidence tab"} />
        <Stat label="Predictions made" value={model.prediction_count} />
      </dl>
      <div className="mv-block"><h3>Identity</h3>
        <table className="mv-table"><tbody>
          <tr><th scope="row">Model version identity</th><td className="mv-hash">{model.identity_hash}</td></tr>
          <tr><th scope="row">Training data identity</th><td className="mv-hash">{model.training_data_hash}</td></tr>
          <tr><th scope="row">Seed</th><td>{model.seed}</td></tr>
          <tr><th scope="row">Engine version</th><td>{model.engine_version}</td></tr>
          <tr><th scope="row">Evidence file</th><td>{model.dataset_filename ?? "Not stored"}</td></tr>
          <tr><th scope="row">Libraries at build</th><td>{Object.entries(model.environment).map(([k, v]) => `${k} ${v}`).join(", ")}</td></tr>
          <tr><th scope="row">Libraries now</th><td>{Object.entries(model.reproducibility.runtime_now).map(([k, v]) => `${k} ${v}`).join(", ")}</td></tr>
        </tbody></table>
        <p className="mv-note">The identity is a hash of the canonical training rows, features, target, preprocessing, configuration, seed, engine version and library versions. The same inputs under the same libraries always give the same identity. The fitted model itself is not stored.</p>
      </div>
      <div className="mv-block"><h3>Features and target</h3>
        <table className="mv-table"><thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Unit</th><th scope="col">Training range</th></tr></thead><tbody>
          {model.feature_definitions.map((f) => { const range = s.feature_ranges[f.name]; return <tr key={f.name}><td>{f.name}</td><td>Input</td><td>{f.unit ?? "Not declared"}</td><td>{range ? `${num(range[0])} to ${num(range[1])}` : ""}</td></tr>; })}
          <tr><td>{model.target.name}</td><td>{`Target, ${model.target.direction}`}</td><td>{model.target.unit ?? "Not declared"}</td><td>{`${num(s.target_range[0])} to ${num(s.target_range[1])}`}</td></tr>
        </tbody></table>
      </div>
      <div className="mv-block"><h3>Preprocessing and configuration</h3>
        <ul className="mv-list">{Object.values(model.preprocessing).map((t) => <li key={t}>{t}</li>)}</ul>
        <ul className="mv-list">{Object.entries(model.configuration).map(([k, v]) => <li key={k}>{`${k.replace(/_/g, " ")}: ${String(v)}`}</li>)}</ul>
      </div>
      <div className="mv-block"><h3>Limits</h3><ul className="mv-list">{model.limitations.map((l) => <li key={l}>{l}</li>)}</ul></div>
    </div>
  );
}

function VersionsTab({ model }: { model: ModelDetail }) {
  const [versions, setVersions] = useState<ModelSummary[]>([]);
  const [other, setOther] = useState<number | null>(null);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { listModels(model.program_id).then((p) => setVersions(p.versions)).catch(() => setError("Could not load versions.")); }, [model.program_id]);
  const compare = (id: number) => { setOther(id); setError(null); compareModels(model.id, id).then(setCmp).catch((e) => { setCmp(null); setError(e instanceof Error ? e.message : "Could not compare."); }); };
  return (
    <div>
      {error && <ErrorCallout message={error} />}
      <div className="mv-scroll"><table className="mv-table">
        <caption className="pw-sr-only">Versions in this program</caption>
        <thead><tr><th scope="col">Version</th><th scope="col">Evidence</th><th scope="col" className="num">Rows</th><th scope="col">Validation</th><th scope="col">Registered</th><th scope="col"><span className="pw-sr-only">Compare</span></th></tr></thead>
        <tbody>{versions.map((v) => (
          <tr key={v.id}>
            <td>{v.id === model.id ? `Version ${v.version_number}` : <Link href={`/models/${v.id}`}>{`Version ${v.version_number}`}</Link>} {v.is_current && <Badge tone="accent">Current evidence</Badge>}</td>
            <td>{v.dataset_filename ?? "Not stored"}</td><td className="num">{v.n_training_rows}</td><td><StateBadge state={v.validation.state} /></td><td>{formatRelativeTime(v.registered_at)}</td>
            <td>{v.id !== model.id && <Button size="sm" variant={other === v.id ? "primary" : "secondary"} onClick={() => compare(v.id)}>{`Compare with version ${model.version_number}`}</Button>}</td>
          </tr>))}</tbody></table></div>
      {versions.length < 2 && <p className="mv-note">A new version appears when the accepted evidence changes. Existing versions are never overwritten.</p>}
      {cmp && (
        <div className="mv-block" style={{ marginTop: 16 }}>
          <h3>{`Version ${cmp.a.version_number} and version ${cmp.b.version_number}`}</h3>
          <p className="mv-note">{cmp.note}</p>
          {cmp.protocols.map((row) => (
            <div key={row.protocol_key} className="mv-block">
              <h3>{row.protocol_key.replace(/_/g, " ")}</h3>
              {!row.comparable ? <p className="mv-note">{row.reason}</p> : (
                <>
                  <p className="mv-note">{row.reading}</p>
                  <table className="mv-table"><thead><tr><th scope="col">Measure</th><th scope="col" className="num">{`Version ${cmp.a.version_number}`}</th><th scope="col" className="num">{`Version ${cmp.b.version_number}`}</th><th scope="col" className="num">Difference</th></tr></thead><tbody>
                    {Object.entries(row.metrics ?? {}).map(([k, m]) => <tr key={k}><td>{k.replace(/_/g, " ")}</td><td className="num">{num(m.a)}</td><td className="num">{num(m.b)}</td><td className="num">{num(m.difference)}</td></tr>)}
                    <tr><td>Mean predictive spread</td><td className="num">{num(row.sharpness?.a)}</td><td className="num">{num(row.sharpness?.b)}</td><td className="num" /></tr>
                    <tr><td>Mean interval score</td><td className="num">{num(row.mean_crps?.a)}</td><td className="num">{num(row.mean_crps?.b)}</td><td className="num" /></tr>
                    <tr><td>Held out rows scored</td><td className="num">{row.n_scored?.a}</td><td className="num">{row.n_scored?.b}</td><td className="num" /></tr>
                  </tbody></table>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelDetailInner() {
  const { checking } = useRequireAuth();
  const router = useRouter();
  const search = useSearchParams();
  const id = Number(useParams().id);
  const tab = (TABS.find((t) => t.key === search.get("tab"))?.key ?? "validation") as TabKey;
  const [model, setModel] = useState<ModelDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => { getModel(id).then((m) => { setModel(m); setError(null); }).catch(() => setError("This model version could not be loaded.")); }, [id]);
  useEffect(() => { if (!checking) load(); }, [checking, load]);

  if (checking) return null;
  const setTab = (key: TabKey) => router.replace(`/models/${id}?tab=${key}`);

  return (
    <AppShell>
      <Topbar><Breadcrumbs items={[{ label: "Models", href: "/models" }, { label: model ? `Version ${model.version_number}` : "Model version" }]} /></Topbar>
      <div className="app-shell__content">
        {error && <ErrorCallout message={error} />}
        {model && (
          <>
            <PageHeader title={`${model.program_name ?? "Program"}, version ${model.version_number}`}
              subtitle={`Build record for a Gaussian process fitted from ${model.n_training_rows} training rows. ${model.model_key}`} />
            <Card padding="md">
              <p className="mv-note" style={{ marginTop: 0 }}>
                <StateBadge state={model.validation.state} />{" "}{model.is_current ? "Built from the current evidence." : "Built from earlier evidence. A newer version exists."}{" "}
                <Link href={`/programs/${model.program_id}`}>Open program</Link>
              </p>
              <p className="mv-note" role="note"><strong>Build record, not a stored model.</strong>{" "}{model.reproducibility.statement}</p>
              {!model.reproducibility.runtime_matches && <p className="mv-note" role="alert">The libraries running now differ from the ones this version was built under. Register a new version before validating.</p>}
              <p className="mv-note">{model.validation.note} Predictions and validation results are model outputs. They are not physical qualification results.</p>
            </Card>
            <div role="tablist" aria-label="Model sections" className="pw-tabs" style={{ marginTop: 16 }}>
              {TABS.map((t) => <button key={t.key} id={`mv-tab-${t.key}`} role="tab" type="button" className="pw-tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>{t.label}</button>)}
            </div>
            <div role="tabpanel" aria-labelledby={`mv-tab-${tab}`}>
              {tab === "validation" && <ValidationTab model={model} reload={load} />}
              {tab === "applicability" && <ApplicabilityTab model={model} />}
              {tab === "uncertainty" && <UncertaintyTab model={model} />}
              {tab === "training" && <TrainingTab model={model} />}
              {tab === "versions" && <VersionsTab model={model} />}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

export default function ModelDetailPage() {
  return <Suspense fallback={null}><ModelDetailInner /></Suspense>;
}
