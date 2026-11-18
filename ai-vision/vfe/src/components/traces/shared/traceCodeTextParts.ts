import { TRACE_CODE_SCAN_LIMIT, traceCodeFences } from "./traceCodeFences.ts"
import type { TraceMediaPart } from "./traceMediaProjection.ts"

export function traceRecordedTextPart(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const { type, text, content } = value as Record<string, unknown>
  if (type !== "text" && type !== "input_text" && type !== "output_text")
    return null
  if (typeof text === "string") return text
  return typeof content === "string" ? content : null
}

type TextPart = { start: number; end: number; text: string | null }

/** Join only adjacent recorded text parts that share a fence, with no synthetic separator. */
export function traceCodeTextParts(
  parts: readonly (string | null)[]
): TextPart[] {
  const groups: TextPart[] = []
  for (let index = 0; index < parts.length; ) {
    const start = index
    const offsets = [0]
    let length = 0
    while (index < parts.length && parts[index] !== null) {
      length += parts[index]!.length
      if (length <= TRACE_CODE_SCAN_LIMIT) offsets.push(length)
      index++
    }
    if (index === start) {
      groups.push({ start, end: ++index, text: null })
      continue
    }

    const joined =
      index - start > 1 && length <= TRACE_CODE_SCAN_LIMIT
        ? parts.slice(start, index).join("")
        : ""
    const spans: { start: number; end: number }[] = []
    let first = 0
    let last = 0
    for (const fence of traceCodeFences(joined)) {
      while (offsets[first + 1] <= fence.start) first++
      // A complete fence does not own the next part's line break.
      const closingEnd = fence.closed
        ? fence.end -
          (joined
            .slice(Math.max(fence.end - 2, 0), fence.end)
            .match(/(?:\r\n|[\r\n])$/)?.[0].length ?? 0)
        : fence.end
      last = Math.max(first, last)
      while (offsets[last + 1] < closingEnd) last++
      if (last <= first) continue

      // Appending an independent prose block can turn a complete end-of-part
      // closing fence into an apparent opening fence with an unclosed body.
      const localStart = fence.start - offsets[first]
      if (
        traceCodeFences(parts[start + first]!).some(
          (local) => local.closed && local.start === localStart
        )
      )
        continue

      const previous = spans.at(-1)
      if (previous && previous.end >= first) previous.end = last
      else spans.push({ start: first, end: last })
    }

    let next = 0
    let spanIndex = 0
    while (start + next < index) {
      const span = spans[spanIndex]
      if (span?.start === next) {
        groups.push({
          start: start + span.start,
          end: start + span.end + 1,
          text: joined.slice(offsets[span.start], offsets[span.end + 1]),
        })
        next = span.end + 1
        spanIndex++
      } else {
        groups.push({
          start: start + next,
          end: start + next + 1,
          text: parts[start + next],
        })
        next++
      }
    }
  }
  return groups
}

export function traceMediaCodeParts(parts: TraceMediaPart[]): TraceMediaPart[] {
  if (parts.length < 2) return parts
  return traceCodeTextParts(
    parts.map((part) => (part.kind === "text" ? part.text : null))
  ).map(({ start, end, text }) =>
    end === start + 1 || text === null ? parts[start] : { kind: "text", text }
  )
}
