export function formatNumber(value: number | null | undefined, decimals = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatPercent(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(decimals)}%`;
}

// Display of a model-estimated probability (a fraction in [0, 1]). Mirrors backend change_case_rules
// (PROBABILITY_DISPLAY_CEILING / _FLOOR / format_model_probability): the value itself is never changed, but a figure
// is never shown as 100% or 0%, because the number rests on the model's own distribution assumption and is not a
// measured or validated probability.
export const PROBABILITY_DISPLAY_CEILING = 0.99;
export const PROBABILITY_DISPLAY_FLOOR = 0.01;

export function formatModelProbability(probability: number | null | undefined): string {
  // not a real number in [0, 1] (null, undefined, text, NaN, infinity, out of range): never shown as a percentage or bound
  if (typeof probability !== "number" || !(probability >= 0 && probability <= 1)) return "not available";
  if (probability >= PROBABILITY_DISPLAY_CEILING) return "\u226599%";
  if (probability <= PROBABILITY_DISPLAY_FLOOR) return "\u22641%";
  return `${Math.round(probability * 100)}%`;
}

export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  const date = new Date(isoString);
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

export function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  return new Date(isoString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
