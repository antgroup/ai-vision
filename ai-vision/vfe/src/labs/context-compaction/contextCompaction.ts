import type { OTelSpan } from "../../components/traces-otel/types.ts"
import { parseTimestampMs } from "../../components/traces-otel/format.ts"

export interface ContextCompactionSource {
  traceId: string
  spanId: string
  eventIndex: number
  eventName: string
  markId: string
  time?: string
  categoryProfile?: unknown
}

export interface ContextCompactionFact {
  id: string
  outcome: string
  data: Record<string, unknown>
  sources: ContextCompactionSource[]
}

const DATA_PREFIX = "nemo_relay.mark.data."

function identifier(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

/** Adapt explicit Relay mark projections into source-independent context facts.
 * Relay's OTLP projection does not retain data_schema: do not claim a schema
 * version from the event name or the optional category_profile. Original
 * events remain authoritative and are never mutated by this display adapter. */
export function collectContextCompactions(
  spans: OTelSpan[]
): ContextCompactionFact[] {
  const facts = new Map<string, ContextCompactionFact>()
  const conflicts = new Map<string, Set<string>>()
  for (const span of spans) {
    for (const [eventIndex, event] of (span.events ?? []).entries()) {
      if (event.name !== "compaction" && event.name !== "compaction.attempt")
        continue
      const attrs = event.attributes ?? {}
      const markId = identifier(attrs["nemo_relay.mark.uuid"])
      if (!markId) continue
      const data = Object.fromEntries(
        Object.entries(attrs)
          .filter(([key]) => key.startsWith(DATA_PREFIX))
          .map(([key, value]) => [key.slice(DATA_PREFIX.length), value])
      )
      if (!identifier(data.kind)) continue
      const reportedOutcome = identifier(data.outcome) ?? "unknown"
      // The reserved compaction mark is for committed rewrites only. Treat a
      // contradictory projection as a conflict, never as successful savings.
      const outcome =
        reportedOutcome === "unknown"
          ? "unknown"
          : (reportedOutcome === "committed") !== (event.name === "compaction")
            ? "conflict"
            : reportedOutcome
      const attemptId = identifier(data.attempt_id)
      const id = JSON.stringify([
        span.trace_id,
        identifier(data.session_id),
        attemptId ? "attempt" : "mark",
        attemptId ?? markId,
      ])
      const source: ContextCompactionSource = {
        traceId: span.trace_id,
        spanId: span.span_id,
        eventIndex,
        eventName: event.name,
        markId,
        time: event.time_unix_nano,
        categoryProfile: attrs["nemo_relay.mark.category_profile"],
      }
      const existing = facts.get(id)
      if (!existing) {
        facts.set(id, { id, outcome, data, sources: [source] })
        conflicts.set(id, new Set())
        continue
      }
      if (
        !existing.sources.some(
          (s) =>
            s.spanId === source.spanId && s.eventIndex === source.eventIndex
        )
      ) {
        existing.sources.push(source)
      }
      if (existing.outcome !== outcome) existing.outcome = "conflict"
      const conflictingKeys = conflicts.get(id)!
      for (const [key, value] of Object.entries(data)) {
        if (conflictingKeys.has(key)) continue
        const previous = existing.data[key]
        if (previous === undefined || previous === null) {
          existing.data[key] = value
        } else if (
          value !== undefined &&
          value !== null &&
          !Object.is(previous, value)
        ) {
          existing.data[key] = null
          conflictingKeys.add(key)
          existing.outcome = "conflict"
        }
      }
    }
  }
  const firstTime = (fact: ContextCompactionFact) =>
    Math.min(
      ...fact.sources.map((source) =>
        source.time ? (parseTimestampMs(source.time) ?? Infinity) : Infinity
      )
    )
  // Unknown timestamps stay last; the stable sort retains input order for
  // equal/unknown times and source indexes always refer to original events.
  return [...facts.values()].sort((a, b) => firstTime(a) - firstTime(b))
}
