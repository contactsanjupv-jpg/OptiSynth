import "@/styles/components/line-chart.css";

export interface ChartSeries {
  name: string;
  color: string;
  points: { x: number; y: number }[];
  dashed?: boolean;
}

interface LineChartProps {
  series: ChartSeries[];
  /** A single flat reference line, e.g. the customer's target value. */
  targetLine?: { value: number; label: string };
  xLabel?: string;
  yLabel?: string;
  height?: number;
}

const PADDING = { top: 16, right: 16, bottom: 32, left: 44 };

/**
 * Deliberately dependency-free: no recharts/d3/chart.js. For the small
 * number of series this product renders (best-found vs baseline vs target),
 * a hand-rolled SVG line chart is easier to keep pixel-precise with the
 * design tokens than wiring theme overrides into a charting library.
 */
export function LineChart({ series, targetLine, xLabel, yLabel, height = 260 }: LineChartProps) {
  const width = 560;
  const allPoints = series.flatMap((s) => s.points);
  if (allPoints.length === 0) {
    return <div className="line-chart line-chart--empty">No data yet</div>;
  }

  const xValues = allPoints.map((p) => p.x);
  const yValues = allPoints.map((p) => p.y).concat(targetLine ? [targetLine.value] : []);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues, 1);
  const yMin = Math.min(0, ...yValues);
  const yMax = Math.max(...yValues) * 1.08 || 1;

  const plotWidth = width - PADDING.left - PADDING.right;
  const plotHeight = height - PADDING.top - PADDING.bottom;

  const scaleX = (x: number) => PADDING.left + ((x - xMin) / (xMax - xMin || 1)) * plotWidth;
  const scaleY = (y: number) => PADDING.top + plotHeight - ((y - yMin) / (yMax - yMin || 1)) * plotHeight;

  const toPath = (points: { x: number; y: number }[]) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"} ${scaleX(p.x)} ${scaleY(p.y)}`).join(" ");

  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => yMin + ((yMax - yMin) * i) / yTicks);
  const xTicks = Math.min(5, xMax - xMin || 1);
  const xTickValues = Array.from({ length: xTicks + 1 }, (_, i) => xMin + ((xMax - xMin) * i) / xTicks);

  return (
    <div className="line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} className="line-chart__svg" role="img">
        {yTickValues.map((v, i) => (
          <g key={i}>
            <line
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={scaleY(v)}
              y2={scaleY(v)}
              className="line-chart__grid-line"
            />
            <text x={PADDING.left - 8} y={scaleY(v) + 4} className="line-chart__tick-label" textAnchor="end">
              {Math.round(v)}
            </text>
          </g>
        ))}

        {xTickValues.map((v, i) => (
          <text
            key={i}
            x={scaleX(v)}
            y={height - PADDING.bottom + 18}
            className="line-chart__tick-label"
            textAnchor="middle"
          >
            {Math.round(v)}
          </text>
        ))}

        {targetLine && (
          <>
            <line
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={scaleY(targetLine.value)}
              y2={scaleY(targetLine.value)}
              className="line-chart__target-line"
            />
          </>
        )}

        {series.map((s) => (
          <path
            key={s.name}
            d={toPath(s.points)}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeDasharray={s.dashed ? "4 3" : undefined}
          />
        ))}

        {xLabel && (
          <text x={width / 2} y={height - 2} textAnchor="middle" className="line-chart__axis-label">
            {xLabel}
          </text>
        )}
        {yLabel && (
          <text
            x={-height / 2}
            y={12}
            textAnchor="middle"
            transform="rotate(-90)"
            className="line-chart__axis-label"
          >
            {yLabel}
          </text>
        )}
      </svg>

      <div className="line-chart__legend">
        {series.map((s) => (
          <span key={s.name} className="line-chart__legend-item">
            <span className="line-chart__legend-swatch" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
        {targetLine && (
          <span className="line-chart__legend-item">
            <span className="line-chart__legend-swatch line-chart__legend-swatch--dashed" />
            {targetLine.label}
          </span>
        )}
      </div>
    </div>
  );
}
