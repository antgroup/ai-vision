/**
 * Pure derivation of the trace-compare page model from two real trace detail
 * responses plus the declared memory semantics. Recognition reuses the same
 * per-dimension entries as the trace page (decision/MCP/memory/skill/conversation); a span
 * never counts toward two groups — priority is decision > memory > MCP >
 * skill, so a memory-server tool call is counted once, as memory.
 *
 * Honest-degradation rules: missing usage stays null (the page renders "—"),
 * and memory hit entries only appear when the verified result shapes parse
 * (mirroring the backend `_memory_evidence`); unknown shapes degrade to the
 * bare hit count instead of guessed numbers.
 */
import {
  enrichDecisionSpans,
  resolveDecisionSpanInfos,
  type DecisionSpanInfo,
} from "@/components/labs/decision/decisionDisplay"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { collectTraceUserInput } from "@/components/traces/shared/traceUserInput"
import {
  resolveSpanContent,
  resolveToolSpanOutcome,
  type SpanContentModel,
} from "@/components/traces-otel/spanContent"
import { spanIO } from "@/components/traces-otel/spanIO"
import { sessionMessagePreview } from "@/components/traces-otel/sessionMessages"
import {
  nanoDurationMs,
  parseTimestampMs,
} from "@/components/traces-otel/format"
import { extractReportedSpanUsage } from "@/components/traces-otel/spanUsage"
import type {
  NativeTraceDetailResponse,
  OTelSpan,
} from "@/components/traces-otel/types"
import {
  memoryEvidenceFromResult,
  memoryResultPayload,
  declaredMemoryToolCategory,
  isMemorySpanCall,
  memoryHitLinesFromResult,
  type MemorySemanticsByServer,
} from "@/labs/memory/memoryDisplay"
import { nativeMemoryOperations } from "@/labs/memory/nativeMemory"
import {
  powerContextSpanStateKey,
  powerContextSpanStatus,
  resolvePowerContextSpan,
  type PowerContextSpanInfo,
} from "@/labs/memory/powerContextSpans"
import {
  ARGUMENT_KEYS,
  isErrorSpan,
  mcpCallDurationMs,
  resolveMcpSpanInfo,
  summarizeToolCallArguments,
} from "@/labs/mcp/mcpDisplay"
import {
  resolveSkillDetailFields,
  resolveSkillSpanInfo,
} from "@/labs/skill/skillDisplay"

export type CompareGroup =
  | "conversation"
  | "decision"
  | "mcp"
  | "memory"
  | "skill"

export interface CompareEnhancedCall {
  group: CompareGroup
  tool: string
  spanId: string
  /** Offset from the trace's first span start, for the shared timeline. */
  offsetMs: number | null
  durationMs: number | null
  status: "error" | "ok" | "unknown"
  argsSummary: string
  arguments: string | null
  description: string | null
  command: string | null
  /** Verified hit count for memory calls; null for the other groups. */
  hits: number | null
  conversation?: Extract<SpanContentModel, { kind: "conversation" }>
  modelPresentation?: ReturnType<typeof getModelSpanPresentation>
  powerContext?: PowerContextSpanInfo & {
    stateKey: ReturnType<typeof powerContextSpanStateKey>
  }
}

export interface CompareSideModel {
  traceId: string
  runCount: number
  wallMs: number | null
  startedAt: string | null
  model: string | null
  /** Last explicit user message or canonical plain-text root input. */
  task: string | null
  tokens: { total: number | null; input: number | null; output: number | null }
  calls: CompareEnhancedCall[]
  decisionCalls: CompareDecisionCall[]
}

export interface CompareDecisionCall {
  spanId: string
  durationMs: number | null
  tokens: number | null
  statusOk: boolean
  /** Shared call costs must not be repeated for each answered question. */
  judgments: {
    info: DecisionSpanInfo
    targetName: string | null
    targetSpanId: string | null
  }[]
}

export interface CompareMemoryEvidenceRow {
  sourceSpanId: string
  result: unknown
  category: ReturnType<typeof declaredMemoryToolCategory>
  outcome: "ok" | "unknown"
  entries: string[]
  sourceTool: string
  hits: number | null
  evidenceType: "search" | "list" | "note" | "write"
}

export interface CompareModel {
  sideA: CompareSideModel
  sideB: CompareSideModel
  /** Tool-returned evidence for each side, without cross-trace attribution. */
  memoryEvidence: Record<"A" | "B", CompareMemoryEvidenceRow[]>
}

const TASK_TEXT_LIMIT = 120

function attrText(
  attrs: Record<string, unknown> | undefined,
  key: string
): string | null {
  const value = attrs?.[key]
  return typeof value === "string" && value.trim() ? value.trim() : null
}

function spanStartMs(span: OTelSpan): number | null {
  return span.start_time_unix_nano
    ? parseTimestampMs(span.start_time_unix_nano)
    : null
}

function spanEndMs(span: OTelSpan): number | null {
  return span.end_time_unix_nano
    ? parseTimestampMs(span.end_time_unix_nano)
    : null
}

function findRootSpan(detail: NativeTraceDetailResponse): OTelSpan | null {
  const { spans, semantics } = detail
  if (!spans.length) return null
  const rootId = semantics?.rootObservationId
  if (rootId) {
    const byId = spans.find((span) => span.span_id === rootId)
    if (byId) return byId
  }
  const ids = new Set(spans.map((span) => span.span_id))
  return (
    spans.find(
      (span) => !span.parent_span_id || !ids.has(span.parent_span_id)
    ) ?? spans[0]
  )
}

function taskInputText(
  detail: NativeTraceDetailResponse,
  root: OTelSpan | null
): string | null {
  const sources = collectTraceUserInput([
    {
      id: root?.span_id ?? detail.traceId,
      name: root?.name ?? "",
      input: detail.semantics?.input ?? undefined,
      attributes: root?.attributes,
      isRoot: true,
    },
  ])
  const lastUser = sources[0]?.messages.at(-1)
  // An image-only latest user message must not restore an earlier text task.
  if (lastUser) return lastUser.text?.trim() || null
  const input = detail.semantics?.input
  // Preserve canonical plain-text root input, but never infer a role from
  // arbitrary object fields or display serialized messages as a task.
  if (typeof input !== "string") return null
  const text = input.trim()
  if (!text) return null
  try {
    const decoded: unknown = JSON.parse(text)
    if (decoded && typeof decoded === "object") return null
  } catch {
    // A literal prompt such as "[Bug] fix the form" remains canonical text.
  }
  return text
}

function truncate(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

function sideWallMs(spans: OTelSpan[]): number | null {
  let min: number | null = null
  let max: number | null = null
  for (const span of spans) {
    const start = spanStartMs(span)
    const end = spanEndMs(span)
    // A partial envelope is not the complete trace duration.
    if (start === null || end === null || end < start) return null
    if (min === null || start < min) min = start
    if (max === null || end > max) max = end
  }
  return min !== null && max !== null ? max - min : null
}

/** Earliest span start (epoch ms) — the per-side timeline origin T+0. */
function sideOriginMs(spans: OTelSpan[]): number | null {
  let min: number | null = null
  for (const span of spans) {
    const start = spanStartMs(span)
    if (start !== null && (min === null || start < min)) min = start
  }
  return min
}

function recognizeSpan(
  span: OTelSpan,
  declared: MemorySemanticsByServer
): CompareEnhancedCall | null {
  const attrs = span.attributes
  const start = spanStartMs(span)
  const base = {
    spanId: span.span_id,
    offsetMs: start,
    durationMs: mcpCallDurationMs(span),
    status: resolveToolSpanOutcome(
      span,
      attrs?.["gen_ai.tool.call.result"] ??
        attrs?.["langfuse.observation.output"]
    ),
    description: null,
    command: null,
    arguments:
      ARGUMENT_KEYS.map((key) => {
        const value = attrs?.[key]
        if (typeof value === "string") return value.trim() || null
        return value && typeof value === "object"
          ? JSON.stringify(value, null, 2)
          : null
      }).find((value) => value !== null) ?? null,
  }
  // Priority: decision > memory > MCP > skill — first match wins so a
  // memory-server tool call is attributed once, as memory.
  if (attrs) {
    const decisionInfos = resolveDecisionSpanInfos(attrs)
    if (decisionInfos.length > 0) {
      const info = decisionInfos[0]
      return {
        ...base,
        group: "decision",
        tool: info.type ?? span.name,
        argsSummary: info.choice ?? info.applied ?? "",
        hits: null,
      }
    }
    const mcp = resolveMcpSpanInfo(attrs)
    if (mcp) {
      const result = attrs["gen_ai.tool.call.result"]
      const evidence =
        base.status === "error"
          ? null
          : memoryEvidenceFromResult(
              result,
              declaredMemoryToolCategory(declared, mcp.serverId, mcp.toolName)
            )
      // Config-declared servers always count; undeclared ones join only on a
      // verified memory result shape (the no-config fallback).
      if (
        evidence ||
        isMemorySpanCall(
          mcp,
          declared,
          undefined,
          attrs["agentic.tool.category"]
        )
      ) {
        return {
          ...base,
          group: "memory",
          tool: mcp.toolName ?? "-",
          argsSummary: summarizeToolCallArguments(attrs) ?? "",
          description: mcp.description,
          command: null,
          hits: evidence?.hits ?? null,
        }
      }
      return {
        ...base,
        group: "mcp",
        tool: mcp.toolName ?? mcp.serverId ?? span.name,
        argsSummary: summarizeToolCallArguments(attrs) ?? "",
        description: mcp.description,
        command: null,
        hits: null,
      }
    }
    const skill = resolveSkillSpanInfo(attrs)
    if (skill) {
      const skillDetails = resolveSkillDetailFields(attrs)
      let command = skillDetails?.rawArguments ?? base.arguments
      if (command) {
        try {
          const payload: unknown = JSON.parse(command)
          if (
            payload &&
            typeof payload === "object" &&
            !Array.isArray(payload)
          ) {
            const fields = payload as Record<string, unknown>
            command =
              [fields.cmd, fields.command, fields.invocation].find(
                (value): value is string =>
                  typeof value === "string" && value.trim().length > 0
              ) ?? command
          }
        } catch {
          // Plain shell commands already contain the display value.
        }
      }
      return {
        ...base,
        group: "skill",
        tool: skill.name ?? span.name,
        argsSummary: summarizeToolCallArguments(attrs) ?? "",
        description:
          attrText(attrs, "agentic.skill.description") ??
          attrText(attrs, "gen_ai.tool.description"),
        command,
        hits: null,
      }
    }
    const powerContext = resolvePowerContextSpan(span)
    if (powerContext) {
      const status = powerContextSpanStatus(span)
      return {
        ...base,
        group: "memory",
        tool: span.name,
        durationMs: nanoDurationMs(
          span.start_time_unix_nano,
          span.end_time_unix_nano
        ),
        status,
        argsSummary: "",
        hits: null,
        powerContext: {
          ...powerContext,
          stateKey: powerContextSpanStateKey(span, powerContext, status),
        },
      }
    }
    const output = spanIO(attrs, "output")?.value
    const conversation = resolveSpanContent(
      span,
      spanIO(attrs, "input")?.value,
      output
    )
    if (conversation?.kind === "conversation") {
      return {
        ...base,
        group: "conversation",
        tool: span.name,
        status: resolveToolSpanOutcome(span, output),
        argsSummary: truncate(
          sessionMessagePreview(conversation.user.value) ?? "",
          TASK_TEXT_LIMIT
        ),
        hits: null,
        conversation,
        modelPresentation: getModelSpanPresentation(span),
      }
    }
  }
  return null
}

function comparisonMemoryNodes(
  spans: OTelSpan[],
  calls: CompareEnhancedCall[]
) {
  const recalls = new Set(
    calls
      .filter((call) => call.powerContext?.operation === "recall")
      .map((call) => call.spanId)
  )
  const selected = new Set(recalls)
  const searchHits = new Map<string, number>()
  const byId = new Map(spans.map((span) => [span.span_id, span]))
  const hasAncestor = (
    span: OTelSpan,
    matches: (ancestor: OTelSpan) => boolean
  ) => {
    const seen = new Set([span.span_id])
    let parentId = span.parent_span_id
    while (parentId && !seen.has(parentId)) {
      const parent = byId.get(parentId)
      if (parent?.trace_id !== span.trace_id) return false
      if (matches(parent)) return true
      seen.add(parentId)
      parentId = parent.parent_span_id
    }
    return false
  }
  // Show the recorded outcome and its measured search. The native business
  // call resolver already selects one source boundary across SDK/HTTP/server.
  for (const { span, operation, searchSpan, hits } of nativeMemoryOperations(
    spans
  )) {
    if (
      operation !== "prepare_context" ||
      !hasAncestor(span, (ancestor) => recalls.has(ancestor.span_id))
    )
      selected.add(span.span_id)
    if (
      searchSpan &&
      hits !== null &&
      !hasAncestor(
        searchSpan,
        (ancestor) =>
          resolvePowerContextSpan(ancestor) !== null &&
          powerContextSpanStatus(ancestor) === "error"
      )
    ) {
      selected.add(searchSpan.span_id)
      searchHits.set(searchSpan.span_id, hits)
    }
  }
  return calls
    .filter((call) => !call.powerContext || selected.has(call.spanId))
    .map((call) => {
      const hits = searchHits.get(call.spanId)
      return call.powerContext && hits !== undefined ? { ...call, hits } : call
    })
}

function buildSideModel(
  detail: NativeTraceDetailResponse,
  declared: MemorySemanticsByServer
): CompareSideModel {
  const root = findRootSpan(detail)
  const models = [
    ...new Set(
      detail.spans.flatMap((span) => {
        const model = getModelSpanPresentation(span).model
        return model ? [model] : []
      })
    ),
  ]
  const inputTextValue = taskInputText(detail, root)
  const usage = detail.semantics?.usage
  const calls: CompareEnhancedCall[] = []
  const decisionCalls: CompareDecisionCall[] = []
  const decisionContexts = enrichDecisionSpans(detail.spans)
  const seenDecisions = new Set<string>()
  for (const span of detail.spans) {
    const contexts = decisionContexts.get(span.span_id)
    // Match the native decision summary's innermost-call deduplication.
    if (
      resolveDecisionSpanInfos(span.attributes ?? {}).length > 0 &&
      (!contexts || seenDecisions.has(span.span_id))
    )
      continue
    if (contexts) seenDecisions.add(span.span_id)
    const call = recognizeSpan(span, declared)
    if (call) calls.push(call)
    const judgments = contexts
      ?.filter(({ info }) =>
        info.provider
          ? ["jev", "typesafe"].includes(info.provider.toLowerCase())
          : /^jev(?:[-.]|$)/i.test(info.model ?? "")
      )
      .map(({ info, target }) => ({
        info,
        targetName: target?.name ?? null,
        targetSpanId: target?.span_id ?? null,
      }))
    if (judgments?.length) {
      decisionCalls.push({
        spanId: span.span_id,
        durationMs: mcpCallDurationMs(span),
        tokens: extractReportedSpanUsage(span.attributes).totalTokens,
        statusOk: !isErrorSpan(span),
        judgments,
      })
    }
  }
  const spanOrder = new Map(
    detail.spans.map((span) => [span.span_id, spanStartMs(span) ?? Infinity])
  )
  decisionCalls.sort(
    (a, b) => spanOrder.get(a.spanId)! - spanOrder.get(b.spanId)!
  )
  // recognizeSpan records raw epoch starts; rebase to the side's first span
  // start so timeline blocks sit inside the [0, wallMs] lane (absolute
  // values would clamp every block to the right edge with a nonsense T+).
  const originMs = sideOriginMs(detail.spans)
  const displayCalls = calls.some((call) => call.powerContext)
    ? comparisonMemoryNodes(detail.spans, calls)
    : calls
  if (originMs !== null) {
    for (const call of displayCalls) {
      if (call.offsetMs !== null) call.offsetMs -= originMs
    }
  }
  displayCalls.sort(
    (x, y) => (x.offsetMs ?? Infinity) - (y.offsetMs ?? Infinity)
  )
  return {
    traceId: detail.traceId,
    runCount: detail.spans.length,
    wallMs: sideWallMs(detail.spans),
    startedAt: root
      ? spanStartMs(root) !== null
        ? new Date(spanStartMs(root)!).toISOString()
        : null
      : null,
    model: models.length > 0 ? models.join(" · ") : null,
    task: inputTextValue
      ? truncate(inputTextValue, TASK_TEXT_LIMIT)
      : (root?.name ?? null),
    tokens: {
      total: usage?.total ?? null,
      input: usage?.input ?? null,
      output: usage?.output ?? null,
    },
    calls: displayCalls,
    decisionCalls,
  }
}

/** Returned Memory content is evidence of a result, never of saved work. */
function buildMemoryEvidenceRows(
  detail: NativeTraceDetailResponse,
  calls: CompareEnhancedCall[],
  declared: MemorySemanticsByServer
): CompareMemoryEvidenceRow[] {
  const memoryOutcomes = new Map(
    calls
      .filter((call) => call.group === "memory" && call.status !== "error")
      .map((call) => [call.spanId, call.status] as const)
  )
  return detail.spans.flatMap((span): CompareMemoryEvidenceRow[] => {
    if (!memoryOutcomes.has(span.span_id)) return []
    const attrs = span.attributes
    const mcp = resolveMcpSpanInfo(attrs ?? {})
    if (!mcp) return []
    const result = attrs?.["gen_ai.tool.call.result"]
    const evidence = memoryEvidenceFromResult(
      result,
      declaredMemoryToolCategory(declared, mcp.serverId, mcp.toolName)
    )
    if (!evidence) return []
    const payload = memoryResultPayload(result)
    const entries =
      evidence.type === "search"
        ? memoryHitLinesFromResult(result).map((line) => line.content)
        : (evidence.type === "note" || evidence.type === "write") &&
            typeof payload === "string"
          ? [
              payload.replace(/^Working note recorded[\s:：]*/i, "").trim(),
            ].filter(Boolean)
          : evidence.type === "write" && payload && typeof payload === "object"
            ? [JSON.stringify(payload, null, 2)]
            : []
    return [
      {
        sourceSpanId: span.span_id,
        result,
        category: declaredMemoryToolCategory(
          declared,
          mcp.serverId,
          mcp.toolName
        ),
        outcome: memoryOutcomes.get(span.span_id) === "ok" ? "ok" : "unknown",
        sourceTool: mcp.toolName ?? "—",
        entries,
        hits: evidence.hits ?? null,
        evidenceType: evidence.type,
      },
    ]
  })
}

export function buildCompareModel(
  sideADetail: NativeTraceDetailResponse,
  sideBDetail: NativeTraceDetailResponse,
  declared: MemorySemanticsByServer = {}
): CompareModel {
  const sideA = buildSideModel(sideADetail, declared)
  const sideB = buildSideModel(sideBDetail, declared)
  return {
    sideA,
    sideB,
    memoryEvidence: {
      A: buildMemoryEvidenceRows(sideADetail, sideA.calls, declared),
      B: buildMemoryEvidenceRows(sideBDetail, sideB.calls, declared),
    },
  }
}
