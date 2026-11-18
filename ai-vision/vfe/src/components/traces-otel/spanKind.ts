import type { OTelSpan } from "./types"
import { resolveDecisionSpanInfo } from "@/components/labs/decision/decisionDisplay"
import { resolveMcpSpanInfo } from "@/labs/mcp/mcpDisplay"
import { resolveSkillSpanInfo } from "@/labs/skill/skillDisplay"

const SPAN_KINDS = [
  "generation",
  "agent",
  "tool",
  "workflow",
  "task",
  "chain",
  "retriever",
  "embedding",
  "event",
  "span",
  "skill",
  "mcp",
  "routing",
  "approval",
  "decision",
] as const

export type SpanKind = (typeof SPAN_KINDS)[number]

const LANGFUSE_TYPE_MAP: Record<string, SpanKind> = {
  GENERATION: "generation",
  SPAN: "span",
  EVENT: "event",
  TOOL: "tool",
  AGENT: "agent",
  CHAIN: "chain",
  RETRIEVER: "retriever",
  EMBEDDING: "embedding",
}

const GEN_AI_KIND_MAP: Record<string, SpanKind> = {
  llm: "generation",
  chat: "span",
  mcp: "tool",
  reranker: "chain",
  guardrail: "span",
  generation: "generation",
  agent: "agent",
  tool: "tool",
  chain: "chain",
  retriever: "retriever",
  embedding: "embedding",
  workflow: "workflow",
  task: "task",
  span: "span",
  event: "event",
  skill: "skill",
}

const GEN_AI_OP_MAP: Record<string, SpanKind> = {
  invoke_agent: "agent",
  execute_tool: "tool",
  chat: "generation",
  embed: "embedding",
  embedding: "embedding",
  embeddings: "embedding",
  create_embedding: "embedding",
  rerank: "chain",
}

const OTEL_KIND_MAP: Record<string, SpanKind> = {
  "1": "span", // INTERNAL
  "2": "span", // SERVER
  "3": "span", // CLIENT
  "4": "event", // PRODUCER
  "5": "event", // CONSUMER
}

/** Keep native Chain and Graph classification in the same precedence order. */
export function resolveSpanKind(span: OTelSpan): SpanKind | null {
  const decision = span.attributes
    ? resolveDecisionSpanInfo(span.attributes)
    : null
  if (decision) {
    if (decision.type === "model_routing") return "routing"
    if (decision.type === "tool_approval") return "approval"
    return "decision"
  }
  if (span.attributes && resolveMcpSpanInfo(span.attributes)) return "mcp"

  // Skill calls outrank the generic TOOL kind their spans also carry; both
  // native reports and shape-derived matches render as skills in the tree.
  const skillInfo = span.attributes
    ? resolveSkillSpanInfo(span.attributes)
    : null
  if (skillInfo) return "skill"

  const opName = span.attributes?.["gen_ai.operation.name"] as
    | string
    | undefined
  if (opName) {
    const lower = opName.toLowerCase()
    const mapped = GEN_AI_OP_MAP[lower]
    if (mapped) return mapped
    if (SPAN_KINDS.includes(lower as SpanKind)) return lower as SpanKind
  }

  const genAiKind = span.attributes?.["gen_ai.span.kind"] as string | undefined
  if (genAiKind) {
    const mapped = GEN_AI_KIND_MAP[genAiKind.toLowerCase()]
    if (mapped) return mapped
  }

  const lfType = span.attributes?.["langfuse.observation.type"] as
    | string
    | undefined
  if (lfType && LANGFUSE_TYPE_MAP[lfType.toUpperCase()])
    return LANGFUSE_TYPE_MAP[lfType.toUpperCase()]

  if (span.kind !== null) {
    const mapped = OTEL_KIND_MAP[String(span.kind)]
    if (mapped) return mapped
  }

  // Best-effort heuristic from span name — may produce false positives
  const name = (span.name || "").toLowerCase()
  if (
    name.includes("chat") ||
    name.includes("llm") ||
    name.includes("generation")
  )
    return "generation"
  if (name.includes("tool")) return "tool"
  if (name.includes("agent") || name.startsWith("turn")) return "agent"
  if (name.includes("retriev")) return "retriever"
  if (name.includes("embed")) return "embedding"
  if (name.includes("chain")) return "chain"
  return null
}
