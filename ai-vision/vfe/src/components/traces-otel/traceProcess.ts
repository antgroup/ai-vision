import type { OTelSpan } from "./types"
import { getModelSpanPresentation } from "../traces/shared/modelSpanPresentation.ts"
import { messageRole, resolveToolOutcome } from "./spanContent.ts"
import { nanoDurationMs } from "./format.ts"
import { spanIO, type SpanIOValue } from "./spanIO.ts"
import {
  collectSpanToolDefinitions,
  projectSpanMessages,
  recordedMessageSequence,
  recordedToolCallId,
  type SpanMessageEvidence,
} from "./spanMessageProjection.ts"
import { resolveModelCallRole, type ModelCallRole } from "./spanModelContext.ts"
import { resolveReportedInputKind } from "../traces/shared/traceUserInput.ts"
import { reportedOmittedPaths } from "../../labs/privacy/relayPrivacyPolicy.ts"

export interface TraceProcessSource {
  spanId: string
  field: "input" | "output"
  messageIndex?: number
  attributeKey?: string
  attributeKeys?: string[]
  definitionIndex?: number
}

export interface TraceProcessMessage {
  kind?: "tool-definition"
  definitionName?: string
  role: string
  content: unknown
  source: TraceProcessSource
}

export interface TraceProcessEntry {
  id: string
  kind: "message" | "tool"
  role?: string
  content?: unknown
  toolName?: string
  arguments?: unknown
  result?: unknown
  outcome?: "error" | "ok" | "unknown"
  sources: TraceProcessSource[]
  spanId: string
  linkedToolSpanId?: string
  /** The linked execution's own producer configuration, never a removal receipt. */
  omittedPaths?: string[]
  callId?: string
  model?: string
  durationMs?: number | null
  branchSpanId?: string
  callRole?: ModelCallRole
}

export interface TraceProcessModel {
  entries: TraceProcessEntry[]
  context: TraceProcessMessage[]
  /** Actual tool executions that cannot be safely joined to the message requests. */
  unlinkedTools?: TraceProcessEntry[]
  /** Loaded spans that have no source reference in the process or its context. */
  unrepresentedSpanCount: number
}

export interface TraceProcessOptions {
  output?: unknown
  outputSourceSpanId?: string
  outputState?: string
}

type ObjectValue = Record<string, unknown>
type Outcome = "error" | "ok" | "unknown"

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

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined
}

/** Preserve nanoseconds for both native integers and RFC3339 timestamps. */
function timestamp(value: string | undefined): bigint | null {
  if (!value) return null
  if (/^-?\d+$/.test(value)) return BigInt(value)
  const parts = value.match(
    /^(\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:\d{2})$/
  )
  if (!parts) return null
  const milliseconds = Date.parse(parts[1] + parts[3])
  return Number.isFinite(milliseconds)
    ? BigInt(milliseconds) * 1_000_000n +
        BigInt((parts[2] ?? "").padEnd(9, "0").slice(0, 9))
    : null
}

/** Unknown times follow recorded times; equal times keep stable source order. */
function compareTimestamps(
  first: bigint | null,
  second: bigint | null
): number {
  if (first === null) return second === null ? 0 : 1
  if (second === null) return -1
  return first < second ? -1 : first > second ? 1 : 0
}

function body(value: ObjectValue): ObjectValue {
  return record(value.kwargs)
    ? value.kwargs
    : record(value.data)
      ? value.data
      : value
}

const sequence = recordedMessageSequence

interface ToolCall {
  id?: string
  name?: string
  args: unknown
}

interface Message {
  role: string
  content: unknown
  id?: string
  resultId?: string
  name?: string
  calls: ToolCall[]
  responses: { id?: string; content: unknown; raw: ObjectValue }[]
  raw: ObjectValue
}

function normalize(raw: ObjectValue): Message {
  const value = body(raw)
  const additional = record(value.additional_kwargs)
    ? value.additional_kwargs
    : {}
  const parts = Array.isArray(value.parts) ? value.parts : []
  const reportedCalls = value.tool_calls ?? additional.tool_calls
  const calls = Array.isArray(reportedCalls)
    ? reportedCalls
    : parts.filter(
        (part) =>
          record(part) && ["tool_call", "tool_use"].includes(String(part.type))
      )
  const contentParts = parts.filter(
    (part) =>
      !record(part) || !["tool_call", "tool_use"].includes(String(part.type))
  )
  return {
    role: messageRole(raw)!,
    content: Object.hasOwn(value, "content")
      ? value.content
      : contentParts.length
        ? contentParts
        : undefined,
    id: text(value.id),
    resultId: text(value.tool_call_id) ?? text(value.toolCallId),
    name: text(value.name),
    calls: calls.filter(record).map((call) => {
      const fn = record(call.function) ? call.function : call
      return {
        id: text(call.id) ?? text(call.tool_call_id),
        name: text(fn.name),
        args: decode(
          Object.hasOwn(fn, "arguments")
            ? fn.arguments
            : Object.hasOwn(fn, "args")
              ? fn.args
              : fn.input
        ),
      }
    }),
    responses: parts
      .filter(
        (part): part is ObjectValue =>
          record(part) && part.type === "tool_call_response"
      )
      .map((part) => ({
        id: text(part.id),
        content: part.response,
        raw: part,
      })),
    raw: value,
  }
}

function toolDefinition(message: Message): boolean {
  return (
    message.role === "tool" &&
    !message.resultId &&
    record(message.content) &&
    message.content.type === "function" &&
    record(message.content.function)
  )
}

/** Object key order and transport envelopes differ across message SDKs. Text is untouched. */
function stable(value: unknown): string {
  if (value === undefined) return "undefined"
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`
  if (record(value))
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable(value[key])}`)
      .join(",")}}`
  return JSON.stringify(value)
}

function signature(message: Message): string {
  return stable({
    role: message.role,
    content: message.content,
    resultId: message.resultId,
    calls: message.calls,
    responses: message.responses.map(({ id, content }) => ({ id, content })),
  })
}

function declaredTool(span: OTelSpan): boolean {
  const kind = resolveReportedInputKind(span.attributes ?? {})
  return kind === "TOOL" || kind === "MCP"
}

function agent(span: OTelSpan): boolean {
  return resolveReportedInputKind(span.attributes ?? {}) === "AGENT"
}

function modelSpan(span: OTelSpan): boolean {
  const kind = resolveReportedInputKind(span.attributes ?? {})
  return kind === "GENERATION" || kind === "LLM"
}

function operationPayload(span: OTelSpan): boolean {
  const kind = resolveReportedInputKind(span.attributes ?? {})
  return declaredTool(span) || kind === "EMBEDDING" || kind === "RETRIEVER"
}

function error(span: OTelSpan): boolean {
  const code = String(span.status?.code ?? "").toUpperCase()
  return (
    code === "2" ||
    code.endsWith("ERROR") ||
    span.attributes?.["langfuse.observation.level"] === "ERROR"
  )
}

function outcome(result: unknown, raw?: ObjectValue, span?: OTelSpan): Outcome {
  if (span && error(span)) return "error"
  const protocol = raw ? resolveToolOutcome(raw) : "unknown"
  const value = resolveToolOutcome(result)
  return protocol === "error" || value === "error"
    ? "error"
    : value === "ok" || protocol === "ok"
      ? "ok"
      : "unknown"
}

interface Observed {
  message: Message
  signature: string
  entries: TraceProcessEntry[]
}

interface Stream {
  history: Observed[]
  ids: Map<string, Observed>
  initialized: boolean
  definitions: Set<string>
}

/**
 * A deterministic reading projection, not a second trace or an inferred task summary.
 * Messages come from explicit model calls when present; wrapper rollups are excluded.
 * Only message IDs, same-stream contiguous history overlap and exact call IDs join records.
 */
export function buildTraceProcess(
  spans: readonly OTelSpan[],
  options: TraceProcessOptions = {}
): TraceProcessModel {
  const entries: TraceProcessEntry[] = []
  const context: TraceProcessMessage[] = []
  const unlinkedTools: TraceProcessEntry[] = []
  const byId = new Map(spans.map((span) => [span.span_id, span]))
  const streams = new Map<string, Stream>()
  const calls = new Map<string, TraceProcessEntry[]>()
  const callMessageIds = new Map<TraceProcessEntry, string | undefined>()
  let serial = 0
  let hasMessageCalls = false

  const callRole = (span: OTelSpan) => {
    const parent = span.parent_span_id
      ? byId.get(span.parent_span_id)
      : undefined
    return resolveModelCallRole(span, parent ? [parent] : []) ?? undefined
  }

  const auxiliaryBoundary = (span: OTelSpan, role: ModelCallRole): string => {
    const parent = span.parent_span_id
      ? byId.get(span.parent_span_id)
      : undefined
    if (
      parent?.trace_id === span.trace_id &&
      parent.name === "hermes.logical_llm_call" &&
      parent.attributes?.["nemo_relay.scope_type"] === "function"
    ) {
      const parentRole = callRole(parent)
      if (parentRole?.kind === "auxiliary" && parentRole.value === role.value)
        return parentRole.sourceSpanId
    }
    return role.sourceSpanId
  }

  // The recorded ancestor establishes stream separation; it does not label a subagent.
  const branch = (span: OTelSpan): string => {
    const role = callRole(span)
    if (role?.kind === "auxiliary") return auxiliaryBoundary(span, role)
    let current = span
    const visited = new Set<string>([span.span_id])
    while (current.parent_span_id) {
      const parent = byId.get(current.parent_span_id)
      if (!parent) return current.parent_span_id
      if (visited.has(parent.span_id)) return span.span_id
      visited.add(parent.span_id)
      const parentRole = callRole(parent)
      if (parentRole?.kind === "auxiliary")
        return auxiliaryBoundary(parent, parentRole)
      if (declaredTool(parent) || agent(parent)) return parent.span_id
      current = parent
    }
    return current.span_id
  }
  const keyForCall = (branchId: string, callId: string) =>
    `${branchId}\0${callId}`
  const addSource = (entry: TraceProcessEntry, source: TraceProcessSource) => {
    if (!entry.sources.some((existing) => stable(existing) === stable(source)))
      entry.sources.push(source)
  }
  const register = (entry: TraceProcessEntry) => {
    entries.push(entry)
    if (entry.callId) {
      const key = keyForCall(entry.branchSpanId!, entry.callId)
      calls.set(key, [...(calls.get(key) ?? []), entry])
    }
    return entry
  }
  const base = (span: OTelSpan, source: TraceProcessSource) => ({
    id: `process-${++serial}`,
    spanId: span.span_id,
    branchSpanId: branch(span),
    sources: [source],
    callRole: callRole(span),
  })

  const consume = (
    span: OTelSpan,
    field: "input" | "output",
    io: SpanIOValue,
    messages: ObjectValue[],
    snapshot = field === "input",
    evidence?: SpanMessageEvidence[]
  ) => {
    const branchId = branch(span)
    const stream: Stream = streams.get(branchId) ?? {
      history: [],
      ids: new Map(),
      initialized: false,
      definitions: new Set(),
    }
    streams.set(branchId, stream)
    const sourceFor = (
      index: number,
      callIndex?: number
    ): TraceProcessSource => {
      const message = evidence?.[index]
      const call =
        callIndex === undefined
          ? undefined
          : message?.toolCallSources?.[callIndex]
      return {
        spanId: span.span_id,
        field,
        messageIndex: message?.index ?? index,
        attributeKey:
          call?.attributeKey ?? message?.attributeKey ?? io.attributeKey,
        ...(call?.attributeKeys || message?.attributeKeys
          ? { attributeKeys: call?.attributeKeys ?? message?.attributeKeys }
          : {}),
      }
    }
    if (field === "input") {
      for (const definition of collectSpanToolDefinitions(
        span.attributes ?? {}
      )) {
        const key = stable(definition.value)
        if (stream.definitions.has(key)) continue
        stream.definitions.add(key)
        context.push({
          kind: "tool-definition",
          definitionName: definition.name,
          role: "tool",
          content: definition.value,
          source: {
            spanId: span.span_id,
            field,
            attributeKey: definition.attributeKey,
            definitionIndex: definition.index,
          },
        })
      }
    }
    const indexed = messages.map((raw, index) => ({
      message: normalize(raw),
      index,
    }))
    for (const { message, index } of indexed.filter(({ message }) =>
      toolDefinition(message)
    )) {
      const key = stable(message.content)
      if (!stream.definitions.has(key)) {
        stream.definitions.add(key)
        context.push({
          kind: "tool-definition",
          definitionName:
            record(message.content) && record(message.content.function)
              ? text(message.content.function.name)
              : undefined,
          role: message.role,
          content: message.content,
          source: sourceFor(index),
        })
      }
    }
    const conversation = indexed.filter(
      ({ message }) => !toolDefinition(message)
    )
    const incoming = conversation.map(({ message }) => message)
    const signatures = incoming.map(signature)
    const sameEvent = (observed: Observed, index: number) =>
      observed.signature === signatures[index] &&
      (!observed.message.id ||
        !incoming[index].id ||
        observed.message.id === incoming[index].id)
    let matched: Observed[] = []
    if (snapshot) {
      // A cumulative or rolling input snapshot can repeat an exact contiguous sequence.
      for (
        let size = Math.min(stream.history.length, incoming.length);
        size > 0;
        size--
      ) {
        const suffix = stream.history.slice(-size)
        if (suffix.every((item, index) => sameEvent(item, index))) {
          matched = suffix
          break
        }
      }
      // A shorter input can repeat the beginning of the same recorded history.
      if (
        !matched.length &&
        incoming.length &&
        incoming.length <= stream.history.length &&
        incoming.every((_item, index) =>
          sameEvent(stream.history[index], index)
        )
      )
        matched = stream.history.slice(0, incoming.length)
    }
    const lastUser = incoming.map((message) => message.role).lastIndexOf("user")
    const initialInput = field === "input" && !stream.initialized
    const model = getModelSpanPresentation(span).model ?? undefined
    incoming.forEach((message, index) => {
      const source = sourceFor(conversation[index].index)
      const identified = message.id ? stream.ids.get(message.id) : undefined
      const prior =
        matched[index] ??
        (identified?.signature === signatures[index] ? identified : undefined)
      if (prior) {
        if (message.id && !prior.message.id) {
          prior.message.id = message.id
          stream.ids.set(message.id, prior)
          if (message.role === "assistant")
            prior.entries.forEach((entry) => {
              if (entry.kind === "tool") callMessageIds.set(entry, message.id)
            })
        }
        prior.entries.forEach((entry) => addSource(entry, source))
        // Payload overlap does not erase a later explicit protocol failure.
        // Keep the same result content and attach the new source for inspection.
        if (message.role === "tool")
          prior.entries.forEach((entry) => {
            if (
              entry.kind === "tool" &&
              (outcome(message.content, message.raw) === "error" ||
                message.responses.some(
                  (response) =>
                    response.id === entry.callId &&
                    outcome(response.content, response.raw) === "error"
                ))
            )
              entry.outcome = "error"
          })
        return
      }
      const observed: Observed = {
        message,
        signature: signatures[index],
        entries: [],
      }
      stream.history.push(observed)
      if (message.id) stream.ids.set(message.id, observed)
      if (
        message.role === "system" ||
        message.role === "developer" ||
        (initialInput && lastUser >= 0 && index < lastUser)
      ) {
        context.push({ role: message.role, content: message.content, source })
        return
      }
      if (
        message.role === "tool" &&
        (message.resultId || message.responses.length)
      ) {
        const responses = message.responses.length
          ? message.responses
          : [
              {
                id: message.resultId,
                content: message.content,
                raw: message.raw,
              },
            ]
        for (const response of responses) {
          const candidates = response.id
            ? (calls.get(keyForCall(branchId, response.id)) ?? [])
            : []
          const target = candidates.length === 1 ? candidates[0] : undefined
          const reportedOutcome =
            outcome(undefined, message.raw) === "error"
              ? "error"
              : outcome(response.content, response.raw)
          if (
            target &&
            (target.result === undefined ||
              stable(target.result) === stable(response.content))
          ) {
            target.result = response.content
            if (reportedOutcome === "error" || target.outcome !== "error")
              target.outcome = reportedOutcome
            addSource(target, source)
            observed.entries.push(target)
          } else {
            observed.entries.push(
              register({
                ...base(span, source),
                kind: "tool",
                toolName: message.name,
                callId: response.id,
                result: response.content,
                outcome: reportedOutcome,
              })
            )
          }
        }
        return
      }
      if (
        message.role !== "assistant" ||
        message.calls.length === 0 ||
        (message.content !== undefined &&
          (typeof message.content !== "string" ||
            message.content.trim() !== ""))
      ) {
        observed.entries.push(
          register({
            ...base(span, source),
            kind: "message",
            role: message.role,
            content: message.content,
            ...(message.role === "assistant"
              ? {
                  model,
                  durationMs: nanoDurationMs(
                    span.start_time_unix_nano,
                    span.end_time_unix_nano
                  ),
                }
              : {}),
          })
        )
      }
      if (message.role === "assistant")
        message.calls.forEach((call, callIndex) => {
          const callSource = sourceFor(conversation[index].index, callIndex)
          hasMessageCalls = true
          const candidates = call.id
            ? (calls.get(keyForCall(branchId, call.id)) ?? [])
            : []
          const repeated = candidates.find(
            (entry) =>
              entry.toolName === call.name &&
              stable(entry.arguments) === stable(call.args) &&
              (!message.id ||
                !callMessageIds.get(entry) ||
                callMessageIds.get(entry) === message.id)
          )
          if (repeated) {
            addSource(repeated, callSource)
            observed.entries.push(repeated)
            return
          }
          const entry = register({
            ...base(span, callSource),
            kind: "tool",
            toolName: call.name,
            arguments: call.args,
            callId: call.id,
            outcome: "unknown",
            model,
          })
          callMessageIds.set(entry, message.id)
          observed.entries.push(entry)
        })
    })
    if (field === "input") stream.initialized = true
  }

  const modelSpans = spans.filter(modelSpan)
  const messageSpans = modelSpans.length
    ? modelSpans
    : spans.filter((span) => !operationPayload(span))
  const events = messageSpans.flatMap((span, spanIndex) =>
    (["input", "output"] as const).flatMap((field) => {
      const attrs = span.attributes ?? {}
      const evidence = projectSpanMessages(attrs, field)
      const definitions =
        field === "input" ? collectSpanToolDefinitions(attrs) : []
      const io =
        spanIO(attrs, field) ??
        (evidence.length
          ? {
              value: evidence.map((message) => message.value),
              attributeKey: evidence[0].attributeKey,
            }
          : definitions.length
            ? { value: undefined, attributeKey: definitions[0].attributeKey }
            : undefined)
      return io && (evidence.length || definitions.length)
        ? [
            {
              span,
              spanIndex,
              field,
              io,
              evidence,
              messages: evidence.map((message) => message.value),
              time: timestamp(
                field === "input"
                  ? span.start_time_unix_nano
                  : span.end_time_unix_nano
              ),
            },
          ]
        : []
    })
  )
  events.sort(
    (a, b) =>
      compareTimestamps(a.time, b.time) ||
      a.spanIndex - b.spanIndex ||
      (a.field === "input" ? -1 : 1)
  )
  events.forEach(({ span, field, io, messages, evidence }) =>
    consume(span, field, io, messages, field === "input", evidence)
  )

  const toolSpans = spans.filter(declaredTool)
  const invocationId = (span: OTelSpan) =>
    recordedToolCallId(span.attributes ?? {})

  const invocations = new Map<string, number>()
  for (const span of toolSpans) {
    const id = invocationId(span)
    if (id) {
      const key = keyForCall(branch(span), id)
      invocations.set(key, (invocations.get(key) ?? 0) + 1)
    }
  }
  for (const span of toolSpans) {
    const attrs = span.attributes ?? {}
    const callId = invocationId(span)
    const candidates = callId
      ? (calls.get(keyForCall(branch(span), callId)) ?? [])
      : []
    const input = spanIO(attrs, "input")
    const output = spanIO(attrs, "output")
    const target =
      candidates.length === 1 &&
      callId &&
      invocations.get(keyForCall(branch(span), callId)) === 1
        ? candidates[0]
        : undefined
    const separate = hasMessageCalls && !target
    const entry =
      target ??
      ({
        id: `process-${++serial}`,
        kind: "tool",
        spanId: span.span_id,
        branchSpanId: branch(span),
        sources: [],
        toolName: text(attrs["gen_ai.tool.name"]) ?? span.name,
        callId,
        arguments: input?.value,
        outcome: "unknown",
      } satisfies TraceProcessEntry)
    if (!target) {
      if (separate) unlinkedTools.push(entry)
      else register(entry)
    }
    // Duplicate tool call IDs cannot establish which invocation produced the result.
    if (entry.linkedToolSpanId && entry.linkedToolSpanId !== span.span_id)
      continue
    if (entry.toolName === undefined)
      entry.toolName = text(attrs["gen_ai.tool.name"]) ?? span.name
    if (entry.arguments === undefined && input) entry.arguments = input.value
    entry.linkedToolSpanId = span.span_id
    const omittedPaths = reportedOmittedPaths(span.resource)
    if (omittedPaths.length) entry.omittedPaths = omittedPaths
    entry.durationMs = nanoDurationMs(
      span.start_time_unix_nano,
      span.end_time_unix_nano
    )
    if (input)
      addSource(entry, {
        spanId: span.span_id,
        field: "input",
        attributeKey: input.attributeKey,
      })
    const rawResult =
      output && record(output.value) && messageRole(output.value) === "tool"
        ? body(output.value)
        : undefined
    const result =
      rawResult && Object.hasOwn(rawResult, "content")
        ? rawResult.content
        : output?.value
    if (entry.result === undefined && output) entry.result = result
    if (output)
      addSource(entry, {
        spanId: span.span_id,
        field: "output",
        attributeKey: output.attributeKey,
      })
    const reported = outcome(result, rawResult, span)
    if (reported === "error" || entry.outcome !== "error")
      entry.outcome = reported
  }

  // Only backend-owned, source-verified canonical output can add a bare assistant value.
  const outputSpan = options.outputSourceSpanId
    ? byId.get(options.outputSourceSpanId)
    : undefined
  const originalOutput = outputSpan
    ? spanIO(outputSpan.attributes ?? {}, "output")
    : undefined
  const traceOutputKey = "langfuse.trace.output"
  const outputKey =
    originalOutput && stable(originalOutput.value) === stable(options.output)
      ? originalOutput.attributeKey
      : outputSpan &&
          Object.hasOwn(outputSpan.attributes ?? {}, traceOutputKey) &&
          stable(decode(outputSpan.attributes?.[traceOutputKey])) ===
            stable(options.output)
        ? traceOutputKey
        : undefined
  if (
    outputSpan &&
    !operationPayload(outputSpan) &&
    outputKey &&
    options.output !== undefined &&
    ["available", "empty"].includes(options.outputState ?? "")
  ) {
    const canonicalEvidence =
      originalOutput?.attributeKey === outputKey
        ? projectSpanMessages(outputSpan.attributes ?? {}, "output")
        : undefined
    const canonicalMessages = canonicalEvidence
      ? canonicalEvidence.map((message) => message.value)
      : sequence(options.output)
    if (canonicalMessages.length) {
      consume(
        outputSpan,
        "output",
        { value: options.output, attributeKey: outputKey },
        canonicalMessages,
        true,
        canonicalEvidence
      )
    } else if (modelSpan(outputSpan)) {
      const source: TraceProcessSource = {
        spanId: outputSpan.span_id,
        field: "output",
        attributeKey: outputKey,
      }
      const branchId = branch(outputSpan)
      const lastAssistant = [...entries]
        .reverse()
        .find(
          (entry) =>
            entry.kind === "message" &&
            entry.role === "assistant" &&
            entry.branchSpanId === branchId
        )
      if (
        lastAssistant &&
        stable(lastAssistant.content) === stable(options.output)
      )
        addSource(lastAssistant, source)
      else
        register({
          ...base(outputSpan, source),
          kind: "message",
          role: "assistant",
          content: options.output,
        })
    }
  }
  if (!hasMessageCalls && toolSpans.length) {
    // Without recorded message requests, actual executions belong among the
    // message events at their own start times. Missing/invalid times sort last;
    // equal times retain the established event/source order. Keep nanoseconds.
    const times = new Map(
      entries.map((entry) => {
        const source = entry.sources[0]
        const span = byId.get(entry.linkedToolSpanId ?? source?.spanId ?? "")
        const value =
          entry.linkedToolSpanId || source?.field === "input"
            ? span?.start_time_unix_nano
            : span?.end_time_unix_nano
        return [entry, timestamp(value)] as const
      })
    )
    entries.sort((a, b) =>
      compareTimestamps(times.get(a) ?? null, times.get(b) ?? null)
    )
  }
  const represented = new Set([
    ...entries.flatMap((entry) => entry.sources.map((source) => source.spanId)),
    ...context.map((message) => message.source.spanId),
    ...unlinkedTools.flatMap((entry) =>
      entry.sources.map((source) => source.spanId)
    ),
    ...[...entries, ...unlinkedTools].flatMap((entry) =>
      entry.linkedToolSpanId ? [entry.linkedToolSpanId] : []
    ),
  ])
  return {
    entries,
    context,
    unlinkedTools,
    unrepresentedSpanCount: spans.filter(
      (span) => !represented.has(span.span_id)
    ).length,
  }
}
