"use client";

import { Badge } from "@/components/ui/Badge";
import type { ValidationPoint, ValidationState, ValidationStatus } from "@/lib/api/models";
import "@/styles/components/models.css";

export const STATE_LABEL: Record<ValidationState, string> = {
  completed: "Validation completed",
  completed_with_warnings: "Completed with warnings",
  incomplete: "Validation incomplete",
  failed: "Validation failed",
};
const STATE_TONE: Record<ValidationState, "success" | "warning" | "danger" | "neutral"> = {
  completed: "success", completed_with_warnings: "warning", incomplete: "neutral", failed: "danger",
};
export const STATUS_LABEL: Record<ValidationStatus, string> = {
  completed: "Completed", completed_with_warnings: "Completed with warnings", insufficient_samples: "Not enough data",
  failed: "Failed", not_available: "Not available",
};
const STATUS_TONE: Record<ValidationStatus, "success" | "warning" | "danger" | "neutral"> = {
  completed: "success", completed_with_warnings: "warning", insufficient_samples: "neutral", failed: "danger", not_available: "neutral",
};
export const CLASS_LABEL: Record<string, string> = {
  within_training_spacing: "Within training spacing", sparse_region: "Sparse region", isolated: "Isolated", not_assessed: "Not assessed",
};
export const SUPPORT_LABEL: Record<string, string> = {
  within_evidence: "Within the evidence", weak: "Weakly supported", unsupported: "Unsupported", not_assessed: "Not assessed",
};
export const READING_LABEL: Record<string, string> = {
  insufficient_samples: "Too few points to read", under_covering: "Intervals too narrow", over_covering: "Intervals wider than needed",
  no_clear_departure: "No clear departure from nominal",
};

export const StateBadge = ({ state }: { state: ValidationState }) => <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>;
export const StatusBadge = ({ status }: { status: ValidationStatus }) => <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;

export function num(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "Not defined";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 100000 || a < 0.001)) return v.toExponential(2);
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "Not defined" : `${(v * 100).toFixed(1)}%`);

export function Stat({ label, value, note }: { label: string; value: React.ReactNode; note?: React.ReactNode }) {
  return <div className="mv-stat"><dt>{label}</dt><dd>{value}</dd>{note ? <small>{note}</small> : null}</div>;
}

/** Predicted against measured for held out rows. Every point was predicted by a model that had not seen it. */
export function HeldOutPlot({ points, unit }: { points: ValidationPoint[]; unit: string }) {
  if (points.length === 0) return null;
  const all = points.flatMap((p) => [p.measured, p.predicted]);
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo || 1) * 0.08;
  const a = lo - pad, b = hi + pad;
  const W = 420, H = 340, L = 56, B = 40, T = 12, R = 12;
  const x = (v: number) => L + ((v - a) / (b - a)) * (W - L - R);
  const y = (v: number) => H - B - ((v - a) / (b - a)) * (H - B - T);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => a + t * (b - a));
  return (
    <svg className="mv-plot" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Predicted against measured for ${points.length} held out rows`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth="1" />
          <text x={L - 6} y={y(t) + 4} fontSize="10" textAnchor="end" fill="var(--color-text-secondary)">{num(t, 1)}</text>
          <text x={x(t)} y={H - B + 16} fontSize="10" textAnchor="middle" fill="var(--color-text-secondary)">{num(t, 1)}</text>
        </g>
      ))}
      <line x1={x(a)} y1={y(a)} x2={x(b)} y2={y(b)} stroke="var(--color-text-tertiary)" strokeDasharray="4 4" />
      {points.map((p) => (
        <g key={p.row}>
          <line x1={x(p.measured)} x2={x(p.measured)} y1={y(p.predicted - 1.96 * p.sigma)} y2={y(p.predicted + 1.96 * p.sigma)} stroke="var(--color-text-tertiary)" strokeWidth="1" opacity="0.5" />
          <circle cx={x(p.measured)} cy={y(p.predicted)} r="3.5" fill="var(--color-text-primary)"><title>{`Row ${p.row + 1}: measured ${num(p.measured)}, predicted ${num(p.predicted)}`}</title></circle>
        </g>
      ))}
      <text x={(L + W - R) / 2} y={H - 6} fontSize="11" textAnchor="middle" fill="var(--color-text-secondary)">{`Measured${unit ? ` (${unit})` : ""}`}</text>
      <text transform={`translate(12 ${(T + H - B) / 2}) rotate(-90)`} fontSize="11" textAnchor="middle" fill="var(--color-text-secondary)">{`Predicted${unit ? ` (${unit})` : ""}`}</text>
    </svg>
  );
}
