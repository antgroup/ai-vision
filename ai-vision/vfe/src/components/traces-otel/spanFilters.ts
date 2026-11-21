import { isErrorSpan } from "@/labs/mcp/mcpDisplay"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { nanoDurationMs } from "./format"
import { resolveToolOutcome } from "./spanContent"
import { resolveSpanKind, type SpanKind } from "./spanKind"
import { extractReportedSpanUsage } from "./spanUsage"
import {
  matchesSpanCostThreshold,
  type SpanCostExchangeRates,
  type SpanCostThreshold,
} from "./spanCostFilter.ts"
import type { OTelSpan, SpanTreeNode } from "./types"

export type SpanFilterKind = SpanKind | "unknown"

export interface SpanFilters {
  errorOnly: boolean
  hideCompletedTurnToolErrors?: boolean
  lowConfidenceOnly: boolean
  kinds: readonly SpanFilterKind[]
  toolNames: readonly string[]
  toolNameMode: "include" | "exclude"
  minDurationMs: number | null
  minTokens: number | null
  minCost?: SpanCostThreshold | null
  tokenMetric: "total" | "input" | "output"
  model: string
}

export function createSpanFilters(): SpanFilters {
  return {
    errorOnly: false,
    lowConfidenceOnly: false,
    kinds: [],
    toolNames: [],
    toolNameMode: "include",
    minDurationMs: null,
    minTokens: null,
    minCost: null,
    tokenMetric: "total",
    model: "",
  }
}

export function hasSpanFilters(filters: SpanFilters): boolean {
  return (
    filters.errorOnly ||
    filters.hideCompletedTurnToolErrors === true ||
    filters.lowConfidenceOnly ||
    filters.kinds.length > 0 ||
    filters.toolNames.length > 0 ||
    filters.minDurationMs !== null ||
    filters.minTokens !== null ||
    (filters.minCost !== null && filters.minCost !== undefined) ||
    filters.model !== ""
  )
}

/** Hide only explicitly declared tool errors under a verified successful root. */
export function getCompletedTurnToolErrorIds(
  spans: readonly OTelSpan[],
  rootSpanId: string,
  tree: readonly SpanTreeNode[]
): Set<string> {
  const result = new Set<string>()
  const root = spans.find((span) => span.span_id === rootSpanId)
  if (
    !root ||
    (root.parent_span_id && !/^0+$/.test(root.parent_span_id)) ||
    isErrorSpan(root) ||
    !["1", "OK", "STATUS_CODE_OK"].includes(
      String(root.status?.code ?? "").toUpperCase()
    )
  )
    return result
  const rootSubtreeIds = getSpanSubtreeIds(tree, rootSpanId)
  for (const span of spans) {
    if (
      span.span_id === rootSpanId ||
      !rootSubtreeIds.has(span.span_id) ||
      !isErrorSpan(span)
    )
      continue
    const attrs = span.attributes ?? {}
    const is = (value: unknown, ...choices: string[]) =>
      typeof value === "string" && choices.includes(value.trim().toLowerCase())
    const operation =
      typeof attrs["gen_ai.operation.name"] === "string"
        ? attrs["gen_ai.operation.name"].trim().toLowerCase()
        : ""
    if (
      operation === "execute_tool" ||
      operation === "tool" ||
      (!operation &&
        (is(attrs["gen_ai.span.kind"], "tool", "mcp") ||
          is(attrs["langfuse.observation.type"], "tool")))
    )
      result.add(span.span_id)
  }
  return result
}

/** A tool name is evidence only on a declared tool call, not a wrapper or decision. */
function reportedToolName(span: OTelSpan): string | null {
  const attrs = span.attributes ?? {}
  const value = attrs["gen_ai.tool.name"]
  if (typeof value !== "string" || !value.trim()) return null
  const kind = resolveSpanKind(span)
  if (kind !== "tool" && kind !== "mcp" && kind !== "skill") return null
  const text = (key: string) =>
    typeof attrs[key] === "string" ? attrs[key].trim().toLowerCase() : ""
  const operation = text("gen_ai.operation.name")
  if (operation) {
    if (operation !== "execute_tool" && operation !== "tool") return null
  } else {
    const declaredKind =
      text("gen_ai.span.kind") || text("langfuse.observation.type")
    if (declaredKind !== "tool" && declaredKind !== "mcp") return null
  }
  return value.trim()
}

/** Each dimension constrains the same original span, never its descendants. */
export function filterSpans(
  spans: readonly OTelSpan[],
  filters: SpanFilters,
  context: {
    searchMatchIds?: ReadonlySet<string>
    lowConfidenceSpanIds?: ReadonlySet<string>
    scopeSpanIds?: ReadonlySet<string>
    completedTurnToolErrorIds?: ReadonlySet<string>
    exchangeRates?: SpanCostExchangeRates
  } = {}
): Set<string> {
  const matches = new Set<string>()
  for (const span of spans) {
    if (context.scopeSpanIds && !context.scopeSpanIds.has(span.span_id))
      continue
    if (context.searchMatchIds && !context.searchMatchIds.has(span.span_id))
      continue
    if (filters.errorOnly && !isErrorSpan(span)) continue
    if (
      filters.hideCompletedTurnToolErrors &&
      context.completedTurnToolErrorIds?.has(span.span_id)
    )
      continue
    if (
      filters.minCost !== null &&
      filters.minCost !== undefined &&
      !matchesSpanCostThreshold(span, filters.minCost, context.exchangeRates)
    )
      continue
    if (
      filters.lowConfidenceOnly &&
      !context.lowConfidenceSpanIds?.has(span.span_id)
    )
      continue
    if (filters.toolNames.length > 0) {
      const toolName = reportedToolName(span)
      const namedToolMatches =
        toolName !== null && filters.toolNames.includes(toolName)
      if (
        filters.toolNameMode === "exclude"
          ? namedToolMatches
          : !namedToolMatches
      )
        continue
    }
    if (
      filters.kinds.length > 0 &&
      !filters.kinds.includes(resolveSpanKind(span) ?? "unknown")
    )
      continue
    if (filters.minDurationMs !== null) {
      const duration = nanoDurationMs(
        span.start_time_unix_nano,
        span.end_time_unix_nano
      )
      if (
        duration === null ||
        !Number.isFinite(filters.minDurationMs) ||
        filters.minDurationMs < 0 ||
        duration < filters.minDurationMs
      )
        continue
    }
    if (filters.model || filters.minTokens !== null) {
      // The presentation resolver honors explicit non-model operations and
      // does not turn incidental model metadata or a name heuristic into a call.
      const model = getModelSpanPresentation(span)
      if (!model.operation) continue
      if (filters.model && model.model !== filters.model) continue
      if (filters.minTokens !== null) {
        const usage = extractReportedSpanUsage(span.attributes)
        const tokens =
          filters.tokenMetric === "input"
            ? usage.inputTokens
            : filters.tokenMetric === "output"
              ? usage.outputTokens
              : usage.totalTokens
        if (
          tokens === null ||
          !Number.isFinite(filters.minTokens) ||
          filters.minTokens < 0 ||
          tokens < filters.minTokens
        )
          continue
      }
    }
    matches.add(span.span_id)
  }
  return matches
}

/** Trace-local scope from the original tree; IDs and source nodes remain intact. */
export function getSpanSubtreeIds(
  tree: readonly SpanTreeNode[],
  spanId: string
): Set<string> {
  const pending = [...tree].reverse()
  const visited = new Set<SpanTreeNode>()
  let selected: SpanTreeNode | undefined
  while (pending.length) {
    const node = pending.pop()!
    if (visited.has(node)) continue
    visited.add(node)
    if (node.spanId === spanId) {
      selected = node
      break
    }
    pending.push(...[...node.children].reverse())
  }
  const ids = new Set<string>()
  if (!selected) return ids
  pending.length = 0
  pending.push(selected)
  visited.clear()
  while (pending.length) {
    const node = pending.pop()!
    if (visited.has(node)) continue
    visited.add(node)
    ids.add(node.spanId)
    pending.push(...[...node.children].reverse())
  }
  return ids
}

/** Ancestors provide context; their attributes do not count as query matches. */
export function filterSpanTree(
  tree: readonly SpanTreeNode[],
  matches: ReadonlySet<string>
): SpanTreeNode[] {
  return tree.flatMap((node) => {
    const children = filterSpanTree(node.children, matches)
    return matches.has(node.spanId) || children.length > 0
      ? [{ spanId: node.spanId, children }]
      : []
  })
}

export function getSpanFilterOptions(spans: readonly OTelSpan[]): {
  kinds: SpanFilterKind[]
  models: string[]
  toolNames: string[]
} {
  const kinds = new Set<SpanFilterKind>()
  const models = new Set<string>()
  const toolNames = new Set<string>()
  for (const span of spans) {
    kinds.add(resolveSpanKind(span) ?? "unknown")
    const model = getModelSpanPresentation(span)
    if (model.operation && model.model) models.add(model.model)
    const toolName = reportedToolName(span)
    if (toolName) toolNames.add(toolName)
  }
  return {
    kinds: [...kinds].sort(),
    models: [...models].sort(),
    toolNames: [...toolNames].sort(),
  }
}

export interface SpanRepeatGroup {
  id: string
  parentId: string
  name: string
  spanIds: string[]
}

const NON_COMPACT_TOOL =
  /(?:^|[^a-z])(?:edit|write|patch|apply|delete|remove|rm|move|mv|create|update|insert|replace|deploy|commit|push|test|tests|pytest|unittest|verify|validate|build|shell|bash|exec|execute|command|terminal|run|eval)(?:[^a-z]|$)/i
const RESULT_KEYS = [
  "gen_ai.tool.call.result",
  "gen_ai.output.messages",
  "langfuse.observation.output",
  "output.value",
  "aivision.request.output",
]
const ARGUMENT_KEYS = [
  "agentic.tool.call.arguments",
  "gen_ai.tool.call.arguments",
  "gen_ai.tool.call.input",
  "input.value",
  "langfuse.observation.input",
]

function plainToolName(
  span: OTelSpan | undefined,
  node: SpanTreeNode,
  parentId: string
): string | null {
  if (
    !span ||
    node.children.length ||
    span.parent_span_id !== parentId ||
    resolveSpanKind(span) !== "tool" ||
    isErrorSpan(span) ||
    span.events?.length ||
    span.links?.length
  )
    return null
  const attrs = span.attributes ?? {}
  // A non-error/UNSET status is not evidence that an operation succeeded.
  if (
    !["1", "OK", "STATUS_CODE_OK"].includes(
      String(span.status?.code ?? "").toUpperCase()
    )
  )
    return null
  if (
    attrs["agentic.tool.source.type"] ||
    attrs["agentic.tool.source.status"] ||
    attrs["gen_ai.tool.call.error"] ||
    attrs["error.type"] ||
    RESULT_KEYS.some((key) => resolveToolOutcome(attrs[key]) === "error")
  )
    return null
  const name =
    typeof attrs["gen_ai.tool.name"] === "string"
      ? attrs["gen_ai.tool.name"].trim()
      : span.name.trim()
  if (
    !name ||
    NON_COMPACT_TOOL.test(name.replace(/([a-z0-9])([A-Z])/g, "$1 $2")) ||
    NON_COMPACT_TOOL.test(span.name.replace(/([a-z0-9])([A-Z])/g, "$1 $2"))
  )
    return null
  for (const key of ARGUMENT_KEYS) {
    let value = attrs[key]
    if (value === undefined || value === null) continue
    if (typeof value === "string") {
      try {
        value = JSON.parse(value)
      } catch {
        return null
      }
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return null
    if (
      Object.keys(value).some((field) =>
        /^(?:cmd|command|commands|script|code)$/i.test(field)
      )
    )
      return null
  }
  return name
}

/** Pure presentation groups. Call on the original tree so filtering cannot bridge a boundary. */
export function getSpanRepeatGroups(
  tree: readonly SpanTreeNode[],
  spanById: ReadonlyMap<string, OTelSpan>
): SpanRepeatGroup[] {
  const groups: SpanRepeatGroup[] = []
  function visit(nodes: readonly SpanTreeNode[], parentId: string | null) {
    let run: SpanRepeatGroup | null = null
    let sourceName: string | undefined
    const flush = () => {
      if (run && run.spanIds.length > 1) groups.push(run)
      run = null
    }
    for (const node of nodes) {
      const span = spanById.get(node.spanId)
      const name =
        parentId === null ? null : plainToolName(span, node, parentId)
      if (name && run && run.name === name && sourceName === span?.name)
        run.spanIds.push(node.spanId)
      else {
        flush()
        run =
          name && parentId !== null
            ? {
                id: `repeat:${node.spanId}`,
                parentId,
                name,
                spanIds: [node.spanId],
              }
            : null
        sourceName = span?.name
      }
      visit(node.children, node.spanId)
    }
    flush()
  }
  visit(tree, null)
  return groups
}
