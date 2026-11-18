import type { TraceProjectionStep } from "../diagnoseApi"

export type TraceReplayTurnRole =
  | "answer"
  | "thought"
  | "tool"
  | "retrieval"
  | "agent"
  | "other"

export function stepRole(step: TraceProjectionStep): TraceReplayTurnRole {
  if (step.type === "GENERATION")
    return step.node === "Answer模型LLM结果" ? "answer" : "thought"
  if (step.type === "RETRIEVER") return "retrieval"
  if (step.type === "TOOL") return "tool"
  if (step.type === "AGENT") return "agent"
  return "other"
}

export function extractContentText(value: unknown): string | null {
  if (value === null || value === undefined) return null

  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) return ""
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        const parsed = JSON.parse(trimmed)
        return extractContentText(parsed) ?? value
      } catch {
        return value
      }
    }
    return value
  }

  if (Array.isArray(value)) {
    const parts = value
      .map((item) => extractContentText(item))
      .filter(
        (part): part is string => typeof part === "string" && part.trim() !== ""
      )
    return parts.length ? parts.join("\n") : null
  }

  if (typeof value === "object") {
    const obj = value as Record<string, unknown>
    if (
      typeof obj.content === "string" ||
      Array.isArray(obj.content) ||
      typeof obj.content === "object"
    ) {
      const content = extractContentText(obj.content)
      if (content !== null) return content
    }
    if (typeof obj.text === "string") return obj.text
    if (typeof obj.answer === "string") return obj.answer
    if (Array.isArray(obj.choices)) {
      const content = extractContentText(obj.choices)
      if (content !== null) return content
    }
    if (obj.message) {
      const content = extractContentText(obj.message)
      if (content !== null) return content
    }
    if (obj.delta) {
      const content = extractContentText(obj.delta)
      if (content !== null) return content
    }
  }

  return null
}
