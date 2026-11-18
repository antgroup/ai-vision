import { parseStructuredTraceJson } from "./traceJson"
import { traceMediaParts } from "./traceMediaProjection"

/** Project only explicit media and known tool-message envelopes for display. */
export function mediaOutputValue(value: unknown): unknown | null {
  let current = value
  for (let depth = 0; depth < 5; depth++) {
    if (typeof current === "string") {
      const parsed = parseStructuredTraceJson(current)
      if (parsed) {
        current = parsed
        continue
      }
    }
    if (traceMediaParts(current)) return current
    const messages = Array.isArray(current) ? current : [current]
    if (
      messages.length > 0 &&
      messages.every(
        (message) =>
          message !== null &&
          typeof message === "object" &&
          !Array.isArray(message) &&
          ["assistant", "tool"].includes(message.role) &&
          Object.hasOwn(message, "content")
      )
    ) {
      const content = messages.flatMap((message) =>
        typeof message.content === "string"
          ? [{ type: "text", text: message.content }]
          : Array.isArray(message.content)
            ? message.content
            : [message.content]
      )
      return traceMediaParts(content) ? content : null
    }
    if (!current || typeof current !== "object" || Array.isArray(current))
      return null
    const obj = current as Record<string, unknown>
    if (
      obj.lc === 1 &&
      obj.type === "constructor" &&
      Array.isArray(obj.id) &&
      obj.id.join(".") === "langchain_core.messages.ToolMessage" &&
      obj.kwargs &&
      typeof obj.kwargs === "object"
    ) {
      current = (obj.kwargs as Record<string, unknown>).content
    } else if (
      obj.__nv_pydantic__ === "langchain_core.messages.tool.ToolMessage" &&
      obj.data &&
      typeof obj.data === "object"
    ) {
      current = (obj.data as Record<string, unknown>).content
    } else return null
  }
  return null
}
