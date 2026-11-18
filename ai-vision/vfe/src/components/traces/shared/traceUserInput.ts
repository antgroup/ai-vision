import { parseStructuredTraceJson } from "./traceJson.ts"

export interface TraceInputSource {
  id: string
  name: string
  input?: unknown
  attributes?: Record<string, unknown>
  startTime?: string | null
  isRoot?: boolean
  isTool?: boolean
}

export interface UserInputMessage {
  index: number
  content: unknown
  text: string | null
  copyText: string
}

export interface UserInputSource {
  id: string
  name: string
  isRoot: boolean
  messages: UserInputMessage[]
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function decodeEnvelope(value: unknown): unknown {
  if (typeof value !== "string") return value
  const trimmed = value.trim()
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value
  return parseStructuredTraceJson(trimmed) ?? value
}

function contentIdentity(value: unknown): string | undefined {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (!record(item)) return item
    // Stored copies have per-span IDs. Their verified hash identifies the bytes;
    // retain format and custom metadata when comparing source messages.
    const attachment =
      item.type === "aivision_attachment" &&
      typeof item.sha256 === "string" &&
      /^[0-9a-f]{64}$/i.test(item.sha256)
    const fields = Object.entries(item).filter(
      ([key]) =>
        !attachment ||
        ![
          "id",
          "spaceId",
          "agentId",
          "traceId",
          "spanId",
          "direction",
        ].includes(key)
    )
    return Object.fromEntries(
      fields.sort(([left], [right]) =>
        left < right ? -1 : left > right ? 1 : 0
      )
    )
  })
}

/** Text-only SDK block wrappers do not make a different user request. */
function comparableUserContent(content: unknown): unknown {
  if (typeof content === "string") return { text: content }
  if (
    Array.isArray(content) &&
    content.length > 0 &&
    content.every(
      (block) =>
        typeof block === "string" ||
        (record(block) &&
          ["text", "input_text", "output_text"].includes(String(block.type)) &&
          ((typeof block.text === "string" &&
            Object.keys(block).every(
              (key) => key === "type" || key === "text"
            )) ||
            (typeof block.content === "string" &&
              Object.keys(block).every(
                (key) => key === "type" || key === "content"
              ))))
    )
  ) {
    return { text: readableText(content) }
  }
  // Attachments and block metadata must not disappear through text deduplication.
  return { structured: content }
}

const OPERATION_KINDS: Record<string, string> = {
  chat: "GENERATION",
  text_completion: "GENERATION",
  embeddings: "EMBEDDING",
  embedding: "EMBEDDING",
  execute_tool: "TOOL",
  invoke_agent: "AGENT",
  retrieval: "RETRIEVER",
}

/** Resolve original SDK evidence before any synthesized operation fallback. */
export function resolveReportedInputKind(
  attrs: Record<string, unknown>
): string | undefined {
  const operation = attrs["gen_ai.operation.name"]
  const operationKind =
    typeof operation === "string" && Object.hasOwn(OPERATION_KINDS, operation)
      ? OPERATION_KINDS[operation]
      : undefined
  const provenance = decodeEnvelope(attrs["aivision.normalization.sources"])
  const derivedOperation =
    record(provenance) && Object.hasOwn(provenance, "gen_ai.operation.name")
  const reportedKind = [
    "langfuse.observation.type",
    "gen_ai.span.kind",
    "openinference.span.kind",
  ]
    .map((key) => attrs[key])
    .find((value) => value !== undefined && value !== null)
  // Match trace_query/semantics/observations.py: explicit operations win;
  // synthesized operations only fill a missing kind from the original SDK.
  const kind =
    (!derivedOperation && operationKind) ||
    (reportedKind ? String(reportedKind).toUpperCase() : operationKind)
  return kind
}

function isOperationInput(source: TraceInputSource): boolean {
  if (source.isTool) return true
  const kind = resolveReportedInputKind(source.attributes ?? {})
  return kind === "TOOL" || kind === "RETRIEVER" || kind === "EMBEDDING"
}

/** OpenInference records message roles separately from its display-only IO text. */
function openInferenceInput(
  attrs: Record<string, unknown>
): unknown[] | undefined {
  const messages = new Map<number, Record<string, unknown>>()
  for (const [key, value] of Object.entries(attrs)) {
    const match = /^llm\.input_messages\.(\d+)\.message\.(role|content)$/.exec(
      key
    )
    if (!match) continue
    const index = Number(match[1])
    if (!Number.isSafeInteger(index)) continue
    const message = messages.get(index) ?? {}
    message[match[2]] = value
    messages.set(index, message)
  }
  const ordered = [...messages.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, message]) => message)
    .filter(
      (message) =>
        typeof message.role === "string" && Object.hasOwn(message, "content")
    )
  return ordered.length ? ordered : undefined
}

function inputValue(source: TraceInputSource): unknown {
  const attrs = source.attributes ?? {}
  const standard = "gen_ai.input.messages"
  const provenance = decodeEnvelope(attrs["aivision.normalization.sources"])
  const derived = record(provenance) && Object.hasOwn(provenance, standard)
  const structuredOi =
    derived || !Object.hasOwn(attrs, standard)
      ? openInferenceInput(attrs)
      : undefined
  const original = [
    "langfuse.observation.input",
    "input.value",
    "aivision.request.input",
  ].find((key) => attrs[key] !== undefined && attrs[key] !== null)

  if (source.input !== undefined) {
    const input = decodeEnvelope(source.input)
    // A canonical IO string can be OpenInference's concatenated display text.
    // Prefer its explicit role/content fields without parsing role-like prose.
    // Dedicated business IO (including explicit empty values) still wins.
    if (
      structuredOi &&
      typeof input === "string" &&
      source.input !== "" &&
      original === "input.value" &&
      source.input === attrs["input.value"]
    ) {
      return structuredOi
    }
    // Canonical IO may itself contain the fallback projection. If it matches
    // the synthesized messages, require the original SDK payload as evidence.
    if (
      !derived ||
      contentIdentity(input) !==
        contentIdentity(decodeEnvelope(attrs[standard]))
    ) {
      return input
    }
  }
  if (!derived && Object.hasOwn(attrs, standard)) {
    return decodeEnvelope(attrs[standard])
  }
  if (original === undefined) return structuredOi
  const input = decodeEnvelope(attrs[original])
  // Relay's LLM request envelope is not an arbitrary business content field.
  // Only unwrap its selected original input with explicit runtime evidence.
  if (
    original === "input.value" &&
    attrs["nemo_relay.scope_type"] === "llm" &&
    ["LLM", "GENERATION"].includes(resolveReportedInputKind(attrs) ?? "") &&
    record(input) &&
    record(input.content) &&
    Array.isArray(input.content.messages)
  )
    return input.content.messages
  return original === "input.value" &&
    typeof input === "string" &&
    input !== "" &&
    structuredOi
    ? structuredOi
    : input
}

function messageSequence(value: unknown, depth = 0): unknown[] {
  if (depth > 3) return []
  const parsed = decodeEnvelope(value)
  if (Array.isArray(parsed)) {
    // A single batch is unambiguous; multiple batches may be separate prompts.
    const sequence =
      parsed.length === 1 && Array.isArray(parsed[0]) ? parsed[0] : parsed
    // Relay's LangChain callback wraps serialized messages in an explicit
    // `message` envelope. Unwrap only that type; roles are checked below.
    return sequence.flatMap((item) =>
      record(item) &&
      item.type === "message" &&
      !Object.hasOwn(item, "role") &&
      Array.isArray(item.message)
        ? messageSequence(item.message, depth + 1)
        : [item]
    )
  }
  if (!record(parsed)) return []
  if (Object.hasOwn(parsed, "messages")) {
    return messageSequence(parsed.messages, depth + 1)
  }
  return [parsed]
}

function userContent(value: unknown): { content: unknown } | null {
  if (!record(value)) return null
  let message = value
  if (Object.hasOwn(message, "role")) {
    if (message.role !== "user") return null
  } else {
    if (record(message.data)) message = message.data
    const type = message.type ?? value.type
    if (type === "constructor") {
      if (!record(message.kwargs)) return null
      const kwargs = message.kwargs
      const id = message.id
      const knownHumanClass =
        message.lc === 1 &&
        Array.isArray(id) &&
        (id[0] === "langchain" || id[0] === "langchain_core") &&
        id.includes("messages") &&
        id.at(-1) === "HumanMessage"
      if (
        kwargs.type !== "human" &&
        kwargs.type !== "HumanMessage" &&
        !(kwargs.type === undefined && knownHumanClass)
      ) {
        return null
      }
      message = kwargs
    } else if (type !== "human" && type !== "HumanMessage") {
      return null
    }
    if (Object.hasOwn(message, "role") && message.role !== "user") return null
  }
  return {
    content: Object.hasOwn(message, "content")
      ? message.content
      : message.parts,
  }
}

function readableText(content: unknown): string | null {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return null
  const text = content.flatMap((block): string[] => {
    if (typeof block === "string") return [block]
    if (
      !record(block) ||
      !["text", "input_text", "output_text"].includes(String(block.type))
    ) {
      return []
    }
    if (typeof block.text === "string") return [block.text]
    if (typeof block.content === "string") return [block.content]
    return []
  })
  return text.length > 0 ? text.join("\n") : null
}

function timestamp(value: string | null | undefined): bigint | null {
  if (!value) return null
  if (/^\d{16,}$/.test(value)) return BigInt(value)
  const millis = Date.parse(value)
  if (!Number.isFinite(millis)) return null
  // Date preserves milliseconds only; retain the remaining RFC3339 precision.
  const fraction = value.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/)?.[1]
  const remainder = fraction ? BigInt(fraction.padEnd(9, "0").slice(3, 9)) : 0n
  return BigInt(millis) * 1_000_000n + remainder
}

function compareSources(
  left: TraceInputSource,
  right: TraceInputSource
): number {
  if (Boolean(left.isRoot) !== Boolean(right.isRoot))
    return left.isRoot ? -1 : 1
  const leftTime = timestamp(left.startTime)
  const rightTime = timestamp(right.startTime)
  if (leftTime !== rightTime) {
    if (leftTime === null) return 1
    if (rightTime === null) return -1
    return leftTime < rightTime ? -1 : 1
  }
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0
}

/** User-role evidence from loaded inputs; this does not identify a current turn. */
export function collectTraceUserInput(
  sources: TraceInputSource[]
): UserInputSource[] {
  const seen = new Set<string>()
  const result: UserInputSource[] = []
  for (const source of [...sources].sort(compareSources)) {
    if (isOperationInput(source)) continue
    const messages = messageSequence(inputValue(source)).flatMap(
      (value, index): UserInputMessage[] => {
        const user = userContent(value)
        if (!user) return []
        const { content } = user
        return [
          {
            index,
            content,
            text: readableText(content),
            copyText:
              typeof content === "string"
                ? content
                : (JSON.stringify(content, null, 2) ?? ""),
          },
        ]
      }
    )
    if (messages.length === 0) continue
    const identity = contentIdentity(
      messages.map(({ content }) => comparableUserContent(content))
    )!
    if (seen.has(identity)) continue
    seen.add(identity)
    result.push({
      id: source.id,
      name: source.name,
      isRoot: Boolean(source.isRoot),
      messages,
    })
  }
  return result
}
