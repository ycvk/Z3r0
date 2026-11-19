export function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) {
    return "-";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let nextValue = value;
  let unitIndex = 0;
  while (nextValue >= 1000 && unitIndex < units.length - 1) {
    nextValue /= 1000;
    unitIndex += 1;
  }

  const formatted = unitIndex === 0 ? String(nextValue) : nextValue.toFixed(nextValue >= 10 ? 1 : 2);
  return `${formatted} ${units[unitIndex]}`;
}

export function formatCompactTokens(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k`;
  }
  return String(Math.trunc(value));
}

export function formatTokenUsageTooltip(usage: {
  llm_requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  total_tokens?: number;
}) {
  const total = (usage.total_tokens ?? 0).toLocaleString();
  const requests = (usage.llm_requests ?? 0).toLocaleString();
  const input = (usage.input_tokens ?? 0).toLocaleString();
  const output = (usage.output_tokens ?? 0).toLocaleString();
  const cached = (usage.cached_tokens ?? 0).toLocaleString();
  return `Total: ${total} tokens (${requests} req) · In: ${input} · Out: ${output} · Cached: ${cached}`;
}
