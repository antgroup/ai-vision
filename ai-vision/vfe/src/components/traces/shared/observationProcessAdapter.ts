import type { LangfuseTraceDetail } from "@/agents/types/langfuse"
import type { OTelSpan } from "@/components/traces-otel/types"

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function unixNano(time: string | null): string | undefined {
  if (!time) return undefined
  const milliseconds = Date.parse(time)
  return Number.isFinite(milliseconds)
    ? String(BigInt(milliseconds) * 1_000_000n)
    : undefined
}

/** Keep Observation identity and original IO; never synthesize calls from names. */
export function observationProcessSpans(
  trace: Pick<LangfuseTraceDetail, "id" | "observations">,
  masked = false
): OTelSpan[] {
  // Masking is checked before reading even the Observation array: shared pages
  // can retain the original payload in memory while the masked view is active.
  if (masked) return []
  return (trace.observations ?? []).map((observation) => {
    const metadata = record(observation.metadata) ? observation.metadata : {}
    const attributes = record(metadata.attributes) ? metadata.attributes : {}
    const attrs: Record<string, unknown> = {}
    for (const key of [
      "gen_ai.span.kind",
      "openinference.span.kind",
      "gen_ai.operation.name",
      "gen_ai.tool.call.id",
      "gen_ai.tool.name",
      "langfuse.observation.metadata.tool_call_id",
    ]) {
      const value = attributes[key] ?? metadata[key]
      if (typeof value === "string" && value !== "") attrs[key] = value
    }
    if (
      attrs["langfuse.observation.metadata.tool_call_id"] === undefined &&
      typeof metadata.tool_call_id === "string"
    ) {
      attrs["langfuse.observation.metadata.tool_call_id"] =
        metadata.tool_call_id
    }
    // Observation type is explicit SDK evidence. It also prevents the shared
    // projection from guessing a kind from a name when the type is unknown.
    attrs["langfuse.observation.type"] = observation.type || "SPAN"
    attrs["langfuse.observation.input"] = observation.input
    attrs["langfuse.observation.output"] = observation.output
    if (observation.model) attrs["gen_ai.request.model"] = observation.model
    const error = observation.level?.toUpperCase() === "ERROR"
    return {
      trace_id: trace.id,
      span_id: observation.id,
      parent_span_id: observation.parentObservationId ?? undefined,
      name: observation.name ?? observation.id,
      start_time_unix_nano: unixNano(observation.startTime),
      end_time_unix_nano: unixNano(observation.endTime),
      status: error ? { code: "ERROR", message: "" } : undefined,
      attributes: attrs,
    }
  })
}
