import type { TraceProcessModel, TraceProcessSource } from "./traceProcess"

export interface TraceProcessLocation {
  entryId: string
  request: number
}

/** Only a unique recorded relationship is precise enough for a step jump. */
export function findProcessEntry(
  model: TraceProcessModel,
  source: Partial<TraceProcessSource> & { spanId: string },
  content?: unknown
) {
  const candidates = [...model.entries, ...(model.unlinkedTools ?? [])].filter(
    (entry) => {
      if (!source.field && entry.linkedToolSpanId === source.spanId) return true
      return entry.sources.some(
        (candidate) =>
          candidate.spanId === source.spanId &&
          (!source.field || candidate.field === source.field) &&
          (source.messageIndex === undefined ||
            candidate.messageIndex === source.messageIndex)
      )
    }
  )
  const matches =
    content === undefined
      ? candidates
      : candidates.filter(
          (entry) =>
            entry.kind === "message" &&
            JSON.stringify(entry.content) === JSON.stringify(content)
        )
  return matches.length === 1 ? matches[0] : undefined
}
