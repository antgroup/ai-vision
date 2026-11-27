import {
  collectTraceUserInput,
  resolveReportedInputKind,
} from "../traces/shared/traceUserInput.ts"
import { traceRecordedTextPart } from "../traces/shared/traceCodeTextParts.ts"
import type { OTelSpan } from "./types"

export interface ContentMessage {
  index: number
  value: unknown
}
export interface ContentField {
  key: string
  value: unknown
}
export interface RecordedChange {
  path: string
  diff?: string
  before?: string
  after?: string
}
export type SpanContentModel =
  | {
      kind: "conversation"
      user: ContentMessage & { content: unknown }
      inputHistory: ContentMessage[]
      latestOutput: ContentMessage | null
      outputHistory: ContentMessage[]
    }
  | {
      kind: "tool"
      input: unknown
      output: unknown
      fields: ContentField[]
      resultFields: ContentField[]
      otherArguments: ContentField[]
      otherResults: ContentField[]
      outcome: "error" | "ok" | "unknown"
      changes: RecordedChange[]
    }

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

/** Decode transport envelopes only; nested message text and argument values stay intact. */
function decode(value: unknown): unknown {
  let decoded = value
  for (let depth = 0; depth < 2 && typeof decoded === "string"; depth++) {
    try {
      decoded = JSON.parse(decoded)
    } catch {
      break
    }
  }
  return decoded
}

/** Relay's reported Pydantic ToolMessage is a known transport DTO, not an arbitrary data object. */
function relayToolMessageData(value: unknown): Record<string, unknown> | null {
  if (!record(value)) return null
  if (
    value.__nv_pydantic__ === "langchain_core.messages.tool.ToolMessage" &&
    record(value.data)
  )
    return value.data
  if (
    value.lc === 1 &&
    value.type === "constructor" &&
    Array.isArray(value.id) &&
    value.id.join(".") === "langchain_core.messages.ToolMessage" &&
    record(value.kwargs)
  )
    return value.kwargs
  return null
}

function sequence(value: unknown, depth = 0): unknown[] | null {
  if (depth > 3) return null
  const parsed = decode(value)
  if (Array.isArray(parsed)) {
    return parsed.length === 1 && Array.isArray(parsed[0]) ? parsed[0] : parsed
  }
  if (!record(parsed)) return null
  if (Object.hasOwn(parsed, "messages"))
    return sequence(parsed.messages, depth + 1)
  if (
    Object.hasOwn(parsed, "role") ||
    Object.hasOwn(parsed, "type") ||
    messageRole(parsed) !== null
  )
    return [parsed]
  return null
}

function fields(value: unknown, keys: string[]): ContentField[] {
  if (!record(value)) return []
  return keys.flatMap((key) =>
    Object.hasOwn(value, key) ? [{ key, value: value[key] }] : []
  )
}

/** Remove only fields that were successfully projected; custom metadata stays inspectable. */
function changes(input: unknown): {
  changes: RecordedChange[]
  remaining: unknown
} {
  if (!record(input)) return { changes: [], remaining: input }
  let remaining = { ...input }
  const projected: RecordedChange[] = []
  const extract = (path: string, source: Record<string, unknown>) => {
    const rest = { ...source }
    const change: RecordedChange = { path }
    const pair = [
      ["old_text", "new_text"],
      ["before", "after"],
    ].find(
      ([before, after]) =>
        typeof source[before] === "string" && typeof source[after] === "string"
    )
    if (pair) {
      change.before = source[pair[0]] as string
      change.after = source[pair[1]] as string
      delete rest[pair[0]]
      delete rest[pair[1]]
    }
    if (typeof source.unified_diff === "string") {
      change.diff = source.unified_diff
      delete rest.unified_diff
    }
    if (!pair && change.diff === undefined) return null
    projected.push(change)
    return rest
  }
  const pathKey =
    typeof input.path === "string"
      ? "path"
      : typeof input.file_path === "string"
        ? "file_path"
        : null
  if (pathKey) {
    const rest = extract(input[pathKey] as string, input)
    if (rest) {
      delete rest[pathKey]
      remaining = rest
    }
  }
  if (record(input.changes)) {
    const rest = { ...input.changes }
    for (const [path, change] of Object.entries(input.changes)) {
      const unused = record(change) ? extract(path, change) : null
      if (unused === null) continue
      if (Object.keys(unused).length) rest[path] = unused
      else delete rest[path]
    }
    if (Object.keys(rest).length) remaining.changes = rest
    else delete remaining.changes
  }
  return { changes: projected, remaining }
}

/** Only explicit structured outcome fields are evidence; output text is not. */
function structuredToolOutcome(result: unknown): "error" | "ok" | "unknown" {
  const exitCode = record(result) ? result.exit_code : undefined
  const failed =
    record(result) &&
    ((typeof result.error === "string" && result.error.length > 0) ||
      record(result.error) ||
      result.is_error === true ||
      result.isError === true ||
      result.status === "failed" ||
      result.status === "error" ||
      (typeof exitCode === "number" &&
        Number.isFinite(exitCode) &&
        exitCode !== 0))
  const succeeded =
    record(result) &&
    (exitCode === 0 ||
      result.status === "completed" ||
      result.status === "success")
  return failed ? "error" : succeeded ? "ok" : "unknown"
}

export function resolveToolOutcome(
  output: unknown
): "error" | "ok" | "unknown" {
  const result = decode(output)
  const direct = structuredToolOutcome(result)
  const message = structuredToolOutcome(relayToolMessageData(result))
  return direct === "error" || message === "error"
    ? "error"
    : direct === "ok" || message === "ok"
      ? "ok"
      : "unknown"
}

/** A missing error is not evidence of success; preserve explicit failures. */
export function resolveToolSpanOutcome(
  span: Pick<OTelSpan, "attributes" | "status">,
  output: unknown
): "error" | "ok" | "unknown" {
  const code = String(span.status?.code ?? "").toUpperCase()
  if (
    ["2", "ERROR", "STATUS_CODE_ERROR"].includes(code) ||
    span.attributes?.["langfuse.observation.level"] === "ERROR"
  )
    return "error"
  const outcome = resolveToolOutcome(output)
  if (outcome !== "unknown") return outcome
  return ["1", "OK", "STATUS_CODE_OK"].includes(code) ? "ok" : "unknown"
}

/** Compare decoded transport values, without rewriting source strings. */
export function sameContentValue(first: unknown, second: unknown): boolean {
  if (first === second) return true
  const a = decode(first)
  const b = decode(second)
  return a === b || JSON.stringify(a) === JSON.stringify(b)
}

/** Source-based excerpts only. The caller retains original IO and specialized cards. */
export function resolveSpanContent(
  span: OTelSpan,
  input: unknown,
  output: unknown,
  options: { tool?: boolean } = {}
): SpanContentModel | null {
  const attrs = span.attributes ?? {}
  const isTool = options.tool || resolveReportedInputKind(attrs) === "TOOL"
  if (isTool) {
    if (input === undefined && output === undefined) return null
    const args = decode(input)
    const receivedResult = decode(output)
    const messageData = relayToolMessageData(receivedResult)
    const result = messageData ?? receivedResult
    const projection = changes(args)
    const argumentFields = fields(projection.remaining, [
      "command",
      "cmd",
      "path",
      "file_path",
      "query",
      "pattern",
      "url",
      "offset",
      "limit",
      "description",
    ])
    const resultFields = fields(result, [
      "error",
      "exit_code",
      "status",
      "stderr",
      "stdout",
      "path",
      "offset",
      "limit",
      "total_lines",
      "content",
    ])
    const remaining = (value: unknown, selected: ContentField[]) =>
      record(value)
        ? Object.entries(value)
            .filter(([key]) => !selected.some((field) => field.key === key))
            .map(([key, value]) => ({ key, value }))
        : []
    return {
      kind: "tool",
      input,
      output,
      fields: argumentFields,
      resultFields,
      otherArguments: remaining(projection.remaining, argumentFields),
      otherResults: [
        ...remaining(result, resultFields),
        ...(messageData
          ? remaining(receivedResult, [{ key: "data", value: messageData }])
          : []),
      ],
      outcome: resolveToolOutcome(output),
      changes: projection.changes,
    }
  }
  const source = collectTraceUserInput([
    { id: span.span_id, name: span.name, attributes: attrs, input },
  ])[0]
  const user = source?.messages.at(-1)
  if (!user) return null
  const inputs = sequence(input) ?? []
  // A derived message array can differ from its original source. Only attach
  // indices/history when the displayed payload itself carries the same user.
  const displayed = collectTraceUserInput([
    { id: span.span_id, name: span.name, input },
  ])[0]?.messages.at(-1)
  if (
    !displayed ||
    !Object.hasOwn(inputs, user.index) ||
    displayed.index !== user.index ||
    JSON.stringify(displayed.content) !== JSON.stringify(user.content)
  )
    return null
  const candidateOutputs = sequence(output)
  const outputs =
    candidateOutputs && isRecordedMessageSequence(candidateOutputs)
      ? candidateOutputs
      : null
  const latest = outputs?.at(-1)
  return {
    kind: "conversation",
    user: {
      index: user.index,
      content: user.content,
      value: inputs[user.index],
    },
    inputHistory: inputs.flatMap((value, index) =>
      index === user.index ? [] : [{ index, value }]
    ),
    latestOutput: outputs?.length
      ? { index: outputs.length - 1, value: latest }
      : output !== undefined
        ? { index: 0, value: output }
        : null,
    outputHistory:
      outputs?.slice(0, -1).map((value, index) => ({ index, value })) ?? [],
  }
}

export function contentPreview(
  value: unknown,
  limit = 360
): { text: string; truncated: boolean } {
  const text =
    typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "")
  return { text: text.slice(0, limit), truncated: text.length > limit }
}

function messageBody(value: Record<string, unknown>) {
  return record(value.kwargs)
    ? value.kwargs
    : record(value.data)
      ? value.data
      : value
}

/** Extract presentation content while leaving the full message available on expansion. */
export function messageContent(
  value: unknown,
  { includeToolCalls = true }: { includeToolCalls?: boolean } = {}
): unknown {
  if (!record(value)) return value
  const message = messageBody(value)
  const content = Object.hasOwn(message, "content")
    ? message.content
    : message.parts
  const calls =
    message.tool_calls ??
    (record(message.additional_kwargs)
      ? message.additional_kwargs.tool_calls
      : undefined)
  if (includeToolCalls && calls !== undefined)
    return { content, tool_calls: calls }
  return content === undefined ? value : content
}

/** Known protocol roles share the existing message labels; custom roles stay raw. */
export function messageRole(value: unknown): string | null {
  if (!record(value)) return null
  if (typeof value.role === "string") return value.role
  const message = record(value.data) ? value.data : value
  let type = message.type ?? value.type
  if (
    type === "constructor" &&
    value.lc === 1 &&
    Array.isArray(value.id) &&
    ["langchain", "langchain_core"].includes(String(value.id[0])) &&
    value.id.includes("messages")
  )
    type = value.id.at(-1)
  const roles: Record<string, string> = {
    human: "user",
    HumanMessage: "user",
    ai: "assistant",
    AIMessage: "assistant",
    system: "system",
    SystemMessage: "system",
    tool: "tool",
    ToolMessage: "tool",
  }
  return typeof type === "string" ? (roles[type] ?? null) : null
}

export function isRecordedMessageSequence(value: readonly unknown[]): boolean {
  return (
    value.length > 0 &&
    value.every(
      (item) =>
        record(item) &&
        messageRole(item) !== null &&
        (messageContent(item) !== item ||
          typeof messageBody(item).reasoning_content === "string")
    )
  )
}

/** Typed message and content blocks retain their specialized rendering. */
export function isStructuredTraceValue(value: unknown): boolean {
  if (!record(value) && !Array.isArray(value)) return false
  if (!Array.isArray(value)) return true
  if (isRecordedMessageSequence(value)) return false
  const typedContent =
    value.every((item) => record(item) && typeof item.type === "string") &&
    value.some((item) => traceRecordedTextPart(item) !== null)
  return !typedContent
}
