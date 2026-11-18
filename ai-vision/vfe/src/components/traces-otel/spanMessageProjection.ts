import { messageRole } from "./spanContent.ts"
import { spanIO } from "./spanIO.ts"

type ObjectValue = Record<string, unknown>

interface FieldBuilder {
  value: ObjectValue
  keys: string[]
}

interface MessageBuilder extends FieldBuilder {
  calls: Map<number, FieldBuilder>
}

export interface SpanMessageEvidence {
  value: ObjectValue
  index: number
  attributeKey: string
  attributeKeys?: string[]
  toolCallSources?: { attributeKey: string; attributeKeys: string[] }[]
}

export interface SpanToolDefinition {
  index: number
  name?: string
  value: unknown
  rawValue: unknown
  attributeKey: string
}

function record(value: unknown): value is ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function decode(value: unknown): unknown {
  if (typeof value !== "string") return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

/** Traverse only declared SDK message envelopes, never arbitrary tool payloads. */
export function recordedMessageSequence(
  value: unknown,
  { relayModel = false }: { relayModel?: boolean } = {}
): ObjectValue[] {
  let remaining = 20_000
  const visit = (value: unknown, depth: number): ObjectValue[] => {
    if (--remaining < 0 || depth > 12) return []
    if (Array.isArray(value))
      return value.flatMap((item) => visit(item, depth + 1))
    if (!record(value)) return []
    if (messageRole(value) !== null) return [value]
    const langchainResponse =
      value.__nemo_relay_integrations_langchain_model_response
    if (
      relayModel &&
      record(langchainResponse) &&
      Array.isArray(langchainResponse.messages)
    )
      return visit(langchainResponse.messages, depth + 1)
    // Relay may sanitize `headers` away from its {headers, content} request.
    // Only model-scope input.value is permitted to unwrap this transport.
    if (
      relayModel &&
      record(value.content) &&
      Object.hasOwn(value.content, "messages") &&
      Object.keys(value).every((key) => key === "headers" || key === "content")
    )
      return visit(value.content.messages, depth + 1)
    if (value.type === "message" && Array.isArray(value.message))
      return visit(value.message, depth + 1)
    if (value.type === "tool_message" && record(value.tool_call)) {
      const result = value.tool_call
      return [{ ...result, role: "tool" }]
    }
    if (value.type === "command" && record(value.command))
      return visit(value.command, depth + 1)
    if (Object.hasOwn(value, "messages"))
      return visit(value.messages, depth + 1)
    if (record(value.update) && Object.hasOwn(value.update, "messages"))
      return visit(value.update.messages, depth + 1)
    return []
  }
  return visit(value, 0)
}

function ordinal(value: string): number | null {
  const index = Number(value)
  return Number.isSafeInteger(index) && index >= 0 ? index : null
}

/** OpenInference's flattened role fields are evidence; concatenated IO text is not. */
function openInferenceMessages(
  attrs: ObjectValue,
  field: "input" | "output"
): SpanMessageEvidence[] {
  const prefix = `llm.${field}_messages.`
  const messages = new Map<number, MessageBuilder>()
  for (const [key, value] of Object.entries(attrs)) {
    if (!key.startsWith(prefix)) continue
    const match = key
      .slice(prefix.length)
      .match(
        /^(\d+)\.message\.(role|content|tool_call_id|name|id|tool_calls\.(\d+)\.tool_call\.(id|function\.name|function\.arguments))$/
      )
    if (!match) continue
    const index = ordinal(match[1])
    if (index === null) continue
    const message: MessageBuilder = messages.get(index) ?? {
      value: {},
      keys: [],
      calls: new Map(),
    }
    message.keys.push(key)
    if (match[3] !== undefined) {
      const callIndex = ordinal(match[3])
      if (callIndex === null) continue
      const call: FieldBuilder = message.calls.get(callIndex) ?? {
        value: {},
        keys: [],
      }
      call.keys.push(key)
      if (match[4] === "id") call.value.id = value
      else {
        const fn: ObjectValue = record(call.value.function)
          ? call.value.function
          : {}
        fn[match[4].slice("function.".length)] = value
        call.value.function = fn
      }
      message.calls.set(callIndex, call)
    } else message.value[match[2]] = value
    messages.set(index, message)
  }
  return [...messages.entries()]
    .sort(([a], [b]) => a - b)
    .flatMap(([index, message]) => {
      if (typeof message.value.role !== "string") return []
      const calls = [...message.calls.entries()]
        .sort(([a], [b]) => a - b)
        .map(([, call]) => call)
      if (calls.length)
        message.value.tool_calls = calls.map((call) => call.value)
      const contentKey = `${prefix}${index}.message.content`
      return [
        {
          value: message.value,
          index,
          attributeKey: Object.hasOwn(attrs, contentKey)
            ? contentKey
            : `${prefix}${index}.message.role`,
          attributeKeys: message.keys,
          toolCallSources: calls.map((call) => ({
            attributeKey:
              call.keys.find((key) => key.endsWith(".id")) ?? call.keys[0],
            attributeKeys: call.keys,
          })),
        },
      ]
    })
}

/** Enhanced projection only: source payloads and raw IO precedence remain unchanged. */
export function projectSpanMessages(
  attrs: ObjectValue,
  field: "input" | "output"
): SpanMessageEvidence[] {
  const io = spanIO(attrs, field)
  const provenance = decode(attrs["aivision.normalization.sources"])
  const derived =
    record(provenance) && Object.hasOwn(provenance, `gen_ai.${field}.messages`)
  const canUseOi =
    !io ||
    (io.attributeKey === `${field}.value` &&
      typeof io.value === "string" &&
      (field === "output" || io.value !== "")) ||
    (derived && io.attributeKey === `gen_ai.${field}.messages`)
  if (canUseOi) {
    const messages = openInferenceMessages(attrs, field)
    if (messages.length) return messages
  }
  if (!io) return []
  const relayModel =
    io.attributeKey === `${field}.value` &&
    attrs["nemo_relay.scope_type"] === "llm" &&
    attrs["openinference.span.kind"] === "LLM"
  return recordedMessageSequence(io.value, { relayModel }).map(
    (value, index) => ({
      value,
      index,
      attributeKey: io.attributeKey,
    })
  )
}

/** A model's declared tool snapshot, never an assertion that any tool executed. */
export function collectSpanToolDefinitions(
  attrs: ObjectValue
): SpanToolDefinition[] {
  const result = (
    rawValue: unknown,
    index: number,
    attributeKey: string
  ): SpanToolDefinition => {
    const value = decode(rawValue)
    const fn = record(value) && record(value.function) ? value.function : value
    const name =
      record(fn) && typeof fn.name === "string" && fn.name.length
        ? fn.name
        : undefined
    return { index, name, value, rawValue, attributeKey }
  }
  if (Object.hasOwn(attrs, "gen_ai.tool.definitions")) {
    const definitions = decode(attrs["gen_ai.tool.definitions"])
    return Array.isArray(definitions)
      ? definitions.map((value, index) =>
          result(value, index, "gen_ai.tool.definitions")
        )
      : []
  }
  return Object.entries(attrs)
    .flatMap(([key, value]) => {
      const match = /^llm\.tools\.(\d+)\.tool\.json_schema$/.exec(key)
      const index = match ? ordinal(match[1]) : null
      return index === null ? [] : [result(value, index, key)]
    })
    .sort((a, b) => a.index - b.index)
}

/** Explicit canonical IDs win. Conflicting legacy/OI aliases cannot establish a join. */
export function recordedToolCallId(attrs: ObjectValue): string | undefined {
  const value = (key: string) => {
    const raw = attrs[key]
    // IDs are opaque strings, including JSON literals such as "0" or "null".
    // Unwrap only a serialized string; never coerce an identifier to a scalar.
    const decoded =
      typeof raw === "string" && raw.trimStart().startsWith('"')
        ? decode(raw)
        : raw
    return typeof decoded === "string" && decoded.length ? decoded : undefined
  }
  const canonical = "gen_ai.tool.call.id"
  if (Object.hasOwn(attrs, canonical)) return value(canonical)
  const aliases = [
    "langfuse.observation.metadata.tool_call_id",
    "tool_call.id",
    "openinference.metadata.tool_call_id",
  ]
    .map(value)
    .filter((id): id is string => id !== undefined)
  const unique = [...new Set(aliases)]
  return unique.length === 1 ? unique[0] : undefined
}
