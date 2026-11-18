import type { TraceListFilters } from "@/agents/types/langfuse"

export type TraceListThresholdKind = "duration" | "nodes" | "turns" | "tokens"
export type TraceListThresholdField =
  | "minDurationMs"
  | "minSpanCount"
  | "minTraceCount"
  | "minTotalTokens"

/** Server list predicates have useful lower bounds; reported telemetry may be zero. */
export const TRACE_LIST_THRESHOLDS = {
  minDurationMs: {
    kind: "duration",
    minimum: 1,
    label: "traceFilters.duration",
  },
  minSpanCount: { kind: "nodes", minimum: 1, label: "traceFilters.nodeCount" },
  minTraceCount: {
    kind: "turns",
    minimum: 1,
    label: "sessionFilters.traceCount",
  },
  minTotalTokens: {
    kind: "tokens",
    minimum: 100,
    label: "sessionFilters.tokens",
  },
} as const

export function traceListThresholdMinimum(
  kind: TraceListThresholdKind
): number {
  return kind === "tokens" ? 100 : 1
}

export function isValidTraceListThreshold(
  value: number,
  kind: TraceListThresholdKind
): boolean {
  return (
    Number.isFinite(value) &&
    value <= Number.MAX_SAFE_INTEGER &&
    value >= traceListThresholdMinimum(kind) &&
    (kind === "duration" || Number.isSafeInteger(value))
  )
}

export function traceListThresholdErrors(filters: TraceListFilters) {
  return (Object.keys(TRACE_LIST_THRESHOLDS) as TraceListThresholdField[])
    .filter((field) => {
      const raw = filters.thresholdInputs?.[field]
      const value = raw === undefined ? filters[field] : Number(raw)
      return (
        (raw !== undefined && !raw.trim()) ||
        (value !== undefined &&
          !isValidTraceListThreshold(value, TRACE_LIST_THRESHOLDS[field].kind))
      )
    })
    .map((field) => ({ field, ...TRACE_LIST_THRESHOLDS[field] }))
}

const URL_FIELDS: Record<TraceListThresholdField, string> = {
  minDurationMs: "min_duration_ms",
  minSpanCount: "min_span_count",
  minTraceCount: "min_trace_count",
  minTotalTokens: "min_total_tokens",
}

/** Keep invalid restored predicates editable instead of querying a broader scope. */
export function restoreTraceListThresholdInputs(
  params: URLSearchParams,
  filters: TraceListFilters
): TraceListFilters {
  const restored = { ...filters }
  for (const field of Object.keys(URL_FIELDS) as TraceListThresholdField[]) {
    const raw = params.get(URL_FIELDS[field])
    if (raw === null) continue
    const value = Number(raw)
    if (
      raw.trim() &&
      isValidTraceListThreshold(value, TRACE_LIST_THRESHOLDS[field].kind)
    )
      continue
    if (Number.isFinite(value)) restored[field] = value
    restored.thresholdInputs = { ...restored.thresholdInputs, [field]: raw }
  }
  return restored
}
