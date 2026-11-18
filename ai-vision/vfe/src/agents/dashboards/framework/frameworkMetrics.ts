import type { QueryTimeRange } from "@/agents/types"
import type {
  NativeTraceMetricsResponse,
  NativeTraceMetricsUsage,
  NativeTraceReportedUsage,
} from "@/components/traces-otel/api"

export interface FrameworkUsageCell {
  value: number | null
  status:
    | "complete"
    | "subtotal"
    | "unreported"
    | "not-applicable"
    | "ambiguous"
  reportedCalls: number | null
}

function usageCell(
  usage: NativeTraceMetricsUsage,
  reported: NativeTraceReportedUsage | undefined,
  field: "input" | "output" | "total",
  embedding = false
): FrameworkUsageCell {
  const reportedCalls = reported?.[field].reportedCalls ?? null
  if (usage[field] !== null && usage[field] !== undefined) {
    return { value: usage[field], status: "complete", reportedCalls }
  }
  if (usage.unknownReason === "ambiguous_nested_generations") {
    return { value: null, status: "ambiguous", reportedCalls }
  }
  const subtotal = reported?.[field].value
  if (
    typeof subtotal === "number" &&
    reportedCalls !== null &&
    reportedCalls > 0
  ) {
    return { value: subtotal, status: "subtotal", reportedCalls }
  }
  return {
    value: null,
    status: embedding && field === "output" ? "not-applicable" : "unreported",
    reportedCalls,
  }
}

/** Freeze relative bounds once, shared by every dashboard query. */
export function frameworkWindow(timeRange: QueryTimeRange, now = Date.now()) {
  if (timeRange.type === "absolute") return timeRange
  const amount = Number.parseInt(timeRange.range, 10)
  const unit = timeRange.range.slice(-1)
  const multiplier =
    unit === "d" ? 86_400_000 : unit === "h" ? 3_600_000 : 60_000
  return { type: "absolute" as const, from: now - amount * multiplier, to: now }
}

export function frameworkMetricsView(
  metrics: NativeTraceMetricsResponse | null | undefined,
  unknownModel = "Unknown model"
) {
  if (!metrics) return null
  if (metrics.meta?.complete !== true) {
    throw new Error("Trace metrics require a complete time window")
  }
  const models = metrics.groups.map((group) => ({
    name: group.value ?? unknownModel,
    amount: group.usage.total,
    calls: group.callCount,
  }))
  const unknownModelCount = models.filter(
    (group) => group.amount === null
  ).length
  return {
    summary: metrics,
    summaryUsage: {
      input: usageCell(metrics.usage, metrics.reportedUsage, "input"),
      output: usageCell(metrics.usage, metrics.reportedUsage, "output"),
      total: usageCell(metrics.usage, metrics.reportedUsage, "total"),
    },
    ambiguousUsage:
      metrics.usage.unknownReason === "ambiguous_nested_generations",
    usageByType: (metrics.usageByType ?? []).map((row) => {
      // The global nesting warning is sufficient to reject additive Chat
      // subtotals even if a server omits the same reason from its type row.
      const usage =
        metrics.usage.unknownReason === "ambiguous_nested_generations" &&
        row.type === "GENERATION"
          ? { ...row.usage, unknownReason: "ambiguous_nested_generations" }
          : row.usage
      const total = usageCell(usage, row.reportedUsage, "total")
      return {
        type: row.type,
        callCount: row.callCount,
        input: usageCell(usage, row.reportedUsage, "input"),
        output: usageCell(
          usage,
          row.reportedUsage,
          "output",
          row.type === "EMBEDDING"
        ),
        total,
        missingTotalCalls:
          total.status === "complete" || total.status === "ambiguous"
            ? 0
            : Math.max(0, row.callCount - (total.reportedCalls ?? 0)),
      }
    }),
    tokens: metrics.timeseries.map((bucket) => ({
      timestamp: bucket.timestamp,
      input: bucket.usage.input,
      output: bucket.usage.output,
      total: bucket.usage.total,
      calls: bucket.callCount,
    })),
    models,
    unknownModelCount,
    canShowModelShare: unknownModelCount === 0,
  }
}

/** Complete-list and detail links use the same verified dashboard window and instance. */
export function frameworkTraceHref(input: {
  spaceId: string
  agentProduct: string
  agentUuid?: string | null
  from: string
  to: string
  traceId?: string
  spanId?: string
  sessionId?: string
  view?: "trace" | "session"
}) {
  const params = new URLSearchParams({
    spaceId: input.spaceId,
    source_system: "signoz",
    range: "custom",
    from: input.from,
    to: input.to,
  })
  if (input.agentUuid) params.set("agent_uuid", input.agentUuid)
  if (input.view === "session") params.set("view", "session")
  if (input.sessionId) params.set("sessionId", input.sessionId)
  if (input.traceId) {
    params.set("traceId", input.traceId)
    params.set("trace_from", input.from)
    params.set("trace_to", input.to)
    if (input.spanId) params.set("focus_span", input.spanId)
  }
  // The legacy Agent URL reader preserves literal plus signs. Encode spaces
  // as %20 rather than URLSearchParams' + so custom product names round-trip.
  return `/trace?${params.toString()}&agent=${encodeURIComponent(input.agentProduct)}`
}
