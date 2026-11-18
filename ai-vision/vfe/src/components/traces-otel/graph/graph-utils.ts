import type { OTelSpan } from "../types"
import { nanoDurationMs, parseTimestampMs } from "../format"
import { extractReportedSpanUsage } from "../spanUsage"
import { isErrorSpan } from "@/labs/mcp/mcpDisplay"
import { resolveSpanKind, type SpanKind } from "../spanKind"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import {
  enrichDecisionSpans,
  type DecisionSpanInfo,
} from "@/components/labs/decision/decisionDisplay"
import {
  resolvePowerContextSpan,
  type PowerContextSpanInfo,
} from "@/labs/memory/powerContextSpans"

export interface GraphNode {
  id: string
  type: string
  name: string
  operation?: ReturnType<typeof getModelSpanPresentation>["operation"]
  labelKey?: ReturnType<typeof getModelSpanPresentation>["labelKey"]
  model?: string | null
  latency: number | null
  level: string
  tokens: number | null
  hasUsage: boolean
  startTime: number | null
  endTime: number | null
  decisions?: DecisionSpanInfo[]
  powerContext?: PowerContextSpanInfo | null
  filterContext?: boolean
  searchActive?: boolean
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  edgeType: "parent-child"
}

const GRAPH_TYPE_LABELS: Record<SpanKind, string> = {
  generation: "LLM",
  agent: "Agent",
  tool: "Tool",
  workflow: "Workflow",
  task: "Task",
  chain: "Chain",
  retriever: "Retriever",
  embedding: "Embedding",
  event: "Event",
  span: "Span",
  skill: "Skill",
  mcp: "MCP",
  routing: "Decision",
  approval: "Decision",
  decision: "Decision",
}

export function spansToGraph(spans: OTelSpan[]): {
  nodes: GraphNode[]
  edges: GraphEdge[]
} {
  const decisionContexts = enrichDecisionSpans(spans)
  const nodes: GraphNode[] = spans.map((s) => {
    const attrs = s.attributes ?? {}
    const decisions =
      decisionContexts.get(s.span_id)?.map(({ info }) => info) ?? []
    const kind = resolveSpanKind(s)
    const powerContext =
      !decisions.length && kind !== "mcp" && kind !== "skill"
        ? resolvePowerContextSpan(s)
        : null
    const presentation =
      !decisions.length && !powerContext && kind !== "mcp" && kind !== "skill"
        ? getModelSpanPresentation(s)
        : null
    const usage = extractReportedSpanUsage(attrs)
    const durationMs = nanoDurationMs(
      s.start_time_unix_nano,
      s.end_time_unix_nano
    )
    const startMs = s.start_time_unix_nano
      ? parseTimestampMs(s.start_time_unix_nano)
      : null
    const endMs = s.end_time_unix_nano
      ? parseTimestampMs(s.end_time_unix_nano)
      : null
    return {
      id: s.span_id,
      type: decisions.length ? "Decision" : GRAPH_TYPE_LABELS[kind ?? "span"],
      name: s.name,
      operation: presentation?.operation ?? null,
      labelKey: presentation?.labelKey ?? null,
      model: presentation?.model ?? null,
      latency: durationMs !== null ? durationMs / 1000 : null,
      level: isErrorSpan(s) ? "ERROR" : "OK",
      tokens: usage.totalTokens,
      hasUsage:
        !!presentation?.operation ||
        Object.values(usage).some((value) => value !== null),
      startTime: startMs,
      endTime: endMs,
      decisions,
      powerContext,
    }
  })

  // Time orders nodes for a stable layout; it is not evidence of a dependency.
  // Unknown timestamps follow reported times, retaining input order for ties.
  nodes.sort((a, b) => {
    if (a.startTime === b.startTime) return 0
    if (a.startTime === null) return 1
    if (b.startTime === null) return -1
    return a.startTime - b.startTime
  })

  const edges: GraphEdge[] = []
  const parentIds = new Map(spans.map((s) => [s.span_id, s.parent_span_id]))

  // Dagre also uses edge insertion order when arranging sibling nodes.
  for (const node of nodes) {
    const parentId = parentIds.get(node.id)
    if (parentId && parentIds.has(parentId)) {
      edges.push({
        id: `pc-${parentId}-${node.id}`,
        source: parentId,
        target: node.id,
        edgeType: "parent-child",
      })
    }
  }

  return { nodes, edges }
}
